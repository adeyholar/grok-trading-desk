"""Stock intraday pass: universe → defined-risk ticket → High→Checker→Go → dry-run.

Worker discretion (Scanner/Context/Checker/Trade Desk) inside Adeola Go window.
Adeola locks: Go / No-Go / risk-caps / overnight hold. Hard discipline: defined
risk, max 1–2 opens, No-Go after 2 losses, paper-only, never invent fills.
No autopilot time-exits. Living Log after review (operator process).
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

import yaml

from ...shared.discipline import (
    BookDisciplineState,
    DisciplineError,
    gate_high_checker_go,
)
from .executor import OrderValidationError, StockIntradayExecutor
from .session import EXIT_POLICY_NOTE, session_status
from .ticket import build_stock_intraday_ticket
from .universe import filter_intraday_universe, normalize_symbol

log = logging.getLogger("stock_intraday")


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def stock_intraday_checker_stub(
    ticket: dict[str, Any], config: dict[str, Any]
) -> dict[str, Any]:
    """Fail-closed checker stub: approve only well-formed defined-risk tickets."""
    lane = config.get("stock_intraday", {}) or {}
    if not ticket.get("symbol") or ticket.get("side") != "buy":
        return {"approve": False, "reason": "invalid_ticket"}
    if not ticket.get("stop_price") and ticket.get("stop_pct") is None:
        return {"approve": False, "reason": "missing_stop"}
    if float(ticket.get("max_risk_usd") or 0) <= 0:
        return {"approve": False, "reason": "missing_defined_risk"}
    min_score = float(lane.get("min_checker_score", 0.0))
    score = float(ticket.get("score") or 1.0)
    if score < min_score:
        return {"approve": False, "reason": "checker_score_low"}
    return {
        "approve": True,
        "reason": "stub_approve",
        "adeola_go_required": True,
        "adeola_go_enforced": False,
        "note": (
            "Worker checker grade informed by evidence cites; Adeola locks "
            "Go/No-Go/risk-caps/overnight only — flexible to market fluidity"
        ),
        "informed_discretion": True,
    }


def run_stock_intraday_pass(
    config: dict[str, Any],
    *,
    symbols: list[str] | None = None,
    prices: dict[str, float] | None = None,
    dry_run: bool = True,
    submit_paper: bool = False,
    adeola_go: bool | None = None,
    require_adeola_go: bool = False,
    executor: StockIntradayExecutor | None = None,
    discipline: BookDisciplineState | None = None,
) -> list[dict[str, Any]]:
    lane = config.get("stock_intraday", {}) or {}
    raw_symbols = symbols or list(lane.get("symbols") or [])
    filtered = filter_intraday_universe(
        raw_symbols,
        extra=lane.get("extra_symbols") or [],
    )
    status = session_status(config=config)
    log.info(
        "stock intraday pass: %d symbols (worker discretion + discipline) — %s",
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
        book="stock_intraday",
    )

    want_submit = bool(submit_paper) and not dry_run
    if want_submit and require_adeola_go and adeola_go is not True:
        log.info("stock intraday: paper submit blocked — Adeola Go window not open")
        return [
            {
                "bought": False,
                "reason": "adeola_go_required",
                "detail": "paper submit requires Adeola Go window",
                "session": status,
                "discretionary": True,
                "discretion_owner": "workers",
            }
        ]

    executor = executor or StockIntradayExecutor(
        config,
        dry_run=not want_submit,
        paper=True,
        discipline=state,
    )

    price_map = {normalize_symbol(k): float(v) for k, v in (prices or {}).items()}
    for item in lane.get("reference_prices") or []:
        if isinstance(item, dict) and item.get("symbol"):
            price_map[normalize_symbol(item["symbol"])] = float(item.get("price") or 0)

    results: list[dict[str, Any]] = []
    max_risk = float(lane.get("max_risk_usd", 100.0))

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
                    "discretionary": True,
                    "discretion_owner": "workers",
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
                    "session": status,
                }
            )
            continue

        # Informed discretion: cite market fact + optional lesson/journal.
        # Never invent prices — use caller-supplied reference only.
        cites = [
            {
                "kind": "market_fact",
                "ref": f"{symbol} ref_price={ref} (caller-supplied; not invented)",
            }
        ]
        for extra_cite in lane.get("evidence_cites") or []:
            if isinstance(extra_cite, dict):
                cites.append(extra_cite)
        ticket = build_stock_intraday_ticket(
            symbol=symbol,
            reference_price=ref,
            max_risk_usd=max_risk,
            limit_slippage=executor.limit_slippage,
            evidence_cites=cites,
        )

        high_ok = bool(ticket.get("symbol") and ticket.get("max_risk_usd"))
        check = stock_intraday_checker_stub(ticket, config)
        gate = gate_high_checker_go(
            high_ok=high_ok,
            checker_approve=check.get("approve"),
            adeola_go=adeola_go,
            require_adeola_go=require_adeola_go and want_submit,
        )
        if not gate.get("approve"):
            log.info("stock intraday No-Go %s: %s", symbol, gate.get("reason"))
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
                    "discretionary": True,
                    "discretion_owner": "workers",
                }
            )
            continue

        try:
            fill = executor.buy(ticket, submit=want_submit, reference_price=ref)
        except OrderValidationError as exc:
            log.warning("stock intraday validation refused %s: %s", symbol, exc)
            results.append(
                {
                    "bought": False,
                    "reason": exc.reason,
                    "detail": exc.detail,
                    "symbol": symbol,
                    "ticket": ticket,
                    "session": status,
                }
            )
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("stock intraday submit failed for %s", symbol)
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
                "limit_price": fill.get("limit_price"),
                "stop_price": fill.get("stop_price"),
                "target_price": fill.get("target_price"),
                "qty": fill.get("qty"),
                "mode": "intraday",
                "discretionary": True,
                "discretion_owner": "workers",
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
            "Phase 1 stock INTRADAY paper dry-run — worker discretion + defined "
            "risk + High→Checker→Go. " + EXIT_POLICY_NOTE
        )
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument(
        "--submit-paper",
        action="store_true",
        help="POST to Alpaca paper (refuses live). Requires Adeola Go window.",
    )
    parser.add_argument(
        "--adeola-go",
        action="store_true",
        help="Open Adeola Go window for paper submit (not hold/exit micromanagement).",
    )
    parser.add_argument("--symbols", default="")
    parser.add_argument("--price", type=float, default=0.0)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    symbols = None
    if args.symbols.strip():
        symbols = [
            normalize_symbol(sym) for sym in args.symbols.split(",") if sym.strip()
        ]
    prices = None
    if args.price and args.price > 0 and symbols:
        prices = {sym: float(args.price) for sym in symbols}

    dry_run = not args.submit_paper
    log.info("session: %s", session_status(config=config)["note"])
    results = run_stock_intraday_pass(
        config,
        symbols=symbols,
        prices=prices,
        dry_run=dry_run,
        submit_paper=args.submit_paper,
        adeola_go=True if args.adeola_go else None,
        require_adeola_go=bool(args.submit_paper),
    )
    bought = sum(1 for r in results if r.get("bought"))
    log.info(
        "stock intraday pass done: %d results, %d bought/dry-run (submit_paper=%s)",
        len(results),
        bought,
        args.submit_paper,
    )
    log.info("Living Log: review after session; never invent fills from dry-run.")


if __name__ == "__main__":
    main()
