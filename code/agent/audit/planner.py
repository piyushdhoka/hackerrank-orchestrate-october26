"""Turn assessed resources into the ordered steps_json (README §3.2: status, steps, order)."""
from __future__ import annotations

from ..data import Case
from .scope import ScopedResource
from .state import ResourceState

OPERATION_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
STATE_OF = {
    "REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE", "DISABLE_DATA_FLOW": "DISABLED", "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED", "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT", "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED", "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}
PHASE_OF = {op: ("FINDING" if op.startswith("REPORT_") else "VERIFICATION" if op.startswith("VERIFY_")
                 else "REMEDIATION") for op in OPERATION_ORDER}
# action -> (finding, remediation, verification)
TEMPLATES = {
    "DELETE": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "DISABLE": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "BLOCK_RESTORE": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    "RETAIN": (None, None, "VERIFY_RECORD_PRESENT"),
}


def sort_key(step: dict) -> tuple:
    return OPERATION_ORDER.index(step["operation"]), step["system_id"], step["resource_id"]


def plan_case(case: Case, items: list[tuple[ScopedResource, ResourceState]], strict: bool,
              omission_citation) -> tuple[str, list[dict], list[dict]]:
    """Return (status, steps, step_traces). step_traces carry provenance and are NOT part of output.csv."""
    findings, remediation, verification = [], [], []
    notes = []
    for sr, st in items:
        finding_op, remedy_op, verify_op = TEMPLATES[st.action]
        prov = sorted(sr.provenance)
        if not st.known:
            if strict:
                notes.append({"resource_id": sr.resource_id, "dropped": "state unknown in strict mode"})
                continue
            prov = prov + ["assumed_state"]
        if st.action != "DELETE" and st.action != "RETAIN" and st.exists is False:
            verify_op = "VERIFY_RECORD_ABSENT"
        if st.action == "RETAIN" and st.exists is False:
            notes.append({"resource_id": sr.resource_id, "dropped": "retained record missing; no operation"})
            continue
        base = {"system_id": st.system, "resource_id": sr.resource_id}
        if st.violation and finding_op:
            if sr.finding_grade and st.violation_obs is not None:
                findings.append(({**base, "operation": finding_op, "evidence_id": st.violation_obs.source},
                                 prov + [f"evidence:{st.violation_obs.via}"]))
                remediation.append(({**base, "operation": remedy_op, "evidence_id": ""}, prov))
            else:
                # Inferred links never produce findings (README §3.3). Keep the target state as a check.
                notes.append({"resource_id": sr.resource_id,
                              "unreported_violation": "no finding-grade link or no citable evidence"})
        verification.append(({**base, "operation": verify_op, "evidence_id": None}, prov, st))

    status = "INCOMPLETE" if findings else "COMPLETE"
    steps_with_prov = list(findings) + list(remediation)
    for step, prov, st in verification:
        if status == "INCOMPLETE":
            step["evidence_id"] = ""
        else:
            cite = st.compliant_obs.source if st.compliant_obs is not None else None
            if cite is None:
                cite = omission_citation(st)
                if not cite:
                    # a COMPLETE verification must cite what proves it; nothing admissible does
                    notes.append({"resource_id": step["resource_id"], "dropped": "no admissible evidence to verify"})
                    continue
                prov = prov + ["omission_citation"]
            step["evidence_id"] = cite
        steps_with_prov.append((step, prov))

    steps_with_prov.sort(key=lambda sp: sort_key(sp[0]))
    steps, traces = [], []
    for n, (step, prov) in enumerate(steps_with_prov, start=1):
        out = {"step_no": n, "phase": PHASE_OF[step["operation"]], "system_id": step["system_id"],
               "resource_id": step["resource_id"], "operation": step["operation"],
               "state": STATE_OF[step["operation"]], "evidence_id": step["evidence_id"]}
        steps.append(out)
        traces.append({**out, "provenance": prov})
    return status, steps, traces + ([{"notes": notes}] if notes else [])
