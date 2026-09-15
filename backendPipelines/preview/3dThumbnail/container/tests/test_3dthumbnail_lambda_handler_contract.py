#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The RENDER3D branch handler's contract with constructPipeline and the state machine.

It reads two s3:// locations from the state, downloads the file, analyses it, uploads the still frames
beside the analysis manifest, merges its results into the manifest constructPipeline pre-wrote (so the
sys_file attributes survive), and returns the whole state merged with the six branch keys every render
branch of the SYSTEM GenAI metadata state machine emits. It never reports a task token — PipelineEndTask
does — and every fault propagates so the RenderDegradePass catch fires. Importing it has no Lambda-only
side effect: the Fargate renderer's job entry (preview_pipeline/analysis/batch_job.py) imports it too."""

import dataclasses
import importlib.util
import json
import os
import re
import subprocess  # nosec B404 - the import probe runs this interpreter with a fixed argument list
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

np = pytest.importorskip("numpy", reason="frames are numpy arrays")

from preview_pipeline.analysis import analysis as analysis_module  # noqa: E402
from preview_pipeline.utils import s3_utils  # noqa: E402

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
CONTAINER_DIR = os.path.normpath(os.path.join(TESTS_DIR, ".."))
HANDLER_PATH = os.path.join(CONTAINER_DIR, "lambda_handler.py")

MANIFEST_URI = "s3://aux-bucket/pipelines/system-genai-metadata/E1/analysis.json"
MANIFEST_KEY = "pipelines/system-genai-metadata/E1/analysis.json"
INPUT_URI = "s3://asset-bucket/xid/parts/pump.stp"
PREWRITTEN = {
    "schemaVersion": 1, "fileClass": "cad", "renderBranch": "RENDER3D",
    "attributes": {"sys_file": {"name": "pump.stp", "ext": ".stp", "sizeBytes": 13}},
    "renderImages": [], "textExcerpt": None, "facts": {}, "warnings": ["pre-existing"], "renderSkipped": None,
}
# The rendered configBody of the system-genai-metadata-default template, passed inline: manifest_io reads a
# non-s3:// location as inline JSON, so no S3 double is needed for the configuration.
CONFIG = json.dumps({"seedWithExistingMetadata": True, "includeSiblingFiles": True, "renderViews": 8,
                     "maxTextChars": 12000, "writeAssetKeywords": False, "embeddingIncludeTextExcerpt": True,
                     "writeExtractedMetadata": True, "extractGeoLocation": True,
                     "classificationVocabulary": {"categories": {}, "styles": [], "materials": [], "colors": [],
                                                  "allowUnlisted": True}})


