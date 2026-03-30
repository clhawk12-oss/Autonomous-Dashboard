# Session Handoff — March 30, 2026

This file captures the exact state of the autonomous portfolio project after each work session so the next session can pick up immediately without re-discovery. Update this file at the end of every session.

---

## Session 4 — March 30, 2026 — Position Sizing Rationale + 7-Day Earnings Alert + Streamlit Cloud

### What Was Built / Changed This Session

**Position sizing justification in every trade action**
- Both system prompts (`_SWING_SYSTEM`, `_LONG_TERM_SYSTEM`) in `agent.py`: `rationale` field in JSON schema now explicitly requires three elements: (1) approx portfolio weight%, (2) conviction level and why that size not more/less, (3) fit with existing exposure
- Added rule 11 (swing) and rule 12 (long-term) in RULES sections reinforcing the three-element rationale requirement
- File: `agent.py`

**7-day earnings alert for held positions**
- `fetch_earnings_dates(held_tickers)` → `fetch_earnings_dates(held_tickers, days_ahead=7)` in `main.py`
- `build_earnings_context()` header updated from "next 14 days" to "next 7 days" in `agent.py`
- Watchlist earnings fetch stays at 14 days (opportunity scouting, not a risk alert)
- Log message updated to match
- Files: `agent.py`, `main.py`

**Streamlit Community Cloud deployment**
- Added `streamlit>=1.35.0` to `requirements.txt`
- Dashboard now live at a public streamlit.app URL — reads directly from the GitHub repo, auto-refreshes every 60 seconds
- No secrets needed — dashboard only reads holdings.json, equity_log.jsonl, trade_log.md

**Security fix — .env exposure**
- `.env` was committed in the initial commit and became public when repo was made public for Streamlit
- Anthropic auto-revoked the exposed key via secret scanning
- Created `.gitignore` (was missing entirely) with `.env`, `__pycache__/`, `*.pyc`
- Removed `.env` from git tracking via `git rm --cached .env`
- New API key generated, updated in local `.env` and GitHub Actions secret

---

### Bugs Fixed This Session

**Missing .gitignore** — repo had no .gitignore; `__pycache__/` files were being committed. Fixed by creating one.

**Exposed API key** — `.env` committed in initial commit. Key was auto-revoked by Anthropic. Removed from tracking, new key rotated in.

---

### Open Issues / Deferred

- **No holiday awareness** — `is_market_open()` checks weekday + hours but not US public holidays.
- **Windows Task Scheduler** — `setup_scheduler.ps1` still configured but redundant now that GitHub Actions handles cloud runs.

---

### Critical File Locations (new/changed this session)

| What | File | Key location |
|---|---|---|
| Position sizing rationale requirement | `agent.py` | `rationale` field in both schemas; rules 11/12 |
| 7-day earnings alert | `agent.py` | `build_earnings_context()` header |
| 7-day earnings fetch | `main.py` | `fetch_earnings_dates(held_tickers, days_ahead=7)` |
| Streamlit + plotly dependencies | `requirements.txt` | `streamlit>=1.35.0`, `plotly>=5.18.0` |
| Gitignore | `.gitignore` | new file — `.env`, `__pycache__/`, `*.pyc` |

---

## Session 3 — March 30, 2026 — GitHub Actions Push Fix + Full-Universe News/Earnings

### What Was Built / Changed This Session

**GitHub Actions push rejection — permanently fixed**
- Root cause: the commit step had no `set -e`, so if `git pull --rebase` failed silently, the script continued into `git push` which then failed with "fetch first"
- Secondary cause: users were re-running failed jobs instead of triggering a fresh workflow run — re-runs replay the old workflow file, not the latest
- Fix (3 iterations to get right):
  1. Added `set -e` so any failure exits loud
  2. Moved `git fetch origin main && git rebase origin/main` to run BEFORE `git add/commit` (not after) — eliminated the main race window
  3. Final fix: committed first, then wrapped push in a retry loop (`for attempt in 1 2 3`) that does `git fetch; git checkout -- .; git rebase; git push` on each attempt — `git checkout -- .` discards unstaged working-tree files (`.env`, `__pycache__`, etc.) that caused `cannot rebase: You have unstaged changes`
- Both swing and long_term commit steps updated identically
- File: `.github/workflows/run_agents.yml`

