"""
notify.py
---------
Sends a daily portfolio digest email via Gmail SMTP.

Email structure per agent:
  Performance
  Actions Taken (today's trades — between performance and positions)
  Current Positions
  PM Narrative

Usage (called from GitHub Actions after both agents complete):
    python notify.py
"""

import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytz

ROOT     = Path(__file__).parent
ET_TZ    = pytz.timezone("America/New_York")
NOW_ET   = datetime.now(ET_TZ)
DATE_STR = NOW_ET.strftime("%A, %B ") + str(NOW_ET.day)   # e.g. "Friday, May 8"

GMAIL_ADDRESS      = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
NOTIFY_TO_EMAIL    = os.environ["NOTIFY_TO_EMAIL"]


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def read_equity_log(agent_dir: str) -> dict | None:
    path = ROOT / agent_dir / "equity_log.jsonl"
    if not path.exists():
        return None
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def read_summary_sections(agent_dir: str) -> dict[str, str]:
    """
    Parse summary.md into named sections.
    Returns a dict with keys: 'performance', 'positions', 'narrative'.
    """
    path = ROOT / agent_dir / "summary.md"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()

    sections: dict[str, str] = {}
    # Split on ## headings — keep heading in section text
    raw = re.split(r"\n(?=## )", text)
    for chunk in raw:
        chunk = chunk.strip()
        if chunk.startswith("## Performance"):
            sections["performance"] = chunk
        elif chunk.startswith("## Current Positions"):
            sections["positions"] = chunk
        elif chunk.startswith("## PM Narrative"):
            sections["narrative"] = chunk
    return sections


def read_last_run_actions(agent_dir: str) -> list[dict]:
    """
    Extract the most recent run's executed trades from trade_log.md.
    Returns a list of dicts: {action, ticker, shares, price}.
    Excludes SKIPPED entries.
    """
    path = ROOT / agent_dir / "trade_log.md"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    blocks = [b.strip() for b in re.split(r"\n---\n", text) if b.strip()]
    if not blocks:
        return []
    last_block = blocks[-1]
    trades = []
    for m in re.finditer(
        r"^\| (BUY|SELL|SHORT|COVER) \| (\S+) \| (\d+) \| \$([\d,]+\.?\d*) \|",
        last_block,
        re.MULTILINE,
    ):
        trades.append({
            "action": m.group(1),
            "ticker": m.group(2),
            "shares": m.group(3),
            "price":  m.group(4),
        })
    return trades


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def fmt_pct(v: float) -> str:
    return f"{v:+.2f}%"


def fmt_actions_section(trades: list[dict]) -> str:
    """Format today's trades as a plain-text section."""
    lines = ["## Actions Taken"]
    real = [t for t in trades if t["action"] not in ("SKIPPED",)]
    if not real:
        lines.append("No trades — held all positions.")
        return "\n".join(lines)
    for t in real:
        lines.append(
            f"  {t['action']:<6}  {t['ticker']:<6}  "
            f"{t['shares']} shares @ ${t['price']}"
        )
    return "\n".join(lines)


def build_agent_block(label: str, icon: str, agent_dir: str) -> str:
    """Build the plain-text block for one agent with actions injected."""
    eq      = read_equity_log(agent_dir)
    secs    = read_summary_sections(agent_dir)
    trades  = read_last_run_actions(agent_dir)

    # Header line with total value and return
    header = f"{icon}  {label}"
    if eq:
        pv  = eq.get("portfolio_value", 0)
        ret = eq.get("total_return_pct", 0) * 100
        header += f"   |   ${pv:,.0f}  ({fmt_pct(ret)} total)"

    divider = "─" * 60

    # Assemble: performance → actions → positions → narrative
    body_parts = []
    if "performance" in secs:
        body_parts.append(secs["performance"])
    body_parts.append(fmt_actions_section(trades))
    if "positions" in secs:
        body_parts.append(secs["positions"])
    if "narrative" in secs:
        body_parts.append(secs["narrative"])

    body = "\n\n".join(body_parts) if body_parts else "_No data available._"
    return f"{header}\n{divider}\n{body}"


# ---------------------------------------------------------------------------
# Build + send
# ---------------------------------------------------------------------------

def build_email_body() -> str:
    swing_block = build_agent_block("Swing Trader",       "⚡", "swing")
    lt_block    = build_agent_block("Long-Term Investor", "🌱", "long_term")

    return (
        f"Portfolio Digest — {DATE_STR}\n"
        f"{'═' * 60}\n\n"
        f"{swing_block}\n\n\n"
        f"{'═' * 60}\n\n"
        f"{lt_block}\n"
    )


def send_email(subject: str, body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = NOTIFY_TO_EMAIL
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, NOTIFY_TO_EMAIL, msg.as_string())


if __name__ == "__main__":
    body    = build_email_body()
    subject = f"Portfolio Digest — {DATE_STR}"
    send_email(subject, body)
    print(f"Email sent to {NOTIFY_TO_EMAIL}")
