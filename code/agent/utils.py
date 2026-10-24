"""Shared helpers: timestamps, identifier extraction, resource-id conventions."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .normalize import EMAIL_RE, PHONE_RE, identifiers_in, norm_phone  # noqa: F401  (re-exported)

TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")

RESOURCE_PREFIXES = (
    "account", "search", "analytics", "support", "export", "data_flow", "backup",
    "audit_record", "tombstone", "legal_hold", "marketing", "object", "email_delivery",
    "cache", "telemetry",
)
RESOURCE_RE = re.compile(r"\b(?:%s)_\d+\b" % "|".join(RESOURCE_PREFIXES))

# Logical owner system per resource prefix. Replica copies are reported against the logical
# system (case_05: a failover-replica copy of account_005 is an account_service finding).
SYSTEM_BY_PREFIX = {
    "account": "account_service",
    "search": "search_service",
    "analytics": "analytics_platform",
    "support": "support_platform",
    "export": "export_service",
    "data_flow": "integration_service",
    "backup": "backup_service",
    "audit_record": "security_service",
    "tombstone": "identity_service",
    "legal_hold": "legal_service",
    "marketing": "marketing_platform",
    "object": "object_storage",
    "email_delivery": "email_delivery_service",
    "cache": "cache_service",
    "telemetry": "telemetry_service",
}

NULL_STRINGS = {"", "null", "none", "nil", "—", "-"}


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    m = TS_RE.search(str(value))
    if not m:
        return None
    s = m.group(0)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif not re.search(r"[+-]\d{2}:\d{2}$", s):
        s += "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def iso(d: datetime | None) -> str | None:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def clean(value):
    """Normalise scalar values from any source: 'null'-like strings become None."""
    if isinstance(value, str):
        v = value.strip()
        return None if v.lower() in NULL_STRINGS else v
    return value


def resource_prefix(resource_id: str) -> str:
    return resource_id.rsplit("_", 1)[0]


def canonical_system(resource_id: str, observed: str | None = None) -> str:
    return SYSTEM_BY_PREFIX.get(resource_prefix(resource_id), observed or "unknown_system")


def case_number(case_id: str) -> int:
    m = re.search(r"(\d+)$", case_id)
    return int(m.group(1)) if m else 0
