"""Phase 1 Stock Intraday paper executor — long-only, defined-risk, dry-run default.

Worker discretion inside Adeola Go + sound discipline. No autopilot time-exits.
Live POST only when dry_run is False (--submit-paper), paper=True, paper host.
Long-only v1. Shorts refused. Never invent fills.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ...shared.alpaca_paper import (
    PAPER_BASE_URL,
    assert_paper_base_url,
    resolve_paper_credentials,
)
from ...shared.discipline import (
    BookDisciplineState,
    DisciplineError,
    assert_never_invent_fills,
    require_defined_risk,
    require_evidence_cites,
)
from .session import EXIT_POLICY_NOTE, FLATTEN_BY_NOTE
from .universe import in_intraday_universe, normalize_symbol

log = logging.getLogger(__name__)

ALLOWED_SIDES = frozenset({"buy"})
DEFAULT_MAX_NOTIONAL_USD = 500.0
DEFAULT_MAX_RISK_USD = 100.0
DEFAULT_LIMIT_SLIPPAGE = 0.001


class OrderValidationError(ValueError):
    """Hard gate failure — order must not be submitted."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def marketable_limit_price(
    side: str,
    reference_price: float,
    *,
    slippage: float = DEFAULT_LIMIT_SLIPPAGE,
) -> float:
    if reference_price <= 0:
        raise OrderValidationError("invalid_price", str(reference_price))
    side_n = str(side).strip().lower()
    slip = max(0.0, float(slippage))
    if side_n == "buy":
        return round(reference_price * (1.0 + slip), 2)
    if side_n == "sell":
        return round(reference_price * (1.0 - slip), 2)
    raise OrderValidationError("side_not_allowed", side_n)


def validate_stock_intraday_order(
    order: dict[str, Any],
    *,
    require_universe: bool = True,
    extra_universe: list[str] | None = None,
) -> None:
    if not isinstance(order, dict):
        raise OrderValidationError("invalid_order", "order must be a dict")

    symbol = normalize_symbol(str(order.get("symbol") or ""))
    if not symbol:
        raise OrderValidationError("missing_symbol")

    if require_universe and not in_intraday_universe(symbol, extra=extra_universe):
        raise OrderValidationError("symbol_not_in_intraday_universe", symbol)

    side = str(order.get("side") or "").strip().lower()
    if side not in ALLOWED_SIDES:
        raise OrderValidationError("side_not_allowed", side or "missing")
    if side == "sell" or str(order.get("position_intent") or "").lower() in {
        "sell_short",
        "short",
    }:
        raise OrderValidationError("short_rejected", side)

    qty = order.get("qty", order.get("quantity", 0))
    try:
        qty_i = int(qty)
    except (TypeError, ValueError) as exc:
        raise OrderValidationError("invalid_qty", str(qty)) from exc
    if qty_i < 1:
        raise OrderValidationError("invalid_qty", str(qty_i))

    limit = order.get("limit_price")
    if limit is None:
        raise OrderValidationError("missing_limit_price", "marketable limit required")
    try:
        limit_f = float(limit)
    except (TypeError, ValueError) as exc:
        raise OrderValidationError("invalid_limit_price", str(limit)) from exc
    if limit_f <= 0:
        raise OrderValidationError("invalid_limit_price", str(limit_f))

    max_notional = order.get("max_notional_usd")
    if max_notional is not None:
        try:
            max_n = float(max_notional)
            ref_f = float(order.get("reference_price") or limit_f)
            notional = qty_i * ref_f  # size vs ref; limit may be marketable slip above
        except (TypeError, ValueError) as exc:
            raise OrderValidationError("invalid_notional", str(exc)) from exc
        if notional > max_n + 1e-6:
            raise OrderValidationError(
                "notional_exceeded",
                f"notional={notional:.2f} max={max_notional}",
            )

    try:
        require_defined_risk(order)
        require_evidence_cites(order, min_cites=1, context="stock_intraday_order")
    except DisciplineError as exc:
        raise OrderValidationError(exc.reason, exc.detail) from exc

    est = order.get("estimated_risk_usd")
    max_risk = order.get("max_risk_usd")
    if est is not None and max_risk is not None:
        try:
            if float(est) > float(max_risk) + 1e-6:
                raise OrderValidationError(
                    "risk_exceeded", f"estimated={est} max={max_risk}"
                )
        except (TypeError, ValueError) as exc:
            raise OrderValidationError("invalid_risk", str(exc)) from exc


