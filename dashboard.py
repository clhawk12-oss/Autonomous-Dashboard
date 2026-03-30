"""
dashboard.py
------------
Streamlit dashboard for the Autonomous Portfolio Agent System.

Tabs:
    📈 Overview   — equity curves and portfolio metrics
    💼 Positions  — open positions and sector exposure
    📋 Trade Log  — card-style run history with reasoning and trades

Run locally:
    python -m streamlit run dashboard.py
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import pytz
import streamlit as st
import yfinance as yf

# ── Config ────────────────────────────────────────────────────────────────────
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
    "INTC": "Semiconductors", "MCHP": "Semiconductors", "SWKS": "Semiconductors",
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
    # Financials
    "V":    "Financials", "MA":   "Financials", "JPM":  "Financials",
    "BAC":  "Financials", "GS":   "Financials", "BRK-B": "Financials",
    "PYPL": "Fintech",    "SQ":   "Fintech",    "AFRM": "Fintech",
    # Healthcare
    "UNH":  "Healthcare", "LLY":  "Healthcare", "ABBV": "Healthcare",
    "JNJ":  "Healthcare", "PFE":  "Healthcare", "MRNA": "Healthcare",
    # EV / Auto
    "TSLA": "EV/Auto", "RIVN": "EV/Auto",
    # Memory / Storage
    "WDC":  "Memory/Storage", "STX": "Memory/Storage",
    # Telecom / Satellite
    "TSEM": "Semiconductors",
}

AGENT_CONFIG = {
    "swing":     {"label": "Swing Trader",      "color": "#4c9be8", "icon": "⚡"},
    "long_term": {"label": "Long-Term Investor", "color": "#f0883e", "icon": "🌱"},
}

_ACTION_ICON = {
    "BUY":     "🟢",
    "SELL":    "🔴",
    "SHORT":   "🔻",
    "COVER":   "🔵",
    "SKIPPED": "⏭",
}
_ACTION_COLOR = {
    "BUY":     "#00c853",
    "SELL":    "#ff5252",
    "SHORT":   "#ff6d00",
    "COVER":   "#4c9be8",
    "SKIPPED": "#888888",
}


# ── Data loaders ──────────────────────────────────────────────────────────────

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
def load_equity_log(agent_dir: str) -> list:
    path = Path(agent_dir) / "equity_log.jsonl"
    records = []
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
def load_trade_log_parsed(agent_dir: str) -> list:
    """
    Parse trade_log.md into a list of structured run dicts, newest-first.

    Each dict contains:
        timestamp_str, run_id, market, reasoning, benchmarks,
        trades: [{action, ticker, shares, price, note}],
        portfolio_cash, portfolio_pnl_pct
    """
    path = Path(agent_dir) / "trade_log.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    raw_blocks = re.split(r"\n---\n", text)
    runs = []

    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        m = re.search(r"### (.+?) — Run ID: (.+?)$", block, re.MULTILINE)
        if not m:
            continue

        timestamp_str = m.group(1).strip()
        run_id        = m.group(2).strip()

        def _field(pattern: str) -> str:
            mo = re.search(pattern, block, re.MULTILINE)
            return mo.group(1).strip() if mo else ""

        reasoning  = _field(r"\*\*Reasoning\*\*: (.+?)$")
        benchmarks = _field(r"\*\*Benchmarks\*\*: (.+?)$")
        market     = _field(r"\*\*Market\*\*: (.+?)$")

        trades = []
        for tm in re.finditer(
            r"^\| (BUY|SELL|SHORT|COVER|SKIPPED) \| (\S+) \| (\d+) \| \$([\d,]+\.?\d*) \| (.+?) \|",
            block,
            re.MULTILINE,
        ):
            trades.append({
                "action": tm.group(1),
                "ticker": tm.group(2),
                "shares": int(tm.group(3)),
                "price":  float(tm.group(4).replace(",", "")),
                "note":   tm.group(5).strip(),
            })

        port_m = re.search(
            r"\*\*Portfolio after run\*\*: Cash \$([\d,]+\.?\d*) \| P&L "
            r"\$([\+\-][\d,]+\.?\d*) \(([\+\-][\d.]+)%\)",
            block,
        )
        runs.append({
            "timestamp_str":     timestamp_str,
            "run_id":            run_id,
            "market":            market,
            "reasoning":         reasoning,
            "benchmarks":        benchmarks,
            "trades":            trades,
            "portfolio_cash":    float(port_m.group(1).replace(",", "")) if port_m else None,
            "portfolio_pnl_pct": float(port_m.group(3)) if port_m else None,
        })

    return list(reversed(runs))


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

def escape_md_dollars(text: str) -> str:
    """Escape bare dollar signs so Streamlit doesn't treat them as LaTeX delimiters."""
    # Replace $ not already escaped and not part of a known markdown construct
    return re.sub(r'(?<!\\)\$', r'\\$', text)


