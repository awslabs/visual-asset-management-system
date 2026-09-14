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

    @pytest.mark.parametrize("slashes", ["", "\\", "\\\\"])
    def test_deadline_parameter_envelope_task_token_redacted(self, slashes):
        # Deadline Cloud CreateJob wraps every parameter value in a single-key type object, so the
        # reserved VamsTaskToken parameter reaches the workflow log group as
        # `"VamsTaskToken":{"String":"<token>"}` — inside the escaped state input the same shape
        # carries a backslash before each quote. The direct key:"value" rule stops at the `{`, so
        # without an envelope-aware rule the token survives redaction.
        q = slashes + '"'
        text = ('{%sparameters%s:{%sVamsWorkflowId%s:{%sString%s:%swf1%s},'
                '%sVamsTaskToken%s:{%sString%s:%s%s%s},'
                '%sVamsPipelineExecutionId%s:{%sString%s:%spe1%s}}}') % (
            q, q, q, q, q, q, q, q,
            q, q, q, q, q, TASK_TOKEN, q,
            q, q, q, q, q, q)
        out = redact_log_text(text)
        assert TASK_TOKEN not in out, "task token leaked through the Deadline parameter envelope"
        assert REDACTED in out
        assert "wf1" in out and "pe1" in out

    def test_non_sensitive_wrapped_value_unchanged(self):
        # The envelope rule fires only for a sensitive key: an ordinary parameter wrapped in the
        # same {"String": ...} object is returned byte-identical.
        text = '{"VamsWorkflowId": {"String": "wf-keep-me-123"}}'
        assert redact_log_text(text) == text

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


def _describe_jobs_cause(env_name="TASK_TOKEN", token=TASK_TOKEN, escaped=False):
    """A `batch:submitJob.sync` failure Cause: the DescribeJobs object, whose container environment
    carries the task token as a {"Name","Value"} pair alongside ordinary variables."""
    cause = json.dumps({
        "JobId": "0f1e2d3c-job", "JobName": "isaac-train", "Status": "FAILED",
        "StatusReason": "Essential container in task exited",
        "Container": {
            "Environment": [
                {"Name": "S3_INPUT_KEY", "Value": "assets/TASK_TOKEN/model.glb"},
                {"Name": env_name, "Value": token},
                {"Name": "OUTPUT_BUCKET", "Value": "vams-assets-bucket"},
            ],
            "LogStreamName": "isaac-jd/default/abc",
            "ExitCode": 1,
        },
    }, separators=(",", ":"))
    return json.dumps(cause)[1:-1] if escaped else cause


@pytest.mark.unit
class TestEnvironmentNameValueRedaction:
    """{"Name": "TASK_TOKEN", "Value": "<token>"} — the environment-variable shape a Batch DescribeJobs
    object carries. The sensitive label is the VALUE of "Name", so the key-driven rules cannot see it;
    a dedicated rule reads the Name and masks the Value that follows. (ARCC BSC4 "Log Every Security
    Event", Anti-Patterns → Logging Sensitive Data; VAMS #319/#307.)"""

    @pytest.mark.parametrize("env_name", [
        "TASK_TOKEN", "VAMS_TASK_TOKEN", "EXTERNAL_SFN_TASK_TOKEN", "SFN_EXTERNAL_TASK_TOKEN",
        "AWS_SESSION_TOKEN", "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "X_AMZ_SECURITY_TOKEN",
        "TaskToken", "task-token",
    ])
    def test_describe_jobs_environment_token_redacted_by_env_name(self, env_name):
        out = redact_log_text(_describe_jobs_cause(env_name))
        assert TASK_TOKEN not in out, f"token leaked behind Name {env_name}"
        assert REDACTED in out
        # The rest of the object is untouched, and it is still JSON.
        parsed = json.loads(out)
        env = {e["Name"]: e["Value"] for e in parsed["Container"]["Environment"]}
        assert env[env_name] == REDACTED
        assert env["S3_INPUT_KEY"] == "assets/TASK_TOKEN/model.glb"
        assert env["OUTPUT_BUCKET"] == "vams-assets-bucket"
        assert parsed["Container"]["LogStreamName"] == "isaac-jd/default/abc"
        assert parsed["JobId"] == "0f1e2d3c-job"

    def test_escaped_describe_jobs_environment_token_redacted(self):
        # The same object re-encoded inside a history or CloudWatch line carries a backslash before
        # every quote.
        out = redact_log_text(_describe_jobs_cause(escaped=True))
        assert TASK_TOKEN not in out
        assert REDACTED in out
        assert "isaac-jd/default/abc" in out and "assets/TASK_TOKEN/model.glb" in out

    def test_boto3_lower_case_shape_redacted(self):
        # boto3's DescribeJobs response spells the pair `name`/`value`.
        text = '{"container":{"environment":[{"name":"TASK_TOKEN","value":"%s"}]}}' % TASK_TOKEN
        out = redact_log_text(text)
        assert TASK_TOKEN not in out and REDACTED in out

    def test_whitespace_between_the_pair_is_tolerated(self):
        text = '{ "Name" : "TASK_TOKEN" , "Value" : "%s" }' % TASK_TOKEN
        out = redact_log_text(text)
        assert TASK_TOKEN not in out and REDACTED in out

    @pytest.mark.parametrize("text", [
        # The Name is matched by its tail: a Name that merely contains a sensitive word is ordinary.
        '{"Name":"TASK_TOKEN_TTL","Value":"3600"}',
        '{"Name":"TOKENIZER","Value":"bert-base"}',
        # A Value that mentions a sensitive word is content, not a credential.
        '{"Name":"S3_INPUT_KEY","Value":"assets/TASK_TOKEN/model.glb"}',
        '{"Name":"OUTPUT_PREFIX","Value":"runs/SessionToken/out"}',
        # An ordinary Name/Value pair.
        '{"Name":"OUTPUT_BUCKET","Value":"vams-assets-bucket"}',
        # "Name" must be the whole key.
        '{"JobName":"TASK_TOKEN","Value":"not-a-token"}',
        # A plain Batch failure Cause with no environment block.
        '{"JobId":"j1","Status":"FAILED","StatusReason":"Essential container in task exited"}',
        "Essential container in task exited",
    ])
    def test_ordinary_name_value_pairs_and_causes_unchanged(self, text):
        assert redact_log_text(text) == text

    def test_only_the_sensitive_entry_of_an_environment_list_is_masked(self):
        text = ('[{"Name":"A","Value":"1"},{"Name":"VAMS_TASK_TOKEN","Value":"%s"},'
                '{"Name":"Z","Value":"26"}]') % TASK_TOKEN
        assert redact_log_text(text) == (
            '[{"Name":"A","Value":"1"},{"Name":"VAMS_TASK_TOKEN","Value":"%s"},'
            '{"Name":"Z","Value":"26"}]') % REDACTED


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
