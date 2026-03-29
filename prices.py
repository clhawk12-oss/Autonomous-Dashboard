"""
prices.py
---------
yfinance data layer for the autonomous portfolio agents.

MultiIndex handling mirrors investment_dashboard/modules/data_loader.py lines 98-120
to ensure consistent behaviour across yfinance >= 0.2.38.
"""

import time as _time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import pytz
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)

ET_TZ = pytz.timezone("America/New_York")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _download(tickers: list[str], period: str = "1y", progress: bool = False) -> pd.DataFrame:
    """
    Thin wrapper around yf.download that normalises the MultiIndex column
    structure that yfinance >= 0.2.38 returns even for a single ticker when
    passed as a list.
    """
    raw = yf.download(
        tickers=tickers,
        period=period,
        auto_adjust=True,
        progress=progress,
        threads=True,
    )
    return raw


def _extract_series(raw: pd.DataFrame, field: str, ticker: str) -> pd.Series:
    """
    Extract a single field Series from a potentially MultiIndex DataFrame.
    Returns empty Series on failure rather than raising.
    """
    try:
        if isinstance(raw.columns, pd.MultiIndex):
            return raw[field][ticker.upper()].dropna()
        else:
            # Single-ticker download without MultiIndex
            if field in raw.columns:
                return raw[field].dropna()
            return pd.Series(dtype=float)
    except KeyError:
        return pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_eod_prices(tickers: list[str]) -> dict[str, float]:
    """
    Fetch the most recent adjusted closing price for each ticker.
    Returns {ticker: price}.  Tickers that fail to download are omitted.
    """
    if not tickers:
        return {}

    upper = [t.upper() for t in tickers]
    raw = _download(upper, period="5d")

    prices: dict[str, float] = {}
    for ticker in upper:
        series = _extract_series(raw, "Close", ticker)
        if not series.empty:
            prices[ticker] = float(series.iloc[-1])

    return prices


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> float:
    """Average True Range — used for position sizing and stop placement."""
    if len(close) < period + 1:
        return float("nan")

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) else float("nan")


def fetch_technical_summary(ticker: str) -> dict:
    """
    Single yf.download call that returns a compact dict of technical metrics
    for one ticker.  All values are JSON-serialisable floats or None.

    Keys returned:
        price, price_1w_ago, price_1m_ago,
        return_1w, return_1m,
        high_52w, low_52w, pct_from_52w_high,
        avg_volume_20d, volume_ratio,
        atr_14
    """
    t = ticker.upper()
    raw = _download([t], period="1y")

    close  = _extract_series(raw, "Close",  t)
    high   = _extract_series(raw, "High",   t)
    low    = _extract_series(raw, "Low",    t)
    volume = _extract_series(raw, "Volume", t)

    if close.empty or len(close) < 5:
        return {"ticker": t, "price": None, "error": "insufficient data"}

    price      = float(close.iloc[-1])
    price_1w   = float(close.iloc[-6])  if len(close) >= 6  else None
    price_1m   = float(close.iloc[-22]) if len(close) >= 22 else None
    high_52w   = float(high.max())
    low_52w    = float(low.min())

    ret_1w = round((price / price_1w - 1), 4) if price_1w else None
    ret_1m = round((price / price_1m - 1), 4) if price_1m else None
    pct_from_high = round((price / high_52w - 1), 4) if high_52w else None

    avg_vol_20d = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else None
    vol_today   = float(volume.iloc[-1])          if not volume.empty  else None
    vol_ratio   = round(vol_today / avg_vol_20d, 2) if avg_vol_20d and avg_vol_20d > 0 else None

    atr = compute_atr(high, low, close, 14)

    return {
        "ticker":            t,
        "price":             round(price, 2),
        "price_1w_ago":      round(price_1w, 2)   if price_1w   else None,
        "price_1m_ago":      round(price_1m, 2)   if price_1m   else None,
        "return_1w":         ret_1w,
        "return_1m":         ret_1m,
        "high_52w":          round(high_52w, 2),
        "low_52w":           round(low_52w, 2),
        "pct_from_52w_high": pct_from_high,
        "avg_volume_20d":    int(avg_vol_20d) if avg_vol_20d else None,
        "volume_ratio":      vol_ratio,
        "atr_14":            round(atr, 4) if not np.isnan(atr) else None,
    }


