"""Observation: one statement about one resource at one moment, as recorded by one source.

Two clocks are kept apart on purpose (README §3.2: time):
  * ``t``           record-level time (inner snapshot capture, readback time, log line time)
  * ``manifest_at`` when the source itself was captured (evidence_files.csv) -> citation gate
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..normalize import identifiers_in, norm_phone
from ..utils import clean

STATUS_LABELS = {"FOUND", "NOT_FOUND", "IDENTIFIABLE", "ANONYMIZED", "REDACTED", "ACTIVE", "DISABLED", "BLOCKED"}


@dataclass
class Observation:
    source: str                 # file_id or activity_id
    source_kind: str            # 'file' | 'activity' | 'document_llm'
    manifest_at: datetime
    t: datetime
    system: str
    resource_id: str
    role: str = "primary"
    exists: bool | None = True
    fields: dict = field(default_factory=dict)
    ids: frozenset = frozenset()
    category: str | None = None
    status: str | None = None
    via: str = ""

    @property
    def citable(self) -> bool:
        return self.source_kind in ("file", "document_llm")


def flatten(d: dict, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        elif isinstance(v, list):
            scalars = [str(x) for x in v if not isinstance(x, (dict, list))]
            if scalars:
                out[key] = " ".join(scalars)
        else:
            out[key] = clean(v)
    return out


def ids_of(fields: dict) -> frozenset:
    s: set[str] = set()
    for k, v in fields.items():
        if k == "resource_id" or v is None:
            continue
        s |= identifiers_in(v)
        # a field that is declared to hold a phone may omit the '+' (free text still requires it)
        if "phone" in k.rsplit(".", 1)[-1].lower():
            tel = norm_phone(v)
            if tel:
                s.add(tel)
    return frozenset(s)


def status_of(fields: dict) -> str | None:
    for key in ("result", "status", "state"):
        v = fields.get(key)
        if isinstance(v, str) and v.upper() in STATUS_LABELS:
            return v.upper()
    return None


def record_exists(fields: dict, status: str | None) -> bool | None:
    if status == "NOT_FOUND":
        return False
    if str(fields.get("status_code")) == "404" or str(fields.get("http_status")) == "404":
        return False
    if "hits.total" in fields:
        try:
            return int(fields["hits.total"]) > 0
        except (TypeError, ValueError):
            return None
    return True
