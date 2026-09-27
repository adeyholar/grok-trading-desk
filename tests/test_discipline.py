"""Shared discipline + evidence cite tests."""

from __future__ import annotations

import pytest

from src.shared.discipline import (
    DisciplineError,
    assert_never_invent_fills,
    require_defined_risk,
    require_evidence_cites,
)


def test_require_defined_risk():
    require_defined_risk({"max_risk_usd": 50, "stop_pct": 0.01})
    with pytest.raises(DisciplineError) as ei:
        require_defined_risk({"max_risk_usd": 50})
    assert ei.value.reason == "missing_stop"


def test_evidence_cites():
    require_evidence_cites(
        {"evidence_cites": [{"kind": "market_fact", "ref": "SPY>500"}]},
        min_cites=1,
    )
    with pytest.raises(DisciplineError) as ei:
        require_evidence_cites({"evidence_cites": []})
    assert ei.value.reason == "missing_evidence_cites"
    with pytest.raises(DisciplineError):
        require_evidence_cites(
            {"evidence_cites": [{"kind": "vibes", "ref": "gut"}]}
        )


def test_never_invent_fills():
    assert_never_invent_fills({"submitted": False, "order_id": "DRY_RUN", "dry_run": True})
    with pytest.raises(DisciplineError):
        assert_never_invent_fills(
            {"submitted": False, "order_id": "fake-uuid", "dry_run": True}
        )
    with pytest.raises(DisciplineError):
        assert_never_invent_fills(
            {"submitted": False, "order_id": "DRY_RUN", "dry_run": True, "filled": True}
        )
