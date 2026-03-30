"""
agent.py
--------
Anthropic API decision engine.

Builds the three-layer prompt, calls Claude, parses and validates the
structured JSON response, and returns a decision dict ready for main.py
to execute.
"""

import json
import re
import time as time_module
from typing import Optional

import anthropic

from config import (
    ANTHROPIC_API_KEY,
    SWING_MODEL,
    LONG_TERM_MODEL,
    MAX_TOKENS_SWING,
    MAX_TOKENS_LONG,
    STARTING_CASH,
    SWING_MAX_POSITIONS,
    SWING_MIN_WEIGHT,
    SWING_MAX_WEIGHT,
    LONG_TERM_MAX_POSITIONS,
    LONG_TERM_MIN_WEIGHT,
    LONG_TERM_MAX_WEIGHT,
    MIN_CASH_BUFFER,
    TRADEABLE_UNIVERSE,
)

# ---------------------------------------------------------------------------
# Retry config
# ---------------------------------------------------------------------------
MAX_RETRIES       = 3
RETRY_BASE_DELAY  = 5   # seconds; doubles each attempt


# ---------------------------------------------------------------------------
# System prompt (Layer 1 — static per agent type)
# ---------------------------------------------------------------------------

_SWING_SYSTEM = f"""You are an autonomous paper trading agent managing a SWING TRADING account.
Your sole objective is to maximise risk-adjusted returns by capturing short-to-medium-term price movements.

COORDINATION
A read-only snapshot of the Long-Term Investor's current holdings is provided in your context.
Do not SHORT a ticker the long-term agent holds as a core long position unless you have an explicit
short-term thesis and acknowledge the conflict in your reasoning. The two portfolios should not work
against each other without deliberate justification.

MANDATE
- Tradeable universe: {', '.join(TRADEABLE_UNIVERSE)}
- Both LONG and SHORT positions are permitted
- Target holding period: several days to ~2 months; exit when thesis breaks or target is hit
- Max {SWING_MAX_POSITIONS} concurrent positions
- Position sizing: up to {int(SWING_MAX_WEIGHT*100)}% of total portfolio value per position; no minimum — small positions (1–3%) are fine when conviction is moderate
- Stop losses optional — set one on any position where you want downside protection
- Starting capital: ${STARTING_CASH:,.0f}

CASH AS A STRATEGIC POSITION
Cash is not a default — it is an active position like any other.
- When markets offer high-conviction setups across multiple names: deploy aggressively, as low as {int(MIN_CASH_BUFFER*100)}% cash.
- When risk/reward is poor, volatility is extreme, or you lack conviction: hold 50–90%+ cash.
- Never hold cash simply because you have nothing to do. Explicitly state your cash rationale in your reasoning.
The only hard floor is {int(MIN_CASH_BUFFER*100)}% cash — enforced by the system and cannot be breached.

ANALYSIS APPROACH
Use all available information to form your view:
- Technical: price action, momentum, volume, distance from 52W high/low, ATR
- Macro: interest rate environment, sector rotation, risk-on/risk-off regime
- Fundamental: earnings trends, competitive dynamics, valuation relative to growth
- News & catalysts: earnings beats/misses, guidance, product launches, regulatory events
Draw on your training knowledge of recent market conditions, earnings results, and macro trends
to complement the price data provided. Your knowledge has a training cutoff — acknowledge when
you are reasoning from potentially stale information.

SHORT SELLING (paper trading simulation)
- SHORT entry: receive proceeds, post 150% margin. Net cash = −0.5 × (shares × price)
- COVER: margin released, pay current price to close
- Annual borrow cost 2% accrues daily on short positions

OUTPUT FORMAT
Return ONLY valid JSON — no prose, no markdown fences, no text outside the JSON object.
{{
  "reasoning": "2-3 sentences: market view, regime, and your current portfolio stance",
  "actions": [
    {{
      "action": "BUY" | "SELL" | "SHORT" | "COVER",
      "ticker": "NVDA",
      "shares": 25,
      "rationale": "REQUIRED on every action — (1) approx portfolio weight% this represents, (2) why that size and not more/less: conviction level and specific risk being taken, (3) how it fits with existing exposure: sector, direction, or theme overlap",
      "thesis": "REQUIRED for BUY and SHORT — your edge and expected catalyst",
      "stop_loss_pct": 0.06,
      "take_profit_pct": 0.18
    }}
  ],
  "summary": "paragraph PM narrative — what you did, why, and what you are watching",
  "memory_update": {{
    "market_notes": "your updated market regime and macro assessment (replaces prior notes)",
    "watching": ["specific catalyst or level to monitor", "another item"]
  }}
}}

RULES
1. Empty actions array is valid — HOLD the portfolio when no compelling setup exists.
2. Cannot SHORT a long position or BUY a short position without closing first.
3. shares must be a positive integer.
4. stop_loss_pct is optional for BUY and SHORT (decimal, e.g. 0.06 = 6%). Omit or set to null if you do not want a stop on this position.
5. Do not exceed {SWING_MAX_POSITIONS} total positions after all actions.
6. Position size cap is {int(SWING_MAX_WEIGHT*100)}% of portfolio value. No minimum — size freely based on conviction.
7. Cash must not drop below {int(MIN_CASH_BUFFER*100)}% after all trades (system enforced).
8. Only trade tickers in the approved universe above.
9. Always populate memory_update — this is how you leave notes for your future self.
   market_notes should reflect your current macro/regime view.
   watching should list specific catalysts, earnings dates, price levels, or events to monitor.
10. If "## EARNINGS ALERT" appears in context, you MUST explicitly address each flagged position
    in your reasoning — state whether you are holding, trimming, or exiting into earnings and why.
11. Every action MUST include a rationale covering all three elements: portfolio weight%, sizing
    justification (conviction + specific risk), and fit with existing exposure.
"""

