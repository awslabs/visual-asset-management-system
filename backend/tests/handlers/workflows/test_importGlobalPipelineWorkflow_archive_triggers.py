# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The registration custom resource's Delete removes the built-in workflow's triggers before archiving.

A `vamsSchema` bundle registers a workflow and its fileUpload trigger through SYSTEM_USER cross-calls;
removing the registration archives the workflow, and a trigger row that outlives it keeps matching
uploads. The CR lists the workflow's triggers through the trigger service (paged, the way a client does)
and deletes each by its key -- percent-encoded, because a "type#triggerId" key carries a '#' the service
decodes from the path parameter -- before the workflow DELETE, then the pipeline DELETE. Every step stays
best-effort: a teardown is never blocked, failures surface as warning attributes.
"""

import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

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

TRIGGERS_PATH = "/database/GLOBAL/workflows/conv/triggers"
WORKFLOW_PATH = "/database/GLOBAL/workflows/conv"
PIPELINE_PATH = "/database/GLOBAL/pipelines/conv"


def _resp(status_code, body=None):
    class _Payload:
        def read(self):
            return json.dumps({"statusCode": status_code, "body": json.dumps(body or {})}).encode()
    return {"Payload": _Payload()}


def _props():
    return {"inlineBundle": {
        "pipeline": {"pipelineId": "conv", "pipelineName": "Converter",
                     "executionConfig": {"executionType": "Lambda", "lambda": {}}},
        "workflow": {"workflowId": "conv", "workflowName": "Convert WF"},
    }}


def _page(keys, next_token=None):
    body = {"Items": [{"triggerType": k, "triggerBaseType": k.split("#", 1)[0]} for k in keys]}
    if next_token:
        body["NextToken"] = next_token
    return {"message": body}


class _Services:
    """A stand-in for the four service lambdas. ``pages`` maps a startingToken (None for the first
    page) to the trigger listing it answers; ``delete_status`` is the trigger DELETE's status;
    ``list_body``, when given, is the fixed 200 body every GET answers instead of a page."""

    def __init__(self, pages, list_status=200, delete_status=200, list_body=None):
        self.pages = pages
        self.list_status = list_status
        self.delete_status = delete_status
        self.list_body = list_body
        self.calls = []  # (method, path, pathParameters, queryStringParameters)

    def invoke(self, FunctionName, InvocationType, Payload):
        event = json.loads(Payload.decode("utf-8"))
        method = event["requestContext"]["http"]["method"]
        path = event["requestContext"]["http"]["path"]
        self.calls.append((method, path, event.get("pathParameters") or {},
                           event.get("queryStringParameters") or {}))
        if method == "GET" and path == TRIGGERS_PATH:
            if self.list_status != 200:
                return _resp(self.list_status, {"message": "nope"})
            if self.list_body is not None:
                return _resp(200, self.list_body)
            token = (event.get("queryStringParameters") or {}).get("startingToken")
            return _resp(200, self.pages[token])
        if method == "DELETE" and path.startswith(TRIGGERS_PATH + "/"):
            return _resp(self.delete_status, {"message": "Trigger deleted" if self.delete_status == 200 else "boom"})
        return _resp(200, {"message": "archived"})

    def deletes(self):
        return [path for method, path, _pp, _q in self.calls if method == "DELETE"]


def _archive(services):
    with patch.object(imp, "lambda_client") as m:
        m.invoke.side_effect = services.invoke
        return imp.archive_bundle(_props())


@pytest.mark.unit
class TestDeleteRemovesTriggersBeforeArchiving:
    def test_every_trigger_is_deleted_by_its_encoded_key(self):
        services = _Services({None: _page(["fileUpload", "fileUpload#nightly"])})
        result = _archive(services)
        assert result["warnings"] == []
        assert services.deletes()[:2] == [TRIGGERS_PATH + "/fileUpload", TRIGGERS_PATH + "/fileUpload%23nightly"]

    def test_the_encoded_key_is_also_the_path_parameter(self):
        services = _Services({None: _page(["fileUpload#nightly"])})
        _archive(services)
        delete_calls = [c for c in services.calls if c[0] == "DELETE" and c[1].startswith(TRIGGERS_PATH)]
        assert delete_calls[0][2] == {"databaseId": "GLOBAL", "workflowId": "conv",
                                      "triggerType": "fileUpload%23nightly"}

    def test_triggers_go_before_the_workflow_and_the_workflow_before_the_pipeline(self):
        services = _Services({None: _page(["fileUpload", "fileUpload#nightly"])})
        _archive(services)
        assert services.deletes() == [TRIGGERS_PATH + "/fileUpload", TRIGGERS_PATH + "/fileUpload%23nightly",
                                      WORKFLOW_PATH, PIPELINE_PATH]

    def test_the_listing_is_paged_to_exhaustion(self):
        services = _Services({None: _page(["fileUpload"], next_token="tok-2"),
                              "tok-2": _page(["fileUpload#second"])})
        _archive(services)
        list_calls = [c for c in services.calls if c[0] == "GET"]
        assert [c[3].get("startingToken") for c in list_calls] == [None, "tok-2"]
        assert {(c[3]["maxItems"], c[3]["pageSize"]) for c in list_calls} == {("500", "500")}
        assert services.deletes()[:2] == [TRIGGERS_PATH + "/fileUpload", TRIGGERS_PATH + "/fileUpload%23second"]

    def test_a_missing_workflow_lists_no_triggers_and_still_archives(self):
        services = _Services({}, list_status=404)
        result = _archive(services)
        assert result["warnings"] == []
        assert services.deletes() == [WORKFLOW_PATH, PIPELINE_PATH]

    def test_a_listing_whose_body_is_not_a_page_yields_no_keys(self):
        """A 200 whose ``message`` is a plain acknowledgement string rather than a listing page carries
        no trigger keys: nothing is deleted and nothing is warned."""
        services = _Services({}, list_body={"message": "archived"})
        result = _archive(services)
        assert result["warnings"] == []
        assert services.deletes() == [WORKFLOW_PATH, PIPELINE_PATH]

    def test_a_failed_listing_is_a_warning_and_the_archive_proceeds(self):
        services = _Services({}, list_status=500)
        result = _archive(services)
        assert any(w.startswith("trigger listing:") for w in result["warnings"])
        assert services.deletes() == [WORKFLOW_PATH, PIPELINE_PATH]

    def test_a_failed_trigger_delete_is_a_warning_not_a_teardown_failure(self):
        services = _Services({None: _page(["fileUpload"])}, delete_status=500)
        result = _archive(services)
        assert result["warnings"] == ["trigger delete fileUpload: boom"]
        assert services.deletes()[-2:] == [WORKFLOW_PATH, PIPELINE_PATH]

    def test_an_already_deleted_trigger_is_not_a_warning(self):
        services = _Services({None: _page(["fileUpload"])}, delete_status=404)
        assert _archive(services)["warnings"] == []

    def test_the_cloudformation_delete_surfaces_trigger_warnings(self):
        services = _Services({None: _page(["fileUpload"])}, delete_status=500)
        event = {"RequestType": "Delete", "ResourceProperties": _props(), "PhysicalResourceId": "conv"}
        with patch.object(imp, "lambda_client") as m:
            m.invoke.side_effect = services.invoke
            resp = imp.lambda_handler(event, MagicMock(log_stream_name="log"))
        assert "trigger delete fileUpload" in resp["Data"]["warnings"]
