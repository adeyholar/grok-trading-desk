"""Single-leg Level-2 options buy executor — paper host, dry-run by default.

Live POST only when:
  * dry_run is False (CLI --submit-paper), AND
  * paper is True, AND
  * base_url is the Alpaca paper host.

Never sell-to-open. Never multi-leg. Never live (api.alpaca.markets).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .contracts import (
    PAPER_BASE_URL,
    assert_paper_base_url,
    resolve_paper_credentials,
)

log = logging.getLogger(__name__)

ALLOWED_INTENTS = frozenset({"buy call", "buy put"})
ALLOWED_SIDES = frozenset({"buy"})
ALLOWED_OPTION_TYPES = frozenset({"call", "put"})
MAX_LEVEL = 2


class OrderValidationError(ValueError):
    """Hard gate failure — order must not be submitted."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _normalize_intent(order: dict[str, Any]) -> str:
    intent = str(order.get("intent") or "").strip().lower()
    if intent:
        return " ".join(intent.split())
    side = str(order.get("side") or "").strip().lower()
    otype = str(
        order.get("option_type") or order.get("type") or order.get("contract_type") or ""
    ).strip().lower()
    if otype in {"market", "limit", "stop", "stop_limit"}:
        # Alpaca order "type" is the order type; option type lives elsewhere.
        otype = str(order.get("option_type") or "").strip().lower()
    return f"{side} {otype}".strip()


def validate_option_order(order: dict[str, Any], *, max_level: int = MAX_LEVEL) -> None:
    """Hard validators for Level-2 long-only single-leg buys. Raises on failure."""
    if not isinstance(order, dict):
        raise OrderValidationError("invalid_order", "order must be a dict")

    symbol = str(order.get("symbol") or "").strip()
    if not symbol:
        raise OrderValidationError("missing_symbol")

    # Multi-leg refusal
    legs = order.get("legs")
    if isinstance(legs, list) and len(legs) > 1:
        raise OrderValidationError("mleg_rejected", f"{len(legs)} legs")
    if isinstance(legs, (int, float)) and int(legs) > 1:
        raise OrderValidationError("mleg_rejected", f"legs={legs}")
    order_class = str(order.get("order_class") or "simple").strip().lower()
    if order_class in {"mleg", "multi", "multileg", "bracket", "oco", "oto"}:
        raise OrderValidationError("mleg_rejected", f"order_class={order_class}")
    if order.get("leg_requests") or order.get("legs_requests"):
        raise OrderValidationError("mleg_rejected", "leg_requests present")

    side = str(order.get("side") or "").strip().lower()
    if side not in ALLOWED_SIDES:
        raise OrderValidationError("side_not_allowed", side or "missing")
    if side == "sell" or str(order.get("position_intent") or "").lower() in {
        "sell_to_open",
        "sto",
    }:
        raise OrderValidationError("sell_to_open_rejected", side)

    otype = str(
        order.get("option_type") or order.get("contract_type") or ""
    ).strip().lower()
    # Fall back to type only when it looks like call/put
    if not otype:
        maybe = str(order.get("type") or "").strip().lower()
        if maybe in ALLOWED_OPTION_TYPES:
            otype = maybe
    if otype not in ALLOWED_OPTION_TYPES:
        raise OrderValidationError("option_type_not_allowed", otype or "missing")

    intent = _normalize_intent({**order, "option_type": otype, "side": side})
    if intent not in ALLOWED_INTENTS:
        raise OrderValidationError("intent_not_allowed", intent)

    level = order.get("level", max_level)
    try:
        level_i = int(level)
    except (TypeError, ValueError) as exc:
        raise OrderValidationError("invalid_level", str(level)) from exc
    if level_i > max_level or level_i < 1:
        raise OrderValidationError("level_exceeded", f"level={level_i} max={max_level}")
    if max_level > MAX_LEVEL:
        raise OrderValidationError("level_exceeded", f"product max_level is {MAX_LEVEL}")

    qty = order.get("qty", order.get("quantity", 0))
    try:
        qty_i = int(qty)
    except (TypeError, ValueError) as exc:
        raise OrderValidationError("invalid_qty", str(qty)) from exc
    if qty_i < 1:
        raise OrderValidationError("invalid_qty", str(qty_i))

    max_risk = order.get("max_risk_usd")
    estimated = order.get("estimated_risk_usd")
    if max_risk is not None and estimated is not None:
        try:
            est_f = float(estimated)
            max_f = float(max_risk)
        except (TypeError, ValueError) as exc:
            raise OrderValidationError("invalid_risk", str(exc)) from exc
        if est_f > max_f + 1e-6:
            raise OrderValidationError(
                "risk_exceeded",
                f"estimated={estimated} max={max_risk}",
            )


