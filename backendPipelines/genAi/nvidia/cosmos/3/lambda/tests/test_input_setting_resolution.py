#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for manifestHelper.resolve_input_setting: CONFIG-FIRST with an ASSET-METADATA fallback.

The precedence matters and used to be the other way round. A prompt (or seed, guidance, frame count)
typed on the execute screen as a template's dynamic tag must beat a value saved on the asset earlier —
otherwise a stale asset value silently wins and the run ignores what the operator just entered. When
the configuration leaves the field blank, the asset's metadata supplies it, so an asset can still
carry a standing default.

Only assetMetadata is consulted by default: these settings describe the RUN, not one file, and a
workflow may select many files.
"""

import os
import sys

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

import manifestHelper  # noqa: E402


def _md(asset=None, file=None):
    vams = {}
    if asset is not None:
        vams["assetMetadata"] = asset
    if file is not None:
        vams["fileMetadata"] = file
    return {"VAMS": vams}


@pytest.mark.unit
class TestResolveInputSetting:
    def test_configuration_wins_over_asset_metadata(self):
        got = manifestHelper.resolve_input_setting(
            {"PROMPT": "from execute screen"}, _md(asset={"COSMOS3_PROMPT": "saved on asset"}),
            ("PROMPT", "prompt"), "COSMOS3_PROMPT")
        assert got == "from execute screen"

    def test_asset_metadata_supplies_a_blank_configuration_field(self):
        for blank in ("", "   ", None):
            got = manifestHelper.resolve_input_setting(
                {"PROMPT": blank}, _md(asset={"COSMOS3_PROMPT": "saved on asset"}),
                ("PROMPT", "prompt"), "COSMOS3_PROMPT")
            assert got == "saved on asset", f"blank={blank!r}"

    def test_a_missing_configuration_key_falls_back(self):
        got = manifestHelper.resolve_input_setting(
            {"OTHER": "x"}, _md(asset={"COSMOS3_PROMPT": "saved"}), ("PROMPT",), "COSMOS3_PROMPT")
        assert got == "saved"

    def test_lower_case_alias_is_accepted(self):
        got = manifestHelper.resolve_input_setting(
            {"prompt": "lower"}, _md(asset={"COSMOS3_PROMPT": "saved"}),
            ("PROMPT", "prompt"), "COSMOS3_PROMPT")
        assert got == "lower"

    def test_first_matching_config_key_wins(self):
        got = manifestHelper.resolve_input_setting(
            {"PROMPT": "canonical", "prompt": "alias"}, {}, ("PROMPT", "prompt"), "COSMOS3_PROMPT")
        assert got == "canonical"

    def test_file_metadata_is_ignored_by_default(self):
        """These settings describe the run, not a file — and a workflow may select many files, so a
        per-file value would be ambiguous."""
        got = manifestHelper.resolve_input_setting(
            {}, _md(file={"COSMOS3_PROMPT": "on the file"}), ("PROMPT",), "COSMOS3_PROMPT")
        assert got == ""

    def test_file_metadata_is_used_when_explicitly_requested(self):
        got = manifestHelper.resolve_input_setting(
            {}, _md(file={"COSMOS3_PROMPT": "on the file"}), ("PROMPT",), "COSMOS3_PROMPT",
            metadata_scopes=("fileMetadata", "assetMetadata"))
        assert got == "on the file"

    def test_json_strings_are_accepted_for_both_sources(self):
        got = manifestHelper.resolve_input_setting(
            '{"PROMPT": "from json config"}', '{"VAMS": {"assetMetadata": {"COSMOS3_PROMPT": "md"}}}',
            ("PROMPT",), "COSMOS3_PROMPT")
        assert got == "from json config"
        got2 = manifestHelper.resolve_input_setting(
            "{}", '{"VAMS": {"assetMetadata": {"COSMOS3_PROMPT": "md"}}}',
            ("PROMPT",), "COSMOS3_PROMPT")
        assert got2 == "md"

    def test_neither_source_yields_empty_string(self):
        assert manifestHelper.resolve_input_setting({}, {}, ("PROMPT",), "COSMOS3_PROMPT") == ""
        assert manifestHelper.resolve_input_setting(None, None, ("PROMPT",), "COSMOS3_PROMPT") == ""

    def test_malformed_json_does_not_raise(self):
        """A truncated/corrupt config or metadata object must degrade to the other source rather than
        failing the launch."""
        got = manifestHelper.resolve_input_setting(
            "{not json", '{"VAMS": {"assetMetadata": {"COSMOS3_PROMPT": "md"}}}',
            ("PROMPT",), "COSMOS3_PROMPT")
        assert got == "md"
        assert manifestHelper.resolve_input_setting({"PROMPT": "cfg"}, "{not json",
                                                    ("PROMPT",), "COSMOS3_PROMPT") == "cfg"

    def test_a_non_string_configuration_value_passes_through(self):
        """Numeric settings (seed, frame count) must not be stringified or dropped."""
        assert manifestHelper.resolve_input_setting(
            {"SEED": 42}, {}, ("SEED",), "COSMOS3_SEED") == 42
        assert manifestHelper.resolve_input_setting(
            {"SEED": 0}, _md(asset={"COSMOS3_SEED": 7}), ("SEED",), "COSMOS3_SEED") == 0

    def test_a_single_config_key_may_be_passed_as_a_string(self):
        assert manifestHelper.resolve_input_setting(
            {"PROMPT": "p"}, {}, "PROMPT", "COSMOS3_PROMPT") == "p"
