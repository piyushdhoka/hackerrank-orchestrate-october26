"""Output validation, scoring, usage + evaluation reports, and the single main.py command."""
import csv
import json
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
sys.path.insert(0, str(CODE))

from agent.llm.client import LLM  # noqa: E402
from agent.reports import evaluation, scoring, usage, validation  # noqa: E402


def step(n, phase, op, state, sys_="account_service", rid="account_1", ev=""):
    return {"step_no": n, "phase": phase, "system_id": sys_, "resource_id": rid, "operation": op, "state": state,
            "evidence_id": ev}


GOOD = [
    ("case_2", "NOT_APPLICABLE", []),
    ("case_10", "COMPLETE", [step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT", ev="evidence_1")]),
    ("case_33", "INCOMPLETE", [step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", ev="evidence_2"),
                               step(2, "REMEDIATION", "DELETE_RECORD", "ABSENT"),
                               step(3, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT")]),
]


# ------------------------------------------------------------------ validate
def test_valid_output_has_no_errors():
    assert validation.validate_rows(GOOD, ["case_2", "case_10", "case_33"]) == []


@pytest.mark.parametrize("mutate,expected", [
    (lambda r: r[:2], "missing case_33"),
    (lambda r: [r[1], r[0], r[2]], "not sorted"),
    (lambda r: [("case_2", "NOT_APPLICABLE", r[1][2])] + r[1:], "NOT_APPLICABLE must have []"),
    (lambda r: [r[0], ("case_10", "COMPLETE", [step(1, "VERIFICATION", "VERIFY_RECORD_ABSENT", "ABSENT")]), r[2]],
     "COMPLETE step without evidence"),
    (lambda r: [r[0], r[1], ("case_33", "INCOMPLETE", [dict(s, evidence_id="evidence_9") if s["phase"] == "REMEDIATION" else s for s in r[2][2]])],
     "future step cites evidence"),
    (lambda r: [r[0], r[1], ("case_33", "INCOMPLETE", [dict(s, step_no=s["step_no"] + 1) for s in r[2][2]])],
     "step_no"),
    (lambda r: [r[0], r[1], ("case_33", "INCOMPLETE", [dict(r[2][2][0], state="ABSENT")] + r[2][2][1:])],
     "state"),
    (lambda r: [r[0], r[1], ("case_33", "INCOMPLETE", list(reversed(r[2][2])))], "order"),
])
def test_each_contract_violation_is_reported(mutate, expected):
    errors = validation.validate_rows(mutate(GOOD), ["case_2", "case_10", "case_33"])
    assert any(expected in e for e in errors), errors


# ------------------------------------------------------------------ scoring
def as_gold(rows):
    return {cid: (st, steps) for cid, st, steps in rows}


def test_identical_predictions_score_perfectly():
    r = scoring.score(as_gold(GOOD), as_gold(GOOD))
    assert r["status_accuracy"] == r["exact_case_match"] == r["steps"]["f1"] == 1.0
    assert r["failures"] == {}


def test_missing_step_lowers_recall_and_is_named():
    pred = as_gold(GOOD)
    pred["case_33"] = ("INCOMPLETE", pred["case_33"][1][:2])
    r = scoring.score(pred, as_gold(GOOD))
    assert r["steps"]["recall"] < 1.0 and r["steps"]["precision"] == 1.0
    assert any("missing_step" in f for f in r["failures"]["case_33"])


# ------------------------------------------------------------------ usage report from the logged ledger
LEDGER = [
    {"provider": "anthropic", "model": "claude-sonnet-5-5", "purpose": "intent", "cached": False, "input_tokens": 3000, "output_tokens": 500},
    {"provider": "anthropic", "model": "claude-sonnet-5-5", "purpose": "intent", "cached": True, "input_tokens": 2000, "output_tokens": 400},
    {"provider": "openai", "model": "gpt-sol", "purpose": "document:evidence_04", "cached": False, "input_tokens": 1500, "output_tokens": 300},
]
PRICES = {"claude-sonnet-5-5": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
          "gpt-sol": {"input_per_mtok": 10.0, "output_per_mtok": 40.0}}


def test_ledger_round_trips_through_jsonl(tmp_path):
    p = tmp_path / "llm_calls.jsonl"
    usage.write_ledger(p, LEDGER)
    assert usage.load_ledger(p) == LEDGER


def test_summary_separates_live_and_cached_tokens_per_model():
    s = usage.summarize(LEDGER, PRICES, n_requests=518)
    son = s["models"]["anthropic:claude-sonnet-5-5"]
    assert (son["live_calls"], son["cached_calls"]) == (1, 1)
    assert (son["input_tokens"], son["output_tokens"]) == (3000, 500)
    assert (son["cached_input_tokens"], son["cached_output_tokens"]) == (2000, 400)
    assert son["cost_usd"] == pytest.approx(3000 * 3 / 1e6 + 500 * 15 / 1e6)
    assert s["overall"]["total_tokens"] == 3000 + 500 + 1500 + 300
    assert s["overall"]["tokens_per_request"] == pytest.approx(5300 / 518)
    assert s["overall"]["cost_usd"] == pytest.approx(0.0165 + 1500 * 10 / 1e6 + 300 * 40 / 1e6)


def test_missing_prices_are_reported_not_invented():
    s = usage.summarize(LEDGER, {}, n_requests=518)
    md = usage.render(s, {"run": "test"})
    assert s["overall"]["cost_usd"] is None
    assert "not configured" in md and "claude-sonnet-5-5" in md


def test_report_contains_required_fields():
    md = usage.render(usage.summarize(LEDGER, PRICES, n_requests=518), {"run": "test"})
    for needle in ["anthropic", "claude-sonnet-5-5", "openai", "gpt-sol", "Model calls", "Input tokens",
                   "Output tokens", "per request", "Estimated cost", "Overall"]:
        assert needle in md, needle


def test_zero_call_run_says_so():
    md = usage.render(usage.summarize([], PRICES, n_requests=518), {"run": "test"})
    assert "No model calls" in md


def test_cached_ledger_entries_keep_original_token_counts(tmp_path):
    backend = lambda p, m, x: ('{"ok": true}', 120, 30)  # noqa: E731
    first = LLM(tmp_path, backend=backend)
    first.call("anthropic", "claude-sonnet-5-5", "v1", {"system": "s", "user": "u"}, purpose="intent")
    again = LLM(tmp_path, backend=backend)
    again.call("anthropic", "claude-sonnet-5-5", "v1", {"system": "s", "user": "u"}, purpose="intent")
    entry = again.ledger.calls[0]
    assert entry["cached"] is True and (entry["input_tokens"], entry["output_tokens"]) == (120, 30)


# ------------------------------------------------------------------ evaluation report
def test_auto_section_is_replaced_and_manual_text_kept(tmp_path):
    p = tmp_path / "evaluation_report.md"
    p.write_text("# Report\n\nhand-written intro\n\n" + evaluation.START + "\nold numbers\n" + evaluation.END
                 + "\n\n## Failures found\nhand-written analysis\n", encoding="utf-8")
    evaluation.update_report(p, "NEW NUMBERS")
    text = p.read_text(encoding="utf-8")
    assert "hand-written intro" in text and "hand-written analysis" in text
    assert "NEW NUMBERS" in text and "old numbers" not in text
    evaluation.update_report(p, "NEW NUMBERS")
    assert p.read_text(encoding="utf-8") == text   # idempotent


def test_missing_report_is_created_from_template(tmp_path):
    p = tmp_path / "evaluation_report.md"
    evaluation.update_report(p, "NUMBERS")
    text = p.read_text(encoding="utf-8")
    assert "NUMBERS" in text and evaluation.START in text and "Custom evaluation cases" in text


def test_metrics_section_lists_required_metrics():
    r = scoring.score(as_gold(GOOD), as_gold(GOOD))
    md = evaluation.render_metrics({"sample (gold-compatible)": r, "sample (strict)": r}, custom=r)
    for needle in ["Status accuracy", "Step precision", "Field accuracy", "Sequence", "strict", "Custom"]:
        assert needle in md, needle


# ------------------------------------------------------------------ one command
def test_main_one_command_writes_output_and_reports(tmp_path):
    import main
    rc = main.main(["--no-llm", "--output", str(tmp_path / "output.csv"), "--runs-dir", str(tmp_path / "runs"),
                    "--reports-dir", str(tmp_path / "reports")])
    assert rc == 0
    with open(tmp_path / "output.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 518 and list(rows[0]) == ["case_id", "deletion_status", "steps_json"]
    ev = (tmp_path / "reports" / "evaluation_report.md").read_text(encoding="utf-8")
    us = (tmp_path / "reports" / "usage_report.md").read_text(encoding="utf-8")
    assert "Status accuracy" in ev and "No model calls" in us
    assert "held-out (12)" in ev and "dev (20)" in ev and "Weak attribution" in ev
    assert (tmp_path / "runs" / "eval" / "llm_calls.jsonl").exists()
    assert json.loads((tmp_path / "runs" / "eval" / "traces" / "case_33.json").read_text(encoding="utf-8"))["case_id"] == "case_33"


# ------------------------------------------------------------------ provider errors degrade, never crash
def test_provider_error_returns_none_and_is_logged(tmp_path):
    def boom(provider, model, payload):
        raise RuntimeError("429 RESOURCE_EXHAUSTED quota")
    llm = LLM(tmp_path, backend=boom)
    assert llm.call("gemini", "m", "v1", {"system": "s", "user": "u"}, purpose="intent") is None
    entry = llm.ledger.calls[-1]
    assert entry["cached"] is False and "RESOURCE_EXHAUSTED" in entry["error"]
    assert list(tmp_path.glob("*.json")) == []            # failures are never cached


def test_intent_gate_survives_provider_errors(tmp_path):
    from agent.llm import intent
    llm = LLM(tmp_path, backend=lambda p, m, x: (_ for _ in ()).throw(RuntimeError("503")))
    decisions, report = intent.classify_all(["Please delete my account."], llm)
    assert decisions["Please delete my account."].basis == "deterministic" and report["llm_failures"] == 1


def test_document_gate_falls_back_when_primary_model_has_no_quota(tmp_path, monkeypatch):
    from agent.llm import client as llm_mod
    monkeypatch.setenv("GHOST_DOC_MODEL", "primary-model")
    monkeypatch.setenv("GHOST_DOC_FALLBACK_MODEL", "fallback-model")
    seen = []

    def backend(provider, model, payload):
        seen.append(model)
        if model == "primary-model":
            raise RuntimeError("429 RESOURCE_EXHAUSTED limit: 0")
        return '{"rows": []}', 50, 5
    llm = LLM(tmp_path, backend=backend)
    text = llm.call_with_fallback("gemini", llm_mod.doc_models(), "doc-v1", {"prompt": "p", "sha": "x"}, "document:e")
    assert text == '{"rows": []}' and seen == ["primary-model", "fallback-model"]
    assert [c.get("error") is not None for c in llm.ledger.calls] == [True, False]


# ------------------------------------------------------------------ custom evaluation cases
def test_custom_cases_are_valid_gold():
    import importlib.util
    spec = importlib.util.spec_from_file_location("custom_cases", CODE / "evaluation" / "custom_cases.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    gold = mod.CUSTOM_GOLD
    assert len(gold) >= 3
    rows = sorted(((cid, g["status"], g["steps"]) for cid, g in gold.items()), key=lambda r: int(r[0].split("_")[1]))
    assert validation.validate_rows(rows, list(gold)) == []
    assert all(g["why"].strip() for g in gold.values())


# ------------------------------------------------------------------ dev / held-out split
def test_sample_split_is_frozen_stratified_and_disjoint():
    import importlib.util
    spec = importlib.util.spec_from_file_location("splits", CODE / "evaluation" / "splits.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    gold = scoring.load(ROOT / "dataset" / "sample_output.csv")
    dev, held = set(mod.DEV), set(mod.HELD_OUT)
    assert (len(dev), len(held)) == (20, 12) and not dev & held and dev | held == set(gold)
    held_complete = sum(gold[c][0] == "COMPLETE" for c in held)
    assert held_complete == 3                      # 10 COMPLETE overall -> 7 dev / 3 held-out
    assert mod.make_split(gold, seed=mod.SEED) == (mod.DEV, mod.HELD_OUT)   # reproducible from the seed


def test_scoring_a_subset_only_counts_those_cases():
    gold = as_gold(GOOD)
    r = scoring.score(gold, gold, only=["case_2", "case_10"])
    assert r["cases"] == 2


# ------------------------------------------------------------------ reports must not overstate LLM use
def test_usage_table_shows_failed_calls_per_model():
    ledger = LEDGER + [{"provider": "gemini", "model": "model-x", "purpose": "document:e", "cached": False,
                        "input_tokens": 0, "output_tokens": 0, "error": "429 RESOURCE_EXHAUSTED"}]
    md = usage.render(usage.summarize(ledger, PRICES, n_requests=518), {"run": "t"})
    row = next(l for l in md.splitlines() if "| model-x |" in l)
    assert "Failed calls" in md and row.split("|")[5].strip() == "1"


def test_gate_status_line_reports_fallbacks():
    line = evaluation.gate_status({"llm_available": True, "distinct_texts": 398, "llm_failures": 398})
    assert "0 of 398" in line and "deterministic" in line


# ------------------------------------------------------------------ citation timing (submission check)
def test_citation_after_audit_at_is_flagged():
    from agent.data import Dataset
    ds = Dataset(ROOT)
    # case_29 audits at 2026-09-15T12:00Z; evidence_02 was captured 2026-09-17 -> must not be cited
    bad = [("case_29", "INCOMPLETE", [step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", "search_service",
                                           "search_1993", ev="evidence_02")])]
    good = [("case_29", "INCOMPLETE", [step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", "search_service",
                                            "search_1993", ev="evidence_92")])]
    assert any("after audit_at" in e for e in validation.citation_time_errors(bad, ds))
    assert validation.citation_time_errors(good, ds) == []


def test_unknown_evidence_id_is_flagged():
    from agent.data import Dataset
    rows_ = [("case_29", "INCOMPLETE", [step(1, "FINDING", "REPORT_REMAINING_RECORD", "PRESENT", ev="evidence_999")])]
    assert any("unknown evidence" in e for e in validation.citation_time_errors(rows_, Dataset(ROOT)))
