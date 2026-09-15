#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for how the splatToolbox container loads its runtime config allowlist.

``set_config_parameters`` opens ``config.json`` in the working directory to learn which keys of
``{**input_configuration, **asset_metadata}`` may be exported. That file is the only thing separating
a reconstruction setting from an arbitrary asset-metadata key, so when it cannot be read the launch
fails rather than exporting nothing: a run that silently ignored ``MAX_STEPS``,
``RECON_SOFTWARE_NAME`` and every other setting would still exit 0 and be recorded as complete, on a
GPU, with a default reconstruction the operator did not ask for. The Batch job runs
``python __main__.py`` with no wrapper, so the exception is the non-zero exit the workflow's Batch
catch hands to ``pipelineEnd``.
"""

import json
import os

import pytest

_ALLOWLIST = {
    "MAX_STEPS": "15000",
    "RECON_SOFTWARE_NAME": "glomap",
}


@pytest.fixture
def restored_environ():
    """The process environment, restored afterwards.

    ``set_config_parameters`` assigns into ``os.environ`` directly, which ``monkeypatch`` cannot undo.
    """
    saved = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def working_dir(tmp_path, monkeypatch):
    """An empty working directory, standing in for the image's CODE_PATH."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.unit
class TestConfigAllowlistLoad:
    def test_a_missing_allowlist_fails_the_launch_naming_the_path(
            self, container_main, working_dir, restored_environ):
        """The failure this closes: no ``config.json`` in the working directory dropped every
        parameter with one warning line, and the run went on to report success."""
        os.environ.pop("MAX_STEPS", None)
        expected_path = os.path.join(os.getcwd(), "config.json")
        assert not os.path.exists(expected_path)

        with pytest.raises(container_main.ConfigAllowlistError) as raised:
            container_main.set_config_parameters({"MAX_STEPS": 30000}, {})

        message = str(raised.value)
        assert expected_path in message
        assert "No such file" in message
        assert isinstance(raised.value.__cause__, FileNotFoundError)
        assert "MAX_STEPS" not in os.environ

    def test_a_malformed_allowlist_fails_the_launch_naming_the_path(
            self, container_main, working_dir, restored_environ):
        (working_dir / "config.json").write_text("{not json", encoding="utf-8")
        os.environ.pop("MAX_STEPS", None)

        with pytest.raises(container_main.ConfigAllowlistError) as raised:
            container_main.set_config_parameters({"MAX_STEPS": 30000}, {})

        assert os.path.join(os.getcwd(), "config.json") in str(raised.value)
        assert isinstance(raised.value.__cause__, ValueError)
        assert "MAX_STEPS" not in os.environ

    def test_an_allowlist_that_is_not_an_object_fails_the_launch(
            self, container_main, working_dir, restored_environ):
        """A JSON array parses, but has no keys to allow."""
        (working_dir / "config.json").write_text(json.dumps(["MAX_STEPS"]), encoding="utf-8")
        os.environ.pop("MAX_STEPS", None)

        with pytest.raises(container_main.ConfigAllowlistError) as raised:
            container_main.set_config_parameters({"MAX_STEPS": 30000}, {})

        assert os.path.join(os.getcwd(), "config.json") in str(raised.value)
        assert "MAX_STEPS" not in os.environ

    def test_a_readable_allowlist_still_exports_its_keys(
            self, container_main, working_dir, restored_environ):
        """The control for the arms above: an assertion that the load raises is satisfiable by a
        function that always raises, so the file-present path must still export."""
        (working_dir / "config.json").write_text(json.dumps(_ALLOWLIST), encoding="utf-8")
        for key in ("MAX_STEPS", "RECON_SOFTWARE_NAME", "SOME_UNRELATED_ASSET_KEY"):
            os.environ.pop(key, None)

        container_main.set_config_parameters(
            {"MAX_STEPS": 30000, "SOME_UNRELATED_ASSET_KEY": "12"},
            {"RECON_SOFTWARE_NAME": "colmap"})

        assert os.environ["MAX_STEPS"] == "30000"
        assert os.environ["RECON_SOFTWARE_NAME"] == "colmap"
        assert "SOME_UNRELATED_ASSET_KEY" not in os.environ
