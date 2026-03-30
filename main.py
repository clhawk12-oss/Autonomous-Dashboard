"""
main.py
-------
Orchestrator for the autonomous portfolio agents.

Usage:
    python main.py --agent swing
    python main.py --agent long_term
    python main.py --agent both
    python main.py --agent swing --force   # skip market-hours / schedule guard
"""

import argparse
import json
import os
import sys
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytz
from dotenv import load_dotenv

# Load .env before importing config (ANTHROPIC_API_KEY must be in env)
load_dotenv(Path(__file__).parent / ".env")

from config import (
    SWING_DIR,
    LONG_TERM_DIR,
    STARTING_CASH,
    SWING_MAX_POSITIONS,
    SWING_MIN_WEIGHT,
    SWING_MAX_WEIGHT,
    LONG_TERM_MAX_POSITIONS,
    LONG_TERM_MIN_WEIGHT,
    LONG_TERM_MAX_WEIGHT,
    MIN_CASH_BUFFER,
    SHORT_MARGIN_REQUIREMENT,
    SHORT_BORROW_RATE_ANNUAL,
    SWING_SCHEDULE_ET,
    LONG_TERM_SCHEDULE_ET,
    SCHEDULE_TOLERANCE_MINUTES,
    AI_INFRA_UNIVERSE,
    TRADEABLE_UNIVERSE,
    BENCHMARK_TICKERS,
    SWING_MEMORY_RUNS,
    LONG_TERM_MEMORY_RUNS,
)
from prices import (
    fetch_eod_prices,
    fetch_watchlist_technicals,
    fetch_news,
    fetch_earnings_dates,
    get_market_status,
    is_market_open,
)
from agent import build_user_message, call_claude

ET_TZ = pytz.timezone("America/New_York")

# ---------------------------------------------------------------------------
# Holdings I/O
# ---------------------------------------------------------------------------

def _default_holdings(agent_type: str) -> dict:
    return {
        "meta": {
            "agent_type":    agent_type,
            "starting_cash": STARTING_CASH,
            "last_updated":  None,
            "last_run_id":   None,
        },
        "cash":             STARTING_CASH,
        "margin_reserved":  0.0,
        "positions":        {},
        "closed_positions": [],
        "performance": {
            "total_realized_pnl":   0.0,
            "total_unrealized_pnl": 0.0,
            "total_pnl":            0.0,
            "total_return_pct":     0.0,
            "long_exposure":        0.0,
            "short_exposure":       0.0,
            "net_exposure":         0.0,
            "gross_exposure":       0.0,
            "cash_pct":             1.0,
            "win_rate":             None,
            "trades_closed":        0,
        },
    }


