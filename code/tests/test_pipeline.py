"""End-to-end invariants, sample score regression guard, provenance rules, document gate (mocked)."""
import csv
import json
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "evaluation"))

from agent.reports.scoring import load, score  # noqa: E402
from agent.llm.client import LLM  # noqa: E402
from agent.pipeline import run  # noqa: E402
from agent.audit.planner import OPERATION_ORDER, PHASE_OF, STATE_OF  # noqa: E402
from agent.audit.scope import FINDING_GRADE  # noqa: E402


@pytest.fixture(scope="module")
def sample_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("sample")
    report = run(ROOT, requests="sample", output=d / "out.csv", trace_dir=d / "traces",
                 llm=LLM(d / "cache", enabled=False))
    return d, report


@pytest.fixture(scope="module")
def eval_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("eval")
    report = run(ROOT, requests="eval", output=d / "out.csv", trace_dir=d / "traces", llm=LLM(d / "cache", enabled=False))
    return d, report


def rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def check_contract(rs, n):
    assert len(rs) == n
    nums = [int(r["case_id"].split("_")[1]) for r in rs]
    assert nums == sorted(nums)
    for r in rs:
        steps = json.loads(r["steps_json"])
        assert r["deletion_status"] in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE")
        if r["deletion_status"] == "NOT_APPLICABLE":
            assert steps == []
            continue
        assert [s["step_no"] for s in steps] == list(range(1, len(steps) + 1))
        keys = [(OPERATION_ORDER.index(s["operation"]), s["system_id"], s["resource_id"]) for s in steps]
        assert keys == sorted(keys)
        for s in steps:
            assert set(s) == {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
            assert s["state"] == STATE_OF[s["operation"]] and s["phase"] == PHASE_OF[s["operation"]]
            if r["deletion_status"] == "COMPLETE":
                assert s["phase"] == "VERIFICATION"
            else:
                assert bool(s["evidence_id"]) == (s["phase"] == "FINDING")
        if r["deletion_status"] == "INCOMPLETE":
            assert any(s["phase"] == "FINDING" for s in steps)


def test_sample_output_contract(sample_run):
    d, _ = sample_run
    check_contract(rows(d / "out.csv"), 32)


def test_eval_output_contract(eval_run):
    d, _ = eval_run
    check_contract(rows(d / "out.csv"), 518)


def test_sample_score_regression_guard(sample_run):
    d, _ = sample_run
    res = score(load(d / "out.csv"), load(ROOT / "dataset" / "sample_output.csv"))
    assert res["status_accuracy"] == 1.0
    assert res["steps"]["f1"] >= 0.985
    assert res["pairwise_order_accuracy"] == 1.0
    assert res["exact_case_match"] >= 28 / 32


def test_findings_only_from_finding_grade_links(eval_run):
    d, _ = eval_run
    for f in (d / "traces").glob("case_*.json"):
        t = json.loads(f.read_text(encoding="utf-8"))
        for s in t["steps"]:
            if s.get("phase") == "FINDING":
                assert set(s["provenance"]) & FINDING_GRADE, (f.name, s)
                assert t["resources"][s["resource_id"]]["violation"] is True


def test_every_step_has_provenance(eval_run):
    d, _ = eval_run
    for f in (d / "traces").glob("case_*.json"):
        t = json.loads(f.read_text(encoding="utf-8"))
        for s in t["steps"]:
            if "step_no" in s:
                assert s["provenance"], (f.name, s)


def test_strict_mode_never_uses_inferred_links(tmp_path):
    run(ROOT, requests="sample", output=tmp_path / "o.csv", trace_dir=tmp_path / "t", strict=True,
        llm=LLM(tmp_path / "c", enabled=False))
    banned = {"batch_attribution", "owner_batch", "inner_time_link", "assumed_state"}
    for f in (tmp_path / "t").glob("case_*.json"):
        for s in json.loads(f.read_text(encoding="utf-8"))["steps"]:
            assert not (set(s.get("provenance", [])) & banned), (f.name, s)


def test_document_gate_rows_become_citable_findings(tmp_path):
    """A screenshot row read by the (mocked) document model turns an unknown catalog-linked
    record into evidence: case_34's account console shows an active profile."""
    def backend(provider, model, payload):
        if "path" not in payload:            # intent batch: no opinion -> deterministic decision stands
            return '{"results": []}', 10, 2
        if "account_admin_console" in payload["path"]:
            return json.dumps({"system": "account_service", "captured_at": "2026-09-26T11:00:00Z", "rows": [
                {"resource_id": "account_5762", "identifiers": ["haruka.s1838@zoho.com", "user_11"],
                 "status": "Active", "fields": {"email": "haruka.s1838@zoho.com"}}]}), 1000, 100
        return json.dumps({"rows": []}), 1000, 10
    run(ROOT, requests="eval", output=tmp_path / "o.csv", trace_dir=tmp_path / "t", only={"case_34"},
        llm=LLM(tmp_path / "c", backend=backend))
    r = rows(tmp_path / "o.csv")[0]
    steps = json.loads(r["steps_json"])
    assert r["deletion_status"] == "INCOMPLETE"
    assert steps[0]["operation"] == "REPORT_REMAINING_RECORD" and steps[0]["evidence_id"] == "evidence_04"


def test_document_gate_reports_catalog_conflicts(tmp_path):
    def backend(provider, model, payload):
        if "path" not in payload:
            return '{"results": []}', 10, 2
        if "marketing_profile_console" in payload["path"]:
            return json.dumps({"rows": [{"resource_id": "marketing_001", "identifiers": ["sj.park@naver.com"],
                                         "name": "Soo-jin Park", "status": "Live"}]}), 900, 80
        return json.dumps({"rows": []}), 900, 10
    report = run(ROOT, requests="eval", output=tmp_path / "o.csv", trace_dir=tmp_path / "t", only={"case_63"},
                 llm=LLM(tmp_path / "c", backend=backend))
    conflicts = report["document_gate"]["conflicts"]
    assert [c["resource_id"] for c in conflicts] == ["marketing_001"]


def test_screenshot_rows_are_observed_at_capture_time(tmp_path):
    """A console shows state at the moment it was captured; dates printed in a row (created, last drill)
    describe the record's history, not when we observed it."""
    from agent.data import Dataset
    from agent.evidence import Extractor
    from agent.llm.documents import read_documents

    def backend(provider, model, payload):
        return json.dumps({"captured_at": None, "rows": [{"resource_id": "backup_9287", "identifiers": ["areed1433@gmail.com"],
                                                          "row_time": "2026-04-26T02:00:00Z"}]}), 1, 1
    ex = Extractor(Dataset(ROOT)).run()
    read_documents(ex, LLM(tmp_path, backend=backend))
    obs = [o for o in ex.obs if o.source == "evidence_32" and o.resource_id == "backup_9287"]
    assert obs and all(o.t == ex.ds.manifest["evidence_32"]["captured"] for o in obs)


def _doc_backend(rows_by_file):
    def backend(provider, model, payload):
        if "path" not in payload:
            return '{"results": []}', 1, 1
        for name, data in rows_by_file.items():
            if name in payload["path"]:
                return json.dumps(data), 1, 1
        return json.dumps({"rows": []}), 1, 1
    return backend


def _run_one(tmp_path, case_id, rows_by_file):
    run(ROOT, requests="eval", output=tmp_path / "o.csv", trace_dir=tmp_path / "t", only={case_id},
        llm=LLM(tmp_path / "c", backend=_doc_backend(rows_by_file)))
    r = rows(tmp_path / "o.csv")[0]
    return r["deletion_status"], json.loads(r["steps_json"])


def test_identifiers_shown_on_a_screenshot_record_count_as_exposed_pii(tmp_path):
    status, steps = _run_one(tmp_path, "case_50", {"analytics_profile_view": {"rows": [
        {"resource_id": "analytics_7438", "identifiers": ["user_20", "darwish.omar@icloud.com"], "name": "Omar Darwish"}]}})
    assert status == "INCOMPLETE"
    assert (steps[0]["operation"], steps[0]["resource_id"], steps[0]["evidence_id"]) == \
        ("REPORT_EXPOSED_PII", "analytics_7438", "evidence_19")


def test_catalog_display_id_maps_screenshot_row_to_resource(tmp_path):
    # the registry shows the tombstone by its display id user_17 (catalog: "tombstone_17 (user_17)"), no PII
    status, steps = _run_one(tmp_path, "case_46", {"deleted_account_reference": {"rows": [
        {"resource_id": "user_17", "identifiers": ["user_17"], "status": "Retained reference"}]}})
    tomb = [s for s in steps if s["resource_id"] == "tombstone_17"]
    assert tomb and tomb[0]["operation"] == "VERIFY_RECORD_REDACTED" and tomb[0]["evidence_id"] == "evidence_42"


def test_file_name_alias_on_screenshot_marks_object_present(tmp_path):
    status, steps = _run_one(tmp_path, "case_61", {"object_storage_browser": {"rows": [], "other_records": [
        {"label": "delivery_confirmation_19.pdf", "status": "v3"}]}})
    assert status == "INCOMPLETE"
    assert (steps[0]["operation"], steps[0]["resource_id"], steps[0]["evidence_id"]) == \
        ("REPORT_REMAINING_RECORD", "object_19", "evidence_46")


def test_catalog_alias_in_a_field_beats_a_model_invented_id(tmp_path):
    # the document model numbered unlabeled rows object_1..object_8; the Name field carries the real catalog alias
    status, steps = _run_one(tmp_path, "case_61", {"object_storage_browser": {"rows": [
        {"resource_id": "object_1", "identifiers": [], "status": "v3", "fields": {"Name": "delivery_confirmation_19.pdf"}}]}})
    assert status == "INCOMPLETE"
    assert (steps[0]["operation"], steps[0]["resource_id"], steps[0]["evidence_id"]) == \
        ("REPORT_REMAINING_RECORD", "object_19", "evidence_46")


def test_connector_status_words_are_read_as_enabled(tmp_path):
    status, steps = _run_one(tmp_path, "case_39", {"connector_management_console": {"rows": [
        {"resource_id": "data_flow_1121", "identifiers": ["jain.aditi@gmail.com"], "status": "Healthy",
         "fields": {"Mode": "Continuous"}}]}})
    assert status == "INCOMPLETE"
    assert (steps[0]["operation"], steps[0]["resource_id"], steps[0]["evidence_id"]) == \
        ("REPORT_ACTIVE_DATA_FLOW", "data_flow_1121", "evidence_14")


def test_present_backup_without_any_restore_control_is_a_restore_risk(tmp_path):
    status, steps = _run_one(tmp_path, "case_55", {"backup_restore_console": {"rows": [
        {"resource_id": "backup_9287", "identifiers": ["areed1433@gmail.com"], "fields": {"Tier": "Archive"}}]}})
    assert status == "INCOMPLETE"
    assert (steps[0]["operation"], steps[0]["resource_id"], steps[0]["evidence_id"]) == \
        ("REPORT_RESTORE_RISK", "backup_9287", "evidence_32")


def test_document_prompt_requires_focus_row_and_forbids_invented_ids():
    from agent.llm.documents import PROMPT
    assert "detail panel" in PROMPT and "must also appear in rows" in PROMPT
    assert "never number or invent" in PROMPT


def test_complete_citations_observe_the_record_unless_proving_absence(eval_run):
    """A COMPLETE verification may cite a snapshot that merely omits the record only when it verifies ABSENCE;
    any other state must be cited to a source that actually observes the record."""
    from agent.data import Dataset
    from agent.evidence import Extractor
    ex = Extractor(Dataset(ROOT)).run()
    observed = {(o.source, o.resource_id) for o in ex.obs}
    d, _ = eval_run
    bad = []
    for r in rows(d / "out.csv"):
        if r["deletion_status"] != "COMPLETE":
            continue
        for s in json.loads(r["steps_json"]):
            if s["operation"] != "VERIFY_RECORD_ABSENT" and (s["evidence_id"], s["resource_id"]) not in observed:
                bad.append((r["case_id"], s["resource_id"], s["operation"], s["evidence_id"]))
    assert bad == []