def fetch_watchlist_technicals(tickers: list[str]) -> dict[str, dict]:
    """
    Batch fetch technical summaries for a list of tickers.
    Returns {ticker: summary_dict}.

    Uses a single yf.download call for all tickers to minimise HTTP round-trips,
    then extracts per-ticker Series from the MultiIndex result.
    """
    if not tickers:
        return {}

    upper = [t.upper() for t in tickers]
    raw = _download(upper, period="1y")

    results: dict[str, dict] = {}
    for t in upper:
        close  = _extract_series(raw, "Close",  t)
        high   = _extract_series(raw, "High",   t)
        low    = _extract_series(raw, "Low",    t)
        volume = _extract_series(raw, "Volume", t)

        if close.empty or len(close) < 5:
            results[t] = {"ticker": t, "price": None, "error": "insufficient data"}
            continue

        price    = float(close.iloc[-1])
        price_1w = float(close.iloc[-6])  if len(close) >= 6  else None
        price_1m = float(close.iloc[-22]) if len(close) >= 22 else None
        high_52w = float(high.max())
        low_52w  = float(low.min())

        results[t] = {
            "ticker":            t,
            "price":             round(price, 2),
            "price_1w_ago":      round(price_1w, 2)                   if price_1w   else None,
            "price_1m_ago":      round(price_1m, 2)                   if price_1m   else None,
            "return_1w":         round(price / price_1w - 1, 4)       if price_1w   else None,
            "return_1m":         round(price / price_1m - 1, 4)       if price_1m   else None,
            "high_52w":          round(high_52w, 2),
            "low_52w":           round(low_52w, 2),
            "pct_from_52w_high": round(price / high_52w - 1, 4)       if high_52w   else None,
            "avg_volume_20d":    int(volume.iloc[-20:].mean())         if len(volume) >= 20 else None,
            "volume_ratio":      round(float(volume.iloc[-1]) / float(volume.iloc[-20:].mean()), 2)
                                 if len(volume) >= 20 and volume.iloc[-20:].mean() > 0 else None,
            "atr_14":            round(compute_atr(high, low, close, 14), 4)
                                 if len(close) >= 15 else None,
        }

    return results


def _fetch_ticker_news(ticker: str, max_headlines: int, cutoff_ts: float) -> tuple[str, list]:
    """Fetch news for one ticker. Returns (ticker, items)."""
    try:
        raw = yf.Ticker(ticker).news or []
        items = []
        for article in raw:
            # yfinance >= 0.2.50 wraps content under a "content" key
            if "content" in article:
                content = article["content"]
                title     = content.get("title", "")
                publisher = (content.get("provider") or {}).get("displayName", "")
                pub_str   = content.get("pubDate", "")
                try:
                    pub_ts = datetime.fromisoformat(pub_str.replace("Z", "+00:00")).timestamp()
                except (ValueError, AttributeError):
                    pub_ts = 0.0
            else:
                title     = article.get("title", "")
                publisher = article.get("publisher", "")
                pub_ts    = float(article.get("providerPublishTime", 0))

            if not title or pub_ts < cutoff_ts:
                continue
            hours_ago = (_time.time() - pub_ts) / 3600
            items.append({
                "title":     title,
                "publisher": publisher,
                "hours_ago": round(hours_ago, 1),
            })
            if len(items) >= max_headlines:
                break
        return ticker.upper(), items
    except Exception:
        return ticker.upper(), []


