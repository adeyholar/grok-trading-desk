import pytest

from tests.conftest import CONFIG
from src.models import Allocation
from src.shared.allocator import Allocator

MARKET_PULSE = {"regime": "neutral", "go_signal": 0.5}
PNL = {"stocks": -40.0}


async def test_allocator_always_returns_full_stocks(client_factory):
    client = client_factory({"stocks_pct": 0.4, "reason": "tilt ignored"})
    result = await Allocator(CONFIG, client=client).run({})
    assert result["stocks_pct"] == pytest.approx(1.0)
    assert result["reason"] == "tilt ignored"


async def test_allocator_falls_back_to_full_stocks_on_broken_json(client_factory, no_sleep):
    result = await Allocator(CONFIG, client=client_factory("not json")).run({})
    assert result == {"stocks_pct": 1.0, "reason": "allocator_unavailable"}


async def test_allocator_forces_full_stocks_even_when_split_missing(client_factory, no_sleep):
    result = await Allocator(CONFIG, client=client_factory({"reason": "dunno"})).run({})
    assert result["stocks_pct"] == 1.0
    assert result["reason"] == "dunno"


async def test_allocate_is_stocks_only(client_factory):
    client = client_factory({"stocks_pct": 0.5, "reason": "ok"})
    allocation = await Allocator(CONFIG, client=client).allocate(MARKET_PULSE, PNL)
    assert isinstance(allocation, Allocation)
    assert allocation.stocks_pct == pytest.approx(1.0)


async def test_allocate_sends_pulse_and_pnl_to_the_model(client_factory):
    client = client_factory({"stocks_pct": 1.0, "reason": "ok"})
    await Allocator(CONFIG, client=client).allocate(MARKET_PULSE, PNL)
    body = client.calls[0]["json"]
    assert "Reply ONLY JSON" in body["messages"][0]["content"]
    sent = body["messages"][1]["content"]
    assert "market_pulse" in sent and "weekly_pnl_usd" in sent


def test_allocation_normalized_is_always_full_stocks():
    assert Allocation(stocks_pct=0.0).normalized().stocks_pct == 1.0
    assert Allocation(stocks_pct=0.3).normalized().stocks_pct == 1.0
