"""Score predictions against gold answers (case status, steps, fields, sequence, failure taxonomy)."""
from __future__ import annotations

import csv
import json
from collections import Counter
from itertools import combinations
from pathlib import Path


def load(path: Path) -> dict[str, tuple[str, list]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def key(s, ev=False):
    k = (s["phase"], s["system_id"], s["resource_id"], s["operation"], s["state"])
    return k + (s["evidence_id"],) if ev else k


def prf(tp, fp, fn):
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def score(pred: dict, gold: dict, only=None) -> dict:
    if only is not None:
        gold = {c: v for c, v in gold.items() if c in set(only)}
    cases = sorted(gold, key=lambda c: int(c.split("_")[-1]))
    status_ok, confusion = 0, Counter()
    tp = fp = fn = tpe = fpe = fne = 0
    field_hits, field_total = Counter(), 0
    exact, pair_ok, pair_total = 0, 0, 0
    failures = {}
    for cid in cases:
        gs, gsteps = gold[cid]
        ps, psteps = pred.get(cid, ("MISSING", []))
        confusion[(gs, ps)] += 1
        status_ok += gs == ps
        exact += (gs == ps and psteps == gsteps)
        gk, pk = Counter(map(key, gsteps)), Counter(map(key, psteps))
        tp += sum((gk & pk).values()); fp += sum((pk - gk).values()); fn += sum((gk - pk).values())
        gke, pke = Counter(key(s, True) for s in gsteps), Counter(key(s, True) for s in psteps)
        tpe += sum((gke & pke).values()); fpe += sum((pke - gke).values()); fne += sum((gke - pke).values())
        align = {(s["phase"], s["system_id"], s["resource_id"]): s for s in psteps}
        for s in gsteps:
            field_total += 1
            p = align.get((s["phase"], s["system_id"], s["resource_id"]))
            for f in ("operation", "state", "evidence_id", "step_no"):
                field_hits[f] += bool(p and p[f] == s[f])
        shared = [key(s) for s in gsteps if key(s) in pk]
        ppos = {key(s): i for i, s in enumerate(psteps)}
        for a, b in combinations(shared, 2):
            pair_total += 1
            pair_ok += ppos[a] < ppos[b]
        f = []
        gres, pres = {s["resource_id"] for s in gsteps}, {s["resource_id"] for s in psteps}
        f += [f"missing_resource:{r}" for r in sorted(gres - pres)]
        f += [f"extra_resource:{r}" for r in sorted(pres - gres)]
        for s in gsteps:
            p = align.get((s["phase"], s["system_id"], s["resource_id"]))
            if p and p["operation"] != s["operation"]:
                f.append(f"wrong_operation:{s['resource_id']}:{p['operation']}!={s['operation']}")
            elif p and p["evidence_id"] != s["evidence_id"]:
                f.append(f"wrong_evidence:{s['resource_id']}:{p['evidence_id'] or '-'}!={s['evidence_id'] or '-'}")
            elif not p and s["resource_id"] in pres:
                f.append(f"missing_step:{s['phase']}:{s['resource_id']}")
        for s in psteps:
            if (s["phase"], s["system_id"], s["resource_id"]) not in {(g["phase"], g["system_id"], g["resource_id"]) for g in gsteps} and s["resource_id"] in gres:
                f.append(f"extra_step:{s['phase']}:{s['resource_id']}")
        if gs != ps:
            f.insert(0, f"status:{ps}!={gs}")
        if not f and psteps != gsteps:
            f.append("order_or_numbering")
        if f:
            failures[cid] = f
    n = len(cases) or 1   # empty gold: report zeros rather than divide by zero
    P, R, F = prf(tp, fp, fn)
    Pe, Re, Fe = prf(tpe, fpe, fne)
    return {
        "cases": n,
        "status_accuracy": status_ok / n,
        "exact_case_match": exact / n,
        "steps": {"precision": P, "recall": R, "f1": F, "tp": tp, "fp": fp, "fn": fn},
        "steps_with_evidence": {"precision": Pe, "recall": Re, "f1": Fe},
        "field_accuracy": {f: field_hits[f] / field_total for f in field_hits} if field_total else {},
        "pairwise_order_accuracy": pair_ok / pair_total if pair_total else 1.0,
        "confusion": {f"{g}->{p}": v for (g, p), v in sorted(confusion.items())},
        "failures": failures,
    }
