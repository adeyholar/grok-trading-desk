"""Sound discipline gates shared by Stock / Crypto / Options Intraday books.

DISCRETIONARY = worker judgment (Scanner / Context / Checker / Trade Desk),
NOT Adeola micromanaging every hold/exit.

Adeola locks only: Go window / No-Go / risk-cap changes / overnight hold OK.

Workers (inside active Go + discipline): propose / grade / veto / ticket timing;
take-profit when profitable same session by judgment; cut losers on
stop/invalidation by judgment; ask Adeola only for overnight hold or risk-cap changes.

SOUND DISCIPLINE (hard in code):
  * Defined risk on every ticket (max_risk_usd + stop).
  * Gate path: High → Checker → Go. Missing/failed checker = No-Go.
  * Max 1–2 open positions per book.
  * Force No-Go after 2 session losses.
  * Paper host only; dry-run default; never invent fills.
  * No autopilot time-exits / no forced bell flatten.
  * Worker discretion is INFORMED: High / approve / ticket / exit must cite
    evidence (Living Log Lesson, reviewed journal, market facts). Never invent data.
  * Trade Desk reads Alpaca paper account/positions before submit or manage.

Living Log after review is an operator process — callers may append events;
this module does not fabricate fills or PnL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_MAX_OPEN_PER_BOOK = 2
DEFAULT_LOSS_STREAK_NO_GO = 2


class DisciplineError(ValueError):
    """Hard discipline refusal — ticket must not proceed."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass
class BookDisciplineState:
    """Per-book open count + session loss streak (injected by caller / tests)."""

    open_count: int = 0
    session_losses: int = 0
    max_open: int = DEFAULT_MAX_OPEN_PER_BOOK
    loss_streak_no_go: int = DEFAULT_LOSS_STREAK_NO_GO
    book: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def assert_may_open(self) -> None:
        if self.session_losses >= self.loss_streak_no_go:
            raise DisciplineError(
                "no_go_after_losses",
                f"{self.book or 'book'}: {self.session_losses} losses "
                f">= {self.loss_streak_no_go} → force No-Go",
            )
        if self.open_count >= self.max_open:
            raise DisciplineError(
                "max_open_exceeded",
                f"{self.book or 'book'}: open={self.open_count} max={self.max_open}",
            )


def require_defined_risk(ticket: dict[str, Any]) -> None:
    """Every ticket must carry defined risk: max_risk_usd > 0 and a stop."""
    if not isinstance(ticket, dict):
        raise DisciplineError("invalid_ticket", "ticket must be a dict")
    try:
        max_risk = float(ticket.get("max_risk_usd") or 0)
    except (TypeError, ValueError) as exc:
        raise DisciplineError("invalid_max_risk_usd", str(exc)) from exc
    if max_risk <= 0:
        raise DisciplineError("missing_defined_risk", "max_risk_usd required")

    stop = ticket.get("stop_price")
    stop_pct = ticket.get("stop_pct")
    if stop is None and stop_pct is None:
        raise DisciplineError(
            "missing_stop",
            "stop_price or stop_pct required for defined risk",
        )
    if stop is not None:
        try:
            if float(stop) <= 0:
                raise DisciplineError("invalid_stop_price", str(stop))
        except (TypeError, ValueError) as exc:
            raise DisciplineError("invalid_stop_price", str(exc)) from exc
    if stop_pct is not None:
        try:
            sp = float(stop_pct)
        except (TypeError, ValueError) as exc:
            raise DisciplineError("invalid_stop_pct", str(exc)) from exc
        if sp <= 0 or sp >= 1:
            raise DisciplineError("invalid_stop_pct", str(stop_pct))


