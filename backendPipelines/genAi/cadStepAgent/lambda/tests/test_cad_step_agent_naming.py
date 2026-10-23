# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Output file-name rules for the GenAI CAD STEP agent pipeline (spec section 7)."""

import datetime
import hashlib
import importlib.util
import os
import sys

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONTAINER_COPY = os.path.join(
    os.path.dirname(_LAMBDA_DIR), "container", "cad_step_agent", "cad_step_naming.py")


def _load():
    spec = importlib.util.spec_from_file_location(
        "cad_step_agent_naming_undertest", os.path.join(_LAMBDA_DIR, "cad_step_naming.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


naming = _load()
NOW = datetime.datetime(2026, 9, 22, 18, 55, 0)


@pytest.mark.unit
class TestModifyMode:
    def test_default_keeps_the_input_name_and_extension(self):
        assert naming.resolve_output_filename("modify", "bracket.stp") == "bracket.stp"
        assert naming.resolve_output_filename("modify", "Bracket.STEP") == "Bracket.step"

    def test_prefix_stacks_on_the_input_name(self):
        assert naming.resolve_output_filename(
            "modify", "bracket.stp", output_filename_prefix="v2-") == "v2-bracket.stp"

    def test_override_replaces_the_base_name_but_the_input_extension_wins(self):
        assert naming.resolve_output_filename(
            "modify", "bracket.stp", output_filename="mount.glb") == "mount.stp"
        assert naming.resolve_output_filename(
            "modify", "bracket.stp", output_filename="mount") == "mount.stp"

    def test_prefix_stacks_on_an_override(self):
        assert naming.resolve_output_filename(
            "modify", "bracket.stp", output_filename="mount.step",
            output_filename_prefix="agent_") == "agent_mount.stp"

    def test_a_modify_run_needs_an_input_name(self):
        with pytest.raises(naming.OutputNameError):
            naming.resolve_output_filename("modify", "")


@pytest.mark.unit
class TestGenerateMode:
    def test_design_name_slug_plus_timestamp(self):
        assert naming.resolve_output_filename(
            "generate", design_name="Jetson Nano board", now=NOW) == "jetson-nano-board-20260922-185500.step"

    def test_prompt_slug_when_no_design_name(self):
        name = naming.resolve_output_filename(
            "generate", prompt="Create a 3D model of an NVIDIA Jetson Nano carrier board", now=NOW)
        assert name == "create-a-3d-model-of-an-20260922-185500.step"

    def test_fallback_base_name(self):
        assert naming.resolve_output_filename("generate", now=NOW) == "cad-agent-20260922-185500.step"

    def test_override_extension_stp_is_honoured_and_other_extensions_replaced(self):
        assert naming.resolve_output_filename("generate", output_filename="board.stp") == "board.stp"
        assert naming.resolve_output_filename("generate", output_filename="board.obj") == "board.step"
        assert naming.resolve_output_filename("generate", output_filename="board") == "board.step"

    def test_prefix_with_override(self):
        assert naming.resolve_output_filename(
            "generate", output_filename="board.step", output_filename_prefix="gen-") == "gen-board.step"


@pytest.mark.unit
class TestSanitizing:
    def test_directory_components_are_stripped_from_an_override(self):
        assert naming.resolve_output_filename(
            "generate", output_filename="../../etc/passwd") == "passwd.step"
        assert naming.resolve_output_filename(
            "modify", "a.stp", output_filename="C:\\nested\\name.stp") == "name.stp"

    def test_an_override_that_sanitizes_to_nothing_is_rejected(self):
        with pytest.raises(naming.OutputNameError):
            naming.resolve_output_filename("generate", output_filename="///")
        with pytest.raises(naming.OutputNameError):
            naming.resolve_output_filename("generate", output_filename_prefix="...")

    def test_a_name_vams_rejects_is_refused_before_compute(self):
        with pytest.raises(naming.OutputNameError):
            naming.resolve_output_filename("modify", "a.stp", output_filename="bad<name>")
        with pytest.raises(naming.OutputNameError):
            naming.resolve_output_filename("modify", "a.stp", output_filename_prefix="x" * 260)

    def test_unknown_mode_is_rejected(self):
        with pytest.raises(naming.OutputNameError):
            naming.resolve_output_filename("transmute", "a.stp")


@pytest.mark.unit
class TestRelativeSubdir:
    def test_sliced_at_the_threaded_asset_id(self):
        assert naming.relative_subdir_of("xabc/assetA/sub/dir/file.stp", "assetA") == "sub/dir/"
        assert naming.relative_subdir_of("xabc/assetA/file.stp", "assetA") == ""

    def test_asset_id_not_in_key_yields_root(self):
        assert naming.relative_subdir_of("xabc/other/file.stp", "assetA") == ""


@pytest.mark.unit
def test_the_container_copy_is_byte_identical():
    """The Lambda pre-check and the container's placement must agree; both read the same module."""
    def digest(path):
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    assert os.path.isfile(_CONTAINER_COPY), _CONTAINER_COPY
    assert digest(os.path.join(_LAMBDA_DIR, "cad_step_naming.py")) == digest(_CONTAINER_COPY)
