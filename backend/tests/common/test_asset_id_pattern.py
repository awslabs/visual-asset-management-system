# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shared asset ID rule stays permissive; a new asset ID uses one set everywhere.

ASSET_ID is validated on every read path, so an id an earlier release stored
(ending in '#', '%', ')' or a symbol) has to keep matching it. The narrower
last-character rule applies only where a new asset id is chosen.
"""

import pytest

from common.validators import asset_id_regex, new_asset_id_regex

# Characters the shared rule allowed only because its last position was broader
# than its body: they are not in [\w.,'-].
REFUSED_LAST_CHARACTERS = ["tail!", "tail#", "tail%", "tail)", "tail™"]


@pytest.mark.parametrize("value", REFUSED_LAST_CHARACTERS)
def test_the_existing_asset_id_rule_still_accepts_an_older_last_character(value):
    assert asset_id_regex.fullmatch(value) is not None


@pytest.mark.parametrize("value", REFUSED_LAST_CHARACTERS)
def test_the_new_asset_id_rule_refuses_them(value):
    assert new_asset_id_regex.fullmatch(value) is None


@pytest.mark.parametrize(
    "value",
    ["tail", "tail-name", "tail_name", "tail.name", "tail,name", "tail'name", "tailé"],
)
def test_the_new_asset_id_rule_accepts_the_allowed_last_characters(value):
    assert new_asset_id_regex.fullmatch(value) is not None


@pytest.mark.parametrize("value", ["tail ", "tail."])
def test_both_rules_refuse_a_trailing_space_or_dot(value):
    assert asset_id_regex.fullmatch(value) is None
    assert new_asset_id_regex.fullmatch(value) is None


def test_the_read_path_validator_still_accepts_a_stored_id_ending_in_hash():
    """A lookup keeps the rule the id was created under; only creation is narrower."""
    from common.validators import validate_asset_id

    valid, message = validate_asset_id("assetId", "part#")

    assert valid, message
