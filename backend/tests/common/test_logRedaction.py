# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for common.logRedaction — credential redaction of returned log text."""

import json
import time

import pytest

from backend.backend.common.logRedaction import (
    redact_log_text,
    redact_log_events,
    REDACTED,
)


@pytest.mark.unit
class TestRedactLogText:
    def test_none_and_non_string_passthrough(self):
        assert redact_log_text(None) is None
        assert redact_log_text("") == ""
        assert redact_log_text(123) == 123

    def test_plain_text_unchanged(self):
        text = "Pipeline started processing 3 input files at /data/foo.e57"
        assert redact_log_text(text) == text

    def test_json_authorization_redacted(self):
        text = '{"authorization": "Bearer abc.def.ghi", "status": "ok"}'
        out = redact_log_text(text)
        assert "abc.def.ghi" not in out
        assert REDACTED in out
        assert '"status": "ok"' in out

    def test_bare_key_value_redacted(self):
        text = "SessionToken=FQoGZXIvYXdzEED//// status=running"
        out = redact_log_text(text)
        assert "FQoGZXIvYXdzEED" not in out
        assert "status=running" in out

    def test_bearer_token_redacted(self):
        text = "calling api with Authorization: Bearer eyJhbGciOiJ.payloadpart.sig"
        out = redact_log_text(text)
        assert "payloadpart" not in out
        assert REDACTED in out

    def test_aws_access_key_id_redacted(self):
        text = "assumed role with key AKIAIOSFODNN7EXAMPLE for the job"
        out = redact_log_text(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in out
        assert REDACTED in out

    def test_jwt_redacted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.s5H5ZoQ5abc-DEF_123"
        text = f"token issued: {jwt}"
        out = redact_log_text(text)
        assert jwt not in out
        assert REDACTED in out

    def test_secret_access_key_json(self):
        text = '{"Credentials": {"SecretAccessKey": "wJalrXUtnFEMI/K7MDENG", "Expiration": "2026"}}'
        out = redact_log_text(text)
        assert "wJalrXUtnFEMI/K7MDENG" not in out
        assert "Expiration" in out


# A Step Functions task token is a bearer capability to complete or fail the pending pipeline task,
# so it must never survive into a logs response. It reaches the redactor in two shapes: plain JSON
# (a vamsExecute lambda logging its invocation event) and backslash-escaped JSON (the workflow state
# machine's own log group, which runs with includeExecutionData enabled).
TASK_TOKEN = "AAAAKgAAAAIAAAAAAAAAA" + "b" * 680


@pytest.mark.unit
class TestTaskTokenRedaction:
    def test_plain_json_task_token_redacted(self):
        text = '{"TaskToken": "%s", "status": "ok"}' % TASK_TOKEN
        out = redact_log_text(text)
        assert TASK_TOKEN not in out
        assert REDACTED in out
        assert '"status": "ok"' in out

    def test_escaped_json_task_token_redacted(self):
        text = '{\\"TaskToken\\": \\"%s\\", \\"workflowId\\": \\"wf1\\"}' % TASK_TOKEN
        out = redact_log_text(text)
        assert TASK_TOKEN not in out
        assert REDACTED in out
        assert "wf1" in out

    def test_escaped_json_session_token_redacted(self):
        text = '{\\"SessionToken\\": \\"FQoGZXIvYXdzEEDsecret\\", \\"Expiration\\": \\"2026\\"}'
        out = redact_log_text(text)
        assert "FQoGZXIvYXdzEEDsecret" not in out
        assert REDACTED in out
        assert "Expiration" in out

    def test_escaped_json_secret_access_key_redacted(self):
        text = '{\\"SecretAccessKey\\": \\"wJalrXUtnFEMI/K7MDENG\\"}'
        out = redact_log_text(text)
        assert "wJalrXUtnFEMI/K7MDENG" not in out
        assert REDACTED in out

    @pytest.mark.parametrize(
        "key", ["TaskToken", "taskToken", "externalSfnTaskToken", "sfnExternalTaskToken",
                "VamsTaskToken"])
    def test_every_task_token_key_spelling_redacted(self, key):
        # The pipelines and the ASL builder each use a different spelling; all end in TaskToken and
        # all must match, in plain, escaped, and single-quoted form.
        for text in ('{"%s": "%s"}' % (key, TASK_TOKEN),
                     '{\\"%s\\": \\"%s\\"}' % (key, TASK_TOKEN),
                     "{'%s': '%s'}" % (key, TASK_TOKEN)):
            out = redact_log_text(text)
            assert TASK_TOKEN not in out, f"{key} leaked in {text[:40]}"
            assert REDACTED in out

    def test_lambda_invocation_event_line_redacted(self):
        # The shape a vamsExecute lambda emits via `logger.info(event)`.
        text = ("{'body': {'workflowId': 'wf1', 'workflowExecutionId': 'abc', "
                "'TaskToken': '%s', 'inputManifestS3Location': 's3://b/k'}}" % TASK_TOKEN)
        out = redact_log_text(text)
        assert TASK_TOKEN not in out
        assert "inputManifestS3Location" in out

    def test_sfn_state_entered_history_line_redacted(self):
        # The shape the workflow log group holds: the task body as an escaped JSON string.
        inner = json.dumps({"body": {"TaskToken": TASK_TOKEN, "workflowId": "wf1"}})
        text = json.dumps({"type": "TaskStateEntered", "details": {"input": inner}})
        out = redact_log_text(text)
        assert TASK_TOKEN not in out
        assert "TaskStateEntered" in out

    def test_task_token_events_redacted(self):
        events = [{"timestamp": 1, "message": '{\\"TaskToken\\": \\"%s\\"}' % TASK_TOKEN}]
        out = redact_log_events(events)
        assert TASK_TOKEN not in out[0]["message"]
        assert REDACTED in out[0]["message"]

    def test_deeply_escaped_task_token_redacted(self):
        # Re-encoded payloads carry more backslashes per quote the deeper they were nested.
        for depth in (1, 2, 3):
            slashes = "\\" * depth
            text = '{%s"TaskToken%s": %s"%s%s"}' % (
                slashes, slashes, slashes, TASK_TOKEN, slashes)
            assert TASK_TOKEN not in redact_log_text(text), f"leaked at depth {depth}"

    def test_backslash_run_does_not_blow_up(self):
        # The escaped-quote repeat is bounded, so a long backslash run stays linear rather than
        # backtracking quadratically — log text is caller-influenced and one event must not be able
        # to outlast the Lambda timeout.
        start = time.perf_counter()
        redact_log_text("TaskToken" + "\\" * 20000)
        assert time.perf_counter() - start < 1.0

    @pytest.mark.parametrize("text", [
        "Pipeline started processing 3 input files at /data/foo.e57",
        "lambda:invoke.waitForTaskToken resource resolved for step 1",
        '{"type": "TaskStateEntered", "name": "Pipeline1Task"}',
        "No TASK_TOKEN set, skipping send_task_success",
        "Batch job 12ab submitted, waiting for callback (TimeoutSeconds 86400)",
    ])
    def test_benign_log_text_unchanged(self, text):
        # The token rules are labelled-key driven, so ordinary orchestration log text that merely
        # mentions the callback pattern is returned byte-identical.
        assert redact_log_text(text) == text


@pytest.mark.unit
class TestRedactLogEvents:
    def test_non_list_passthrough(self):
        assert redact_log_events(None) is None
        assert redact_log_events("nope") == "nope"

    def test_event_messages_redacted(self):
        events = [
            {"timestamp": 1, "message": "ok, nothing secret"},
            {"timestamp": 2, "message": 'authorization: "Bearer secrettoken123"'},
            {"timestamp": 3},  # no message key
        ]
        out = redact_log_events(events)
        assert out[0]["message"] == "ok, nothing secret"
        assert "secrettoken123" not in out[1]["message"]
        assert REDACTED in out[1]["message"]
        assert out[1]["timestamp"] == 2
        assert out[2] == {"timestamp": 3}

    def test_does_not_mutate_input(self):
        events = [{"message": "SessionToken=abc123def"}]
        redact_log_events(events)
        # Original list entry is untouched (new dicts returned).
        assert events[0]["message"] == "SessionToken=abc123def"
