#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import io
import json
import os
from unittest.mock import patch

import pytest

from miris_upload_fakes import claim_held_by

pytestmark = pytest.mark.unit

# This module's boto3 clients are constructed at import time from AWS_REGION. Set it
# defensively so this test file does not depend on the ambient shell environment.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

AUX = "s3://vams-aux-bucket/pipelines/miris-upload/exec-aaaa1111/"
FP = "f" * 64
CLAIM_KEY = f"locks/miris-upload/db1/a1/{FP}.claim"


def _batch_cause(exit_code=None, status_reason="Essential container in task exited"):
    """The Cause a failed .sync BatchSubmitJob state carries: the Batch job description as JSON."""
    container = {"Image": "x", "LogStreamName": "MirisUploadJob/default/abc"}
    if exit_code is not None:
        container["ExitCode"] = exit_code
    return json.dumps({
        "JobId": "job-1",
        "JobName": "MirisUpload_x",
        "Status": "FAILED",
        "StatusReason": status_reason,
        "Container": container,
        "Attempts": [{"Container": dict(container)}],
    })


@pytest.fixture
def end():
    import importlib
    import pipelineEnd
    return importlib.reload(pipelineEnd)


def _event(**overrides):
    event = {
        "externalSfnTaskToken": "tok-1",
        "status": "success",
        "databaseId": "db1",
        "assetId": "a1",
        "claimFingerprint": FP,
        "inputOutputS3AssetAuxiliaryFilesPath": AUX,
    }
    event.update(overrides)
    return event


def test_success_reports_task_success_and_keeps_the_claim(end):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        end.lambda_handler(_event(), None)

    output = json.loads(sfn.send_task_success.call_args.kwargs["output"])
    assert output == {"status": "success"}
    # The claim is what collapses the trigger fan-out; a successful run must not remove it.
    s3.delete_object.assert_not_called()


def test_failure_reports_task_failure_and_releases_the_claim(end):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        claim_held_by(s3, "tok-1")
        end.lambda_handler(_event(status="failed", errorType="States.TaskFailed",
                                  errorCause=_batch_cause(4)), None)

    sfn.send_task_success.assert_not_called()
    assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-1"
    assert sfn.send_task_failure.call_args.kwargs["cause"] == end.EXIT_CODE_REASONS[4]
    s3.delete_object.assert_called_once_with(
        Bucket="vams-aux-bucket", Key=CLAIM_KEY)


def test_failure_without_a_token_releases_a_claim_recorded_without_an_owner(end):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        claim_held_by(s3, "")
        end.lambda_handler(_event(status="failed", externalSfnTaskToken=""), None)

    sfn.send_task_failure.assert_not_called()
    s3.delete_object.assert_called_once()


@pytest.mark.parametrize("route, held_by", [
    ("failure_branch", "tok-run-b"), ("execution_ended", "tok-run-b"), ("execution_ended", None),
], ids=["held-by-retry-failure-branch", "held-by-retry-execution-ended", "already-released"])
def test_a_claim_not_held_by_this_run_is_left_in_place(end, route, held_by):
    # Run A failed and was reported once already; a retried run B may hold a new claim at the key.
    from botocore.exceptions import ClientError

    event = _event(status="failed") if route == "failure_branch" else _status_event("FAILED")
    with patch.object(end, "sfn"), patch.object(end, "s3_client") as s3:
        if held_by:
            claim_held_by(s3, held_by)
        else:
            s3.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey", "Message": "gone"}}, "GetObject")
        end.lambda_handler(event, None)

    s3.get_object.assert_called_once_with(Bucket="vams-aux-bucket", Key=CLAIM_KEY)
    s3.delete_object.assert_not_called()


def test_failure_cause_is_truncated(end):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client"):
        end.lambda_handler(_event(status="failed", errorCause=json.dumps({"errorMessage": "x" * 5000})), None)

    assert len(sfn.send_task_failure.call_args.kwargs["cause"]) == 256


def test_release_failure_does_not_mask_the_pipeline_failure(end):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        claim_held_by(s3, "tok-1")
        s3.delete_object.side_effect = Exception("AccessDenied")
        result = end.lambda_handler(_event(status="failed"), None)

    assert result["statusCode"] == 200
    sfn.send_task_failure.assert_called_once()


@pytest.mark.parametrize("exit_code", sorted([1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16, 17, 18]))
def test_every_container_exit_code_maps_to_a_reason_not_the_job_description(end, exit_code):
    cause = _batch_cause(exit_code)
    reason = end.failure_reason("States.TaskFailed", cause)

    assert reason == end.EXIT_CODE_REASONS[exit_code].format(tag="vams-asset-<assetId>")
    assert "JobId" not in reason and "{" not in reason


