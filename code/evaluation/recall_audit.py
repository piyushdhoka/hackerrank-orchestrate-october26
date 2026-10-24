"""Recall audit of the eval output.

For every eval case, every observation whose identifiers exactly match the case's subject is checked against the
case's steps. Each (case, resource) that is linked but absent from the steps is classified:

  after_audit         every linking record is dated after audit_at
  before_request      every linking record predates requested_at (older copy / another request of the same person)
  other_case          the resource appears in another case's steps
  shared_identifier   the resource is tied to more than one user by evidence (decoy / ambiguous)
  candidate_miss      a linking record inside [requested_at, audit_at] exists, yet no step -> needs a look

    python code/evaluation/recall_audit.py [--output output.csv] [--traces runs/eval/traces]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from agent.data import Dataset  # noqa: E402
from agent.evidence import Extractor  # noqa: E402
from agent.llm.client import LLM, load_dotenv  # noqa: E402
from agent.llm.documents import read_documents  # noqa: E402
from agent.reports.scoring import load  # noqa: E402


def audit(output: Path, traces: Path) -> dict:
    load_dotenv(ROOT)
    ds = Dataset(ROOT)
    ex = Extractor(ds).run()
    read_documents(ex, LLM(ROOT / "code" / "cache" / "llm"))          # cached screenshot reads, if any
    rows = load(output)
    in_steps = {cid: {s["resource_id"] for s in steps} for cid, (_, steps) in rows.items()}
    stepped_by = defaultdict(set)
    for cid, rids in in_steps.items():
        for r in rids:
            stepped_by[r].add(cid)
    owners = defaultdict(set)
    for o in ex.obs:
        for i in o.ids:
            owners[o.resource_id] |= ds.identity.owners(i)
    by_ident = defaultdict(list)
    for o in ex.obs:
        for i in o.ids:
            by_ident[i].append(o)
    results = []
    for c in sorted((c for c in ds.cases.values() if c.source == "eval"), key=lambda c: c.number):
        status = rows[c.case_id][0]
        subj = ds.subject_for(c)
        if status == "NOT_APPLICABLE" or subj is None:
            continue
        linked = defaultdict(list)
        for ident in subj.identifiers():
            for o in by_ident.get(ident, []):
                if o.resource_id not in in_steps[c.case_id]:
                    linked[o.resource_id].append(o)
        trace = json.loads((traces / f"{c.case_id}.json").read_text(encoding="utf-8"))
        notes = [n for st in trace["steps"] if "notes" in st for n in st["notes"]]
        for rid, obs in sorted(linked.items()):
            in_window = [o for o in obs if c.requested_at <= o.t <= c.audit_at]
            if len(owners[rid]) > 1:
                cls = "shared_identifier"
            elif in_window:
                cls = "candidate_miss"
            elif stepped_by[rid] - {c.case_id}:
                cls = "other_case"
            elif all(o.t > c.audit_at for o in obs):
                cls = "after_audit"
            else:
                cls = "before_request"
            why = None
            if cls == "candidate_miss":
                r = (trace.get("resources") or {}).get(rid)
                why = {"in_scope": r is not None, "notes": [n for n in notes if n.get("resource_id") == rid],
                       "state": None if r is None else {k: r[k] for k in ("category", "action", "exists", "known", "violation")},
                       "linking": sorted({f"{o.source}:{o.via}:{o.t.isoformat()}" for o in in_window})[:4]}
            results.append({"case_id": c.case_id, "resource_id": rid, "class": cls,
                            "other_cases": sorted(stepped_by[rid] - {c.case_id}), "why": why})
    return {"pairs": results, "summary": dict(Counter(r["class"] for r in results))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=ROOT / "output.csv")
    ap.add_argument("--traces", type=Path, default=ROOT / "runs" / "eval" / "traces")
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    res = audit(a.output, a.traces)
    print(json.dumps(res["summary"], indent=1))
    for r in res["pairs"]:
        if r["class"] == "candidate_miss":
            print(r["case_id"], r["resource_id"], json.dumps(r["why"], default=str))
    if a.json:
        a.json.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
