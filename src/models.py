"""Pydantic models for the equity desk."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Market(str, Enum):
    STOCKS = "stocks"


class ExitAction(str, Enum):
    HOLD = "HOLD"
    TIGHTEN = "TIGHTEN"
    TRIM = "TRIM"
    CLOSE = "CLOSE"


class Stock(BaseModel):
    """A screener candidate."""

    symbol: str
    name: str = ""
    sector: str = "unknown"
    price: float = 0.0
    prev_close: float = 0.0
    avg_volume: float = 0.0
    volume: float = 0.0
    market_cap: float = 0.0
    raw: dict[str, Any] = Field(default_factory=dict)
    seen_at: datetime = Field(default_factory=_utcnow)

    @property
    def rel_volume(self) -> float:
        if self.avg_volume <= 0:
            return 0.0
        return self.volume / self.avg_volume

    @property
    def gap_pct(self) -> float:
        if self.prev_close <= 0:
            return 0.0
        return (self.price - self.prev_close) / self.prev_close


class Position(BaseModel):
    """An open equity position."""

    market: Market = Market.STOCKS
    symbol: str
    quantity: float
    entry_price: float
    current_price: float = 0.0
    amount_usd: float = 0.0
    stop_price: float | None = None
    take_profit_price: float | None = None
    sector: str = "unknown"
    opened_at: datetime = Field(default_factory=_utcnow)
    score: float = 0.0
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def hold_time_hours(self) -> float:
        opened = self.opened_at
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        return (_utcnow() - opened).total_seconds() / 3600.0

    @property
    def pnl_usd(self) -> float:
        if not self.current_price:
            return 0.0
        return (self.current_price - self.entry_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0 or not self.current_price:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price


class Allocation(BaseModel):
    """Budget assignment. Stocks-only desk: always 100% equities."""

    stocks_pct: float = 1.0
    reason: str = ""
    decided_at: datetime = Field(default_factory=_utcnow)

    def normalized(self, stock_max_pct: float = 1.0) -> "Allocation":
        """Clamp stocks share; this fork is equity-only so result is always 1.0."""
        stocks = max(0.0, min(float(self.stocks_pct), stock_max_pct))
        if stocks <= 0:
            stocks = 1.0
        return Allocation(
            stocks_pct=1.0,  # equity-only desk
            reason=self.reason,
            decided_at=self.decided_at,
        )


class Pulse(BaseModel):
    """Regime read for equities."""

    market: Market = Market.STOCKS
    regime: str = "unknown"
    go_signal: float = 0.0
    risk_appetite: float = 0.5
    notes: str = ""
    fetched_at: datetime = Field(default_factory=_utcnow)


class Decision(BaseModel):
    """Final verdict on one candidate, ready to log."""

    market: Market = Market.STOCKS
    symbol: str
    score: float = 0.0
    buy: bool = False
    reason: str = ""
    agent_scores: dict[str, Any] = Field(default_factory=dict)
    amount_usd: float = 0.0
