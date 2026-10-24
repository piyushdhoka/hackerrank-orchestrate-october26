"""Final submission checks for output.csv.

    python code/evaluation/check_submission.py                       # checks ./output.csv
    python code/evaluation/check_submission.py --compare other.csv   # + byte-identical comparison

Checks: exact header; one row per request (518), sorted by numeric case id; valid JSON array per row;
statuses/operations/states/phases from the contract; consecutive step numbers; phase order and operation
ordering; COMPLETE = cited verifications only; INCOMPLETE = cited findings, empty evidence on remediation and
verification; NOT_APPLICABLE = []; every cited id exists and was captured at or before the case's audit_at.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from agent.data import Dataset  # noqa: E402
from agent.reports.validation import citation_time_errors, validate_rows  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=ROOT / "output.csv")
    ap.add_argument("--compare", type=Path, default=None, help="second run's output.csv for a byte comparison")
    a = ap.parse_args(argv)
    problems: list[str] = []
    raw = a.output.read_bytes()
    with open(a.output, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        body = list(reader)
    if header != ["case_id", "deletion_status", "steps_json"]:
        problems.append(f"header is {header}")
    rows = []
    for i, r in enumerate(body, start=2):
        try:
            steps = json.loads(r[2])
            assert isinstance(steps, list)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"line {i}: steps_json is not a JSON array ({exc})")
            continue
        rows.append((r[0], r[1], steps))
    ds = Dataset(ROOT)
    expected = [c.case_id for c in ds.cases.values() if c.source == "eval"]
    problems += validate_rows(rows, expected)
    problems += citation_time_errors(rows, ds)
    n_steps = sum(len(s) for _, _, s in rows)
    print(f"rows: {len(rows)} (expected {len(expected)}); steps: {n_steps}; sha256: {hashlib.sha256(raw).hexdigest()}")
    if a.compare:
        same = a.compare.read_bytes() == raw
        print(f"byte-identical to {a.compare}: {same}")
        if not same:
            problems.append("second run differs")
    for p in problems:
        print("FAIL", p)
    print("ALL CHECKS PASSED" if not problems else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
