#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A conversion that cannot be performed must FAIL the execution, and no invocation may leave files
in the container's /tmp.

- **The manifest** is the only carrier of the input file's identity within its asset and of the run's
  output-files prefix, so a read failure answered with an empty manifest downgrades the run to the
  legacy body fields and writes the converted model wherever those happen to point.
- **A format with no loader or no exporter** must be rejected before anything is downloaded. The
  input extension is checked against ``SUPPORTED_INPUT_FORMATS`` and the requested output type against
  ``SUPPORTED_OUTPUT_FORMATS``, and the bundle's ``inputFileFilters`` declare the first of those, so
  the sets move together (``test_vams_schema_bundle.py``).
- **The working directory** is per-invocation. ``/tmp`` is a fixed budget, set by the function's
  ``ephemeralStorageSize`` in ``conversion3dBasicFunctions.ts``, shared by every invocation
  the execution environment is reused for, and a `.gltf` export writes its buffers as separate
  `.bin` files beside the model, so what accumulates is more than the two named outputs. The
  resulting failure depends on model size and invocation order and does not reproduce with small
  fixtures.
"""

import os
import sys
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

_ASSET_BUCKET = "abkt"
_MANIFEST_LOCATION = f"s3://{_ASSET_BUCKET}/pipelines/c/JOB/exec/E1/inputs/manifest.json"
_OUTPUT_PATH = "s3://obkt/pipelines/conv3dBasic/JOB/output/E1/files/"

# The legacy body fields a swallowed manifest read falls back to. They deliberately name a DIFFERENT
# asset and a different output prefix from the manifest, so a downgrade cannot be mistaken for a
# correct resolution.
_LEGACY_BODY = {
    "inputManifestS3Location": _MANIFEST_LOCATION,
    "inputS3AssetFilePath": f"s3://{_ASSET_BUCKET}/xOTHERASSET/legacy.stl",
    "outputS3AssetFilesPath": f"s3://{_ASSET_BUCKET}/xOTHERASSET/",
}

_MANIFEST_PAYLOAD = (
    '{"inputFiles": [{"relativePath": "/parts/housing/model.obj", "bucket": "abkt",'
    ' "key": "xd130a6d6/parts/housing/model.obj"}],'
    ' "outputs": {"bucket": "obkt", "files": "pipelines/conv3dBasic/JOB/output/E1/files/"}}')


def _load():
    """Load the pipeline module from its 'lambda.py' file name (not a valid module name)."""
    spec = importlib.util.spec_from_file_location(
        "trimesh_pipeline_lambda_contract", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


def _s3_returning(payload):
    stub = MagicMock()
    body = payload.encode("utf-8")
    stub.get_object.return_value = {"Body": MagicMock(read=lambda b=body: b)}
    return stub


@pytest.mark.unit
class TestManifestReadFailsLoudly:
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
        # returns the legacy pair -- input and OUTPUT prefix both pointing at xOTHERASSET -- and the
        # converted model is written into another asset rather than the one the run was launched for.
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
        with patch.object(mod, "s3_client", _s3_returning(_MANIFEST_PAYLOAD)):
            input_path, output_path, relative_subdir = mod.resolve_inputs_from_manifest(
                {"inputManifestS3Location": _MANIFEST_LOCATION})
        assert input_path == "s3://abkt/xd130a6d6/parts/housing/model.obj"
        assert output_path == _OUTPUT_PATH
        assert relative_subdir == "parts/housing"


class _Conversion:
    """What one convert_input_output call read, wrote and left behind."""

    def __init__(self):
        self.work_dirs = []
        self.downloaded = []
        self.uploaded = []
        self.input_paths = []

    @property
    def work_dir(self):
        assert self.work_dirs, "the run never downloaded an input file"
        return self.work_dirs[-1]

    @property
    def uploaded_keys(self):
        return sorted(key for _, key in self.uploaded)


def _run_conversion(mod, conversion, input_key="xd130a6d6/parts/housing/model.obj",
                    output_filetype=".glb", relative_subdir="parts/housing", load_error=None,
                    companion_names=(), export_writes_nothing=False):
    def _download(bucket, key, path):
        # In-band: the working directory has to EXIST while the run is using it. Against a
        # hardcoded '/tmp' this is whatever the platform happens to have, not a directory the run
        # created for itself.
        assert os.path.isdir(os.path.dirname(path)), path
        conversion.work_dirs.append(_work_root(path))
        conversion.input_paths.append(path)
        conversion.downloaded.append((bucket, key))
        # Write the input for real, so a run that uploaded the whole working directory would be
        # caught uploading it.
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("downloaded input")
        return path

    def _upload(bucket, key, path):
        conversion.uploaded.append((bucket, key))
        return key

    trimesh_stub = MagicMock()
    if load_error is not None:
        trimesh_stub.load.side_effect = load_error
    else:
        def _export(path, file_type=None, **kwargs):
            if export_writes_nothing:
                return
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("exported")
            for name in companion_names:
                with open(os.path.join(os.path.dirname(path), name), "w",
                          encoding="utf-8") as handle:
                    handle.write("companion")

        trimesh_stub.load.return_value.export = MagicMock(side_effect=_export)

    with patch.object(mod, "download", MagicMock(side_effect=_download)), \
            patch.object(mod, "uploadV2", MagicMock(side_effect=_upload)), \
            patch.object(mod, "trimesh", trimesh_stub):
        return mod.convert_input_output(
            f"s3://{_ASSET_BUCKET}/{input_key}", _OUTPUT_PATH, output_filetype, relative_subdir)


def _work_root(input_file_path):
    """The per-invocation working directory, given the path the input was downloaded to. The input
    and the export live in sibling subdirectories of it."""
    return os.path.dirname(os.path.dirname(input_file_path))


@pytest.mark.unit
class TestWorkingDirectoryIsRemoved:
    def test_a_successful_conversion_leaves_no_working_directory(self):
        mod = _load()
        conversion = _Conversion()
        _run_conversion(mod, conversion)
        assert conversion.uploaded, "the run uploaded nothing, so cleanup is not being measured"
        assert not os.path.exists(conversion.work_dir), conversion.work_dir

    def test_a_failed_load_leaves_no_working_directory(self):
        mod = _load()
        conversion = _Conversion()
        with pytest.raises(OSError, match="sentinel-load-failure"):
            _run_conversion(mod, conversion, load_error=OSError("sentinel-load-failure"))
        assert not os.path.exists(conversion.work_dir), conversion.work_dir

    def test_two_conversions_do_not_share_a_working_directory(self):
        # The platform-independent control: a fixed '/tmp/input{ext}' and '/tmp/output{type}' give
        # both runs the same directory, so a large first output is still occupying /tmp when the
        # second run needs the space.
        mod = _load()
        conversion = _Conversion()
        _run_conversion(mod, conversion, input_key="xid/first.stl", output_filetype=".glb",
                        relative_subdir="")
        _run_conversion(mod, conversion, input_key="xid/second.obj", output_filetype=".stl",
                        relative_subdir="")
        first, second = conversion.work_dirs
        assert first != second
        assert not os.path.exists(first) and not os.path.exists(second)


@pytest.mark.unit
class TestFormatsWithoutALoaderAreRejected:
    """`.dae` needs pycollada and `.xaml` / `.3dxml` need lxml and networkx, none of which
    poetry.lock resolves for this container; trimesh has no `.xaml` / `.3dxml` exporter at all."""

    UNAVAILABLE = [".dae", ".xaml", ".3dxml"]

    @pytest.mark.parametrize("extension", UNAVAILABLE)
    def test_it_is_rejected_as_an_input_before_anything_is_downloaded(self, extension):
        mod = _load()
        conversion = _Conversion()
        with pytest.raises(ValueError, match="Input format"):
            _run_conversion(mod, conversion, input_key=f"xid/model{extension}",
                            output_filetype=".glb", relative_subdir="")
        # Rejecting it only at the load would have already paid for the download, and for meshCad
        # the same load failure was swallowed entirely.
        assert conversion.downloaded == [], conversion.downloaded

    @pytest.mark.parametrize("extension", UNAVAILABLE)
    def test_it_is_rejected_as_an_output_before_anything_is_uploaded(self, extension):
        mod = _load()
        conversion = _Conversion()
        with pytest.raises(ValueError, match="Output format"):
            _run_conversion(mod, conversion, input_key="xid/model.stl",
                            output_filetype=extension, relative_subdir="")
        assert conversion.uploaded == [], conversion.uploaded

    def test_a_format_the_container_does_support_is_still_accepted(self):
        # A positive control for the rejections above: the narrowed sets have not closed the pipeline
        # to the formats it can actually convert.
        mod = _load()
        assert len(mod.SUPPORTED_INPUT_FORMATS) == 6, mod.SUPPORTED_INPUT_FORMATS
        for extension in sorted(mod.SUPPORTED_INPUT_FORMATS):
            conversion = _Conversion()
            _run_conversion(mod, conversion, input_key=f"xid/model{extension}",
                            output_filetype=".glb", relative_subdir="")
            assert conversion.uploaded, extension


@pytest.mark.unit
class TestAFormatItLoadsButCannotWriteIsInputOnly:
    """`.xyz` is a point-cloud text format trimesh loads into a PointCloud, and its xyz exporter takes
    a PointCloud only and raises even for one -- so nothing this pipeline loads can be written as
    `.xyz`, while `.xyz` itself remains a working input. The two sets are what keep those apart."""

    def test_the_output_set_is_the_input_set_minus_xyz(self):
        mod = _load()
        assert len(mod.SUPPORTED_OUTPUT_FORMATS) == 5, mod.SUPPORTED_OUTPUT_FORMATS
        assert mod.SUPPORTED_INPUT_FORMATS - mod.SUPPORTED_OUTPUT_FORMATS == {".xyz"}

    def test_xyz_is_rejected_as_an_output_before_anything_is_downloaded(self):
        mod = _load()
        conversion = _Conversion()
        with pytest.raises(ValueError, match=r"Output format \.xyz not supported"):
            _run_conversion(mod, conversion, input_key="xid/model.stl",
                            output_filetype=".xyz", relative_subdir="")
        # Rejecting it at the export instead pays for the download first and reports a
        # trimesh-internal error rather than naming the unsupported target.
        assert conversion.downloaded == [], conversion.downloaded
        assert conversion.uploaded == [], conversion.uploaded

    def test_xyz_to_xyz_is_rejected_too(self):
        # The same-format run is the one an operator reaches by picking the input's own format, and
        # trimesh's xyz exporter raises even for a genuine PointCloud, so it is not an exception.
        mod = _load()
        conversion = _Conversion()
        with pytest.raises(ValueError, match=r"Output format \.xyz not supported"):
            _run_conversion(mod, conversion, input_key="xid/cloud.xyz",
                            output_filetype=".xyz", relative_subdir="")
        assert conversion.downloaded == [], conversion.downloaded

    def test_xyz_still_converts_as_an_input(self):
        mod = _load()
        conversion = _Conversion()
        _run_conversion(mod, conversion, input_key="xid/parts/cloud.xyz",
                        output_filetype=".glb", relative_subdir="parts")
        assert conversion.uploaded, conversion.uploaded
        assert conversion.downloaded, conversion.downloaded


# Measured against trimesh 4.11.4: a `.gltf` export writes its vertex data to companion
# `gltf_buffer_N.bin` files (2 for a single mesh, 4 for a two-mesh scene, 6 for a textured mesh) and
# a textured `.obj` export writes `material.mtl` plus `material_0.png`. Both name their companions
# from the exporter, not from the model, so the names repeat across conversions. `.glb`, `.stl` and
# `.ply` wrote a single file for a plain mesh, a textured mesh and a scene alike.
_GLTF_COMPANIONS = ("gltf_buffer_0.bin", "gltf_buffer_1.bin")
_TEXTURED_OBJ_COMPANIONS = ("material.mtl", "material_0.png")
_FILES_PREFIX = "pipelines/conv3dBasic/JOB/output/E1/files/"


@pytest.mark.unit
class TestEveryFileTheExportProducedIsUploaded:
    """A `.gltf` references its buffers by a path relative to itself, so a model uploaded without
    them cannot be opened at all -- the built-in Convert to GLTF template shipped exactly that."""

    def test_a_gltf_export_uploads_its_buffer_companions(self):
        mod = _load()
        conversion = _Conversion()
        _run_conversion(mod, conversion, input_key="xd130a6d6/parts/housing/model.obj",
                        output_filetype=".gltf", relative_subdir="parts/housing",
                        companion_names=_GLTF_COMPANIONS)
        # THE positive control for this fix: pre-fix exactly one object was uploaded, the .gltf,
        # and the two buffers it names were left in /tmp and deleted.
        assert conversion.uploaded_keys == [
            f"{_FILES_PREFIX}parts/housing/model/gltf_buffer_0.bin",
            f"{_FILES_PREFIX}parts/housing/model/gltf_buffer_1.bin",
            f"{_FILES_PREFIX}parts/housing/model/model.gltf",
        ], conversion.uploaded_keys

    def test_the_companions_land_in_the_models_own_directory(self):
        # The reference inside the .gltf is the bare name 'gltf_buffer_0.bin', so it resolves only
        # if the companions are siblings of the model wherever the write-back puts it. Asserting one
        # shared directory is what pins that, rather than merely that three objects were uploaded.
        mod = _load()
        conversion = _Conversion()
        _run_conversion(mod, conversion, input_key="xd130a6d6/parts/housing/model.obj",
                        output_filetype=".gltf", relative_subdir="parts/housing",
                        companion_names=_GLTF_COMPANIONS)
        directories = {key.rsplit("/", 1)[0] for key in conversion.uploaded_keys}
        assert directories == {f"{_FILES_PREFIX}parts/housing/model"}, directories

    def test_a_textured_obj_export_uploads_its_material_companions(self):
        # `.gltf` is not the only multi-file target, which is why the upload reads the directory
        # rather than special-casing an extension.
        mod = _load()
        conversion = _Conversion()
        _run_conversion(mod, conversion, input_key="xd130a6d6/parts/housing/model.glb",
                        output_filetype=".obj", relative_subdir="parts/housing",
                        companion_names=_TEXTURED_OBJ_COMPANIONS)
        assert conversion.uploaded_keys == [
            f"{_FILES_PREFIX}parts/housing/model/material.mtl",
            f"{_FILES_PREFIX}parts/housing/model/material_0.png",
            f"{_FILES_PREFIX}parts/housing/model/model.obj",
        ], conversion.uploaded_keys

    @pytest.mark.parametrize("extension", [".glb", ".stl", ".ply"])
    def test_a_single_file_export_keeps_its_place_beside_the_source(self, extension):
        # The other direction: a format that writes one file must not gain a directory, so this fix
        # changes nothing for the three built-in templates whose target is single-file.
        mod = _load()
        conversion = _Conversion()
        _run_conversion(mod, conversion, input_key="xd130a6d6/parts/housing/model.obj",
                        output_filetype=extension, relative_subdir="parts/housing")
        assert conversion.uploaded_keys == [
            f"{_FILES_PREFIX}parts/housing/model{extension}"], conversion.uploaded_keys

    def test_the_downloaded_input_is_never_uploaded(self):
        # The trap in reading a directory back: the input was downloaded into the same working
        # directory, and it is a file the export did not produce. It is written for real by the
        # download stub, so a run that walked the whole working directory would upload it here.
        mod = _load()
        conversion = _Conversion()
        _run_conversion(mod, conversion, input_key="xd130a6d6/parts/housing/model.obj",
                        output_filetype=".gltf", relative_subdir="parts/housing",
                        companion_names=_GLTF_COMPANIONS)
        assert conversion.input_paths, "the run never downloaded an input file"
        input_name = os.path.basename(conversion.input_paths[-1])
        assert input_name == "input.obj", input_name
        assert not any(key.endswith(f"/{input_name}") for key in conversion.uploaded_keys), \
            conversion.uploaded_keys
        assert len(conversion.uploaded_keys) == 3, conversion.uploaded_keys

    def test_two_gltf_conversions_in_one_directory_do_not_overwrite_each_others_buffers(self):
        # The companions carry exporter-chosen names, so two models converted into the same asset
        # directory would both write 'gltf_buffer_0.bin' there. The write-back strips the staging
        # prefix and resolves what is left against the asset root, so two executions DO land in one
        # directory -- and the second buffer would win, leaving the first model reading its data.
        mod = _load()
        first, second = _Conversion(), _Conversion()
        _run_conversion(mod, first, input_key="xd130a6d6/parts/housing/pump.obj",
                        output_filetype=".gltf", relative_subdir="parts/housing",
                        companion_names=_GLTF_COMPANIONS)
        _run_conversion(mod, second, input_key="xd130a6d6/parts/housing/bracket.obj",
                        output_filetype=".gltf", relative_subdir="parts/housing",
                        companion_names=_GLTF_COMPANIONS)
        assert set(first.uploaded_keys).isdisjoint(second.uploaded_keys), (
            first.uploaded_keys, second.uploaded_keys)

    def test_an_export_that_wrote_nothing_fails_rather_than_uploading_nothing(self):
        # Reading the directory back means an exporter that quietly produced no file would upload
        # nothing and report success, which is the failure mode this pipeline had elsewhere.
        mod = _load()
        conversion = _Conversion()
        with pytest.raises(RuntimeError, match="produced no such file"):
            _run_conversion(mod, conversion, input_key="xd130a6d6/parts/housing/model.obj",
                            output_filetype=".gltf", relative_subdir="parts/housing",
                            export_writes_nothing=True)
        assert conversion.uploaded == [], conversion.uploaded
