"""Options Mode Intraday vs Mode Week — does not break 2–6w overlay."""

from __future__ import annotations

from datetime import date, timedelta

from src.models import Stock
from src.options.modes import (
    MODE_INTRADAY,
    MODE_WEEK,
    INTRADAY_EXIT_NOTE,
    options_mode_profile,
)
from src.options.overlay import pick_liquid_candidates


def _contract(underlying, otype, strike, dte, *, oi=500, premium=2.5, as_of=None):
    today = as_of or date(2026, 9, 27)
    exp = today + timedelta(days=dte)
    letter = "C" if otype == "call" else "P"
    return {
        "symbol": f"{underlying}{exp.strftime('%y%m%d')}{letter}{int(strike * 1000):08d}",
        "underlying_symbol": underlying,
        "type": otype,
        "strike_price": str(strike),
        "expiration_date": exp.isoformat(),
        "open_interest": str(oi),
        "close_price": str(premium),
        "status": "active",
        "tradable": True,
    }


def test_mode_week_profile_defaults():
    p = options_mode_profile(MODE_WEEK)
    assert p["mode"] == MODE_WEEK
    assert p["dte_min"] == 14
    assert p["dte_max"] == 42
    assert p["contract_band_active"] is True
    assert p["force_time_exit"] is False


def test_mode_intraday_0dte_locked_by_default():
    p = options_mode_profile(MODE_INTRADAY)
    assert p["mode"] == MODE_INTRADAY
    assert p["same_session_exit"] is True
    assert p["0dte_unlocked"] is False
    assert p["contract_band_active"] is False
    assert p["force_time_exit"] is False
    assert "same-session" in INTRADAY_EXIT_NOTE.lower() or "same session" in INTRADAY_EXIT_NOTE.lower()


def test_mode_intraday_unlock_0dte():
    p = options_mode_profile(MODE_INTRADAY, unlock_0dte=True)
    assert p["0dte_unlocked"] is True
    assert p["contract_band_active"] is True
    assert p["dte_max"] == 0


def test_week_overlay_still_2_to_6w():
    as_of = date(2026, 9, 27)
    picks = pick_liquid_candidates(
        [Stock(symbol="AAPL", price=200.0)],
        [
            _contract("AAPL", "call", 205, 7, as_of=as_of),
            _contract("AAPL", "call", 204, 28, as_of=as_of, oi=2000),
            _contract("AAPL", "put", 196, 28, as_of=as_of, oi=1500),
            _contract("AAPL", "call", 210, 90, as_of=as_of),
        ],
        dte_min=14,
        dte_max=42,
        as_of=as_of,
    )
    assert len(picks) == 2
    assert all(14 <= p.dte <= 42 for p in picks)
