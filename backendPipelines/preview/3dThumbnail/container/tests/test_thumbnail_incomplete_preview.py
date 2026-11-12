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

import json
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
            "(0 geometry layers, 0 data files, 4 textures/other files). See the job log for the list.")

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
        assert lines[0].startswith("Incomplete preview: 5 referenced files could not be fetched")
        assert any("Textures, materials and other files not fetched" in line for line in lines)
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
            "(3 geometry layers, 0 data files, 1 texture/other file). See the job log for the list.")


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


def _run(tmp_path, caplog, fetch, results_location=None, put_json=None, load=None):
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
            patch.object(core, "_load_file", load or MagicMock(return_value=MagicMock())), \
            patch.object(core.geometry_budget, "apply_budgets", lambda data, budgets: data), \
            patch.object(core, "_normalize_up_axis", lambda data, ext: data), \
            patch.object(core.splat_handler, "is_splat", lambda data: False), \
            patch.object(core.renderer, "generate_rotating_frames", MagicMock(return_value=[1, 2])), \
            patch.object(core.image_utils, "ensure_under_size_limit", MagicMock(side_effect=_write)), \
            patch.object(core.dependencies, "fetch_referenced_files", fetch), \
            patch.object(core.s3, "get_object_size", MagicMock(return_value=1024)), \
            patch.object(core.s3, "download", _download), \
            patch.object(core.s3, "upload", MagicMock(side_effect=lambda bucket, key, path: key)), \
            patch.object(core.s3, "put_json", put_json if put_json is not None else MagicMock()):
        result = core._run_preview_pipeline(_stage(), {}, {}, False, "xid",
                                            results_location=results_location)
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
                    "(1 geometry layer, 0 data files, 1 texture/other file). See the job log for the list.")
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


# --------------------------------------------------------------------------------------------------
# The result document under the execution's results folder
# --------------------------------------------------------------------------------------------------

RESULTS = {"bucketName": "run-bucket", "objectDir": "pipelines/preview3dThumbnail/JOB/output/E1/results/"}
RESULT_KEY = RESULTS["objectDir"] + "preview-incomplete.json"


def _skipped_report(*entries):
    report = dependencies.FetchReport()
    for reference, reason, rejected in entries:
        report.skipped.append(dependencies.SkippedReference(reference, reason, "scene/root.usda",
                                                            rejected=rejected))
    return report


@pytest.mark.unit
class TestResultDocument:
    def test_the_document_names_the_message_counts_and_each_file(self):
        incomplete = dependencies.incomplete_preview(_skipped_report(
            ("scene/parts/wheel.usda", "missing from the asset", False),
            ("scene/tex/wood.png", "over the byte budget (10 bytes)", False)))
        document = json.loads(dependencies.incomplete_result_document(incomplete, "scene/root.usda"))
        assert document == {
            "schema": "vams.preview3dThumbnail.incomplete/v1",
            "status": "INCOMPLETE",
            "message": incomplete.message(),
            "inputFile": "scene/root.usda",
            "counts": {"skipped": 2, "geometryLayers": 1, "dataFiles": 0, "texturesOrOther": 1,
                       "notFollowed": 0},
            "fetchStoppedEarly": False,
            "skippedFiles": [
                {"kind": "geometryLayer", "path": "scene/parts/wheel.usda", "namedIn": "scene/root.usda",
                 "reason": "missing from the asset"},
                {"kind": "textureOrOther", "path": "scene/tex/wood.png", "namedIn": "scene/root.usda",
                 "reason": "over the byte budget (10 bytes)"},
            ],
            "truncated": False,
        }

    def test_a_reference_refused_as_outside_the_asset_is_named_by_its_file_name_only(self):
        incomplete = dependencies.incomplete_preview(_skipped_report(
            ("s3://other-bucket/xother/secret/part.usda", "outside the asset (URI scheme)", True),
            ("C:\\Users\\artist\\maps\\wood.png", "outside the asset (absolute path)", True),
            ("../../xother/tex.png?sig=abc", "outside the asset ('..' escape)", True)))
        body = dependencies.incomplete_result_document(incomplete, "scene/root.usda").decode("utf-8")
        paths = [row["path"] for row in json.loads(body)["skippedFiles"]]
        assert paths == ["part.usda", "wood.png", "tex.png"]
        for leaked in ("other-bucket", "xother", "artist", "sig=abc", "s3://"):
            assert leaked not in body

    def test_the_list_is_capped_with_complete_counts_and_a_truncated_flag(self):
        entries = [(f"scene/tex/t{index}.png", "missing from the asset", False) for index in range(250)]
        incomplete = dependencies.incomplete_preview(_skipped_report(*entries))
        document = json.loads(dependencies.incomplete_result_document(incomplete))
        assert len(document["skippedFiles"]) == dependencies.MAX_RESULT_ENTRIES
        assert document["counts"]["skipped"] == 250 and document["truncated"] is True

    def test_long_values_are_bounded_and_the_document_stays_within_its_byte_limit(self):
        entries = [("scene/" + "x" * 2000 + f"{index}.usda", "r" * 2000, False) for index in range(150)]
        incomplete = dependencies.incomplete_preview(_skipped_report(*entries))
        body = dependencies.incomplete_result_document(incomplete, "i" * 5000)
        document = json.loads(body)
        assert len(body) <= dependencies.MAX_RESULT_DOCUMENT_BYTES
        assert all(len(row["path"]) <= dependencies.MAX_RESULT_TEXT_CHARS
                   and len(row["reason"]) <= dependencies.MAX_RESULT_TEXT_CHARS
                   for row in document["skippedFiles"])
        assert len(document["inputFile"]) <= dependencies.MAX_RESULT_TEXT_CHARS
        assert document["truncated"] is True

    def test_a_fetch_that_stopped_early_says_so(self):
        incomplete = dependencies.incomplete_preview(dependencies.FetchReport(), RuntimeError("x"))
        document = json.loads(dependencies.incomplete_result_document(incomplete))
        assert document["fetchStoppedEarly"] is True and document["skippedFiles"] == []