def load_holdings(agent_dir: Path) -> dict:
    """
    Read holdings.json.  Returns initialised default if file missing.
    Backs up corrupt files to holdings.json.bak.{timestamp} and resets.
    """
    agent_type = agent_dir.name   # "swing" or "long_term"
    path = agent_dir / "holdings.json"

    if not path.exists():
        print(f"[{agent_type}] No holdings.json found — initialising with ${STARTING_CASH:,.0f} cash.")
        holdings = _default_holdings(agent_type)
        save_holdings(agent_dir, holdings)
        return holdings

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        bak = path.with_suffix(f".json.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        print(f"[{agent_type}] holdings.json corrupt ({e}). Backing up to {bak.name} and resetting.")
        path.rename(bak)
        holdings = _default_holdings(agent_type)
        save_holdings(agent_dir, holdings)
        return holdings


def save_holdings(agent_dir: Path, holdings: dict) -> None:
    """
    Atomic write: write to .tmp then os.replace() — never leaves file half-written.
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    tmp_path  = agent_dir / "holdings.json.tmp"
    real_path = agent_dir / "holdings.json"

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(holdings, f, indent=2, default=str)

    os.replace(tmp_path, real_path)


# ---------------------------------------------------------------------------
# Memory I/O
# ---------------------------------------------------------------------------

def _default_memory() -> dict:
    return {"market_notes": "", "watching": [], "recent_runs": []}


def load_memory(agent_dir: Path) -> dict:
    """Read memory.json. Returns empty default if file missing or corrupt."""
    path = agent_dir / "memory.json"
    if not path.exists():
        return _default_memory()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return _default_memory()


def save_memory(agent_dir: Path, memory: dict) -> None:
    """Atomic write for memory.json."""
    agent_dir.mkdir(parents=True, exist_ok=True)
    tmp_path  = agent_dir / "memory.json.tmp"
    real_path = agent_dir / "memory.json"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, default=str)
    os.replace(tmp_path, real_path)


def update_memory(
    memory: dict,
    decision: dict,
    run_id: str,
    timestamp: str,
    execution_notes: list[str],
    max_runs: int,
) -> dict:
    """
    Incorporate Claude's memory_update into the memory dict and append
    this run's entry to recent_runs.  Returns the updated memory dict.
    """
    mu = decision.get("memory_update", {})
    if mu:
        if mu.get("market_notes"):
            memory["market_notes"] = mu["market_notes"]
        if mu.get("watching") is not None:
            memory["watching"] = mu["watching"]

    # Build a compact actions_taken list from execution_notes (exclude HOLDs and errors)
    actions_taken = [
        n for n in execution_notes
        if not n.startswith("HOLD") and not n.startswith("SKIPPED") and n.strip()
    ]

    memory["recent_runs"].append({
        "run_id":       run_id,
        "timestamp":    timestamp,
        "reasoning":    decision.get("reasoning", ""),
        "actions_taken": actions_taken,
    })

    # Keep only the last max_runs entries
    memory["recent_runs"] = memory["recent_runs"][-max_runs:]
    return memory


# ---------------------------------------------------------------------------
# Price & performance helpers
# ---------------------------------------------------------------------------

def update_prices(holdings: dict, prices: dict[str, float]) -> None:
    """Refresh current_price, market_value, unrealized_pnl for every position."""
    for ticker, pos in holdings["positions"].items():
        price = prices.get(ticker)
        if price is None:
            continue
        pos["current_price"] = price
        mv = pos["shares"] * price
        pos["market_value"] = mv
        if pos["direction"] == "long":
            pos["unrealized_pnl"] = (price - pos["avg_cost"]) * pos["shares"]
        else:  # short
            pos["unrealized_pnl"] = (pos["avg_cost"] - price) * pos["shares"]
        cost_basis = pos["avg_cost"] * pos["shares"]
        pos["unrealized_pct"] = pos["unrealized_pnl"] / cost_basis if cost_basis else 0.0


def compute_performance(holdings: dict) -> dict:
    """Recompute the performance block from scratch."""
    positions = holdings["positions"]
    closed    = holdings["closed_positions"]

    long_exp  = sum(p["market_value"] for p in positions.values() if p["direction"] == "long")
    short_exp = sum(p["market_value"] for p in positions.values() if p["direction"] == "short")
    total_unreal = sum(p.get("unrealized_pnl", 0.0) for p in positions.values())

    total_real = sum(c.get("realized_pnl", 0.0) for c in closed)
    total_pnl  = total_real + total_unreal

    cash = holdings["cash"]
    margin = holdings.get("margin_reserved", 0.0)
    portfolio_value = cash + margin + long_exp

    total_return = total_pnl / STARTING_CASH if STARTING_CASH else 0.0
    cash_pct = cash / portfolio_value if portfolio_value else 1.0

    wins = [c for c in closed if c.get("realized_pnl", 0) > 0]
    win_rate = round(len(wins) / len(closed), 3) if closed else None

    return {
        "total_realized_pnl":   round(total_real,    2),
        "total_unrealized_pnl": round(total_unreal,  2),
        "total_pnl":            round(total_pnl,     2),
        "total_return_pct":     round(total_return,  6),
        "long_exposure":        round(long_exp,      2),
        "short_exposure":       round(short_exp,     2),
        "net_exposure":         round(long_exp - short_exp, 2),
        "gross_exposure":       round(long_exp + short_exp, 2),
        "cash_pct":             round(cash_pct, 4),
        "win_rate":             win_rate,
        "trades_closed":        len(closed),
    }


# ---------------------------------------------------------------------------
# Mechanical stop enforcement
# ---------------------------------------------------------------------------

def enforce_stops(holdings: dict, prices: dict[str, float]) -> list[dict]:
    """
    Check every position's stop_loss against current price.
    Returns a list of forced-close synthetic action dicts to be executed.
    Does NOT mutate holdings — caller executes the returned actions.
    """
    forced = []
    for ticker, pos in holdings["positions"].items():
        price = prices.get(ticker)
        if price is None:
            continue
        stop = pos.get("stop_loss")
        if stop is None:
            continue

        triggered = (
            (pos["direction"] == "long"  and price <= stop) or
            (pos["direction"] == "short" and price >= stop)
        )
        if triggered:
            action_type = "SELL" if pos["direction"] == "long" else "COVER"
            forced.append({
                "action":    action_type,
                "ticker":    ticker,
                "shares":    pos["shares"],
                "rationale": f"Stop loss triggered at ${price:.2f} (stop: ${stop:.2f})",
                "_stop_loss_trigger": True,
            })
    return forced


# ---------------------------------------------------------------------------
# Trade execution
# ---------------------------------------------------------------------------

def execute_action(
    holdings: dict,
    action: dict,
    prices: dict[str, float],
    agent_type: str,
) -> str:
    """
    Apply a single trade action to holdings in memory.
    Returns a human-readable execution note.
    Raises ValueError if the action violates cash/margin/position constraints.
    """
    act    = action["action"].upper()
    ticker = action["ticker"].upper()
    shares = float(action["shares"])
    price  = prices.get(ticker)

    if price is None:
        raise ValueError(f"No price available for {ticker} — cannot execute {act}")

    if shares <= 0:
        raise ValueError(f"shares must be positive, got {shares}")

    max_pos    = SWING_MAX_POSITIONS    if agent_type == "swing" else LONG_TERM_MAX_POSITIONS
    min_w      = SWING_MIN_WEIGHT       if agent_type == "swing" else LONG_TERM_MIN_WEIGHT
    max_w      = SWING_MAX_WEIGHT       if agent_type == "swing" else LONG_TERM_MAX_WEIGHT

    cash       = holdings["cash"]
    margin     = holdings.get("margin_reserved", 0.0)
    positions  = holdings["positions"]

    # Estimate total portfolio value for weight checks
    long_exp   = sum(p["market_value"] for p in positions.values() if p["direction"] == "long")
    portfolio_value = cash + margin + long_exp

    if act == "BUY":
        cost = shares * price
        if cost > cash:
            raise ValueError(f"Insufficient cash: need ${cost:,.2f}, have ${cash:,.2f}")

        new_cash = cash - cost
        min_cash_needed = portfolio_value * MIN_CASH_BUFFER
        if new_cash < min_cash_needed:
            raise ValueError(
                f"BUY would breach cash buffer: ${new_cash:,.2f} < ${min_cash_needed:,.2f}"
            )

        position_value = shares * price
        weight = position_value / portfolio_value if portfolio_value else 0
        # Check maximum weight only — no minimum enforced (Claude sizes freely)
        existing = positions.get(ticker)
        if existing and existing["direction"] == "long":
            total_value = (existing["shares"] + shares) * price
            if total_value / portfolio_value > max_w:
                raise ValueError(
                    f"Adding to {ticker} would exceed max weight {max_w*100:.0f}%: "
                    f"{total_value/portfolio_value*100:.1f}%"
                )
        elif weight > max_w:
            raise ValueError(
                f"Position too large: {weight*100:.1f}% > max {max_w*100:.0f}%"
            )

        if ticker not in positions and len(positions) >= max_pos:
            raise ValueError(f"Max positions ({max_pos}) reached")

        if ticker in positions and positions[ticker]["direction"] == "short":
            raise ValueError(f"Cannot BUY {ticker} while short — COVER first")

        # Update holdings
        if ticker in positions:
            pos = positions[ticker]
            total_shares = pos["shares"] + shares
            pos["avg_cost"] = (pos["avg_cost"] * pos["shares"] + cost) / total_shares
            pos["shares"]   = total_shares
        else:
            stop_pct = action.get("stop_loss_pct", 0.05)
            tp_pct   = action.get("take_profit_pct")
            positions[ticker] = {
                "direction":     "long",
                "shares":        shares,
                "avg_cost":      price,
                "current_price": price,
                "market_value":  shares * price,
                "unrealized_pnl": 0.0,
                "unrealized_pct": 0.0,
                "date_opened":   datetime.now(ET_TZ).strftime("%Y-%m-%d"),
                "thesis":        action.get("thesis", ""),
                "stop_loss":     round(price * (1 - stop_pct), 2),
                "take_profit":   round(price * (1 + tp_pct), 2) if tp_pct else None,
            }

        holdings["cash"] = new_cash
        return f"BUY {shares:.0f} {ticker} @ ${price:.2f} (${cost:,.2f})"

    elif act == "SELL":
        if ticker not in positions or positions[ticker]["direction"] != "long":
            raise ValueError(f"No long position in {ticker} to SELL")

        pos = positions[ticker]
        sell_shares = min(shares, pos["shares"])
        proceeds    = sell_shares * price
        realized    = (price - pos["avg_cost"]) * sell_shares

        holdings["cash"] = cash + proceeds

        # Close or reduce
        exit_reason = action.get("rationale", "")
        if sell_shares >= pos["shares"]:
            _record_closed(holdings, ticker, pos, price, realized, exit_reason)
            del positions[ticker]
        else:
            pos["shares"] -= sell_shares

        return f"SELL {sell_shares:.0f} {ticker} @ ${price:.2f} → P&L ${realized:+,.2f}"

    elif act == "SHORT":
        if agent_type == "long_term":
            raise ValueError("SHORT not permitted — long-term account is long-only")
        if ticker in positions:
            raise ValueError(
                f"Already have a position in {ticker} — cannot SHORT. Close existing first."
            )
        if len(positions) >= max_pos:
            raise ValueError(f"Max positions ({max_pos}) reached")

        proceeds     = shares * price
        margin_hold  = proceeds * SHORT_MARGIN_REQUIREMENT
        net_cash_chg = proceeds - margin_hold   # negative: costs 0.5× position value

        if cash + net_cash_chg < portfolio_value * MIN_CASH_BUFFER:
            raise ValueError(
                f"SHORT would breach cash buffer after margin requirement of ${margin_hold:,.2f}"
            )
        if cash + net_cash_chg < 0:
            raise ValueError(
                f"Insufficient cash for margin requirement: need ${margin_hold:,.2f}, "
                f"net after proceeds ${cash + net_cash_chg:,.2f}"
            )

        stop_pct = action.get("stop_loss_pct", 0.05)
        tp_pct   = action.get("take_profit_pct")

        positions[ticker] = {
            "direction":          "short",
            "shares":             shares,
            "avg_cost":           price,
            "current_price":      price,
            "market_value":       shares * price,
            "unrealized_pnl":     0.0,
            "unrealized_pct":     0.0,
            "date_opened":        datetime.now(ET_TZ).strftime("%Y-%m-%d"),
            "thesis":             action.get("thesis", ""),
            "stop_loss":          round(price * (1 + stop_pct), 2),
            "take_profit":        round(price * (1 - tp_pct), 2) if tp_pct else None,
            "borrow_cost_accrued": 0.0,
            "_margin_hold":       margin_hold,
        }
        holdings["cash"] = cash + net_cash_chg
        holdings["margin_reserved"] = margin + margin_hold

        return (
            f"SHORT {shares:.0f} {ticker} @ ${price:.2f} "
            f"(proceeds ${proceeds:,.2f}, margin ${margin_hold:,.2f})"
        )

    elif act == "COVER":
        if ticker not in positions or positions[ticker]["direction"] != "short":
            raise ValueError(f"No short position in {ticker} to COVER")

        pos          = positions[ticker]
        cover_shares = min(shares, pos["shares"])
        buyback      = cover_shares * price
        margin_hold  = pos.get("_margin_hold", pos["avg_cost"] * pos["shares"] * SHORT_MARGIN_REQUIREMENT)
        realized     = (pos["avg_cost"] - price) * cover_shares

        holdings["cash"]             = cash + margin_hold - buyback
        holdings["margin_reserved"]  = max(0.0, margin - margin_hold)

        exit_reason = action.get("rationale", "")
        if cover_shares >= pos["shares"]:
            _record_closed(holdings, ticker, pos, price, realized, exit_reason)
            del positions[ticker]
        else:
            pos["shares"] -= cover_shares

        return f"COVER {cover_shares:.0f} {ticker} @ ${price:.2f} → P&L ${realized:+,.2f}"

    elif act == "HOLD":
        return f"HOLD — no action on {ticker}"

    else:
        raise ValueError(f"Unknown action type: {act}")


def _record_closed(
    holdings: dict,
    ticker: str,
    pos: dict,
    close_price: float,
    realized_pnl: float,
    exit_reason: str,
) -> None:
    """Append a closed-position record; cap the list at 20 entries."""
    cost_basis = pos["avg_cost"] * pos["shares"]
    record = {
        "ticker":        ticker,
        "direction":     pos["direction"],
        "shares":        pos["shares"],
        "avg_cost":      pos["avg_cost"],
        "close_price":   close_price,
        "realized_pnl":  round(realized_pnl, 2),
        "realized_pct":  round(realized_pnl / cost_basis, 4) if cost_basis else 0.0,
        "date_opened":   pos.get("date_opened"),
        "date_closed":   datetime.now(ET_TZ).strftime("%Y-%m-%d"),
        "exit_reason":   exit_reason,
    }
    holdings["closed_positions"].append(record)
    # Keep only the last 20 closed positions in the JSON (full history in trade_log.md)
    holdings["closed_positions"] = holdings["closed_positions"][-20:]


# ---------------------------------------------------------------------------
# Borrow cost accrual (EOD only)
# ---------------------------------------------------------------------------

def accrue_borrow_costs(holdings: dict) -> list[str]:
    """
    Deduct daily borrow cost from cash for each short position.
    Returns list of log lines. Called once per EOD run.
    """
    notes = []
    for ticker, pos in holdings["positions"].items():
        if pos["direction"] != "short":
            continue
        price  = pos.get("current_price", pos["avg_cost"])
        daily  = pos["shares"] * price * SHORT_BORROW_RATE_ANNUAL / 252
        pos["borrow_cost_accrued"] = pos.get("borrow_cost_accrued", 0.0) + daily
        holdings["cash"] -= daily
        notes.append(f"Borrow cost {ticker}: ${daily:.4f}/day (total ${pos['borrow_cost_accrued']:.2f})")
    return notes


# ---------------------------------------------------------------------------
# Equity log (one JSONL line per run — powers the dashboard equity curve)
# ---------------------------------------------------------------------------

def append_equity_log(agent_dir: Path, holdings: dict, run_id: str) -> None:
    """Append a portfolio value snapshot to equity_log.jsonl."""
    perf  = holdings["performance"]
    cash  = holdings["cash"]
    value = cash + holdings.get("margin_reserved", 0.0) + perf["long_exposure"]
    record = {
        "run_id":           run_id,
        "timestamp":        datetime.now(ET_TZ).isoformat(),
        "portfolio_value":  round(value, 2),
        "cash":             round(cash, 2),
        "total_pnl":        perf["total_pnl"],
        "total_return_pct": perf["total_return_pct"],
        "long_exposure":    perf["long_exposure"],
        "short_exposure":   perf["short_exposure"],
        "n_positions":      len(holdings["positions"]),
    }
    path = agent_dir / "equity_log.jsonl"
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def append_trade_log(agent_dir: Path, entries: list[str]) -> None:
    """Append markdown entries to trade_log.md."""
    path = agent_dir / "trade_log.md"
    agent_type = agent_dir.name

    if not path.exists():
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# {agent_type.replace('_', ' ').title()} — Trade Log\n\n")

    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(entries) + "\n\n")


def write_summary(agent_dir: Path, holdings: dict, narrative: str) -> None:
    """Overwrite summary.md with current portfolio snapshot + Claude's narrative."""
    agent_type = agent_dir.name.replace("_", " ").title()
    perf       = holdings["performance"]
    positions  = holdings["positions"]
    now_et     = datetime.now(ET_TZ).strftime("%Y-%m-%d %H:%M ET")

    lines = [
        f"# {agent_type} — Portfolio Summary",
        f"*Last updated: {now_et}*",
        "",
        "## Performance",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total P&L | ${perf['total_pnl']:+,.2f} ({perf['total_return_pct']*100:+.2f}%) |",
        f"| Realized P&L | ${perf['total_realized_pnl']:+,.2f} |",
        f"| Unrealized P&L | ${perf['total_unrealized_pnl']:+,.2f} |",
        f"| Cash | ${holdings['cash']:,.2f} ({perf['cash_pct']*100:.1f}%) |",
        f"| Long Exposure | ${perf['long_exposure']:,.2f} |",
        f"| Short Exposure | ${perf['short_exposure']:,.2f} |",
        f"| Net Exposure | ${perf['net_exposure']:,.2f} |",
        f"| Win Rate | {perf['win_rate'] or 'n/a'} ({perf['trades_closed']} closed) |",
        "",
    ]

    if positions:
        lines += [
            "## Current Positions",
            "| Ticker | Dir | Shares | Avg Cost | Current | Value | P&L | P&L% | Stop |",
            "|--------|-----|--------|----------|---------|-------|-----|------|------|",
        ]
        for ticker, pos in positions.items():
            lines.append(
                f"| {ticker} | {pos['direction'].upper()} | {pos['shares']:.0f} "
                f"| ${pos['avg_cost']:.2f} | ${pos.get('current_price', 0):.2f} "
                f"| ${pos.get('market_value', 0):,.2f} "
                f"| ${pos.get('unrealized_pnl', 0):+,.2f} "
                f"| {pos.get('unrealized_pct', 0)*100:+.1f}% "
                f"| ${pos.get('stop_loss', 0):.2f} |"
            )
    else:
        lines.append("## Current Positions: none (fully cash)")

    lines += ["", "## PM Narrative", narrative]

    path = agent_dir / "summary.md"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Schedule guard
# ---------------------------------------------------------------------------

def _is_scheduled_now(agent_type: str) -> bool:
    """
    Returns True if current ET time is within SCHEDULE_TOLERANCE_MINUTES of
    any configured run time for this agent.
    """
    schedule = SWING_SCHEDULE_ET if agent_type == "swing" else LONG_TERM_SCHEDULE_ET
    now_et   = datetime.now(ET_TZ)
    now_min  = now_et.hour * 60 + now_et.minute

    for h, m in schedule:
        target_min = h * 60 + m
        if abs(now_min - target_min) <= SCHEDULE_TOLERANCE_MINUTES:
            return True
    return False


# ---------------------------------------------------------------------------
# Core agent run
# ---------------------------------------------------------------------------

def run_agent(agent_type: str, force: bool = False) -> None:
    """
    Full orchestration sequence for one agent run.

    agent_type: "swing" | "long_term"
    force:      bypass market-hours and schedule guard (for testing)
    """
    agent_dir = SWING_DIR if agent_type == "swing" else LONG_TERM_DIR
    agent_dir.mkdir(parents=True, exist_ok=True)

    now_et   = datetime.now(ET_TZ)
    run_id   = f"{agent_type}-{now_et.strftime('%Y%m%d-%H%M')}"
    mkt      = get_market_status()

    print(f"\n{'='*60}")
    print(f"[{run_id}] Starting run — market: {mkt['session']} ({mkt['current_et']})")

    # ── Market hours gate (swing only) ─────────────────────────────────────
    if agent_type == "swing" and not force:
        if not is_market_open():
            print(f"[{run_id}] Market closed — skipping swing run.")
            return

    # ── Schedule gate ──────────────────────────────────────────────────────
    if not force and not _is_scheduled_now(agent_type):
        print(f"[{run_id}] Not within scheduled window — skipping.")
        return

    log_entries = [
        f"### {now_et.strftime('%Y-%m-%d %H:%M ET')} — Run ID: {run_id}",
        f"**Market**: {mkt['session']} | {mkt['current_et']}",
    ]

    # ── Load state ─────────────────────────────────────────────────────────
    holdings = load_holdings(agent_dir)
    memory   = load_memory(agent_dir)

    # ── Load peer holdings (read-only, for coordination context) ───────────
    peer_dir      = LONG_TERM_DIR if agent_type == "swing" else SWING_DIR
    peer_agent    = "long_term"   if agent_type == "swing" else "swing"
    peer_holdings: dict = {}
    peer_path = peer_dir / "holdings.json"
    if peer_path.exists():
        try:
            with open(peer_path, "r", encoding="utf-8") as _pf:
                peer_holdings = json.load(_pf)
        except Exception:
            pass

    # ── Fetch prices ───────────────────────────────────────────────────────
    print(f"[{run_id}] Fetching prices for {len(AI_INFRA_UNIVERSE)} tickers...")
    try:
        technicals = fetch_watchlist_technicals(AI_INFRA_UNIVERSE)
        prices = {t: s["price"] for t, s in technicals.items() if s.get("price")}
    except Exception as e:
        msg = f"Price fetch failed: {e}"
        print(f"[{run_id}] ERROR: {msg}")
        log_entries.append(f"**ERROR**: {msg}")
        append_trade_log(agent_dir, log_entries)
        return

    # ── Update current prices in holdings ──────────────────────────────────
    update_prices(holdings, prices)

    # ── Fetch news + earnings ───────────────────────────────────────────────
    held_tickers     = list(holdings["positions"].keys())
    watchlist_only   = [t for t in TRADEABLE_UNIVERSE if t not in held_tickers]
    news: dict             = {}
    earnings: dict         = {}
    watchlist_news: dict   = {}
    watchlist_earnings: dict = {}

    print(f"[{run_id}] Fetching news and earnings for {len(held_tickers)} held + {len(watchlist_only)} watchlist tickers...")
    try:
        if held_tickers:
            news     = fetch_news(held_tickers, max_headlines=5, days=7)
            earnings = fetch_earnings_dates(held_tickers, days_ahead=7)
            if earnings:
                print(f"[{run_id}] Earnings alert (held): {earnings}")
            else:
                print(f"[{run_id}] No earnings in next 7 days for held positions.")
        watchlist_news     = fetch_news(watchlist_only, max_headlines=2, days=3)
        watchlist_earnings = fetch_earnings_dates(watchlist_only)
        print(f"[{run_id}] Watchlist: {len(watchlist_news)} tickers with news, "
              f"{len(watchlist_earnings)} with upcoming earnings.")
    except Exception as e:
        print(f"[{run_id}] WARN: news/earnings fetch failed: {e}")

    # ── Accrue borrow costs (EOD runs only) ────────────────────────────────
    is_eod_run = agent_type == "long_term" or (
        agent_type == "swing" and now_et.hour >= 15
    )
    borrow_notes = []
    if is_eod_run:
        borrow_notes = accrue_borrow_costs(holdings)

    # ── Mechanical stop enforcement ────────────────────────────────────────
    stop_actions = enforce_stops(holdings, prices)
    stop_notes   = []
    for action in stop_actions:
        try:
            note = execute_action(holdings, action, prices, agent_type)
            stop_notes.append(f"**STOP_LOSS** {note}")
            print(f"[{run_id}] {note}")
        except ValueError as e:
            stop_notes.append(f"**STOP_LOSS FAILED** {action['ticker']}: {e}")

    if stop_notes:
        log_entries.extend(stop_notes)

    # ── Recompute performance ───────────────────────────────────────────────
    holdings["performance"] = compute_performance(holdings)

    # ── Call Claude ────────────────────────────────────────────────────────
    print(f"[{run_id}] Calling Claude ({agent_type} model)...")
    try:
        user_msg  = build_user_message(
            holdings, technicals, mkt,
            memory=memory,
            news=news,
            earnings=earnings,
            peer_holdings=peer_holdings,
            peer_agent=peer_agent,
            watchlist_news=watchlist_news,
            watchlist_earnings=watchlist_earnings,
        )
        decision  = call_claude(agent_type, user_msg)
    except RuntimeError as e:
        msg = f"Claude call failed: {e}"
        print(f"[{run_id}] ERROR: {msg}")
        log_entries.append(f"**ERROR**: {msg}")
        # Save whatever price updates we've made
        save_holdings(agent_dir, holdings)
        append_trade_log(agent_dir, log_entries)
        return

    reasoning = decision.get("reasoning", "")
    summary   = decision.get("summary", "")
    actions   = decision.get("actions", [])

    log_entries.append(f"**Reasoning**: {reasoning}")

    # ── Execute Claude's actions ───────────────────────────────────────────
    execution_notes = []
    skipped_notes   = []   # track skipped actions separately for summary correction
    bench_line = " | ".join(
        f"{t} 1W {(technicals.get(t) or {}).get('return_1w', 0)*100:+.1f}%"
        for t in ["SPY", "QQQ", "SMH"]
        if technicals.get(t) and technicals[t].get("return_1w") is not None
    )
    log_entries.append(f"**Benchmarks**: {bench_line}")
    log_entries.append("")

    if not actions:
        execution_notes.append("No trades — HOLD all positions.")
        print(f"[{run_id}] No trades this run.")
    else:
        log_entries.append("| Action | Ticker | Shares | Price | Note |")
        log_entries.append("|--------|--------|--------|-------|------|")

        for action in actions:
            act    = action.get("action", "").upper()
            ticker = action.get("ticker", "").upper()
            shares = action.get("shares", 0)
            price  = prices.get(ticker, 0)

            if act == "HOLD":
                execution_notes.append(f"HOLD {ticker}")
                continue

            try:
                note = execute_action(holdings, action, prices, agent_type)
                execution_notes.append(note)
                print(f"[{run_id}] {note}")
                log_entries.append(
                    f"| {act} | {ticker} | {shares} | ${price:.2f} | {action.get('rationale', '')} |"
                )
            except ValueError as e:
                err_note = f"{act} {ticker}: {e}"
                skipped_notes.append(err_note)
                execution_notes.append(f"SKIPPED {err_note}")
                print(f"[{run_id}] WARN: SKIPPED {err_note}")
                log_entries.append(
                    f"| SKIPPED | {ticker} | {shares} | ${price:.2f} | {e} |"
                )

    # ── Final performance recompute ────────────────────────────────────────
    holdings["performance"] = compute_performance(holdings)

    # ── Update meta ────────────────────────────────────────────────────────
    holdings["meta"]["last_updated"] = now_et.isoformat()
    holdings["meta"]["last_run_id"]  = run_id

    # ── Append equity snapshot ─────────────────────────────────────────────
    append_equity_log(agent_dir, holdings, run_id)

    # ── Update and save memory ─────────────────────────────────────────────
    max_runs = SWING_MEMORY_RUNS if agent_type == "swing" else LONG_TERM_MEMORY_RUNS
    memory = update_memory(
        memory, decision, run_id, now_et.isoformat(), execution_notes, max_runs
    )
    save_memory(agent_dir, memory)
    print(f"[{run_id}] memory.json updated.")

    # ── Save holdings ──────────────────────────────────────────────────────
    save_holdings(agent_dir, holdings)
    print(f"[{run_id}] Holdings saved.")

    # ── Write summary.md ───────────────────────────────────────────────────
    # Append a correction footnote if any of Claude's proposed actions were skipped,
    # so the narrative accurately reflects what actually executed.
    if skipped_notes:
        skipped_str = "; ".join(skipped_notes)
        summary += (
            f"\n\n*Note: The following proposed actions were not executed due to "
            f"constraint violations: {skipped_str}*"
        )
    write_summary(agent_dir, holdings, summary)
    print(f"[{run_id}] summary.md updated.")

    # ── Append trade log ───────────────────────────────────────────────────
    perf = holdings["performance"]
    log_entries.append("")
    log_entries.append(
        f"**Portfolio after run**: Cash ${holdings['cash']:,.2f} | "
        f"P&L ${perf['total_pnl']:+,.2f} ({perf['total_return_pct']*100:+.2f}%)"
    )
    if borrow_notes:
        log_entries.extend(borrow_notes)
    log_entries.append("")
    log_entries.append("---")

    append_trade_log(agent_dir, log_entries)
    print(f"[{run_id}] trade_log.md updated.")
    print(f"[{run_id}] Run complete. P&L: ${perf['total_pnl']:+,.2f} ({perf['total_return_pct']*100:+.2f}%)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous Portfolio Agent")
    parser.add_argument(
        "--agent",
        choices=["swing", "long_term", "both"],
        default="both",
        help="Which agent to run",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass market-hours and schedule guard (useful for testing)",
    )
    args = parser.parse_args()

    if args.agent in ("swing", "both"):
        try:
            run_agent("swing", force=args.force)
        except Exception:
            print("[swing] Unhandled exception:")
            traceback.print_exc()

    if args.agent in ("long_term", "both"):
        try:
            run_agent("long_term", force=args.force)
        except Exception:
            print("[long_term] Unhandled exception:")
            traceback.print_exc()


if __name__ == "__main__":
    main()
