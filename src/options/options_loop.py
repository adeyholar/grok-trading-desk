"""Options paper pass: screener survivors → overlay → checker stub → dry-run.

Adeola Go is a product-side gate (not enforced in code for Phase 1). Dry-run is
the default; real paper POSTs require --submit-paper AND the paper host.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any

import yaml

from ..models import Stock
from ..stocks.screener import Screener
from .options_executor import OptionsExecutor, OrderValidationError
from .overlay import OptionCandidate, OptionsOverlay

log = logging.getLogger("options_loop")


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def options_checker_stub(candidate: OptionCandidate, config: dict[str, Any]) -> dict[str, Any]:
    """Phase-1 stub: approve liquid Level-2 candidates; fail-closed on empties.

    Product gate "Adeola Go" is documented, not required in code yet.
    """
    opts = config.get("options", {}) or {}
    if not candidate.symbol or candidate.option_type not in {"call", "put"}:
        return {"approve": False, "reason": "invalid_candidate"}
    if candidate.side != "buy":
        return {"approve": False, "reason": "side_not_buy"}
    min_score = float(opts.get("min_overlay_score", 0.0))
    if candidate.score < min_score:
        return {"approve": False, "reason": "overlay_score_low"}
    # Documented product gate — logged, not blocking Phase 1 dry-run.
    return {
        "approve": True,
        "reason": "stub_approve",
        "adeola_go_required": True,
        "adeola_go_enforced": False,
        "note": "Adeola Go is product-side; Phase 1 dry-run does not require it",
    }


async def run_options_pass(
    config: dict[str, Any],
    *,
    underlyings: list[Stock] | None = None,
    dry_run: bool = True,
    submit_paper: bool = False,
    executor: OptionsExecutor | None = None,
    overlay: OptionsOverlay | None = None,
    screener: Screener | None = None,
) -> list[dict[str, Any]]:
    """One options desk pass. Fail-closed: validation errors become skips."""
    if underlyings is None:
        screener = screener or Screener(config)
        underlyings = await screener.run()
        log.info("options pass: %d screener survivors", len(underlyings))

    if not underlyings:
        log.info("options pass: no underlyings — nothing to do")
        return []

    overlay = overlay or OptionsOverlay(config)
    # When underlyings are injected (tests), overlay.fetch can be stubbed by
    # monkeypatching client; for unit tests callers pass a custom overlay.
    candidates = overlay.run(underlyings)
    log.info("options pass: %d overlay candidates", len(candidates))

    want_submit = bool(submit_paper) and not dry_run
    executor = executor or OptionsExecutor(
        config,
        dry_run=not want_submit,
        paper=True,
    )

    results: list[dict[str, Any]] = []
    for candidate in candidates:
        check = options_checker_stub(candidate, config)
        if not check.get("approve"):
            log.info(
                "options skip %s: checker %s",
                candidate.symbol,
                check.get("reason"),
            )
            results.append(
                {
                    "bought": False,
                    "reason": check.get("reason"),
                    "symbol": candidate.symbol,
                    "checker": check,
                }
            )
            continue

        order = candidate.to_order(
            qty=1,
            max_risk_usd=float((config.get("options") or {}).get("max_risk_usd", 250)),
        )
        try:
            fill = executor.buy(order, submit=want_submit)
        except OrderValidationError as exc:
            log.warning("options validation refused %s: %s", candidate.symbol, exc)
            results.append(
                {
                    "bought": False,
                    "reason": exc.reason,
                    "detail": exc.detail,
                    "symbol": candidate.symbol,
                }
            )
            continue
        except Exception as exc:  # noqa: BLE001
            log.exception("options submit failed for %s", candidate.symbol)
            results.append(
                {
                    "bought": False,
                    "reason": "submit_failed",
                    "detail": str(exc),
                    "symbol": candidate.symbol,
                }
            )
            continue

        results.append(
            {
                "bought": True,
                "dry_run": fill.get("dry_run", True),
                "submitted": fill.get("submitted", False),
                "order_id": fill.get("order_id"),
                "symbol": candidate.symbol,
                "intent": fill.get("intent"),
                "checker": check,
                "fill": fill,
            }
        )
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Phase 1 options paper dry-run (Level 2 long call/put)"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="log orders only (default)",
    )
    parser.add_argument(
        "--submit-paper",
        action="store_true",
        help="POST to Alpaca paper host (still refuses live). Implies not dry-run.",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="comma-separated underlyings to skip the screener (e.g. AAPL,MSFT)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config(args.config)
    underlyings: list[Stock] | None = None
    if args.symbols.strip():
        underlyings = [
            Stock(symbol=sym.strip().upper(), price=0.0)
            for sym in args.symbols.split(",")
            if sym.strip()
        ]

    dry_run = not args.submit_paper
    results = asyncio.run(
        run_options_pass(
            config,
            underlyings=underlyings,
            dry_run=dry_run,
            submit_paper=args.submit_paper,
        )
    )
    bought = sum(1 for r in results if r.get("bought"))
    log.info(
        "options pass done: %d results, %d bought/dry-run (submit_paper=%s)",
        len(results),
        bought,
        args.submit_paper,
    )


if __name__ == "__main__":
    main()