_LONG_TERM_SYSTEM = f"""You are an autonomous paper trading agent managing a LONG-TERM CAPITAL APPRECIATION account.
Your sole objective is to compound capital over years by owning the best businesses at attractive prices.

COORDINATION
A read-only snapshot of the Swing Trader's current holdings is provided in your context.
Ignore its short-term positions for your long-term thesis. However, if the swing agent is heavily
SHORT a name you are considering for a long-term buy, treat that as a momentum signal worth
noting in your reasoning — it does not override your thesis but is worth acknowledging.

MANDATE
- Tradeable universe: {', '.join(TRADEABLE_UNIVERSE)}
- LONG ONLY — no short selling, no shorting, no inverse bets of any kind
- Target holding period: months to years; sell only when thesis is broken or valuation becomes extreme
- Max {LONG_TERM_MAX_POSITIONS} concurrent positions
- Position sizing: up to {int(LONG_TERM_MAX_WEIGHT*100)}% of total portfolio value per position; no minimum — small starter positions (1–3%) are fine when building conviction
- Stop losses optional — set one on any position where you want downside protection
- Starting capital: ${STARTING_CASH:,.0f}

CASH AS A STRATEGIC POSITION
Cash is not a default — it is an active decision.
- When you have high conviction across multiple names: deploy aggressively, as low as {int(MIN_CASH_BUFFER*100)}% cash.
- When valuations are stretched, macro risks are elevated, or your conviction is low: hold 30–70%+ cash patiently.
- Never sell a good position just to raise cash. Raise cash by not buying, or by trimming extended winners.
The only hard floor is {int(MIN_CASH_BUFFER*100)}% cash — enforced by the system.

INVESTMENT PHILOSOPHY
You are a long-term fundamental investor. Prioritise:
- Durable competitive advantages (moats): network effects, switching costs, scale economies, IP
- Long runway for growth: large TAM, underpenetrated markets, secular tailwinds
- Management quality: capital allocation track record, insider ownership, candour
- Reasonable valuation relative to long-term earnings power — you are not a momentum trader
- Portfolio construction: diversify across sectors and themes, avoid excessive concentration

ANALYSIS APPROACH
Use all available information:
- Fundamental: business quality, revenue growth trajectory, margins, FCF generation, balance sheet
- Valuation: where is the stock relative to intrinsic value and historical multiples
- Technical: use price action only for timing entries/exits, not as primary thesis driver
- Macro: long-cycle themes — AI infrastructure buildout, energy transition, healthcare innovation,
  financial digitalisation, global consumer trends
- News & catalysts: earnings, management changes, competitive threats, regulatory shifts
Draw on your training knowledge of businesses, industries, and macro trends.
Acknowledge when reasoning from potentially stale data.

OUTPUT FORMAT
Return ONLY valid JSON — no prose, no markdown fences, no text outside the JSON object.
{{
  "reasoning": "2-3 sentences: your long-term macro view and portfolio construction rationale",
  "actions": [
    {{
      "action": "BUY" | "SELL",
      "ticker": "NVDA",
      "shares": 30,
      "rationale": "REQUIRED on every action — (1) approx portfolio weight% this represents, (2) why that size and not more/less: conviction level and time horizon confidence, (3) how it fits with existing holdings: sector, theme, or concentration impact",
      "thesis": "REQUIRED for BUY — multi-sentence thesis covering moat, growth, valuation",
      "stop_loss_pct": 0.12,
      "take_profit_pct": null
    }}
  ],
  "summary": "paragraph PM narrative — portfolio construction logic and key positions/themes",
  "memory_update": {{
    "market_notes": "your updated long-term macro and thematic view (replaces prior notes)",
    "watching": ["specific catalyst or event to monitor", "another item"]
  }}
}}

RULES
1. Empty actions array is valid — do NOT trade just to appear active. Patience is a virtue.
2. Only BUY, SELL, and COVER are permitted. SHORT will be rejected. COVER is only valid to close an existing short position — no new shorts may be opened.
3. shares must be a positive integer.
4. stop_loss_pct is optional for BUY (decimal, e.g. 0.12 = 12%). Omit or set to null if you do not want a stop on this position.
5. Do not exceed {LONG_TERM_MAX_POSITIONS} total positions after all actions.
6. Position size cap is {int(LONG_TERM_MAX_WEIGHT*100)}% of portfolio value. No minimum — size freely based on conviction.
7. Cash must not drop below {int(MIN_CASH_BUFFER*100)}% after all trades (system enforced).
8. Only trade tickers in the approved universe above.
9. Thesis quality matters more than activity. A well-reasoned HOLD beats a poorly-reasoned trade.
10. Always populate memory_update — this is your continuity across sessions.
    market_notes should capture your current structural macro/thematic view.
    watching should list earnings dates, catalysts, valuation targets, or macro events.
11. If "## EARNINGS ALERT" appears in context, you MUST explicitly address each flagged position
    in your reasoning — state whether you are holding, trimming, or adding into earnings and why.
12. Every action MUST include a rationale covering all three elements: portfolio weight%, sizing
    justification (conviction + time horizon confidence), and fit with existing holdings.
"""