**Full-universe news and earnings feed**
- Previously: news and earnings fetched only for currently-held tickers (risk management only)
- Now: fetched for the entire 145-ticker tradeable universe, enabling opportunity discovery
- `prices.py`: `fetch_news()` gains `days: int = 7` parameter (configurable cutoff); `max_workers` bumped from 10 → 20 for both `fetch_news` and `fetch_earnings_dates`
- `agent.py`: two new context builders:
  - `build_watchlist_news_context()` — formats news for unowned tickers under "Market News — Watchlist Opportunities (last 3 days)"
  - `build_watchlist_earnings_context()` — lists upcoming earnings for unowned watchlist tickers
  - `build_news_context()` label updated to "Recent News — Held Positions (last 7 days)"
  - `build_user_message()` gains `watchlist_news` and `watchlist_earnings` params
- `main.py`:
  - Added `TRADEABLE_UNIVERSE` to config imports
  - News fetch split: held tickers get 5 headlines / 7-day window; watchlist-only tickers get 2 headlines / 3-day window
  - Earnings fetch split: held → ALERT section; watchlist-only → "Upcoming Earnings" section
  - Log line added: `Watchlist: X tickers with news, Y with upcoming earnings`
- Context order in prompt: held earnings alert → watchlist earnings → held news → watchlist news → benchmark → technicals

---

### Bugs Fixed This Session

**GitHub Actions push rejection (finally resolved)**
- Three separate errors encountered and fixed in sequence:
  1. `! [rejected] main -> main (fetch first)` — old workflow re-run; fixed by triggering fresh runs and restructuring commit step
  2. `! [rejected] main -> main (fetch first)` again — race condition between rebase and push; fixed with retry loop
  3. `cannot rebase: You have unstaged changes` — `.env` and `__pycache__` left in working tree; fixed with `git checkout -- .` before each rebase attempt

**Key lesson**: Always use "Run workflow" (fresh run) not "Re-run jobs" when testing workflow changes — re-runs replay the old cached workflow file.

---

### Open Issues / Deferred

- **Dashboard not hosted** — `dashboard.py` runs locally only. Natural next step: deploy to Streamlit Community Cloud (free), pointed at the GitHub repo, for persistent live access without `git pull`.
- **No holiday awareness** — `is_market_open()` checks weekday + hours but not US public holidays. Agents will trigger but gracefully find no data and skip.
- **Windows Task Scheduler** — `setup_scheduler.ps1` still configured but redundant now that GitHub Actions handles cloud runs.

---

### Critical File Locations (new/changed this session)

| What | File | Key location |
|---|---|---|
| GitHub Actions workflow (fixed) | `.github/workflows/run_agents.yml` | Both `Commit updated state` steps |
| Watchlist news fetch | `prices.py` | `fetch_news(days=)` param, `max_workers=20` |
| Watchlist news context builder | `agent.py` | `build_watchlist_news_context()` |
| Watchlist earnings context builder | `agent.py` | `build_watchlist_earnings_context()` |
| Updated `build_user_message` signature | `agent.py` | `watchlist_news`, `watchlist_earnings` params |
| Expanded news/earnings fetch | `main.py` | lines ~719–745, `watchlist_only` split |

---

## Session 2 — March 29, 2026 — Memory, News, Dashboard, GitHub Actions

### What Was Built / Changed This Session

**Memory across runs**
- New `memory.json` per agent (in `swing/` and `long_term/`)
- Stores: `market_notes` (rolling macro view), `watching` (catalysts to monitor), `recent_runs` (last 5 swing / 10 long-term run reasoning + actions)
- Claude's `memory_update` field in every response updates `market_notes` and `watching`
- Functions: `load_memory()`, `save_memory()`, `update_memory()` in `main.py`
- New `build_memory_context()` in `agent.py` — prepended to Layer 2 context
- Thesis excerpt increased from 80 → 400 chars (`build_portfolio_context()` in `agent.py`)

**News feed + earnings calendar**
- `prices.py`: `fetch_news(tickers, max_headlines=5)` — parallel yfinance `.news` fetch, 7-day cutoff, handles old + new news dict formats
- `prices.py`: `fetch_earnings_dates(tickers, days_ahead=14)` — parallel yfinance `.calendar` fetch, normalises dict vs DataFrame response
- `agent.py`: `build_news_context()` and `build_earnings_context()` format results for Claude
- `build_user_message()` updated to accept and include `news` and `earnings` parameters
- News/earnings only fetched for currently-held tickers (keeps prompt lean)

**Peer portfolio visibility**
- Each agent now sees the other agent's open positions (read-only) in Layer 2 context
- `agent.py`: `build_peer_context()` — formats peer holdings table
- `main.py`: loads peer holdings before each agent call, passes to `build_user_message()`
- Both system prompts updated with COORDINATION section: no conflicting directions on same ticker
- Long-term agent prompt: Rule 2 clarified — `"Only BUY, SELL, and COVER are permitted"`

