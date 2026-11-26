# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The asset ID rule uses the same character set in every position."""

import pytest

from common.validators import asset_id_regex


@pytest.mark.parametrize("value", ["tail!", "tail#", "mid!x", "tail#x", "tail "])
def test_asset_id_rejects_a_refused_character(value):
    assert asset_id_regex.fullmatch(value) is None


@pytest.mark.parametrize(
    "value",
    ["tail", "tail-name", "tail_name", "tail.name", "tail,name", "tail'name"],
)
def test_asset_id_accepts_the_allowed_characters(value):
    assert asset_id_regex.fullmatch(value) is not None
