"""Log files in six formats (logfmt, JSON lines, RFC5424 syslog, [LEVEL] [svc] [trace] app traces,
pipe-delimited, cloud-runtime with escaped message="..."). Lines are grouped by trace id so a
*_started line's lookup_key joins its *_completed line's result."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..normalize import identifiers_in
from ..utils import RESOURCE_RE, clean, parse_ts
from .observation import flatten, ids_of, status_of

# Resource-less lines (query_started, cache_lookup, connection_acquired) only contribute who was looked
# up (any field whose value holds an identifier: lookup_key, match_key, owner_key, subject_ref, ...) and
# through which pool - never state such as result=miss.
CONTEXT_KEYS = {"pool", "service", "logger", "function", "trace_id", "requestId"}
KV_RE = re.compile(r'([A-Za-z_][\w.]*)=(?:"((?:[^"\\]|\\.)*)"|(\S+))')
BRACKET_RE = re.compile(r"^\s*\[(\w+)\]\s*\[([\w.-]+)\]\s*\[([\w.-]+)\]\s*([\w.]+):\s*(.*)$")
SYSLOG_RE = re.compile(r"^\s*<\d+>\d+\s+\S+\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+(.*)$")


def kv(text: str) -> dict:
    out = {}
    for k, quoted, bare in KV_RE.findall(text):
        v = (quoted if quoted or bare == "" else bare).replace('\\"', '"')
        if k == "message" and "=" in v:
            out.update(kv(v))
        else:
            out[k] = clean(v)
    return out


def parse_line(line: str) -> tuple[datetime | None, dict]:
    m = re.match(r"^(\S+)\s+(.*)$", line)
    if not m:
        return None, {}
    t, rest = parse_ts(m.group(1)), m.group(2)
    if rest.lstrip().startswith("{"):
        try:
            f = flatten(json.loads(rest))
        except json.JSONDecodeError:
            f = kv(rest)
    elif rest.startswith("| "):
        parts = [p.strip() for p in rest.split(" | ")]
        parts[0] = parts[0].lstrip("| ").strip()
        f = {}
        if len(parts) >= 5:
            f = {"level": parts[0], "service": parts[1], "trace_id": parts[2], "event": parts[3]}
            f.update(kv(" | ".join(parts[4:])))
    elif (b := BRACKET_RE.match(rest)):
        f = {"level": b.group(1), "service": b.group(2), "trace_id": b.group(3), "event": b.group(4)}
        f.update(kv(b.group(5)))
    elif (s := SYSLOG_RE.match(rest)):
        f = {"service": s.group(1), "trace_id": s.group(2)}
        f.update(kv(s.group(3)))
    else:
        f = kv(rest)
    if "service" not in f:
        f["service"] = f.get("logger") or f.get("function")
    if "trace_id" not in f and f.get("requestId"):
        f["trace_id"] = f["requestId"]
    return t, f


def exists_from(f: dict, status: str | None) -> bool | None:
    if status == "NOT_FOUND":
        return False
    if str(f.get("http_status")) == "404" or str(f.get("rows")) == "0":
        return False
    if str(f.get("result", "")).lower() == "miss" or str(f.get("total_hits")) == "0":
        return False
    if str(f.get("available", "")).lower() == "false":
        return False
    return True


def parse(ex, fid: str, path: Path, manifest_at: datetime) -> None:
    groups: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        t, f = parse_line(line)
        if f:
            groups[f.get("trace_id") or f"line{i}"].append((t or manifest_at, f))
    for lines in groups.values():
        base: dict = {}
        per_rid: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
        for t, f in lines:
            rid = f.get("resource_id")
            if rid and RESOURCE_RE.fullmatch(rid):
                per_rid[rid].append((t, f))
            else:
                base.update({k: v for k, v in f.items()
                             if v is not None and (k in CONTEXT_KEYS or identifiers_in(v))})
        for rid, items in per_rid.items():
            merged = dict(base)
            for _, f in items:
                merged.update({k: v for k, v in f.items() if v is not None or k not in merged})
            status = status_of(merged)
            event = str(merged.get("event", ""))
            category = merged.get("record_type")
            if not category and rid.startswith("support_"):
                if "attachment" in event or str(merged.get("file", "")).endswith(".pdf"):
                    category = "SUPPORT_ATTACHMENT"
                elif "message" in event:
                    category = "SUPPORT_MESSAGE"
            ex.add(source=fid, source_kind="file", manifest_at=manifest_at, t=items[-1][0],
                   system=merged.get("system") or merged.get("service"), resource_id=rid,
                   role="replica" if str(merged.get("pool", "")).endswith("_replica") else "primary",
                   fields=merged, ids=ids_of({k: v for k, v in merged.items() if k not in ("trace_id", "requestId")}),
                   category=category, status=status, exists=exists_from(merged, status), via=f"log:{event}")
