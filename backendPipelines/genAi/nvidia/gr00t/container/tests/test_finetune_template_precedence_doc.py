#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The shipped fine-tune template tells an operator the precedence the container actually applies.

``resolve_config`` merges ``gr00t_config.json`` from the asset OVER the configuration supplied on the
execute screen, so a stale but well-formed copy of that file in the asset decides the run while the
screen shows the values the operator typed. ``inputInstructions`` is the text rendered on that screen
(``web/src/features/orchestration/components/InstructionsPanel``), which makes it the one place the
override can be stated where the person it affects will read it.

Both halves are asserted together, in one file, because the hazard is DIVERGENCE: a test on the
template alone goes stale the moment the merge order changes, and a test on the merge order alone
says nothing about what the operator was told. The merge order is exercised against the real
``resolve_config`` rather than read from the docstring.

The module is named for this suite alone: every pipeline ships a top-level ``__main__.py`` and a
sibling suite loading one by path under a shared alias would silently assert against another
pipeline's file.
"""

import importlib.util
import json
import os
import sys

import pytest

_CONTAINER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PIPELINE_DIR = os.path.dirname(_CONTAINER_DIR)
_FINETUNE_TEMPLATE = os.path.join(
    _PIPELINE_DIR, "vamsSchema", "templates", "gr00t-finetune-default.json")

if _CONTAINER_DIR not in sys.path:
    sys.path.insert(0, _CONTAINER_DIR)


def _load_container_entrypoint():
    """``__main__.py`` loaded from its path under an alias private to this suite."""
    spec = importlib.util.spec_from_file_location(
        "gr00t_container_entrypoint_precedence_doc", os.path.join(_CONTAINER_DIR, "__main__.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


container = _load_container_entrypoint()


def _instructions():
    with open(_FINETUNE_TEMPLATE, encoding="utf-8") as handle:
        return json.load(handle)["inputInstructions"]


@pytest.mark.unit
class TestTheContainerReallyRanksTheAssetFileFirst:
    """The behaviour the text has to describe, taken from the code rather than from its docstring."""

    def _resolve(self, tmp_path, asset_file_config, lambda_config):
        (tmp_path / "gr00t_config.json").write_text(
            json.dumps(asset_file_config), encoding="utf-8")
        return container.resolve_config(
            {"gr00tConfig": json.dumps(lambda_config)}, tmp_path)

    def test_the_asset_file_overrides_the_execute_screen_configuration(self, tmp_path):
        resolved = self._resolve(tmp_path, {"maxSteps": 20000}, {"maxSteps": 6000})
        assert resolved["maxSteps"] == 20000

    def test_the_execute_screen_configuration_wins_where_the_asset_file_is_silent(self, tmp_path):
        """CONTROL on the direction of the override: it is per-key, not wholesale, so a value the
        asset file does not mention still comes from the screen."""
        resolved = self._resolve(tmp_path, {"maxSteps": 20000}, {"loraRank": 32})
        assert (resolved["maxSteps"], resolved["loraRank"]) == (20000, 32)

    def test_with_no_asset_file_the_screen_configuration_stands(self, tmp_path):
        resolved = container.resolve_config({"gr00tConfig": json.dumps({"maxSteps": 6000})}, tmp_path)
        assert resolved["maxSteps"] == 6000


@pytest.mark.unit
class TestTheTemplateSaysSo:
    """What the operator is shown at the moment they supply the configuration."""

    def test_the_instructions_name_the_asset_config_file(self):
        assert "gr00t_config.json" in _instructions()

    def test_the_instructions_rank_it_above_the_template_configuration(self):
        """The ordering, not merely a mention: the file has to be listed ahead of this template's own
        configuration, which is what an operator needs in order to act on it."""
        text = _instructions()
        assert "Precedence" in text
        file_position = text.index("gr00t_config.json")
        template_position = text.index("This template's configuration")
        assert file_position < template_position, (
            "the asset config file must be listed above the template's configuration")

    def test_the_instructions_say_what_to_do_about_a_stale_file(self):
        text = _instructions()
        assert "Delete or update it" in text

    def test_the_instructions_still_fit_the_field_bound(self):
        """``inputInstructions`` is capped at 4096 characters by
        ``models.pipelines.CreateTemplateRequestModel``, and the CDK registration replays this
        template through that model — so an over-long value fails the deployment's pipeline import,
        not this file."""
        assert len(_instructions()) <= 4096

    def test_the_metadata_names_are_not_offered_as_the_file_keys(self):
        """The file's keys are the configuration field names, and the metadata block above it lists
        GROOT_* names — an operator copying the wrong set gets a file that silently sets nothing."""
        text = _instructions()
        assert "not the GROOT_* metadata names" in text
