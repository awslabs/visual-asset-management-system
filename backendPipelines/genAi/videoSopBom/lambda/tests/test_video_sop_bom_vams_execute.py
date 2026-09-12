#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""vamsExecuteVideoSopBomPipeline: the workflow entry point.

The workflow's task token is captured before any other work so a manifest read failure reaches the
abort; the gates that need no download run in cost order (container entries, count, extension, one
HeadObject per entry for bytes); every refusal is ONE SendTaskFailure on the external token with a
short code in `error` and one readable sentence in `cause`, and NO invoke of openPipeline. The
nested RequestResponse invoke keeps the canonical StatusCode + FunctionError guard."""

import importlib
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE = "vamsExecuteVideoSopBomPipeline"
_TOKEN = "tok-123"
_GIB = 1024 ** 3


def _load():
    if _MODULE in sys.modules:
        return importlib.reload(sys.modules[_MODULE])
    return importlib.import_module(_MODULE)


def _entry(name, version_id="", asset_id="xidV", database_id="dbV"):
    """A manifest inputFiles entry with the 8 keys the backend writes."""
    return {"relativePath": f"/{name}", "databaseId": database_id, "assetId": asset_id,
            "assetRootS3Key": f"{asset_id}/",
            "auxPreviewPrefix": f"{database_id}/{asset_id}/{name}/preview",
            "bucket": "abkt", "key": f"{asset_id}/{name}", "versionId": version_id}


def _manifest(entries):
    return {
        "schemaVersion": 1,
        "inputFiles": entries,
        "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
        "outputs": {"bucket": "abkt",
                    "files": "pipelines/genai-video-sop-bom/JOB/output/E1/files/",
                    "previews": "pipelines/genai-video-sop-bom/JOB/output/E1/previews/",
                    "metadata": "pipelines/genai-video-sop-bom/JOB/output/E1/metadata/",
                    "results": "pipelines/genai-video-sop-bom/JOB/output/E1/results/"},
        "outputTarget": {"locationType": "asset", "assetId": "xidV", "databaseId": "dbV",
                         "fileBaseExecutionPathExtension": "/E1/"},
        "auxBucket": "aux", "auxTempPrefix": "pipelines/genai-video-sop-bom/E1/",
        "auxPreviewPipelineSuffix": "",
        "systemConfig": {"orchestrationBusArn": "arn:bus",
                         "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1"},
    }


def _body():
    return {"TaskToken": _TOKEN,
            "inputManifestS3Location":
                "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/manifest.json",
            "inputConfigurationS3Location":
                "s3://abkt/pipelines/workflowExecutionInputs/E1/pipeline1/config.json",
            "executingUserName": "smoke", "executingRequestContext": "smoke"}


def _s3(manifest, sizes=None):
    """get_object serves the manifest; head_object serves ContentLength by key (default 1 GiB)."""
    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(manifest).encode("utf-8"))}
    sizes = sizes or {}

    def head_object(Bucket, Key, **kwargs):
        return {"ContentLength": sizes.get(Key, _GIB)}

    s3.head_object.side_effect = head_object
    return s3


def _run(mod, manifest, sizes=None, invoke_response=None, body=None, s3=None):
    """(response, invoke mock, sfn mock) for one handler call with the AWS clients stubbed."""
    s3 = s3 or _s3(manifest, sizes)
    invoke = MagicMock(return_value=invoke_response or {"StatusCode": 200})
    sfn = MagicMock()
    with patch.object(mod, "s3_client", s3), patch.object(mod, "sfn_client", sfn), \
            patch.object(mod.lambda_client, "invoke", invoke):
        resp = mod.lambda_handler({"body": json.dumps(body or _body())}, MagicMock())
    return resp, invoke, sfn


def _rejection(sfn):
    """The single send_task_failure call's (error, cause), with the cause contract asserted. The
    budget is read from the loaded module (every test calls _load() first); the value itself is
    pinned once, in TestModuleContract."""
    assert sfn.send_task_failure.call_count == 1
    kwargs = sfn.send_task_failure.call_args.kwargs
    assert kwargs["taskToken"] == _TOKEN
    assert not kwargs["cause"].startswith(kwargs["error"])
    assert len(kwargs["cause"]) <= sys.modules[_MODULE].MAX_CAUSE_CHARS
    return kwargs["error"], kwargs["cause"]


@pytest.mark.unit
class TestHappyPath:
    def test_forwards_the_input_list_and_locations_not_content(self):
        mod = _load()
        entries = [_entry("part1.mp4"), _entry("part2.mov", version_id="v2")]
        resp, invoke, sfn = _run(mod, _manifest(entries))
        assert resp["statusCode"] == 200
        sfn.send_task_failure.assert_not_called()
        payload = json.loads(invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert payload["inputFiles"] == entries
        assert payload["inputManifestS3Location"] == _body()["inputManifestS3Location"]
        assert payload["inputMetadataS3Location"] == _manifest([])["inputMetadataS3Location"]
        assert payload["inputConfigurationS3Location"] == _body()["inputConfigurationS3Location"]
        assert payload["sfnExternalTaskToken"] == _TOKEN
        assert payload["orchestrationEventPrefix"] == "vams.prod.execution.E1.pipeline.P1"
        assert payload["assetId"] == "xidV" and payload["databaseId"] == "dbV"
        assert payload["outputS3AssetFilesPath"] == \
            "s3://abkt/pipelines/genai-video-sop-bom/JOB/output/E1/files/"
        assert payload["inputOutputS3AssetAuxiliaryFilesPath"] == \
            "s3://aux/pipelines/genai-video-sop-bom/E1/"
        assert payload["executingUserName"] == "smoke"
        for absent in ("TaskToken", "inputMetadata", "inputParameters"):
            assert absent not in payload

    def test_the_invoke_is_request_response_against_the_configured_function(self):
        mod = _load()
        _, invoke, _ = _run(mod, _manifest([_entry("part1.mp4")]))
        assert invoke.call_args.kwargs["FunctionName"] == os.environ["OPEN_PIPELINE_FUNCTION_NAME"]
        assert invoke.call_args.kwargs["InvocationType"] == "RequestResponse"

    def test_one_head_object_per_entry_with_version_id_only_when_truthy_and_not_null(self):
        mod = _load()
        entries = [_entry("a.mp4", version_id=""), _entry("b.mp4", version_id="null"),
                   _entry("c.mp4", version_id="v1")]
        s3 = _s3(_manifest(entries))
        resp, _, _ = _run(mod, _manifest(entries), s3=s3)
        assert resp["statusCode"] == 200
        calls = [c.kwargs for c in s3.head_object.call_args_list]
        assert len(calls) == 3
        assert calls[0] == {"Bucket": "abkt", "Key": "xidV/a.mp4"}
        assert calls[1] == {"Bucket": "abkt", "Key": "xidV/b.mp4"}
        assert calls[2] == {"Bucket": "abkt", "Key": "xidV/c.mp4", "VersionId": "v1"}

    def test_the_source_never_calls_enforce_single_input_file(self):
        # Durable guard: every other vamsExecute in the tree calls it, and copying one back in
        # would silently refuse every 2-4 video run. Not a temporary test.
        source = open(os.path.join(_LAMBDA_DIR, f"{_MODULE}.py"), encoding="utf-8").read()
        # Positive control: the file read is the handler (an empty file passes the negative alone).
        assert "manifestHelper.resolve_pipeline_inputs(" in source
        assert "enforce_single_input_file" not in source


@pytest.mark.unit
class TestTokenAndManifest:
    def test_missing_task_token_returns_500_without_a_callback(self):
        mod = _load()
        body = _body()
        del body["TaskToken"]
        resp, invoke, sfn = _run(mod, _manifest([_entry("a.mp4")]), body=body)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        sfn.send_task_failure.assert_not_called()

    def test_a_manifest_read_failure_reaches_the_abort(self):
        # The token is captured BEFORE resolve_pipeline_inputs, so the raise from fetch_manifest
        # is reported rather than leaving the workflow task waiting out its 8.5 h taskTimeout.
        mod = _load()
        s3 = MagicMock()
        s3.get_object.side_effect = Exception("AccessDenied")
        resp, invoke, sfn = _run(mod, None, s3=s3)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomPipelineError"
        assert "manifest" in cause


@pytest.mark.unit
class TestCountAndContainerGates:
    def test_zero_files_are_rejected_before_any_head_object(self):
        mod = _load()
        s3 = _s3(_manifest([]))
        resp, invoke, sfn = _run(mod, _manifest([]), s3=s3)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        s3.head_object.assert_not_called()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomInputRejected"
        assert "at least 1" in cause

    def test_five_files_are_rejected_naming_count_and_cap(self):
        mod = _load()
        entries = [_entry(f"part{i}.mp4") for i in range(5)]
        resp, invoke, sfn = _run(mod, _manifest(entries))
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomInputRejected"
        assert "5 video files" in cause and "at most 4" in cause

    def test_four_files_are_accepted(self):
        # The positive control for the count gate.
        mod = _load()
        resp, invoke, _ = _run(mod, _manifest([_entry(f"part{i}.mp4") for i in range(4)]))
        assert resp["statusCode"] == 200
        invoke.assert_called_once()

    def test_a_container_entry_is_rejected_even_when_the_count_passes(self):
        mod = _load()
        folder = dict(_entry("videos"), key="xidV/videos/", relativePath="/videos/")
        resp, invoke, sfn = _run(mod, _manifest([folder]))
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomInputRejected"
        assert "xidV/videos/" in cause and "whole-asset or folder" in cause


@pytest.mark.unit
class TestExtensionGate:
    @pytest.mark.parametrize("extension", [".pdf", ".mp", ".avi", ""])
    def test_an_unlisted_extension_is_rejected_before_head_object(self, extension):
        mod = _load()
        entries = [_entry("part1.mp4"), _entry(f"notes{extension}")]
        s3 = _s3(_manifest(entries))
        resp, invoke, sfn = _run(mod, _manifest(entries), s3=s3)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        s3.head_object.assert_not_called()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomInputRejected"
        assert f"notes{extension}" in cause
        assert "accepts .mp4, .mov, .m4v, .webm, .mkv" in cause

    @pytest.mark.parametrize("extension", [".mp4", ".mov", ".m4v", ".webm", ".mkv", ".MP4", ".Mkv"])
    def test_a_listed_extension_is_accepted_in_either_case(self, extension):
        mod = _load()
        resp, invoke, sfn = _run(mod, _manifest([_entry(f"part1{extension}")]))
        assert resp["statusCode"] == 200
        invoke.assert_called_once()
        sfn.send_task_failure.assert_not_called()


@pytest.mark.unit
class TestByteGates:
    def test_an_oversized_file_is_rejected_naming_file_size_and_cap(self):
        mod = _load()
        entries = [_entry("part1.mp4"), _entry("part3.mov")]
        resp, invoke, sfn = _run(mod, _manifest(entries), sizes={"xidV/part3.mov": int(5.2 * _GIB)})
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomInputRejected"
        assert "part3.mov is 5.2 GB" in cause and "at most 4.0 GB per video" in cause

    def test_a_total_over_the_cap_is_rejected_naming_total_and_cap(self):
        # Two 4.0 GB files pass the per-file cap; the total cap is lowered to 7 GB (7168 MB) so
        # the SUM is what trips — proving real sizes were added, not compared one at a time.
        mod = _load()
        entries = [_entry("part1.mp4"), _entry("part2.mp4")]
        sizes = {"xidV/part1.mp4": 4 * _GIB, "xidV/part2.mp4": 4 * _GIB}
        with patch.object(mod, "MAX_TOTAL_INPUT_SIZE_MB", 7168):
            resp, invoke, sfn = _run(mod, _manifest(entries), sizes=sizes)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomInputRejected"
        assert "total 8.0 GB" in cause and "at most 7.0 GB in total" in cause

    def test_sizes_exactly_at_the_caps_are_accepted(self):
        mod = _load()
        entries = [_entry(f"part{i}.mp4") for i in range(4)]
        sizes = {e["key"]: 4 * _GIB for e in entries}
        resp, invoke, _ = _run(mod, _manifest(entries), sizes=sizes)
        assert resp["statusCode"] == 200
        invoke.assert_called_once()

    def test_a_cap_below_one_gigabyte_renders_in_whole_megabytes(self):
        # The smoke round (WP06 R3/R3b) lowers a cap to a few MB and asserts the configured integer
        # appears in the cause; a GB-only rendering would read "0.0 GB" for both values and name
        # neither (spec D5: the cause names both values).
        mod = _load()
        entries = [_entry("size-big.mp4")]
        with patch.object(mod, "MAX_VIDEO_FILE_SIZE_MB", 3):
            resp, invoke, sfn = _run(mod, _manifest(entries),
                                     sizes={"xidV/size-big.mp4": int(3.5 * mod.MIB)})
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomInputRejected"
        assert "size-big.mp4 is 3.5 MB" in cause and "at most 3 MB per video" in cause

    def test_a_head_object_client_error_is_the_readable_head_object_cause(self):
        mod = _load()
        entries = [_entry("part1.mp4")]
        s3 = _s3(_manifest(entries))
        s3.head_object.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "HeadObject")
        resp, invoke, sfn = _run(mod, _manifest(entries), s3=s3)
        assert resp["statusCode"] == 500
        invoke.assert_not_called()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomPipelineError"
        assert cause == ("/part1.mp4: HeadObject on s3://abkt/xidV/part1.mp4 failed (AccessDenied) "
                         "— the pipeline Lambda cannot read this asset bucket")


@pytest.mark.unit
class TestInvokeGuards:
    def test_function_error_on_a_200_invoke_is_a_failure(self):
        mod = _load()
        resp, invoke, sfn = _run(mod, _manifest([_entry("a.mp4")]),
                                 invoke_response={"StatusCode": 200, "FunctionError": "Unhandled"})
        assert resp["statusCode"] == 500
        invoke.assert_called_once()
        code, cause = _rejection(sfn)
        assert code == "VideoSopBomPipelineError"
        assert "Invoke Open Pipeline Lambda Failed" in cause and "Unhandled" in cause

    def test_a_non_200_status_code_is_a_failure(self):
        mod = _load()
        resp, _, sfn = _run(mod, _manifest([_entry("a.mp4")]), invoke_response={"StatusCode": 500})
        assert resp["statusCode"] == 500
        code, _ = _rejection(sfn)
        assert code == "VideoSopBomPipelineError"

    def test_a_callback_denial_does_not_raise_out_of_the_handler(self):
        mod = _load()
        sfn = MagicMock()
        sfn.send_task_failure.side_effect = Exception("AccessDeniedException")
        with patch.object(mod, "s3_client", _s3(_manifest([]))), patch.object(mod, "sfn_client", sfn), \
                patch.object(mod.lambda_client, "invoke", MagicMock()):
            resp = mod.lambda_handler({"body": json.dumps(_body())}, MagicMock())
        assert resp["statusCode"] == 500
        # The callback was ATTEMPTED and its denial swallowed — a handler that never reached the
        # abort would also return 500.
        sfn.send_task_failure.assert_called_once()
        assert sfn.send_task_failure.call_args.kwargs["taskToken"] == _TOKEN


@pytest.mark.unit
class TestModuleContract:
    """The registry rule: every cap is read at import with int(os.environ[...]) and no default, so
    a builder that omits or mistypes one fails at import rather than at the first run that should
    have been refused. Each case removes or corrupts one variable, reloads, and restores."""

    @pytest.mark.parametrize("name", [
        "VIDEO_SOP_BOM_MAX_VIDEO_FILES", "VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB",
        "VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB", "OPEN_PIPELINE_FUNCTION_NAME",
    ])
    def test_a_missing_required_variable_fails_at_import(self, name, monkeypatch):
        monkeypatch.delenv(name)
        try:
            with pytest.raises(KeyError, match=name):
                _load()
        finally:
            monkeypatch.undo()
            _load()

    def test_a_non_integer_cap_fails_at_import(self, monkeypatch):
        monkeypatch.setenv("VIDEO_SOP_BOM_MAX_VIDEO_FILES", "four")
        try:
            with pytest.raises(ValueError):
                _load()
        finally:
            monkeypatch.undo()
            _load()

    def test_failure_for_truncates_to_the_stored_budget(self):
        # SendTaskFailure rejects a cause over 32768 characters (ValidationException) and the stored
        # executionError budget is 16384: a Lambda traceback or a long file list must be cut, or the
        # callback itself fails and the workflow task waits out its 8.5 h taskTimeout.
        mod = _load()
        assert mod.MAX_CAUSE_CHARS == 16384
        assert mod.failure_for(Exception("y" * 40000)) == ("VideoSopBomPipelineError", "y" * 16384)
        rejection = mod.PipelineRejection("VideoSopBomInputRejected", "z" * 40000)
        assert mod.failure_for(rejection) == ("VideoSopBomInputRejected", "z" * 16384)
