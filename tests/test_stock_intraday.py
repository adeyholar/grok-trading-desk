"""Unit tests for Stock Intraday — defined-risk dry-run, no network."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from src.shared.alpaca_paper import PAPER_BASE_URL
from src.shared.discipline import (
    BookDisciplineState,
    DisciplineError,
    gate_high_checker_go,
    require_evidence_cites,
)
from src.stocks.intraday.executor import (
    OrderValidationError,
    StockIntradayExecutor,
    marketable_limit_price,
    validate_stock_intraday_order,
)
from src.stocks.intraday.loop import run_stock_intraday_pass
from src.stocks.intraday.session import (
    EXIT_POLICY_NOTE,
    at_session_reminder,
    session_status,
    should_flatten_by,
)
from src.stocks.intraday.ticket import build_stock_intraday_ticket
from src.stocks.intraday.universe import (
    filter_intraday_universe,
    in_intraday_universe,
)


class _BoomClient:
    def post(self, *args, **kwargs):
        raise AssertionError("dry-run must not POST")

    def get(self, *args, **kwargs):
        raise AssertionError("dry-run must not GET")


def _cites(**over: Any) -> list[dict[str, Any]]:
    base = [
        {"kind": "market_fact", "ref": "AAPL last=200 bid/ask tight (test fixture)"},
        {"kind": "living_log_lesson", "ref": "lesson-test-001 respect defined risk"},
    ]
    if over:
        base.append(dict(over))
    return base


def test_universe_mega_and_etf():
    assert in_intraday_universe("AAPL")
    assert in_intraday_universe("SPY")
    assert not in_intraday_universe("ZZZZ")
    assert filter_intraday_universe(["aapl", "ZZZ", "QQQ"]) == ["AAPL", "QQQ"]


def test_marketable_limit_buy_above_ref():
    assert marketable_limit_price("buy", 100.0, slippage=0.001) == 100.1


def test_ticket_requires_defined_risk_and_evidence():
    t = build_stock_intraday_ticket(
        symbol="AAPL",
        reference_price=200.0,
        max_risk_usd=100.0,
        evidence_cites=_cites(),
    )
    assert t["stop_price"] < 200
    assert t["target_price"] > 200
    assert t["max_risk_usd"] == 100.0
    assert t["discretion_owner"] == "workers"
    require_evidence_cites(t, min_cites=1)


def test_ticket_rejects_missing_evidence():
    with pytest.raises((DisciplineError, ValueError, OrderValidationError)):
        build_stock_intraday_ticket(
            symbol="AAPL", reference_price=200.0, evidence_cites=[]
        )


def test_validate_rejects_short_and_unknown():
    good = build_stock_intraday_ticket(
        symbol="AAPL", reference_price=200.0, evidence_cites=_cites()
    )
    validate_stock_intraday_order(good)
    with pytest.raises(OrderValidationError) as ei:
        validate_stock_intraday_order({**good, "side": "sell", "position_intent": "short"})
    assert ei.value.reason in {"side_not_allowed", "short_rejected"}
    with pytest.raises(OrderValidationError) as ei2:
        validate_stock_intraday_order({**good, "symbol": "ZZZZ"})
    assert ei2.value.reason == "symbol_not_in_intraday_universe"


def test_executor_dry_run_no_post():
    ex = StockIntradayExecutor(
        {"stock_intraday": {"dry_run": True, "base_url": PAPER_BASE_URL}},
        dry_run=True,
        paper=True,
        client=_BoomClient(),  # type: ignore[arg-type]
    )
    ticket = build_stock_intraday_ticket(
        symbol="MSFT", reference_price=400.0, evidence_cites=_cites()
    )
    result = ex.buy(ticket)
    assert result["submitted"] is False
    assert result["dry_run"] is True
    assert result["order_id"] == "DRY_RUN"
    assert result["filled"] is False
    assert result["stop_price"] is not None


def test_executor_refuses_live_host():
    with pytest.raises(ValueError):
        StockIntradayExecutor(
            dry_run=False, paper=True, base_url="https://api.alpaca.markets"
        )


def test_discipline_max_open_and_loss_no_go():
    state = BookDisciplineState(open_count=2, max_open=2, book="stock_intraday")
    with pytest.raises(DisciplineError) as ei:
        state.assert_may_open()
    assert ei.value.reason == "max_open_exceeded"
    state2 = BookDisciplineState(session_losses=2, loss_streak_no_go=2)
    with pytest.raises(DisciplineError) as ei2:
        state2.assert_may_open()
    assert ei2.value.reason == "no_go_after_losses"


def test_gate_checker_fail_closed():
    g = gate_high_checker_go(high_ok=True, checker_approve=None)
    assert g["approve"] is False
    assert g["reason"] == "checker_fail_closed"


def test_session_no_force_flatten():
    status = session_status()
    assert status["force_time_exit"] is False
    assert status["should_flatten"] is False
    assert status["discretion_owner"] == "workers"
    assert "worker" in EXIT_POLICY_NOTE.lower() or "judgment" in EXIT_POLICY_NOTE.lower()
    # reminder helper is informational only
    noon = datetime(2026, 9, 27, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    assert should_flatten_by(noon) is False
    assert at_session_reminder(noon) is False


def test_run_pass_dry_run():
    config = {
        "stock_intraday": {
            "dry_run": True,
            "max_risk_usd": 100,
            "base_url": PAPER_BASE_URL,
            "evidence_cites": [
                {"kind": "reviewed_journal", "ref": "journal-2026-09-27"}
            ],
        }
    }
    ex = StockIntradayExecutor(
        config, dry_run=True, paper=True, client=_BoomClient()  # type: ignore[arg-type]
    )
    results = run_stock_intraday_pass(
        config,
        symbols=["AAPL", "SPY"],
        prices={"AAPL": 200.0, "SPY": 500.0},
        dry_run=True,
        submit_paper=False,
        executor=ex,
    )
    assert results
    assert all(r.get("bought") for r in results)
    assert all(r.get("dry_run") for r in results)
    assert all(r["order_id"] == "DRY_RUN" for r in results)
    assert all(r.get("filled") is False for r in results)
