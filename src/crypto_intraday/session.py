"""Crypto Intraday session guidance — worker discretion + Adeola locks.

Same policy as stock intraday: workers manage TP/stops by judgment inside Go;
Adeola locks Go/No-Go/risk-caps/overnight; no forced bell flatten.
Crypto trades ~24/7 on Alpaca paper — "same session" means the operator session
window, not equity RTH close.
"""

from __future__ import annotations

from typing import Any

EXIT_POLICY_NOTE = (
    "Crypto Intraday: worker discretion (TP when judgment says take it; losers on "
    "stop/invalidation by judgment); no forced time flatten; Adeola locks "
    "Go/No-Go/risk-caps/overnight only. Alpaca paper BTC/USD ETH/USD only."
)


def residual_risk_prompt() -> str:
    return (
        "Residual risk ask: crypto overnight/carry needs Adeola OK "
        "(risk-cap / hold lock). Workers manage TP/stops by judgment inside Go."
    )


def session_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "mode": "intraday",
        "book": "crypto_intraday",
        "discretionary": True,
        "discretion_owner": "workers",
        "adeola_locks": ["go_window", "no_go", "risk_caps", "overnight_hold"],
        "force_time_exit": False,
        "exit_policy": EXIT_POLICY_NOTE,
        "note": EXIT_POLICY_NOTE,
        "residual_risk_prompt": residual_risk_prompt(),
        "allowed_symbols": ["BTC/USD", "ETH/USD"],
    }