def test_too_little_time_left_says_nothing_reached_miris_and_to_run_again(end):
    reason = end.failure_reason("States.TaskFailed", _batch_cause(14), "a1")

    assert "Nothing was uploaded to Miris" in reason
    assert "again" in reason
    assert "vams-asset" not in reason
    assert len(reason) <= end.CAUSE_MAX_CHARS


def test_a_failure_after_the_miris_asset_exists_says_it_is_kept_and_names_its_tag(end):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client"):
        end.lambda_handler(_event(status="failed", errorType="States.TaskFailed",
                                  errorCause=_batch_cause(8)), None)

    cause = sfn.send_task_failure.call_args.kwargs["cause"]
    assert "kept" in cause
    assert cause.endswith("find it in Miris by the tag vams-asset-a1.")


@pytest.mark.parametrize("asset_id, tag", [("a" * 63, "vams-asset-" + "a" * 63), (None, "vams-asset-<assetId>")])
def test_a_kept_asset_reason_fits_the_cause_limit(end, asset_id, tag):
    reason = end.failure_reason("States.TaskFailed", _batch_cause(1), *([asset_id] if asset_id else []))

    assert reason.endswith(f"{tag}.")
    assert len(reason) <= end.CAUSE_MAX_CHARS


@pytest.mark.parametrize("error_type, cause, expected", [
    ("States.TaskFailed", _batch_cause(None, "Job attempt duration exceeded timeout"), "time-limit"),
    ("States.TaskFailed", _batch_cause(137), "137"),
    ("States.TaskFailed", _batch_cause(None, "CannotPullContainerError: pull access denied"),
     "CannotPullContainerError: pull access denied"),
    ("ValueError", json.dumps({"errorMessage": "outputS3AssetMetadataPath is missing", "errorType": "ValueError"}),
     "The Miris upload could not be prepared: outputS3AssetMetadataPath is missing"),
], ids=["attempt-timeout", "unmapped-exit-code", "never-started", "construct-failure"])
def test_other_failures_map_to_a_reason_not_the_job_description(end, error_type, cause, expected):
    reason = end.failure_reason(error_type, cause)

    if expected == "time-limit":
        assert reason == end.CONTAINER_TIME_LIMIT_REASON
    else:
        assert expected in reason
    assert "JobId" not in reason


def _status_event(status, **input_overrides):
    execution_input = {
        "externalSfnTaskToken": "tok-1",
        "databaseId": "db1",
        "assetId": "a1",
        "claimFingerprint": FP,
        "inputOutputS3AssetAuxiliaryFilesPath": AUX,
    }
    execution_input.update(input_overrides)
    return {
        "source": "aws.states",
        "detail-type": "Step Functions Execution Status Change",
        "detail": {
            "executionArn": "arn:aws:states:us-east-1:123456789012:execution:sm:MirisUpload_x",
            "status": status,
            "input": json.dumps(execution_input),
        },
    }


@pytest.mark.parametrize("status", ["TIMED_OUT", "ABORTED", "FAILED"])
def test_execution_status_event_releases_the_claim_and_fails_the_outer_token(end, status):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        claim_held_by(s3, "tok-1")
        result = end.lambda_handler(_status_event(status), None)

    assert result["statusCode"] == 200
    s3.delete_object.assert_called_once_with(
        Bucket="vams-aux-bucket", Key=CLAIM_KEY)
    assert sfn.send_task_failure.call_args.kwargs["taskToken"] == "tok-1"
    assert sfn.send_task_failure.call_args.kwargs["cause"] == end.EXECUTION_STATUS_REASONS[status]
    sfn.send_task_success.assert_not_called()


@pytest.mark.parametrize("code", ["TaskTimedOut", "TaskDoesNotExist", "InvalidToken"])
def test_an_already_closed_outer_token_is_not_an_error(end, code):
    from botocore.exceptions import ClientError

    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client"):
        sfn.send_task_failure.side_effect = ClientError(
            {"Error": {"Code": code, "Message": "closed"}}, "SendTaskFailure")
        result = end.lambda_handler(_status_event("FAILED"), None)

    assert result["statusCode"] == 200


def test_any_other_callback_error_propagates_so_the_event_is_retried(end):
    from botocore.exceptions import ClientError

    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client"):
        sfn.send_task_failure.side_effect = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "SendTaskFailure")
        with pytest.raises(ClientError):
            end.lambda_handler(_status_event("TIMED_OUT"), None)


