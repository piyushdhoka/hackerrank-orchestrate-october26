"""Reconstruct each resource's state at audit_at from observations (never from activity claims)."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import policy
from ..data import Case
from ..evidence import Observation
from ..utils import canonical_system

# action -> (reading function name, value that means "violation")
READINGS = {
    "REDACT": "identifiable",
    "ANONYMIZE": "identifiable",
    "DISABLE": "enabled",
    "BLOCK_RESTORE": "restorable",
}


@dataclass
class ResourceState:
    resource_id: str
    system: str
    category: str
    category_basis: str
    action: str
    exists: bool | None = None
    reading: bool | None = None        # identifiable / enabled / restorable depending on action
    violation: bool = False
    known: bool = False                # False -> state had to be assumed
    violation_obs: Observation | None = None
    compliant_obs: Observation | None = None
    notes: list[str] = field(default_factory=list)
    roles: dict = field(default_factory=dict)

    def summary(self) -> dict:
        def ref(o):
            return None if o is None else {"source": o.source, "t": o.t.isoformat(), "via": o.via, "role": o.role}
        return {
            "category": self.category, "category_basis": self.category_basis, "action": self.action,
            "exists": self.exists, "reading": self.reading, "violation": self.violation, "known": self.known,
            "violation_evidence": ref(self.violation_obs), "compliant_evidence": ref(self.compliant_obs),
            "roles": self.roles, "notes": self.notes,
        }


def knowable(o: Observation, case: Case, strict: bool) -> bool:
    """Observation usable when deciding this case. Gold-compatible mode uses the record-level clock;
    strict mode additionally requires the source itself to exist by audit_at."""
    if not (case.requested_at <= o.t <= case.audit_at):
        return False
    return o.manifest_at <= case.audit_at if strict else True


def citable(o: Observation, case: Case) -> bool:
    return o.citable and o.manifest_at <= case.audit_at


def _read(action: str, category: str, o: Observation) -> bool | None:
    name = READINGS.get(action)
    if name == "identifiable":
        return policy.identifiable(category, o)
    if name == "enabled":
        return policy.enabled(o)
    if name == "restorable":
        return policy.restorable(o)
    return None


def _latest(obs: list[Observation]) -> Observation | None:
    return max(obs, key=lambda o: (o.t, o.manifest_at, o.source)) if obs else None


def _best_citation(obs: list[Observation], case: Case) -> Observation | None:
    """Latest manifest-admissible source showing the state (README §3.2: steps)."""
    c = [o for o in obs if citable(o, case)]
    return max(c, key=lambda o: (o.manifest_at, o.t, o.source)) if c else None


def assess(resource_id: str, all_obs: list[Observation], case: Case, rules: dict,
           worker_ops: list[str], strict: bool) -> ResourceState:
    category, basis = policy.resolve_category(resource_id, all_obs, worker_ops)
    action = policy.action_for(category, rules)
    st = ResourceState(resource_id, canonical_system(resource_id), category, basis, action)

    obs = [o for o in all_obs if o.exists is not None and knowable(o, case, strict)]
    by_role: dict[str, list[Observation]] = {}
    for o in obs:
        by_role.setdefault(o.role, []).append(o)
    latest = {role: _latest(items) for role, items in by_role.items()}
    st.roles = {r: {"exists": o.exists, "source": o.source, "t": o.t.isoformat()} for r, o in latest.items()}

    if any(o.exists for o in latest.values()):
        st.exists = True
    elif latest:
        st.exists = False

    if action in READINGS and st.exists:
        # per copy, the most recent observation that actually carries the reading
        readings = []
        for role, items in by_role.items():
            present = [o for o in items if o.exists]
            with_reading = [(o, _read(action, category, o)) for o in present]
            with_reading = [(o, r) for o, r in with_reading if r is not None]
            if with_reading:
                readings.append(max(with_reading, key=lambda x: (x[0].t, x[0].manifest_at))[1])
        if readings:
            st.reading = any(readings)

    # violation & citations
    if action == "DELETE":
        st.known = st.exists is not None
        st.violation = bool(st.exists)
        st.violation_obs = _best_citation([o for o in obs if o.exists], case)
        st.compliant_obs = _best_citation([o for o in obs if o.exists is False], case)
    elif action == "RETAIN":
        st.known = st.exists is not None
        st.compliant_obs = _best_citation([o for o in obs if o.exists], case)
        until = max((policy.retained_until(o) for o in all_obs if policy.retained_until(o)), default=None)
        if until and until < case.audit_at:
            st.notes.append(f"retention ended {until.isoformat()} before audit_at")
        if st.exists is False:
            st.notes.append("policy-required retained record observed missing (no operation exists for this)")
    else:
        if st.exists is False:
            st.known = True
            st.compliant_obs = _best_citation([o for o in obs if o.exists is False], case)
        else:
            if action == "BLOCK_RESTORE" and st.exists and st.reading is None and not strict:
                # restore is what a backup is for; blocking it is an explicit control that leaves evidence
                # (restore_allowed=false, decision=deny, BLOCKED). None observed -> the archive can be restored.
                st.reading = True
                st.notes.append("no restore control observed; present archive treated as restorable")
                st.known = True
                st.violation = True
                st.violation_obs = _best_citation([o for o in obs if o.exists], case)
                return st
            st.known = st.reading is not None
            st.violation = bool(st.reading)
            st.violation_obs = _best_citation(
                [o for o in obs if o.exists and _read(action, category, o) is True], case)
            st.compliant_obs = _best_citation(
                [o for o in obs if o.exists and _read(action, category, o) is False], case)
    return st
