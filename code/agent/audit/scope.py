"""Which resources belong to which case (README §3.2: linking, worker batch), with provenance for every link.

Provenance tags:
  identifier_link     subject identifier in a record dated inside [requested_at, audit_at], source captured by audit_at
  catalog_link        LINK_RESOURCE_IDENTITY activity naming the subject, inside the window
  inner_time_link     identifier link whose record time is in-window but whose file was captured after audit_at
  owner_batch         deletion-worker action near reported_at on a resource that some evidence (any time) ties to the subject
  batch_attribution   deletion-worker action near reported_at on a resource no evidence ties to anyone; assigned by
                      elimination, then numeric case order (a last resort, counted in reports)
Only identifier_link / catalog_link may support FINDINGs; the rest can add verification targets only.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

from ..data import Activity, Case, Dataset
from ..identity import IdentityProfile as Subject
from ..evidence import Observation
from ..utils import canonical_system, resource_prefix

BATCH_WINDOW = timedelta(hours=2)
STATUS_FOR_RESULT = {"SUCCESS": "COMPLETE", "PARTIAL": "PARTIALLY_COMPLETE", "FAILED": "NOT_STARTED"}
FINDING_GRADE = {"identifier_link", "catalog_link", "lineage_identifier_link"}
WORKER_ACTIONS = {"DELETE_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD", "DISABLE_DATA_FLOW", "BLOCK_RESTORE",
                  "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS", "RETAIN_RECORD"}


@dataclass
class ScopedResource:
    resource_id: str
    system: str
    provenance: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)

    @property
    def finding_grade(self) -> bool:
        return bool(self.provenance & FINDING_GRADE)


class Scoper:
    def __init__(self, ds: Dataset, observations: list[Observation], strict: bool):
        self.ds, self.strict = ds, strict
        self.by_identifier: dict[str, list[Observation]] = defaultdict(list)
        self.by_resource: dict[str, list[Observation]] = defaultdict(list)
        for o in observations:
            self.by_resource[o.resource_id].append(o)
            for i in o.ids:
                self.by_identifier[i].append(o)
        # resource -> users tied to it by ANY evidence at ANY time (used only to keep batch
        # attribution from handing someone else's record to a case)
        self.owners: dict[str, set[str]] = defaultdict(set)
        for o in observations:
            for i in o.ids:
                users = ds.identity.owners(i)
                if len(users) == 1:
                    self.owners[o.resource_id] |= users
        self.worker: dict[str, list[Activity]] = defaultdict(list)
        for a in ds.activities:
            if a.source_system == "deletion_worker" and a.event_type in WORKER_ACTIONS:
                self.worker[a.resource_id].append(a)

    def worker_ops(self, rid: str) -> list[str]:
        return [a.event_type for a in self.worker.get(rid, [])]

    # ------------------------------------------------------------ identifier links
    def linked(self, case: Case, subject: Subject) -> dict[str, ScopedResource]:
        out: dict[str, ScopedResource] = {}
        for ident in subject.identifiers():
            for o in self.by_identifier.get(ident, []):
                if not (case.requested_at <= o.t <= case.audit_at):
                    continue
                if o.source_kind == "activity":
                    tag = "catalog_link"
                elif o.manifest_at <= case.audit_at:
                    tag = "identifier_link"
                elif not self.strict:
                    tag = "inner_time_link"
                else:
                    continue
                sr = out.setdefault(o.resource_id, ScopedResource(o.resource_id, o.system))
                sr.provenance.add(tag)
                sr.sources.add(o.source)
        return out

    # ------------------------------------------------------------ batch attribution
    def _batch_acts(self, case: Case) -> list[Activity]:
        acts = [a for items in self.worker.values() for a in items
                if abs(a.t - case.reported_at) <= BATCH_WINDOW and a.t <= case.audit_at]
        return sorted(acts, key=lambda a: (a.t, a.activity_id))

    def owner_batch(self, case: Case, subject: Subject, scope: dict[str, ScopedResource]) -> None:
        if self.strict:
            return
        for a in self._batch_acts(case):
            if subject.user_id in self.owners.get(a.resource_id, ()):
                sr = scope.setdefault(a.resource_id, ScopedResource(a.resource_id, canonical_system(a.resource_id)))
                sr.provenance.add("owner_batch")
                sr.sources.add(a.activity_id)

    def assign_orphans(self, cases: list[Case], scopes: dict[str, dict[str, ScopedResource]],
                       has_findings: dict[str, bool]) -> list[dict]:
        """Hand worker-touched records that no evidence ties to anyone to the cases in the same batch.
        Every case gets at most one record per resource type. Primary accounts go to any case lacking one
        (every authorised case has exactly one primary account in all 32 samples); other types only go to
        cases without findings (in samples, unlinked anonymised records only ever belong to compliant
        cases). Ties: orphans by action time are matched to candidate cases in numeric case order."""
        log: list[dict] = []
        if self.strict:
            return log
        orphans: list[tuple[str, Activity]] = []
        for rid, acts in self.worker.items():
            if not self.owners.get(rid):
                orphans.append((rid, min(acts, key=lambda a: (a.t, a.activity_id))))
        orphans.sort(key=lambda x: (x[1].t, x[1].activity_id))
        ordered_cases = sorted(cases, key=lambda c: c.number)
        # files holding each case's identifier-linked records: an orphan read back in the same file
        # (e.g. a null-profile 404 next to the case's audit record) is being verified for that case
        case_files = {c.case_id: {s for sr in scopes[c.case_id].values() if sr.finding_grade for s in sr.sources
                                  if s.startswith("evidence_")} for c in cases}
        orphan_files = {rid: {o.source for o in self.by_resource.get(rid, []) if o.source.startswith("evidence_")}
                        for rid, _ in orphans}

        def eligible(rid, act, c):
            if abs(act.t - c.reported_at) > BATCH_WINDOW or act.t > c.audit_at:
                return False
            prefix = resource_prefix(rid)
            if prefix in {resource_prefix(r) for r in scopes[c.case_id]}:
                return False
            return prefix == "account" or not has_findings.get(c.case_id)

        def compatible(rid, act, c):
            # the workflow's reported status mirrors its primary-profile deletion result (all samples);
            # the signal says nothing about other resource types
            if resource_prefix(rid) != "account":
                return False
            return STATUS_FOR_RESULT.get(act.result) == c.reported_status

        def co_observed(rid, act, c):
            return bool(orphan_files[rid] & case_files[c.case_id])

        passes = [("coobserved+status", lambda *x: compatible(*x) and co_observed(*x)),
                  ("status", compatible), ("coobserved", co_observed), ("case_order", lambda *x: True)]
        assigned: set[str] = set()
        for name, pred in passes:
            for rid, act in orphans:
                if rid in assigned:
                    continue
                cands = [c for c in ordered_cases if eligible(rid, act, c) and pred(rid, act, c)]
                if not cands:
                    continue
                chosen = cands[0]
                sr = scopes[chosen.case_id].setdefault(rid, ScopedResource(rid, canonical_system(rid)))
                sr.provenance.update({"batch_attribution", f"batch_pass:{name}"})
                sr.sources.add(act.activity_id)
                assigned.add(rid)
                log.append({"resource_id": rid, "activity_id": act.activity_id, "case_id": chosen.case_id,
                            "pass": name, "candidates": [c.case_id for c in cands]})
        return log
