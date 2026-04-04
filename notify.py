"""
notify.py
---------
Sends a daily portfolio digest email via Gmail SMTP.
Reads swing/summary.md and long_term/summary.md and emails them
to NOTIFY_TO_EMAIL using GMAIL_ADDRESS + GMAIL_APP_PASSWORD.

Usage (called from GitHub Actions after both agents complete):
    python notify.py
"""

import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytz

ROOT       = Path(__file__).parent
ET_TZ      = pytz.timezone("America/New_York")
NOW_ET     = datetime.now(ET_TZ)
DATE_STR   = NOW_ET.strftime("%A, %B %-d")   # e.g. "Friday, April 4"

GMAIL_ADDRESS    = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
NOTIFY_TO_EMAIL  = os.environ["NOTIFY_TO_EMAIL"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_summary(agent_dir: str) -> str:
    path = ROOT / agent_dir / "summary.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return "_No summary available._"


def read_equity_log(agent_dir: str) -> dict | None:
    path = ROOT / agent_dir / "equity_log.jsonl"
    if not path.exists():
        return None
    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def fmt_pct(v: float) -> str:
    return f"{v:+.2f}%"


def build_agent_block(label: str, icon: str, agent_dir: str) -> str:
    """Build the plain-text block for one agent."""
    eq = read_equity_log(agent_dir)
    summary = read_summary(agent_dir)

    header = f"{icon}  {label}"
    if eq:
        pv  = eq.get("portfolio_value", 0)
        ret = eq.get("total_return_pct", 0) * 100
        header += f"   |   ${pv:,.0f}  ({fmt_pct(ret)} total)"

    divider = "─" * 60
    return f"{header}\n{divider}\n{summary}"


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
