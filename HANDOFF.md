# Session Handoff — March 29, 2026

This file captures the exact state of the autonomous portfolio project after each work session so the next session can pick up immediately without re-discovery. Update this file at the end of every session.

---

## Session 1 — March 29, 2026 — Initial Build

### What Was Built This Session

Built the entire project from scratch. All files are new.

**Files created:**
- `config.py` — all constants, single source of truth
- `prices.py` — yfinance data layer (EOD prices, technical summaries, market hours)
- `agent.py` — Anthropic API decision engine (prompts, JSON parsing, retry logic)
- `main.py` — full orchestrator (stop enforcement, trade execution, logging, scheduling)
- `setup_scheduler.ps1` — one-time Windows Task Scheduler registration script
- `requirements.txt`
- `.env` / `.env.example`
- `swing/trade_log.md`
- `long_term/trade_log.md`
- `README.md`
- `HANDOFF.md` (this file)

---

### System Verified Working

First live run completed successfully on March 29, 2026 (weekend `--force` run):

**Swing agent (Haiku)** opened 2 positions:
- LONG ARM 35sh @ $144.13, stop $135.48 (6% stop)
- SHORT META 15sh @ $525.72, stop $562.52 (7% stop)
- Cash remaining: $91,012 (84.4% of portfolio)

**Long-term agent (Sonnet)** opened 8 positions:
- LONG NVDA 60sh, MSFT 28sh, GOOGL 36sh, AMZN 50sh, AVGO 33sh, ARM 69sh, MRVL 105sh, EQIX 10sh
- SHORT META 19sh
- CRM short skipped (cash buffer breach)
- Cash remaining: $15,661 (15.7% of portfolio)

---

### Bugs Fixed This Session

**1. JSON truncation — long-term agent (fixed)**
- Problem: `MAX_TOKENS_LONG = 2500` caused Claude's JSON response to be cut off mid-output at ~char 9058
- Fix: increased to `MAX_TOKENS_LONG = 4096` in `config.py`

**2. Inaccurate PM narrative in summary.md (fixed)**
- Problem: Claude writes its `summary` field assuming all proposed actions succeed, but constraint checks can reject some afterward. Summary mentioned DLR and MRVL as positions when they were actually skipped.
- Fix: `main.py` now tracks skipped actions separately and appends a correction footnote to Claude's summary before writing to `summary.md`
- Location: `run_agent()` in `main.py` — `skipped_notes` list + summary append before `write_summary()` call

---

### Current Architecture State

Everything in `config.py` is the tuning surface. No magic numbers elsewhere.

**Prompt architecture** (3 layers per call):
1. Static system prompt: identity + mandate + constraints + JSON schema
2. Dynamic context: holdings table + benchmark returns + 32-ticker technical summary
3. Static task: "return JSON only"

**Constraint enforcement** (2 layers):
- Soft: Claude instructed in prompt (position sizing, cash buffer, stop requirements)
- Hard: Python code enforces after Claude responds (cash math, direction conflicts, weight bounds)

**Atomic writes:** `holdings.json` written via temp file + `os.replace()` — never corrupt on crash.

---

### Open Issues / Deferred

- **Task Scheduler not yet registered** — `setup_scheduler.ps1` is ready but has not been run yet. User needs to run it in PowerShell as Administrator to start autonomous operation.
- **No news/fundamentals in prompt** — Claude sees price + technicals only. Adding a news feed or fundamental data (P/E, revenue growth) would improve decision quality, especially for the long-term agent.
- **No memory across runs** — Claude only sees current holdings and last 5 closed trades. Past reasoning only survives in each position's `thesis` field.
- **No holiday awareness** — `is_market_open()` checks weekday + hours but not US public holidays. Low priority for paper trading.
- **Min position size constraint causes skips** — swing agent's 5% minimum frequently causes Claude's proposed smaller positions to be rejected. Could lower `SWING_MIN_WEIGHT` to 3% or add better position sizing guidance to the prompt.

---

### Possible Enhancements (not yet scoped)

- Add yfinance fundamental data (P/E, revenue growth, margins) to the Claude context for the long-term agent
- Add a news headline feed (e.g. via an RSS or financial news API) as an optional context layer
- Build a simple Streamlit dashboard to view both portfolios' performance visually (could reuse investment_dashboard patterns)
- Add a weekly email/SMS digest using the Twilio setup from investment_dashboard
- Track benchmark-relative performance (alpha vs. QQQ/SMH) in the performance block
- Add position-level attribution to summary.md (which positions are winning/losing and by how much)

---

### Critical File Locations

| What | File | Key location |
|------|------|-------------|
| All tunable constants | `config.py` | Full file — edit here first |
| Claude system prompts | `agent.py` | `_SWING_SYSTEM`, `_LONG_TERM_SYSTEM` (~line 48) |
| Prompt context assembly | `agent.py` | `build_portfolio_context()`, `build_technicals_context()` |
| Stop loss enforcement | `main.py` | `enforce_stops()` |
| Trade execution logic | `main.py` | `execute_action()` |
| Short selling cash math | `main.py` | `execute_action()` SHORT / COVER branches |
| Skipped action footnote fix | `main.py` | `run_agent()` — `skipped_notes` + summary append before `write_summary()` |
| Schedule guard logic | `main.py` | `_is_scheduled_now()` + `run_agent()` gates |
| Atomic write | `main.py` | `save_holdings()` |
| yfinance MultiIndex handling | `prices.py` | `_extract_series()` — mirrors `data_loader.py` lines 98-120 |

---

## How to Update This File

At the end of each session, prepend a new section above this one with:

```markdown
## Session N — [Date] — [Short description]

### What Was Built / Changed
...

### Bugs Fixed
...

### Open Issues / Deferred
...

### Critical File Locations (if new this session)
...
```

Keep prior session notes intact below — they serve as a changelog.