def test_execution_status_event_without_input_is_a_no_op(end):
    event = _status_event("ABORTED")
    del event["detail"]["input"]
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        result = end.lambda_handler(event, None)

    assert result["body"] == "no-op"
    sfn.send_task_failure.assert_not_called()
    s3.delete_object.assert_not_called()


def test_a_failed_run_without_a_claim_reads_and_deletes_nothing(end):
    """A manual run writes no claim, so its failure has none to release."""
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        end.lambda_handler(_event(status="failed", claimFingerprint=""), None)

    s3.get_object.assert_not_called()
    s3.delete_object.assert_not_called()
    sfn.send_task_failure.assert_called_once()


@pytest.mark.parametrize("exit_code, log_key", [
    (10, "ambiguous_root"), (11, "unreadable_layer"), (12, "reference_outside_asset"),
    (15, "root_not_renderable"), (16, "root_file_invalid")])
def test_a_root_or_reference_failure_names_its_log_entry(end, exit_code, log_key):
    reason = end.failure_reason("States.TaskFailed", _batch_cause(exit_code))

    assert log_key in reason
    assert len(reason) <= end.CAUSE_MAX_CHARS


def test_the_ambiguous_root_reason_points_to_miris_root_file_and_the_logged_candidates(end):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client"):
        end.lambda_handler(_event(status="failed", errorCause=_batch_cause(10)), None)

    cause = sfn.send_task_failure.call_args.kwargs["cause"]
    assert cause == end.EXIT_CODE_REASONS[10] and len(cause) < end.CAUSE_MAX_CHARS
    assert "miris_root_file" in cause and "ambiguous_root entry lists them" in cause
    assert "remove or move the files that are not the scene" in cause
    # Referencing the other roots from one of them renders the scene twice.
    assert "reference" not in cause.lower()


REPORT_URI = "s3://assets/pipelines/m/job/output/E1/results/ambiguous_root.json"


def _report_s3(s3, body):
    """A mocked S3 client whose ambiguous_root report holds `body` (raises when None)."""
    def get_object(Bucket, Key):
        assert (Bucket, Key) == ("assets", "pipelines/m/job/output/E1/results/ambiguous_root.json")
        if body is None:
            raise Exception("AccessDenied")
        return {"Body": io.BytesIO(body.encode() if isinstance(body, str) else json.dumps(body).encode())}
    s3.get_object.side_effect = get_object
    return s3


def test_the_ambiguous_root_reason_names_the_reported_roots(end):
    roots = [f"scenes/{'deep/' * 80}scene_{i:02}.usda" for i in range(10)]
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        _report_s3(s3, {"roots": roots, "count": 14})
        end.lambda_handler(_event(status="failed", claimFingerprint="", errorCause=_batch_cause(10),
                                  ambiguousRootReportS3Uri=REPORT_URI), None)

    cause = sfn.send_task_failure.call_args.kwargs["cause"]
    assert len(cause) <= end.AMBIGUOUS_ROOT_CAUSE_MAX_CHARS
    assert cause.startswith("The asset has several root USD files")
    assert "scene_00.usda" not in cause  # each path is bounded; its head is kept
    assert roots[0][:end.AMBIGUOUS_ROOT_PATH_MAX_CHARS] in cause
    assert " and 4 more. " in cause and "miris_root_file" in cause and "remove or move" in cause
    assert "reference" not in cause.lower()


@pytest.mark.parametrize("report", [None, "not json", {"roots": []}, {"count": 2}],
                         ids=["unreadable", "malformed", "no-roots", "no-roots-key"])
def test_without_a_readable_report_the_reason_points_to_the_log(end, report):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        _report_s3(s3, report)
        end.lambda_handler(_event(status="failed", claimFingerprint="", errorCause=_batch_cause(10),
                                  ambiguousRootReportS3Uri=REPORT_URI), None)

    assert sfn.send_task_failure.call_args.kwargs["cause"] == end.EXIT_CODE_REASONS[10]


def test_the_report_is_read_only_for_an_ambiguous_root(end):
    with patch.object(end, "sfn") as sfn, patch.object(end, "s3_client") as s3:
        end.lambda_handler(_event(status="failed", claimFingerprint="", errorCause=_batch_cause(11),
                                  ambiguousRootReportS3Uri=REPORT_URI), None)

    s3.get_object.assert_not_called()
    assert sfn.send_task_failure.call_args.kwargs["cause"] == end.EXIT_CODE_REASONS[11]
