"""Local scorer: predictions vs gold (dataset/sample_output.csv or custom gold cases).

    python code/evaluation/evaluate.py --pred runs/sample/output.csv
    python code/evaluation/evaluate.py --pred runs/sample/output.csv --gold dataset/sample_output.csv --details

Metrics
  status        case-level deletion_status accuracy (+ confusion)
  steps         micro P/R/F1 on (phase, system_id, resource_id, operation, state)
  steps+ev      same key plus evidence_id
  fields        on steps aligned by (phase, system_id, resource_id): operation/state/evidence_id/step_no accuracy
  sequence      exact steps_json array match; pairwise order agreement on shared steps
  failures      per-case taxonomy: missing / extra resource, wrong operation, wrong evidence, order
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from agent.reports.scoring import load, score  # noqa: E402,F401  (re-exported for callers)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pred", type=Path, default=ROOT / "runs" / "sample" / "output.csv")
    ap.add_argument("--gold", type=Path, default=ROOT / "dataset" / "sample_output.csv")
    ap.add_argument("--details", action="store_true")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    res = score(load(a.pred), load(a.gold))
    head = {k: v for k, v in res.items() if k != "failures"}
    print(json.dumps(head, indent=1))
    print(f"cases with any failure: {len(res['failures'])}/{res['cases']}")
    if a.details:
        for cid, fs in res["failures"].items():
            print(cid, "; ".join(fs))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
