#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""pipelineEnd is the only state inside the machine that touches the parent workflow's task token:
success when no ``$.error`` is present, failure otherwise. A caught Bedrock failure is NOT ``$.error`` —
it is recorded through execution.status.json — so an execution whose analysisStatus is FAILED still
reports task success and lets the workflow proceed to process-output."""

from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h


def _run(event):
    mod = h.load_handler("pipelineEnd")
    sfn = MagicMock()
    mod.sfn = sfn
    result = mod.lambda_handler(event, MagicMock())
    return result, sfn


@pytest.mark.unit
class TestTokenRoutes:
    def test_no_error_sends_success(self):
        event = {"externalSfnTaskToken": h.TASK_TOKEN, "analysisStatus": "SUCCEEDED"}
        result, sfn = _run(event)
        sfn.send_task_success.assert_called_once_with(
            taskToken=h.TASK_TOKEN, output='{"status": "Pipeline Success"}')
        sfn.send_task_failure.assert_not_called()
        assert result is event

    def test_error_sends_failure_naming_the_error(self):
        event = {"externalSfnTaskToken": h.TASK_TOKEN,
                 "error": {"Error": "Lambda.Unknown", "Cause": "task timed out"}}
        _result, sfn = _run(event)
        sfn.send_task_failure.assert_called_once_with(
            taskToken=h.TASK_TOKEN, error="Pipeline Failure: Lambda.Unknown",
            cause="See AWS cloudwatch logs for error cause.")
        sfn.send_task_success.assert_not_called()

    def test_a_caught_bedrock_failure_still_reports_success(self):
        """analysisStatus FAILED travels through execution.status.json, not the token; sending a task
        failure here would skip process-output and lose the attributes already written."""
        event = {"externalSfnTaskToken": h.TASK_TOKEN, "analysisStatus": "FAILED",
                 "embeddingStatus": "SKIPPED"}
        _result, sfn = _run(event)
        sfn.send_task_success.assert_called_once()
        sfn.send_task_failure.assert_not_called()

    def test_no_token_makes_no_call(self):
        for event in ({"error": {"Error": "x", "Cause": "y"}}, {"externalSfnTaskToken": ""}, {}):
            _result, sfn = _run(event)
            sfn.send_task_success.assert_not_called()
            sfn.send_task_failure.assert_not_called()
