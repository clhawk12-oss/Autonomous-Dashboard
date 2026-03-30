# Autonomous Portfolio Agent System

An autonomous paper trading system powered by the Anthropic Claude API. Two independent agents manage separate $100,000 paper portfolios of AI/tech equities, making equity decisions on a daily schedule without human intervention.

---

## Two Agents

| | Swing Trader | Long-Term Investor |
|---|---|---|
| **Model** | claude-haiku-4-5-20251001 (fast) | claude-sonnet-4-6 (deeper reasoning) |
| **Directory** | `swing/` | `long_term/` |
| **Runs** | 1× daily: 3:30pm ET (near close) | 1× daily: 4:15pm ET (after close) |
| **Max positions** | 20 | 30 |
| **Position size** | Up to 20% per position (no minimum) | Up to 15% per position (no minimum) |
| **Max stop loss** | 10% from entry | 20% from entry |
| **Cash floor** | 3% (hard, code-enforced) | 3% (hard, code-enforced) |
| **Holding period** | Days to ~2 months | Months to years |
| **Direction** | Long + Short | Long only |
| **Style** | Tactical, momentum + technical | Thesis-driven, moat + valuation |

Both agents start with $100,000 cash. Cash is treated as a strategic position — agents can hold anywhere from 3% to 90%+ depending on conviction.

---

## Universe — 145 Tradeable Tickers

Loaded dynamically from `watchlists.json` — edit that file to add or remove tickers. Sources:

- AI infrastructure watchlist
- Quality / sector watchlist
- Robinhood holdings
- Vanguard Roth IRA holdings

**Benchmarks** (context only, not traded): SPY, QQQ, SMH, XLK, SOXX

To change the tradeable universe, edit `watchlists.json`. No code changes needed.

---

## File Structure

```
autonomous portfolio/
├── config.py              ← All constants — single source of truth
├── agent.py               ← Prompt architecture + Claude API calls
├── main.py                ← Orchestrator (stops, execution, memory, logging)
├── prices.py              ← yfinance data layer (prices, technicals, news, earnings)
├── dashboard.py           ← Streamlit dashboard (run: python -m streamlit run dashboard.py)
├── watchlists.json        ← Tradeable universe (edit to add/remove tickers)
├── setup_scheduler.ps1    ← One-time Windows Task Scheduler registration (optional if using Actions)
├── requirements.txt
├── .env                   ← ANTHROPIC_API_KEY (never commit)
├── .env.example
│
├── .github/
│   └── workflows/
│       └── run_agents.yml ← GitHub Actions: runs both agents daily in the cloud
│
├── swing/
│   ├── holdings.json      ← Live portfolio state
│   ├── memory.json        ← Rolling run history (last 5 runs)
│   ├── equity_log.jsonl   ← Per-run portfolio value history (for equity curve)
│   ├── trade_log.md       ← Append-only trade journal
│   └── summary.md         ← Current snapshot (overwritten each run)
│
└── long_term/
    ├── holdings.json
    ├── memory.json        ← Rolling run history (last 10 runs)
    ├── equity_log.jsonl
    ├── trade_log.md
    └── summary.md
```

---

## How It Works — The Loop

Every scheduled run follows this exact sequence:

```
1.  Load holdings.json
2.  Load memory.json (prior run reasoning)
3.  Fetch prices + technicals for all 145 tickers via yfinance
4.  Fetch recent news headlines + upcoming earnings for held tickers only
5.  Load peer agent's holdings.json (for coordination context)
6.  Enforce mechanical stop-losses (before Claude is called)
7.  Accrue daily borrow cost on short positions (swing only)
8.  Call Claude API with: memory + portfolio state + technicals + news + earnings + peer holdings
9.  Execute Claude's trade decisions (with hard constraint checks)
10. Recompute performance metrics
11. Update memory.json with this run's reasoning + memory_update
12. Save holdings.json (atomic write)
13. Append to equity_log.jsonl (one line: timestamp + portfolio value)
14. Append to trade_log.md
15. Overwrite summary.md
```

Steps 4 and 7 are enforced in Python — Claude cannot override hard constraints.

---

## Memory Across Runs

Each agent has a `memory.json` that persists Claude's own reasoning between runs:

```json
{
  "market_notes": "Claude's current macro/regime view — updated every run",
  "watching": ["NVDA earnings April 23", "Fed meeting May 7 — rate path key"],
  "recent_runs": [
    {
      "run_id": "swing-20260329-1530",
      "timestamp": "2026-03-29T15:30:00-04:00",
      "reasoning": "full reasoning from that run",
      "actions_taken": ["BUY ARM 35sh @ $144.13"]
    }
  ]
}
```

- `market_notes` — rolling macro view, overwritten each run
- `watching` — catalysts and levels Claude flagged to monitor, updated each run
- `recent_runs` — last 5 runs (swing) or 10 runs (long-term) of reasoning + actions

This memory is prepended to Claude's context so it can build conviction over time and stay consistent with multi-day views.

---

## What Claude Sees (the prompt)

Each Claude call receives three layers:

**Layer 1 — System (static):** Claude's identity, mandate, all rules, and the exact JSON schema it must return.

**Layer 2 — Context (dynamic, built fresh each run):**
- Memory context from prior runs (market notes, watching list, recent reasoning)
- Current holdings: cash, every open position with thesis (up to 400 chars) / stop / P&L
- Last 5 closed positions with realized P&L and exit reason
- Benchmark returns: SPY, QQQ, SMH (1W and 1M)
- Watchlist technicals for all 145 tradeable names: price, 1W/1M return, % from 52W high, volume ratio, ATR-14
- Recent news headlines for held tickers (last 7 days, up to 5 per ticker)
- Upcoming earnings dates for held tickers (next 14 days)
- Peer agent's open positions (read-only, for coordination — no conflicting directions)

**Layer 3 — Task (static):** "Analyse the above and return a single JSON object."

---

## What Claude Returns

```json
{
  "reasoning": "2-3 sentence market view and portfolio stance",
  "actions": [
    {
      "action": "BUY | SELL | SHORT | COVER",
      "ticker": "NVDA",
      "shares": 30,
      "rationale": "why THIS size: conviction level, risk taken, portfolio fit",
      "thesis": "full investment thesis (required for BUY and SHORT)",
      "stop_loss_pct": 0.06,
      "take_profit_pct": 0.18
    }
  ],
  "summary": "PM narrative paragraph written to summary.md",
  "memory_update": {
    "market_notes": "updated macro/regime view",
    "watching": ["catalyst or level to monitor next run"]
  }
}
```

Empty `actions` array is valid — agents hold when no compelling setup exists.

---

## Position Sizing

No minimum position size. Agents size freely from 1 share up to the maximum weight cap. The `rationale` field on every action must explain the sizing logic (why this specific number of shares, what conviction level it reflects, how it fits the portfolio) — not just repeat the thesis.

---

## Constraints — Two Layers

### Soft (Claude is instructed to respect these)
- Stay within the approved 145-ticker universe
- Size positions within allowed weight cap
- Keep ≥3% cash buffer (treat as strategic minimum, not default)
- Set stop losses on every new position

### Hard (enforced in Python — Claude cannot override)
- Stop losses triggered mechanically before Claude runs
- BUY/SHORT rejected if it would breach 3% cash floor
- BUY rejected if already short the same ticker (must COVER first, and vice versa)
- Position rejected if it exceeds max weight cap
- Position rejected if max position count already reached
- SHORT rejected for long_term agent (long-only mandate)
- All trade cash math verified before any state mutation

---

## Short Selling Mechanics (Swing agent only)

Simulates Reg T margin:
- **To SHORT:** receive proceeds, post 150% of position value as margin. Net cash impact = −0.5 × (shares × price)
- **To COVER:** margin released, pay current market price to close
- **Borrow cost:** 2% annually, accrued daily at EOD on each short position

Long-term agent is strictly long-only. SHORT is rejected at the code level.

---

## State Files

### `holdings.json`
Live source of truth. Contains:
- `cash` — available cash
- `margin_reserved` — held for short positions
- `positions` — every open position with avg cost, current price, P&L, thesis, stop/target
- `closed_positions` — recent closed trades (full history in trade_log.md)
- `performance` — computed metrics: total P&L, exposures, win rate

Written atomically (temp file + rename) — never left in a corrupt partial state.

