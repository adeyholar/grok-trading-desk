"""Unit tests for Crypto Intraday — Alpaca paper BTC/USD ETH/USD, dry-run."""

from __future__ import annotations

from typing import Any

import pytest

from src.crypto_intraday.executor import (
    CryptoIntradayExecutor,
    OrderValidationError,
    validate_crypto_intraday_order,
)
from src.crypto_intraday.loop import run_crypto_intraday_pass
from src.crypto_intraday.symbols import (
    ALLOWED_CRYPTO_SYMBOLS,
    filter_crypto_symbols,
    is_allowed_crypto_symbol,
    normalize_crypto_symbol,
)
from src.crypto_intraday.ticket import build_crypto_intraday_ticket
from src.shared.alpaca_paper import PAPER_BASE_URL
from src.shared.discipline import BookDisciplineState, DisciplineError


class _BoomClient:
    def post(self, *args, **kwargs):
        raise AssertionError("dry-run must not POST")


def _cites() -> list[dict[str, Any]]:
    return [
        {"kind": "market_fact", "ref": "BTC/USD ref from test fixture"},
        {"kind": "living_log_lesson", "ref": "lesson-crypto-001 size small"},
    ]


def test_allowlist_fail_closed():
    assert is_allowed_crypto_symbol("BTC/USD")
    assert is_allowed_crypto_symbol("ETHUSD")
    assert normalize_crypto_symbol("btc") == "BTC/USD"
    assert not is_allowed_crypto_symbol("SOL/USD")
    assert not is_allowed_crypto_symbol("BONK")
    assert filter_crypto_symbols(["BTC", "DOGE", "ETH/USD"]) == ["BTC/USD", "ETH/USD"]
    assert ALLOWED_CRYPTO_SYMBOLS == frozenset({"BTC/USD", "ETH/USD"})


def test_ticket_defined_risk_and_evidence():
    t = build_crypto_intraday_ticket(
        symbol="BTC/USD",
        reference_price=60000.0,
        max_risk_usd=50.0,
        evidence_cites=_cites(),
    )
    assert t["symbol"] == "BTC/USD"
    assert t["stop_price"] < 60000
    assert t["informed_discretion"] is True
    validate_crypto_intraday_order(t)


def test_rejects_pump_symbol():
    with pytest.raises(ValueError):
        build_crypto_intraday_ticket(
            symbol="PUMP/USD",
            reference_price=1.0,
            evidence_cites=_cites(),
        )


def test_executor_dry_run():
    ex = CryptoIntradayExecutor(
        {"crypto_intraday": {"dry_run": True}},
        dry_run=True,
        paper=True,
        client=_BoomClient(),  # type: ignore[arg-type]
    )
    t = build_crypto_intraday_ticket(
        symbol="ETH/USD",
        reference_price=3000.0,
        evidence_cites=_cites(),
    )
    result = ex.buy(t)
    assert result["order_id"] == "DRY_RUN"
    assert result["submitted"] is False
    assert result["filled"] is False


def test_executor_refuses_live_host():
    with pytest.raises(ValueError):
        CryptoIntradayExecutor(base_url="https://api.alpaca.markets")


def test_no_go_after_losses():
    state = BookDisciplineState(session_losses=2, book="crypto_intraday")
    with pytest.raises(DisciplineError) as ei:
        state.assert_may_open()
    assert ei.value.reason == "no_go_after_losses"


def test_run_pass_dry_run():
    config = {
        "crypto_intraday": {
            "dry_run": True,
            "max_risk_usd": 50,
            "evidence_cites": [
                {"kind": "reviewed_journal", "ref": "j-crypto-1"}
            ],
        }
    }
    ex = CryptoIntradayExecutor(
        config, dry_run=True, client=_BoomClient()  # type: ignore[arg-type]
    )
    results = run_crypto_intraday_pass(
        config,
        symbols=["BTC/USD"],
        prices={"BTC/USD": 60000.0},
        dry_run=True,
        executor=ex,
    )
    assert len(results) == 1
    assert results[0]["bought"] is True
    assert results[0]["order_id"] == "DRY_RUN"
    assert results[0]["filled"] is False


def test_submit_requires_account_snapshot():
    config = {"crypto_intraday": {"dry_run": False, "max_risk_usd": 50}}
    results = run_crypto_intraday_pass(
        config,
        symbols=["BTC/USD"],
        prices={"BTC/USD": 60000.0},
        dry_run=False,
        submit_paper=True,
        adeola_go=True,
        require_adeola_go=True,
        account_snapshot=None,
    )
    assert results[0]["bought"] is False
    assert results[0]["reason"] == "account_snapshot_required"
