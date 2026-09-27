"""Desk-level wiring: the paths that only break when the pieces are combined."""

import json
from datetime import datetime, timezone

import pytest
import yaml

from tests.conftest import FakeClient
from src.desk import TradingDesk
from src.models import Market, Position, Stock
from src.stocks.stock_executor import OrderRejected, classify_rejection

GOOD = {
    "market_pulse": {"regime": "risk_on", "go_signal": 0.8, "volatility": "low"},
    "analyst": {"fundamentals_score": 0.8, "technicals_score": 0.85, "trend": "up",
                "support": 45, "resistance": 60, "valuation": "fair"},
    "radar": {"sentiment_score": 0.8, "news_momentum": 0.7, "controversy": 0.05,
              "catalysts": ["earnings"]},
    "insider": {"insider_buying": 0.7, "insider_selling": 0.1, "institutional_flow": 0.8,
                "cluster_buying": True},
    "stock_checker": {"approve": True, "confidence": 0.8, "adjusted_score": 0.75,
                      "suggested_stop_pct": 0.07, "suggested_target_pct": 0.18},
    "allocator": {"stocks_pct": 1.0, "reason": "stocks-only desk"},
    "exit_manager": {"action": "HOLD", "reason": "intact", "confidence": 0.6},
}

STOCK = Stock(symbol="ACME", sector="technology", price=50, prev_close=46,
              avg_volume=2e6, volume=6e6, market_cap=5e9)


def build(tmp_path, overrides=None, **kwargs) -> TradingDesk:
    config = yaml.safe_load(open("config.example.yaml"))
    config["logging"] = {"path": str(tmp_path / "desk.jsonl"), "echo_stdout": False,
                         "cost_report_every": 0}
    desk = TradingDesk(config, dry_run=kwargs.pop("dry_run", True), **kwargs)

    replies = {**GOOD, **(overrides or {})}
    for name, reply in replies.items():
        getattr(desk, name)._client = FakeClient([reply])
    return desk


# --- stock path -------------------------------------------------------------------

async def test_a_clean_stock_is_bought(tmp_path):
    desk = build(tmp_path)
    pulse = await desk.market_pulse.run()
    result = await desk.evaluate_stock(STOCK, pulse)
    assert result["bought"] is True
    records = [json.loads(line) for line in open(tmp_path / "desk.jsonl")]
    assert records[-1]["type"] == "buy"
    assert records[-1]["market"] == "stocks"


async def test_controversy_vetoes_before_the_checker(tmp_path):
    desk = build(tmp_path, {"radar": {**GOOD["radar"], "controversy": 0.95}})
    pulse = await desk.market_pulse.run()
    assert (await desk.evaluate_stock(STOCK, pulse))["reason"] == "veto_controversy"
    assert desk.stock_checker._client.calls == []


async def test_the_checker_can_veto_a_high_scoring_stock(tmp_path):
    desk = build(tmp_path, {"stock_checker": {"approve": False, "confidence": 0.9,
                                              "kill_reasons": ["gap already filled"],
                                              "suggested_stop_pct": 0.07,
                                              "suggested_target_pct": 0.18}})
    pulse = await desk.market_pulse.run()
    assert (await desk.evaluate_stock(STOCK, pulse))["reason"] == "checker_rejected"


async def test_a_paused_market_blocks_the_session(tmp_path):
    desk = build(tmp_path, {"market_pulse": {"regime": "risk_off", "go_signal": 0.1}})
    results = await desk.run_stock_session()
    assert results == []


async def test_a_pdt_block_is_logged_as_its_own_reason(tmp_path):
    desk = build(tmp_path, dry_run=False)

    class Blocked:
        paper = True

        async def buy_bracket(self, *a, **k):
            raise OrderRejected("pdt_blocked", "403 forbidden")

        async def get_positions(self):
            return []

    desk.stock_executor = Blocked()
    pulse = await desk.market_pulse.run()
    result = await desk.evaluate_stock(STOCK, pulse)

    assert result["reason"] == "pdt_blocked"
    records = [json.loads(line) for line in open(tmp_path / "desk.jsonl")]
    assert records[-1]["reason"] == "pdt_blocked"


def test_rejection_classifier_recognises_the_common_refusals():
    class Err(Exception):
        def __init__(self, msg, status=None):
            super().__init__(msg)
            self.status_code = status

    assert classify_rejection(Err("forbidden", 403)) == "pdt_blocked"
    assert classify_rejection(Err("potential wash trade detected")) == "wash_trade_blocked"
    assert classify_rejection(Err("insufficient buying power")) == "insufficient_buying_power"
    assert classify_rejection(Err("asset is not active")) == "asset_not_tradable"
    assert classify_rejection(Err("slow down", 429)) == "broker_rate_limited"
    assert classify_rejection(Err("something odd")) == "order_rejected"


# --- risk interaction ---------------------------------------------------------------

