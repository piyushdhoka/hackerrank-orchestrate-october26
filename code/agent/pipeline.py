"""End-to-end run: extract -> LLM gates -> scope -> state -> plan -> output.csv + per-case traces."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from .data import Dataset
from .llm.documents import read_documents
from .audit.lineage import expand_downstream
from .evidence import Extractor
from .llm.intent import classify_all
from .llm.client import LLM, load_dotenv
from .audit.planner import plan_case
from .audit.scope import Scoper
from .audit.state import assess
from .utils import iso


def write_output(path: Path, rows: list[tuple[str, str, list]]) -> None:
    """output.csv: exact header, one row per case, compact deterministic JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid, status, steps in rows:
            w.writerow([cid, status, json.dumps(steps, ensure_ascii=False, separators=(",", ":"))])


def _omission_citer(ex: Extractor):
    present = defaultdict(set)
    for o in ex.obs:
        if o.source_kind == "file" and o.exists:
            present[o.source].add(o.resource_id)

    def cite(st, case):
        best = None
        # a snapshot that merely omits the record can only prove ABSENCE
        omission_ok = st.action == "DELETE" or st.exists is False
        for fid, cov in (ex.coverage.items() if omission_ok else ()):
            m = ex.ds.manifest.get(fid)
            if not m or m["captured"] > case.audit_at or st.resource_id in present[fid]:
                continue
            if any(sys == st.system and t and case.requested_at <= t <= case.audit_at for sys, t in cov):
                key = (m["captured"], fid)
                best = max(best, key) if best else key
        if best:
            return best[1]
        # last resort: the latest admissible source naming the resource at all (file, document or the
        # catalog activity row - activity_ids are valid evidence ids)
        named = [o for o in by_resource.get(st.resource_id, []) if o.t <= case.audit_at and o.manifest_at <= case.audit_at]
        if named:
            return max(named, key=lambda o: (o.manifest_at, o.source)).source
        return None
    by_resource = defaultdict(list)
    for o in ex.obs:
        by_resource[o.resource_id].append(o)
    return cite


def run(root: Path, requests: str = "eval", output: Path | None = None, strict: bool = False,
        use_llm: bool = True, trace_dir: Path | None = None, only: set[str] | None = None,
        llm: LLM | None = None) -> dict:
    load_dotenv(root)
    ds = Dataset(root)
    llm = llm or LLM(root / "code" / "cache" / "llm", enabled=use_llm)
    ex = Extractor(ds).run()
    doc_report = read_documents(ex, llm)
    decisions, intent_report = classify_all([c.request_text for c in ds.cases.values()], llm)

    scoper = Scoper(ds, ex.obs, strict)
    subjects = {cid: ds.subject_for(c) for cid, c in ds.cases.items()}
    authorized = [c for c in ds.cases.values() if decisions[c.request_text].authorizes_deletion and subjects[c.case_id]]

    scopes, states, has_findings, lineage = {}, {}, {}, {}

    def assess_scope(case):
        for rid in scopes[case.case_id]:
            if rid not in states[case.case_id]:
                states[case.case_id][rid] = assess(rid, scoper.by_resource.get(rid, []), case, ds.rules,
                                                   scoper.worker_ops(rid), strict)
        has_findings[case.case_id] = any(
            st.violation and scopes[case.case_id][rid].finding_grade and st.violation_obs is not None
            for rid, st in states[case.case_id].items())

    for c in authorized:
        scopes[c.case_id] = scoper.linked(c, subjects[c.case_id])
        scoper.owner_batch(c, subjects[c.case_id], scopes[c.case_id])
        lineage[c.case_id] = expand_downstream(c, subjects[c.case_id].user_id, scopes[c.case_id], scoper, ds)
        states[c.case_id] = {}
        assess_scope(c)
    orphan_log = scoper.assign_orphans(authorized, scopes, has_findings)
    for c in authorized:
        assess_scope(c)

    citer = _omission_citer(ex)
    target = [c for c in ds.cases.values() if c.source == requests and (not only or c.case_id in only)]
    target.sort(key=lambda c: c.number)
    rows, stats = [], defaultdict(int)
    if trace_dir:
        trace_dir.mkdir(parents=True, exist_ok=True)
    for c in target:
        decision = decisions[c.request_text]
        subject = subjects[c.case_id]
        trace = {"case_id": c.case_id, "requested_at": iso(c.requested_at), "reported_at": iso(c.reported_at),
                 "audit_at": iso(c.audit_at), "mode": "strict" if strict else "gold-compatible",
                 "intent": decision.to_dict(), "subject": subject.user_id if subject else None,
                 "identity": subject.to_dict() if subject else None}
        if not decision.authorizes_deletion:
            status, steps, step_traces = "NOT_APPLICABLE", [], []
        elif subject is None:
            status, steps, step_traces = "INCOMPLETE", [], [{"notes": ["request email not resolvable"]}]
        else:
            items = [(scopes[c.case_id][rid], states[c.case_id][rid]) for rid in sorted(scopes[c.case_id])]
            status, steps, step_traces = plan_case(c, items, strict, lambda st, c=c: citer(st, c))
            trace["resources"] = {rid: {"provenance": sorted(sr.provenance), "sources": sorted(sr.sources),
                                        **states[c.case_id][rid].summary()}
                                  for rid, sr in sorted(scopes[c.case_id].items())}
            trace["lineage"] = lineage.get(c.case_id, [])
            # resources whose state could only come from a screenshot/PDF the document gate didn't read
            unread = set(doc_report["unavailable"])
            pending = sorted(rid for rid, st in states[c.case_id].items()
                             if not st.known and unread and any(o.via == "catalog_link" for o in scoper.by_resource.get(rid, [])))
            if pending:
                trace["needs_document_gate"] = pending
                stats["cases_needing_document_gate"] += 1
        trace["status"], trace["steps"] = status, step_traces
        stats[status] += 1
        for st in step_traces:
            for p in st.get("provenance", []):
                stats["prov:" + p.split(":")[0]] += 1
        rows.append((c.case_id, status, steps))
        if trace_dir:
            (trace_dir / f"{c.case_id}.json").write_text(json.dumps(trace, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
    if output:
        write_output(output, rows)
    report = {"cases_written": len(rows), "rows": rows, "expected_ids": [c.case_id for c in target],
              "stats": dict(stats), "intent_gate": intent_report,
              "document_gate": doc_report, "batch_attribution": orphan_log,
              "llm_usage": llm.ledger.totals(), "llm_calls": llm.ledger.calls}
    if trace_dir:
        (trace_dir / "_run_report.json").write_text(json.dumps({k: v for k, v in report.items() if k != "rows"},
                                                               ensure_ascii=False, indent=1, default=str),
                                                    encoding="utf-8")
    return report