def parse_reasoning_sections(reasoning: str) -> dict:
    """
    Try to split structured reasoning into Macro / Sectors / Positions sections.
    Returns {'macro': ..., 'sectors': ..., 'positions': ...} if structure found,
    or {'full': reasoning} for old single-paragraph format.
    """
    sections: dict = {}
    for label, key in [("Macro", "macro"), ("Sectors", "sectors"), ("Positions", "positions")]:
        m = re.search(
            rf'\*\*{label}:\*\*\s*(.+?)(?=\*\*(?:Macro|Sectors|Positions):\*\*|$)',
            reasoning,
            re.DOTALL,
        )
        if m:
            sections[key] = m.group(1).strip()
    return sections if sections else {"full": reasoning}


def format_trade_note(note: str) -> str:
    """
    Format a trade note as markdown bullet points.
    If the note already contains bullet structure (• or -), return as-is.
    Otherwise split on semicolons and sentence boundaries.
    """
    if not note:
        return ""
    note = escape_md_dollars(note)
    # Already structured with bullets
    if "•" in note or "\n-" in note or "\n•" in note:
        return note
    # Split on semicolons first — Claude often uses these as natural separators
    parts = [p.strip() for p in note.split(";") if p.strip()]
    if len(parts) > 1:
        return "\n".join(f"- {p.rstrip('.')}" for p in parts)
    # Fall back to splitting on ". " before a capital letter
    parts = re.split(r'\. (?=[A-Z])', note)
    parts = [p.strip().rstrip(".") for p in parts if p.strip()]
    if len(parts) > 1:
        return "\n".join(f"- {p}" for p in parts)
    return note


def portfolio_value(h: dict) -> float:
    """
    True net equity: what we'd have if all positions closed at current prices.
    For shorts: margin (locked collateral) is released, but we pay current market value to close.
    Formula: cash + margin_reserved + long_exposure - short_exposure
    """
    perf = h.get("performance", {})
    return (
        h.get("cash", 0.0)
        + h.get("margin_reserved", 0.0)
        + perf.get("long_exposure", 0.0)
        - perf.get("short_exposure", 0.0)
    )


def get_sector(ticker: str) -> str:
    return SECTOR_MAP.get(ticker.upper(), "Other")


def fmt_delta(v: float) -> str:
    return f"{v:+.2f}%"


def time_ago(timestamp_str: str) -> str:
    """Convert '2026-03-30 17:25 ET' → '2h ago', '3d ago', etc."""
    try:
        dt   = datetime.strptime(timestamp_str[:16], "%Y-%m-%d %H:%M")
        dt   = ET_TZ.localize(dt)
        mins = int((datetime.now(ET_TZ) - dt).total_seconds() / 60)
        if mins < 60:
            return f"{mins}m ago"
        if mins < 1440:
            return f"{mins // 60}h ago"
        return f"{mins // 1440}d ago"
    except Exception:
        return timestamp_str