def gate_high_checker_go(
    *,
    high_ok: bool,
    checker_approve: bool | None,
    adeola_go: bool | None = None,
    require_adeola_go: bool = False,
) -> dict[str, Any]:
    """Fail-closed High → Checker → Go.

    checker_approve None → fail closed (No-Go).
    Adeola Go is product-side; when require_adeola_go and not True → No-Go.
    """
    if not high_ok:
        return {
            "approve": False,
            "reason": "high_gate_failed",
            "gate": "high",
            "adeola_go_required": True,
            "adeola_go": adeola_go,
        }
    if checker_approve is None:
        return {
            "approve": False,
            "reason": "checker_fail_closed",
            "gate": "checker",
            "adeola_go_required": True,
            "adeola_go": adeola_go,
            "note": "Checkers fail-closed: missing verdict is No-Go",
        }
    if not checker_approve:
        return {
            "approve": False,
            "reason": "checker_rejected",
            "gate": "checker",
            "adeola_go_required": True,
            "adeola_go": adeola_go,
        }
    if require_adeola_go and adeola_go is not True:
        return {
            "approve": False,
            "reason": "adeola_go_required",
            "gate": "go",
            "adeola_go_required": True,
            "adeola_go": adeola_go,
            "adeola_go_enforced": True,
            "note": "Adeola Go required for live paper path; dry-run may document only",
        }
    return {
        "approve": True,
        "reason": "gates_clear",
        "gate": "go",
        "adeola_go_required": True,
        "adeola_go": adeola_go,
        "adeola_go_enforced": bool(require_adeola_go),
        "note": "Worker discretion inside Adeola Go window; Adeola locks Go/No-Go/risk-caps/overnight only",
    }


def assert_never_invent_fills(result: dict[str, Any]) -> None:
    """Dry-run / refused paths must not look like real broker fills."""
    if not result.get("submitted") and result.get("order_id") not in {
        None,
        "",
        "DRY_RUN",
        "NO_GO",
    }:
        raise DisciplineError(
            "invented_fill",
            f"non-submit result has order_id={result.get('order_id')!r}",
        )
    if result.get("dry_run") and result.get("filled") is True:
        raise DisciplineError("invented_fill", "dry_run result marked filled=True")


# -- Informed discretion (evidence cites) ----------------------------------------

EVIDENCE_KINDS = frozenset(
    {
        "living_log_lesson",
        "reviewed_journal",
        "market_fact",
        "account_snapshot",  # Alpaca paper account/positions read by Trade Desk
    }
)


def require_evidence_cites(
    payload: dict[str, Any],
    *,
    min_cites: int = 1,
    context: str = "ticket",
) -> None:
    """Fail-closed: High/approve/ticket/exit must carry non-empty evidence cites.

    Each cite is a dict with at least {kind, ref} where kind ∈ EVIDENCE_KINDS
    and ref is a non-empty string (lesson id, journal entry, or market fact note).
    Never invent fills or market data — cites document what the worker actually read.
    """
    if not isinstance(payload, dict):
        raise DisciplineError("invalid_payload", f"{context} must be a dict")
    cites = payload.get("evidence_cites") or payload.get("evidence") or []
    if not isinstance(cites, list) or len(cites) < min_cites:
        raise DisciplineError(
            "missing_evidence_cites",
            f"{context}: need >= {min_cites} evidence cite(s) "
            "(living_log_lesson / reviewed_journal / market_fact / account_snapshot)",
        )
    for i, cite in enumerate(cites):
        if not isinstance(cite, dict):
            raise DisciplineError("invalid_evidence_cite", f"{context}[{i}] not a dict")
        kind = str(cite.get("kind") or "").strip().lower()
        ref = str(cite.get("ref") or cite.get("source") or "").strip()
        if kind not in EVIDENCE_KINDS:
            raise DisciplineError(
                "invalid_evidence_kind",
                f"{context}[{i}].kind={kind!r} not in {sorted(EVIDENCE_KINDS)}",
            )
        if not ref:
            raise DisciplineError(
                "empty_evidence_ref",
                f"{context}[{i}] missing ref (never invent data)",
            )


def account_read_required(ticket: dict[str, Any]) -> bool:
    """True when Trade Desk must have an account_snapshot cite before submit/manage."""
    cites = ticket.get("evidence_cites") or ticket.get("evidence") or []
    if not isinstance(cites, list):
        return True
    return not any(
        isinstance(c, dict)
        and str(c.get("kind") or "").lower() == "account_snapshot"
        for c in cites
    )
