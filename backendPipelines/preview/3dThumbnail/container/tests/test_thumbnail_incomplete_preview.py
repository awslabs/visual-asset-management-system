#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""A preview rendered without some of its referenced files is reported as incomplete.

A referenced file that cannot be fetched (missing from the asset, outside the asset, over a fetch
budget) is skipped and never fails the run. The run used to end with the plain success message, and
the only trace was one warning per reference. It now logs one summary that lists each skipped file with
its reason, with the geometry layers a USD stage composes (whose absence drops geometry) apart from
textures, materials and data files (whose absence drops colour or detail), and it completes with an
"incomplete" message. A run that fetched everything keeps the plain success message.

The USD case needs the real pxr and runs where usd-core is installed (the pipeline image, CI).
"""

import logging
import os
import shutil
from unittest.mock import MagicMock, patch

import pytest

from conftest import requires_real
from preview_pipeline import core, dependencies
from preview_pipeline.utils.pipeline.objects import (
    PipelineStage,
    PipelineStatus,
    PipelineType,
    StageOutput,
)

BUCKET = "asset-bucket"


class FakeBucket:
    """S3 HeadObject/GetObject served from a local directory tree keyed like the bucket."""

    def __init__(self, root):
        self.root = str(root)
        self.downloads = []

    def put(self, key, data):
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb" if isinstance(data, bytes) else "w") as handle:
            handle.write(data)
        return path

    def head_object_size(self, bucket, key):
        path = os.path.join(self.root, key)
        return os.path.getsize(path) if os.path.isfile(path) else None

    def download(self, bucket, key, path):
        self.downloads.append(key)
        shutil.copyfile(os.path.join(self.root, key), path)
        return path


def _fetch(tmp_path, bucket, input_key, ext, asset_id="xid", limits=None):
    root_key = dependencies.asset_root_key(input_key, asset_id)
    local_root = str(tmp_path / "local")
    local_input = dependencies.local_path_for(
        local_root, dependencies.relative_to_root(input_key, root_key))
    os.makedirs(os.path.dirname(local_input), exist_ok=True)
    shutil.copyfile(os.path.join(bucket.root, input_key), local_input)
    with patch.object(dependencies.s3, "head_object_size", bucket.head_object_size), \
            patch.object(dependencies.s3, "download", bucket.download):
        return dependencies.fetch_referenced_files(BUCKET, input_key, local_input, local_root,
                                                   root_key, ext, limits=limits)


def _entries(entries):
    return {entry.reference: entry.reason for entry in entries}


OBJ = "mtllib ../materials/model.mtl extra.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
MTL = ("newmtl wood\nKd 1 1 1\nmap_Kd tex/wood.png\nmap_d /etc/shadow\n"
       "map_Ka ../../other-asset/secret.png\nmap_Ks https://example.com/spec.png\n")


@pytest.mark.unit
class TestRejectionReason:
    @pytest.mark.parametrize("reference,reason", [
        ("/abs/tex.png", "outside the asset (absolute path)"),
        ("C:\\textures\\wood.png", "outside the asset (absolute path)"),
        ("\\\\server\\share\\tex.png", "outside the asset (absolute path)"),
        ("http://example.com/part.usda", "outside the asset (URI scheme)"),
        ("omniverse://host/x.usd", "outside the asset (URI scheme)"),
        ("../../other-asset/tex.png", "outside the asset ('..' escape)"),
        ("tex\x00.png", "contains control characters"),
        ("", "an empty path"),
    ])
    def test_each_refusal_is_named(self, reference, reason):
        assert dependencies.resolve_reference(reference, "models") is None
        assert dependencies.rejection_reason(reference) == reason

    def test_a_percent_encoded_escape_is_named_as_decoded(self):
        assert dependencies.rejection_reason("..%2f..%2fx", percent_encoded=True) == \
            "outside the asset ('..' escape)"


@pytest.mark.unit
class TestGeometryLayers:
    @pytest.mark.parametrize("reference", [
        "parts/wheel.usda", "a.USD", "b.usdc", "pkg.usdz[inner.usda]", "http://host/part.usda",
        "C:\\layers\\base.usd"])
    def test_usd_layers_are_geometry(self, reference):
        assert dependencies.is_geometry_layer(reference)

    @pytest.mark.parametrize("reference", [
        "tex/wood.png", "model.mtl", "scene.bin", "data.bin", "maps/normal.exr", "noext", ""])
    def test_everything_else_is_a_texture_material_or_data_file(self, reference):
        assert not dependencies.is_geometry_layer(reference)


@pytest.mark.unit
class TestFetchSummary:
    def _bucket(self, tmp_path):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/models/model.obj", OBJ)
        bucket.put("xid/materials/model.mtl", MTL)
        bucket.put("xid/materials/tex/wood.png", b"png")
        bucket.put("other-asset/secret.png", b"secret")
        return bucket

    def test_every_skipped_file_is_listed_with_its_reason(self, tmp_path):
        report = _fetch(tmp_path, self._bucket(tmp_path), "xid/models/model.obj", ".obj")
        incomplete = dependencies.incomplete_preview(report)
        assert incomplete is not None
        assert incomplete.geometry_layers == []
        assert _entries(incomplete.other_files) == {
            "models/extra.mtl": "missing from the asset",
            "/etc/shadow": "outside the asset (absolute path)",
            "../../other-asset/secret.png": "outside the asset ('..' escape)",
            "https://example.com/spec.png": "outside the asset (URI scheme)",
        }
        referrers = {entry.reference: entry.referrer for entry in incomplete.other_files}
        assert referrers["models/extra.mtl"] == "models/model.obj"
        assert referrers["/etc/shadow"] == "materials/model.mtl"
        assert incomplete.message() == (
            "Preview generated, but incomplete: 4 referenced files could not be fetched "
            "(0 geometry layers, 4 textures/other files). See the job log for the list.")

    def test_a_fully_fetched_input_is_not_incomplete(self, tmp_path):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/models/model.obj", "mtllib model.mtl\nv 0 0 0\n")
        bucket.put("xid/models/model.mtl", "newmtl wood\nmap_Kd tex/wood.png\n")
        bucket.put("xid/models/tex/wood.png", b"png")
        report = _fetch(tmp_path, bucket, "xid/models/model.obj", ".obj")
        assert sorted(report.fetched) == ["models/model.mtl", "models/tex/wood.png"]
        assert dependencies.incomplete_preview(report) is None

    @pytest.mark.parametrize("limits,disk,reason", [
        (dependencies.FetchLimits(max_files=1), None, "over the file budget (1 files)"),
        (dependencies.FetchLimits(max_total_bytes=len(MTL) + 1), None,
         f"over the byte budget ({len(MTL) + 1} bytes)"),
    ])
    def test_budget_reasons(self, tmp_path, limits, disk, reason):
        report = _fetch(tmp_path, self._bucket(tmp_path), "xid/models/model.obj", ".obj",
                        limits=limits)
        incomplete = dependencies.incomplete_preview(report)
        assert reason in _entries(incomplete.other_files).values()

    def test_the_disk_reason(self, tmp_path):
        bucket = self._bucket(tmp_path)
        with patch.object(dependencies.shutil, "disk_usage",
                          lambda path: shutil._ntuple_diskusage(10, 10, 0)):
            report = _fetch(tmp_path, bucket, "xid/models/model.obj", ".obj")
        reasons = _entries(dependencies.incomplete_preview(report).other_files)
        assert reasons["materials/model.mtl"].startswith("over the disk budget (")

    def test_a_fetch_that_stopped_early_is_incomplete(self):
        report = dependencies.FetchReport(fetched=["a.usda"])
        incomplete = dependencies.incomplete_preview(report, RuntimeError("throttled"))
        assert incomplete is not None and incomplete.skipped_count == 0
        assert incomplete.fetch_error == "RuntimeError"
        assert "may be incomplete" in incomplete.message()

    def test_the_summary_is_one_warning_block_with_quoted_names(self, tmp_path, caplog):
        report = _fetch(tmp_path, self._bucket(tmp_path), "xid/models/model.obj", ".obj")
        report.skipped.append(dependencies.SkippedReference("forged\nINFO done.png", "missing", "m"))
        incomplete = dependencies.incomplete_preview(report)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            incomplete.log()
        lines = [record.getMessage() for record in caplog.records]
        assert lines[0].startswith("Incomplete preview: Preview generated, but incomplete: 5 ")
        assert any("Textures, materials and data files not fetched" in line for line in lines)
        assert any("'models/extra.mtl' (named in 'models/model.obj'): missing from the asset" in line
                   for line in lines)
        # A name carrying a newline is logged escaped, on its own line.
        assert all("\n" not in line for line in lines)
        assert all(record.levelno == logging.WARNING for record in caplog.records)


_USD_ROOT = """#usda 1.0
(
    subLayers = [@./parts/base.usda@]
    upAxis = "Y"
)
def Xform "World"
{
    def Xform "Body" (references = @./parts/body.usda@)
    {
    }
    def Xform "Wheel" (references = @./parts/wheel.usda@)
    {
    }
    def Xform "Remote" (payload = @http://example.com/parts/remote.usda@)
    {
    }
    def Xform "Neighbour" (references = @../../other-asset/secret.usda@)
    {
    }
}
"""
_USD_PART = """#usda 1.0
(
    defaultPrim = "Part"
)
def Xform "Part"
{
    def Mesh "Tri"
    {
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        asset info:texture = @../tex/missing.png@
    }
}
"""


@pytest.mark.unit
@requires_real("pxr")
class TestUsdStageSummary:
    def test_missing_and_outside_layers_are_geometry_and_a_missing_texture_is_not(self, tmp_path):
        """The root sits one directory above its component layers: one component is missing, one
        payload is an http:// URI, one reference escapes the asset, and a fetched component names a
        texture that is not in the asset."""
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/scene/root.usda", _USD_ROOT)
        bucket.put("xid/scene/parts/base.usda", "#usda 1.0\n")
        bucket.put("xid/scene/parts/body.usda", _USD_PART)
        bucket.put("other-asset/secret.usda", _USD_PART)

        report = _fetch(tmp_path, bucket, "xid/scene/root.usda", ".usda")
        assert sorted(report.fetched) == ["scene/parts/base.usda", "scene/parts/body.usda"]
        assert not any(key.startswith("other-asset/") for key in bucket.downloads)

        incomplete = dependencies.incomplete_preview(report)
        assert _entries(incomplete.geometry_layers) == {
            "scene/parts/wheel.usda": "missing from the asset",
            "http://example.com/parts/remote.usda": "outside the asset (URI scheme)",
            "../../other-asset/secret.usda": "outside the asset ('..' escape)",
        }
        assert _entries(incomplete.other_files) == {"scene/tex/missing.png": "missing from the asset"}
        assert incomplete.message() == (
            "Preview generated, but incomplete: 4 referenced files could not be fetched "
            "(3 geometry layers, 1 texture/other file). See the job log for the list.")


# --------------------------------------------------------------------------------------------------
# The run's completion message
# --------------------------------------------------------------------------------------------------

OUTPUT_DIR = "pipelines/preview3dThumbnail/JOB/output/E1/files/"
INPUT_KEY = "xid/scene/model.obj"


def _stage():
    output = StageOutput(bucketName="run-bucket", objectDir=OUTPUT_DIR)
    return PipelineStage(
        type=PipelineType.PREVIEW_3D_THUMBNAIL,
        inputFile={"bucketName": BUCKET, "objectKey": INPUT_KEY, "fileExtension": ".obj"},
        outputFiles=output.__dict__,
        outputMetadata=output.__dict__,
        temporaryFiles=output.__dict__,
    )


def _run(tmp_path, caplog, fetch):
    """Run the stage to completion against stubbed I/O and rendering with ``fetch`` standing in for
    the referenced-file fetch. Returns (result, the logged messages)."""
    def _create_dir(parts):
        path = tmp_path / "-".join(parts)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _download(bucket, key, path, **kwargs):
        with open(path, "wb") as handle:
            handle.write(b"v 0 0 0\n")
        return path

    def _write(frames, path, **kwargs):
        with open(path, "wb") as handle:
            handle.write(b"preview")
        return path

    with caplog.at_level(logging.INFO), \
            patch.object(core, "_create_dir", _create_dir), \
            patch.object(core, "_check_existing_preview", MagicMock(return_value=None)), \
            patch.object(core, "_load_file", MagicMock(return_value=MagicMock())), \
            patch.object(core.geometry_budget, "apply_budgets", lambda data, budgets: data), \
            patch.object(core, "_normalize_up_axis", lambda data, ext: data), \
            patch.object(core.splat_handler, "is_splat", lambda data: False), \
            patch.object(core.renderer, "generate_rotating_frames", MagicMock(return_value=[1, 2])), \
            patch.object(core.image_utils, "ensure_under_size_limit", MagicMock(side_effect=_write)), \
            patch.object(core.dependencies, "fetch_referenced_files", fetch), \
            patch.object(core.s3, "get_object_size", MagicMock(return_value=1024)), \
            patch.object(core.s3, "download", _download), \
            patch.object(core.s3, "upload", MagicMock(side_effect=lambda bucket, key, path: key)):
        result = core._run_preview_pipeline(_stage(), {}, {}, False, "xid")
    return result, [record.getMessage() for record in caplog.records]


def _fetch_skipping(*entries, error=None):
    """A stand-in for fetch_referenced_files that records ``entries`` as skipped references (and
    raises ``error`` afterwards, when given)."""
    def fetch(*args, report=None, **kwargs):
        report = report if report is not None else dependencies.FetchReport()
        for reference, reason in entries:
            report.missing.append(reference)
            report.skipped.append(dependencies.SkippedReference(reference, reason, "scene/model.obj"))
        if error is not None:
            raise error
        return report
    return fetch


@pytest.mark.unit
class TestCompletionMessage:
    def test_a_run_that_fetched_everything_reports_plain_success(self, tmp_path, caplog):
        result, messages = _run(tmp_path, caplog, _fetch_skipping())
        assert result.status == PipelineStatus.COMPLETE
        assert core.SUCCESS_MESSAGE in messages
        assert not any("incomplete" in message.lower() for message in messages)

    def test_a_run_that_skipped_files_succeeds_and_says_incomplete(self, tmp_path, caplog):
        result, messages = _run(tmp_path, caplog, _fetch_skipping(
            ("scene/parts/wheel.usda", "missing from the asset"),
            ("scene/tex/wood.png", "missing from the asset")))
        assert result.status == PipelineStatus.COMPLETE, result.errorMessage
        expected = ("Preview generated, but incomplete: 2 referenced files could not be fetched "
                    "(1 geometry layer, 1 texture/other file). See the job log for the list.")
        assert expected in messages
        assert core.SUCCESS_MESSAGE not in messages
        assert any("'scene/parts/wheel.usda' (named in 'scene/model.obj'): missing from the asset"
                   in message for message in messages)

    def test_a_fetch_that_raised_succeeds_and_says_incomplete(self, tmp_path, caplog):
        result, messages = _run(tmp_path, caplog, _fetch_skipping(
            ("scene/tex/wood.png", "missing from the asset"), error=RuntimeError("throttled")))
        assert result.status == PipelineStatus.COMPLETE, result.errorMessage
        assert any(message.startswith("Preview generated, but incomplete: 1 referenced file could "
                                      "not be fetched") for message in messages)
        assert any("fetch stopped early (RuntimeError)" in message for message in messages)
        assert core.SUCCESS_MESSAGE not in messages
