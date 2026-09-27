"""Outcome memory: pairing buys to closes, recall ranking, prompt injection."""

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import CONFIG
from src.models import Market, Position
from src.shared.allocator import Allocator
from src.shared.exit_manager import ExitManager
from src.shared.memory import OutcomeMemory
from src.stocks.stock_checker import StockChecker


def ts(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def buy(symbol, market="stocks", score=0.7, amount=100.0, theme="", sector="", days=1):
    return {
        "ts": ts(days), "type": "buy", "market": market, "symbol": symbol,
        "score": score, "amount": amount,
        "all_agent_scores": {"narrative": {"theme": theme}, "matrix": {"sector": sector}},
    }


def close(symbol, pnl, market="stocks", hold=5.0, days=0):
    return {"ts": ts(days), "type": "close", "market": market,
            "symbol": symbol, "pnl": pnl, "hold_time": hold}


LOG = [
    buy("ACME", sector="tech", amount=500.0, days=6),
    close("ACME", 120.0, hold=30.0, days=5),
    buy("BETA", sector="energy", amount=400.0, days=4),
    close("BETA", -40.0, hold=20.0, days=3),
    buy("GAMMA", sector="tech", amount=300.0, days=2),
    close("GAMMA", -80.0, hold=10.0, days=1),
]


def mem(**over) -> OutcomeMemory:
    config = {"memory": {"enabled": True, "max_examples": 5, "lookback_days": 30, **over}}
    m = OutcomeMemory(config)
    m.load(LOG)
    return m


# --- pairing ---------------------------------------------------------------------

def test_buys_are_joined_to_their_closes():
    trades = mem()._trades
    assert len(trades) == 3
    acme = next(t for t in trades if t["symbol"] == "ACME")
    assert acme["pnl"] == 120.0
    assert acme["won"] is True
    assert acme["return_pct"] == pytest.approx(0.24)
    assert acme["sector"] == "tech"


def test_an_open_position_is_not_a_trade_yet():
    m = OutcomeMemory({})
    m.load([buy("OPEN")])
    assert m._trades == []


def test_a_close_without_its_buy_still_records_pnl():
    m = OutcomeMemory({})
    m.load([close("ORPHAN", -25.0)])
    assert m._trades[0]["pnl"] == -25.0
    assert m._trades[0]["return_pct"] == 0.0


def test_lookback_window_is_enforced():
    m = OutcomeMemory({"memory": {"lookback_days": 2}})
    m.load(LOG)
    assert all(t["symbol"] not in {"ACME", "BETA"} for t in m._trades)


def test_malformed_records_are_skipped_not_fatal():
    m = OutcomeMemory({})
    m.load([{"type": "close", "ts": "not-a-date", "symbol": "X", "market": "stocks", "pnl": 1}])
    assert len(m._trades) == 1


# --- recall -----------------------------------------------------------------------

def test_recall_is_scoped_to_stocks():
    assert all(t["market"] == "stocks" for t in mem().recall(Market.STOCKS))


def test_the_same_symbol_ranks_above_the_same_sector():
    recalled = mem().recall(Market.STOCKS, symbol="BETA", sector="tech")
    assert recalled[0]["symbol"] == "BETA"


def test_sector_matching_works():
    recalled = mem().recall(Market.STOCKS, sector="energy")
    assert recalled[0]["symbol"] == "BETA"


def test_recall_respects_max_examples():
    assert len(mem(max_examples=2).recall(Market.STOCKS)) == 2


def test_recall_is_empty_when_disabled():
    assert mem(enabled=False).recall(Market.STOCKS) == []


# --- summary -----------------------------------------------------------------------

def test_summary_reports_the_base_rate():
    summary = mem().summary(Market.STOCKS)
    assert summary["closed_trades"] == 3
    assert summary["win_rate"] == pytest.approx(1 / 3, abs=0.01)
    assert summary["total_pnl"] == pytest.approx(0.0)
    assert summary["avg_win"] == pytest.approx(120.0)
    assert summary["avg_loss"] == pytest.approx(-60.0)


def test_summary_surfaces_the_worst_sectors():
    worst = mem().summary(Market.STOCKS)["worst_themes"]
    # energy = -40, tech = 120 + (-80) = +40 → energy is worst
    assert worst[0]["theme"] == "energy"
    assert {w["theme"] for w in worst} == {"energy", "tech"}


def test_summary_of_an_empty_market_is_empty():
    assert OutcomeMemory({}).summary(Market.STOCKS) == {}


# --- prompt injection -----------------------------------------------------------------

def test_context_is_empty_on_a_cold_desk():
    assert OutcomeMemory({}).context(Market.STOCKS) == {}


def test_context_carries_both_the_record_and_the_examples():
    block = mem().context(Market.STOCKS, sector="tech")["past_outcomes"]
    assert block["this_market"]["closed_trades"] == 3
    assert any(t["symbol"] == "ACME" for t in block["comparable_trades"])


async def test_stock_checker_prompt_includes_past_outcomes(client_factory):
    agent = StockChecker(CONFIG, client=client_factory({"approve": False}))
    agent.memory = mem()
    await agent.run({"stock": {"symbol": "ACME", "sector": "tech"}})

    sent = agent._client.calls[0]["json"]["messages"][1]["content"]
    assert "past_outcomes" in sent
    assert "ACME" in sent


async def test_exit_manager_prompt_includes_past_outcomes(client_factory):
    agent = ExitManager(CONFIG, client=client_factory({"action": "HOLD"}))
    agent.memory = mem()
    await agent.run(Position(market=Market.STOCKS, symbol="ACME", quantity=1,
                             entry_price=10.0, sector="tech"))

    sent = agent._client.calls[0]["json"]["messages"][1]["content"]
    assert "past_outcomes" in sent and "ACME" in sent


async def test_allocator_prompt_includes_stocks_track_record(client_factory):
    agent = Allocator(CONFIG, client=client_factory({"stocks_pct": 1.0, "reason": "ok"}))
    agent.memory = mem()
    await agent.run({})

    sent = agent._client.calls[0]["json"]["messages"][1]["content"]
    assert "track_record" in sent and "stocks" in sent


async def test_no_memory_attached_leaves_the_prompt_alone(client_factory):
    agent = StockChecker(CONFIG, client=client_factory({"approve": False}))
    await agent.run({"stock": {"symbol": "X"}})
    sent = agent._client.calls[0]["json"]["messages"][1]["content"]
    assert "past_outcomes" not in sent