def run_title(trades: list) -> str:
    """Generate a one-line human title from a run's trade list."""
    real = [t for t in trades if t["action"] != "SKIPPED"]
    if not real:
        return "Held all positions — no trades"
    parts = []
    for verb, action in [("Bought", "BUY"), ("Sold", "SELL"), ("Shorted", "SHORT"), ("Covered", "COVER")]:
        group = [t for t in real if t["action"] == action]
        if not group:
            continue
        tickers = ", ".join(t["ticker"] for t in group)
        parts.append(f"{verb} {tickers}")
    return "  ·  ".join(parts)


# ── UI Components ─────────────────────────────────────────────────────────────

def render_agent_metrics(h: dict, label: str) -> None:
    if not h:
        st.warning(f"No data for {label} yet.")
        return
    perf = h.get("performance", {})
    pv   = portfolio_value(h)
    ret  = perf.get("total_return_pct", 0.0) * 100
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Portfolio Value", f"${pv:,.0f}",   delta=fmt_delta(ret))
    c2.metric("Total P&L",       f"${perf.get('total_pnl', 0):+,.0f}")
    c3.metric("Cash",            f"{perf.get('cash_pct', 1)*100:.1f}%")
    c4.metric("Positions",       len(h.get("positions", {})))
    win = perf.get("win_rate")
    c5.metric("Win Rate",        f"{win*100:.0f}%" if win is not None else "n/a",
              help=f"{perf.get('trades_closed', 0)} closed trades")


def render_positions_table(h: dict) -> None:
    positions = h.get("positions", {})
    if not positions:
        st.info("No open positions — fully in cash.")
        return
    rows = []
    for ticker, pos in positions.items():
        price     = pos.get("current_price") or pos.get("avg_cost", 0)
        stop      = pos.get("stop_loss")
        stop_dist = (abs(price - stop) / price * 100) if (price and stop) else None
        rows.append({
            "Ticker":    ticker,
            "Dir":       pos["direction"].upper(),
            "Shares":    int(pos["shares"]),
            "Avg Cost":  round(pos.get("avg_cost", 0), 2),
            "Current":   round(price, 2),
            "Value":     round(pos.get("market_value", 0), 2),
            "P&L $":     round(pos.get("unrealized_pnl", 0), 2),
            "P&L %":     round(pos.get("unrealized_pct", 0) * 100, 2),
            "Stop":      f"${stop:.2f}" if stop else "—",
            "Stop Dist": f"{stop_dist:.1f}%" if stop_dist is not None else "—",
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
        df.style
          .apply(color_row, axis=1)
          .format({
              "Avg Cost": "${:.2f}",
              "Current":  "${:.2f}",
              "Value":    "${:,.2f}",
              "P&L $":    "${:+.2f}",
              "P&L %":    "{:+.2f}%",
          }),
        use_container_width=True,
        hide_index=True,
    )


def render_equity_curve(swing_eq: list, lt_eq: list) -> None:
    if not swing_eq and not lt_eq:
        st.info("No equity history yet — equity curve will appear after the first agent run.")
        return

    from datetime import date as date_cls
    all_records = swing_eq + lt_eq
    start_date  = min(r["timestamp"][:10] for r in all_records) if all_records else str(date_cls.today())

    fig = go.Figure()

    for records, name, color in [
        (swing_eq, "Swing Trader",       "#4c9be8"),
        (lt_eq,    "Long-Term Investor",  "#f0883e"),
    ]:
        if not records:
            continue
        xs   = [r["timestamp"][:16].replace("T", " ") for r in records]
        base = records[0]["portfolio_value"] or STARTING_CASH
        ys   = [r["portfolio_value"] / base * 100 for r in records]
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines+markers", name=name,
            line=dict(color=color, width=2),
            marker=dict(size=5),
            hovertemplate="%{x}<br>Index: %{y:.1f}<extra>" + name + "</extra>",
        ))

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
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            bgcolor="rgba(20, 20, 30, 0.85)",
            bordercolor="#555",
            borderwidth=1,
            font=dict(color="#ffffff", size=12),
        ),
        height=420,
        margin=dict(l=50, r=20, t=60, b=40),
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


