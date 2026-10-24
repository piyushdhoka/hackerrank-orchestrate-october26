"""JSON evidence: snapshot bundles, service resource exports, ledgers, manifests, connector configs."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..utils import parse_ts
from .observation import flatten, ids_of, record_exists, status_of
from .sql_parser import COLLECTION_CATEGORY


def parse(ex, fid: str, path: Path, manifest_at: datetime) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    top_t = parse_ts(data.get("captured_at") or data.get("exported_at")) or manifest_at
    _walk(ex, fid, manifest_at, data, {"t": top_t, "system": None, "collection": data.get("collection")})


def _walk(ex, fid, manifest_at, o, ctx) -> None:
    if isinstance(o, list):
        for x in o:
            _walk(ex, fid, manifest_at, x, ctx)
        return
    if not isinstance(o, dict):
        return
    ctx = dict(ctx)
    if "captured_at" in o and "resource_id" not in o:
        ctx["t"] = parse_ts(o["captured_at"]) or ctx["t"]       # inner snapshot clock
    if isinstance(o.get("data_store"), str):
        ctx["system"] = o["data_store"]
        ctx["collection"] = o.get("collection")
        ex.coverage[fid].append((o["data_store"], ctx["t"]))
    if isinstance(o.get("resource_id"), str):
        _record(ex, fid, manifest_at, o, ctx)
    for k, v in o.items():
        if k == "service_exports" and isinstance(v, dict):
            for system, colls in v.items():
                ex.coverage[fid].append((system, ctx["t"]))
                for cname, items in colls.items():
                    _walk(ex, fid, manifest_at, items, {**ctx, "system": system, "collection": cname})
        elif isinstance(v, (dict, list)):
            _walk(ex, fid, manifest_at, v, ctx)


def _record(ex, fid, manifest_at, rec, ctx) -> None:
    f = flatten(rec)
    t = parse_ts(rec.get("recorded_at") or rec.get("observed_at")) or ctx["t"]
    status = status_of(f)
    category = f.get("record_type") or COLLECTION_CATEGORY.get(ctx.get("collection") or "")
    ex.add(source=fid, source_kind="file", manifest_at=manifest_at, t=t, system=rec.get("system") or ctx.get("system"),
           resource_id=rec["resource_id"], fields=f, ids=ids_of(f), category=category, status=status,
           exists=record_exists(f, status), via=f"json:{ctx.get('collection')}")
