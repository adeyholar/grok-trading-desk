"""Crypto intraday pass: allowlist → defined-risk ticket → High→Checker→Go → dry-run.

Alpaca paper BTC/USD ETH/USD only. Worker discretion informed by evidence cites
(Living Log / journal / market facts). Trade Desk reads paper account before
submit when --submit-paper. Flexible to market fluidity — no rigid time-exits.
Hard risk gates stay. Never invent fills or data. No pump.fun.
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

import yaml

from ..shared.discipline import (
    BookDisciplineState,
    DisciplineError,
    gate_high_checker_go,
)
from .executor import CryptoIntradayExecutor, OrderValidationError
from .session import EXIT_POLICY_NOTE, session_status
from .symbols import filter_crypto_symbols, normalize_crypto_symbol
from .ticket import build_crypto_intraday_ticket

log = logging.getLogger("crypto_intraday")


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def crypto_intraday_checker_stub(
    ticket: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    lane = config.get("crypto_intraday", {}) or {}
    if not ticket.get("symbol") or ticket.get("side") != "buy":
        return {"approve": False, "reason": "invalid_ticket"}
    if float(ticket.get("max_risk_usd") or 0) <= 0:
        return {"approve": False, "reason": "missing_defined_risk"}
    if not (ticket.get("evidence_cites") or ticket.get("evidence")):
        return {"approve": False, "reason": "missing_evidence_cites"}
    min_score = float(lane.get("min_checker_score", 0.0))
    if float(ticket.get("score") or 1.0) < min_score:
        return {"approve": False, "reason": "checker_score_low"}
    return {
        "approve": True,
        "reason": "stub_approve",
        "informed_discretion": True,
        "adeola_go_required": True,
        "adeola_go_enforced": False,
        "note": "Worker grade from evidence cites; flexible to live data + Living Log",
    }


def run_crypto_intraday_pass(
    config: dict[str, Any],
    *,
    symbols: list[str] | None = None,
    prices: dict[str, float] | None = None,
    dry_run: bool = True,
    submit_paper: bool = False,
    adeola_go: bool | None = None,
    require_adeola_go: bool = False,
    account_snapshot: dict[str, Any] | None = None,
    executor: CryptoIntradayExecutor | None = None,
    discipline: BookDisciplineState | None = None,
) -> list[dict[str, Any]]:
    lane = config.get("crypto_intraday", {}) or {}
    raw = symbols or list(lane.get("symbols") or ["BTC/USD", "ETH/USD"])
    filtered = filter_crypto_symbols(raw)
    status = session_status(config)
    log.info(
        "crypto intraday pass: %d symbols — %s",
        len(filtered),
        EXIT_POLICY_NOTE,
    )
    if not filtered:
        return []

    state = discipline or BookDisciplineState(
        open_count=int(lane.get("open_count", 0) or 0),
        session_losses=int(lane.get("session_losses", 0) or 0),
        max_open=int(lane.get("max_open", 2) or 2),
        loss_streak_no_go=int(lane.get("loss_streak_no_go", 2) or 2),
        book="crypto_intraday",
    )

    want_submit = bool(submit_paper) and not dry_run
    if want_submit and require_adeola_go and adeola_go is not True:
        return [
            {
                "bought": False,
                "reason": "adeola_go_required",
                "detail": "paper submit requires Adeola Go window",
                "session": status,
            }
        ]

    # Trade Desk: before submit/manage, require paper account snapshot cite
    # (caller supplies real Alpaca paper read — never invent).
    if want_submit and account_snapshot is None and not lane.get("account_snapshot"):
        return [
            {
                "bought": False,
                "reason": "account_snapshot_required",
                "detail": (
                    "Trade Desk must read Alpaca paper account/positions "
                    "before submit (never invent)"
                ),
                "session": status,
            }
        ]

    executor = executor or CryptoIntradayExecutor(
        config, dry_run=not want_submit, paper=True, discipline=state
    )

    price_map = {
        normalize_crypto_symbol(k): float(v) for k, v in (prices or {}).items()
    }
    for item in lane.get("reference_prices") or []:
        if isinstance(item, dict) and item.get("symbol"):
            price_map[normalize_crypto_symbol(item["symbol"])] = float(
                item.get("price") or 0
            )

    snap = account_snapshot or lane.get("account_snapshot")
    results: list[dict[str, Any]] = []
    max_risk = float(lane.get("max_risk_usd", 50.0))

    for symbol in filtered:
        try:
            state.assert_may_open()
        except DisciplineError as exc:
            results.append(
                {
                    "bought": False,
                    "reason": exc.reason,
                    "detail": exc.detail,
                    "symbol": symbol,
                    "session": status,
                }
            )
            break

        ref = price_map.get(symbol, 0.0)
        if ref <= 0:
            ref = float(lane.get("default_reference_price") or 0)
        if ref <= 0:
            results.append(
                {
                    "bought": False,
                    "reason": "missing_reference_price",
                    "symbol": symbol,
                    "detail": "never invent price data",
                    "session": status,
                }
            )
            continue

        cites: list[dict[str, Any]] = [
            {
                "kind": "market_fact",
                "ref": f"{symbol} ref_price={ref} (caller-supplied; not invented)",
            }
        ]
        for extra_cite in lane.get("evidence_cites") or []:
            if isinstance(extra_cite, dict):
                cites.append(extra_cite)
        if snap:
            cites.append(
                {
                    "kind": "account_snapshot",
                    "ref": str(
                        snap.get("ref")
                        or snap.get("summary")
                        or "alpaca_paper_account_read"
                    ),
                }
            )

        try:
            ticket = build_crypto_intraday_ticket(
                symbol=symbol,
                reference_price=ref,
                max_risk_usd=max_risk,
                evidence_cites=cites,
            )
        except ValueError as exc:
            results.append(
                {
                    "bought": False,
                    "reason": "ticket_build_failed",
                    "detail": str(exc),
                    "symbol": symbol,
                }
            )
            continue

        high_ok = bool(ticket.get("symbol") and ticket.get("max_risk_usd"))
        check = crypto_intraday_checker_stub(ticket, config)
        gate = gate_high_checker_go(
            high_ok=high_ok,
            checker_approve=check.get("approve"),
            adeola_go=adeola_go,
            require_adeola_go=require_adeola_go and want_submit,
        )
        if not gate.get("approve"):
            results.append(
                {
                    "bought": False,
                    "reason": gate.get("reason"),
                    "symbol": symbol,
                    "gate": gate,
                    "checker": check,
                    "ticket": ticket,
                    "order_id": "NO_GO",
                    "filled": False,
                    "session": status,
                }
            )
            continue

        try:
            fill = executor.buy(ticket, submit=want_submit, reference_price=ref)
        except OrderValidationError as exc:
            results.append(
                {
                    "bought": False,
                    "reason": exc.reason,
                    "detail": exc.detail,
                    "symbol": symbol,
                    "ticket": ticket,
                }
            )
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("crypto intraday submit failed for %s", symbol)
            results.append(
                {
                    "bought": False,
                    "reason": "submit_failed",
                    "detail": str(exc),
                    "symbol": symbol,
                }
            )
            continue

        results.append(
            {
                "bought": True,
                "dry_run": fill.get("dry_run", True),
                "submitted": fill.get("submitted", False),
                "filled": False,
                "order_id": fill.get("order_id"),
                "symbol": symbol,
                "mode": "intraday",
                "discretionary": True,
                "discretion_owner": "workers",
                "informed_discretion": True,
                "exit_policy": EXIT_POLICY_NOTE,
                "gate": gate,
                "checker": check,
                "session": status,
                "fill": fill,
            }
        )
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Phase 3 crypto INTRADAY paper dry-run — Alpaca BTC/USD ETH/USD, "
            "informed worker discretion + defined risk. " + EXIT_POLICY_NOTE
        )
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--submit-paper", action="store_true")
    parser.add_argument("--adeola-go", action="store_true")
    parser.add_argument("--symbols", default="BTC/USD,ETH/USD")
    parser.add_argument("--price", type=float, default=0.0, help="ref price for all")
    parser.add_argument("--btc-price", type=float, default=0.0)
    parser.add_argument("--eth-price", type=float, default=0.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    symbols = filter_crypto_symbols(
        [s for s in args.symbols.split(",") if s.strip()]
    )
    prices: dict[str, float] = {}
    if args.price > 0:
        prices = {s: float(args.price) for s in symbols}
    if args.btc_price > 0:
        prices["BTC/USD"] = float(args.btc_price)
    if args.eth_price > 0:
        prices["ETH/USD"] = float(args.eth_price)

    dry_run = not args.submit_paper
    results = run_crypto_intraday_pass(
        config,
        symbols=symbols,
        prices=prices or None,
        dry_run=dry_run,
        submit_paper=args.submit_paper,
        adeola_go=True if args.adeola_go else None,
        require_adeola_go=bool(args.submit_paper),
    )
    bought = sum(1 for r in results if r.get("bought"))
    log.info(
        "crypto intraday done: %d results, %d bought/dry-run",
        len(results),
        bought,
    )
    log.info("Living Log: review after session; never invent fills from dry-run.")


if __name__ == "__main__":
    main()
