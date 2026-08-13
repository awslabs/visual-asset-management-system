#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Tests for the conversion/3dBasic Trimesh lambda's manifest contract: single-input enforcement
(the pipeline is registered with inputFileArity 'one'), case-insensitive extension checks matching
the registered inputFileFilters, and output keys that preserve the input file's subdirectory within
the asset."""

import os
import sys
import json
import types
import importlib.util
from unittest.mock import MagicMock, patch

import pytest

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub common.logger + trimesh so the module imports without powertools / the mesh library.
if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger
if "trimesh" not in sys.modules:
    sys.modules["trimesh"] = MagicMock()

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")


def _load():
    """Load the pipeline module from its 'lambda.py' file name (not a valid module name)."""
    spec = importlib.util.spec_from_file_location(
        "trimesh_pipeline_lambda", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _manifest(relative_path, key, extra_files=0):
    files = [{
        "relativePath": relative_path,
        "bucket": "abkt",
        "key": key,
    }]
    for i in range(extra_files):
        files.append({"relativePath": f"/extra{i}.stl", "bucket": "abkt", "key": f"xid/extra{i}.stl"})
    return {
        "inputFiles": files,
        "outputs": {"bucket": "obkt", "files": "pipelines/p1/JOB/output/E1/files/"},
    }


@pytest.mark.unit
class TestRelativeSubdirFromManifestPath:
    def test_nested_path_yields_subdirectory(self):
        mod = _load()
        assert mod.relative_subdir_from_manifest_path("/parts/housing/model.obj") == "parts/housing"

    def test_asset_root_file_yields_empty(self):
        mod = _load()
        assert mod.relative_subdir_from_manifest_path("/model.obj") == ""
        assert mod.relative_subdir_from_manifest_path("model.obj") == ""
        assert mod.relative_subdir_from_manifest_path("") == ""


@pytest.mark.unit
class TestResolveInputsFromManifest:
    def test_single_input_resolves_path_and_subdir(self):
        mod = _load()
        manifest = _manifest("/parts/housing/model.obj", "xid/parts/housing/model.obj")
        with patch.object(mod, "_fetch_json_from_s3", MagicMock(return_value=manifest)):
            input_path, output_path, relative_subdir = mod.resolve_inputs_from_manifest(
                {"inputManifestS3Location": "s3://abkt/m.json"})
        assert input_path == "s3://abkt/xid/parts/housing/model.obj"
        assert output_path == "s3://obkt/pipelines/p1/JOB/output/E1/files/"
        assert relative_subdir == "parts/housing"

    def test_multiple_inputs_fail_fast(self):
        # inputFileArity 'one': a template override widening arity must not silently drop files.
        mod = _load()
        manifest = _manifest("/model.obj", "xid/model.obj", extra_files=2)
        with patch.object(mod, "_fetch_json_from_s3", MagicMock(return_value=manifest)):
            with pytest.raises(ValueError, match="single input file"):
                mod.resolve_inputs_from_manifest({"inputManifestS3Location": "s3://abkt/m.json"})


@pytest.mark.unit
class TestConvertInputOutput:
    def _run(self, mod, input_path, output_filetype, relative_subdir=""):
        uploaded = {}

        def _upload(bucket, key, path):
            uploaded["bucket"] = bucket
            uploaded["key"] = key
            return key

        with patch.object(mod, "download", MagicMock(side_effect=lambda b, k, p: p)), \
                patch.object(mod, "uploadV2", MagicMock(side_effect=_upload)), \
                patch.object(mod, "trimesh", MagicMock()):
            mod.convert_input_output(
                input_path, "s3://obkt/pipelines/p1/JOB/output/E1/files/",
                output_filetype, relative_subdir)
        return uploaded

    def test_uppercase_input_extension_is_accepted(self):
        mod = _load()
        uploaded = self._run(mod, "s3://abkt/xid/Bracket.STL", ".glb")
        assert uploaded["key"] == "pipelines/p1/JOB/output/E1/files/Bracket.glb"

    def test_uppercase_output_filetype_is_accepted(self):
        mod = _load()
        uploaded = self._run(mod, "s3://abkt/xid/bracket.stl", ".GLB")
        assert uploaded["key"].endswith(".glb")

    def test_output_preserves_relative_subdirectory(self):
        mod = _load()
        uploaded = self._run(mod, "s3://abkt/xid/parts/housing/model.obj", ".glb", "parts/housing")
        assert uploaded["key"] == "pipelines/p1/JOB/output/E1/files/parts/housing/model.glb"

    def test_unsupported_extension_still_rejected(self):
        mod = _load()
        with pytest.raises(ValueError, match="Input format"):
            self._run(mod, "s3://abkt/xid/model.xyzzy", ".glb")