@pytest.mark.unit
class TestTheResultIsWrittenOnlyForAnIncompleteRun:
    def test_an_incomplete_run_writes_one_document_under_the_results_folder(self, tmp_path, caplog):
        put_json = MagicMock(side_effect=lambda bucket, key, body: key)
        result, _ = _run(tmp_path, caplog, _fetch_skipping(
            ("scene/parts/wheel.usda", "missing from the asset")), RESULTS, put_json)
        assert result.status == PipelineStatus.COMPLETE
        put_json.assert_called_once()
        bucket, key, body = put_json.call_args.args
        assert (bucket, key) == ("run-bucket", RESULT_KEY)
        document = json.loads(body)
        assert document["inputFile"] == "scene/model.obj"
        assert document["counts"]["geometryLayers"] == 1
        assert document["message"].startswith("Preview generated, but incomplete: 1 referenced file ")

    def test_a_complete_run_writes_nothing(self, tmp_path, caplog):
        put_json = MagicMock()
        result, messages = _run(tmp_path, caplog, _fetch_skipping(), RESULTS, put_json)
        assert result.status == PipelineStatus.COMPLETE
        put_json.assert_not_called()
        assert core.SUCCESS_MESSAGE in messages

    def test_without_a_results_location_nothing_is_written(self, tmp_path, caplog):
        """A command from a Lambda built before the location travelled (the upgrade window)."""
        put_json = MagicMock()
        result, messages = _run(tmp_path, caplog, _fetch_skipping(
            ("scene/tex/wood.png", "missing from the asset")), None, put_json)
        assert result.status == PipelineStatus.COMPLETE
        put_json.assert_not_called()
        assert any(message.startswith("Preview generated, but incomplete") for message in messages)

    @pytest.mark.parametrize("put_json", [MagicMock(return_value=None),
                                          MagicMock(side_effect=RuntimeError("AccessDenied"))])
    def test_a_failed_write_never_fails_the_run(self, tmp_path, caplog, put_json):
        result, _ = _run(tmp_path, caplog, _fetch_skipping(
            ("scene/tex/wood.png", "missing from the asset")), RESULTS, put_json)
        assert result.status == PipelineStatus.COMPLETE, result.errorMessage
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("Could not write the incomplete-preview result" in message for message in warnings)


@pytest.mark.unit
class TestTheCommandNamesTheResultsLocation:
    @pytest.mark.parametrize("extra,expected", [
        ([], None),
        (['{"inputVersions": {"PREVIEW_3D_THUMBNAIL": "v1"}}'], None),
        (['{"inputVersions": {}, "outputResults": {"bucketName": "run-bucket", '
          '"objectDir": "pipelines/preview3dThumbnail/JOB/output/E1/results"}}'], RESULTS),
        (['{"inputVersions": {}, "outputResults": {"bucketName": "", "objectDir": "r/"}}'], None),
        (['{"inputVersions": {}, "outputResults": "s3://run-bucket/r/"}'], None),
        (["not json"], None),
    ])
    def test_the_location_or_none(self, extra, expected):
        from preview_pipeline.utils.pipeline import objects
        assert objects.results_location(extra) == expected

    def test_run_hands_the_location_to_the_stage(self):
        stage = _stage()
        params = {"jobName": "j", "stages": [stage.__dict__], "localTest": "True"}
        with patch.object(core.manifest_io, "fetch_metadata", MagicMock(return_value={})), \
                patch.object(core.manifest_io, "fetch_input_configuration", MagicMock(return_value={})), \
                patch.object(core, "_run_preview_pipeline",
                             MagicMock(side_effect=lambda s, *a, **k: s)) as stage_run:
            core.run(params, results_location=RESULTS)
        assert stage_run.call_args.kwargs["results_location"] == RESULTS


