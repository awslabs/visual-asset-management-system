# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pipeline task payloads carry the executing user's name, never the caller's request context.

FIX-064: ``TaskStateBuilder.build_payload`` forwarded ``$.executingRequestContext`` — the whole
API Gateway request context the execute call arrived with, meaning every decoded JWT claim plus
the resolved ``vams:roles`` list, the caller's source IP, and the account/API identifiers — into
every pipeline task body. A pipeline may be a third-party Lambda, a customer-supplied SQS
consumer, an EventBridge subscriber, or a Deadline Cloud job, so that context left VAMS.

Both halves are pinned here, because an assertion that a claim is absent is also satisfied by an
empty payload: the fields the fifteen ``vamsExecute`` pipeline lambdas actually read must survive
(including the ``executingRequestContext`` key itself, which several read with bracket access),
and no reference into the request context may remain anywhere in a generated task state.

The request context is still threaded to the workflow's process-output state
(``workflowAslBuilder.generate_workflow_asl``), which is where
``processWorkflowExecutionOutput`` reads it for the output write-back's object-level check. That
path is a VAMS core lambda and is deliberately untouched.
"""

import json
import re

import pytest

from backend.backend.common.workflows.stepfunctions_builder import (
    DeadlineCloudTaskBuilder,
    TASK_BUILDER_CLASSES,
    get_task_builder,
)

# The path context workflowAslBuilder supplies for step 1 of a workflow.
PATH_CONTEXT = {
    "inputManifestS3Location":
        "States.Format('s3://{}/executions/{}/input/pipeline1/manifest.json', "
        "$.workflowExecutionS3InputOutputBucket, $$.Execution.Name)",
    "inputConfigurationS3Location":
        "States.Format('s3://{}/executions/{}/input/pipeline1/config.json', "
        "$.workflowExecutionS3InputOutputBucket, $$.Execution.Name)",
    "pipelineExecutionIdRef": "$.pipelineExecutionIds[0]",
}

# The state-machine input executeWorkflow starts a run with, carrying the full API Gateway
# request context of the authorizing call (executeWorkflow: executing_request_context=
# event.get("requestContext")).
SFN_INPUT = {
    "workflowDatabaseId": "smoke-db",
    "workflowId": "wf-thumbnail",
    "workflowExecutionId": "exec-0001",
    "workflowExecutionS3InputOutputBucket": "vams-run-bucket",
    "executingUserName": "alice@example.com",
    "pipelineExecutionIds": ["pe-0001", "pe-0002"],
    "executingRequestContext": {
        "accountId": "111122223333",
        "apiId": "rest-api-zz9",
        "domainName": "vams.example.com",
        "requestId": "req-9f1e",
        "http": {
            "path": "/workflows/smoke-db/wf-thumbnail/execute",
            "method": "POST",
            "sourceIp": "203.0.113.7",
        },
        "identity": {"sourceIp": "203.0.113.7", "userAgent": "curl/8.4.0"},
        "authorizer": {
            "principalId": "alice@example.com",
            "vams:tokens": '["alice@example.com"]',
            "vams:roles": '["admin","database-admin"]',
            "vams:mfaEnabled": "true",
            "vams:externalAttributes": '["clearance-high"]',
            "sub": "sub-8b7c9d",
            "email": "alice@example.com",
            "cognito:username": "alice@example.com",
            "custom:department": "engineering",
        },
    },
}

# Values that exist only inside the request context. The executing user name and its email-shaped
# duplicate are deliberately NOT listed: that name is forwarded on purpose.
CONTEXT_ONLY_VALUES = (
    '["admin","database-admin"]',
    '["clearance-high"]',
    "203.0.113.7",
    "111122223333",
    "rest-api-zz9",
    "req-9f1e",
    "sub-8b7c9d",
    "engineering",
    "curl/8.4.0",
)

# Keys that exist only inside the request context.
CONTEXT_ONLY_KEYS = (
    "authorizer",
    "identity",
    "accountId",
    "sourceIp",
    "cognito:username",
    "vams:roles",
    "vams:tokens",
    "vams:mfaEnabled",
    "vams:externalAttributes",
    "custom:department",
)

# Every body key the built-in vamsExecute pipeline lambdas read out of the task body.
PIPELINE_CONSUMED_KEYS = (
    "workflowDatabaseId",
    "workflowId",
    "workflowExecutionId",
    "workflowExecutionS3InputOutputBucket",
    "executingUserName",
    "inputManifestS3Location",
    "inputConfigurationS3Location",
    "TaskToken",
)

_REFERENCE = re.compile(r"^\$\.(?P<field>[A-Za-z0-9_]+)(?:\[(?P<index>\d+)\])?$")


def pipeline(execution_type):
    """A saved pipeline record of the given execution type, callback enabled."""
    resources = {
        "Lambda": {"resourceType": "Lambda", "resourceId": "vamsExecuteThumbnail"},
        "SQS": {"resourceType": "SQS",
                "resourceId": "https://sqs.us-east-1.amazonaws.com/999988887777/customer-queue"},
        "EventBridge": {"resourceType": "EventBridge", "resourceId": "customer-bus",
                        "eventSource": "customer.pipeline"},
        "DeadlineCloud": {
            "resourceType": "DeadlineCloud",
            "deadlineFarmId": "farm-" + "0" * 32,
            "deadlineQueueId": "queue-" + "0" * 32,
            "deadlineTemplate": "name: VamsJob\nspecificationVersion: jobtemplate-2023-09",
            "deadlineTemplateType": "YAML",
        },
    }
    return {
        "name": "thumbnailPipeline",
        "pipelineId": "thumbnail",
        "databaseId": "smoke-db",
        "pipelineExecutionType": execution_type,
        "waitForCallback": "Enabled",
        "taskTimeout": "7200",
        "userProvidedResource": json.dumps(resources[execution_type]),
    }


def build_body(execution_type):
    """The task body a workflow save would bake into the state machine for one pipeline step."""
    builder = get_task_builder(execution_type)
    record = pipeline(execution_type)
    payload = builder.build_payload(record, PATH_CONTEXT)
    payload = builder.apply_callback(payload, record)
    return payload["body"]


def build_state(execution_type):
    """The complete task state, so the SQS MessageBody / EventBridge Detail / Deadline job
    parameter renderings of the body are covered too."""
    builder = get_task_builder(execution_type)
    record = pipeline(execution_type)
    payload = builder.build_payload(record, PATH_CONTEXT)
    payload = builder.apply_callback(payload, record)
    return builder.build_task_state(record, "step1-abc12-thumbnailPipeline", payload)


def resolve_reference(reference, sfn_input):
    """Resolve one ASL Parameters value the way Step Functions would.

    A KeyError here means the body references a field the state-machine input does not carry —
    which Step Functions reports as an unretryable States.ParameterPathFailure at runtime."""
    if reference == "$$.Task.Token":
        return "AAAAKgAAAAI-task-token"
    if reference.startswith("States."):
        # Intrinsic: rendered by the service. Returned verbatim so its own path references are
        # still scanned by the leak assertions.
        return reference
    match = _REFERENCE.match(reference)
    if not match:
        raise AssertionError(f"Unsupported ASL reference in the pipeline body: {reference}")
    value = sfn_input[match.group("field")]
    if match.group("index") is not None:
        value = value[int(match.group("index"))]
    return value


def resolve_body(body, sfn_input):
    """The `data` dict a pipeline lambda receives, with every reference substituted."""
    resolved = {}
    for key, value in body.items():
        if key.endswith(".$"):
            resolved[key[:-2]] = resolve_reference(value, sfn_input)
        else:
            resolved[key] = value
    return resolved


def _collect(node, keys, strings):
    """Gather every dict key and every string leaf of a nested payload."""
    if isinstance(node, dict):
        for key, value in node.items():
            keys.add(key)
            _collect(value, keys, strings)
    elif isinstance(node, list):
        for item in node:
            _collect(item, keys, strings)
    elif isinstance(node, str):
        strings.add(node)


def contains_context_leak(payload):
    """Request-context-only keys and values found anywhere in a payload.

    Walks the structure rather than matching against json.dumps: a claim value is itself a JSON
    string (vams:roles is '["admin","database-admin"]'), whose quotes a dump would escape, so a
    dumped-text search silently matches nothing."""
    keys, strings = set(), set()
    _collect(payload, keys, strings)
    found = [value for value in CONTEXT_ONLY_VALUES
             if any(value in string for string in strings)]
    found += [key for key in CONTEXT_ONLY_KEYS if key in keys]
    return found


@pytest.mark.unit
class TestNoRequestContextReferenceInPipelineTasks:
    """The baked ASL must not name the request context anywhere in a pipeline task."""

    @pytest.mark.parametrize("execution_type", sorted(TASK_BUILDER_CLASSES))
    def test_body_does_not_reference_the_request_context_path(self, execution_type):
        body = build_body(execution_type)
        assert "$.executingRequestContext" not in json.dumps(body)

    @pytest.mark.parametrize("execution_type", sorted(TASK_BUILDER_CLASSES))
    def test_whole_task_state_does_not_reference_the_request_context_path(self, execution_type):
        # Covers the SQS MessageBody, the EventBridge Entries[].Detail, and the Deadline Cloud
        # Vams* job parameters, each of which is a different rendering of the same body.
        state = build_state(execution_type)
        assert "$.executingRequestContext" not in json.dumps(state)

    def test_every_builder_type_is_covered(self):
        # A registry gaining a builder must not silently escape the parametrized cases above.
        assert sorted(TASK_BUILDER_CLASSES) == [
            "DeadlineCloud", "EventBridge", "Lambda", "SQS"]


@pytest.mark.unit
class TestPipelineConsumedFieldsSurvive:
    """Positive control: the fields the pipeline lambdas actually read are still forwarded."""

    @pytest.mark.parametrize("execution_type", sorted(TASK_BUILDER_CLASSES))
    def test_consumed_keys_present(self, execution_type):
        body = build_body(execution_type)
        keys = {key[:-2] if key.endswith(".$") else key for key in body}
        missing = [key for key in PIPELINE_CONSUMED_KEYS if key not in keys]
        assert not missing, f"{execution_type} body dropped {missing}"

    @pytest.mark.parametrize("execution_type", sorted(TASK_BUILDER_CLASSES))
    def test_executing_request_context_key_is_never_omitted(self, execution_type):
        # conversion3dBasic, modelOps, rapidPipeline and rapidPipelineEKS read this key with
        # bracket access, so the key stays even though its value no longer carries the context.
        assert "executingRequestContext.$" in build_body(execution_type)

    def test_deadline_pipeline_execution_id_still_appended(self):
        # The Deadline job-callback lambda locates the pipeline-execution row from this parameter.
        body = build_body("DeadlineCloud")
        assert body["pipelineExecutionId.$"] == "$.pipelineExecutionIds[0]"


@pytest.mark.unit
class TestResolvedBodyDeliveredToPipeline:
    """What a pipeline lambda actually receives once Step Functions substitutes the references."""

    @pytest.mark.parametrize("execution_type", sorted(TASK_BUILDER_CLASSES))
    def test_no_claim_or_caller_identifier_reaches_the_pipeline(self, execution_type):
        resolved = resolve_body(build_body(execution_type), SFN_INPUT)
        # A non-empty payload, so "no claims present" cannot be satisfied by an empty body.
        assert len(resolved) >= len(PIPELINE_CONSUMED_KEYS)
        assert contains_context_leak(resolved) == []

    @pytest.mark.parametrize("execution_type", sorted(TASK_BUILDER_CLASSES))
    def test_pipeline_read_pattern_still_works(self, execution_type):
        # Mirrors the built-in vamsExecute lambdas: the user name is the attribution value, and
        # the request-context key is read directly without raising.
        resolved = resolve_body(build_body(execution_type), SFN_INPUT)
        assert resolved["executingUserName"] == "alice@example.com"
        assert resolved["executingRequestContext"] == "alice@example.com"
        assert isinstance(resolved["executingRequestContext"], str)

    def test_detector_would_catch_a_leak(self):
        # Negative control. Without this, the assertions above would also pass for a body that
        # forwarded nothing at all, or for a leak-scan that matched nothing by construction.
        leaking = dict(build_body("Lambda"))
        leaking["executingRequestContext.$"] = "$.executingRequestContext"
        assert "$.executingRequestContext" in json.dumps(leaking)
        resolved = resolve_body(leaking, SFN_INPUT)
        leaked = contains_context_leak(resolved)
        for expected in ('["admin","database-admin"]', "203.0.113.7", "authorizer"):
            assert expected in leaked

    def test_every_referenced_path_exists_on_the_state_machine_input(self):
        # resolve_reference raises KeyError for a field the SFN input does not carry, so a
        # projection that pointed at an absent path would fail here rather than at run time.
        for execution_type in sorted(TASK_BUILDER_CLASSES):
            resolve_body(build_body(execution_type), SFN_INPUT)


@pytest.mark.unit
class TestDeadlineCloudJobParameters:
    """The Deadline Cloud flattening of the body."""

    def test_excluded_body_field_name_still_matches_the_body_key(self):
        body = build_body("DeadlineCloud")
        keys = {key[:-2] if key.endswith(".$") else key for key in body}
        assert DeadlineCloudTaskBuilder.EXCLUDED_BODY_FIELDS == {"executingRequestContext"}
        assert DeadlineCloudTaskBuilder.EXCLUDED_BODY_FIELDS <= keys

    def test_request_context_is_not_injected_as_a_job_parameter(self):
        job_parameters = build_state("DeadlineCloud")["Parameters"]["Parameters"]
        assert "VamsExecutingRequestContext" not in job_parameters
        assert job_parameters["VamsExecutingUserName"] == {"String.$": "$.executingUserName"}

    def test_job_parameters_carry_no_claims(self):
        job_parameters = build_state("DeadlineCloud")["Parameters"]["Parameters"]
        assert contains_context_leak(job_parameters) == []
