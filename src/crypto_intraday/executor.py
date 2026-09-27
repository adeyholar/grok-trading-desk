"""Phase 3 Crypto Intraday paper executor — Alpaca BTC/USD ETH/USD only.

Fail-closed allowlist. Defined-risk tickets. Dry-run default. Paper host locked.
Worker discretion inside Adeola Go + discipline. No pump.fun / Solana. Never invent fills.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..shared.alpaca_paper import (
    PAPER_BASE_URL,
    assert_paper_base_url,
    resolve_paper_credentials,
)
from ..shared.discipline import (
    BookDisciplineState,
    DisciplineError,
    assert_never_invent_fills,
    require_defined_risk,
    require_evidence_cites,
)
from .session import EXIT_POLICY_NOTE
from .symbols import is_allowed_crypto_symbol, normalize_crypto_symbol
from .ticket import marketable_limit_price

log = logging.getLogger(__name__)

ALLOWED_SIDES = frozenset({"buy"})
DEFAULT_MAX_RISK_USD = 50.0


class OrderValidationError(ValueError):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def validate_crypto_intraday_order(order: dict[str, Any]) -> None:
    if not isinstance(order, dict):
        raise OrderValidationError("invalid_order", "order must be a dict")
    symbol = normalize_crypto_symbol(str(order.get("symbol") or ""))
    if not symbol or not is_allowed_crypto_symbol(symbol):
        raise OrderValidationError(
            "symbol_not_allowed",
            str(order.get("symbol") or "missing") + " (Alpaca paper BTC/USD ETH/USD only)",
        )
    side = str(order.get("side") or "").strip().lower()
    if side not in ALLOWED_SIDES:
        raise OrderValidationError("side_not_allowed", side or "missing")
    try:
        qty_f = float(order.get("qty") or order.get("quantity") or 0)
    except (TypeError, ValueError) as exc:
        raise OrderValidationError("invalid_qty", str(exc)) from exc
    if qty_f <= 0:
        raise OrderValidationError("invalid_qty", str(qty_f))
    limit = order.get("limit_price")
    if limit is None:
        raise OrderValidationError("missing_limit_price")
    try:
        if float(limit) <= 0:
            raise OrderValidationError("invalid_limit_price", str(limit))
    except (TypeError, ValueError) as exc:
        raise OrderValidationError("invalid_limit_price", str(exc)) from exc
    try:
        require_defined_risk(order)
        require_evidence_cites(order, min_cites=1, context="crypto_intraday_order")
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


class CryptoIntradayExecutor:
    """Dry-run or paper-submit a long-only defined-risk crypto ticket."""

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
        lane = self.config.get("crypto_intraday", {}) or {}
        if dry_run is None:
            dry_run = bool(lane.get("dry_run", True))
        self.dry_run = bool(dry_run)
        self.paper = bool(paper)
        configured = base_url or lane.get("base_url") or PAPER_BASE_URL
        self.base_url = assert_paper_base_url(str(configured))
        self.api_key, self.api_secret = resolve_paper_credentials(self.config)
        self.max_risk_usd = float(lane.get("max_risk_usd", DEFAULT_MAX_RISK_USD))
        self.limit_slippage = float(lane.get("limit_slippage", 0.001))
        self.discipline = discipline or BookDisciplineState(
            open_count=int(lane.get("open_count", 0) or 0),
            session_losses=int(lane.get("session_losses", 0) or 0),
            max_open=int(lane.get("max_open", 2) or 2),
            loss_streak_no_go=int(lane.get("loss_streak_no_go", 2) or 2),
            book="crypto_intraday",
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
                    "missing Alpaca paper credentials for crypto intraday submit"
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
                "non_paper_refused", "crypto intraday requires paper=True"
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
        payload["symbol"] = normalize_crypto_symbol(str(payload.get("symbol") or ""))
        payload["side"] = "buy"
        payload.setdefault("time_in_force", "gtc")
        payload.setdefault("order_type", "limit")
        payload.setdefault("mode", "intraday")
        payload.setdefault("discretionary", True)
        payload.setdefault("discretion_owner", "workers")
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
            stop_pct = float(payload.get("stop_pct") or 0.015)
            payload["stop_pct"] = stop_pct
            payload["stop_price"] = round(ref * (1.0 - stop_pct), 6)

        validate_crypto_intraday_order(payload)

        do_submit = (not self.dry_run) if submit is None else bool(submit)
        if do_submit and self.dry_run:
            do_submit = False

        result = {
            "symbol": payload["symbol"],
            "side": "buy",
            "qty": float(payload["qty"]),
            "limit_price": float(payload["limit_price"]),
            "stop_price": payload.get("stop_price"),
            "target_price": payload.get("target_price"),
            "max_risk_usd": payload.get("max_risk_usd"),
            "estimated_risk_usd": payload.get("estimated_risk_usd"),
            "mode": "intraday",
            "discretionary": True,
            "discretion_owner": "workers",
            "paper": self.paper,
            "base_url": self.base_url,
            "dry_run": not do_submit,
            "filled": False,
            "exit_policy": EXIT_POLICY_NOTE,
            "order": payload,
        }

        if not do_submit:
            log.info(
                "CRYPTO INTRADAY DRY-RUN buy %s qty=%s @ limit %s stop=%s",
                result["symbol"],
                result["qty"],
                result["limit_price"],
                result.get("stop_price"),
            )
            out = {**result, "submitted": False, "order_id": "DRY_RUN"}
            assert_never_invent_fills(out)
            self.dry_run_log.append(out)
            return out

        self._ensure_allowed_to_submit()
        body = {
            "symbol": payload["symbol"],
            "qty": str(payload["qty"]),
            "side": "buy",
            "type": "limit",
            "time_in_force": payload.get("time_in_force", "gtc"),
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
            "CRYPTO INTRADAY PAPER SUBMIT buy %s id=%s", result["symbol"], order_id
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
