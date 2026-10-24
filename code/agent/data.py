"""Load participant-facing CSVs: requests, identity graph, manifest, activity, rules."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .identity import IdentityGraph, IdentityProfile
from .utils import case_number, parse_ts


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@dataclass
class Case:
    case_id: str
    email: str
    requested_at: datetime
    reported_status: str
    reported_at: datetime
    audit_at: datetime
    request_text: str
    source: str  # 'eval' | 'sample'

    @property
    def number(self) -> int:
        return case_number(self.case_id)


@dataclass
class Activity:
    activity_id: str
    t: datetime
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    result: str
    details: str


class Dataset:
    def __init__(self, root: Path):
        self.root = root
        ds = root / "dataset"
        self.dataset_dir = ds
        self.cases: dict[str, Case] = {}
        for fname, src in (("deletion_requests.csv", "eval"), ("sample_deletion_requests.csv", "sample")):
            p = ds / fname
            if not p.exists():
                continue
            for r in read_csv(p):
                self.cases[r["case_id"]] = Case(
                    r["case_id"], r["email"].strip(), parse_ts(r["requested_at"]), r["reported_status"],
                    parse_ts(r["reported_at"]), parse_ts(r["audit_at"]), r["request_text"], src)
        self.rules = {r["data_category"]: r for r in read_csv(ds / "deletion_rules.csv")}
        self.manifest = {r["file_id"]: {**r, "captured": parse_ts(r["captured_at"])}
                         for r in read_csv(ds / "evidence_files.csv")}
        self.activities = [
            Activity(a["activity_id"], parse_ts(a["event_time"]), a["event_type"], a["source_system"],
                     a["target_system"], a["resource_id"], a["reported_result"], a["details"])
            for a in read_csv(ds / "system_activity.csv")]
        self.identity = IdentityGraph.from_rows(read_csv(ds / "user_identities.csv"),
                                                read_csv(ds / "user_audit_log.csv"))

    def subject_for(self, case: Case) -> IdentityProfile | None:
        uid = self.identity.resolve(case.email)
        return self.identity.profile(uid) if uid else None