async def test_the_daily_loss_limit_stops_new_entries(tmp_path):
    desk = build(tmp_path)
    desk.risk.record_close(Market.STOCKS, -desk.risk.daily_loss_limit_usd)
    pulse = await desk.market_pulse.run()
    assert (await desk.evaluate_stock(STOCK, pulse))["reason"] == "daily_loss_limit_reached"


async def test_the_sector_cap_is_enforced_through_the_desk(tmp_path):
    desk = build(tmp_path)
    desk.positions = [
        Position(market=Market.STOCKS, symbol=s, quantity=1, entry_price=10, sector="technology")
        for s in ("A", "B")
    ]
    pulse = await desk.market_pulse.run()
    assert (await desk.evaluate_stock(STOCK, pulse))["reason"] == "max_per_sector"


# --- allocation, exits, memory --------------------------------------------------------

async def test_allocation_is_stocks_only_and_logged(tmp_path):
    desk = build(tmp_path, {"allocator": {"stocks_pct": 1.0, "reason": "equity desk"}})
    allocation = await desk.run_allocation()
    assert allocation.stocks_pct == pytest.approx(1.0)
    assert desk.risk.allocation.stocks_pct == allocation.stocks_pct
    kinds = [json.loads(line)["type"] for line in open(tmp_path / "desk.jsonl")]
    assert "allocation" in kinds and "cost" in kinds


async def test_exit_pass_holds_and_logs_an_action(tmp_path):
    desk = build(tmp_path)
    desk.positions = [Position(market=Market.STOCKS, symbol="ACME", quantity=10,
                               entry_price=50.0, current_price=55.0)]
    decisions = await desk.run_exit_pass()
    assert decisions[0]["action"] == "HOLD"
    actions = [json.loads(l) for l in open(tmp_path / "desk.jsonl") if '"action"' in l]
    assert actions[-1]["action"] == "HOLD"


async def test_memory_reaches_the_checker_but_not_the_analyst(tmp_path):
    desk = build(tmp_path)
    assert desk.stock_checker.memory is desk.memory
    assert desk.exit_manager.memory is desk.memory
    assert desk.allocator.memory is desk.memory
    assert desk.analyst.memory is None
    assert desk.radar.memory is None


async def test_past_outcomes_reach_the_checker_prompt(tmp_path):
    desk = build(tmp_path)
    desk.log.buy("stocks", "OLDCO", 0.7, {"matrix": {"sector": "technology"}}, 100.0, "tx")
    desk.log.close("stocks", "OLDCO", -60.0, 4.0)
    desk.refresh_memory()

    pulse = await desk.market_pulse.run()
    await desk.evaluate_stock(STOCK, pulse)
    sent = desk.stock_checker._client.calls[0]["json"]["messages"][1]["content"]
    assert "past_outcomes" in sent and "OLDCO" in sent


# --- costs -----------------------------------------------------------------------------

async def test_costs_accumulate_across_the_whole_desk(tmp_path):
    desk = build(tmp_path)
    from tests.conftest import FakeResponse

    usage = {"prompt_tokens": 100, "completion_tokens": 20, "cost_in_usd_ticks": 10_000_000_000}
    for name in ("analyst", "radar", "insider", "stock_checker"):
        bot = getattr(desk, name)
        bot._client = FakeClient([FakeResponse(json.dumps(GOOD[name]), usage=usage)])

    pulse = {"regime": "risk_on", "go_signal": 0.8, "volatility": "low"}
    await desk.evaluate_stock(STOCK, pulse)
    snap = desk.costs.snapshot()
    assert snap["calls"] == 4
    assert snap["cost_usd"] == pytest.approx(4.0)
    assert set(snap["by_agent"]) == {"analyst", "radar", "insider", "stock_checker"}


# --- config sanity ------------------------------------------------------------------------

def test_shipped_config_uses_live_model_slugs(tmp_path):
    desk = build(tmp_path)
    assert desk.analyst.model == "grok-4.3"
    assert desk.stock_checker.model == "grok-4.6"
    assert desk.stock_checker.model != desk.analyst.model


def test_every_retrieval_agent_declares_a_search_policy(tmp_path):
    desk = build(tmp_path)
    for name in ("radar", "insider", "analyst", "market_pulse"):
        assert getattr(desk, name).SEARCH is not None, name


def test_market_hours_window(tmp_path):
    desk = build(tmp_path)
    weekday = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)  # a Thursday
    assert desk.market_is_open(weekday.replace(hour=10, minute=0)) is True
    assert desk.market_is_open(weekday.replace(hour=8, minute=0)) is False
    saturday = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    assert desk.market_is_open(saturday) is False


def test_desk_exposes_equity_surface_only(tmp_path):
    desk = build(tmp_path)
    assert hasattr(desk, "stock_loop")
    assert hasattr(desk, "exit_loop")
    assert hasattr(desk, "allocator_loop")
    assert hasattr(desk, "screener")
    assert hasattr(desk, "stock_executor")
    assert not hasattr(desk, "scout")
