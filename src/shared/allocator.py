"""Bot 10 — capital allocator.

This fork is equity-only (Options Paper Desk next). The allocator returns a
fixed 100% stocks assignment. A future multi-sleeve options desk can restore
model-driven splits without touching the risk gate.
"""

from __future__ import annotations

from typing import Any

from ..base_agent import TEXT, UNIT, GrokAgent, schema
from ..models import Allocation

PROMPT = """You confirm the daily budget for an equity-only trading desk.

This desk trades US equities only. Always assign 100% of the budget to stocks.
Summarise the equity regime in one short reason string.

Reply ONLY JSON, no explanation.
Schema: {"stocks_pct": float 0..1, "reason": string}"""


class Allocator(GrokAgent):
    name = "allocator"
    model_tier = "fast"
    PROMPT = PROMPT
    SCHEMA = schema({"stocks_pct": UNIT, "reason": TEXT})
    # Market pulse is already in the payload; no extra retrieval needed.
    SEARCH = None

    def facts(self, payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    def memory_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        from ..models import Market

        return {
            "track_record": {
                "stocks": self.memory.summary(Market.STOCKS),
            }
        }

    def postprocess(self, data: dict[str, Any]) -> dict[str, Any]:
        # Equity-only: ignore any model attempt to leave stocks.
        reason = str(data.get("reason", "") or "stocks-only desk")
        return {"stocks_pct": 1.0, "reason": reason}

    def fallback(self) -> dict[str, Any]:
        return {"stocks_pct": 1.0, "reason": "allocator_unavailable"}

    async def allocate(
        self,
        market_pulse: dict[str, Any],
        weekly_pnl: dict[str, float],
        risk: dict[str, Any] | None = None,
    ) -> Allocation:
        """Return a fixed 100% stocks allocation (equity-only desk)."""
        result = await self.run(
            {
                "market_pulse": market_pulse,
                "weekly_pnl_usd": weekly_pnl,
            }
        )
        risk = risk or (self.config.get("risk", {}) or {})
        allocation = Allocation(
            stocks_pct=1.0,
            reason=result.get("reason", "stocks-only desk"),
        )
        return allocation.normalized(
            stock_max_pct=float(risk.get("stock_max_pct", 1.0)),
        )