class StockIntradayExecutor:
    """Dry-run or paper-submit a long-only defined-risk equity ticket."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        dry_run: bool | None = None,
        paper: bool = True,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        discipline: BookDisciplineState | None = None,
    ):
        self.config = config or {}
        lane = self.config.get("stock_intraday", {}) or {}
        if dry_run is None:
            dry_run = bool(lane.get("dry_run", True))
        self.dry_run = bool(dry_run)
        self.paper = bool(paper)
        configured = base_url or lane.get("base_url") or PAPER_BASE_URL
        self.base_url = assert_paper_base_url(str(configured))
        self.api_key, self.api_secret = resolve_paper_credentials(self.config)
        self.max_notional_usd = float(
            lane.get("max_notional_usd", DEFAULT_MAX_NOTIONAL_USD)
        )
        self.max_risk_usd = float(lane.get("max_risk_usd", DEFAULT_MAX_RISK_USD))
        self.limit_slippage = float(lane.get("limit_slippage", DEFAULT_LIMIT_SLIPPAGE))
        self.require_universe = bool(lane.get("require_universe", True))
        extra = lane.get("extra_symbols") or []
        self.extra_universe = [normalize_symbol(s) for s in extra]
        self.discipline = discipline or BookDisciplineState(
            open_count=int(lane.get("open_count", 0) or 0),
            session_losses=int(lane.get("session_losses", 0) or 0),
            max_open=int(lane.get("max_open", 2) or 2),
            loss_streak_no_go=int(lane.get("loss_streak_no_go", 2) or 2),
            book="stock_intraday",
        )
        self._client = client
        self._owns_client = client is None
        self.submitted: list[dict[str, Any]] = []
        self.dry_run_log: list[dict[str, Any]] = []

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json",
        }

    @property
    def http(self) -> httpx.Client:
        if self._client is None:
            if not self.api_key or not self.api_secret:
                raise RuntimeError(
                    "missing Alpaca paper credentials for stock intraday submit"
                )
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=30.0,
            )
            self._owns_client = True
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _ensure_allowed_to_submit(self) -> None:
        if not self.paper:
            raise OrderValidationError(
                "non_paper_refused", "stock intraday executor requires paper=True"
            )
        assert_paper_base_url(self.base_url)
        if "paper-api.alpaca.markets" not in self.base_url.lower():
            raise OrderValidationError("non_paper_base_url", self.base_url)

    def buy(
        self,
        order: dict[str, Any],
        *,
        submit: bool | None = None,
        reference_price: float | None = None,
    ) -> dict[str, Any]:
        try:
            self.discipline.assert_may_open()
        except DisciplineError as exc:
            raise OrderValidationError(exc.reason, exc.detail) from exc

        payload = dict(order)
        payload["symbol"] = normalize_symbol(str(payload.get("symbol") or ""))
        payload["side"] = "buy"
        payload.setdefault("time_in_force", "day")
        payload.setdefault("order_type", "limit")
        payload.setdefault("mode", "intraday")
        payload.setdefault("discretionary", True)
        payload.setdefault("discretion_owner", "workers")
        if payload.get("max_notional_usd") is None:
            payload["max_notional_usd"] = self.max_notional_usd
        if payload.get("max_risk_usd") is None:
            payload["max_risk_usd"] = self.max_risk_usd

        ref = reference_price
        if ref is None:
            ref = float(payload.get("reference_price") or payload.get("price") or 0)
        if payload.get("limit_price") is None and ref > 0:
            payload["limit_price"] = marketable_limit_price(
                "buy", ref, slippage=self.limit_slippage
            )
            payload["reference_price"] = ref
        if ref > 0 and payload.get("stop_price") is None:
            stop_pct = float(payload.get("stop_pct") or 0.01)
            payload["stop_pct"] = stop_pct
            payload["stop_price"] = round(ref * (1.0 - stop_pct), 2)
        if ref > 0 and payload.get("target_price") is None:
            target_pct = float(payload.get("target_pct") or 0.02)
            payload["target_pct"] = target_pct
            payload["target_price"] = round(ref * (1.0 + target_pct), 2)
        if payload.get("estimated_risk_usd") is None and ref > 0:
            stop = float(payload.get("stop_price") or 0)
            qty_guess = int(payload.get("qty") or payload.get("quantity") or 0)
            if stop > 0 and qty_guess >= 1:
                payload["estimated_risk_usd"] = round(qty_guess * (ref - stop), 2)

        qty = int(payload.get("qty") or payload.get("quantity") or 0)
        limit = float(payload.get("limit_price") or 0)
        max_n = float(payload.get("max_notional_usd") or self.max_notional_usd or 0)
        size_px = float(ref or limit or 0)
        if size_px > 0 and max_n > 0:
            cap = int(max_n // size_px)
            if cap < 1:
                raise OrderValidationError(
                    "notional_exceeded",
                    f"1 share at {size_px} exceeds max_notional {max_n}",
                )
            if qty < 1:
                qty = cap
            else:
                qty = min(qty, cap)
            payload["qty"] = qty
        payload.setdefault("qty", max(qty, 1))

        validate_stock_intraday_order(
            payload,
            require_universe=self.require_universe,
            extra_universe=self.extra_universe,
        )

        do_submit = (not self.dry_run) if submit is None else bool(submit)
        if do_submit and self.dry_run:
            do_submit = False

        result = {
            "symbol": payload["symbol"],
            "side": "buy",
            "qty": int(payload["qty"]),
            "limit_price": float(payload["limit_price"]),
            "stop_price": payload.get("stop_price"),
            "target_price": payload.get("target_price"),
            "max_risk_usd": payload.get("max_risk_usd"),
            "estimated_risk_usd": payload.get("estimated_risk_usd"),
            "order_type": "limit",
            "time_in_force": payload.get("time_in_force", "day"),
            "mode": "intraday",
            "discretionary": True,
            "discretion_owner": "workers",
            "paper": self.paper,
            "base_url": self.base_url,
            "dry_run": not do_submit,
            "filled": False,
            "exit_policy": EXIT_POLICY_NOTE,
            "flatten_note": FLATTEN_BY_NOTE,
            "order": payload,
        }

        if not do_submit:
            log.info(
                "STOCK INTRADAY DRY-RUN buy %s x%d @ limit %.2f stop=%s risk~%s",
                result["symbol"],
                result["qty"],
                result["limit_price"],
                result.get("stop_price"),
                result.get("estimated_risk_usd"),
            )
            out = {**result, "submitted": False, "order_id": "DRY_RUN"}
            assert_never_invent_fills(out)
            self.dry_run_log.append(out)
            return out

        self._ensure_allowed_to_submit()
        body = {
            "symbol": payload["symbol"],
            "qty": str(int(payload["qty"])),
            "side": "buy",
            "type": "limit",
            "time_in_force": payload.get("time_in_force", "day"),
            "limit_price": str(payload["limit_price"]),
        }
        response = self.http.post(
            f"{self.base_url}/v2/orders",
            headers=self._headers(),
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        order_id = str(data.get("id") or data.get("client_order_id") or "")
        log.info(
            "STOCK INTRADAY PAPER SUBMIT buy %s x%d id=%s",
            result["symbol"],
            result["qty"],
            order_id,
        )
        out = {
            **result,
            "dry_run": False,
            "submitted": True,
            "order_id": order_id,
            "broker": data,
        }
        self.submitted.append(out)
        self.discipline.open_count += 1
        return out
