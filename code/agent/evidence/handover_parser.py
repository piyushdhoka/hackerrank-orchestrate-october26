"""Plain-text shift handover: '- ts | system | resource | CATEGORY | STATE | subject' lines."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from ..normalize import identifiers_in
from ..utils import clean, parse_ts

LINE_RE = re.compile(r"^-\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.+)$")


def parse(ex, fid: str, path: Path, manifest_at: datetime) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        m = LINE_RE.match(line.strip())
        if not m:
            continue
        ts, system, rid, cat, state, subject = m.groups()
        f = {"record_type": cat, "result": state, "subject": clean(subject)}
        ex.coverage[fid].append((system, parse_ts(ts)))
        ex.add(source=fid, source_kind="file", manifest_at=manifest_at, t=parse_ts(ts) or manifest_at,
               system=system, resource_id=rid, fields=f, ids=frozenset(identifiers_in(subject)),
               category=cat if cat != "DATA_FLOW" else None, status=state,
               exists=state != "NOT_FOUND", via="handover")
