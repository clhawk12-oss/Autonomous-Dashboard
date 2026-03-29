"""
dashboard.py
------------
Streamlit dashboard for the Autonomous Portfolio Agent System.

Run:
    streamlit run dashboard.py

Reads directly from holdings.json, equity_log.jsonl, and trade_log.md.
No database required.
"""

import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st
import yfinance as yf

# ── Config ───────────────────────────────────────────────────────────────────
ROOT          = Path(__file__).parent
SWING_DIR     = ROOT / "swing"
LT_DIR        = ROOT / "long_term"
STARTING_CASH = 100_000.0
ET_TZ         = pytz.timezone("America/New_York")

SECTOR_MAP: dict[str, str] = {
    # Semiconductors
    "NVDA": "Semiconductors", "AMD": "Semiconductors", "AVGO": "Semiconductors",
    "MRVL": "Semiconductors", "ARM": "Semiconductors", "QCOM": "Semiconductors",
    "TXN": "Semiconductors",  "MU": "Semiconductors",  "SMCI": "Semiconductors",
    "INTC": "Semiconductors", "MCHP": "Semiconductors","SWKS": "Semiconductors",
    "ADI": "Semiconductors",  "NXPI": "Semiconductors",
    # Semiconductor Equipment
    "AMAT": "Semi Equipment", "LRCX": "Semi Equipment", "KLAC": "Semi Equipment",
    "ASML": "Semi Equipment", "ONTO": "Semi Equipment", "ACLS": "Semi Equipment",
    "ENTG": "Semi Equipment", "MKSI": "Semi Equipment", "COHU": "Semi Equipment",
    # Cloud / Software
    "MSFT": "Cloud/Software", "GOOGL": "Cloud/Software", "GOOG": "Cloud/Software",
    "ORCL": "Cloud/Software", "CRM":  "Cloud/Software", "NOW":  "Cloud/Software",
    "SNOW": "Cloud/Software", "DDOG": "Cloud/Software", "MDB":  "Cloud/Software",
    "GTLB": "Cloud/Software", "ZS":   "Cloud/Software", "CRWD": "Cloud/Software",
    "NET":  "Cloud/Software",
    # Data / AI Software
    "PLTR": "Data/AI", "AI": "Data/AI", "BBAI": "Data/AI",
    # Hyperscalers / E-commerce
    "AMZN": "Cloud/E-commerce",
    # Social Media
    "META": "Social Media", "PINS": "Social Media", "SNAP": "Social Media",
    # Consumer Tech
    "AAPL": "Consumer Tech", "DELL": "Consumer Tech", "HPQ": "Consumer Tech",
    # Networking
    "ANET": "Networking", "CSCO": "Networking", "CIEN": "Networking",
    "JNPR": "Networking",
    # Data Center REITs
    "EQIX": "Data Center REIT", "DLR": "Data Center REIT", "IRM": "Data Center REIT",
    # Energy
    "VST": "Energy", "CEG": "Energy", "NRG": "Energy", "POWL": "Energy",
    "LNG": "Energy", "CVX": "Energy", "XOM": "Energy", "COP": "Energy",
    "PSX": "Energy", "VLO": "Energy",
    # Consumer / Retail
    "COST": "Consumer/Retail", "WMT": "Consumer/Retail", "TGT": "Consumer/Retail",
    "AMZN": "Cloud/E-commerce",
    # Financials
    "V":    "Financials", "MA":   "Financials", "JPM":  "Financials",
    "BAC":  "Financials", "GS":   "Financials", "BRK-B":"Financials",
    "PYPL": "Fintech",    "SQ":   "Fintech",    "AFRM": "Fintech",
    # Healthcare
    "UNH":  "Healthcare", "LLY":  "Healthcare", "ABBV": "Healthcare",
    "JNJ":  "Healthcare", "PFE":  "Healthcare", "MRNA": "Healthcare",
    # EV / Auto
    "TSLA": "EV/Auto", "RIVN": "EV/Auto",
    # Memory / Storage
    "WDC":  "Memory/Storage", "STX": "Memory/Storage",
}


# ── Data loaders ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_holdings(agent_dir: str) -> dict:
    path = Path(agent_dir) / "holdings.json"
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@st.cache_data(ttl=60)
def load_equity_log(agent_dir: str) -> list[dict]:
    path = Path(agent_dir) / "equity_log.jsonl"
    records: list[dict] = []
    if not path.exists():
        return records
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


@st.cache_data(ttl=60)
def load_trade_log_sections(agent_dir: str) -> list[dict]:
    """
    Split trade_log.md into individual run sections.
    Returns list of {run_id, timestamp, content} newest-first.
    """
    path = Path(agent_dir) / "trade_log.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    raw_blocks = re.split(r"\n---\n", text)
    sections: list[dict] = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        m = re.search(r"### (.+?) — Run ID: (.+?)$", block, re.MULTILINE)
        if not m:
            continue
        sections.append({
            "timestamp": m.group(1).strip(),
            "run_id":    m.group(2).strip(),
            "content":   block,
        })
    return list(reversed(sections))


