"""Batch attribution must say which matching pass assigned each orphan, so weak steps can be counted."""
import json
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
sys.path.insert(0, str(CODE))

from agent.llm.client import LLM  # noqa: E402
from agent.pipeline import run  # noqa: E402
from agent.reports import attribution, evaluation, scoring  # noqa: E402

PASSES = {"coobserved+status", "status", "coobserved", "case_order"}


@pytest.fixture(scope="module")
def traces(tmp_path_factory):
    d = tmp_path_factory.mktemp("attr")
    run(ROOT, requests="eval", trace_dir=d, llm=LLM(d / "cache", enabled=False))
    return [json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.glob("case_*.json"))]


def batch_resources(traces):
    for t in traces:
        for rid, r in (t.get("resources") or {}).items():
            if "batch_attribution" in r["provenance"]:
                yield t["case_id"], rid, r["provenance"]


def test_every_batch_attribution_names_its_pass(traces):
    for cid, rid, prov in batch_resources(traces):
        passes = [p.split(":", 1)[1] for p in prov if p.startswith("batch_pass:")]
        assert len(passes) == 1 and passes[0] in PASSES, (cid, rid, prov)


def test_status_matching_only_applies_to_primary_accounts(traces):
    for cid, rid, prov in batch_resources(traces):
        if not rid.startswith("account_"):
            assert not any("status" in p for p in prov if p.startswith("batch_pass:")), (cid, rid, prov)


def test_weak_attribution_counter():
    tr = [{"case_id": "case_1", "steps": [
        {"step_no": 1, "resource_id": "account_1", "provenance": ["batch_attribution", "batch_pass:status"]},
        {"step_no": 2, "resource_id": "analytics_1", "provenance": ["batch_attribution", "batch_pass:case_order"]},
        {"step_no": 3, "resource_id": "account_2", "provenance": ["batch_attribution", "batch_pass:coobserved+status"]},
        {"step_no": 4, "resource_id": "search_1", "provenance": ["identifier_link"]}]},
          {"case_id": "case_2", "steps": [{"step_no": 1, "resource_id": "search_2", "provenance": ["identifier_link"]}]}]
    c = attribution.count(tr)
    assert (c["steps"], c["weak_steps"], c["cases_with_weak_steps"]) == (5, 2, 1)
    assert c["by_pass"] == {"status": 1, "case_order": 1, "coobserved+status": 1}


def test_evaluation_report_shows_weak_attribution():
    r = scoring.score({}, {})
    md = evaluation.render_metrics({"sample": r}, attribution={"steps": 5, "weak_steps": 2, "cases_with_weak_steps": 1,
                                                              "cases": 2, "by_pass": {"status": 1, "case_order": 1}})
    assert "weak attribution" in md.lower() and "2 of 5" in md
