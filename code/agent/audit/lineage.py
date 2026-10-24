"""Recreation lineage: restore and sync paths that copy a subject's data into other systems.

Edges come from explicit activity text, not timing:
  RESTORE_BACKUP  backup_X  -> "Restore job repopulated downstream resource search_Y"
  SYNC_RECORD     data_flow_X -> "Active connector repopulated downstream resource search_Y"
and from evidence fields that name an upstream record (search_documents_primary.source_resource_id).

If either end is in a case's scope, the other end joins with provenance 'lineage_link'. The edge is
documented, so it inherits finding-grade from its anchor, but only when the other end isn't tied to a
different subject by any evidence.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..data import Case, Dataset
from .scope import ScopedResource, Scoper
from ..utils import RESOURCE_RE, canonical_system

EDGE_RE = re.compile(r"repopulated downstream resource (%s)" % RESOURCE_RE.pattern)


def lineage_edges(ds: Dataset, scoper: Scoper) -> list[tuple[str, str, str, object]]:
    """(upstream, downstream, source_id, time) edges."""
    edges = []
    for a in ds.activities:
        m = EDGE_RE.search(a.details)
        if m and a.event_type in ("RESTORE_BACKUP", "SYNC_RECORD"):
            edges.append((a.resource_id, m.group(1), a.activity_id, a.t))
    for rid, obs in scoper.by_resource.items():
        for o in obs:
            up = o.fields.get("source_resource_id")
            if up and RESOURCE_RE.fullmatch(str(up)):
                edges.append((str(up), rid, o.source, o.t))
    return edges


_EDGES_CACHE: dict[int, list] = {}


def expand_downstream(case: Case, user_id: str, scope: dict[str, ScopedResource], scoper: Scoper,
                      ds: Dataset) -> list[dict]:
    key = id(scoper)
    if key not in _EDGES_CACHE:
        _EDGES_CACHE[key] = lineage_edges(ds, scoper)
    neighbours = defaultdict(list)
    for up, down, src, t in _EDGES_CACHE[key]:
        if t <= case.audit_at:
            neighbours[up].append((down, src))
            neighbours[down].append((up, src))
    log = []
    frontier = list(scope)
    while frontier:
        rid = frontier.pop()
        anchor = scope[rid]
        for other, src in neighbours.get(rid, []):
            owners = scoper.owners.get(other, set())
            if owners and user_id not in owners:
                log.append({"skipped": other, "via": src, "reason": "tied to another subject"})
                continue
            sr = scope.get(other)
            if sr is None:
                sr = scope[other] = ScopedResource(other, canonical_system(other))
                frontier.append(other)
            tag = "lineage_identifier_link" if anchor.finding_grade else "lineage_link"
            if tag not in sr.provenance:
                sr.provenance.add(tag)
                sr.sources.add(src)
                log.append({"added": other, "from": rid, "via": src, "tag": tag})
    return log
