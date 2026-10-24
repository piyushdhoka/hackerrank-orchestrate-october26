"""Evidence parsing: a trace's lookup identifiers must reach the resource its completed line names."""
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
sys.path.insert(0, str(CODE))

from agent.data import Dataset  # noqa: E402
from agent.evidence import Extractor  # noqa: E402


@pytest.fixture(scope="module")
def ex():
    return Extractor(Dataset(ROOT)).run()


def obs_for(ex, source, rid):
    return [o for o in ex.obs if o.source == source and o.resource_id == rid]


def test_match_key_on_started_line_links_the_hit(ex):
    # evidence_39 trace tr-5482781: index_query_started match_key="s.kim14@yahoo.com" -> search_5479 result=hit
    o = obs_for(ex, "evidence_39", "search_5479")
    assert o and "s.kim14@yahoo.com" in o[0].ids and o[0].exists is True


def test_any_identifier_bearing_key_is_carried(ex):
    # every *_key value that holds an identifier on a resource-less line must reach that trace's resource
    import re
    from agent.evidence.log_parser import parse_line
    from agent.normalize import identifiers_in
    missing = []
    for f in (ROOT / "dataset" / "evidence" / "logs").glob("*.log"):
        fid = next(k for k, m in ex.ds.manifest.items() if m["file_path"].endswith(f.name))
        lines = [parse_line(l)[1] for l in f.read_text(encoding="utf-8").splitlines()]
        by_trace = {}
        for fields in lines:
            if fields.get("trace_id"):
                by_trace.setdefault(fields["trace_id"], []).append(fields)
        for tid, items in by_trace.items():
            rids = {x["resource_id"] for x in items if x.get("resource_id") and re.fullmatch(r"[a-z_]+_\d+", x["resource_id"])}
            idents = {i for x in items if not x.get("resource_id") for k, v in x.items() if k.endswith("_key")
                      for i in identifiers_in(v)}
            for rid in rids:
                got = set().union(*[o.ids for o in obs_for(ex, fid, rid)]) if obs_for(ex, fid, rid) else set()
                if not idents <= got:
                    missing.append((f.name, tid, rid, sorted(idents - got)))
    assert missing == []


def test_state_fields_from_resource_less_lines_never_leak(ex):
    # evidence_01 tr-4314388: cache_lookup result=miss must not mark audit_record_1596 absent
    o = obs_for(ex, "evidence_01", "audit_record_1596")
    assert o and o[0].exists is True


def test_phone_fields_without_plus_are_identifiers_but_free_text_digits_are_not():
    from agent.evidence.observation import ids_of
    assert "tel:447700900042" in ids_of({"phone": "447700900042"})
    assert "tel:447700900042" in ids_of({"requester_phone": "0044 7700 900042"})
    assert not any(i.startswith("tel:") for i in ids_of({"order_total": "447700900042", "note": "ref 20260915"}))


def test_lookalike_emails_stay_distinct():
    from agent.evidence.observation import ids_of
    assert ids_of({"email": "g.silva76.jr@gmail.com"}) == frozenset({"g.silva76.jr@gmail.com"})
