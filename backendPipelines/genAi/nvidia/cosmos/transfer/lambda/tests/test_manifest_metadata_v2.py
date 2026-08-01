#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the v2 grouped-by-asset metadata read path in the vendored pipeline manifestHelper
(WB7). ``fetch_metadata`` returns the v2 body as-is while still unwrapping the legacy v1 envelope;
``get_asset_file_record`` / ``asset_metadata_for`` / ``file_metadata_for`` / ``file_attributes_for``
pull specific records; ``to_legacy_vams_view`` projects either envelope version onto the legacy
``{"VAMS": {...}}`` shape every pipeline reader already digs into; ``resolved_file_key`` derives the
per-file metadata key from the resolved manifest.

The envelope shape mirrors backend ``executionRecords.build_grouped_metadata_envelope`` exactly:
``{"schemaVersion": 2, "assets": [ {databaseId, assetId, assetData, files: [ {fileKey, metadata,
attributes?} ]} ]}`` — asset-level metadata is the fileKey '/' record; per-file metadata/attributes
are per-file records keyed by the normalized relative file key.

Every pipeline vendors its own copy of ``manifestHelper.py``, so a final check asserts the copies
are byte-identical to this one — the read path exercised here is then the one they all run."""

import os
import sys
import json
import glob
import types
import hashlib
from unittest.mock import MagicMock

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

if "customLogging" not in sys.modules:
    _cl_pkg = types.ModuleType("customLogging")
    _cl_logger = types.ModuleType("customLogging.logger")
    _cl_logger.safeLogger = lambda **kw: MagicMock()
    _cl_pkg.logger = _cl_logger
    sys.modules["customLogging"] = _cl_pkg
    sys.modules["customLogging.logger"] = _cl_logger

import manifestHelper as mh  # noqa: E402


def _v2_envelope():
    """A grouped envelope with two assets, asset-level + per-file metadata + file attributes."""
    return {
        "schemaVersion": 2,
        "assets": [
            {
                "databaseId": "db1", "assetId": "assetA",
                "assetData": {"assetName": "A", "description": "", "tags": []},
                "files": [
                    {"fileKey": "/", "metadata": {"COSMOS_TRANSFER_CONTROL_TYPE": "edge"}},
                    {"fileKey": "/clips/in.mp4",
                     "metadata": {"COSMOS_TRANSFER_PROMPT": "make it snow"},
                     "attributes": {"fps": "30"}},
                ],
            },
            {
                "databaseId": "db2", "assetId": "assetB",
                "assetData": {"assetName": "B", "description": "", "tags": []},
                "files": [
                    {"fileKey": "/", "metadata": {"PART": "pump"}},
                ],
            },
        ],
    }


def _s3_returning(body_obj):
    """A MagicMock s3 client whose get_object returns the given JSON body."""
    s3 = MagicMock()
    payload = MagicMock()
    payload.read.return_value = json.dumps(body_obj).encode("utf-8")
    s3.get_object.return_value = {"Body": payload}
    return s3


@pytest.mark.unit
class TestFetchMetadataEnvelopeVersions:
    def test_v2_body_returned_as_is(self):
        s3 = _s3_returning(_v2_envelope())
        body = mh.fetch_metadata(s3, "s3://b/metadata.json")
        assert body.get("schemaVersion") == 2 and "assets" in body

    def test_v1_envelope_still_unwrapped(self):
        s3 = _s3_returning({"schemaVersion": 1, "metadata": {"VAMS": {"assetMetadata": {"K": "v"}}}})
        body = mh.fetch_metadata(s3, "s3://b/metadata.json")
        assert body == {"VAMS": {"assetMetadata": {"K": "v"}}}

    def test_empty_location(self):
        assert mh.fetch_metadata(MagicMock(), "") == {}


@pytest.mark.unit
class TestRecordAccessors:
    def test_get_asset_file_record_asset_level(self):
        rec = mh.get_asset_file_record(_v2_envelope(), "db1", "assetA", "/")
        assert rec["metadata"]["COSMOS_TRANSFER_CONTROL_TYPE"] == "edge"

    def test_get_asset_file_record_normalizes_key(self):
        # A caller passing the un-slashed key resolves the same '/clips/in.mp4' record.
        rec = mh.get_asset_file_record(_v2_envelope(), "db1", "assetA", "clips/in.mp4")
        assert rec["metadata"]["COSMOS_TRANSFER_PROMPT"] == "make it snow"

    def test_get_asset_file_record_absent(self):
        assert mh.get_asset_file_record(_v2_envelope(), "db1", "assetA", "/nope.mp4") is None
        assert mh.get_asset_file_record(_v2_envelope(), "dbX", "assetX", "/") is None

    def test_asset_metadata_for(self):
        assert mh.asset_metadata_for(_v2_envelope(), "db2", "assetB") == {"PART": "pump"}

    def test_file_metadata_and_attributes_for(self):
        env = _v2_envelope()
        assert mh.file_metadata_for(env, "db1", "assetA", "/clips/in.mp4") == {"COSMOS_TRANSFER_PROMPT": "make it snow"}
        assert mh.file_attributes_for(env, "db1", "assetA", "/clips/in.mp4") == {"fps": "30"}

    def test_accessors_v1_fallback(self):
        v1 = {"VAMS": {"assetMetadata": {"A": "1"}, "fileMetadata": {"F": "2"}, "fileAttributes": {"T": "3"}}}
        assert mh.asset_metadata_for(v1, "db", "a") == {"A": "1"}
        assert mh.file_metadata_for(v1, "db", "a", "/x") == {"F": "2"}
        assert mh.file_attributes_for(v1, "db", "a", "/x") == {"T": "3"}


@pytest.mark.unit
class TestToLegacyVamsView:
    def test_v2_projected_to_legacy_shape(self):
        view = mh.to_legacy_vams_view(_v2_envelope(), "db1", "assetA", "/clips/in.mp4")
        assert view["VAMS"]["assetMetadata"] == {"COSMOS_TRANSFER_CONTROL_TYPE": "edge"}
        assert view["VAMS"]["fileMetadata"] == {"COSMOS_TRANSFER_PROMPT": "make it snow"}
        assert view["VAMS"]["fileAttributes"] == {"fps": "30"}

    def test_v2_asset_level_only_when_file_key_is_root(self):
        view = mh.to_legacy_vams_view(_v2_envelope(), "db2", "assetB", "/")
        assert view["VAMS"]["assetMetadata"] == {"PART": "pump"}
        assert view["VAMS"]["fileMetadata"] == {}
        assert view["VAMS"]["fileAttributes"] == {}

    def test_v1_passthrough_unchanged(self):
        v1 = {"VAMS": {"assetMetadata": {"K": "v"}}}
        assert mh.to_legacy_vams_view(v1, "db", "a", "/x") == v1

    def test_non_dict_yields_empty(self):
        assert mh.to_legacy_vams_view("", "db", "a", "/") == {}
        assert mh.to_legacy_vams_view(None) == {}


@pytest.mark.unit
class TestResolvedFileKey:
    def test_from_manifest_first_input_file(self):
        resolved = {"inputFiles": [{"relativePath": "/clips/in.mp4"}]}
        assert mh.resolved_file_key(resolved) == "/clips/in.mp4"

    def test_normalizes_unslashed(self):
        resolved = {"inputFiles": [{"relativePath": "clips/in.mp4"}]}
        assert mh.resolved_file_key(resolved) == "/clips/in.mp4"

    def test_no_manifest_defaults_to_asset_level(self):
        assert mh.resolved_file_key({"inputFiles": []}) == "/"
        assert mh.resolved_file_key({}) == "/"


def _pipelines_root():
    path = _LAMBDA_DIR
    while os.path.basename(path) != "backendPipelines":
        parent = os.path.dirname(path)
        if parent == path:
            pytest.skip("backendPipelines root not found")
        path = parent
    return path


def _helper_digest(path):
    with open(path, "r", encoding="utf-8", newline=None) as fh:
        return hashlib.sha256(fh.read().encode("utf-8")).hexdigest()


@pytest.mark.unit
class TestVendoredHelperCopiesMatch:
    def test_every_pipeline_helper_is_identical(self):
        canonical = os.path.join(_LAMBDA_DIR, "manifestHelper.py")
        copies = sorted(glob.glob(os.path.join(_pipelines_root(), "**", "lambda", "manifestHelper.py"),
                                  recursive=True))
        assert len(copies) > 1, "expected the vendored helper in multiple pipelines"
        expected = _helper_digest(canonical)
        drifted = [c for c in copies if _helper_digest(c) != expected]
        assert drifted == [], f"vendored manifestHelper.py copies drifted from {canonical}: {drifted}"
