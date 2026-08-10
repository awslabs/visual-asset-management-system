#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the meshCadMetadataExtraction lambda's manifest contract: single-input enforcement
(the pipeline is registered with inputFileArity 'one') and attribute-file keys named after the
input file's path WITHIN THE ASSET, so a shadowed step-2 input's attributes still apply to the
asset file rather than to the prior pipeline's output prefix."""

import os
import sys
import types
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub common.logger + metadata_extractors so the module imports without powertools / cadquery.
if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger
if "metadata_extractors" not in sys.modules:
    _extractors = types.ModuleType("metadata_extractors")
    _extractors.extract_cad_metadata = MagicMock(return_value={})
    _extractors.extract_mesh_metadata = MagicMock(return_value={"volume": 1.5})
    _extractors.get_handler_for_format = MagicMock(return_value="mesh")
    sys.modules["metadata_extractors"] = _extractors

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

_ASSET_BUCKET = "abkt"
_RUN_BUCKET = "obkt"
_ASSET_ROOT = "xd130a6d6/"
_OUTPUT_FILES_PREFIX = "pipelines/conv3dBasic/JOB/output/E1/files/"
_OUTPUT_METADATA_PREFIX = "pipelines/metaExtract/JOB/output/E1/metadata/"


def _load():
    """Load the pipeline module from its 'lambda.py' file name (not a valid module name)."""
    spec = importlib.util.spec_from_file_location(
        "meshcad_pipeline_lambda", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _manifest(input_files):
    return {
        "inputFiles": input_files,
        "outputs": {"bucket": _RUN_BUCKET, "metadata": _OUTPUT_METADATA_PREFIX},
    }


def _original_entry(relative_path="/test/pump.stl"):
    """An unshadowed (step 1) input file: located under the asset root in the asset bucket."""
    return {
        "relativePath": relative_path,
        "databaseId": "db1",
        "assetId": "xd130a6d6",
        "assetRootS3Key": _ASSET_ROOT,
        "bucket": _ASSET_BUCKET,
        "key": _ASSET_ROOT + relative_path.lstrip("/"),
    }


def _shadowed_entry(relative_path="/test/pump.stl"):
    """A step-2 input file a prior pipeline shadowed: the entry keeps the original's asset identity
    and root but points at the output file under the run bucket's output-files prefix."""
    entry = _original_entry(relative_path)
    entry["bucket"] = _RUN_BUCKET
    entry["key"] = _OUTPUT_FILES_PREFIX + relative_path.lstrip("/")
    entry["versionId"] = "v2"
    return entry


@pytest.mark.unit
class TestAssetRelativePath:
    def test_manifest_relative_path_wins_for_a_shadowed_input(self):
        mod = _load()
        entry = _shadowed_entry()
        assert mod.asset_relative_path(
            entry["relativePath"], entry["key"], entry["assetRootS3Key"]) == "test/pump.stl"

    def test_object_key_trimmed_by_asset_root_is_the_fallback(self):
        mod = _load()
        assert mod.asset_relative_path(
            "", _ASSET_ROOT + "test/pump.stl", _ASSET_ROOT) == "test/pump.stl"

    def test_asset_root_file_and_empty_inputs(self):
        mod = _load()
        assert mod.asset_relative_path("/pump.stl", "", "") == "pump.stl"
        assert mod.asset_relative_path("", "", "") == ""


@pytest.mark.unit
class TestResolveInputsFromManifest:
    def test_shadowed_input_resolves_the_asset_relative_path(self):
        mod = _load()
        manifest = _manifest([_shadowed_entry()])
        with patch.object(mod, "_fetch_json_from_s3", MagicMock(return_value=manifest)):
            input_path, relative_path, output_path = mod.resolve_inputs_from_manifest(
                {"inputManifestS3Location": f"s3://{_ASSET_BUCKET}/m.json"})
        assert input_path == f"s3://{_RUN_BUCKET}/{_OUTPUT_FILES_PREFIX}test/pump.stl"
        assert relative_path == "test/pump.stl"
        assert output_path == f"s3://{_RUN_BUCKET}/{_OUTPUT_METADATA_PREFIX}"

    def test_original_input_resolves_the_asset_relative_path(self):
        mod = _load()
        manifest = _manifest([_original_entry()])
        with patch.object(mod, "_fetch_json_from_s3", MagicMock(return_value=manifest)):
            input_path, relative_path, _ = mod.resolve_inputs_from_manifest(
                {"inputManifestS3Location": f"s3://{_ASSET_BUCKET}/m.json"})
        assert input_path == f"s3://{_ASSET_BUCKET}/{_ASSET_ROOT}test/pump.stl"
        assert relative_path == "test/pump.stl"

    def test_legacy_body_fields_resolve_without_a_manifest(self):
        mod = _load()
        with patch.object(mod, "_fetch_json_from_s3", MagicMock(return_value={})):
            input_path, relative_path, output_path = mod.resolve_inputs_from_manifest({
                "inputS3AssetFilePath": f"s3://{_ASSET_BUCKET}/{_ASSET_ROOT}test/pump.stl",
                "inputAssetLocationKey": _ASSET_ROOT,
                "outputS3AssetMetadataPath": f"s3://{_ASSET_BUCKET}/{_ASSET_ROOT}",
            })
        assert input_path == f"s3://{_ASSET_BUCKET}/{_ASSET_ROOT}test/pump.stl"
        assert relative_path == "test/pump.stl"
        assert output_path == f"s3://{_ASSET_BUCKET}/{_ASSET_ROOT}"

    def test_multiple_inputs_fail_fast(self):
        # inputFileArity 'one': a workflow whose filters do not narrow to one file must not have
        # attributes extracted for the first file while both are reported successful.
        mod = _load()
        manifest = _manifest([_original_entry(), _original_entry("/test/housing.stl")])
        with patch.object(mod, "_fetch_json_from_s3", MagicMock(return_value=manifest)):
            with pytest.raises(ValueError, match="single input file"):
                mod.resolve_inputs_from_manifest(
                    {"inputManifestS3Location": f"s3://{_ASSET_BUCKET}/m.json"})


@pytest.mark.unit
class TestAttributeOutputKey:
    def _run(self, mod, relative_path, input_path):
        uploaded = {}

        def _upload(bucket, key, path):
            uploaded["bucket"] = bucket
            uploaded["key"] = key
            return key

        with patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p: p)), \
                patch.object(mod, "upload", MagicMock(side_effect=_upload)), \
                patch.object(mod, "extract_mesh_metadata", MagicMock(return_value={"volume": 1.5})), \
                patch.object(mod, "get_handler_for_format", MagicMock(return_value="mesh")), \
                patch("builtins.open", MagicMock()), \
                patch("json.dump", MagicMock()):
            mod.extract_metadata(
                relative_path, input_path,
                f"s3://{_RUN_BUCKET}/{_OUTPUT_METADATA_PREFIX}")
        return uploaded

    def test_shadowed_input_attributes_land_on_the_asset_file_path(self):
        mod = _load()
        uploaded = self._run(
            mod, "test/pump.stl",
            f"s3://{_RUN_BUCKET}/{_OUTPUT_FILES_PREFIX}test/pump.stl")
        assert uploaded["bucket"] == _RUN_BUCKET
        assert uploaded["key"] == _OUTPUT_METADATA_PREFIX + "test/pump.stl.attribute.json"

    def test_original_input_attributes_land_on_the_asset_file_path(self):
        mod = _load()
        uploaded = self._run(
            mod, "test/pump.stl", f"s3://{_ASSET_BUCKET}/{_ASSET_ROOT}test/pump.stl")
        assert uploaded["key"] == _OUTPUT_METADATA_PREFIX + "test/pump.stl.attribute.json"

    def test_asset_root_file_keeps_a_flat_attribute_key(self):
        mod = _load()
        uploaded = self._run(
            mod, "pump.stl", f"s3://{_ASSET_BUCKET}/{_ASSET_ROOT}pump.stl")
        assert uploaded["key"] == _OUTPUT_METADATA_PREFIX + "pump.stl.attribute.json"
