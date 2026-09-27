"""Same-session guidance for Stock Intraday (worker discretion + discipline).

DISCRETIONARY = Scanner/Context/Checker/Trade Desk judgment — not Adeola
micromanaging every hold/exit. Adeola locks: Go window / No-Go / risk-caps /
overnight hold OK only.

Workers (inside active Go + discipline): take-profit when profitable same
session by judgment; cut losers on stop/invalidation by judgment; no forced
bell flatten. Ask Adeola only for overnight hold or risk-cap changes.

15:55 ET is an optional soft reminder — never an automatic close or entry block.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SESSION_REMINDER_ET = time(15, 55)
SESSION_CHECKPOINT_ET = SESSION_REMINDER_ET
FLATTEN_BY_ET = SESSION_REMINDER_ET  # legacy alias; not a force-exit

EXIT_POLICY_NOTE = (
    "Stock Intraday: worker discretion (TP when judgment says take it; losers on "
    "stop/invalidation by judgment); no forced bell flatten; Adeola locks "
    "Go/No-Go/risk-caps/overnight only."
)
FLATTEN_BY_NOTE = EXIT_POLICY_NOTE


def now_et(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(tz=ET)
    if now.tzinfo is None:
        return now.replace(tzinfo=ET)
    return now.astimezone(ET)


def session_reminder_today(now: datetime | None = None) -> datetime:
    current = now_et(now)
    return datetime.combine(current.date(), SESSION_REMINDER_ET, tzinfo=ET)


def flatten_deadline_today(now: datetime | None = None) -> datetime:
    return session_reminder_today(now)


def at_session_reminder(now: datetime | None = None) -> bool:
    current = now_et(now)
    return current.timetz().replace(tzinfo=None) >= SESSION_REMINDER_ET


def should_flatten_by(now: datetime | None = None) -> bool:
    """Legacy name: soft reminder only. Does NOT authorize force-exit."""
    return at_session_reminder(now)


def past_flatten_deadline(now: datetime | None = None) -> bool:
    return at_session_reminder(now)


def residual_risk_prompt(*, has_open_losers: bool = False) -> str:
    base = (
        "Residual risk ask: overnight carry needs Adeola OK (risk-cap / hold lock). "
        "Workers manage TP/stops by judgment inside the Go window — no clock flatten."
    )
    if has_open_losers:
        return base + " Open losers: stop/invalidation by worker judgment."
    return base


def session_status(
    now: datetime | None = None, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    hours = (config or {}).get("market_hours") or {}
    close_text = str(hours.get("close") or "15:55")
    current = now_et(now)
    reminder = at_session_reminder(current)
    return {
        "timezone": "America/New_York",
        "now_et": current.isoformat(),
        "session_reminder_et": SESSION_REMINDER_ET.strftime("%H:%M"),
        "session_checkpoint_et": SESSION_REMINDER_ET.strftime("%H:%M"),
        "flatten_by_et": SESSION_REMINDER_ET.strftime("%H:%M"),
        "config_close": close_text,
        "at_session_reminder": reminder,
        "at_session_checkpoint": reminder,
        "should_flatten": False,
        "force_time_exit": False,
        "force_loss_flatten": False,
        "discretionary": True,
        "discretion_owner": "workers",
        "adeola_locks": ["go_window", "no_go", "risk_caps", "overnight_hold"],
        "mode": "intraday",
        "exit_policy": EXIT_POLICY_NOTE,
        "note": EXIT_POLICY_NOTE,
        "residual_risk_prompt": residual_risk_prompt(),
    }
