# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every cross-call the import custom resource makes identifies itself with the source marker the
service handlers trust for `isSystem` and for the read-only exemption. The marker rides beside the
identity, so request_to_claims still resolves the caller to SYSTEM_USER."""

import io
import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("PIPELINE_SERVICE_V2_FUNCTION_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATE_SERVICE_FUNCTION_NAME", "t-tpl")
os.environ.setdefault("WORKFLOW_SERVICE_V2_FUNCTION_NAME", "t-wf-v2")
os.environ.setdefault("WORKFLOW_TRIGGER_SERVICE_FUNCTION_NAME", "t-trig")
os.environ.setdefault("LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET", "t-artefacts")

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import importGlobalPipelineWorkflow as imp  # noqa: E402
from backend.backend.handlers.auth import request_to_claims  # noqa: E402
from backend.backend.common.workflows import systemRecords as sr  # noqa: E402
from backend.backend.common.workflows import vamsSchemaImport as vsi  # noqa: E402


def _lambda_client(status_code=200, body="{}"):
    client = MagicMock()
    client.invoke.return_value = {
        "Payload": io.BytesIO(json.dumps({"statusCode": status_code, "body": body}).encode("utf-8"))}
    return client


@pytest.mark.unit
class TestCrossCallEnvelope:
    def test_every_invoke_carries_the_identity_and_the_marker(self):
        client = _lambda_client()
        with patch.object(imp, "lambda_client", client):
            status, inner = imp._invoke(vsi.TARGET_PIPELINE_SERVICE, "PUT",
                                        "/database/GLOBAL/pipelines/p", {"databaseId": "GLOBAL",
                                                                          "pipelineId": "p"},
                                        body={"enabled": True, "isSystem": True})
        assert (status, inner) == (200, {})
        event = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert event["lambdaCrossCall"] == {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}
        assert event["lambdaCrossCall"]["source"] == sr.IMPORT_SOURCE_MARKER
        assert json.loads(event["body"]) == {"enabled": True, "isSystem": True}

    def test_the_marked_envelope_is_what_the_handlers_trust(self):
        client = _lambda_client()
        with patch.object(imp, "lambda_client", client):
            imp._invoke(vsi.TARGET_WORKFLOW_SERVICE, "GET", "/database/GLOBAL/workflows/w",
                        {"databaseId": "GLOBAL", "workflowId": "w"},
                        query_parameters={"includeArchived": "true"})
        event = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert sr.is_schema_import_call(event) is True

    def test_the_marker_does_not_change_the_resolved_identity(self):
        client = _lambda_client()
        with patch.object(imp, "lambda_client", client):
            imp._invoke(vsi.TARGET_TRIGGER_SERVICE, "DELETE", "/database/GLOBAL/workflows/w",
                        {"databaseId": "GLOBAL", "workflowId": "w"})
        event = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        claims = request_to_claims(event)
        assert claims["tokens"] == ["SYSTEM_USER"]
        assert claims["mfaEnabled"] is True
