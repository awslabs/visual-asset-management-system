# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The request context the execute handler puts into the Step Functions input.

The execution input is written to the execution's logs, which any reader of the execution can
fetch. The context carries only what the end-state step reads: the `authorizer` object whole (its
claims resolution and the custom claims hook read it) and an `http` object with the request's method
and path (the output write-back overwrites that path). The caller's `identity` block and the other
API Gateway request fields stay out, for a REST (v1) event and an HTTP (v2) event alike."""

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates")
os.environ.setdefault("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ew  # noqa: E402

MOD = "backend.backend.handlers.workflows.executeWorkflow"
EXECUTE_PATH = "/workflows/db1/wf1/execute"

# The authorizer objects API Gateway hands a VAMS handler: a REST REQUEST authorizer's flat string
# map, and an HTTP API lambda authorizer's `lambda` block. Both are kept whole.
REST_AUTHORIZER = {
    "principalId": "user1",
    "vams:tokens": '["user1"]',
    "vams:roles": '["role1"]',
    "vams:mfaEnabled": "true",
    "vams:apiKeyId": "11111111-2222-3333-4444-555555555555",
    "integrationLatency": 12,
}
HTTP_AUTHORIZER = {"lambda": {"vams:tokens": '["user1"]', "vams:roles": '["role1"]',
                              "vams:mfaEnabled": "false"}}


def _rest_v1_event(body):
    """A REST API (v1) proxy event as API Gateway delivers it, before normalize_event."""
    return {
        "resource": "/workflows/{workflowDatabaseId}/{workflowId}/execute",
        "path": EXECUTE_PATH,
        "httpMethod": "POST",
        "headers": {"authorization": "Bearer t", "User-Agent": "curl/8.4.0"},
        "pathParameters": {"workflowDatabaseId": "db1", "workflowId": "wf1"},
        "queryStringParameters": None,
        "requestContext": {
            "resourceId": "r1", "resourcePath": "/workflows/{workflowDatabaseId}/{workflowId}/execute",
            "httpMethod": "POST", "path": "/api" + EXECUTE_PATH, "stage": "api",
            "accountId": "000000000000", "apiId": "abc123", "requestId": "req-1",
            "extendedRequestId": "ext-1", "domainName": "abc123.execute-api.example",
            "domainPrefix": "abc123", "protocol": "HTTP/1.1", "requestTimeEpoch": 1,
            "identity": {
                "sourceIp": "203.0.113.7", "userAgent": "curl/8.4.0", "userArn": None,
                "cognitoIdentityId": None, "cognitoAuthenticationProvider": None,
                "principalOrgId": None, "apiKey": None, "apiKeyId": None, "accessKey": None,
                "accountId": None, "caller": None, "user": None,
            },
            "authorizer": dict(REST_AUTHORIZER),
        },
        "body": json.dumps(body),
    }


def _http_v2_event(body):
    """An HTTP API (v2) event, whose `http` block also carries the source IP and user agent."""
    return {
        "version": "2.0",
        "rawPath": EXECUTE_PATH,
        "headers": {"authorization": "Bearer t"},
        "pathParameters": {"workflowDatabaseId": "db1", "workflowId": "wf1"},
        "queryStringParameters": {},
        "requestContext": {
            "accountId": "000000000000", "apiId": "abc123", "requestId": "req-2",
            "domainName": "abc123.execute-api.example", "domainPrefix": "abc123",
            "stage": "$default", "time": "t", "timeEpoch": 1,
            "http": {"method": "POST", "path": EXECUTE_PATH, "protocol": "HTTP/1.1",
                     "sourceIp": "203.0.113.7", "userAgent": "curl/8.4.0"},
            "authorizer": json.loads(json.dumps(HTTP_AUTHORIZER)),
        },
        "body": json.dumps(body),
    }


def _launched_input(event):
    """Run the execute handler's happy path on `event`; return the Step Functions input it sent."""
    from backend.tests.handlers.workflows import test_executeWorkflow as tew
    p = tew.TestExecuteOrchestration()._patches()
    with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
         p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
         patch(f"{MOD}._running_execution_exists", return_value=False), \
         patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
         patch(f"{MOD}.dynamodb") as m_dynamo:
        m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
        m_dynamo.Table.return_value = MagicMock()
        resp = ew.lambda_handler(event, MagicMock())
    assert resp["statusCode"] == 200, resp["body"]
    call = m_sfn.start_execution.call_args
    assert call is not None, "the handler answered 200 without starting the execution"
    return json.loads(call.kwargs["input"])


_BODY = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}


@pytest.mark.unit
class TestTheLaunchedExecutionContext:
    @pytest.mark.parametrize("make_event, authorizer", [
        (_rest_v1_event, REST_AUTHORIZER),
        (_http_v2_event, HTTP_AUTHORIZER),
    ], ids=["rest-v1", "http-v2"])
    def test_the_context_is_the_authorizer_and_the_method_and_path(self, make_event, authorizer):
        context = _launched_input(make_event(_BODY))["executingRequestContext"]
        assert set(context) == {"authorizer", "http"}
        assert context["authorizer"] == authorizer
        assert context["http"] == {"method": "POST", "path": EXECUTE_PATH}

    @pytest.mark.parametrize("make_event", [_rest_v1_event, _http_v2_event],
                             ids=["rest-v1", "http-v2"])
    def test_no_client_detail_reaches_the_execution_input(self, make_event):
        sent = json.dumps(_launched_input(make_event(_BODY))["executingRequestContext"])
        for value in ("203.0.113.7", "curl/8.4.0", "000000000000", "abc123", "req-"):
            assert value not in sent


@pytest.mark.unit
class TestExecutingRequestContextHelper:
    def test_a_trigger_dispatch_without_an_authorizer_gets_no_authorizer_key(self):
        # The event shape workflowTriggerDispatch._invoke_execute sends.
        event = {"requestContext": {"http": {"method": "POST", "path": EXECUTE_PATH}},
                 "lambdaCrossCall": {"userName": "SYSTEM_USER"}}
        assert ew._executing_request_context(event) == {
            "http": {"method": "POST", "path": EXECUTE_PATH}}

    def test_a_re_run_keeps_the_callers_authorizer(self):
        # The event shape executionService.rerun_execution sends.
        event = {"requestContext": {"http": {"method": "POST", "path": EXECUTE_PATH},
                                    "authorizer": dict(REST_AUTHORIZER)},
                 "lambdaCrossCall": {"userName": "user1", "mfaEnabled": True}}
        assert ew._executing_request_context(event) == {
            "http": {"method": "POST", "path": EXECUTE_PATH}, "authorizer": REST_AUTHORIZER}

    def test_a_rest_event_not_yet_normalized_reads_the_top_level_method_and_path(self):
        context = ew._executing_request_context(_rest_v1_event(_BODY))
        assert context["http"] == {"method": "POST", "path": EXECUTE_PATH}
        assert set(context) == {"authorizer", "http"}

    def test_the_http_block_is_a_new_object(self):
        # The end-state step overwrites http.path and http.method on the write-back payload.
        event = _http_v2_event(_BODY)
        context = ew._executing_request_context(event)
        assert context["http"] is not event["requestContext"]["http"]