**Swing agent schedule changed: 3× daily → once daily**
- `config.py`: `SWING_SCHEDULE_ET = [(15, 30)]` (was 10am, 1pm, 3:30pm)
- `setup_scheduler.ps1`: swing task now once daily at 15:30 (not every 30 minutes)

**Streamlit dashboard**
- New file: `dashboard.py`
- Shows: equity curves vs SPY/QQQ/SMH benchmarks, portfolio metrics (value, P&L, cash%, win rate), open positions table (color-coded P&L), sector exposure bar chart, trade log expandable sections
- Data sources: `holdings.json`, `equity_log.jsonl` (new), `trade_log.md`
- `st.cache_data(ttl=60)` on all loaders; benchmarks cached ttl=300
- Run: `python -m streamlit run dashboard.py`
- `equity_log.jsonl`: new append file per agent — one JSONL line per run with timestamp + portfolio value. `append_equity_log()` in `main.py`

**GitHub Actions for cloud-based daily runs**
- New file: `.github/workflows/run_agents.yml`
- Two jobs: `swing` (cron `30 20 * * 1-5` = 3:30pm ET), `long_term` (cron `15 21 * * 1-5` = 4:15pm ET)
- `long_term` has `needs: swing` — runs sequentially after swing
- `concurrency: group: portfolio-agents, cancel-in-progress: true` — prevents collisions
- `permissions: contents: write` required for push
- `workflow_dispatch` trigger for manual runs from GitHub UI
- Commit step uses manual git commands with `git pull --rebase origin main` before push — eliminates push rejection when remote has moved ahead between checkout and commit
- `ANTHROPIC_API_KEY` stored as GitHub Secret; written to `.env` via `printf` in workflow step
- pip install: `anthropic yfinance pandas numpy pytz python-dotenv`

**Prompt fixes**
- Both system prompts: removed min weight range wording, changed to "up to X% per position; no minimum"
- Swing prompt: added position sizing rationale requirement — `"why THIS size: explain conviction level, risk taken, and how it fits the portfolio"`
- Long-term Rule 2: explicit `"SHORT will be rejected"` + `"COVER is only valid to close an existing short position"`

**Holdings fix**
- `long_term/holdings.json`: legacy META short manually closed (mandate change: long-only)
- Cash adjusted for margin release, cover cost, and accrued borrow; META added to `closed_positions`

---

### Bugs Fixed This Session

**Push rejection in GitHub Actions (fixed)**
- Problem: Runner checks out at time T, agent runs ~2-3 min, any local push in that window leaves remote ahead; `git push` then fails
- Fix: commit step now runs `git pull --rebase origin main` before `git push origin main`
- Previous attempts that did NOT fix it: git pull --rebase inline, git fetch + reset --soft, stefanzweifel/git-auto-commit-action@v5, concurrency group alone

**Misspelled GitHub Secret (fixed by user)**
- User had secret named `ANTRHOPIC_API_KEY` — corrected to `ANTHROPIC_API_KEY` in GitHub Secrets UI

---

### Open Issues / Deferred

- **No holiday awareness** — `is_market_open()` checks weekday + hours but not US public holidays. GitHub Actions will still trigger on market holidays; agent will find no data and gracefully skip.
- **Windows Task Scheduler still configured** — `setup_scheduler.ps1` sets swing to once daily at 15:30. Now that GitHub Actions handles cloud runs, local scheduler may be redundant. User can leave it or disable it.
- **Dashboard not hosted** — `dashboard.py` runs locally only. For persistent access, could deploy to Streamlit Community Cloud (free) by pointing it at the GitHub repo.
- **News only for held tickers** — news and earnings are fetched for current positions only. Watchlist-wide news (for discovering new opportunities) is not yet implemented.

---

### Critical File Locations (new this session)

| What | File | Key location |
|---|---|---|
| Memory load/save/update | `main.py` | `load_memory()`, `save_memory()`, `update_memory()` |
| Memory context builder | `agent.py` | `build_memory_context()` |
| News fetch | `prices.py` | `fetch_news()`, `_fetch_ticker_news()` |
| Earnings fetch | `prices.py` | `fetch_earnings_dates()`, `_fetch_ticker_earnings()` |
| News/earnings context builders | `agent.py` | `build_news_context()`, `build_earnings_context()` |
| Peer holdings context builder | `agent.py` | `build_peer_context()` |
| Equity log append | `main.py` | `append_equity_log()` |
| GitHub Actions workflow | `.github/workflows/run_agents.yml` | Full file |
| Dashboard | `dashboard.py` | Full file |
| Swing schedule constant | `config.py` | `SWING_SCHEDULE_ET` |
| Memory run-count constants | `config.py` | `SWING_MEMORY_RUNS`, `LONG_TERM_MEMORY_RUNS` |

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
