"""
config.py
---------
Single source of truth for all constants.  Every other module imports from here.
Tradeable universe is loaded from watchlists.json — edit that file to add/remove tickers.
"""

import json
import os
from pathlib import Path

# ── Directories ────────────────────────────────────────────────────────────────
ROOT_DIR      = Path(__file__).parent
SWING_DIR     = ROOT_DIR / "swing"
LONG_TERM_DIR = ROOT_DIR / "long_term"

# ── Load universe from watchlists.json ────────────────────────────────────────
_watchlists_path = ROOT_DIR / "watchlists.json"
with open(_watchlists_path, "r") as _f:
    _watchlists = json.load(_f)

TRADEABLE_UNIVERSE: list[str] = _watchlists["tradeable"]
BENCHMARK_TICKERS:  list[str] = _watchlists["benchmarks"]
AI_INFRA_UNIVERSE:  list[str] = TRADEABLE_UNIVERSE + BENCHMARK_TICKERS  # full fetch list

# ── Anthropic API ──────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
SWING_MODEL        = "claude-haiku-4-5-20251001"   # fast + cheap for 3x/day
LONG_TERM_MODEL    = "claude-sonnet-4-6"           # stronger reasoning for thesis depth
MAX_TOKENS_SWING   = 3000   # increased from 1500 — structured reasoning + thesis bullets need room
MAX_TOKENS_LONG    = 4096

# ── Market hours (US/Eastern) ──────────────────────────────────────────────────
MARKET_OPEN_ET  = (9, 30)
MARKET_CLOSE_ET = (16, 0)

# ── Portfolio constraints ──────────────────────────────────────────────────────
STARTING_CASH = 100_000.0

SWING_MAX_POSITIONS = 20
SWING_MIN_WEIGHT    = 0.0    # no minimum — Claude sizes positions freely
SWING_MAX_WEIGHT    = 0.20   # 20% maximum per position

LONG_TERM_MAX_POSITIONS = 30
LONG_TERM_MIN_WEIGHT    = 0.0    # no minimum — Claude sizes positions freely
LONG_TERM_MAX_WEIGHT    = 0.15

# Hard cash floor — absolute minimum, code-enforced.
# Claude is instructed to treat cash as a strategic position (can hold 0-90%+)
# but this floor prevents margin calls / going truly negative on a bad day.
MIN_CASH_BUFFER = 0.03   # 3%

# ── Memory window (how many past runs to keep in memory.json) ─────────────────
SWING_MEMORY_RUNS     = 5    # ~1.7 days of history at 3 runs/day
LONG_TERM_MEMORY_RUNS = 10   # ~2 weeks of history at 1 run/day

# ── Short selling simulation (swing only — long_term is long-only) ─────────────
# Reg T margin: must hold 150% of short value (100% proceeds + 50% additional)
SHORT_MARGIN_REQUIREMENT = 1.50
# Annual borrow cost accrued daily at EOD (realistic for liquid large-caps)
SHORT_BORROW_RATE_ANNUAL = 0.02

# ── Scheduling (hours, minutes) in US/Eastern ─────────────────────────────────
# Swing: one run near close — EOD signal quality beats intraday noise
SWING_SCHEDULE_ET    = [(15, 30)]
# Long-term: one run after market close (EOD prices available)
LONG_TERM_SCHEDULE_ET = [(16, 15)]
# How many minutes either side of a scheduled time to allow Task Scheduler jitter
SCHEDULE_TOLERANCE_MINUTES = 15
