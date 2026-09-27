"""Pick liquid Level-2 long call/put candidates from screened underlyings.

Prefers 2–6 week DTE (configurable via options.dte_min / dte_max). Pure scoring
helpers are network-free so unit tests stay offline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from ..models import Stock
from .contracts import OptionsContractsClient

log = logging.getLogger(__name__)


@dataclass
class OptionCandidate:
    """A single-leg long option idea ready for the executor."""

    underlying: str
    symbol: str
    option_type: str  # "call" | "put"
    side: str = "buy"
    strike: float = 0.0
    expiration: str = ""
    dte: int = 0
    premium: float = 0.0
    open_interest: float = 0.0
    underlying_price: float = 0.0
    score: float = 0.0
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def intent(self) -> str:
        return f"{self.side} {self.option_type}"

    def to_order(
        self,
        *,
        qty: int = 1,
        max_risk_usd: float | None = None,
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        """Build a Level-2 single-leg buy order dict for OptionsExecutor."""
        premium = limit_price if limit_price is not None else self.premium
        notional = float(qty) * float(premium or 0.0) * 100.0
        order = {
            "symbol": self.symbol,
            "underlying": self.underlying,
            "side": "buy",
            "type": self.option_type,
            "option_type": self.option_type,
            "qty": int(qty),
            "order_class": "simple",
            "legs": 1,
            "level": 2,
            "limit_price": premium or None,
            "expiration": self.expiration,
            "strike": self.strike,
            "dte": self.dte,
            "estimated_risk_usd": round(notional, 2),
            "max_risk_usd": max_risk_usd,
            "intent": self.intent,
        }
        return order


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_expiration(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def contract_dte(contract: dict[str, Any], as_of: date | None = None) -> int | None:
    exp = _parse_expiration(contract.get("expiration_date") or contract.get("expiration"))
    if exp is None:
        return None
    today = as_of or date.today()
    return (exp - today).days


def liquidity_score(contract: dict[str, Any]) -> float:
    """Higher is better. Prefer open interest and a known close/premium."""
    oi = _as_float(contract.get("open_interest"))
    premium = _as_float(contract.get("close_price") or contract.get("last_price"))
    tradable = 1.0 if contract.get("tradable", True) else 0.0
    status_ok = 1.0 if str(contract.get("status", "active")).lower() == "active" else 0.0
    oi_term = min(oi, 50_000) / 50_000.0
    premium_term = 1.0 if premium > 0 else 0.0
    return (0.55 * oi_term) + (0.30 * premium_term) + (0.10 * tradable) + (0.05 * status_ok)


def moneyness_score(strike: float, underlying_price: float, option_type: str) -> float:
    """Prefer near-ATM; slight preference for mild OTM on direction."""
    if underlying_price <= 0 or strike <= 0:
        return 0.0
    rel = (strike - underlying_price) / underlying_price
    if option_type == "call":
        # mild OTM call (~0 to +5%) scores best; deep ITM/OTM worse
        target = 0.02
    else:
        target = -0.02
    distance = abs(rel - target)
    return max(0.0, 1.0 - distance / 0.15)


def pick_liquid_candidates(
    underlyings: list[Stock] | list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    *,
    dte_min: int = 14,
    dte_max: int = 42,
    max_per_underlying: int = 2,
    min_open_interest: float = 10.0,
    as_of: date | None = None,
) -> list[OptionCandidate]:
    """From a flat contract list + underlyings, pick up to one call and one put each.

    Network-free. Fail-closed: illiquid / out-of-window / non-tradable are dropped.
    """
    price_by_symbol: dict[str, float] = {}
    for item in underlyings:
        if isinstance(item, Stock):
            price_by_symbol[item.symbol.upper()] = float(item.price or 0.0)
        elif isinstance(item, dict):
            sym = str(item.get("symbol") or item.get("ticker") or "").upper()
            if sym:
                price_by_symbol[sym] = _as_float(
                    item.get("price") or item.get("last") or item.get("close")
                )

    by_underlying: dict[str, list[dict[str, Any]]] = {}
    today = as_of or date.today()
    for contract in contracts:
        if not contract.get("tradable", True):
            continue
        if str(contract.get("status", "active")).lower() not in {"active", ""}:
            continue
        underlying = str(
            contract.get("underlying_symbol")
            or contract.get("root_symbol")
            or ""
        ).upper()
        if not underlying:
            continue
        dte = contract_dte(contract, today)
        if dte is None or dte < dte_min or dte > dte_max:
            continue
        oi = _as_float(contract.get("open_interest"))
        if oi < min_open_interest and _as_float(contract.get("close_price")) <= 0:
            continue
        by_underlying.setdefault(underlying, []).append(contract)

    picks: list[OptionCandidate] = []
    for underlying, rows in by_underlying.items():
        uprice = price_by_symbol.get(underlying, 0.0)
        best_by_type: dict[str, OptionCandidate] = {}
        for contract in rows:
            otype = str(contract.get("type") or contract.get("option_type") or "").lower()
            if otype not in {"call", "put"}:
                continue
            strike = _as_float(contract.get("strike_price") or contract.get("strike"))
            premium = _as_float(contract.get("close_price") or contract.get("last_price"))
            dte = contract_dte(contract, today) or 0
            score = (
                0.55 * liquidity_score(contract)
                + 0.45 * moneyness_score(strike, uprice, otype)
            )
            # Prefer mid-window DTE (~28 days) slightly.
            mid = (dte_min + dte_max) / 2.0
            span = max(1.0, (dte_max - dte_min) / 2.0)
            score += 0.05 * max(0.0, 1.0 - abs(dte - mid) / span)

            candidate = OptionCandidate(
                underlying=underlying,
                symbol=str(contract.get("symbol") or ""),
                option_type=otype,
                side="buy",
                strike=strike,
                expiration=str(contract.get("expiration_date") or contract.get("expiration") or ""),
                dte=dte,
                premium=premium,
                open_interest=_as_float(contract.get("open_interest")),
                underlying_price=uprice,
                score=round(score, 4),
                reason=f"liquid_{otype}_dte{dte}",
                raw=contract,
            )
            if not candidate.symbol:
                continue
            prev = best_by_type.get(otype)
            if prev is None or candidate.score > prev.score:
                best_by_type[otype] = candidate

        chosen = sorted(best_by_type.values(), key=lambda c: c.score, reverse=True)
        picks.extend(chosen[: max(1, int(max_per_underlying))])

    picks.sort(key=lambda c: c.score, reverse=True)
    return picks


class OptionsOverlay:
    """Fetch contracts for screener survivors and rank Level-2 long candidates."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        client: OptionsContractsClient | None = None,
    ):
        self.config = config or {}
        self.opts = self.config.get("options", {}) or {}
        self._client = client

    @property
    def client(self) -> OptionsContractsClient:
        if self._client is None:
            self._client = OptionsContractsClient(self.config)
        return self._client

    def run(
        self,
        underlyings: list[Stock] | list[dict[str, Any]],
        *,
        fetch: Callable[..., list[dict[str, Any]]] | None = None,
        as_of: date | None = None,
    ) -> list[OptionCandidate]:
        """Screened underlyings → liquid call/put candidates (2–6w DTE by default)."""
        if not underlyings:
            return []

        dte_min = int(self.opts.get("dte_min", 14))
        dte_max = int(self.opts.get("dte_max", 42))
        max_per = int(self.opts.get("max_per_underlying", 2))
        min_oi = float(self.opts.get("min_open_interest", 10))

        symbols: list[str] = []
        for item in underlyings:
            if isinstance(item, Stock):
                symbols.append(item.symbol.upper())
            elif isinstance(item, dict):
                sym = str(item.get("symbol") or "").upper()
                if sym:
                    symbols.append(sym)
        symbols = list(dict.fromkeys(symbols))

        fetch_fn = fetch or self.client.contracts_for_dte_window
        all_contracts: list[dict[str, Any]] = []
        for symbol in symbols:
            try:
                rows = fetch_fn(
                    symbol,
                    dte_min=dte_min,
                    dte_max=dte_max,
                    as_of=as_of,
                )
            except Exception as exc:  # noqa: BLE001 - fail closed per underlying
                log.warning("options overlay: contracts fetch failed for %s: %s", symbol, exc)
                continue
            all_contracts.extend(rows or [])

        return pick_liquid_candidates(
            underlyings,
            all_contracts,
            dte_min=dte_min,
            dte_max=dte_max,
            max_per_underlying=max_per,
            min_open_interest=min_oi,
            as_of=as_of,
        )
