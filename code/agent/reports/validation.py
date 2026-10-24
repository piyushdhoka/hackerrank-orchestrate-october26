"""Contract check for output.csv (AGENTS.md §6.3/§6.4). Run before the file is written."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from ..audit.planner import OPERATION_ORDER, PHASE_OF, STATE_OF
from ..utils import case_number

STEP_KEYS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
STATUSES = {"COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"}


def validate_rows(rows: list[tuple[str, str, list]], expected_ids: list[str]) -> list[str]:
    errors: list[str] = []
    ids = [r[0] for r in rows]
    for cid in sorted(set(expected_ids) - set(ids), key=case_number):
        errors.append(f"missing {cid}")
    for cid in sorted(set(ids) - set(expected_ids), key=case_number):
        errors.append(f"unexpected {cid}")
    if len(ids) != len(set(ids)):
        errors.append("duplicate case_id rows")
    if ids != sorted(ids, key=case_number):
        errors.append("rows not sorted by numeric case_id")
    for cid, status, steps in rows:
        if status not in STATUSES:
            errors.append(f"{cid}: invalid status {status}")
            continue
        if status == "NOT_APPLICABLE":
            if steps != []:
                errors.append(f"{cid}: NOT_APPLICABLE must have []")
            continue
        if not steps:
            errors.append(f"{cid}: {status} without steps")
            continue
        if any(set(s) != STEP_KEYS for s in steps):
            errors.append(f"{cid}: step fields must be exactly {sorted(STEP_KEYS)}")
            continue
        if [s["step_no"] for s in steps] != list(range(1, len(steps) + 1)):
            errors.append(f"{cid}: step_no not consecutive from 1")
        for s in steps:
            if s["operation"] not in STATE_OF:
                errors.append(f"{cid}: unknown operation {s['operation']}")
                continue
            if s["state"] != STATE_OF[s["operation"]]:
                errors.append(f"{cid}: state {s['state']} does not match {s['operation']}")
            if s["phase"] != PHASE_OF[s["operation"]]:
                errors.append(f"{cid}: phase {s['phase']} does not match {s['operation']}")
            if status == "COMPLETE":
                if s["phase"] != "VERIFICATION":
                    errors.append(f"{cid}: COMPLETE case has a {s['phase']} step")
                if not s["evidence_id"]:
                    errors.append(f"{cid}: COMPLETE step without evidence ({s['resource_id']})")
            elif s["phase"] == "FINDING" and not s["evidence_id"]:
                errors.append(f"{cid}: finding without evidence ({s['resource_id']})")
            elif s["phase"] != "FINDING" and s["evidence_id"]:
                errors.append(f"{cid}: future step cites evidence ({s['resource_id']})")
        keys = [(OPERATION_ORDER.index(s["operation"]), s["system_id"], s["resource_id"])
                for s in steps if s["operation"] in STATE_OF]
        if keys != sorted(keys):
            errors.append(f"{cid}: steps out of order")
        if status == "INCOMPLETE" and not any(s["phase"] == "FINDING" for s in steps):
            errors.append(f"{cid}: INCOMPLETE without a finding")
    return errors


def read_output(path: Path) -> list[tuple[str, str, list]]:
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != ["case_id", "deletion_status", "steps_json"]:
            raise ValueError(f"bad header {reader.fieldnames}")
        return [(r["case_id"], r["deletion_status"], json.loads(r["steps_json"])) for r in reader]


def citation_time_errors(rows: list[tuple[str, str, list]], ds) -> list[str]:
    """Every cited evidence id must exist and be available by the case's audit_at (AGENTS.md §6.6):
    file_id -> evidence_files.csv captured_at, activity_id -> event_time, audit_id -> user_audit_log event_time."""
    from ..data import read_csv
    from ..utils import parse_ts
    when = {fid: m["captured"] for fid, m in ds.manifest.items()}
    when.update({a.activity_id: a.t for a in ds.activities})
    when.update({a["audit_id"]: parse_ts(a["event_time"]) for a in read_csv(ds.dataset_dir / "user_audit_log.csv")})
    errors = []
    for cid, _status, steps in rows:
        case = ds.cases.get(cid)
        for s in steps:
            ev = s.get("evidence_id")
            if not ev:
                continue
            if ev not in when:
                errors.append(f"{cid}: unknown evidence id {ev}")
            elif case and when[ev] > case.audit_at:
                errors.append(f"{cid}: {ev} cited but captured after audit_at ({when[ev].isoformat()} > {case.audit_at.isoformat()})")
    return errors
