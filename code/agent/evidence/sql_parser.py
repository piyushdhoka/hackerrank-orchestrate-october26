"""SQLite snapshot exports: typed inventory tables, generic primary/replica tables, special account/search tables."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..utils import clean, parse_ts
from .observation import flatten, ids_of, record_exists, status_of

TYPED_TABLES = {
    "customer_profiles": "account_service",
    "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "support_records": "support_platform",
    "customer_exports": "export_service",
    "connector_subscriptions": "integration_service",
    "backup_restore_entries": "backup_service",
    "security_audit_records": "security_service",
}
SPECIAL_TABLES = {
    "customer_profiles_primary": ("account_service", "primary"),
    "customer_profiles_failover_replica": ("account_service", "failover_replica"),
    "search_documents_primary": ("search_service", "primary"),
}
COLLECTION_CATEGORY = {
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "support_conversations": "SUPPORT_MESSAGE",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE",
    "retained_order_records": "LEGAL_HOLD_RECORD",
}


def parse(ex, fid: str, path: Path, manifest_at: datetime) -> None:
    text = path.read_text(encoding="utf-8")
    hm = re.search(r"Captured at:\s*(\S+)", text) or re.search(r"snapshot_completed_at=(\S+)", text)
    header_t = parse_ts(hm.group(1)) if hm else manifest_at
    con = sqlite3.connect(":memory:")
    con.executescript(text)
    scope: dict[str, datetime] = {}
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
    if "snapshot_scope" in tables:
        for system, cap in con.execute("select origin_system, captured_at from snapshot_scope"):
            scope[system] = parse_ts(cap)
            ex.coverage[fid].append((system, scope[system]))
    by_system_role: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for table in tables:
        cur = con.execute(f'select * from "{table}"')
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur]
        if table in TYPED_TABLES:
            system = TYPED_TABLES[table]
            for r in rows:
                f = {k: clean(v) for k, v in r.items()}
                ex.add(source=fid, source_kind="file", manifest_at=manifest_at, t=scope.get(system, header_t),
                       system=system, resource_id=r["resource_id"], fields=f, ids=ids_of(f),
                       status=status_of(f), via=f"sql:{table}")
        elif table in SPECIAL_TABLES or table.startswith("customer_data_"):
            special = SPECIAL_TABLES.get(table)
            role = special[1] if special else table[len("customer_data_"):]
            for r in rows:
                if special:
                    system, f, category = special[0], {k: clean(v) for k, v in r.items()}, None
                else:
                    system = r["origin_system"]
                    f = flatten(json.loads(r["record_json"]))
                    category = COLLECTION_CATEGORY.get(r.get("collection_name"))
                status = status_of(f)
                ex.add(source=fid, source_kind="file", manifest_at=manifest_at, t=header_t, system=system,
                       resource_id=r["resource_id"], role=role, fields=f, ids=ids_of(f), category=category,
                       status=status, exists=record_exists(f, status), via=f"sql:{table}")
                by_system_role[system][role].add(r["resource_id"])
            for system in ({special[0]} if special else {r["origin_system"] for r in rows}):
                ex.coverage[fid].append((system, header_t))
    # Replication drift: within one capture, a record present in one copy but missing from a
    # sibling copy of the same system is evidence that the sibling no longer holds it.
    for system, roles in by_system_role.items():
        if len(roles) < 2:
            continue
        every = set().union(*roles.values())
        for role, present in roles.items():
            for rid in sorted(every - present):
                ex.add(source=fid, source_kind="file", manifest_at=manifest_at, t=header_t, system=system,
                       resource_id=rid, role=role, exists=False, via="sql:omission")