# --------------------------------------------------------------------------------------------------
# What is counted: composition arcs, not metadata; renderer MDL modules; data files
# --------------------------------------------------------------------------------------------------

# A set in the common studio layout: the set root references each component's interface layer, which
# names itself in ``assetInfo`` relative to the SET root and loads its geometry through a payload.
_SET_ROOT = """#usda 1.0
(
    defaultPrim = "Kitchen"
    upAxis = "Z"
)
def Xform "Kitchen" (
    kind = "assembly"
)
{
    def Xform "Ball_1" (references = @./assets/Ball/Ball.usd@)
    {
    }
    def Xform "Cup_1" (references = @./assets/Cup/Cup.usd@)
    {
    }
}
"""
_COMPONENT = """#usda 1.0
(
    defaultPrim = "{name}"
)
def Xform "{name}" (
    assetInfo = {{
        asset identifier = @assets/{name}/{name}.usd@
        string name = "{name}"
    }}
    kind = "component"
    payload = @./{name}_payload.usd@
)
{{
}}
"""
_COMPONENT_PAYLOAD = """#usda 1.0
(
    subLayers = [@./{name}.geom.usd@]
    customLayerData = {{
        asset sourceFile = @/authoring/machine/{name}.ma@
    }}
)
"""
_COMPONENT_GEOM = """#usda 1.0
def Xform "{name}"
{{
    def Mesh "Mesh"
    {{
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        asset info:texture = @./tex/{name}.png@
    }}
}}
"""


def _studio_set(tmp_path, missing=()):
    bucket = FakeBucket(tmp_path / "bucket")
    bucket.put("xid/Kitchen_set.usda", _SET_ROOT)
    for name in ("Ball", "Cup"):
        files = {
            f"assets/{name}/{name}.usd": _COMPONENT.format(name=name),
            f"assets/{name}/{name}_payload.usd": _COMPONENT_PAYLOAD.format(name=name),
            f"assets/{name}/{name}.geom.usd": _COMPONENT_GEOM.format(name=name),
            f"assets/{name}/tex/{name}.png": b"png",
        }
        for path, data in files.items():
            if path not in missing:
                bucket.put("xid/" + path, data)
    return bucket


def _fetch_set(tmp_path, bucket):
    return _fetch(tmp_path, bucket, "xid/Kitchen_set.usda", ".usda")


@pytest.mark.unit
@requires_real("pxr")
class TestUsdMetadataIsNotAGeometryLayer:
    def test_a_complete_set_is_not_incomplete(self, tmp_path):
        bucket = _studio_set(tmp_path)
        report = _fetch_set(tmp_path, bucket)
        assert sorted(report.fetched) == sorted(
            f"assets/{n}/{f}" for n in ("Ball", "Cup")
            for f in (f"{n}.usd", f"{n}_payload.usd", f"{n}.geom.usd", f"tex/{n}.png"))
        # The fetch itself is unchanged: the identifier is still looked up (and is not in the asset at
        # the path it resolves to), and the absolute customLayerData path is still refused.
        assert "assets/Ball/assets/Ball/Ball.usd" in report.missing
        assert "/authoring/machine/Ball.ma" in report.rejected
        assert dependencies.incomplete_preview(report) is None

    def test_one_missing_component_layer_is_one_geometry_layer(self, tmp_path):
        report = _fetch_set(tmp_path, _studio_set(tmp_path, missing=("assets/Cup/Cup.geom.usd",)))
        incomplete = dependencies.incomplete_preview(report)
        assert _entries(incomplete.geometry_layers) == {"assets/Cup/Cup.geom.usd": "missing from the asset"}
        assert incomplete.data_files == [] and incomplete.other_files == []

    def test_a_missing_texture_attribute_is_a_texture(self, tmp_path):
        report = _fetch_set(tmp_path, _studio_set(tmp_path, missing=("assets/Ball/tex/Ball.png",)))
        incomplete = dependencies.incomplete_preview(report)
        assert incomplete.geometry_layers == []
        assert _entries(incomplete.other_files) == {"assets/Ball/tex/Ball.png": "missing from the asset"}

    def test_a_complete_set_completes_with_plain_success_and_writes_no_result(self, tmp_path, caplog):
        bucket = _studio_set(tmp_path)
        real_fetch = dependencies.fetch_referenced_files

        def fetch(*args, report=None, **kwargs):
            with patch.object(dependencies, "fetch_referenced_files", real_fetch):
                real = _fetch_set(tmp_path / "fetch", bucket)
            report.__dict__.update(real.__dict__)
            return report

        put_json = MagicMock()
        result, messages = _run(tmp_path, caplog, fetch, RESULTS, put_json)
        assert result.status == PipelineStatus.COMPLETE
        assert core.SUCCESS_MESSAGE in messages
        assert not any(message.startswith("Incomplete preview") for message in messages)
        put_json.assert_not_called()


