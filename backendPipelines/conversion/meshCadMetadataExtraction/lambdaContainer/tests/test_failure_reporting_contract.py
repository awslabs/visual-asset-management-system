#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every way this pipeline can fail to produce attributes must FAIL the execution rather than report
success with nothing written, and no invocation may leave files in the container's /tmp.

Three routes reach the same wrong outcome -- an execution recorded SUCCESS against a file that gained
no attributes -- and each is covered here:

- **The manifest.** It is the only carrier of the input file's identity within its asset, so a read
  failure answered with an empty manifest downgrades the run to the legacy body fields and names the
  attribute file after a path that is not the file's asset-relative path.
- **The extractors.** ``extract_mesh_metadata`` / ``extract_cad_metadata`` see every load failure,
  and an empty return from either is indistinguishable from a model that genuinely carries no
  attributes.
- **The upload.** An empty attribute array is a well-formed body the write-back accepts as an empty
  update, so the guard has to sit before the upload rather than relying on the backend to reject it.

``/tmp`` is a fixed budget, set by the function's ``ephemeralStorageSize`` in
``conversionMeshCadMetadataExtractionFunctions.ts``, shared by every invocation the execution
environment is reused for, so
the accumulation is what makes a large model fail on space instead of on its own content -- a failure
that depends on invocation order and does not reproduce with small fixtures.
"""

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

# Stub common.logger, metadata_extractors and cadquery so the modules under test import without
# powertools / the CAD toolkit. trimesh and numpy are real: the extractor tests drive a load failure
# through the module's own trimesh reference rather than relying on the library being absent.
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
if "cadquery" not in sys.modules:
    sys.modules["cadquery"] = MagicMock()

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

_ASSET_BUCKET = "abkt"
_MANIFEST_LOCATION = f"s3://{_ASSET_BUCKET}/pipelines/m/JOB/exec/E1/inputs/manifest.json"
_OUTPUT_METADATA_PREFIX = "pipelines/metaExtract/JOB/output/E1/metadata/"
_OUTPUT_PATH = f"s3://obkt/{_OUTPUT_METADATA_PREFIX}"

# The legacy body fields a swallowed manifest read falls back to. They deliberately name a DIFFERENT
# asset from the manifest, so a downgrade cannot be mistaken for a correct resolution.
_LEGACY_BODY = {
    "inputManifestS3Location": _MANIFEST_LOCATION,
    "inputS3AssetFilePath": f"s3://{_ASSET_BUCKET}/xOTHERASSET/legacy.stl",
    "inputAssetLocationKey": "xOTHERASSET/",
    "outputS3AssetMetadataPath": f"s3://{_ASSET_BUCKET}/xOTHERASSET/",
}


def _load():
    """Load the pipeline module from its 'lambda.py' file name (not a valid module name)."""
    spec = importlib.util.spec_from_file_location(
        "meshcad_pipeline_lambda_failures", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _load_extractor(name):
    """Load one extractor module by path, so cad_extractor's cadquery import hits the stub above."""
    spec = importlib.util.spec_from_file_location(
        f"meshcad_{name}", os.path.join(_LAMBDA_DIR, "metadata_extractors", f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _s3_returning(payload):
    stub = MagicMock()
    body = payload.encode("utf-8")
    stub.get_object.return_value = {"Body": MagicMock(read=lambda b=body: b)}
    return stub


@pytest.mark.unit
class TestManifestReadFailsLoudly:
    """A referenced manifest that cannot be read must raise, never resolve to the legacy fields."""

    def test_no_location_returns_none_so_a_direct_invocation_still_works(self):
        mod = _load()
        assert mod.fetch_manifest("") is None
        assert mod.fetch_manifest(None) is None

    @pytest.mark.parametrize("location", ["not-a-uri", "s3://", "s3://bucket-only"])
    def test_a_malformed_location_raises(self, location):
        mod = _load()
        with pytest.raises(mod.ManifestReadError, match="malformed input manifest location"):
            mod.fetch_manifest(location)

    def test_an_s3_failure_raises(self):
        mod = _load()
        stub = MagicMock()
        stub.get_object.side_effect = RuntimeError("AccessDenied")
        with patch.object(mod, "s3_client", stub):
            with pytest.raises(mod.ManifestReadError, match="Could not read the workflow input"):
                mod.fetch_manifest(_MANIFEST_LOCATION)

    @pytest.mark.parametrize("payload", ["", "   ", "{not json"])
    def test_an_unparseable_body_raises(self, payload):
        mod = _load()
        with patch.object(mod, "s3_client", _s3_returning(payload)):
            with pytest.raises(mod.ManifestReadError, match="Could not read the workflow input"):
                mod.fetch_manifest(_MANIFEST_LOCATION)

    @pytest.mark.parametrize("payload", ["[]", '"a string"', "42"])
    def test_a_body_that_is_not_a_json_object_raises(self, payload):
        mod = _load()
        with patch.object(mod, "s3_client", _s3_returning(payload)):
            with pytest.raises(mod.ManifestReadError, match="is not a JSON object"):
                mod.fetch_manifest(_MANIFEST_LOCATION)

    def test_an_unreadable_manifest_does_not_downgrade_to_the_legacy_body_fields(self):
        # The load-bearing assertion. Against a manifest read that answers failure with {}, this
        # returns the legacy tuple -- pointing at xOTHERASSET -- and the run goes on to write an
        # attribute file under another asset's relative path and report success.
        mod = _load()
        stub = MagicMock()
        stub.get_object.side_effect = RuntimeError("AccessDenied")
        with patch.object(mod, "s3_client", stub):
            with pytest.raises(mod.ManifestReadError):
                mod.resolve_inputs_from_manifest(dict(_LEGACY_BODY))

    def test_a_readable_manifest_still_resolves(self):
        # A positive control for the negatives above: the raising reader has not simply broken the
        # normal path.
        mod = _load()
        payload = (
            '{"inputFiles": [{"relativePath": "/test/pump.stl", "bucket": "abkt",'
            ' "key": "xd130a6d6/test/pump.stl", "assetRootS3Key": "xd130a6d6/"}],'
            ' "outputs": {"bucket": "obkt", "metadata": "' + _OUTPUT_METADATA_PREFIX + '"}}')
        with patch.object(mod, "s3_client", _s3_returning(payload)):
            input_path, relative_path, output_path = mod.resolve_inputs_from_manifest(
                {"inputManifestS3Location": _MANIFEST_LOCATION})
        assert input_path == "s3://abkt/xd130a6d6/test/pump.stl"
        assert relative_path == "test/pump.stl"
        assert output_path == _OUTPUT_PATH


class _Extraction:
    """What one extract_metadata call read, wrote and left behind."""

    def __init__(self):
        self.work_dirs = []
        self.uploaded = []
        self.bodies = []

    @property
    def work_dir(self):
        assert self.work_dirs, "the run never downloaded an input file"
        return self.work_dirs[-1]


def _run_extraction(mod, extraction, metadata=None, extractor_error=None,
                    relative_path="test/pump.stl"):
    """Drive extract_metadata with the download, upload and extractor patched, recording the
    directory the input was downloaded into so the caller can assert it was cleaned up.

    The attribute file itself is written and read for real -- it is still on disk when ``upload`` is
    called -- so the body asserted below is the one that would have reached S3."""

    def _download(bucket, key, path):
        directory = os.path.dirname(path)
        # In-band: the working directory has to EXIST while the run is using it. Against a
        # hardcoded '/tmp' this is whatever the platform happens to have, not a directory the run
        # created for itself.
        assert os.path.isdir(directory), directory
        extraction.work_dirs.append(directory)
        return path

    def _upload(bucket, key, path):
        extraction.uploaded.append((bucket, key))
        with open(path, encoding="utf-8") as handle:
            extraction.bodies.append(json.load(handle))
        return key

    extractor = MagicMock(side_effect=extractor_error) if extractor_error \
        else MagicMock(return_value=metadata if metadata is not None else {"volume": 1.5})

    with patch.object(mod, "download", MagicMock(side_effect=_download)), \
            patch.object(mod, "upload", MagicMock(side_effect=_upload)), \
            patch.object(mod, "extract_mesh_metadata", extractor), \
            patch.object(mod, "get_handler_for_format", MagicMock(return_value="mesh")):
        return mod.extract_metadata(
            relative_path, f"s3://{_ASSET_BUCKET}/xd130a6d6/{relative_path}", _OUTPUT_PATH)


@pytest.mark.unit
class TestWorkingDirectoryIsRemoved:
    def test_a_successful_extraction_leaves_no_working_directory(self):
        mod = _load()
        extraction = _Extraction()
        _run_extraction(mod, extraction)
        assert extraction.uploaded, "the run uploaded nothing, so cleanup is not being measured"
        assert not os.path.exists(extraction.work_dir), extraction.work_dir

    def test_a_failed_extraction_leaves_no_working_directory(self):
        mod = _load()
        extraction = _Extraction()
        with pytest.raises(Exception, match="Attribute extraction failed"):
            _run_extraction(mod, extraction, extractor_error=RuntimeError("unreadable model"))
        assert not os.path.exists(extraction.work_dir), extraction.work_dir

    def test_two_extractions_do_not_share_a_working_directory(self):
        # The platform-independent control: a fixed '/tmp/input{ext}' gives both runs the same
        # directory, so files from the first are still there for the second.
        mod = _load()
        extraction = _Extraction()
        _run_extraction(mod, extraction, relative_path="a/first.stl")
        _run_extraction(mod, extraction, relative_path="b/second.obj")
        first, second = extraction.work_dirs
        assert first != second
        assert not os.path.exists(first) and not os.path.exists(second)


@pytest.mark.unit
class TestEmptyAttributeSetIsNotASuccess:
    def test_an_empty_metadata_dict_is_never_uploaded(self):
        mod = _load()
        extraction = _Extraction()
        with pytest.raises(Exception, match="No attributes could be extracted"):
            _run_extraction(mod, extraction, metadata={})
        assert extraction.uploaded == [], extraction.uploaded

    def test_a_populated_extraction_writes_a_non_empty_attribute_array(self):
        mod = _load()
        extraction = _Extraction()
        result = _run_extraction(mod, extraction, metadata={"AB_geometric_metadata": {"volume": 2}})
        assert result["statusCode"] == 200
        assert extraction.uploaded == [
            ("obkt", _OUTPUT_METADATA_PREFIX + "test/pump.stl.attribute.json")]
        # The body that was written, not just the fact that something was: the guard has to admit a
        # real extraction while rejecting an empty one.
        assert len(extraction.bodies) == 1, extraction.bodies
        assert extraction.bodies[0]["type"] == "attribute"
        assert [entry["metadataKey"] for entry in extraction.bodies[0]["metadata"]] == [
            "AB_geometric_metadata"]


@pytest.mark.unit
class TestFormatsWithoutALoaderAreRejected:
    """`.dae` needs pycollada and `.xaml` / `.3dxml` need lxml and networkx, none of which this
    container installs. The rejection has to come from the format check, which runs before anything
    is downloaded -- reaching the extractor instead is what produced an empty attribute set."""

    UNAVAILABLE = [".dae", ".xaml", ".3dxml"]

    @pytest.mark.parametrize("extension", UNAVAILABLE)
    def test_it_is_rejected_before_anything_is_downloaded(self, extension):
        mod = _load()
        real_handler = _load_extractor("format_handlers").get_handler_for_format
        downloaded = []
        with patch.object(mod, "get_handler_for_format", real_handler), \
                patch.object(mod, "download",
                             MagicMock(side_effect=lambda b, k, p: downloaded.append(k))):
            with pytest.raises(ValueError, match="Unsupported file format"):
                mod.extract_metadata(
                    f"test/model{extension}", f"s3://{_ASSET_BUCKET}/xd130a6d6/test/model{extension}",
                    _OUTPUT_PATH)
        assert downloaded == [], downloaded

    @pytest.mark.parametrize("extension,expected", [(".stl", "mesh"), (".step", "cad"),
                                                    (".dxf", "cad"), (".xyz", "mesh")])
    def test_a_format_the_extractors_do_handle_is_still_routed(self, extension, expected):
        # A positive control for the rejections above: the narrowed list has not closed the pipeline
        # to the formats its extractors can read.
        real_handler = _load_extractor("format_handlers").get_handler_for_format
        assert real_handler(f"model{extension}") == expected


@pytest.mark.unit
class TestExtractorsRaiseOnLoadFailure:
    """The extractors themselves. Each answered a load failure with {}, which the lambda then wrote
    as an empty attribute update and reported successful."""

    def test_mesh_extractor_propagates_a_load_failure(self):
        module = _load_extractor("mesh_extractor")
        stub = MagicMock()
        stub.load.side_effect = OSError("sentinel-mesh-load-failure")
        with patch.object(module, "trimesh", stub):
            with pytest.raises(RuntimeError) as excinfo:
                module.extract_mesh_metadata("/work/model.stl")
        # The cause has to survive into the message: it is what the execution record shows an
        # operator, and it also proves the raise came from the load rather than from the stub
        # tripping some later isinstance check.
        assert "sentinel-mesh-load-failure" in str(excinfo.value)
        assert "model.stl" in str(excinfo.value)

    def test_cad_extractor_propagates_an_import_failure(self):
        module = _load_extractor("cad_extractor")
        stub = MagicMock()
        stub.importers.importStep.side_effect = OSError("sentinel-step-import-failure")
        with patch.object(module, "cq", stub):
            with pytest.raises(RuntimeError) as excinfo:
                module.extract_cad_metadata("/work/housing.step")
        assert "sentinel-step-import-failure" in str(excinfo.value)
        assert "housing.step" in str(excinfo.value)

    def test_cad_extractor_rejects_a_format_it_cannot_import(self):
        module = _load_extractor("cad_extractor")
        with patch.object(module, "cq", MagicMock()):
            with pytest.raises(RuntimeError, match="Unsupported CAD format"):
                module.extract_cad_metadata("/work/drawing.iges")
