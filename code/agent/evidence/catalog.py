"""LINK_RESOURCE_IDENTITY activity rows: the data catalog's resource <-> subject links (+ document aliases)."""
from __future__ import annotations

import re

from ..normalize import identifiers_in


def parse(ex) -> None:
    for a in ex.ds.activities:
        if a.event_type != "LINK_RESOURCE_IDENTITY":
            continue
        m = re.search(re.escape(a.resource_id) + r" \(([^)]+)\)", a.details)
        if m and m.group(1) != a.resource_id:
            # every "(...)" is how some console displays the record (screenshot rows use it)
            ex.display_ids[m.group(1)] = a.resource_id
            # "(NST-2026-0718-1842)" is also a text alias; "(user_17)" is an identifier, not a document alias
            if not identifiers_in(m.group(1)):
                ex.aliases[m.group(1)] = a.resource_id
        cat = "SUPPORT_ATTACHMENT" if "belongs only to" in a.details else None
        ex.add(source=a.activity_id, source_kind="activity", manifest_at=a.t, t=a.t,
               system=a.target_system, resource_id=a.resource_id, exists=None,
               fields={"details": a.details}, ids=frozenset(identifiers_in(a.details)),
               category=cat, via="catalog_link")
