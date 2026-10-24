"""How much of the output rests on weak attribution: steps whose resource was assigned to the case by
reported_status matching or by numeric case order (README §3.3)."""
from __future__ import annotations

from collections import Counter

WEAK_PASSES = {"status", "case_order"}


def count(traces: list[dict]) -> dict:
    steps = weak = 0
    by_pass: Counter = Counter()
    weak_cases = set()
    for t in traces:
        for s in t.get("steps", []):
            if "step_no" not in s:
                continue
            steps += 1
            passes = {p.split(":", 1)[1] for p in s.get("provenance", []) if p.startswith("batch_pass:")}
            by_pass.update(passes)
            if passes & WEAK_PASSES:
                weak += 1
                weak_cases.add(t["case_id"])
    return {"cases": len(traces), "steps": steps, "weak_steps": weak, "cases_with_weak_steps": len(weak_cases),
            "by_pass": dict(by_pass)}
