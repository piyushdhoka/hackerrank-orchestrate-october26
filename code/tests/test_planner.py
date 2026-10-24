"""Planner rules that the end-to-end tests only hit incidentally."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.audit.planner import plan_case  # noqa: E402
from agent.audit.scope import ScopedResource  # noqa: E402
from agent.audit.state import ResourceState  # noqa: E402
from agent.data import Case  # noqa: E402

T = datetime(2026, 6, 1, tzinfo=timezone.utc)
CASE = Case("case_900", "x@example.com", T, "COMPLETE", T, T, "delete", "eval")


class FakeObs:
    def __init__(self, source):
        self.source, self.via = source, "test"


def resource(rid, action, prov, known=True, compliant=None, violation=False):
    st = ResourceState(rid, "security_service" if action == "RETAIN" else "account_service", "X", "test", action,
                       exists=True if action == "RETAIN" else False, known=known, violation=violation,
                       compliant_obs=FakeObs(compliant) if compliant else None)
    return ScopedResource(rid, st.system, set(prov)), st


def test_complete_case_drops_a_target_nothing_can_prove():
    items = [resource("account_1", "DELETE", {"identifier_link"}, compliant="evidence_1"),
             resource("legal_hold_1", "RETAIN", {"batch_attribution"}, known=False)]
    status, steps, traces = plan_case(CASE, items, strict=False, omission_citation=lambda st: None)
    assert status == "COMPLETE"
    assert [s["resource_id"] for s in steps] == ["account_1"]
    notes = [n for t in traces if "notes" in t for n in t["notes"]]
    assert any(n.get("resource_id") == "legal_hold_1" and "no admissible evidence" in n.get("dropped", "") for n in notes)


def test_complete_case_keeps_omission_cited_target():
    items = [resource("account_1", "DELETE", {"batch_attribution"}, known=False)]
    status, steps, _ = plan_case(CASE, items, strict=False, omission_citation=lambda st: "evidence_7")
    assert status == "COMPLETE" and steps[0]["evidence_id"] == "evidence_7"