def fetch_news(tickers: list[str], max_headlines: int = 5) -> dict[str, list[dict]]:
    """
    Fetch recent headlines for a list of tickers (typically held positions).
    Returns {ticker: [{"title", "publisher", "hours_ago"}, ...]}.
    Only includes articles from the last 7 days. Fetches in parallel.
    """
    if not tickers:
        return {}
    cutoff_ts = _time.time() - 7 * 86_400
    results: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers), 10)) as executor:
        futures = {
            executor.submit(_fetch_ticker_news, t, max_headlines, cutoff_ts): t
            for t in tickers
        }
        for future in as_completed(futures):
            ticker, items = future.result()
            if items:
                results[ticker] = items
    return results


def _fetch_ticker_earnings(
    ticker: str, today: date, cutoff: date
) -> tuple[str, Optional[str]]:
    """Return (ticker, YYYY-MM-DD) if earnings fall within window, else (ticker, None)."""
    try:
        cal = yf.Ticker(ticker).calendar
        if cal is None:
            return ticker.upper(), None

        # Normalise to a list regardless of dict vs DataFrame
        if isinstance(cal, dict):
            raw_dates = cal.get("Earnings Date", [])
            raw_dates = raw_dates if isinstance(raw_dates, list) else [raw_dates]
        elif hasattr(cal, "columns"):              # newer versions return a DataFrame
            if "Earnings Date" in cal.columns:
                raw_dates = cal["Earnings Date"].dropna().tolist()
            elif "Earnings Date" in cal.index:
                raw_dates = [cal.loc["Earnings Date"]]
            else:
                return ticker.upper(), None
        else:
            return ticker.upper(), None

        for ed in raw_dates:
            # Normalise to a plain date
            if hasattr(ed, "date"):
                ed_date = ed.date()
            else:
                try:
                    ed_date = date.fromisoformat(str(ed)[:10])
                except (ValueError, TypeError):
                    continue
            if today <= ed_date <= cutoff:
                return ticker.upper(), ed_date.isoformat()

        return ticker.upper(), None
    except Exception:
        return ticker.upper(), None


def fetch_earnings_dates(tickers: list[str], days_ahead: int = 14) -> dict[str, str]:
    """
    Returns {ticker: "YYYY-MM-DD"} for tickers with earnings within `days_ahead` days.
    Fetches in parallel.
    """
    if not tickers:
        return {}
    today   = date.today()
    cutoff  = today + timedelta(days=days_ahead)
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(len(tickers), 10)) as executor:
        futures = {
            executor.submit(_fetch_ticker_earnings, t, today, cutoff): t
            for t in tickers
        }
        for future in as_completed(futures):
            ticker, ed = future.result()
            if ed:
                results[ticker] = ed
    return results


def is_market_open() -> bool:
    """
    True if current US/Eastern time is within regular market hours (9:30–16:00)
    on a weekday.  Does not check for public holidays — acceptable for paper trading.
    """
    now_et = datetime.now(ET_TZ)
    if now_et.weekday() >= 5:   # Saturday = 5, Sunday = 6
        return False
    t = now_et.time()
    return time(9, 30) <= t < time(16, 0)


def get_market_status() -> dict:
    """
    Returns a status dict useful for logging and scheduling decisions.
    """
    now_et = datetime.now(ET_TZ)
    t = now_et.time()
    is_weekday = now_et.weekday() < 5

    open_time  = time(9, 30)
    close_time = time(16, 0)

    if not is_weekday:
        session = "weekend"
        is_open = False
    elif t < open_time:
        session = "pre-market"
        is_open = False
    elif t < close_time:
        session = "regular"
        is_open = True
    else:
        session = "after-hours"
        is_open = False

    def _minutes_until(target: time) -> Optional[int]:
        now_min = now_et.hour * 60 + now_et.minute
        tgt_min = target.hour * 60 + target.minute
        diff = tgt_min - now_min
        return diff if diff > 0 else None

    return {
        "is_open":           is_open,
        "current_et":        now_et.strftime("%H:%M ET"),
        "date":              now_et.strftime("%Y-%m-%d"),
        "session":           session,
        "minutes_to_open":   _minutes_until(open_time),
        "minutes_to_close":  _minutes_until(close_time),
    }
