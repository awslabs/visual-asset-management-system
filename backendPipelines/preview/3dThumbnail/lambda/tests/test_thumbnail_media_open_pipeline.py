#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""openPipeline for the media previews (#1550).

An image the web already shows as its own preview (extension in the self-previewable list, size strictly
under the preview limit) ends the run in the Lambda: the workflow's task token gets a success and no
state machine starts, so no container job runs and nothing is written. The option that writes a smaller
JPEG for such images, a size at or over the limit, an unreadable size and every other type start the
run as before. Media extensions are accepted only when the deployment enables media previews."""

import datetime
import importlib
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

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

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:1:stateMachine:Preview3dThumbnail")

LIMIT = 5 * 1024 * 1024
SELF_PREVIEWABLE = ".png,.jpg,.jpeg,.gif,.webp,.bmp"
MEDIA_ENV = {
    "ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.e57,.png,.jpg,.jpeg,.gif,.webp,.bmp,.tga,.tif,.tiff,.mp4",
    "SELF_PREVIEWABLE_FILEEXTENSIONS": SELF_PREVIEWABLE,
    "PREVIEW_FILE_SIZE_LIMIT_BYTES": str(LIMIT),
}
CONFIG_URI = "s3://abkt/pipelines/run/config.json"


def _load(env):
    base = {"ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.e57"}
    for name in ("SELF_PREVIEWABLE_FILEEXTENSIONS", "PREVIEW_FILE_SIZE_LIMIT_BYTES",
                 "DOWNSCALE_SELF_PREVIEWABLE_IMAGES"):
        os.environ.pop(name, None)
    base.update(env)
    with patch.dict(os.environ, base):
        if "openPipeline" in sys.modules:
            return importlib.reload(sys.modules["openPipeline"])
        return importlib.import_module("openPipeline")


def _event(name="photo.png", token="tok-1", version=""):
    return {
        "inputS3AssetFilePath": f"s3://abkt/xid/pics/{name}",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/p1/MJOB/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/.../previews/",
        "outputS3AssetMetadataPath": "s3://abkt/.../metadata/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/xid/pics/preview/p1/",
        "inputMetadataS3Location": "s3://abkt/.../metadata.json",
        "inputConfigurationS3Location": CONFIG_URI,
        "sfnExternalTaskToken": token,
        "inputVersionId": version,
        "outputS3AssetResultsPath": "s3://abkt/pipelines/p1/MJOB/output/E1/results/",
        "assetId": "xid",
        "databaseId": "db",
    }


def _s3(size=None, head_error=None):
    client = MagicMock()
    if head_error is not None:
        client.head_object.side_effect = head_error
    else:
        client.head_object.return_value = {"ContentLength": size}
    client.get_object.return_value = {"Body": MagicMock(read=MagicMock(return_value=b"{}"))}
    return client


def _run(mod, event, s3):
    start = MagicMock(return_value={"executionArn": "arn:aws:states:us-east-1:1:execution:x:y",
                                    "startDate": datetime.datetime(2026, 1, 1)})
    success, failure = MagicMock(), MagicMock()
    with patch.object(mod, "s3_client", s3), \
            patch.object(mod.sfn, "start_execution", start), \
            patch.object(mod.sfn, "send_task_success", success), \
            patch.object(mod.sfn, "send_task_failure", failure), \
            patch.object(mod.events_client, "put_events", MagicMock()):
        response = mod.lambda_handler(event, MagicMock())
    return response, start, success, failure


@pytest.mark.unit
class TestSelfPreviewableImageEndsTheRun:
    @pytest.mark.parametrize("name", ["a.png", "a.PNG", "a.jpg", "a.jpeg", "a.gif", "a.webp", "a.bmp"])
    def test_a_small_displayable_image_succeeds_without_starting_the_state_machine(self, name):
        mod = _load(MEDIA_ENV)
        response, start, success, failure = _run(mod, _event(name), _s3(LIMIT - 1))
        assert response["statusCode"] == 200
        start.assert_not_called()
        failure.assert_not_called()
        success.assert_called_once()
        assert success.call_args.kwargs["taskToken"] == "tok-1"
        output = json.loads(success.call_args.kwargs["output"])
        assert output["status"] == "Pipeline Success"
        expected = (f"No preview was generated for '{name}': the file is under 5 MiB and is already "
                    f"shown as its own preview.")
        assert output["message"] == expected
        assert response["body"]["message"] == expected

    def test_the_output_has_the_shape_the_state_machine_end_sends(self):
        mod = _load(MEDIA_ENV)
        _, _, success, _ = _run(mod, _event(), _s3(10))
        assert json.loads(success.call_args.kwargs["output"])["status"] == "Pipeline Success"

    def test_exactly_the_limit_starts_a_run(self):
        mod = _load(MEDIA_ENV)
        response, start, success, _ = _run(mod, _event(), _s3(LIMIT))
        assert response["statusCode"] == 200 and start.call_count == 1
        success.assert_not_called()

    def test_over_the_limit_starts_a_run(self):
        mod = _load(MEDIA_ENV)
        _, start, success, _ = _run(mod, _event(), _s3(LIMIT * 3))
        assert start.call_count == 1
        success.assert_not_called()

    @pytest.mark.parametrize("name", ["a.tif", "a.tiff", "a.tga", "a.mp4"])
    def test_tif_tga_and_video_under_the_limit_start_a_run(self, name):
        mod = _load(MEDIA_ENV)
        _, start, success, _ = _run(mod, _event(name), _s3(1000))
        assert start.call_count == 1
        success.assert_not_called()

    def test_a_3d_file_is_unaffected(self):
        mod = _load(MEDIA_ENV)
        _, start, success, _ = _run(mod, _event("model.glb"), _s3(1000))
        assert start.call_count == 1
        success.assert_not_called()

    def test_the_option_on_starts_a_run_for_a_small_image(self):
        mod = _load({**MEDIA_ENV, "DOWNSCALE_SELF_PREVIEWABLE_IMAGES": "true"})
        _, start, success, _ = _run(mod, _event(), _s3(1000))
        assert start.call_count == 1
        success.assert_not_called()

    @pytest.mark.parametrize("value", ["false", "", "no", "0"])
    def test_the_option_is_off_unless_it_is_true(self, value):
        mod = _load({**MEDIA_ENV, "DOWNSCALE_SELF_PREVIEWABLE_IMAGES": value})
        _, start, _, _ = _run(mod, _event(), _s3(1000))
        start.assert_not_called()

    def test_an_unreadable_size_starts_the_run(self):
        mod = _load(MEDIA_ENV)
        _, start, success, _ = _run(mod, _event(), _s3(head_error=Exception("AccessDenied")))
        assert start.call_count == 1
        success.assert_not_called()

    def test_the_head_reads_the_version_the_run_reads(self):
        mod = _load(MEDIA_ENV)
        s3 = _s3(10)
        _run(mod, _event(version="v-9"), s3)
        assert s3.head_object.call_args.kwargs["VersionId"] == "v-9"

    def test_a_run_with_no_task_token_ends_without_a_callback(self):
        mod = _load(MEDIA_ENV)
        response, start, success, _ = _run(mod, _event(token=""), _s3(10))
        assert response["statusCode"] == 200
        start.assert_not_called()
        success.assert_not_called()

    def test_a_failed_callback_is_reported_as_a_failure(self):
        mod = _load(MEDIA_ENV)
        s3 = _s3(10)
        start = MagicMock()
        failure = MagicMock()
        with patch.object(mod, "s3_client", s3), patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.sfn, "send_task_success", MagicMock(side_effect=Exception("TaskTimedOut"))), \
                patch.object(mod.sfn, "send_task_failure", failure):
            response = mod.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 500
        start.assert_not_called()
        failure.assert_called_once()


@pytest.mark.unit
class TestMediaExtensionsNeedTheSwitch:
    @pytest.mark.parametrize("name", ["a.png", "a.tif", "a.mp4"])
    def test_a_media_file_is_refused_when_media_previews_are_off(self, name):
        mod = _load({})  # no media extensions in the allow list, no self-previewable list
        response, start, success, failure = _run(mod, _event(name), _s3(10))
        assert response["statusCode"] == 400
        start.assert_not_called()
        success.assert_not_called()
        failure.assert_called_once()
        assert "cannot process file type" in failure.call_args.kwargs["error"]

    def test_with_the_switch_off_a_small_png_is_never_skipped_as_its_own_preview(self):
        mod = _load({"ALLOWED_INPUT_FILEEXTENSIONS": ".glb,.png"})  # no self-previewable env at all
        _, start, success, _ = _run(mod, _event("a.png"), _s3(10))
        assert start.call_count == 1
        success.assert_not_called()

    def test_the_decision_helpers_read_the_environment(self):
        mod = _load(MEDIA_ENV)
        assert mod.self_previewable_extensions() == SELF_PREVIEWABLE.split(",")
        assert mod.preview_size_limit_bytes() == LIMIT
        mod = _load({})
        assert mod.self_previewable_extensions() == [] and mod.preview_size_limit_bytes() is None
        mod = _load({"PREVIEW_FILE_SIZE_LIMIT_BYTES": "junk"})
        assert mod.preview_size_limit_bytes() is None


@pytest.mark.unit
class TestNoPreviewResultDocument:
    """The Lambda records why no run started in the execution's results, before the task ends."""

    def test_the_document_is_written_under_the_results_folder_before_the_task_succeeds(self):
        mod = _load(MEDIA_ENV)
        s3 = _s3(10)
        order = []
        s3.put_object.side_effect = lambda **kw: order.append("put")
        start = MagicMock()
        success = MagicMock(side_effect=lambda **kw: order.append("success"))
        with patch.object(mod, "s3_client", s3), patch.object(mod.sfn, "start_execution", start), \
                patch.object(mod.sfn, "send_task_success", success):
            response = mod.lambda_handler(_event("photo.png"), MagicMock())
        assert response["statusCode"] == 200 and order == ["put", "success"]
        call = s3.put_object.call_args.kwargs
        assert call["Bucket"] == "abkt"
        assert call["Key"] == "pipelines/p1/MJOB/output/E1/results/preview-not-generated.json"
        document = json.loads(call["Body"].decode("utf-8"))
        assert document == {
            "schema": "vams.mediaPreview.noPreview/v1",
            "file": "photo.png",
            "reason": "alreadyShownAsOwnPreview",
            "message": ("No preview was generated for 'photo.png': the file is under 5 MiB and is "
                        "already shown as its own preview."),
        }

    def test_a_failed_write_still_ends_the_run_successfully(self):
        mod = _load(MEDIA_ENV)
        s3 = _s3(10)
        s3.put_object.side_effect = Exception("AccessDenied")
        response, start, success, failure = _run(mod, _event("photo.png"), s3)
        assert response["statusCode"] == 200
        success.assert_called_once()
        failure.assert_not_called()
        start.assert_not_called()

    def test_no_results_path_writes_nothing(self):
        mod = _load(MEDIA_ENV)
        s3 = _s3(10)
        event = _event("photo.png")
        event.pop("outputS3AssetResultsPath")
        response, _, success, _ = _run(mod, event, s3)
        assert response["statusCode"] == 200
        success.assert_called_once()
        s3.put_object.assert_not_called()

    def test_a_run_that_starts_writes_no_document(self):
        mod = _load(MEDIA_ENV)
        s3 = _s3(LIMIT * 2)
        _, start, _, _ = _run(mod, _event("big.png"), s3)
        assert start.call_count == 1
        s3.put_object.assert_not_called()
