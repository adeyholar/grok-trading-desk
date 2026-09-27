"""Options mode helpers — Mode Intraday vs Mode Week (swing).

Mode Week (default Phase 1 overlay): 2–6 week DTE swing — unchanged.
Mode Intraday: same-session worker-managed exit preference (flexible to market
fluidity). NOT a multi-day "≤5 DTE day hold". 0DTE only if Adeola unlocks later.

Adeola locks: Go / No-Go / risk-caps / overnight. Workers adapt from live data +
Living Log inside Go + discipline. No rigid time-exit automation.
"""

from __future__ import annotations

from typing import Any

MODE_WEEK = "week"
MODE_INTRADAY = "intraday"

# Mode Week defaults (existing overlay) — do not change behavior.
WEEK_DTE_MIN = 14
WEEK_DTE_MAX = 42

# Mode Intraday: same-session exit preference. DTE band stays optional/unlocked.
# 0DTE is gated — Adeola must unlock explicitly (default locked).
INTRADAY_DTE_MIN = 0
INTRADAY_DTE_MAX_UNLOCKED = 0  # 0DTE only when unlocked
INTRADAY_EXIT_NOTE = (
    "Options Mode Intraday: same-session exit by worker judgment (TP when "
    "profitable; losers on stop/invalidation); flexible to market fluidity; "
    "no rigid bell flatten; 0DTE locked unless Adeola unlocks; overnight needs "
    "Adeola OK. Evidence cites required on High/approve/ticket/exit."
)
WEEK_EXIT_NOTE = (
    "Options Mode Week: 2–6w DTE swing overlay (Phase 1 default). Separate from "
    "Mode Intraday same-session book."
)


def normalize_options_mode(mode: str | None) -> str:
    text = str(mode or MODE_WEEK).strip().lower()
    if text in {MODE_INTRADAY, "same_session", "session"}:
        return MODE_INTRADAY
    if text in {MODE_WEEK, "swing", "2-6w", "2_6w"}:
        return MODE_WEEK
    return MODE_WEEK


def options_mode_profile(
    mode: str | None = None,
    *,
    unlock_0dte: bool = False,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return mode profile for overlay / CLI. Does not change Week defaults."""
    opts = (config or {}).get("options", {}) or {}
    resolved = normalize_options_mode(mode or opts.get("mode"))
    unlocked = bool(unlock_0dte or opts.get("unlock_0dte", False))

    if resolved == MODE_INTRADAY:
        dte_min = int(opts.get("intraday_dte_min", INTRADAY_DTE_MIN))
        # Without Adeola unlock, intraday mode documents same-session exit but
        # does not open a 0DTE contract band for automated picks.
        if unlocked:
            dte_max = int(opts.get("intraday_dte_max", INTRADAY_DTE_MAX_UNLOCKED))
        else:
            dte_max = -1  # locked: no auto 0DTE band
        return {
            "mode": MODE_INTRADAY,
            "dte_min": dte_min,
            "dte_max": dte_max,
            "0dte_unlocked": unlocked,
            "same_session_exit": True,
            "force_time_exit": False,
            "flexible_to_market": True,
            "discretion_owner": "workers",
            "adeola_locks": ["go_window", "no_go", "risk_caps", "overnight_hold", "0dte_unlock"],
            "note": INTRADAY_EXIT_NOTE,
            "contract_band_active": unlocked,
        }

    return {
        "mode": MODE_WEEK,
        "dte_min": int(opts.get("dte_min", WEEK_DTE_MIN)),
        "dte_max": int(opts.get("dte_max", WEEK_DTE_MAX)),
        "0dte_unlocked": False,
        "same_session_exit": False,
        "force_time_exit": False,
        "flexible_to_market": True,
        "discretion_owner": "workers",
        "adeola_locks": ["go_window", "no_go", "risk_caps", "overnight_hold"],
        "note": WEEK_EXIT_NOTE,
        "contract_band_active": True,
    }


def is_intraday_mode(mode: str | None = None, config: dict[str, Any] | None = None) -> bool:
    return options_mode_profile(mode, config=config)["mode"] == MODE_INTRADAY
