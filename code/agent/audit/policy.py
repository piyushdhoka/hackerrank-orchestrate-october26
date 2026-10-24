"""Policy: data category per resource, required action per category (deletion_rules.csv),
and category-aware readings of an observation (identifiable? enabled? restorable?)."""
from __future__ import annotations

from collections import Counter

from ..evidence import Observation
from ..utils import EMAIL_RE, PHONE_RE, parse_ts, resource_prefix

DEFAULT_CATEGORY = {
    "account": "USER_PROFILE",
    "search": "SEARCH_INDEX_ENTRY",
    "analytics": "ANALYTICS_PROFILE",
    "support": "SUPPORT_MESSAGE",
    "export": "USER_EXPORT",
    "data_flow": "DATA_FLOW",
    "backup": "BACKUP_ARCHIVE",
    "audit_record": "SECURITY_AUDIT_LOG",
    "tombstone": "ACCOUNT_TOMBSTONE",
    "legal_hold": "LEGAL_HOLD_RECORD",
    "marketing": "MARKETING_PROFILE",
    "object": "SUPPORT_ATTACHMENT",
    "email_delivery": "EMAIL_DELIVERY_LOG",
    "cache": "CACHE_ENTRY",
    "telemetry": "PRODUCT_TELEMETRY",
}
# Explicit labels that are not rows of deletion_rules.csv but behave like one.
PSEUDO_ACTIONS = {"DATA_FLOW": "DISABLE", "BACKUP_ARCHIVE": "BLOCK_RESTORE"}

# Fields that make a record attributable, per category. Stable references that the rules allow
# to remain (support customer_reference, tombstone subject_reference) are deliberately absent.
PII_KEYS = {
    "ANALYTICS_PROFILE": {"user_id", "customer_reference", "email", "phone", "email_hash", "customer_email"},
    "PRODUCT_TELEMETRY": {"user_id", "customer_reference", "email", "phone", "email_hash"},
    "SUPPORT_MESSAGE": {"requester_name", "requester_email", "requester_phone", "email", "phone", "name", "full_name"},
    "ACCOUNT_TOMBSTONE": {"name", "email", "phone", "email_hash", "full_name"},
    "EMAIL_DELIVERY_LOG": {"email", "recipient", "recipient_email", "to"},
}
FREE_TEXT_KEYS = {"message_text", "message_excerpt", "body", "text"}


def action_for(category: str, rules: dict) -> str:
    if category in PSEUDO_ACTIONS:
        return PSEUDO_ACTIONS[category]
    rule = rules.get(category)
    return rule["required_action"] if rule else "DELETE"


def resolve_category(resource_id: str, observations: list[Observation], worker_ops: list[str]) -> tuple[str, str]:
    """Return (category, basis). Explicit labels beat the deletion worker's attempted
    operation, which beats the resource-id prefix default."""
    explicit = Counter(o.category for o in observations if o.category)
    if explicit:
        # handover / ledger / record_type labels are the most specific statements available
        return explicit.most_common(1)[0][0], "explicit_label"
    prefix = resource_prefix(resource_id)
    ops = set(worker_ops)
    if prefix == "analytics" and "DELETE_RECORD" in ops and "ANONYMIZE_RECORD" not in ops:
        return "SECONDARY_IDENTIFIER", "worker_operation_hint"
    if prefix == "support" and "DELETE_RECORD" in ops and "REDACT_RECORD" not in ops:
        return "SUPPORT_ATTACHMENT", "worker_operation_hint"
    return DEFAULT_CATEGORY.get(prefix, "USER_PROFILE"), "prefix_default"


def _truthy(v) -> bool | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "enabled", "active"):
        return True
    if s in ("0", "false", "no", "disabled", "stopped", "inactive"):
        return False
    return None


def identifiable(category: str, o: Observation) -> bool | None:
    if o.status == "IDENTIFIABLE":
        return True
    if o.status in ("ANONYMIZED", "REDACTED"):
        return False
    f = o.fields
    if f.get("matched_value"):
        return True
    if "direct_identifiers" in f and f["direct_identifiers"] is not None:
        try:
            return int(f["direct_identifiers"]) > 0
        except ValueError:
            pass
    keys = PII_KEYS.get(category, set())
    present = [k for k in f if k.rsplit(".", 1)[-1] in keys]
    if any(f[k] is not None for k in present):
        return True
    for k in f:
        if k.rsplit(".", 1)[-1] in FREE_TEXT_KEYS and isinstance(f[k], str):
            if EMAIL_RE.search(f[k]) or PHONE_RE.search(f[k]):
                return True
    if present:
        return False
    if any(str(f.get(k, "")).strip() == "[REDACTED]" for k in FREE_TEXT_KEYS):
        return False
    return None


RUNNING_WORDS = {"healthy", "active", "running", "enabled", "live", "delivering", "syncing"}
STOPPED_WORDS = {"paused", "stopped", "disabled", "inactive", "suspended"}


def enabled(o: Observation) -> bool | None:
    if o.status == "ACTIVE":
        return True
    if o.status == "DISABLED":
        return False
    explicit = _truthy(o.fields.get("enabled"))
    if explicit is not None:
        return explicit
    word = str(o.fields.get("status") or "").strip().lower()   # console status text, e.g. "Healthy"
    if word in RUNNING_WORDS:
        return True
    if word in STOPPED_WORDS:
        return False
    return None


def restorable(o: Observation) -> bool | None:
    if o.status == "ACTIVE":
        return True
    if o.status == "BLOCKED":
        return False
    f = o.fields
    if f.get("restore_allowed") is not None:
        return _truthy(f["restore_allowed"])
    if str(f.get("decision", "")).lower() == "deny":
        return False
    if f.get("eligible") is not None:
        elig, block = _truthy(f.get("eligible")), _truthy(f.get("deletion_block"))
        if elig is not None:
            return bool(elig and not block)
    return None


def retained_until(o: Observation):
    return parse_ts(o.fields.get("retention_until") or o.fields.get("retained_until"))