def render_run_card(run: dict, agent_key: str) -> None:
    """Render a single agent run as a styled card."""
    cfg     = AGENT_CONFIG.get(agent_key, AGENT_CONFIG["swing"])
    trades  = run.get("trades", [])
    real    = [t for t in trades if t["action"] != "SKIPPED"]
    skipped = [t for t in trades if t["action"] == "SKIPPED"]
    title   = run_title(trades)
    ago     = time_ago(run["timestamp_str"])
    pnl     = run.get("portfolio_pnl_pct")

    with st.container(border=True):
        # ── Header: agent + date + P&L + time ago ─────────────────────────
        h1, h2, h3 = st.columns([3, 2, 1])
        with h1:
            st.markdown(
                f"<span style='color:{cfg['color']}; font-weight:600'>"
                f"{cfg['icon']} {cfg['label']}</span>",
                unsafe_allow_html=True,
            )
            # Prominent date on its own line
            st.markdown(
                f"<span style='font-size:0.85em; color:#aaa'>"
                f"📅 {run['timestamp_str']}</span>",
                unsafe_allow_html=True,
            )
        with h2:
            if pnl is not None:
                color = "#00c853" if pnl >= 0 else "#ff5252"
                arrow = "▲" if pnl >= 0 else "▼"
                st.markdown(
                    f"<span style='color:{color}'>{arrow} {pnl:+.2f}%</span>",
                    unsafe_allow_html=True,
                )
        with h3:
            st.caption(ago)

        # ── Benchmarks ────────────────────────────────────────────────────
        if run.get("benchmarks"):
            st.caption("📊 " + run["benchmarks"].replace(" | ", "  ·  "))

        # ── Title ─────────────────────────────────────────────────────────
        st.markdown(f"**{title}**")

        # ── Reasoning — parsed into labeled sections if structured ────────
        if run.get("reasoning"):
            sections = parse_reasoning_sections(run["reasoning"])
            if "full" in sections:
                # Old single-paragraph format — display as-is
                st.markdown(escape_md_dollars(sections["full"]))
            else:
                # New structured format — display each section with a label
                section_config = [
                    ("macro",     "📊 Macro",     "#7ec8e3"),
                    ("sectors",   "🏭 Sectors",   "#f0883e"),
                    ("positions", "📋 Positions", "#a8d8a8"),
                ]
                for key, label, color in section_config:
                    text = sections.get(key, "")
                    if not text:
                        continue
                    st.markdown(
                        f"<span style='color:{color}; font-weight:600'>{label}</span>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(escape_md_dollars(text))

        # ── Individual trades ─────────────────────────────────────────────
        if real:
            st.markdown("---")
            for trade in real:
                icon  = _ACTION_ICON.get(trade["action"], "•")
                color = _ACTION_COLOR.get(trade["action"], "#e0e0e0")
                st.markdown(
                    f"{icon} <span style='color:{color}; font-weight:700'>"
                    f"{trade['action']}</span> &nbsp;"
                    f"**{trade['ticker']}** &nbsp;·&nbsp; "
                    f"{trade['shares']} shares @ \\${trade['price']:,.2f}",
                    unsafe_allow_html=True,
                )
                if trade.get("note"):
                    formatted = format_trade_note(trade["note"])
                    st.markdown(formatted)

        # ── Skipped ───────────────────────────────────────────────────────
        if skipped:
            n = len(skipped)
            with st.expander(f"⏭ {n} skipped action{'s' if n > 1 else ''}"):
                for t in skipped:
                    st.caption(f"**{t['ticker']}** — {t['note']}")

        # ── Footer ────────────────────────────────────────────────────────
        footer_parts = []
        if run.get("portfolio_cash") is not None:
            footer_parts.append(f"Cash \\${run['portfolio_cash']:,.0f}")
        if pnl is not None:
            footer_parts.append(f"P&L {pnl:+.2f}%")
        if footer_parts:
            st.caption("After run: " + "  |  ".join(footer_parts))


def render_trade_log_tab(swing_runs: list, lt_runs: list) -> None:
    """Render the Trade Log tab — card feed with All / Swing / Long-Term sub-tabs."""
    # Tag each run with its agent
    for r in swing_runs:
        r["_agent"] = "swing"
    for r in lt_runs:
        r["_agent"] = "long_term"

    all_runs = sorted(
        swing_runs + lt_runs,
        key=lambda r: r["timestamp_str"],
        reverse=True,
    )

    # Filter toggle
    trades_only = st.toggle(
        "Show runs with trades only",
        value=False,
        help="Hide runs where the agent held and made no new trades",
    )

    sub_all, sub_swing, sub_lt = st.tabs(["All", "⚡ Swing", "🌱 Long-Term"])

    def _render_runs(runs: list, default_agent: Optional[str] = None) -> None:
        filtered = [
            r for r in runs
            if not trades_only
            or any(t["action"] != "SKIPPED" for t in r.get("trades", []))
        ]
        if not filtered:
            st.info("No runs to display.")
            return
        for run in filtered[:60]:
            agent_key = run.get("_agent") or default_agent or "swing"
            render_run_card(run, agent_key)

    with sub_all:
        _render_runs(all_runs)
    with sub_swing:
        _render_runs(swing_runs, "swing")
    with sub_lt:
        _render_runs(lt_runs, "long_term")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    st.set_page_config(
        page_title="Autonomous Portfolio",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.title("📈 Autonomous Portfolio Dashboard")

    # ── Load all data ──────────────────────────────────────────────────────
    swing_h    = load_holdings(str(SWING_DIR))    or {}
    lt_h       = load_holdings(str(LT_DIR))       or {}
    swing_eq   = load_equity_log(str(SWING_DIR))
    lt_eq      = load_equity_log(str(LT_DIR))
    swing_runs = load_trade_log_parsed(str(SWING_DIR))
    lt_runs    = load_trade_log_parsed(str(LT_DIR))

    if not swing_h and not lt_h:
        st.warning(
            "No holdings data found. Run the agents first:\n\n"
            "```\npython main.py --agent both --force\n```"
        )
        return

    # ── Last-updated caption ───────────────────────────────────────────────
    def _fmt_ts(ts: Optional[str]) -> str:
        return ts[:16].replace("T", " ") if ts else "never"

    swing_ts = _fmt_ts((swing_h.get("meta") or {}).get("last_updated"))
    lt_ts    = _fmt_ts((lt_h.get("meta") or {}).get("last_updated"))
    st.caption(
        f"⚡ Swing last run: **{swing_ts} ET**  |  "
        f"🌱 Long-Term last run: **{lt_ts} ET**  |  "
        f"Auto-refreshes every 60s"
    )

    # ── Top-level tabs ─────────────────────────────────────────────────────
    tab_overview, tab_positions, tab_log = st.tabs(
        ["📈 Overview", "💼 Positions", "📋 Trade Log"]
    )

    # ── Overview ───────────────────────────────────────────────────────────
    with tab_overview:
        st.subheader("Equity Curves vs Benchmarks")
        render_equity_curve(swing_eq, lt_eq)
        st.divider()
        col_s, col_l = st.columns(2)
        with col_s:
            st.markdown("### ⚡ Swing Trader")
            render_agent_metrics(swing_h, "Swing")
        with col_l:
            st.markdown("### 🌱 Long-Term Investor")
            render_agent_metrics(lt_h, "Long-Term")

    # ── Positions ──────────────────────────────────────────────────────────
    with tab_positions:
        col_s, col_l = st.columns(2)
        with col_s:
            st.markdown("### ⚡ Swing Trader")
            render_positions_table(swing_h)
        with col_l:
            st.markdown("### 🌱 Long-Term Investor")
            render_positions_table(lt_h)
        st.divider()
        st.subheader("Sector Exposure")
        col_s2, col_l2 = st.columns(2)
        with col_s2:
            render_sector_bar(swing_h, "Swing Trader")
        with col_l2:
            render_sector_bar(lt_h, "Long-Term Investor")

    # ── Trade Log ──────────────────────────────────────────────────────────
    with tab_log:
        render_trade_log_tab(swing_runs, lt_runs)


if __name__ == "__main__":
    main()
