"""Ghost Data Still Exists - deletion audit agent. One command does everything:

    python code/main.py

  1. audits the 518 requests in dataset/deletion_requests.csv -> ./output.csv (validated before writing)
     + per-case provenance traces in runs/eval/traces/ and the token ledger runs/eval/llm_calls.jsonl
  2. re-runs the 32 public samples (gold-compatible and strict) and the custom cases, and scores them
  3. writes code/evaluation/evaluation_report.md (auto section) and code/evaluation/usage_report.md

Options:
    --attribution strict     evidence-bound mode (no batch attribution / inner-time discovery / assumed state)
    --no-llm                 deterministic first pass only (both LLM gates off)
    --requests sample        audit the samples instead of the eval set
    --skip-evaluation        only produce output.csv (+ usage report)
    --cases case_33,case_34  restrict the audited set (debugging)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent.llm import client as llm_mod  # noqa: E402
from agent.pipeline import run, write_output  # noqa: E402
from agent.reports import attribution, evaluation, scoring, usage, validation  # noqa: E402


def _load(name: str):
    path = Path(__file__).resolve().parent / "evaluation" / f"{name}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _custom_gold() -> dict:
    mod = _load("custom_cases")
    return {cid: (c["status"], c["steps"]) for cid, c in mod.CUSTOM_GOLD.items()} if mod else {}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--requests", choices=["eval", "sample"], default="eval")
    p.add_argument("--output", type=Path, default=None, help="default ./output.csv (eval) or runs/sample/output.csv")
    p.add_argument("--runs-dir", type=Path, default=ROOT / "runs", help="traces, ledgers, scored runs")
    p.add_argument("--reports-dir", type=Path, default=ROOT / "code" / "evaluation")
    p.add_argument("--attribution", choices=["gold-compatible", "strict"], default="gold-compatible")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--skip-evaluation", action="store_true")
    p.add_argument("--cases", default="")
    a = p.parse_args(argv)

    strict = a.attribution == "strict"
    use_llm = not a.no_llm
    only = {c.strip() for c in a.cases.split(",") if c.strip()} or None
    output = a.output or (ROOT / "output.csv" if a.requests == "eval" else a.runs_dir / "sample" / "output.csv")

    # 1. the audited set
    run_dir = a.runs_dir / a.requests
    report = run(ROOT, requests=a.requests, strict=strict, use_llm=use_llm, trace_dir=run_dir / "traces", only=only)
    errors = validation.validate_rows(report["rows"], report["expected_ids"])
    if errors:
        print("output.csv NOT written - contract violations:\n  " + "\n  ".join(errors[:50]), file=sys.stderr)
        return 1
    write_output(output, report["rows"])
    usage.write_ledger(run_dir / "llm_calls.jsonl", report["llm_calls"])

    # 2. scoring on the public samples (+ custom cases)
    scored = {}
    if not a.skip_evaluation:
        gold = scoring.load(ROOT / "dataset" / "sample_output.csv")
        splits = _load("splits")
        for mode in (False, True):
            r = run(ROOT, requests="sample", strict=mode, use_llm=use_llm,
                    trace_dir=a.runs_dir / ("sample_strict" if mode else "sample") / "traces")
            pred = {cid: (st, steps) for cid, st, steps in r["rows"]}
            label = "strict" if mode else "gold-compatible"
            scored[f"all 32 ({label})"] = scoring.score(pred, gold)
            if not mode and splits:
                scored["dev (20)"] = scoring.score(pred, gold, only=splits.DEV)
                scored["held-out (12)"] = scoring.score(pred, gold, only=splits.HELD_OUT)
        custom_gold = _custom_gold()
        custom = None
        if custom_gold and a.requests == "eval":
            pred = {cid: (st, steps) for cid, st, steps in report["rows"] if cid in custom_gold}
            custom = scoring.score(pred, custom_gold)
        gates = report["intent_gate"], report["document_gate"]
        meta = {"Mode": a.attribution, "LLM gates": "on" if use_llm else "off",
                "Intent gate": f"{llm_mod.intent_provider()}:{llm_mod.intent_model()} ({evaluation.gate_status(gates[0])})",
                "Document gate": f"{gates[1]['provider']}:{gates[1]['model']} (read {gates[1]['read']} of {gates[1]['files']} files)"}
        traces = [json.loads(f.read_text(encoding="utf-8")) for f in sorted((run_dir / "traces").glob("case_*.json"))]
        note = ["Held-out caveat: the split was frozen after the attribution and citation rules had been reverse-"
                "engineered on all 32 samples, so the held-out column is an upper bound, not a clean generalisation "
                "estimate. Rule changes from now on are justified on dev only."]
        evaluation.update_report(a.reports_dir / "evaluation_report.md",
                                 evaluation.render_metrics(scored, custom, meta, attribution=attribution.count(traces),
                                                           extra=note))

    # 3. usage report from the logged ledger of the run that produced output.csv
    prices = usage.load_prices(ROOT / "code" / "config" / "pricing.json")
    summary = usage.summarize(usage.load_ledger(run_dir / "llm_calls.jsonl"), prices, n_requests=len(report["rows"]))
    meta = {"Run": f"{a.requests} ({len(report['rows'])} requests), mode {a.attribution}",
            "Ledger": f"{run_dir.relative_to(a.runs_dir.parent) if a.runs_dir.parent in run_dir.parents else run_dir}/llm_calls.jsonl",
            "Intent model": f"{llm_mod.intent_provider()}:{llm_mod.intent_model()}",
            "Document model": f"{llm_mod.doc_provider()}:{llm_mod.doc_model()}"}
    a.reports_dir.mkdir(parents=True, exist_ok=True)
    (a.reports_dir / "usage_report.md").write_text(usage.render(summary, meta), encoding="utf-8")

    print(json.dumps({"output": str(output), "rows": len(report["rows"]), "stats": report["stats"],
                      "scores": {n: {"status": r["status_accuracy"], "step_f1": round(r["steps"]["f1"], 4),
                                     "exact": r["exact_case_match"]} for n, r in scored.items()},
                      "usage": summary["overall"]}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