SYSTEM_PROMPTS = {
    "swing":     _SWING_SYSTEM,
    "long_term": _LONG_TERM_SYSTEM,
}


# ---------------------------------------------------------------------------
# Context builders (Layer 2 — dynamic)
# ---------------------------------------------------------------------------

def _fmt_pct(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    return f"{v*100:+.1f}%"


def _fmt_price(v: Optional[float]) -> str:
    if v is None:
        return "n/a"
    return f"${v:,.2f}"


def build_portfolio_context(holdings: dict, market_status: dict) -> str:
    """Serialise current portfolio state into a readable context block."""
    cash     = holdings.get("cash", 0.0)
    margin   = holdings.get("margin_reserved", 0.0)
    perf     = holdings.get("performance", {})
    positions = holdings.get("positions", {})

    lines = [
        f"## Current Portfolio ({market_status.get('current_et', 'n/a')})",
        f"Cash: ${cash:,.2f}  |  Margin reserved: ${margin:,.2f}",
        f"Total P&L: {_fmt_pct(perf.get('total_return_pct'))} (${perf.get('total_pnl', 0):+,.2f})",
        f"Long exposure: ${perf.get('long_exposure', 0):,.2f}  |  "
        f"Short exposure: ${perf.get('short_exposure', 0):,.2f}",
        f"Win rate: {perf.get('win_rate') or 'n/a'} over {perf.get('trades_closed', 0)} closed trades",
        "",
    ]

    if positions:
        lines.append("### Open Positions")
        lines.append("Ticker | Dir   | Shares | Avg Cost | Current | Unreal P&L | Stop   | Thesis excerpt")
        lines.append("-------|-------|--------|----------|---------|------------|--------|---------------")
        for ticker, pos in positions.items():
            thesis_excerpt = (pos.get("thesis") or "")[:400]
            lines.append(
                f"{ticker:6s} | {pos['direction']:5s} | {pos['shares']:6.0f} | "
                f"{_fmt_price(pos.get('avg_cost')):9s} | "
                f"{_fmt_price(pos.get('current_price')):7s} | "
                f"{_fmt_pct(pos.get('unrealized_pct')):10s} | "
                f"{_fmt_price(pos.get('stop_loss')):6s} | {thesis_excerpt}"
            )
    else:
        lines.append("### Open Positions: none (fully cash)")

    recent_closed = holdings.get("closed_positions", [])[-5:]
    if recent_closed:
        lines.append("")
        lines.append("### Recent Closed Positions (last 5)")
        for cp in reversed(recent_closed):
            lines.append(
                f"  {cp.get('ticker')} {cp.get('direction')} — "
                f"P&L {_fmt_pct(cp.get('realized_pct'))} — "
                f"Closed {cp.get('date_closed')} — {cp.get('exit_reason', '')}"
            )

    return "\n".join(lines)


def build_technicals_context(technicals: dict[str, dict]) -> str:
    """Format watchlist technical summaries into a compact table."""
    lines = [
        "## Watchlist Technicals",
        "Ticker | Price    | 1W Ret | 1M Ret | %52wHi | VolRatio | ATR14",
        "-------|----------|--------|--------|--------|----------|------",
    ]
    for t, s in sorted(technicals.items()):
        if s.get("price") is None:
            lines.append(f"{t:6s} | no data")
            continue
        lines.append(
            f"{t:6s} | {_fmt_price(s['price']):8s} | "
            f"{_fmt_pct(s.get('return_1w')):6s} | "
            f"{_fmt_pct(s.get('return_1m')):6s} | "
            f"{_fmt_pct(s.get('pct_from_52w_high')):6s} | "
            f"{s.get('volume_ratio') or 'n/a':8} | "
            f"{s.get('atr_14') or 'n/a'}"
        )
    return "\n".join(lines)


def build_benchmark_context(technicals: dict[str, dict]) -> str:
    """Extract benchmark context for SPY, QQQ, SMH."""
    lines = ["## Benchmark Returns"]
    for ticker in ["SPY", "QQQ", "SMH"]:
        s = technicals.get(ticker, {})
        lines.append(
            f"  {ticker}: "
            f"1W {_fmt_pct(s.get('return_1w'))}  "
            f"1M {_fmt_pct(s.get('return_1m'))}"
        )
    return "\n".join(lines)


def build_memory_context(memory: dict) -> str:
    """
    Format the agent's persisted memory into a context block.
    Returns empty string on first run (no memory yet).
    """
    if not memory or (
        not memory.get("market_notes")
        and not memory.get("watching")
        and not memory.get("recent_runs")
    ):
        return ""

    lines = ["## Your Memory (from prior runs)"]

    if memory.get("market_notes"):
        lines.append(f"**Market notes**: {memory['market_notes']}")

    watching = memory.get("watching", [])
    if watching:
        lines.append("**Watching**:")
        for item in watching:
            lines.append(f"  - {item}")

    recent_runs = memory.get("recent_runs", [])
    if recent_runs:
        lines.append("")
        lines.append("**Recent run history** (newest first):")
        for run in reversed(recent_runs):
            actions_str = ", ".join(run.get("actions_taken", [])) or "no trades"
            lines.append(
                f"  [{run.get('run_id', '?')} | {run.get('timestamp', '?')}] "
                f"{run.get('reasoning', '')} "
                f"| Actions: {actions_str}"
            )

    return "\n".join(lines)


def build_peer_context(peer_holdings: dict, peer_agent: str) -> str:
    """
    Compact read-only view of the other agent's portfolio.
    Prevents blind conflicts (e.g. swing shorting what long-term is building).
    """
    if not peer_holdings or not peer_holdings.get("positions"):
        peer_name = "Long-Term Investor" if peer_agent == "long_term" else "Swing Trader"
        return f"## {peer_name} Portfolio (peer — read-only)\nNo open positions."

    peer_name = "Long-Term Investor" if peer_agent == "long_term" else "Swing Trader"
    positions = peer_holdings["positions"]
    perf      = peer_holdings.get("performance", {})
    cash      = peer_holdings.get("cash", 0)

    lines = [
        f"## {peer_name} Portfolio (peer — read-only, for coordination)",
        f"Cash: ${cash:,.0f}  |  "
        f"Long: ${perf.get('long_exposure', 0):,.0f}  |  "
        f"Short: ${perf.get('short_exposure', 0):,.0f}",
        "",
        "Ticker | Dir   | Shares | Avg Cost | P&L%",
        "-------|-------|--------|----------|-----",
    ]
    for ticker, pos in positions.items():
        lines.append(
            f"{ticker:6s} | {pos['direction']:5s} | {pos['shares']:6.0f} | "
            f"${pos.get('avg_cost', 0):8.2f} | "
            f"{pos.get('unrealized_pct', 0)*100:+.1f}%"
        )
    return "\n".join(lines)


def build_news_context(news: dict[str, list]) -> str:
    """Format recent headlines for held positions into a context block."""
    if not news:
        return ""
    lines = ["## Recent News — Held Positions (last 7 days)"]
    for ticker in sorted(news.keys()):
        items = news[ticker]
        if not items:
            continue
        lines.append(f"**{ticker}**")
        for item in items:
            h = item["hours_ago"]
            age = f"{h:.0f}h ago" if h < 48 else f"{h/24:.0f}d ago"
            lines.append(f"  - [{age}] {item['title']} ({item['publisher']})")
    return "\n".join(lines)


def build_earnings_context(earnings: dict[str, str]) -> str:
    """Format upcoming earnings dates for held positions."""
    if not earnings:
        return ""
    lines = [
        "## EARNINGS ALERT — held positions reporting in the next 7 days",
        "You MUST address each of these in your reasoning (hold / trim / exit / add — and why).",
    ]
    for ticker, date_str in sorted(earnings.items()):
        lines.append(f"  - **{ticker}**: reports {date_str}")
    return "\n".join(lines)


def build_watchlist_news_context(news: dict[str, list]) -> str:
    """Format recent headlines for unowned watchlist tickers (opportunity scan)."""
    if not news:
        return ""
    lines = ["## Market News — Watchlist Opportunities (last 3 days)"]
    for ticker in sorted(news.keys()):
        items = news[ticker]
        if not items:
            continue
        lines.append(f"**{ticker}**")
        for item in items:
            h = item["hours_ago"]
            age = f"{h:.0f}h ago" if h < 48 else f"{h/24:.0f}d ago"
            lines.append(f"  - [{age}] {item['title']} ({item['publisher']})")
    return "\n".join(lines)


def build_watchlist_earnings_context(earnings: dict[str, str]) -> str:
    """Format upcoming earnings for watchlist tickers not currently held."""
    if not earnings:
        return ""
    lines = ["## Upcoming Earnings — Watchlist (next 14 days, not currently held)"]
    for ticker, date_str in sorted(earnings.items()):
        lines.append(f"  - {ticker}: reports {date_str}")
    return "\n".join(lines)


def build_fundamentals_context(fundamentals: dict[str, dict]) -> str:
    """Format fundamental metrics into a compact table for all tradeable tickers."""
    if not fundamentals:
        return ""

    def _fmt_cap(v: Optional[float]) -> str:
        if v is None: return "n/a"
        if v >= 1e12: return f"${v/1e12:.1f}T"
        if v >= 1e9:  return f"${v/1e9:.0f}B"
        return f"${v/1e6:.0f}M"

    def _fmt_pe(v: Optional[float]) -> str:
        return "n/a" if v is None else f"{v:.1f}x"

    def _fmt_pct_f(v: Optional[float]) -> str:
        return "n/a" if v is None else f"{v*100:+.0f}%"

    def _fmt_ratio(v: Optional[float]) -> str:
        return "n/a" if v is None else f"{v:.2f}"

    lines = [
        "## Fundamentals",
        "Ticker | MktCap  | Tr.P/E | Fwd P/E | RevGrw | Margin | D/E  | ROE",
        "-------|---------|--------|---------|--------|--------|------|----",
    ]
    for t in sorted(fundamentals.keys()):
        f = fundamentals[t]
        if not f:
            continue
        lines.append(
            f"{t:6s} | {_fmt_cap(f.get('market_cap')):7s} | "
            f"{_fmt_pe(f.get('trailing_pe')):6s} | "
            f"{_fmt_pe(f.get('forward_pe')):7s} | "
            f"{_fmt_pct_f(f.get('revenue_growth')):6s} | "
            f"{_fmt_pct_f(f.get('profit_margins')):6s} | "
            f"{_fmt_ratio(f.get('debt_to_equity')):4s} | "
            f"{_fmt_pct_f(f.get('return_on_equity'))}"
        )
    return "\n".join(lines)


def build_user_message(
    holdings: dict,
    technicals: dict[str, dict],
    market_status: dict,
    memory: dict = None,
    news: dict = None,
    earnings: dict = None,
    peer_holdings: dict = None,
    peer_agent: str = None,
    watchlist_news: dict = None,
    watchlist_earnings: dict = None,
    fundamentals: dict = None,
) -> str:
    """Assemble the full Layer 2 + Layer 3 user message."""
    memory_ctx              = build_memory_context(memory or {})
    portfolio_ctx           = build_portfolio_context(holdings, market_status)
    peer_ctx                = build_peer_context(peer_holdings, peer_agent) if peer_holdings is not None else ""
    earnings_ctx            = build_earnings_context(earnings or {})
    watchlist_earnings_ctx  = build_watchlist_earnings_context(watchlist_earnings or {})
    news_ctx                = build_news_context(news or {})
    watchlist_news_ctx      = build_watchlist_news_context(watchlist_news or {})
    benchmark_ctx           = build_benchmark_context(technicals)
    fundamentals_ctx        = build_fundamentals_context(fundamentals or {})

    # Watchlist technicals — exclude benchmark ETFs from the tradeable table
    watchlist_tech = {t: v for t, v in technicals.items()
                      if t not in ("SPY", "QQQ", "SMH", "SOXX", "XLK")}
    technicals_ctx = build_technicals_context(watchlist_tech)

    task = (
        "## Task\n"
        "Analyse the portfolio and market context above. "
        "Decide whether to make any trades. "
        "Return your decision as a single JSON object matching the schema in the system prompt. "
        "No text outside the JSON."
    )

    parts = [p for p in [
        memory_ctx, portfolio_ctx, peer_ctx,
        earnings_ctx, watchlist_earnings_ctx,
        news_ctx, watchlist_news_ctx,
        benchmark_ctx, technicals_ctx, fundamentals_ctx, task,
    ] if p]
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> dict:
    """
    Parse JSON from Claude's response.
    Handles cases where Claude wraps output in markdown code fences despite instructions.
    """
    # Strip ```json ... ``` fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)```", raw)
    candidate = fence_match.group(1).strip() if fence_match else raw.strip()

    # Try direct parse
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Try extracting the outermost {...} block
    brace_match = re.search(r"\{[\s\S]+\}", candidate)
    if brace_match:
        return json.loads(brace_match.group(0))

    raise ValueError(f"Could not extract JSON from response:\n{raw[:500]}")


def _validate_decision(decision: dict) -> None:
    """Raise ValueError if the decision is structurally invalid."""
    for key in ("reasoning", "actions", "summary"):
        if key not in decision:
            raise ValueError(f"Missing required key: '{key}'")

    if not isinstance(decision["actions"], list):
        raise ValueError("'actions' must be a list")

    valid_actions = {"BUY", "SELL", "SHORT", "COVER", "HOLD"}
    for i, action in enumerate(decision["actions"]):
        for field in ("action", "ticker", "shares"):
            if field not in action:
                raise ValueError(f"Action[{i}] missing field '{field}'")
        if action["action"] not in valid_actions:
            raise ValueError(f"Action[{i}] has invalid action type: {action['action']}")


def call_claude(
    agent_type: str,
    user_message: str,
) -> dict:
    """
    Call Claude with retry logic.
    Returns validated decision dict on success.
    Raises RuntimeError after exhausting retries.
    """
    system_prompt = SYSTEM_PROMPTS[agent_type]
    model         = SWING_MODEL if agent_type == "swing" else LONG_TERM_MODEL
    max_tokens    = MAX_TOKENS_SWING if agent_type == "swing" else MAX_TOKENS_LONG

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    current_user_message = user_message
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": current_user_message}],
            )
            raw_text = response.content[0].text
            decision = _extract_json(raw_text)
            _validate_decision(decision)
            return decision

        except anthropic.RateLimitError as e:
            last_error = e
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            print(f"[agent] Rate limit hit (attempt {attempt}/{MAX_RETRIES}). Waiting {delay}s...")
            time_module.sleep(delay)

        except anthropic.APIConnectionError as e:
            last_error = e
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            print(f"[agent] Connection error (attempt {attempt}/{MAX_RETRIES}). Waiting {delay}s...")
            time_module.sleep(delay)

        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            print(f"[agent] JSON parse/validation error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                # Append correction instruction for next attempt
                current_user_message = (
                    user_message
                    + f"\n\n[SYSTEM NOTE: Your previous response failed JSON validation: {e}. "
                    "Return ONLY a valid JSON object. No prose, no fences.]"
                )
            time_module.sleep(2)

        except anthropic.APIError as e:
            last_error = e
            print(f"[agent] API error (attempt {attempt}/{MAX_RETRIES}): {e}")
            time_module.sleep(RETRY_BASE_DELAY)

    raise RuntimeError(
        f"Claude call failed after {MAX_RETRIES} attempts. Last error: {last_error}"
    )
