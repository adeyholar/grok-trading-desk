"""Unit tests for Phase 1 options validators + dry-run (no network)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from src.models import Stock
from src.options.contracts import (
    PAPER_BASE_URL,
    OptionsContractsClient,
    assert_paper_base_url,
    resolve_paper_credentials,
)
from src.options.options_executor import (
    MAX_LEVEL,
    OptionsExecutor,
    OrderValidationError,
    validate_option_order,
)
from src.options.options_loop import options_checker_stub, run_options_pass
from src.options.overlay import OptionCandidate, OptionsOverlay, pick_liquid_candidates


def _good_order(**over: Any) -> dict[str, Any]:
    base = {
        "symbol": "AAPL250418C00200000",
        "side": "buy",
        "option_type": "call",
        "qty": 1,
        "order_class": "simple",
        "legs": 1,
        "level": 2,
        "limit_price": 1.25,
        "estimated_risk_usd": 125.0,
        "max_risk_usd": 250.0,
        "intent": "buy call",
    }
    base.update(over)
    return base


# -- credentials / paper URL -------------------------------------------------------


def test_resolve_credentials_prefer_env(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "env-key")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "env-secret")
    key, secret = resolve_paper_credentials(
        {"alpaca": {"api_key": "yaml-key", "api_secret": "yaml-secret", "paper": True}}
    )
    assert key == "env-key"
    assert secret == "env-secret"


def test_resolve_credentials_fallback_yaml(monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    key, secret = resolve_paper_credentials(
        {"alpaca": {"api_key": "yaml-key", "api_secret": "yaml-secret"}}
    )
    assert key == "yaml-key"
    assert secret == "yaml-secret"


def test_assert_paper_base_url_accepts_paper():
    assert assert_paper_base_url(PAPER_BASE_URL) == PAPER_BASE_URL.rstrip("/")
    assert assert_paper_base_url("") == PAPER_BASE_URL


def test_assert_paper_base_url_rejects_live():
    with pytest.raises(ValueError, match="non-paper|live"):
        assert_paper_base_url("https://api.alpaca.markets")
    with pytest.raises(ValueError):
        assert_paper_base_url("https://example.com")


def test_contracts_client_refuses_live_host():
    with pytest.raises(ValueError):
        OptionsContractsClient(base_url="https://api.alpaca.markets")


# -- validators --------------------------------------------------------------------


def test_validate_buy_call_and_buy_put_ok():
    validate_option_order(_good_order())
    validate_option_order(
        _good_order(option_type="put", intent="buy put", symbol="AAPL250418P00200000")
    )


@pytest.mark.parametrize(
    "over,reason",
    [
        ({"side": "sell", "intent": "sell put"}, "side_not_allowed"),
        ({"option_type": "straddle", "intent": "buy straddle"}, "option_type_not_allowed"),
        ({"legs": 2}, "mleg_rejected"),
        ({"legs": [{"symbol": "A"}, {"symbol": "B"}]}, "mleg_rejected"),
        ({"order_class": "mleg"}, "mleg_rejected"),
        ({"order_class": "bracket"}, "mleg_rejected"),
        ({"level": 3}, "level_exceeded"),
        ({"level": 0}, "level_exceeded"),
        ({"symbol": ""}, "missing_symbol"),
        ({"qty": 0}, "invalid_qty"),
        ({"estimated_risk_usd": 999, "max_risk_usd": 100}, "risk_exceeded"),
        ({"position_intent": "sell_to_open", "side": "sell"}, "side_not_allowed"),
    ],
)
def test_validate_rejects(over, reason):
    with pytest.raises(OrderValidationError) as excinfo:
        validate_option_order(_good_order(**over))
    assert excinfo.value.reason == reason


def test_max_level_constant_is_two():
    assert MAX_LEVEL == 2


# -- overlay (offline) -------------------------------------------------------------


def _contract(
    underlying: str,
    otype: str,
    strike: float,
    dte: int,
    *,
    oi: float = 500,
    premium: float = 2.5,
    as_of: date | None = None,
) -> dict[str, Any]:
    today = as_of or date(2026, 9, 27)
    exp = today + timedelta(days=dte)
    letter = "C" if otype == "call" else "P"
    return {
        "symbol": f"{underlying}{exp.strftime('%y%m%d')}{letter}{int(strike * 1000):08d}",
        "underlying_symbol": underlying,
        "type": otype,
        "strike_price": str(strike),
        "expiration_date": exp.isoformat(),
        "open_interest": str(oi),
        "close_price": str(premium),
        "status": "active",
        "tradable": True,
    }


def test_pick_liquid_candidates_prefers_2_to_6w_dte():
    as_of = date(2026, 9, 27)
    underlyings = [Stock(symbol="AAPL", price=200.0)]
    contracts = [
        _contract("AAPL", "call", 205, 7, as_of=as_of),   # too short
        _contract("AAPL", "call", 204, 28, as_of=as_of, oi=2000, premium=3.0),
        _contract("AAPL", "put", 196, 28, as_of=as_of, oi=1500, premium=2.8),
        _contract("AAPL", "call", 210, 90, as_of=as_of),  # too long
    ]
    picks = pick_liquid_candidates(
        underlyings, contracts, dte_min=14, dte_max=42, as_of=as_of
    )
    assert len(picks) == 2
    types = {p.option_type for p in picks}
    assert types == {"call", "put"}
    assert all(14 <= p.dte <= 42 for p in picks)
    assert all(p.side == "buy" for p in picks)


def test_pick_liquid_candidates_empty_when_illiquid():
    as_of = date(2026, 9, 27)
    picks = pick_liquid_candidates(
        [Stock(symbol="ZZZ", price=10.0)],
        [_contract("ZZZ", "call", 10, 21, oi=0, premium=0, as_of=as_of)],
        min_open_interest=10,
        as_of=as_of,
    )
    assert picks == []


# -- dry-run executor (no network) -------------------------------------------------


class _BoomClient:
    """Any real HTTP would fail the test — dry-run must never touch it."""

    def post(self, *args, **kwargs):
        raise AssertionError("dry-run must not POST")

    def get(self, *args, **kwargs):
        raise AssertionError("dry-run must not GET")


def test_executor_dry_run_logs_without_post():
    ex = OptionsExecutor(
        {"options": {"dry_run": True, "max_risk_usd": 250, "base_url": PAPER_BASE_URL}},
        dry_run=True,
        paper=True,
        client=_BoomClient(),  # type: ignore[arg-type]
    )
    result = ex.buy(_good_order())
    assert result["submitted"] is False
    assert result["dry_run"] is True
    assert result["order_id"] == "DRY_RUN"
    assert len(ex.dry_run_log) == 1
    assert ex.submitted == []


def test_executor_refuses_non_paper_even_if_submit_requested():
    with pytest.raises(ValueError):
        OptionsExecutor(dry_run=False, paper=True, base_url="https://api.alpaca.markets")


def test_executor_submit_path_blocked_when_paper_false():
    ex = OptionsExecutor(
        {"alpaca": {"api_key": "k", "api_secret": "s"}, "options": {"dry_run": False}},
        dry_run=False,
        paper=False,
        base_url=PAPER_BASE_URL,
        client=_BoomClient(),  # type: ignore[arg-type]
    )
    # paper=False should fail closed before POST
    with pytest.raises(OrderValidationError) as excinfo:
        # Force submit bypassing dry_run flag via submit=True and dry_run False
        ex.dry_run = False
        ex.buy(_good_order(), submit=True)
    assert excinfo.value.reason == "non_paper_refused"


def test_executor_rejects_mleg_before_any_network():
    ex = OptionsExecutor(dry_run=True, paper=True, client=_BoomClient())  # type: ignore[arg-type]
    with pytest.raises(OrderValidationError) as excinfo:
        ex.buy(_good_order(legs=[{"a": 1}, {"b": 2}]))
    assert excinfo.value.reason == "mleg_rejected"


# -- options pass wiring -----------------------------------------------------------


@pytest.mark.asyncio
async def test_run_options_pass_dry_run_with_stub_overlay():
    as_of = date(2026, 9, 27)
    underlyings = [Stock(symbol="AAPL", price=200.0)]
    contracts = [
        _contract("AAPL", "call", 204, 28, as_of=as_of, oi=2000),
        _contract("AAPL", "put", 196, 28, as_of=as_of, oi=1500),
    ]

    class FakeOverlay(OptionsOverlay):
        def run(self, underlyings, *, fetch=None, as_of=None):  # noqa: ARG002
            return pick_liquid_candidates(
                underlyings, contracts, dte_min=14, dte_max=42, as_of=date(2026, 9, 27)
            )

    config = {
        "options": {
            "dry_run": True,
            "max_risk_usd": 250,
            "base_url": PAPER_BASE_URL,
            "max_level": 2,
        },
        "alpaca": {"api_key": "k", "api_secret": "s", "paper": True},
    }
    ex = OptionsExecutor(config, dry_run=True, paper=True, client=_BoomClient())  # type: ignore[arg-type]
    results = await run_options_pass(
        config,
        underlyings=underlyings,
        dry_run=True,
        submit_paper=False,
        executor=ex,
        overlay=FakeOverlay(config),
    )
    assert results
    assert all(r.get("bought") for r in results)
    assert all(r.get("dry_run") for r in results)
    assert all(r["fill"]["order_id"] == "DRY_RUN" for r in results)


def test_checker_stub_documents_adeola_go():
    cand = OptionCandidate(
        underlying="AAPL",
        symbol="AAPL250418C00200000",
        option_type="call",
        side="buy",
        score=0.5,
    )
    check = options_checker_stub(cand, {"options": {}})
    assert check["approve"] is True
    assert check["adeola_go_required"] is True
    assert check["adeola_go_enforced"] is False


def test_candidate_to_order_is_level2_buy():
    cand = OptionCandidate(
        underlying="AAPL",
        symbol="AAPL250418C00200000",
        option_type="call",
        side="buy",
        premium=1.5,
        strike=200,
        expiration="2026-04-18",
        dte=28,
    )
    order = cand.to_order(qty=1, max_risk_usd=250)
    validate_option_order(order)
    assert order["intent"] == "buy call"
    assert order["level"] == 2
