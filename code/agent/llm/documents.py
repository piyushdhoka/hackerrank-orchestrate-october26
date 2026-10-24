"""Second gate for pixels: screenshots (and PDFs without a text layer) read once by the document model.

Rows come back record-by-record (a console shows several records and decoys, so flat id lists would
cross-link them). Each row becomes an Observation with source_kind='document_llm', observed at the
artifact's capture time. Consoles may show a record by its internal id, a catalog display id
(user_17 -> tombstone_17) or a file name / statement number (catalog aliases). Identifiers displayed for a
record become explicit fields (an empty slot means "not displayed"), so policy can read exposure.
Rows are compared with the LINK_RESOURCE_IDENTITY catalog; disagreements are reported, not resolved.
"""
from __future__ import annotations

import hashlib
import re

from ..evidence.extractor import Extractor
from ..evidence.observation import flatten, ids_of, status_of
from ..normalize import identifiers_in
from ..utils import RESOURCE_RE
from .client import LLM, doc_model, doc_models, doc_provider, extract_json

PROMPT_VERSION = "doc-v3"
PROMPT = """This is an internal business artifact (admin console screenshot or document) from an online shop.
Extract every record that has an internal resource id such as account_123, search_45, analytics_6,
support_7, export_8, data_flow_9, backup_10, audit_record_11, tombstone_12, marketing_13, object_14,
legal_hold_15. Return JSON:
{"system": "<system/console name>", "captured_at": "<ISO time shown, or null>",
 "focus_resource_id": "<id of the record opened in the detail panel, or null>",
 "rows": [{"resource_id": "...", "identifiers": ["emails, phones, user_N references shown FOR THIS ROW"],
           "name": "<person name for this row or null>", "status": "<status text or null>",
           "fields": {"<label>": "<value>"}, "row_time": "<ISO or null>"}],
 "other_records": [{"label": "<file name / reference / display id shown for a record without such an id>",
                    "identifiers": ["..."], "status": "<status/version text or null>"}]}
The record shown in the page header or detail panel must also appear in rows, with its internal id if that id is
printed anywhere on the page. Copy ids exactly as printed: never number or invent an id; a record without a printed
id goes in other_records with its file name or reference. Only attach identifiers to the row they belong to.
Copy values exactly; do not guess."""

MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".pdf": "application/pdf"}


def _key(label: str) -> str:
    return re.sub(r"\W+", "_", str(label).strip().lower()).strip("_")


def resolve_record_id(ex: Extractor, shown: str, field_values=()) -> str | None:
    """A console may show the internal id, a catalog display id (user_17) or a file name/statement number.
    A catalog alias printed in the row (e.g. Name = delivery_confirmation_19.pdf) is grounded in the catalog, so it
    beats an id the model supplied (models have numbered unlabeled rows object_1, object_2, ...)."""
    for v in field_values:
        if isinstance(v, str) and v.strip() in ex.aliases:
            return ex.aliases[v.strip()]
    if RESOURCE_RE.fullmatch(shown):
        return shown
    return ex.display_ids.get(shown) or ex.aliases.get(shown)


def read_documents(ex: Extractor, llm: LLM | None) -> dict:
    report = {"provider": doc_provider(), "model": doc_model(), "fallback_models": doc_models()[1:],
              "files": len(ex.unparsed), "read": 0, "unavailable": [], "conflicts": [], "rows": 0}
    targets = [fid for fid in ex.unparsed if ":" not in fid]
    if llm is None or not llm.enabled:
        report["unavailable"] = targets
        return report
    catalog = {}
    for o in ex.obs:
        if o.via == "catalog_link":
            catalog.setdefault(o.resource_id, set()).update(o.ids)
    for fid in targets:
        m = ex.ds.manifest[fid]
        path = ex.ds.dataset_dir / m["file_path"]
        mime = MIME.get(path.suffix.lower())
        if not mime:
            continue
        payload = {"prompt": PROMPT, "path": str(path), "mime": mime, "sha": hashlib.sha256(path.read_bytes()).hexdigest()}
        reply = llm.call_with_fallback(doc_provider(), doc_models(), PROMPT_VERSION, payload, purpose=f"document:{fid}")
        data = extract_json(reply)
        if not isinstance(data, dict):
            report["unavailable"].append(fid)
            continue
        report["read"] += 1
        rows = [dict(r) for r in data.get("rows") or [] if isinstance(r, dict)]
        rows += [{"resource_id": r.get("label"), "identifiers": r.get("identifiers"), "status": r.get("status")}
                 for r in data.get("other_records") or [] if isinstance(r, dict)]
        for row in rows:
            fields = {_key(k): v for k, v in flatten(row.get("fields") or {}).items()}
            rid = resolve_record_id(ex, str(row.get("resource_id") or "").strip(), fields.values())
            if not rid:
                continue
            shown = set(identifiers_in(" ".join(map(str, row.get("identifiers") or []))))
            # what the screen shows for this record, as explicit fields: an empty slot means "not displayed"
            fields.update(status=row.get("status"), name=row.get("name"),
                          email=next((i for i in sorted(shown) if "@" in i), None),
                          phone=next((i for i in sorted(shown) if i.startswith("tel:")), None))
            users = sorted(i for i in shown if i.startswith("user_"))
            if users:
                fields["user_id"] = users[0]
            if row.get("row_time"):
                fields["row_time"] = row["row_time"]
            ids = shown | set(ids_of(fields))
            st = status_of(fields)
            # the artifact shows state at capture; dates printed in a row describe the record's history
            ex.add(source=fid, source_kind="document_llm", manifest_at=m["captured"], t=m["captured"],
                   system=data.get("system"), resource_id=rid, fields=fields, ids=frozenset(ids), status=st,
                   exists=st != "NOT_FOUND", via="document_llm")
            report["rows"] += 1
            if rid in catalog and ids and not (catalog[rid] & ids):
                report["conflicts"].append({"file_id": fid, "resource_id": rid, "screenshot_ids": sorted(ids),
                                            "catalog_ids": sorted(catalog[rid])})
    return report