@pytest.mark.unit
class TestRendererMdlModules:
    def _report(self, tmp_path, monkeypatch, caplog):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/scene/a.refs", "OmniPBR.mdl\nOmniPBR.mdl\nmaterials/Custom.mdl\n")
        bucket.put("xid/scene/b.refs", "OmniPBR.mdl\n")
        bucket.put("xid/scene/root.refs", "a.refs\nb.refs\n")
        monkeypatch.setitem(dependencies._COLLECTORS, ".refs",
                            lambda path: [(line, False) for line in open(path).read().split()])
        with caplog.at_level(logging.INFO):
            return _fetch(tmp_path, bucket, "xid/scene/root.refs", ".refs")

    def test_a_bare_mdl_module_is_not_counted_and_logged_once(self, tmp_path, monkeypatch, caplog):
        report = self._report(tmp_path, monkeypatch, caplog)
        incomplete = dependencies.incomplete_preview(report)
        assert _entries(incomplete.other_files) == {"scene/materials/Custom.mdl": "missing from the asset"}
        body = dependencies.incomplete_result_document(incomplete).decode("utf-8")
        assert "OmniPBR" not in body
        infos = [r.getMessage() for r in caplog.records
                 if "MDL module resolved by the renderer, not fetched: 'OmniPBR.mdl'" in r.getMessage()]
        assert len(infos) == 1 and all(r.levelno == logging.INFO for r in caplog.records
                                       if "OmniPBR" in r.getMessage())

    def test_only_bare_mdl_modules_are_the_renderers(self):
        assert dependencies._is_renderer_mdl("OmniPBR.mdl")
        assert dependencies._is_renderer_mdl("OmniGlass.MDL")
        for reference in ("materials/Custom.mdl", "./OmniPBR.mdl", "..\\mdl\\x.mdl",
                          "omniverse://host/OmniPBR.mdl", "OmniPBR.png"):
            assert not dependencies._is_renderer_mdl(reference)

    def test_a_scene_missing_only_renderer_modules_is_complete(self, tmp_path, monkeypatch):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/root.refs", "OmniPBR.mdl\nOmniSurface.mdl\n")
        monkeypatch.setitem(dependencies._COLLECTORS, ".refs",
                            lambda path: [(line, False) for line in open(path).read().split()])
        assert dependencies.incomplete_preview(_fetch(tmp_path, bucket, "xid/root.refs", ".refs")) is None


