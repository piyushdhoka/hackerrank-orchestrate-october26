"""Identifier text normalisation.

Every identifier is compared in one canonical form, whatever the source wrote:
  email   'mailto:<Noah.F438085@Outlook.COM>.'  -> 'noah.f438085@outlook.com'
  phone   '+1 (312) 555-0129' / '0013125550129' -> 'tel:13125550129'
  hash    'SHA256:B1D0…'                         -> 'b1d0…' (64 lowercase hex)
  user    ' USER_29 '                            -> 'user_29' (zero padding kept: user_01 is a real id)
  name    'Mateo Ríos'                           -> 'mateo rios' (display/diagnostics only: names repeat)
Text is NFKC-folded first, so full-width forms and zero-width characters pasted from forms disappear.
"""
from __future__ import annotations

import re
import unicodedata

INVISIBLE_RE = re.compile("[​‌‍⁠﻿]")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
USER_RE = re.compile(r"\buser_\d+\b", re.I)
HASH_RE = re.compile(r"\b[0-9a-fA-F]{64}\b")
PHONE_RE = re.compile(r"\+\d[\d\s().-]{6,}\d")


def clean_text(value):
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value))
    s = INVISIBLE_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_email(value):
    s = clean_text(value)
    if not s:
        return None
    m = EMAIL_RE.search(re.sub(r"(?i)^mailto:", "", s))
    return m.group(0).lower() if m else None


def norm_phone(value):
    s = clean_text(value)
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if s.startswith("00"):
        digits = digits[2:]      # international prefix written as 00
    return "tel:" + digits if len(digits) >= 8 else None


def norm_hash(value):
    s = clean_text(value)
    if not s:
        return None
    s = re.sub(r"(?i)^sha256:", "", s).lower()
    return s if re.fullmatch(r"[0-9a-f]{64}", s) else None


def norm_user_id(value):
    s = clean_text(value)
    if not s:
        return None
    s = s.lower()
    return s if re.fullmatch(r"user_\d+", s) else None


def norm_name(value):
    s = clean_text(value)
    if not s:
        return None
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", s.casefold()).strip()


def identifiers_in(value) -> set[str]:
    """All identifiers (normalised) found anywhere in a value."""
    s = clean_text(value)
    out: set[str] = set()
    if not s:
        return out
    out.update(e.lower() for e in EMAIL_RE.findall(s))
    out.update(u.lower() for u in USER_RE.findall(s))
    out.update(h.lower() for h in HASH_RE.findall(s))
    for p in PHONE_RE.findall(s):
        n = norm_phone(p)
        if n:
            out.add(n)
    return out
