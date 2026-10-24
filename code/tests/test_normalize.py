"""Text normalisation for identifiers: each test names the variant it guards against."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import normalize as N  # noqa: E402

HASH = "b1d07882c40af50936b997d454d5c6518ec22754eedbbf86ca9f6a3c30cc4cf0"


@pytest.mark.parametrize("raw", [
    "noah.f438085@outlook.com",
    "  Noah.F438085@Outlook.COM ",          # case + padding (20 eval requests are uppercase)
    "mailto:<noah.f438085@outlook.com>",    # mail-client formatting
    "\"noah.f438085@outlook.com\".",        # quoted, trailing sentence period
    "noah.f438085​@outlook.com",        # zero-width space pasted from a web form
    "ｎｏａｈ.ｆ438085＠ｏｕｔｌｏｏｋ.ｃｏｍ",  # full-width characters
])
def test_email_variants_normalise_to_one_form(raw):
    assert N.norm_email(raw) == "noah.f438085@outlook.com"


def test_invalid_email_is_rejected():
    assert N.norm_email("not an email") is None
    assert N.norm_email("") is None


@pytest.mark.parametrize("raw", ["+1 (312) 555-0129", "+1-312-555-0129", "+13125550129", "0013125550129"])
def test_phone_formats_normalise_to_digits(raw):
    assert N.norm_phone(raw) == "tel:13125550129"


def test_short_numbers_are_not_phones():
    assert N.norm_phone("+12 34") is None


def test_hash_case_and_prefix():
    assert N.norm_hash(HASH.upper()) == HASH
    assert N.norm_hash("sha256:" + HASH) == HASH
    assert N.norm_hash(HASH[:-1]) is None


def test_user_id_case_and_padding_preserved():
    assert N.norm_user_id(" USER_29 ") == "user_29"
    assert N.norm_user_id("user_01") == "user_01"      # dataset uses zero padding; user_01 != user_1
    assert N.norm_user_id("user29") is None


def test_names_fold_accents_case_and_spacing():
    assert N.norm_name("Mateo Ríos") == N.norm_name("mateo   RIOS") == "mateo rios"
    assert N.norm_name("Léonie Girard") == "leonie girard"


def test_clean_text_removes_invisible_characters():
    assert N.clean_text("  a​b﻿   c ") == "ab c"


def test_identifiers_in_free_text():
    text = f"Contact O.ERIKSEN10@YAHOO.COM or +46 70 144 08 06; ref USER_485; hash {HASH.upper()}"
    assert N.identifiers_in(text) == {"o.eriksen10@yahoo.com", "tel:46701440806", "user_485", HASH}


def test_identifiers_in_does_not_shorten_lookalike_emails():
    # g.silva76.jr@ is a different person from g.silva76@ (sample case_28 decoy pattern)
    assert N.identifiers_in("hit: g.silva76.jr@gmail.com") == {"g.silva76.jr@gmail.com"}


def test_identifiers_in_full_width_email():
    assert N.identifiers_in("担当: ｈａｒｕｋａ.ｓ1838＠ｚｏｈｏ.ｃｏｍ") == {"haruka.s1838@zoho.com"}
