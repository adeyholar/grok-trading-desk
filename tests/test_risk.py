from datetime import date, timedelta

import pytest

from src.models import Allocation, Market, Position
from src.shared.risk import RiskManager

CONFIG = {
    "risk": {
        "total_budget_usd": 2000.0,
        "daily_loss_limit_usd": 300.0,
        "max_open_total": 5,
        "max_open_stocks": 3,
        "max_per_sector": 2,
        "stock_max_pct": 1.0,
        "max_position_pct_of_market": 0.15,
        "max_position_pct_of_remaining_loss": 0.25,
    }
}


def rm() -> RiskManager:
    return RiskManager(CONFIG)


def pos(symbol="X", sector="unknown") -> Position:
    return Position(market=Market.STOCKS, symbol=symbol, quantity=1, entry_price=1.0, sector=sector)


# --- allocation ---------------------------------------------------------------

def test_default_allocation_is_full_stocks():
    r = rm()
    assert r.market_budget(Market.STOCKS) == 2000.0
    assert r.allocation.stocks_pct == pytest.approx(1.0)


def test_set_allocation_stays_full_stocks():
    r = rm()
    r.set_allocation(Allocation(stocks_pct=0.5, reason="ignored"))
    assert r.allocation.stocks_pct == pytest.approx(1.0)
    assert r.market_budget(Market.STOCKS) == pytest.approx(2000.0)


# --- open limits ---------------------------------------------------------------

def test_open_is_allowed_on_an_empty_book():
    assert rm().can_open(Market.STOCKS, []) == (True, "ok")


def test_max_open_total_blocks():
    r = rm()
    positions = [pos(f"S{i}") for i in range(5)]
    assert r.can_open(Market.STOCKS, positions) == (False, "max_open_total")


def test_per_market_stock_cap():
    r = rm()
    stocks_full = [pos(f"S{i}") for i in range(3)]
    assert r.can_open(Market.STOCKS, stocks_full) == (False, "max_open_stocks")


def test_sector_cap_applies_to_stocks():
    r = rm()
    two_tech = [pos("A", "tech"), pos("B", "tech")]
    assert r.can_open(Market.STOCKS, two_tech, sector="tech") == (False, "max_per_sector")
    assert r.can_open(Market.STOCKS, two_tech, sector="energy") == (True, "ok")
    assert r.can_open(Market.STOCKS, two_tech, sector="unknown") == (True, "ok")


# --- daily loss ----------------------------------------------------------------

def test_daily_loss_limit_shuts_the_book():
    r = rm()
    r.record_close(Market.STOCKS, -300.0)
    assert r.daily_loss_breached() is True
    assert r.can_open(Market.STOCKS, []) == (False, "daily_loss_limit_reached")


def test_losses_count_against_the_shared_limit():
    r = rm()
    r.record_close(Market.STOCKS, -250.0)
    assert r.remaining_loss_room() == pytest.approx(50.0)
    assert r.can_open(Market.STOCKS, [])[0] is True

    r.record_close(Market.STOCKS, -60.0)
    assert r.daily_loss_breached() is True


def test_profits_do_not_inflate_the_loss_room():
    r = rm()
    r.record_close(Market.STOCKS, 500.0)
    assert r.remaining_loss_room() == pytest.approx(300.0)


def test_daily_reset_clears_pnl_and_deployment():
    r = rm()
    r.record_close(Market.STOCKS, -290.0)
    r.record_fill(Market.STOCKS, 400.0)
    assert r.maybe_reset_day(r.session_date) is False

    assert r.maybe_reset_day(date.today() + timedelta(days=1)) is True
    assert r.realized_pnl_today == 0.0
    assert r.deployed_usd[Market.STOCKS] == 0.0
    assert r.can_open(Market.STOCKS, [])[0] is True


# --- budget consumption ----------------------------------------------------------

def test_deployed_capital_exhausts_the_market_budget():
    r = rm()
    r.record_fill(Market.STOCKS, 2000.0)
    assert r.remaining_market_budget(Market.STOCKS) == 0.0
    assert r.can_open(Market.STOCKS, []) == (False, "market_budget_exhausted")


def test_an_oversized_order_is_rejected():
    r = rm()
    r.record_fill(Market.STOCKS, 1900.0)
    assert r.can_open(Market.STOCKS, [], amount_usd=50.0) == (True, "ok")
    assert r.can_open(Market.STOCKS, [], amount_usd=150.0) == (False, "exceeds_market_budget")


def test_close_returns_budget_and_records_pnl():
    r = rm()
    r.record_fill(Market.STOCKS, 500.0)
    r.record_close(Market.STOCKS, 40.0, amount_usd=500.0)
    assert r.deployed_usd[Market.STOCKS] == 0.0
    assert r.realized_pnl_today == pytest.approx(40.0)


# --- position sizing --------------------------------------------------------------

def test_position_size_respects_the_market_cap():
    r = rm()
    # 15% of 2000 = 300; loss room bound is 0.25*300 = 75 -> 75 binds
    assert r.position_size(Market.STOCKS, score=1.0) == 75.0


def test_position_size_is_bound_by_remaining_loss_room():
    r = rm()
    r.record_close(Market.STOCKS, -200.0)   # 100 left -> 25 cap
    assert r.position_size(Market.STOCKS, score=1.0) == 25.0


def test_position_size_is_bound_by_free_budget():
    r = rm()
    r.record_fill(Market.STOCKS, 1990.0)     # only 10 free
    assert r.position_size(Market.STOCKS, score=1.0) == 10.0


def test_position_size_scales_with_score():
    r = rm()
    full = r.position_size(Market.STOCKS, score=1.0)
    half = r.position_size(Market.STOCKS, score=0.0)
    assert half == pytest.approx(full * 0.5)
    assert r.position_size(Market.STOCKS, score=0.5) == pytest.approx(full * 0.75)


def test_position_size_clamps_absurd_scores():
    r = rm()
    assert r.position_size(Market.STOCKS, score=99) == r.position_size(Market.STOCKS, score=1.0)
    assert r.position_size(Market.STOCKS, score=-5) == r.position_size(Market.STOCKS, score=0.0)


def test_position_size_is_zero_when_the_day_is_blown():
    r = rm()
    r.record_close(Market.STOCKS, -400.0)
    assert r.position_size(Market.STOCKS, score=1.0) == 0.0


def test_snapshot_reports_the_book():
    r = rm()
    r.record_fill(Market.STOCKS, 100.0)
    snap = r.snapshot([pos(), pos(symbol="Y")])
    assert snap["open_positions"] == {"total": 2, "stocks": 2}
    assert snap["deployed"]["stocks"] == 100.0
    assert snap["daily_loss_breached"] is False
