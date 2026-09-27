"""Defined-risk tickets for Crypto Intraday (Alpaca paper BTC/USD ETH/USD)."""

from __future__ import annotations

from typing import Any

from ..shared.discipline import require_defined_risk, require_evidence_cites
from .symbols import is_allowed_crypto_symbol, normalize_crypto_symbol

DEFAULT_STOP_PCT = 0.015
DEFAULT_TARGET_PCT = 0.03
DEFAULT_LIMIT_SLIPPAGE = 0.001


def marketable_limit_price(side: str, reference_price: float, *, slippage: float = DEFAULT_LIMIT_SLIPPAGE) -> float:
    if reference_price <= 0:
        raise ValueError(f"invalid reference_price {reference_price}")
    slip = max(0.0, float(slippage))
    if side == "buy":
        # Crypto often needs more precision than 2 dp
        return round(reference_price * (1.0 + slip), 6)
    raise ValueError(f"side not allowed: {side}")


def build_crypto_intraday_ticket(
    *,
    symbol: str,
    reference_price: float,
    notional_usd: float | None = None,
    qty: float | None = None,
    max_risk_usd: float = 50.0,
    stop_pct: float = DEFAULT_STOP_PCT,
    target_pct: float = DEFAULT_TARGET_PCT,
    limit_slippage: float = DEFAULT_LIMIT_SLIPPAGE,
    evidence_cites: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canon = normalize_crypto_symbol(symbol)
    if not canon or not is_allowed_crypto_symbol(canon):
        raise ValueError(f"crypto symbol not allowed: {symbol!r}")
    ref = float(reference_price)
    if ref <= 0:
        raise ValueError(f"reference_price must be > 0, got {reference_price}")
    stop_pct_f = float(stop_pct)
    target_pct_f = float(target_pct)
    limit = marketable_limit_price("buy", ref, slippage=limit_slippage)
    stop_price = round(ref * (1.0 - stop_pct_f), 6)
    target_price = round(ref * (1.0 + target_pct_f), 6)
    risk_per_unit = max(ref - stop_price, ref * 1e-6)
    if qty is None:
        budget = float(notional_usd) if notional_usd else float(max_risk_usd) / max(stop_pct_f, 1e-6)
        qty = max(budget / ref, 0.0)
    qty_f = float(qty)
    if qty_f <= 0:
        raise ValueError("qty must be > 0")
    estimated_risk = round(qty_f * risk_per_unit, 4)
    ticket = {
        "symbol": canon,
        "side": "buy",
        "qty": qty_f,
        "reference_price": ref,
        "limit_price": limit,
        "stop_price": stop_price,
        "stop_pct": stop_pct_f,
        "target_price": target_price,
        "target_pct": target_pct_f,
        "max_risk_usd": float(max_risk_usd),
        "estimated_risk_usd": estimated_risk,
        "time_in_force": "gtc",
        "order_type": "limit",
        "mode": "intraday",
        "discretionary": True,
        "discretion_owner": "workers",
        "book": "crypto_intraday",
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
    require_evidence_cites(ticket, min_cites=1, context="crypto_intraday_ticket")
    return ticket