@pytest.mark.unit
class TestTheResultNamesNoLocation:
    @pytest.mark.parametrize("reference,expected", [
        ("s3://vams-other-bucket-example", ""),
        ("s3://vams-other-bucket-example/", ""),
        ("s3://vams-other-bucket-example/xother/parts/part.usda", "part.usda"),
        ("http://internal-host.example:8080", ""),
        ("http://internal-host.example:8080/", ""),
        ("https://user:secret@internal-host.example/a/Chair.usd", "Chair.usd"),
        ("http://user:secret@internal-host.example", ""),
        ("https://h.example/tex.png?X-Amz-Signature=abc#frag", "tex.png"),
        ("file:///etc/passwd", ""),
        ("file:///C:/Users/someone/tex.png", "tex.png"),
        ("omniverse://nucleus.example.com/", ""),
        ("omniverse://nucleus.example.com/Projects/scene.usd", "scene.usd"),
        ("C:\\Users\\someone\\", ""),
        ("C:\\Users\\someone", ""),
        ("C:\\Users\\someone\\maps\\wood.png", "wood.png"),
        ("\\\\fileserver\\share", ""),
        ("\\\\fileserver\\share\\", ""),
        ("\\\\fileserver\\share\\maps\\wood.png", "wood.png"),
        ("https%3A%2F%2Finternal-host.example%2Fdir%2Ftex.png", "tex.png"),
        ("https%253A%252F%252Finternal-host.example", ""),
        ("https%3A%2F%2Finternal-host.example%3A8443", ""),
        ("../../other-asset/secret.usda", "secret.usda"),
        ("anon:0x7f00", ""),
        ("/abs/.hidden.png", ""),
        ("pkg.usdz[inner.usda]", "pkg.usdz"),
        ("", ""),
    ])
    def test_the_safe_base_name(self, reference, expected):
        assert dependencies.safe_base_name(reference) == expected

    def test_a_refused_location_is_written_as_its_reason_only(self):
        report = dependencies.FetchReport()
        for reference in ("s3://vams-other-bucket-example", "http://internal-host.example:8080",
                          "omniverse://nucleus.example.com/", "C:\\Users\\someone\\",
                          "https%3A%2F%2Finternal-host.example%2Fdir%2Ftex.png"):
            report.skipped.append(dependencies.SkippedReference(
                reference, dependencies.rejection_reason(reference, "%" in reference), "scene/a.gltf",
                rejected=True, percent_encoded="%" in reference))
        body = dependencies.incomplete_result_document(dependencies.incomplete_preview(report)).decode()
        for leaked in ("vams-other-bucket-example", "internal-host", "8080", "nucleus", "someone",
                       "s3:", "http", "omniverse", "%3A"):
            assert leaked not in body, leaked
        rows = json.loads(body)["skippedFiles"]
        assert [row["path"] for row in rows] == ["", "", "", "", "tex.png"]
        assert all(row["reason"].startswith("outside the asset") for row in rows)


@pytest.mark.unit
class TestTheHeadlineWaitsForThePreview:
    def test_a_run_that_fails_after_the_fetch_never_says_preview_generated(self, tmp_path, caplog):
        result, messages = _run(tmp_path, caplog, _fetch_skipping(
            ("scene/model.bin", "missing from the asset")),
            load=MagicMock(side_effect=ValueError("buffer is missing")))
        assert result.status == PipelineStatus.FAILED
        assert any(message.startswith("Incomplete preview: 1 referenced file could not be fetched")
                   for message in messages)
        assert not any("Preview generated" in message for message in messages)
        assert result.errorMessage.startswith("Failed to load 3D file")


@pytest.mark.unit
class TestDataFiles:
    def test_a_missing_gltf_buffer_is_a_data_file_and_a_missing_image_a_texture(self, tmp_path, caplog):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/duck/Duck.gltf", json.dumps({
            "buffers": [{"uri": "Duck0.bin"}], "images": [{"uri": "DuckCM.png"}]}))
        incomplete = dependencies.incomplete_preview(
            _fetch(tmp_path, bucket, "xid/duck/Duck.gltf", ".gltf"))
        assert _entries(incomplete.data_files) == {"duck/Duck0.bin": "missing from the asset"}
        assert _entries(incomplete.other_files) == {"duck/DuckCM.png": "missing from the asset"}
        assert incomplete.message() == (
            "Preview generated, but incomplete: 2 referenced files could not be fetched (0 geometry "
            "layers, 1 data file, 1 texture/other file). See the job log for the list.")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            incomplete.log()
        lines = [record.getMessage() for record in caplog.records]
        data_title = next(i for i, line in enumerate(lines) if line.strip().startswith("Data files"))
        assert "'duck/Duck0.bin'" in lines[data_title + 1]
        assert "colour" not in lines[data_title]
        document = json.loads(dependencies.incomplete_result_document(incomplete))
        assert document["counts"]["dataFiles"] == 1
        assert [row["kind"] for row in document["skippedFiles"]] == ["dataFile", "textureOrOther"]

    def test_missing_lcc_data_files_are_data_files(self, tmp_path):
        bucket = FakeBucket(tmp_path / "bucket")
        bucket.put("xid/scan/meta.lcc", json.dumps({"version": "x"}))
        bucket.put("xid/scan/index.bin", b"i")
        incomplete = dependencies.incomplete_preview(_fetch(tmp_path, bucket, "xid/scan/meta.lcc", ".lcc"))
        assert set(_entries(incomplete.data_files)) == {"scan/data.bin", "scan/shcoef.bin",
                                                         "scan/environment.bin"}
        assert incomplete.other_files == [] and incomplete.geometry_layers == []
