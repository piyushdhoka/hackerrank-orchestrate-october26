"""Identity graph: every user_id linked to all of its identifiers, past and present.

Built from user_identities.csv (current snapshot) and user_audit_log.csv (field history). Each
identifier is an IdentityAttribute carrying where it came from (user_identities or the audit_id that
retired it) and when it was valid, so a 2026-03 log line can be matched against the email the person
actually used in 2026-03. Lookups go through agent.normalize, so any formatting of an identifier
(case, mailto:, phone punctuation, hash prefix, full-width text) resolves to the same node.
Identifiers shared by more than one user are reported as ambiguous and never resolved.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime

from . import normalize as N
from .utils import parse_ts

NORMALISERS = {"email": N.norm_email, "email_hash": N.norm_hash, "phone": N.norm_phone, "user_id": N.norm_user_id}
LINKABLE = ("user_id", "email", "email_hash", "phone")


@dataclass(frozen=True)
class IdentityAttribute:
    kind: str                     # user_id | email | email_hash | phone | name
    value: str                    # normalised form
    status: str                   # current | historical
    source: str                   # 'user_identities' or the audit_id that changed it
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    raw: str = ""

    def valid_at(self, when: datetime) -> bool:
        return (self.valid_from is None or self.valid_from <= when) and (self.valid_until is None or when < self.valid_until)


@dataclass
class IdentityProfile:
    user_id: str
    name: str
    updated_at: datetime | None
    attributes: list[IdentityAttribute] = field(default_factory=list)

    def identifiers(self) -> set[str]:
        return {a.value for a in self.attributes if a.kind in LINKABLE}

    def emails(self) -> set[str]:
        return {a.value for a in self.attributes if a.kind == "email"}

    def to_dict(self) -> dict:
        def enc(a):
            d = asdict(a)
            for k in ("valid_from", "valid_until"):
                d[k] = d[k].isoformat() if d[k] else None
            return d
        return {"user_id": self.user_id, "name": self.name,
                "updated_at": self.updated_at.isoformat() if self.updated_at else None,
                "attributes": [enc(a) for a in self.attributes]}


class IdentityGraph:
    def __init__(self):
        self.profiles: dict[str, IdentityProfile] = {}
        self._index: dict[str, set[str]] = defaultdict(set)

    @classmethod
    def from_rows(cls, identity_rows: list[dict], audit_rows: list[dict]) -> "IdentityGraph":
        g = cls()
        history: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for a in audit_rows:
            history[(a["user_id"], a["field_name"])].append(a)
        for r in identity_rows:
            uid = N.norm_user_id(r["user_id"])
            p = IdentityProfile(uid, r.get("name", ""), parse_ts(r.get("updated_at")))
            p.attributes.append(IdentityAttribute("user_id", uid, "current", "user_identities", raw=r["user_id"]))
            if N.norm_name(r.get("name")):
                p.attributes.append(IdentityAttribute("name", N.norm_name(r["name"]), "current", "user_identities",
                                                      raw=r["name"]))
            for kind in ("email", "email_hash", "phone"):
                events = sorted(history.get((r["user_id"], kind), []), key=lambda e: (e["event_time"], e["audit_id"]))
                norm = NORMALISERS[kind]
                prev_time = None
                for e in events:
                    t = parse_ts(e["event_time"])
                    if norm(e["old_value"]):
                        p.attributes.append(IdentityAttribute(kind, norm(e["old_value"]), "historical", e["audit_id"],
                                                              prev_time, t, e["old_value"]))
                    prev_time = t
                current = norm(r.get(kind))
                if events and norm(events[-1]["new_value"]) and norm(events[-1]["new_value"]) != current:
                    # history ends on a value the snapshot no longer shows: keep it, open-ended
                    p.attributes.append(IdentityAttribute(kind, norm(events[-1]["new_value"]), "historical",
                                                          events[-1]["audit_id"], prev_time, None, events[-1]["new_value"]))
                if current:
                    p.attributes.append(IdentityAttribute(kind, current, "current", "user_identities", prev_time, None,
                                                          r.get(kind, "")))
            # email_hash is sha256(lowercase email) in this dataset, so every known email (current or historical)
            # also identifies the user through its hash, even when the audit log never recorded that hash
            have = {a.value for a in p.attributes}
            for a in [a for a in p.attributes if a.kind == "email"]:
                h = hashlib.sha256(a.value.encode("utf-8")).hexdigest()
                if h not in have:
                    p.attributes.append(IdentityAttribute("email_hash", h, a.status, f"sha256({a.source})",
                                                          a.valid_from, a.valid_until, a.value))
            g.profiles[uid] = p
            for ident in p.identifiers():
                g._index[ident].add(uid)
        return g

    def resolve(self, raw) -> str | None:
        """user_id for any formatting of a known identifier; None if unknown or shared."""
        for kind in LINKABLE:
            v = NORMALISERS[kind](raw)
            if v and v in self._index:
                users = self._index[v]
                return next(iter(users)) if len(users) == 1 else None
        return None

    def owners(self, identifier: str) -> set[str]:
        """Users holding an already-normalised identifier (empty if unknown)."""
        return set(self._index.get(identifier, ()))

    def profile(self, user_id: str) -> IdentityProfile | None:
        return self.profiles.get(N.norm_user_id(user_id) or user_id)

    def identifiers_at(self, user_id: str, when: datetime) -> set[str]:
        p = self.profile(user_id)
        if not p:
            return set()
        return {a.value for a in p.attributes if a.kind in LINKABLE and a.valid_at(when)}

    def ambiguous(self) -> dict[str, set[str]]:
        return {i: set(u) for i, u in self._index.items() if len(u) > 1}
