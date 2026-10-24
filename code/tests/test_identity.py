"""Identity graph: user_id <-> every current/historical identifier, with provenance and validity windows."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.data import read_csv  # noqa: E402
from agent.identity import IdentityGraph  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
H1, H2 = "a" * 64, "b" * 64


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def small_graph():
    identities = [
        {"user_id": "user_01", "name": "Léa Martin", "email": "lea.new@example.com", "phone": "+33 6 11 22 33 44",
         "email_hash": H2, "updated_at": "2026-03-01T00:00:00Z"},
        {"user_id": "user_02", "name": "Bo Chen", "email": "bo@example.com", "phone": "+86 138 0000 1111",
         "email_hash": "c" * 64, "updated_at": "2025-01-01T00:00:00Z"},
    ]
    audit = [
        {"audit_id": "audit_1", "user_id": "user_01", "event_time": "2025-06-01T00:00:00Z", "field_name": "email",
         "old_value": "lea.first@example.com", "new_value": "lea.mid@example.com"},
        {"audit_id": "audit_2", "user_id": "user_01", "event_time": "2026-03-01T00:00:00Z", "field_name": "email",
         "old_value": "lea.mid@example.com", "new_value": "lea.new@example.com"},
        {"audit_id": "audit_3", "user_id": "user_01", "event_time": "2026-03-01T00:00:00Z", "field_name": "email_hash",
         "old_value": H1, "new_value": H2},
        {"audit_id": "audit_4", "user_id": "user_01", "event_time": "2025-09-09T00:00:00Z", "field_name": "phone",
         "old_value": "+33 6 99 88 77 66", "new_value": "+33 6 11 22 33 44"},
    ]
    return IdentityGraph.from_rows(identities, audit)


def test_resolves_every_historical_and_current_identifier():
    g = small_graph()
    for raw in ["lea.first@example.com", "LEA.MID@EXAMPLE.COM", "mailto:lea.new@example.com",
                "+33 6 99 88 77 66", "+33611223344", H1.upper(), H2, " USER_01 "]:
        assert g.resolve(raw) == "user_01", raw
    assert g.resolve("someone@else.com") is None


def test_profile_links_user_to_all_its_info_with_sources():
    p = small_graph().profile("user_01")
    assert p.name == "Léa Martin"
    got = {(a.kind, a.value, a.status, a.source) for a in p.attributes}
    assert ("email", "lea.first@example.com", "historical", "audit_1") in got
    assert ("email", "lea.mid@example.com", "historical", "audit_2") in got
    assert ("email", "lea.new@example.com", "current", "user_identities") in got
    assert ("email_hash", H1, "historical", "audit_3") in got
    assert ("phone", "tel:33699887766", "historical", "audit_4") in got
    assert ("user_id", "user_01", "current", "user_identities") in got
    assert p.identifiers() >= {"lea.first@example.com", "lea.mid@example.com", "lea.new@example.com", H1, H2,
                               "tel:33699887766", "tel:33611223344", "user_01"}


def test_validity_windows_follow_the_audit_timeline():
    g = small_graph()
    assert "lea.first@example.com" in g.identifiers_at("user_01", ts("2025-01-01T00:00:00Z"))
    mid = g.identifiers_at("user_01", ts("2025-12-01T00:00:00Z"))
    assert "lea.mid@example.com" in mid and "lea.first@example.com" not in mid
    now = g.identifiers_at("user_01", ts("2026-06-01T00:00:00Z"))
    assert {"lea.new@example.com", H2, "tel:33611223344"} <= now
    assert not ({"lea.mid@example.com", H1, "tel:33699887766"} & now)


def test_shared_identifier_is_ambiguous_not_guessed():
    rows = [{"user_id": "user_1", "name": "A", "email": "a@x.com", "phone": "+1 555 000 1234", "email_hash": "", "updated_at": ""},
            {"user_id": "user_2", "name": "B", "email": "b@x.com", "phone": "+1-555-000-1234", "email_hash": "", "updated_at": ""}]
    g = IdentityGraph.from_rows(rows, [])
    assert g.resolve("+15550001234") is None
    assert g.ambiguous() == {"tel:15550001234": {"user_1", "user_2"}}
    assert g.resolve("a@x.com") == "user_1"


def test_profile_serialises_for_traces():
    d = small_graph().profile("user_01").to_dict()
    assert d["user_id"] == "user_01" and any(a["source"] == "audit_2" for a in d["attributes"])


# ------------------------------------------------------------------ real dataset
def dataset_graph():
    ds = ROOT / "dataset"
    return IdentityGraph.from_rows(read_csv(ds / "user_identities.csv"), read_csv(ds / "user_audit_log.csv"))


def test_every_request_email_resolves_to_exactly_one_user():
    g = dataset_graph()
    ds = ROOT / "dataset"
    reqs = read_csv(ds / "deletion_requests.csv") + read_csv(ds / "sample_deletion_requests.csv")
    unresolved = [r["case_id"] for r in reqs if g.resolve(r["email"]) is None]
    assert unresolved == []


def test_dataset_has_no_shared_identifiers():
    assert dataset_graph().ambiguous() == {}


def test_case_29_old_email_and_hash_link_through_audit_rows():
    p = dataset_graph().profile("user_29")
    sources = {a.source for a in p.attributes if a.status == "historical"}
    assert {"audit_284", "audit_285"} <= sources
    assert dataset_graph().resolve("NOAH.F438085@OUTLOOK.COM") == "user_29"


def test_sha256_of_any_known_email_resolves_even_without_an_audit_hash_row():
    import hashlib
    g = small_graph()                      # lea.first@ is historical; its hash was never logged in the audit rows
    assert g.resolve(hashlib.sha256(b"lea.first@example.com").hexdigest()) == "user_01"
    assert g.resolve(hashlib.sha256(b"someone@else.com").hexdigest()) is None
