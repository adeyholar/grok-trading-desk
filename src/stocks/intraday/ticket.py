"""Defined-risk tickets for Stock Intraday (worker discretion + discipline).

Every ticket carries max_risk_usd, stop, optional target. Workers decide timing /
TP / stop cuts inside Adeola Go + discipline. Overnight hold or risk-cap change
asks Adeola. No invented fills. No forced bell flatten.
"""

from __future__ import annotations

from typing import Any

from ...shared.discipline import require_defined_risk, require_evidence_cites
from .executor import marketable_limit_price
from .universe import normalize_symbol

DEFAULT_STOP_PCT = 0.01
DEFAULT_TARGET_PCT = 0.02


def build_stock_intraday_ticket(
    *,
    symbol: str,
    reference_price: float,
    qty: int | None = None,
    max_risk_usd: float = 100.0,
    max_notional_usd: float = 500.0,
    stop_pct: float = DEFAULT_STOP_PCT,
    target_pct: float = DEFAULT_TARGET_PCT,
    limit_slippage: float = 0.001,
    evidence_cites: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a long-only defined-risk ticket (dry-run ready)."""
    sym = normalize_symbol(symbol)
    ref = float(reference_price)
    if ref <= 0:
        raise ValueError(f"reference_price must be > 0, got {reference_price}")
    stop_pct_f = float(stop_pct)
    target_pct_f = float(target_pct)
    limit = marketable_limit_price("buy", ref, slippage=limit_slippage)
    stop_price = round(ref * (1.0 - stop_pct_f), 2)
    target_price = round(ref * (1.0 + target_pct_f), 2)
    risk_per_share = max(ref - stop_price, 0.01)
    if qty is None:
        by_risk = max(1, int(float(max_risk_usd) // risk_per_share))
        # Cap on reference (limit is slightly above); floor at 1 only if 1 share fits.
        by_notional = int(float(max_notional_usd) // ref)
        if by_notional < 1:
            raise ValueError(
                f"max_notional_usd {max_notional_usd} too small for 1 share at {ref}"
            )
        qty = min(by_risk, by_notional)
    qty_i = int(qty)
    estimated_risk = round(qty_i * risk_per_share, 2)
    ticket = {
        "symbol": sym,
        "side": "buy",
        "qty": qty_i,
        "reference_price": ref,
        "limit_price": limit,
        "stop_price": stop_price,
        "stop_pct": stop_pct_f,
        "target_price": target_price,
        "target_pct": target_pct_f,
        "max_risk_usd": float(max_risk_usd),
        "max_notional_usd": float(max_notional_usd),
        "estimated_risk_usd": estimated_risk,
        "time_in_force": "day",
        "order_type": "limit",
        "mode": "intraday",
        "discretionary": True,
        "discretion_owner": "workers",
        "book": "stock_intraday",
        "gates": {"high": None, "checker": None, "go": None},
        "exit_preference": "worker_tp_when_profitable_same_session_by_judgment",
        "loser_exit": "worker_stop_or_invalidation_by_judgment",
        "overnight": "adeola_ok_required",
        "adeola_locks": ["go_window", "no_go", "risk_caps", "overnight_hold"],
        "evidence_cites": list(evidence_cites or []),
        "informed_discretion": True,
    }
    if extra:
        ticket.update(extra)
        if extra.get("evidence_cites") is not None:
            ticket["evidence_cites"] = list(extra["evidence_cites"])
    require_defined_risk(ticket)
    require_evidence_cites(ticket, min_cites=1, context="stock_intraday_ticket")
    return ticket
