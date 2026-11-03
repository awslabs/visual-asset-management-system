# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The end-state step resolves the same claims, and sends the same write-back, from the request context
the execute handler launches with as it did from the whole API Gateway request context.

The execute handler sends only the `authorizer` object and the request's method and path
(executeWorkflow._executing_request_context). The end-state step reads exactly those: its claims
resolution (handlers.auth.request_to_claims, run here for real rather than as the shared conftest stub
that answers claims for any event) reads `authorizer`, and the file-ingestion write-back overwrites
`http.path` and `http.method`. Each run below is made twice, once with the whole context and once with
the one the handler builds from it, and the two must agree."""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

for _k, _v in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "FILE_UPLOAD_LAMBDA_FUNCTION_NAME": "t-upload",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_UPLOAD_TABLE_NAME": "t-asset-upload",
    "DATABASE_STORAGE_TABLE_NAME": "t-db",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "PIPELINE_STORAGE_TABLE_V2_NAME": "t-pipe-v2",
    "PIPELINE_TEMPLATES_STORAGE_TABLE_NAME": "t-templates",
    "PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME": "t-tagschema",
    "S3_ASSETAUXILIARY_STORAGE_BUCKET": "t-aux",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
}.items():
    os.environ.setdefault(_k, _v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows import executeWorkflow as ew  # noqa: E402
from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402
from backend.backend.common.apiRoutes import API_UPLOAD_COMPLETE_EXTERNAL  # noqa: E402

_AUTH_INIT = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(ew.__file__)), "..", "auth", "__init__.py"))

RUN_BUCKET = "run-io-bucket"
FILES_PREFIX = "pipelines/p1/JOB/output/E1/files/"
EXECUTE_PATH = "/workflows/db1/wf1/execute"
_IDENTITY = {"sourceIp": "203.0.113.7", "userAgent": "curl/8.4.0", "userArn": None,
             "cognitoIdentityId": None, "apiKeyId": None, "accessKey": None, "user": None}


def _real_request_to_claims():
    """The production claims extraction, loaded from its own file so the conftest stub cannot stand
    in for it."""
    spec = importlib.util.spec_from_file_location("_real_auth_claims_for_end_state_test", _AUTH_INIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.request_to_claims


def _rest_v1_context():
    """A REST (v1) request context after normalize_event added its `http` block."""
    return {
        "resourceId": "r1", "httpMethod": "POST", "path": "/api" + EXECUTE_PATH, "stage": "api",
        "accountId": "000000000000", "apiId": "abc123", "requestId": "req-1",
        "domainName": "abc123.execute-api.example", "identity": dict(_IDENTITY),
        "authorizer": {"principalId": "user1", "vams:tokens": '["user1"]',
                       "vams:roles": '["role1", "role2"]', "vams:mfaEnabled": "true",
                       "vams:apiKeyId": "11111111-2222-3333-4444-555555555555"},
        "http": {"path": EXECUTE_PATH, "method": "POST", "sourceIp": "203.0.113.7"},
    }


def _http_v2_lambda_context():
    return {
        "accountId": "000000000000", "apiId": "abc123", "requestId": "req-2",
        "domainName": "abc123.execute-api.example", "stage": "$default",
        "http": {"method": "POST", "path": EXECUTE_PATH, "protocol": "HTTP/1.1",
                 "sourceIp": "203.0.113.7", "userAgent": "curl/8.4.0"},
        "authorizer": {"lambda": {"vams:tokens": '["user1"]', "vams:roles": '["role1"]',
                                  "vams:mfaEnabled": "false"}},
    }


def _http_v2_jwt_context():
    return {
        "accountId": "000000000000", "apiId": "abc123", "requestId": "req-3",
        "http": {"method": "POST", "path": EXECUTE_PATH, "sourceIp": "203.0.113.7",
                 "userAgent": "curl/8.4.0"},
        "authorizer": {"jwt": {"claims": {"cognito:username": "user1",
                                          "vams:roles": '["role1"]'}, "scopes": None}},
    }


CONTEXTS = [_rest_v1_context, _http_v2_lambda_context, _http_v2_jwt_context]
CONTEXT_IDS = ["rest-v1", "http-v2-lambda", "http-v2-jwt"]


def _minimal(full_context):
    """The context the execute handler sends for a request that arrived with `full_context`."""
    return ew._executing_request_context({"requestContext": json.loads(json.dumps(full_context))})


def _event(request_context):
    return {"body": {
        "outputAssetId": "asset1.glb", "outputDatabaseId": "db1",
        "workflowExecutionId": "E1", "endStatePipelineExecutionId": "P1",
        "workflowDatabaseId": "wdb", "workflowId": "wf1",
        "workflowExecutionS3InputOutputBucket": RUN_BUCKET,
        "executingUserName": "user1",
        "executingRequestContext": json.loads(json.dumps(request_context)),
        "filesPathKey": FILES_PREFIX,
    }}


def _run(request_context):
    """Run the end-state step's asset-file write-back; return (claims passed to the enforcer,
    file-ingestion payload)."""
    claims_seen = []
    payloads = []

    def _enforcer(claims_and_roles):
        claims_seen.append(claims_and_roles)
        enforcer = MagicMock()
        enforcer.enforce.return_value = True
        return enforcer

    def _ingest(payload):
        payloads.append(json.loads(json.dumps(payload)))
        return {"Payload": MagicMock(read=lambda: json.dumps({
            "statusCode": 200, "body": json.dumps({"overallSuccess": True})}).encode("utf-8"))}

    listing = {"Contents": [{"Key": FILES_PREFIX + "out.glb", "Size": 7}]}
    with patch.object(po, "request_to_claims", _real_request_to_claims()), \
            patch.object(po, "CasbinEnforcer", side_effect=_enforcer), \
            patch.object(po, "lookup_existing_asset",
                         return_value={"databaseId": "db1", "assetId": "asset1.glb",
                                       "bucketId": "b1"}), \
            patch.object(po, "get_default_bucket_details",
                         return_value={"bucketId": "b1", "bucketName": "asset-bucket",
                                       "baseAssetsPrefix": "assets/"}), \
            patch.object(po, "_fetch_execution_logs", return_value=("", "")), \
            patch.object(po, "verify_get_path_objects", return_value=listing), \
            patch.object(po, "create_external_upload_record", return_value="upl-1"), \
            patch.object(po, "_stamp_output_objects", return_value=True), \
            patch.object(po, "_lambda_file_ingestion", side_effect=_ingest), \
            patch.object(po, "record_execution_outputs") as m_record:
        resp = po.lambda_handler(_event(request_context), MagicMock())
    assert resp["statusCode"] == 200
    assert m_record.call_args.kwargs["execution_status"] == "SUCCEEDED"
    assert claims_seen, "the write-back was not authorized against the executing user's claims"
    assert payloads, "no write-back reached file ingestion"
    return claims_seen[0], payloads[0]


@pytest.mark.unit
class TestEndStateStepWithTheLaunchContext:
    @pytest.mark.parametrize("make_context", CONTEXTS, ids=CONTEXT_IDS)
    def test_claims_resolve_the_same_as_from_the_whole_context(self, make_context):
        full_claims, _ = _run(make_context())
        minimal_claims, _ = _run(_minimal(make_context()))
        for field in ("tokens", "roles", "externalAttributes", "mfaEnabled"):
            assert minimal_claims[field] == full_claims[field], field
        assert minimal_claims["tokens"] == ["user1"]
        assert minimal_claims["roles"], "the claims resolved without the authorizer's roles"

    @pytest.mark.parametrize("make_context", CONTEXTS, ids=CONTEXT_IDS)
    def test_the_write_back_payload_is_the_same_as_from_the_whole_context(self, make_context):
        _, full_payload = _run(make_context())
        _, minimal_payload = _run(_minimal(make_context()))
        for field in ("body", "pathParameters", "lambdaCrossCall"):
            assert minimal_payload[field] == full_payload[field], field
        for field in ("path", "method"):
            assert minimal_payload["requestContext"]["http"][field] == \
                full_payload["requestContext"]["http"][field], field
        assert minimal_payload["requestContext"]["http"]["path"] == \
            API_UPLOAD_COMPLETE_EXTERNAL.path.replace("{uploadId}", "upl-1")
        assert minimal_payload["requestContext"]["http"]["method"] == "POST"

    def test_the_mfa_state_of_the_launch_reaches_the_write_back(self):
        # REST authorizer with vams:mfaEnabled "true": the cross-call carries it.
        _, payload = _run(_minimal(_rest_v1_context()))
        assert payload["lambdaCrossCall"] == {"userName": "user1", "mfaEnabled": True}