@pytest.fixture(scope="module")
def handler():
    spec = importlib.util.spec_from_file_location("render3d_lambda_handler_under_test", HANDLER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeS3:
    def __init__(self, objects=None, files=None):
        self.json = dict(objects or {})
        self.files = dict(files or {})
        self.puts = []
        # Bound per instance so the module imports whether or not s3_utils carries the helper, and
        # each test reports its own failure instead of one collection error.
        self.parse_s3_uri = s3_utils.parse_s3_uri

    def download(self, bucket, key, path):
        data = self.files.get((bucket, key))
        if data is None:
            return None
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def get_json(self, bucket, key):
        return self.json.get((bucket, key))

    def put_json(self, bucket, key, payload):
        self.json[(bucket, key)] = payload
        return key

    def put_file(self, bucket, key, path, content_type):
        with open(path, "rb") as handle:
            self.files[(bucket, key)] = handle.read()
        self.puts.append((key, content_type))
        return key


def _fake(manifest=PREWRITTEN):
    files = {("asset-bucket", "xid/parts/pump.stp"): b"ISO-10303-21;"}
    objects = {} if manifest is None else {("aux-bucket", MANIFEST_KEY): dict(manifest)}
    return _FakeS3(objects, files)


def _body(**overrides):
    body = {"analysisManifestS3Location": MANIFEST_URI, "inputS3AssetFilePath": INPUT_URI,
            "fileClass": "cad", "renderBranch": "RENDER3D", "vectorSearchEnabled": True}
    body.update(overrides)
    return body


def _result(frames=2, attributes=None, facts=None, render_skipped=None, warnings=None, loader="OCP"):
    return analysis_module.AnalysisResult(
        attributes={"sys_statistics": {"vertices": 8}} if attributes is None else attributes,
        facts={"geometry": "bounding box 1 x 1 x 1 units"} if facts is None else facts,
        frames=[np.zeros((2, 2, 3), dtype=np.uint8) for _ in range(frames)],
        render_skipped=render_skipped, warnings=list(warnings or []), loader=loader)


def _invoke(handler, tmp_path, fake, result, body):
    work = {}

    def _make_dir(prefix):
        work["dir"] = tempfile.mkdtemp(prefix=prefix, dir=str(tmp_path))
        return work["dir"]

    def _save_png(frame, path):
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG" + bytes(frame.shape))

    analyze = MagicMock(return_value=result)
    with patch.object(handler, "s3", fake), \
            patch.object(handler.workdirs, "make_invocation_dir", side_effect=_make_dir), \
            patch.object(handler.workdirs, "ensure_runtime_dirs", return_value=[]), \
            patch.object(handler.image_utils, "save_png", side_effect=_save_png), \
            patch.object(handler.analysis, "analyze_local_file", analyze), \
            patch.object(handler.core, "_download_gltf_dependencies") as gltf:
        response = handler.lambda_handler(body, None)
    return response, analyze, gltf, work["dir"]


@pytest.mark.unit
class TestParseRequest:
    def test_required_fields_and_derivations(self, handler):
        request = handler.parse_request(_body())
        assert request.analysis_manifest_s3_location == MANIFEST_URI
        assert request.input_s3_asset_file_path == INPUT_URI
        assert request.input_configuration_s3_location == ""
        assert request.file_class == "cad"
        assert request.file_extension == ".stp"

    def test_normalises_the_extension_and_keeps_the_configuration_location(self, handler):
        request = handler.parse_request(_body(fileExt="STEP",
                                              inputConfigurationS3Location="s3://abkt/E1/pipeline1/config.json"))
        assert request.file_extension == ".step"
        assert handler.parse_request(_body(fileExt=".glb")).file_extension == ".glb", "the registry form passes through"
        assert request.input_configuration_s3_location == "s3://abkt/E1/pipeline1/config.json"

    def test_the_state_carries_no_render_knobs(self, handler):
        """renderViews comes from the template configuration (constructPipeline sets no render knob on the
        state) and the size gate is constructPipeline's, so the request has no field to hold them."""
        assert {field.name for field in dataclasses.fields(handler.Render3dRequest)} == {
            "analysis_manifest_s3_location", "input_s3_asset_file_path", "input_configuration_s3_location",
            "file_class", "file_extension"}

    @pytest.mark.parametrize("body,message", [
        ({"inputS3AssetFilePath": INPUT_URI}, "analysisManifestS3Location"),
        ({"analysisManifestS3Location": MANIFEST_URI, "inputS3AssetFilePath": "/local/file.stp"}, "inputS3AssetFilePath"),
        ({"analysisManifestS3Location": MANIFEST_URI, "inputS3AssetFilePath": "s3://b/noext"}, "extension"),
        ("not an object", "JSON object"),
    ])
    def test_rejects_an_incomplete_event(self, handler, body, message):
        with pytest.raises(ValueError, match=message):
            handler.parse_request(body)

    def test_render_prefix_sits_beside_the_manifest(self, handler):
        assert handler.render_prefix_for(MANIFEST_KEY) == "pipelines/system-genai-metadata/E1/render/"
        assert handler.render_prefix_for("analysis.json") == "render/"


@pytest.mark.unit
class TestRenderOptions:
    """renderViews (template tag RENDER_VIEWS) is read from the input configuration, clamped to the
    still-frame bound; anything unreadable is a warning and the default, never a fault."""

    @pytest.mark.parametrize("config,views,warning", [
        (CONFIG, 8, None),
        (json.dumps({"renderViews": "3"}), 3, None),
        (json.dumps({"renderViews": 99}), 8, None),
        (json.dumps({"renderViews": 0}), 1, None),
        (json.dumps({"renderViews": "many"}), 4, "not an integer"),
        (json.dumps({"unrelated": 1}), 4, None),
        ("{not json", 4, "unreadable"),
    ])
    def test_render_views_from_the_configuration(self, handler, config, views, warning):
        options, warnings = handler.load_render_options(config)
        assert options == {"renderViews": views}
        if warning is None:
            assert warnings == []
        else:
            assert len(warnings) == 1 and warning in warnings[0]

    def test_no_configuration_means_the_defaults(self, handler):
        assert handler.load_render_options("") == ({"renderViews": 4}, [])


@pytest.mark.unit
class TestLambdaHandler:
    def test_merges_into_the_prewritten_manifest_and_uploads_frames(self, handler, tmp_path):
        fake = _fake()
        response, analyze, _gltf, work_dir = _invoke(handler, tmp_path, fake, _result(frames=2), _body())

        manifest = fake.json[("aux-bucket", MANIFEST_KEY)]
        keys = ["pipelines/system-genai-metadata/E1/render/render3d_00.png",
                "pipelines/system-genai-metadata/E1/render/render3d_01.png"]
        assert manifest["attributes"] == {"sys_file": {"name": "pump.stp", "ext": ".stp", "sizeBytes": 13},
                                          "sys_statistics": {"vertices": 8}}
        assert manifest["renderImages"] == keys
        assert manifest["facts"] == {"geometry": "bounding box 1 x 1 x 1 units"}
        assert manifest["warnings"] == ["pre-existing"]
        assert manifest["renderSkipped"] is None
        assert manifest["renderBranch"] == "RENDER3D" and manifest["fileClass"] == "cad"
        assert manifest["schemaVersion"] == 1 and manifest["textExcerpt"] is None
        assert fake.puts == [(keys[0], "image/png"), (keys[1], "image/png")]
        assert fake.files[("aux-bucket", keys[0])].startswith(b"\x89PNG")

        assert response == {**_body(), "analysisManifestS3Location": MANIFEST_URI, "renderBranch": "RENDER3D",
                            "fileClass": "cad", "renderSkipped": None, "renderImageCount": 2, "warningCount": 1}
        assert analyze.call_args.args == (os.path.join(work_dir, "input", "pump.stp"), ".stp")
        assert analyze.call_args.kwargs == {"n_views": 4, "work_dir": work_dir}, \
            "no configuration in the event: the spec's 4 stills; the point cap stays the renderer's own"
        assert not os.path.exists(work_dir), "the invocation directory is removed (warm /tmp persists)"

    def test_attributes_only_records_unsupported_and_uploads_nothing(self, handler, tmp_path):
        fake = _fake()
        result = _result(frames=0, attributes={"sys_cad": {"source": "dxf"}}, facts={"cad": "2D DXF drawing"},
                         render_skipped="unsupported", warnings=["from analysis"])
        response, _a, _g, _w = _invoke(handler, tmp_path, fake, result, _body(fileClass="cad"))
        manifest = fake.json[("aux-bucket", MANIFEST_KEY)]
        assert manifest["renderSkipped"] == "unsupported"
        assert manifest["renderImages"] == [] and fake.puts == []
        assert manifest["warnings"] == ["pre-existing", "from analysis"]
        assert manifest["attributes"]["sys_cad"] == {"source": "dxf"} and "sys_file" in manifest["attributes"]
        assert response["renderImageCount"] == 0 and response["renderSkipped"] == "unsupported"
        assert response["warningCount"] == 2, "pre-existing + from analysis"

    def test_a_render_fault_is_recorded_as_error(self, handler, tmp_path):
        fake = _fake()
        _invoke(handler, tmp_path, fake, _result(frames=0, render_skipped="error", warnings=["Render failed: x"]),
                _body())
        assert fake.json[("aux-bucket", MANIFEST_KEY)]["renderSkipped"] == "error"

    def test_render_views_come_from_the_template_configuration(self, handler, tmp_path):
        fake = _fake()
        _r, analyze, _g, _w = _invoke(handler, tmp_path, fake, _result(frames=8),
                                      _body(inputConfigurationS3Location=CONFIG))
        assert analyze.call_args.kwargs["n_views"] == 8, "RENDER_VIEWS (8) reaches the RENDER3D branch"
        manifest = fake.json[("aux-bucket", MANIFEST_KEY)]
        assert len(manifest["renderImages"]) == 8
        assert manifest["warnings"] == ["pre-existing"]

    def test_an_unreadable_configuration_is_a_warning_not_a_fault(self, handler, tmp_path):
        fake = _fake()
        response, analyze, _g, _w = _invoke(handler, tmp_path, fake, _result(frames=1),
                                            _body(inputConfigurationS3Location="{not json"))
        assert analyze.call_args.kwargs["n_views"] == 4
        warnings = fake.json[("aux-bucket", MANIFEST_KEY)]["warnings"]
        assert warnings[0] == "pre-existing" and "unreadable" in warnings[1] and len(warnings) == 2
        assert response["renderImageCount"] == 1 and response["renderSkipped"] is None
        assert response["warningCount"] == 2, "the configuration warning is counted"

    def test_a_missing_manifest_is_created_from_the_event(self, handler, tmp_path):
        fake = _fake(manifest=None)
        _invoke(handler, tmp_path, fake, _result(frames=1), _body(fileClass="mesh"))
        manifest = fake.json[("aux-bucket", MANIFEST_KEY)]
        assert manifest == {
            "schemaVersion": 1, "fileClass": "mesh", "renderBranch": "RENDER3D",
            "attributes": {"sys_statistics": {"vertices": 8}},
            "renderImages": ["pipelines/system-genai-metadata/E1/render/render3d_00.png"],
            "textExcerpt": None, "facts": {"geometry": "bounding box 1 x 1 x 1 units"}, "warnings": [],
            "renderSkipped": None}

    def test_the_response_is_the_whole_state_merged_with_the_branch_keys(self, handler, tmp_path):
        """The branch-task contract with the state machine: Render3dTask is wired with outputPath "$.Payload"
        and no resultPath, so the return must carry every input key unchanged plus the six branch keys."""
        state = _body(assetId="a1", databaseId="db", relativePath="/parts/pump.stp", versionId="v1",
                      fileExt=".stp", externalSfnTaskToken="tok")
        response, _a, _g, _w = _invoke(handler, tmp_path, _fake(), _result(frames=2, warnings=["w1"]), state)
        assert handler.BRANCH_RESULT_KEYS == ("analysisManifestS3Location", "renderBranch", "fileClass",
                                              "renderSkipped", "renderImageCount", "warningCount")
        assert {key: response[key] for key in state} == state, "every input state key survives unchanged"
        assert {key: response[key] for key in handler.BRANCH_RESULT_KEYS} == {
            "analysisManifestS3Location": MANIFEST_URI, "renderBranch": "RENDER3D", "fileClass": "cad",
            "renderSkipped": None, "renderImageCount": 2, "warningCount": 2}
        assert set(response) == set(state) | set(handler.BRANCH_RESULT_KEYS)

    def test_the_body_envelope_is_unwrapped(self, handler, tmp_path):
        fake = _fake()
        response, _a, _g, _w = _invoke(handler, tmp_path, fake, _result(frames=1), {"body": _body()})
        assert response["renderImageCount"] == 1
        assert response["inputS3AssetFilePath"] == INPUT_URI

    def test_gltf_dependencies_are_fetched_beside_the_file(self, handler, tmp_path):
        fake = _FakeS3({("aux-bucket", MANIFEST_KEY): dict(PREWRITTEN)},
                       {("asset-bucket", "xid/scene.gltf"): b"{}"})
        _r, _a, gltf, work_dir = _invoke(handler, tmp_path, fake, _result(frames=1),
                                         _body(inputS3AssetFilePath="s3://asset-bucket/xid/scene.gltf"))
        gltf.assert_called_once_with("asset-bucket", "xid/scene.gltf", os.path.join(work_dir, "input"))

    def test_a_failed_download_raises_and_removes_the_work_dir(self, handler, tmp_path):
        fake = _FakeS3({("aux-bucket", MANIFEST_KEY): dict(PREWRITTEN)}, {})
        made = {}

        def _make_dir(prefix):
            made["dir"] = tempfile.mkdtemp(prefix=prefix, dir=str(tmp_path))
            return made["dir"]

        with patch.object(handler, "s3", fake), \
                patch.object(handler.workdirs, "make_invocation_dir", side_effect=_make_dir), \
                patch.object(handler.workdirs, "ensure_runtime_dirs", return_value=[]):
            with pytest.raises(RuntimeError, match="Unable to download"):
                handler.lambda_handler(_body(), None)
        assert not os.path.exists(made["dir"])
        assert fake.json[("aux-bucket", MANIFEST_KEY)] == PREWRITTEN, "the manifest is untouched on failure"

    def test_an_invalid_event_raises_before_any_download(self, handler):
        fake = _fake()
        with patch.object(handler, "s3", fake), patch.object(fake, "download") as download:
            with pytest.raises(ValueError):
                handler.lambda_handler({"fileClass": "cad"}, None)
        download.assert_not_called()


@pytest.mark.unit
class TestTokenAndClientDiscipline:
    def _source(self):
        with open(HANDLER_PATH, encoding="utf-8") as handle:
            return handle.read()

    def test_the_handler_never_reports_a_task_token(self):
        """PipelineEndTask is the sole reporter inside the machine (spec 6.2). The image does carry
        core's Step Functions client (core imports utils.pipeline.sfn at module level), so the property
        held here is the handler's call graph: no reference to sfn, to the Fargate runner that calls it
        (core.run / _run_preview_pipeline), or to the task-token environment variable."""
        source = self._source()
        for forbidden in ("send_task_success", "send_task_failure", "send_task_heartbeat", "stepfunctions",
                          "TaskToken", "TASK_TOKEN", "sfn", "core.run", "_run_preview_pipeline"):
            assert forbidden not in source, forbidden

    def test_every_boto3_client_comes_from_s3_utils(self):
        """s3_utils carries the adaptive retry configuration; the handler creates no client of its own."""
        source = self._source()
        assert re.search(r"boto3\.(client|resource)\s*\(", source) is None
        assert "from preview_pipeline.utils import s3_utils as s3" in source


# Runs in a fresh interpreter under the stubs this suite uses (conftest also puts the container directory
# on sys.path). A process start, a directory creation or an environment write during the import raises
# there; the result is one JSON line on stdout.
IMPORT_PROBE = r"""
import json, os, subprocess, sys, tempfile
sys.path.insert(0, sys.argv[1])
import conftest  # noqa: F401


def _forbid(name):
    def _wrapped(*args, **kwargs):
        raise AssertionError(name + " called while importing lambda_handler")
    return _wrapped


subprocess.Popen = _forbid("subprocess.Popen")
os.makedirs = _forbid("os.makedirs")
os.mkdir = _forbid("os.mkdir")
tempfile.mkdtemp = _forbid("tempfile.mkdtemp")
before = dict(os.environ)
import lambda_handler  # noqa: E402
after = dict(os.environ)
print(json.dumps({
    "display_running": lambda_handler.analysis.display.is_running(),
    "display": after.get("DISPLAY"),
    "env_changed": sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k)),
    "callable": callable(getattr(lambda_handler, "lambda_handler", None)),
}))
"""


@pytest.mark.unit
class TestImportSideEffects:
    """The Fargate renderer's job entry (preview_pipeline/analysis/batch_job.py) imports lambda_handler.lambda_handler
    inside the Fargate image, where LAMBDA_TASK_ROOT is unset and no /tmp runtime directory exists. Xvfb is
    started by display.ensure_display at render time and the work directories are made per invocation, so
    importing the module must start no process, create no directory and write no environment variable."""

    @pytest.mark.parametrize("environment", [
        pytest.param({}, id="fargate-batch-job"),
        pytest.param({"LAMBDA_TASK_ROOT": "/var/task", "HOME": "/tmp/render3d-home-absent",
                      "MPLCONFIGDIR": "/tmp/render3d-mpl-absent"}, id="lambda-cold-start"),
    ])
    def test_importing_the_handler_has_no_lambda_only_side_effect(self, environment):
        env = {key: value for key, value in os.environ.items() if key not in ("DISPLAY", "LAMBDA_TASK_ROOT")}
        env.update(environment)
        completed = subprocess.run(  # nosec B603 - this interpreter, fixed arguments, no shell
            [sys.executable, "-c", IMPORT_PROBE, TESTS_DIR], cwd=CONTAINER_DIR, env=env,
            capture_output=True, text=True, timeout=120)
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        assert report == {"display_running": False, "display": None, "env_changed": [], "callable": True}


@pytest.mark.unit
class TestS3Helpers:
    def test_parse_s3_uri(self):
        assert s3_utils.parse_s3_uri("s3://b/k/x.json") == ("b", "k/x.json")
        assert s3_utils.parse_s3_uri("s3://bucket-only") == ("bucket-only", "")
        assert s3_utils.parse_s3_uri("") == ("", "")
        assert s3_utils.parse_s3_uri("https://example") == ("", "")

    def test_get_json_returns_none_only_for_a_missing_key(self):
        from botocore.exceptions import ClientError
        missing = ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
        denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetObject")
        client = MagicMock()
        client.get_object.side_effect = missing
        with patch.object(s3_utils, "client", client):
            assert s3_utils.get_json("b", "k") is None
        client.get_object.side_effect = denied
        with patch.object(s3_utils, "client", client):
            with pytest.raises(ClientError):
                s3_utils.get_json("b", "k")

    def test_put_json_and_put_file_set_content_types(self, tmp_path):
        client = MagicMock()
        path = tmp_path / "f.png"
        path.write_bytes(b"png")
        with patch.object(s3_utils, "client", client):
            assert s3_utils.put_json("b", "k.json", {"a": 1}) == "k.json"
            assert s3_utils.put_file("b", "k.png", str(path), "image/png") == "k.png"
        json_call, file_call = client.put_object.call_args_list
        assert json_call.kwargs["ContentType"] == "application/json"
        assert json_call.kwargs["Body"] == b'{\n  "a": 1\n}'
        assert file_call.kwargs["ContentType"] == "image/png"
