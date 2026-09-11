#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the splatToolbox container's asset-metadata read.

The input-metadata file is the grouped envelope (``{"schemaVersion": 2, "assets": [...]}``) whose
asset-level settings live in each group's ``fileKey`` "/" record, with database metadata in its own
top-level section. Every value an operator saved on the asset — MODEL, MAX_STEPS, ... — reaches the
run only through that record, so reading the envelope as a flat map applies nothing and the job
silently runs on container defaults. The legacy ``{"VAMS": {...}}`` view must keep resolving.
"""

import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock

import pytest


def _grouped_envelope(assets, databases=None):
    envelope = {"schemaVersion": 2, "assets": assets}
    if databases:
        envelope["databases"] = databases
    return envelope


def _asset_group(database_id, asset_id, asset_metadata, files=None):
    records = [{"fileKey": "/", "metadata": asset_metadata}]
    records.extend(files or [])
    return {"databaseId": database_id, "assetId": asset_id, "assetData": {}, "files": records}


@pytest.fixture(scope="module")
def main_module():
    """The container entry module, loaded by file (its name is ``__main__.py``).

    Its ``vams_utils`` / ``boto3`` imports are stubbed: this test covers the pure metadata read and
    must not need the container's AWS dependencies installed.
    """
    container_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    stubbed = {}
    for name in ("boto3", "vams_utils", "vams_utils.manifest_io"):
        if name not in sys.modules:
            stubbed[name] = MagicMock()
    sys.modules.update(stubbed)
    try:
        spec = importlib.util.spec_from_file_location(
            "splat_container_main_metadata", os.path.join(container_dir, "__main__.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name in stubbed:
            sys.modules.pop(name, None)
    return module


@pytest.mark.unit
class TestResolveAssetMetadata:
    def test_grouped_envelope_asset_settings_are_applied(self, main_module):
        envelope = _grouped_envelope([
            _asset_group("smoke-db", "xabc123", {"MODEL": "splatfacto-big", "MAX_STEPS": "30000"},
                         files=[{"fileKey": "/video.mp4", "metadata": {"MODEL": "ignored"},
                                 "attributes": {}}]),
        ], databases=[{"databaseId": "smoke-db", "metadata": {"MODEL": "also-ignored"}}])
        assert main_module.resolve_asset_metadata(envelope) == {
            "MODEL": "splatfacto-big", "MAX_STEPS": "30000"}

    def test_grouped_envelope_keys_never_leak_as_settings(self, main_module):
        """The envelope's own structural keys are not config: applying them set nothing at all."""
        resolved = main_module.resolve_asset_metadata(
            _grouped_envelope([_asset_group("smoke-db", "xabc123", {"MODEL": "splatfacto-big"})]))
        assert set(resolved) & {"schemaVersion", "assets", "databases"} == set()

    def test_settings_reach_the_environment(self, main_module, monkeypatch, tmp_path, capsys):
        """End of the chain: a value saved on the asset becomes the env var main.py reads."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.json").write_text(json.dumps({"MODEL": "splatfacto", "MAX_STEPS": "15000"}))
        monkeypatch.delenv("MODEL", raising=False)
        envelope = _grouped_envelope(
            [_asset_group("smoke-db", "xabc123", {"MODEL": "splatfacto-big"})])
        main_module.set_config_parameters({"MAX_STEPS": "15000"},
                                         main_module.resolve_asset_metadata(envelope))
        assert os.environ["MODEL"] == "splatfacto-big"
        assert os.environ["MAX_STEPS"] == "15000"

    def test_legacy_vams_view_still_resolves(self, main_module):
        legacy = {"VAMS": {"assetMetadata": {"MODEL": "splatfacto-big"},
                           "fileMetadata": {"MODEL": "ignored"},
                           "databaseMetadata": {"MODEL": "also-ignored"}}}
        assert main_module.resolve_asset_metadata(legacy) == {"MODEL": "splatfacto-big"}

    def test_an_unenveloped_body_is_taken_as_the_settings_map(self, main_module):
        assert main_module.resolve_asset_metadata({"MODEL": "splatfacto-big"}) == {
            "MODEL": "splatfacto-big"}

    def test_several_assets_apply_nothing_and_say_so(self, main_module, capsys):
        """Two assets leave no way to tell whose setting a value is, and that must be visible."""
        envelope = _grouped_envelope([
            _asset_group("smoke-db", "xabc123", {"MODEL": "splatfacto-big"}),
            _asset_group("smoke-db", "xdef456", {"MODEL": "nerfacto"}),
        ])
        assert main_module.resolve_asset_metadata(envelope) == {}
        assert "names 2 assets" in capsys.readouterr().out

    def test_a_missing_asset_level_record_is_reported(self, main_module, capsys):
        envelope = _grouped_envelope([
            {"databaseId": "smoke-db", "assetId": "xabc123", "assetData": {},
             "files": [{"fileKey": "/video.mp4", "metadata": {"MODEL": "ignored"}}]},
        ])
        assert main_module.resolve_asset_metadata(envelope) == {}
        assert "no asset-level record" in capsys.readouterr().out

    def test_an_unreadable_metadata_file_yields_no_settings(self, main_module):
        for body in ({}, None, "", []):
            assert main_module.resolve_asset_metadata(body) == {}
