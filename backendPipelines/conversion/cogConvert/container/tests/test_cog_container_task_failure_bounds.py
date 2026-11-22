# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Live smoke D1: a refused conversion left the VAMS execution RUNNING.

AWS Step Functions rejects a task failure whose `error` is over 256 characters (ValidationException); the container
sent `"Pipeline Failure: " + message` as the error, so every refusal longer than about 240 characters (the
antimeridian one, the disk one) was rejected, the failure was lost and the workflow waited for its task timeout.
The fake client below enforces the service's own bounds, so these tests fail on that code."""

import json

import pytest
from botocore.exceptions import ClientError

from cog_convert_pipeline import convert, core
from cog_convert_pipeline.utils.aws import sfn
from cog_convert_pipeline.utils.pipeline.objects import PipelineStatus
from conftest import fixture_path
from test_core_flow import ASSET, _definition, world  # noqa: F401


# The real callbacks, captured before any fixture replaces them on the shared module.
_REAL_FAILURE, _REAL_SUCCESS = sfn.send_task_failure, sfn.send_task_success


class BoundedStepFunctions:
    """Records calls and rejects what the real service rejects."""

    def __init__(self):
        self.failures, self.successes = [], []
        self.reject_first_failure = False
        self.reject_first_success = False

    def send_task_failure(self, taskToken, error="", cause=""):
        if len(error) > 256 or len(cause) > 32768:
            raise ClientError({"Error": {"Code": "ValidationException", "Message": "length"}}, "SendTaskFailure")
        if self.reject_first_failure:
            self.reject_first_failure = False
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "SendTaskFailure")
        self.failures.append((error, cause))

    def send_task_success(self, taskToken, output):
        if self.reject_first_success:
            self.reject_first_success = False
            raise ClientError({"Error": {"Code": "ValidationException", "Message": "output"}}, "SendTaskSuccess")
        self.successes.append(output)


@pytest.fixture
def steps(monkeypatch):
    fake = BoundedStepFunctions()
    monkeypatch.setattr(sfn, "client", fake)
    monkeypatch.setattr(sfn, "task_token", "int-token")
    return fake


@pytest.mark.unit
class TestBounds:
    def test_a_long_message_goes_in_the_cause_and_the_error_stays_a_short_name(self, steps):
        sfn.send_task_failure("x" * 5000, "CogConvertRefused")
        error, cause = steps.failures[0]
        assert error == "CogConvertRefused" and len(error) <= 256 and cause == "x" * 5000

    def test_the_antimeridian_message_is_delivered_whole(self, steps):
        sfn.send_task_failure(convert.ANTIMERIDIAN_MESSAGE, "CogConvertRefused")
        assert steps.failures[0][1] == convert.ANTIMERIDIAN_MESSAGE

    def test_every_bound_is_enforced_on_a_huge_message(self, steps):
        sfn.send_task_failure("y" * 100000, "z" * 1000)
        error, cause = steps.failures[0]
        assert len(error) == 256 and len(cause) == 32768 and cause.endswith("[truncated]")

    def test_the_old_call_shape_is_rejected_by_the_service(self):
        # The control: the first review's call (error = prefix + message) is what the service refuses.
        old_error = "Pipeline Failure: " + convert.ANTIMERIDIAN_MESSAGE
        assert len(old_error) > 256
        with pytest.raises(ClientError):
            BoundedStepFunctions().send_task_failure("t", error=old_error, cause="See logs")

    def test_a_rejected_failure_is_retried_with_a_minimal_payload_not_dropped(self, steps, caplog):
        steps.reject_first_failure = True
        with caplog.at_level("ERROR"):
            sfn.send_task_failure("some failure", "CogConvertFailed")
        assert steps.failures == [("CogConvertFailed", sfn.FALLBACK_CAUSE)]
        assert any("was rejected" in r.getMessage() for r in caplog.records)

    def test_a_rejected_success_is_retried_with_a_minimal_output(self, steps):
        steps.reject_first_success = True
        output = core.PipelineExecutionParams(
            jobName="j", currentStageType="COG_CONVERT", definition=["{}"], inputMetadata="", inputParameters="")
        sfn.send_task_success(output)
        assert json.loads(steps.successes[0])["status"] == "COMPLETE"

    def test_an_empty_name_and_message_still_send_valid_text(self, steps):
        sfn.send_task_failure("", "")
        error, cause = steps.failures[0]
        assert error and cause


@pytest.mark.unit
@pytest.mark.parametrize("case,fixture", [
    ("antimeridian", "utm33.tif"),
    ("pixel-limit", "utm33.tif"),
    ("disk", "utm33.tif"),
    ("bands", "utm33.tif"),
    ("truncated", None),
])
def test_every_refusal_ends_the_run_failed_and_reaches_the_task_token(world, steps, monkeypatch, case, fixture):  # noqa: F811
    fake, tokens = world
    # The module-level fakes of `world` replace the S3 calls; the Step Functions calls go through the bounded fake.
    monkeypatch.setattr(core.sfn, "send_task_failure", _REAL_FAILURE)
    monkeypatch.setattr(core.sfn, "send_task_success", _REAL_SUCCESS)
    key = f"{ASSET}/{case}.tif"
    if fixture:
        fake.put_source(key, fixture_path(fixture))
    else:
        fake.objects[("assets", key)] = b"this is not a tiff file at all"
    if case == "antimeridian":
        monkeypatch.setattr(convert, "inspect", lambda *a, **k: {
            "wgs84Extent": {"type": "Polygon", "coordinates": [[[178.7, 0], [178.7, 1], [-178.9, 1], [-178.9, 0]]]}})
    elif case == "pixel-limit":
        monkeypatch.setattr(convert, "MAX_RAW_BYTES", 10)
    elif case == "bands":
        monkeypatch.setattr(convert, "MAX_BANDS", 0)
    elif case == "disk":
        def refuse(*_args, **_kwargs):
            raise convert.ConversionRefused("d" * 900)

        monkeypatch.setattr(convert, "check_disk", refuse)
        monkeypatch.setattr(convert, "inspect", lambda *a, **k: {})
        monkeypatch.setattr(convert, "warped_size", lambda *a, **k: None)

    out = core.run(_definition(key))

    assert out.status is PipelineStatus.FAILED
    assert len(steps.failures) == 1, "the failure must reach Step Functions"
    error, cause = steps.failures[0]
    assert 0 < len(error) <= 256
    assert cause and cause != sfn.FALLBACK_CAUSE, "the reason must travel, not the fallback text"
    if case == "antimeridian":
        assert error == "CogConvertRefused" and "antimeridian" in cause and "Split the file" in cause
    if case in ("pixel-limit", "bands", "disk"):
        assert error == "CogConvertRefused"
    if case == "truncated":
        assert error == "CogConvertFailed" and "not a TIFF" in cause