class OptionsExecutor:
    """Submit (or dry-run log) a single-leg buy call / buy put on paper."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        dry_run: bool | None = None,
        paper: bool = True,
        base_url: str | None = None,
        client: httpx.Client | None = None,
    ):
        self.config = config or {}
        opts = self.config.get("options", {}) or {}
        self.max_level = int(opts.get("max_level", MAX_LEVEL))
        if self.max_level > MAX_LEVEL:
            self.max_level = MAX_LEVEL

        # Product default: dry-run. Explicit False only via caller / --submit-paper.
        if dry_run is None:
            dry_run = bool(opts.get("dry_run", True))
        self.dry_run = bool(dry_run)
        self.paper = bool(paper)
        configured = base_url or opts.get("base_url") or PAPER_BASE_URL
        self.base_url = assert_paper_base_url(str(configured))
        self.api_key, self.api_secret = resolve_paper_credentials(self.config)
        self.max_risk_usd = float(opts.get("max_risk_usd", 250.0))
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
                    "missing Alpaca paper credentials for options submit"
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
                "non_paper_refused", "options executor requires paper=True"
            )
        assert_paper_base_url(self.base_url)
        if "paper-api.alpaca.markets" not in self.base_url.lower():
            raise OrderValidationError("non_paper_base_url", self.base_url)

    def buy(
        self,
        order: dict[str, Any],
        *,
        submit: bool | None = None,
    ) -> dict[str, Any]:
        """Validate then dry-run log, or POST to paper if explicitly allowed."""
        payload = dict(order)
        payload.setdefault("level", self.max_level)
        payload.setdefault("order_class", "simple")
        payload.setdefault("legs", 1)
        if payload.get("max_risk_usd") is None:
            payload["max_risk_usd"] = self.max_risk_usd

        # Size qty from max risk when premium known and qty missing/zero.
        qty = int(payload.get("qty") or payload.get("quantity") or 0)
        premium = float(payload.get("limit_price") or payload.get("premium") or 0)
        if qty < 1 and premium > 0 and self.max_risk_usd > 0:
            # options multiplier 100
            qty = max(1, int(self.max_risk_usd // (premium * 100)))
            payload["qty"] = qty
            payload["estimated_risk_usd"] = round(qty * premium * 100, 2)
        elif qty >= 1 and premium > 0 and payload.get("estimated_risk_usd") is None:
            payload["estimated_risk_usd"] = round(qty * premium * 100, 2)
        payload.setdefault("qty", max(qty, 1))

        validate_option_order(payload, max_level=self.max_level)

        do_submit = (not self.dry_run) if submit is None else bool(submit)
        if do_submit and self.dry_run:
            # Caller asked to submit but executor still in dry-run → stay dry.
            do_submit = False

        result = {
            "symbol": payload["symbol"],
            "side": "buy",
            "option_type": payload.get("option_type")
            or payload.get("contract_type")
            or payload.get("type"),
            "qty": int(payload["qty"]),
            "intent": _normalize_intent(payload),
            "paper": self.paper,
            "base_url": self.base_url,
            "dry_run": not do_submit,
            "order": payload,
        }

        if not do_submit:
            log.info(
                "OPTIONS DRY-RUN %s %s x%d (risk~%s)",
                result["intent"],
                result["symbol"],
                result["qty"],
                payload.get("estimated_risk_usd"),
            )
            self.dry_run_log.append(result)
            return {**result, "submitted": False, "order_id": "DRY_RUN"}

        self._ensure_allowed_to_submit()
        body = {
            "symbol": payload["symbol"],
            "qty": str(int(payload["qty"])),
            "side": "buy",
            "type": "market" if not payload.get("limit_price") else "limit",
            "time_in_force": payload.get("time_in_force", "day"),
        }
        if body["type"] == "limit":
            body["limit_price"] = str(payload["limit_price"])

        response = self.http.post(
            f"{self.base_url}/v2/orders",
            headers=self._headers(),
            json=body,
        )
        response.raise_for_status()
        data = response.json()
        order_id = str(data.get("id") or data.get("client_order_id") or "")
        log.info(
            "OPTIONS PAPER SUBMIT %s %s x%d id=%s",
            result["intent"],
            result["symbol"],
            result["qty"],
            order_id,
        )
        out = {**result, "submitted": True, "order_id": order_id, "broker": data}
        self.submitted.append(out)
        return out
