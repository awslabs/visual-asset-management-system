# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The VAMS user -> EDH uid derivation, pinned by known answers.

EDH stores desktops, directory entries and quotas against the uid, so the mapping must never drift:
a different digest, alphabet, prefix or length orphans every EDH user a deployment has created.
The values below were computed from ``"v" + base32(sha256(userId))[:15].lower()`` and are asserted
as literals rather than recomputed with the same code. Case and surrounding whitespace are NOT
folded here (``request_to_claims`` already NFKC-normalizes and preserves case), so ids that differ
only in those map to different EDH users -- as they are different VAMS users.
"""

import re

import pytest

from backend.backend.handlers.addon.edh.common import edhIdentity

EDH_UID_SHAPE = re.compile(r"^v[a-z2-7]{15}$")

KNOWN_ANSWERS = {
    "alice": "vfpmansl7byak6gq",
    "Alice": "vhpcrayuxhrcy2wt",
    "alice@example.com": "v76gzqgp4byjl6dj",
    "a" * 256: "vallrmdlx4gggir5",
}


@pytest.mark.unit
class TestEdhUidDerivation:
    @pytest.mark.parametrize("user_id,expected", sorted(KNOWN_ANSWERS.items()))
    def test_known_answers(self, user_id, expected):
        assert edhIdentity.edh_uid_for(user_id) == expected

    @pytest.mark.parametrize("user_id", sorted(KNOWN_ANSWERS))
    def test_shape_is_v_plus_fifteen_base32_lowercase(self, user_id):
        uid = edhIdentity.edh_uid_for(user_id)
        assert EDH_UID_SHAPE.fullmatch(uid), uid
        assert len(uid) == 16

    def test_case_is_not_folded(self):
        assert edhIdentity.edh_uid_for("alice") != edhIdentity.edh_uid_for("Alice")

    def test_surrounding_whitespace_is_not_trimmed(self):
        # " alice" is a different (invalid upstream) id; the derivation does not repair it.
        assert edhIdentity.edh_uid_for(" alice") == "v4uguykrj5kpys3a"
        assert edhIdentity.edh_uid_for(" alice") != KNOWN_ANSWERS["alice"]

    def test_a_compatibility_spelling_is_not_folded_here(self):
        # NFKC folding is request_to_claims' job; a fullwidth letter reaching this function is a
        # different id and must not silently share an EDH user with the ASCII spelling.
        assert edhIdentity.edh_uid_for("ａlice") == "vzg7z63h4thx5agn"
        assert edhIdentity.edh_uid_for("ａlice") != KNOWN_ANSWERS["alice"]

    def test_is_deterministic_across_calls(self):
        assert edhIdentity.edh_uid_for("alice") == edhIdentity.edh_uid_for("alice")


@pytest.mark.unit
class TestEdhPrincipalRefusals:
    @pytest.mark.parametrize("user_id", ["", None, 42])
    def test_empty_or_non_string_ids_are_refused(self, user_id):
        with pytest.raises(ValueError):
            edhIdentity.edh_uid_for(user_id)
        with pytest.raises(ValueError):
            edhIdentity.display_name_for(user_id)

    def test_the_system_identity_is_never_an_edh_principal(self):
        with pytest.raises(ValueError):
            edhIdentity.edh_uid_for("SYSTEM_USER")
        with pytest.raises(ValueError):
            edhIdentity.display_name_for("SYSTEM_USER")
        assert edhIdentity.SYSTEM_USER_ID == "SYSTEM_USER"

    def test_a_lowercase_spelling_of_the_system_identity_is_an_ordinary_user(self):
        # Control: the refusal compares the exact reserved string, as every handler does.
        assert EDH_UID_SHAPE.fullmatch(edhIdentity.edh_uid_for("system_user"))


@pytest.mark.unit
class TestDisplayName:
    def test_display_name_is_the_vams_user_id_verbatim(self):
        assert edhIdentity.display_name_for("alice@example.com") == "alice@example.com"
        assert edhIdentity.display_name_for("Alice") == "Alice"