@st.cache_data(ttl=300)
def fetch_benchmark_history(start_date: str) -> pd.DataFrame:
    """Fetch SPY/QQQ/SMH from start_date, normalised to 100."""
    try:
        raw = yf.download(
            ["SPY", "QQQ", "SMH"],
            start=start_date,
            auto_adjust=True,
            progress=False,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            close = raw["Close"]
        else:
            close = raw
        close = close.dropna(how="all")
        if close.empty:
            return pd.DataFrame()
        return (close / close.iloc[0]) * 100
    except Exception:
        return pd.DataFrame()


# ── Helpers ───────────────────────────────────────────────────────────────────

def portfolio_value(h: dict) -> float:
    perf = h.get("performance", {})
    return h.get("cash", 0.0) + h.get("margin_reserved", 0.0) + perf.get("long_exposure", 0.0)


def get_sector(ticker: str) -> str:
    return SECTOR_MAP.get(ticker.upper(), "Other")


def fmt_delta(v: float) -> str:
    return f"{v:+.2f}%"


# ── UI Components ─────────────────────────────────────────────────────────────

def render_agent_metrics(h: dict, label: str) -> None:
    if not h:
        st.warning(f"No data for {label} yet.")
        return
    perf = h.get("performance", {})
    pv   = portfolio_value(h)
    ret  = perf.get("total_return_pct", 0.0) * 100

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Portfolio Value",  f"${pv:,.0f}",  delta=fmt_delta(ret))
    c2.metric("Total P&L",        f"${perf.get('total_pnl', 0):+,.0f}")
    c3.metric("Cash",             f"{perf.get('cash_pct', 1)*100:.1f}%")
    c4.metric("Positions",        len(h.get("positions", {})))
    win = perf.get("win_rate")
    c5.metric("Win Rate",         f"{win*100:.0f}%" if win is not None else "n/a",
              help=f"{perf.get('trades_closed', 0)} closed trades")


def render_positions_table(h: dict) -> None:
    positions = h.get("positions", {})
    if not positions:
        st.info("No open positions — fully in cash.")
        return

    rows = []
    for ticker, pos in positions.items():
        price    = pos.get("current_price") or pos.get("avg_cost", 0)
        stop     = pos.get("stop_loss") or 0
        stop_dist = abs(price - stop) / price * 100 if price else 0
        rows.append({
            "Ticker":    ticker,
            "Dir":       pos["direction"].upper(),
            "Shares":    int(pos["shares"]),
            "Avg Cost":  round(pos.get("avg_cost", 0), 2),
            "Current":   round(price, 2),
            "Value $":   round(pos.get("market_value", 0), 0),
            "P&L $":     round(pos.get("unrealized_pnl", 0), 2),
            "P&L %":     round(pos.get("unrealized_pct", 0) * 100, 2),
            "Stop $":    round(stop, 2),
            "Stop Dist": round(stop_dist, 1),
            "Sector":    get_sector(ticker),
        })

    df = pd.DataFrame(rows)

    def color_row(row):
        if row["P&L %"] > 0:
            return ["color: #00c853"] * len(row)
        elif row["P&L %"] < 0:
            return ["color: #ff5252"] * len(row)
        return [""] * len(row)

    st.dataframe(
        df.style.apply(color_row, axis=1),
        use_container_width=True,
        hide_index=True,
    )


def render_equity_curve(swing_eq: list[dict], lt_eq: list[dict]) -> None:
    if not swing_eq and not lt_eq:
        st.info("No equity history yet — equity curve will appear after the first agent run.")
        return

    # Determine benchmark start date from earliest agent record
    all_records = swing_eq + lt_eq
    start_date = min(r["timestamp"][:10] for r in all_records) if all_records else str(date.today())

    fig = go.Figure()

    agent_series = [
        (swing_eq, "Swing Trader",      "#4c9be8"),
        (lt_eq,    "Long-Term Investor", "#f0883e"),
    ]
    for records, name, color in agent_series:
        if not records:
            continue
        xs   = [r["timestamp"][:16].replace("T", " ") for r in records]
        base = records[0]["portfolio_value"] or STARTING_CASH
        ys   = [r["portfolio_value"] / base * 100 for r in records]
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines+markers", name=name,
            line=dict(color=color, width=2),
            marker=dict(size=5),
            hovertemplate="%{x}<br>Value: %{y:.1f}<extra>" + name + "</extra>",
        ))

    # Benchmarks
    bench_df = fetch_benchmark_history(start_date)
    bench_colors = {"SPY": "#57a55a", "QQQ": "#b07fd4", "SMH": "#d4a843"}
    for col in bench_df.columns:
        fig.add_trace(go.Scatter(
            x=bench_df.index.astype(str),
            y=bench_df[col],
            mode="lines",
            name=col,
            line=dict(color=bench_colors.get(col, "#888"), width=1.5, dash="dot"),
            opacity=0.8,
        ))

    fig.update_layout(
        yaxis_title="Indexed to 100 at start",
        xaxis_title=None,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=420,
        margin=dict(l=50, r=20, t=40, b=40),
        hovermode="x unified",
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font=dict(color="#e0e0e0"),
        xaxis=dict(gridcolor="#2a2a2a"),
        yaxis=dict(gridcolor="#2a2a2a"),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_sector_bar(h: dict, title: str) -> None:
    positions = h.get("positions", {})
    if not positions:
        st.caption("No positions.")
        return

    exposure: dict[str, float] = {}
    for ticker, pos in positions.items():
        mv     = pos.get("market_value", 0)
        signed = -mv if pos["direction"] == "short" else mv
        sector = get_sector(ticker)
        exposure[sector] = exposure.get(sector, 0.0) + signed

    sectors = sorted(exposure.keys())
    values  = [exposure[s] for s in sectors]

    fig = go.Figure(go.Bar(
        x=sectors,
        y=values,
        marker_color=["#d32f2f" if v < 0 else "#4c9be8" for v in values],
        text=[f"${v:+,.0f}" for v in values],
        textposition="outside",
    ))
    fig.update_layout(
        title=title,
        yaxis_title="Market Value ($)",
        height=320,
        margin=dict(l=50, r=20, t=50, b=80),
        plot_bgcolor="#0e1117",
        paper_bgcolor="#0e1117",
        font=dict(color="#e0e0e0"),
        xaxis=dict(gridcolor="#2a2a2a", tickangle=-30),
        yaxis=dict(gridcolor="#2a2a2a"),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_trade_log(sections: list[dict], max_shown: int = 30) -> None:
    if not sections:
        st.info("No trade log entries yet.")
        return
    for s in sections[:max_shown]:
        label = f"{s['timestamp']}  —  {s['run_id']}"
        with st.expander(label, expanded=False):
            st.markdown(s["content"])


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="Autonomous Portfolio",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.title("Autonomous Portfolio Dashboard")

    swing_h  = load_holdings(str(SWING_DIR))
    lt_h     = load_holdings(str(LT_DIR))
    swing_eq = load_equity_log(str(SWING_DIR))
    lt_eq    = load_equity_log(str(LT_DIR))

    if not swing_h and not lt_h:
        st.warning(
            "No holdings data found. Run the agents first:\n\n"
            "```\npython main.py --agent both --force\n```"
        )
        return

    swing_h = swing_h or {}
    lt_h    = lt_h    or {}

    # Last-updated caption
    def fmt_ts(ts: str | None) -> str:
        return ts[:16].replace("T", " ") if ts else "never"

    swing_ts = fmt_ts((swing_h.get("meta") or {}).get("last_updated"))
    lt_ts    = fmt_ts((lt_h.get("meta")    or {}).get("last_updated"))
    st.caption(f"Swing last run: **{swing_ts} ET**  |  Long-term last run: **{lt_ts} ET**  |  Data refreshes every 60s")

    # ── Equity curve (full width) ─────────────────────────────────────────
    st.subheader("Equity Curves vs Benchmarks")
    render_equity_curve(swing_eq, lt_eq)

    st.divider()

    # ── Per-agent metrics + positions ─────────────────────────────────────
    col_s, col_l = st.columns(2)

    with col_s:
        st.subheader("Swing Trader")
        render_agent_metrics(swing_h, "Swing")
        st.markdown("**Open Positions**")
        render_positions_table(swing_h)

    with col_l:
        st.subheader("Long-Term Investor")
        render_agent_metrics(lt_h, "Long-Term")
        st.markdown("**Open Positions**")
        render_positions_table(lt_h)

    st.divider()

    # ── Sector exposure ───────────────────────────────────────────────────
    st.subheader("Sector Exposure")
    col_s2, col_l2 = st.columns(2)
    with col_s2:
        render_sector_bar(swing_h, "Swing Trader")
    with col_l2:
        render_sector_bar(lt_h, "Long-Term Investor")

    st.divider()

    # ── Trade logs ────────────────────────────────────────────────────────
    st.subheader("Trade Log")
    tab_s, tab_l = st.tabs(["Swing", "Long-Term"])
    with tab_s:
        render_trade_log(load_trade_log_sections(str(SWING_DIR)))
    with tab_l:
        render_trade_log(load_trade_log_sections(str(LT_DIR)))


if __name__ == "__main__":
    main()