### `memory.json`
Persisted agent memory. Rolling window of prior run reasoning. Written atomically.

### `trade_log.md`
Append-only journal. Each run appends timestamp, run ID, Claude's reasoning, benchmark returns, executed/skipped trades table, and portfolio state.

### `summary.md`
Overwritten every run. Clean current-state snapshot with performance table, open positions, and Claude's PM narrative.

---

## Scheduling

### GitHub Actions (primary — runs in the cloud, no local machine needed)

`.github/workflows/run_agents.yml` triggers both agents daily:

- **Swing job:** 3:30pm ET Mon–Fri (`30 20 * * 1-5` UTC)
- **Long-term job:** 4:15pm ET Mon–Fri (`15 21 * * 1-5` UTC), runs after swing completes

Both jobs run sequentially (not in parallel) to avoid git push conflicts. The workflow commits agent output files back to the repo automatically.

To trigger a manual run: go to the GitHub repo → Actions → Run Portfolio Agents → Run workflow.

**Required GitHub setup:**
1. Add `ANTHROPIC_API_KEY` as a repository secret (Settings → Secrets → Actions)
2. Ensure the repo has write permissions for Actions (Settings → Actions → General → Workflow permissions → Read and write)

### Windows Task Scheduler (optional — local fallback)

`setup_scheduler.ps1` registers local tasks if you want agents to run on your machine:
- **SwingAgent:** once daily at 3:30pm ET, Mon–Fri
- **LongTermAgent:** once daily at 4:15pm ET, Mon–Fri

---

## Running Manually

```bash
# Test run (bypasses market hours and schedule guard)
python main.py --agent both --force

# Individual agents
python main.py --agent swing --force
python main.py --agent long_term --force

# Normal scheduled run (respects market hours + schedule windows)
python main.py --agent both
```

---

## Setup (first time)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Add API key
cp .env.example .env
# edit .env and fill in ANTHROPIC_API_KEY

# 3. Test run
python main.py --agent both --force

# 4. Register Task Scheduler (PowerShell as Admin)
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
.\setup_scheduler.ps1
```

---

## Tuning — All in `config.py`

| Setting | Current Value | Effect |
|---|---|---|
| `SWING_MAX_POSITIONS` | 20 | Max concurrent swing trades |
| `SWING_MAX_WEIGHT` | 20% | Max position size (no minimum) |
| `SWING_MAX_STOP_LOSS_PCT` | 10% | Hard cap on stop distance |
| `LONG_TERM_MAX_POSITIONS` | 30 | Max long-term holdings |
| `LONG_TERM_MAX_WEIGHT` | 15% | Max long-term position size (no minimum) |
| `LONG_TERM_MAX_STOP_LOSS_PCT` | 20% | Hard cap on stop distance |
| `MIN_CASH_BUFFER` | 3% | Hard floor — code-enforced |
| `SWING_MEMORY_RUNS` | 5 | Prior runs kept in swing/memory.json |
| `LONG_TERM_MEMORY_RUNS` | 10 | Prior runs kept in long_term/memory.json |
| `SHORT_BORROW_RATE_ANNUAL` | 2% | Annual borrow cost on shorts |
| `SWING_MODEL` | claude-haiku-4-5-20251001 | Claude model for swing decisions |
| `LONG_TERM_MODEL` | claude-sonnet-4-6 | Claude model for long-term decisions |

---

## Dashboard

```bash
python -m streamlit run dashboard.py
```

Shows:
- **Equity curves** — both agents vs SPY/QQQ/SMH, indexed to 100 at start
- **Portfolio metrics** — value, total P&L, cash%, open positions, win rate
- **Positions table** — color-coded by P&L, with sector, stop distance, current price
- **Sector exposure** — bar chart per agent (shorts shown as negative)
- **Trade log** — expandable run-by-run entries, newest first

Data refreshes every 60 seconds automatically. No database required — reads directly from `holdings.json`, `equity_log.jsonl`, and `trade_log.md`.

---

## Cost Estimate

~$0.01–0.02/day at current Anthropic pricing:
- Haiku: swing agent, 1 call/day × 145-ticker context (+ news/earnings)
- Sonnet: long-term agent, 1 call/day × 145-ticker context (+ news/earnings)
