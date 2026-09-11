#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for which config keys an input may set in the splatToolbox container's environment.

``set_config_parameters`` exports every key of ``{**input_configuration, **asset_metadata}`` that the
runtime allowlist names. The allowlist is upstream ``src/config.json``, and it names the container's
own control-plane keys alongside the reconstruction settings: ``LOCAL_DEBUG`` turns off the input
download and every output upload while the run still exits 0, ``CODE_PATH`` moves the directory
``main.py`` resolves its scripts and checkpoints against, and ``TASK_TOKEN`` is the workflow callback.
Both sources are operator-supplied — asset metadata needs only metadata-write permission on the asset
— so the launch sequence keeps its own reserved set rather than depending on the synced allowlist.
"""

import json
import os

import pytest

_ALLOWLIST = {
    "CODE_PATH": "/opt/ml/code",
    "DATASET_PATH": "/opt/ml/input/data/train",
    "MODEL_PATH": "/opt/ml/input/data/model",
    "LOCAL_DEBUG": "False",
    "TASK_TOKEN": "",
    "S3_INPUT": "",
    "S3_OUTPUT": "",
    "UUID": "",
    "FILENAME": "",
    "MAX_STEPS": "15000",
    "RECON_SOFTWARE_NAME": "glomap",
}


@pytest.fixture
def allowlist_cwd(tmp_path, monkeypatch):
    """A working directory holding a ``config.json`` allowlist, as the image's CODE_PATH does.

    ``set_config_parameters`` opens ``config.json`` relative to the process's working directory, which
    in the image is ``/opt/ml/code`` and holds the file the Dockerfile staged from upstream.
    """
    (tmp_path / "config.json").write_text(json.dumps(_ALLOWLIST), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def restored_environ():
    """The process environment, restored afterwards.

    ``set_config_parameters`` assigns into ``os.environ`` directly, which ``monkeypatch`` cannot undo.
    """
    saved = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.mark.unit
class TestReservedConfigKeys:
    def test_the_reserved_set_is_not_empty(self, container_main):
        """Guards every loop below from passing by iterating nothing."""
        assert container_main.RESERVED_CONFIG_KEYS

    def test_no_reserved_key_can_be_set_from_asset_metadata(
            self, container_main, allowlist_cwd, restored_environ):
        """The failure this closes: a metadata key named LOCAL_DEBUG makes the run skip its input
        download and every upload, exit 0, and report success having written nothing."""
        reserved = sorted(container_main.RESERVED_CONFIG_KEYS)
        for key in reserved:
            os.environ.pop(key, None)

        container_main.set_config_parameters({}, {key: "SET-BY-METADATA" for key in reserved})

        leaked = [key for key in reserved if os.environ.get(key) == "SET-BY-METADATA"]
        assert leaked == []

    def test_no_reserved_key_can_be_set_from_an_input_configuration(
            self, container_main, allowlist_cwd, restored_environ):
        """A template config body is the other input to the same merge, and an operator may edit it
        per run (both shipped templates set ``allowCustomEdit``)."""
        reserved = sorted(container_main.RESERVED_CONFIG_KEYS)
        for key in reserved:
            os.environ.pop(key, None)

        container_main.set_config_parameters({key: "SET-BY-PARAMS" for key in reserved}, {})

        leaked = [key for key in reserved if os.environ.get(key) == "SET-BY-PARAMS"]
        assert leaked == []

    def test_an_ordinary_reconstruction_setting_is_still_exported(
            self, container_main, allowlist_cwd, restored_environ):
        """The deny list must not close the feature it protects: settings are how a template works."""
        os.environ.pop("MAX_STEPS", None)
        os.environ.pop("RECON_SOFTWARE_NAME", None)

        container_main.set_config_parameters(
            {"MAX_STEPS": 30000}, {"RECON_SOFTWARE_NAME": "colmap"})

        assert os.environ["MAX_STEPS"] == "30000"
        assert os.environ["RECON_SOFTWARE_NAME"] == "colmap"

    def test_a_key_outside_the_allowlist_is_still_ignored(
            self, container_main, allowlist_cwd, restored_environ):
        """Asset metadata carries arbitrary operator keys, so an unknown key stays a skip rather than
        an error — a run must not fail because an asset also records, say, a part number."""
        os.environ.pop("SOME_UNRELATED_ASSET_KEY", None)

        container_main.set_config_parameters({}, {"SOME_UNRELATED_ASSET_KEY": "12"})

        assert "SOME_UNRELATED_ASSET_KEY" not in os.environ

    def test_the_reserved_keys_are_in_the_runtime_allowlist_that_ships(
            self, container_main, container_dir):
        """What makes the deny list load-bearing: these keys really are in the file the image stages,
        so without it an input value for them would be exported.

        ``src/`` is gitignored and arrives from the upstream sync, so it is absent from a fresh
        checkout and this test reports that rather than failing on it.
        """
        upstream_allowlist = os.path.join(container_dir, "src", "config.json")
        if not os.path.exists(upstream_allowlist):
            pytest.skip("upstream src/config.json is not present in this checkout")

        with open(upstream_allowlist, "r", encoding="utf-8") as handle:
            allowlist_keys = set(json.load(handle).keys())

        control_plane = {"CODE_PATH", "LOCAL_DEBUG", "TASK_TOKEN"}
        assert control_plane <= allowlist_keys
        assert control_plane <= set(container_main.RESERVED_CONFIG_KEYS)
