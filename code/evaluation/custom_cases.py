"""Participant-created evaluation cases: eval requests whose answers were derived BY HAND from the evidence
(not from this pipeline's output). main.py scores the eval run against them; the reasoning is in `why`."""


def _s(n, op, system, rid, ev=""):
    phase = "FINDING" if op.startswith("REPORT_") else "VERIFICATION" if op.startswith("VERIFY_") else "REMEDIATION"
    state = {"REPORT_REMAINING_RECORD": "PRESENT", "REPORT_EXPOSED_PII": "PRESENT", "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
             "DISABLE_DATA_FLOW": "DISABLED", "DELETE_RECORD": "ABSENT", "REDACT_RECORD": "REDACTED",
             "ANONYMIZE_RECORD": "ANONYMIZED", "VERIFY_RECORD_ABSENT": "ABSENT", "VERIFY_RECORD_PRESENT": "PRESENT",
             "VERIFY_RECORD_REDACTED": "REDACTED", "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
             "VERIFY_DATA_FLOW_DISABLED": "DISABLED", "VERIFY_RESTORE_BLOCKED": "BLOCKED"}[op]
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": rid, "operation": op, "state": state,
            "evidence_id": ev}


CUSTOM_GOLD = {
    "case_33": {
        "status": "INCOMPLETE",
        "why": "PDF-only link. evidence_18 (account_history_report, captured 07-18 11:00, before audit 07-19 13:00) "
               "is statement NST-2026-0718-1842 for ingrid.dahl@outlook.com / user_18; activity_4009 catalogues that "
               "statement as export_1842. A user export must be deleted (rule_09) and it still exists at audit_at. "
               "No deletion-worker activity near reported_at, so no account step. Tests PDF text + catalog aliases.",
        "steps": [_s(1, "REPORT_REMAINING_RECORD", "export_service", "export_1842", "evidence_18"),
                  _s(2, "DELETE_RECORD", "export_service", "export_1842"),
                  _s(3, "VERIFY_RECORD_ABSENT", "export_service", "export_1842")],
    },
    "case_44": {
        "status": "COMPLETE",
        "why": "Shares the 09-15 deletion batch with sample case_03. evidence_94 (readback 09-26 10:38, before audit "
               "11:00) shows account_2064 looked up by charlotte.wright@outlook.com -> 404; audit_record_1105, "
               "data_flow_223 (enabled=false), support_1066 (requester fields null), backup_6266 (restore_allowed=false) "
               "all keyed by user_42. analytics_5589 is the batch's second anonymised profile (case_03 takes 9406), "
               "read back with all identifiers null. Tests batch attribution next to a sample case.",
        "steps": [_s(1, "VERIFY_RECORD_ABSENT", "account_service", "account_2064", "evidence_94"),
                  _s(2, "VERIFY_RECORD_PRESENT", "security_service", "audit_record_1105", "evidence_94"),
                  _s(3, "VERIFY_RECORD_REDACTED", "support_platform", "support_1066", "evidence_94"),
                  _s(4, "VERIFY_RECORD_ANONYMIZED", "analytics_platform", "analytics_5589", "evidence_94"),
                  _s(5, "VERIFY_DATA_FLOW_DISABLED", "integration_service", "data_flow_223", "evidence_94"),
                  _s(6, "VERIFY_RESTORE_BLOCKED", "backup_service", "backup_6266", "evidence_94")],
    },
    "case_54": {
        "status": "INCOMPLETE",
        "why": "Uppercase request email (BCARVALHO6517@GMAIL.COM) and Portuguese text. evidence_05 (read-replica "
               "capture 09-21 10:00 = audit_at, inclusive) holds user_52's search_3995, support_835 (message names the "
               "email), analytics_1738 (email/phone/hash) and data_flow_5080 (enabled, selector = uppercase email) in "
               "primary and replica, plus retained audit_record_6325. Primary account: account_6829, the batch's "
               "PARTIAL delete matching the PARTIALLY_COMPLETE report. Tests normalisation + replica capture.",
        "steps": [_s(1, "REPORT_REMAINING_RECORD", "search_service", "search_3995", "evidence_05"),
                  _s(2, "REPORT_EXPOSED_PII", "analytics_platform", "analytics_1738", "evidence_05"),
                  _s(3, "REPORT_EXPOSED_PII", "support_platform", "support_835", "evidence_05"),
                  _s(4, "REPORT_ACTIVE_DATA_FLOW", "integration_service", "data_flow_5080", "evidence_05"),
                  _s(5, "DISABLE_DATA_FLOW", "integration_service", "data_flow_5080"),
                  _s(6, "DELETE_RECORD", "search_service", "search_3995"),
                  _s(7, "REDACT_RECORD", "support_platform", "support_835"),
                  _s(8, "ANONYMIZE_RECORD", "analytics_platform", "analytics_1738"),
                  _s(9, "VERIFY_RECORD_ABSENT", "account_service", "account_6829"),
                  _s(10, "VERIFY_RECORD_ABSENT", "search_service", "search_3995"),
                  _s(11, "VERIFY_RECORD_PRESENT", "security_service", "audit_record_6325"),
                  _s(12, "VERIFY_RECORD_REDACTED", "support_platform", "support_835"),
                  _s(13, "VERIFY_RECORD_ANONYMIZED", "analytics_platform", "analytics_1738"),
                  _s(14, "VERIFY_DATA_FLOW_DISABLED", "integration_service", "data_flow_5080")],
    },
    "case_417": {
        "status": "INCOMPLETE",
        "why": "Korean request (no sample uses Korean). evidence_39 (syslog archive, captured 03-25 08:59:44, before "
               "audit 09:00): trace tr-0854521 looks up s.kim14@yahoo.com -> account_4921 rows=1 http 200 (present "
               "despite activity_805's FAILED delete); trace tr-5482781 index_query_started match_key=s.kim14@yahoo.com "
               "-> search_5479 result=hit; trace tr-5017172 subject_ref=user_415 -> audit_record_7959 rows=1. "
               "Found while hand-checking: match_key lookups were being dropped by the log parser (now fixed).",
        "steps": [_s(1, "REPORT_REMAINING_RECORD", "account_service", "account_4921", "evidence_39"),
                  _s(2, "REPORT_REMAINING_RECORD", "search_service", "search_5479", "evidence_39"),
                  _s(3, "DELETE_RECORD", "account_service", "account_4921"),
                  _s(4, "DELETE_RECORD", "search_service", "search_5479"),
                  _s(5, "VERIFY_RECORD_ABSENT", "account_service", "account_4921"),
                  _s(6, "VERIFY_RECORD_ABSENT", "search_service", "search_5479"),
                  _s(7, "VERIFY_RECORD_PRESENT", "security_service", "audit_record_7959")],
    },
    "case_501": {
        "status": "INCOMPLETE",
        "why": "Inventory SQL era (Feb). Request and evidence both use the UPPERCASE email O.ERIKSEN10@YAHOO.COM. "
               "evidence_63 (customer_data_inventory_20260213, captured 07:43:27, before audit 08:00): "
               "customer_profiles account_634 still present as DELETION_PENDING with the email although activity_174 "
               "claims SUCCESS; customer_search_documents search_2799 present. No security record for user_499.",
        "steps": [_s(1, "REPORT_REMAINING_RECORD", "account_service", "account_634", "evidence_63"),
                  _s(2, "REPORT_REMAINING_RECORD", "search_service", "search_2799", "evidence_63"),
                  _s(3, "DELETE_RECORD", "account_service", "account_634"),
                  _s(4, "DELETE_RECORD", "search_service", "search_2799"),
                  _s(5, "VERIFY_RECORD_ABSENT", "account_service", "account_634"),
                  _s(6, "VERIFY_RECORD_ABSENT", "search_service", "search_2799")],
    },
    "case_511": {
        "status": "INCOMPLETE",
        "why": "Replica drift (Feb). Request uses user_509's HISTORICAL email nicolas.f550081@outlook.com (audit_69). "
               "evidence_47 (reporting replica, captured 02-08 07:00 = audit_at, inclusive): account_1035 (ACTIVE, old "
               "email) exists only in customer_data_reporting_replica; activity_262 PARTIAL delete followed by "
               "activity_271 replication checkpoint. audit_record_4883 and legal_hold_4887 (payment dispute/tax record, "
               "retained until 2033: rule_13 RETAIN) are present in both copies.",
        "steps": [_s(1, "REPORT_REMAINING_RECORD", "account_service", "account_1035", "evidence_47"),
                  _s(2, "DELETE_RECORD", "account_service", "account_1035"),
                  _s(3, "VERIFY_RECORD_ABSENT", "account_service", "account_1035"),
                  _s(4, "VERIFY_RECORD_PRESENT", "legal_service", "legal_hold_4887"),
                  _s(5, "VERIFY_RECORD_PRESENT", "security_service", "audit_record_4883")],
    },
    "case_533": {
        "status": "NOT_APPLICABLE",
        "why": "'The mobile number on my profile is out of date. Please replace it with the new one and leave the "
               "account active.' A correction request, not deletion (AGENTS.md §6.5). Tests intent classification.",
        "steps": [],
    },
}
