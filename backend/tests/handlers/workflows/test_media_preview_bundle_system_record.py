# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Media Preview Thumbnail records are system records in `SYSTEM - Preview`, like the 3D Preview
Thumbnail that shares their container (owner decision, 2026-11-17).

The bundle (backendPipelines/preview/3dThumbnail/vamsSchemaMedia) declares `isSystem: true` and the
`SYSTEM - Preview` category on its pipeline and workflow; its name (Media Preview Thumbnail), its fixed
ids (`preview-media-thumbnail`, the Preview3dThumbnailConstruct's idOverrides) and its template are
unchanged. Everything the system-record rules decide follows from the stored flag:

- the import's create and update bodies carry the flag and the category, so a deployment that registered
  the records from an earlier build as an ordinary `Preview` pipeline and workflow gets both on its next
  deploy: the changed bundle changes the registration's schema hash, the import lambda PUTs the update
  bodies through the services with its marked cross-call, and the services store what the body carries;
- an API caller may then switch the records on and off but not change the category;
- a run is stamped `isSystem` at launch, so the execution lists hide it by default and show it when the
  listing is scoped to the media workflow.

The compliance refusal and the SYSTEM-category checks walk every shipped system bundle, this one included
(test_compliancePipelineRule_systemRecordRefused.py, test_zip_bundle_category.py, docsSystemRecords.test.ts).
"""

import io
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.workflows import vamsSchemaImport as vsi
from backend.backend.common.workflows import workflowRecords as wr
from backend.backend.models.workflows import CreateWorkflowRequestModel
from backend.tests.handlers.pipelines import test_pipelineService_isSystem as pipelines
from backend.tests.handlers.pipelines.test_pipelineService_isSystem import _quiet_side_paths  # noqa: F401
from backend.tests.handlers.workflows import test_execute_stamps_system_flag as launch
from backend.tests.handlers.workflows import test_execution_list_system_runs as listing
from backend.tests.handlers.workflows import test_importGlobalPipelineWorkflow_source_marker as importer
from backend.tests.handlers.workflows import test_workflowService_isSystem as workflows
from backend.tests.handlers.workflows.test_execution_list_system_runs import _clear_caches  # noqa: F401
from backend.tests.handlers.workflows.test_workflowService_isSystem import bind_real_to_update_expr  # noqa: F401

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
BUNDLE_DIR = os.path.join(_REPO_ROOT, "backendPipelines", "preview", "3dThumbnail", "vamsSchemaMedia")
GLOBAL = "GLOBAL"
RECORD_ID = "preview-media-thumbnail"
NAME = "Media Preview Thumbnail"
CATEGORY = "SYSTEM - Preview"
EARLIER_CATEGORY = "Preview"
TEMPLATE_ID = "preview-media-thumbnail-default"
# Preview3dThumbnailConstruct's PreviewMediaThumbnailRegistration (resourceOverrides, idOverrides).
RESOURCE_OVERRIDES = {"lambdaName": "vamsExecutePreview3dThumbnailPipeline"}
ID_OVERRIDES = {"pipelineId": RECORD_ID, "workflowId": RECORD_ID}


def _read(name):
    with open(os.path.join(BUNDLE_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def _bundle():
    """The bundle as importGlobalPipelineWorkflow.assemble_bundle builds it from the uploaded files."""
    templates = sorted(name for name in os.listdir(os.path.join(BUNDLE_DIR, "templates")) if name.endswith(".json"))
    return {"pipeline": _read("pipeline.json"), "workflow": _read("workflow.json"),
            "templates": [_read(os.path.join("templates", name)) for name in templates]}


def _requests():
    requests = vsi.build_import_requests(_bundle(), dict(RESOURCE_OVERRIDES), dict(ID_OVERRIDES))
    return {request["kind"]: request for request in requests if request["kind"] in ("pipeline", "workflow")}


def _registered_workflow():
    """The workflow row as workflowService.create_workflow stores it from the import's create body."""
    body = _requests()["workflow"]["createBody"]
    request = CreateWorkflowRequestModel(**body)
    return wr.build_workflow_record(
        database_id=GLOBAL, workflow_id=RECORD_ID, workflow_name=request.workflowName,
        category=request.category or "", description=request.description or "",
        specified_pipelines=[wr.build_specified_pipeline_ref(GLOBAL, RECORD_ID, "p1")],
        system_config=request.systemConfig or wr.build_workflow_system_config(),
        workflow_arn="arn:aws:states:us-east-1:1:stateMachine:vams-preview", job_names=["job-p1"],
        is_system=body.get("isSystem") is True)


def _ordinary(row):
    """A stored row as an earlier build registered it: an ordinary Preview record, no isSystem."""
    row = dict(row, category=EARLIER_CATEGORY)
    row.pop("isSystem", None)
    return row


@pytest.mark.unit
class TestTheShippedBundle:
    def test_pipeline_and_workflow_are_system_records_in_the_preview_system_category(self):
        for name in ("pipeline.json", "workflow.json"):
            record = _read(name)
            assert record["isSystem"] is True, name
            assert record["category"] == CATEGORY, name

    def test_name_ids_and_template_are_unchanged(self):
        assert _read("pipeline.json")["pipelineName"] == NAME
        assert _read("workflow.json")["workflowName"] == NAME
        requests = _requests()
        assert requests["pipeline"]["createBody"]["pipelineId"] == RECORD_ID
        assert requests["workflow"]["createBody"]["workflowId"] == RECORD_ID
        templates = _bundle()["templates"]
        assert [(t["templateId"], t["templateName"], t["isDefault"]) for t in templates] == [
            (TEMPLATE_ID, "JPG/GIF thumbnails", True)]
        trigger = _read("workflow.json")["triggers"][0]
        assert trigger["defaultTemplateIds"] == {f"{GLOBAL}:{RECORD_ID}": TEMPLATE_ID}

    def test_the_import_creates_and_updates_with_the_flag_and_the_category(self):
        requests = _requests()
        for kind in ("pipeline", "workflow"):
            for body in ("createBody", "updateBody"):
                assert requests[kind][body]["isSystem"] is True, (kind, body)
                assert requests[kind][body]["category"] == CATEGORY, (kind, body)


@pytest.mark.unit
class TestTheNextDeploymentMakesAnEarlierRegistrationASystemRecord:
    """An earlier build stored an ordinary `Preview` pipeline and workflow; the import's update bodies
    make them system records in `SYSTEM - Preview`."""

    def test_the_pipeline_record_is_updated(self):
        body = _requests()["pipeline"]["updateBody"]
        stored = _ordinary(dict(pipelines.SYSTEM_ROW, pipelineId=RECORD_ID, pipelineName=NAME))
        event = pipelines._event("PUT", body, pipelines.IMPORT_CALL, path=f"/database/{GLOBAL}/pipelines/{RECORD_ID}",
                                 params={"databaseId": GLOBAL, "pipelineId": RECORD_ID})
        resp, table = pipelines._call(event, stored, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["category"] == CATEGORY and saved["isSystem"] is True
        assert saved["pipelineName"] == NAME

    def test_the_workflow_record_is_updated(self):
        body = dict(_requests()["workflow"]["updateBody"])
        # The workflow service resolves the referenced pipeline through a stub here (pipe1).
        body["specifiedPipelines"] = [{"pipelineId": "pipe1", "pipelineDatabaseId": GLOBAL}]
        stored = _ordinary(dict(workflows.SYSTEM_ROW, workflowId=RECORD_ID, workflowName=NAME))
        event = workflows._event("PUT", body, workflows.IMPORT_CALL, path=f"/database/{GLOBAL}/workflows/{RECORD_ID}",
                                 params={"databaseId": GLOBAL, "workflowId": RECORD_ID})
        resp, table = workflows._call(event, stored, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        written = workflows._written(table)
        assert written["category"] == CATEGORY and written["isSystem"] is True

    def test_the_import_lambda_sends_both_to_records_that_exist(self):
        calls = []

        def invoke(**kwargs):
            calls.append(json.loads(kwargs["Payload"].decode("utf-8")))
            return {"Payload": io.BytesIO(json.dumps({"statusCode": 200, "body": "{}"}).encode("utf-8"))}

        client = MagicMock()
        client.invoke.side_effect = invoke
        properties = {"inlineBundle": _bundle(), "resourceOverrides": dict(RESOURCE_OVERRIDES),
                      "idOverrides": dict(ID_OVERRIDES)}
        with patch.object(importer.imp, "lambda_client", client):
            result = importer.imp.register_bundle(properties)
        assert f"pipeline '{RECORD_ID}' updated" in result["applied"]
        assert f"workflow '{RECORD_ID}' updated" in result["applied"]
        puts = {event["requestContext"]["http"]["path"]: json.loads(event["body"])
                for event in calls if event["requestContext"]["http"]["method"] == "PUT"}
        for path in (f"/database/{GLOBAL}/pipelines/{RECORD_ID}", f"/database/{GLOBAL}/workflows/{RECORD_ID}"):
            assert puts[path]["category"] == CATEGORY, path
            assert puts[path]["isSystem"] is True, path


@pytest.mark.unit
class TestTheApiHoldsTheRecordsReadOnly:
    def test_a_caller_cannot_move_the_pipeline_out_of_the_system_category(self):
        stored = dict(pipelines.SYSTEM_ROW, pipelineId=RECORD_ID, pipelineName=NAME, category=CATEGORY)
        event = pipelines._event("PUT", {"category": EARLIER_CATEGORY},
                                 path=f"/database/{GLOBAL}/pipelines/{RECORD_ID}",
                                 params={"databaseId": GLOBAL, "pipelineId": RECORD_ID})
        resp, table = pipelines._call(event, stored)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["message"] == (
            'System pipelines are read-only except "enabled"; "category" cannot be changed.')
        table.put_item.assert_not_called()

    def test_a_caller_cannot_move_the_workflow_out_of_the_system_category(self):
        stored = dict(workflows.SYSTEM_ROW, workflowId=RECORD_ID, workflowName=NAME, category=CATEGORY)
        event = workflows._event("PUT", {"category": EARLIER_CATEGORY},
                                 path=f"/database/{GLOBAL}/workflows/{RECORD_ID}",
                                 params={"databaseId": GLOBAL, "workflowId": RECORD_ID})
        resp, _table = workflows._call(event, stored)
        assert resp["statusCode"] == 400
        assert json.loads(resp["body"])["message"] == (
            'System workflows are read-only except "enabled"; "category" cannot be changed.')

    def test_a_caller_may_still_switch_the_pipeline_off(self):
        stored = dict(pipelines.SYSTEM_ROW, pipelineId=RECORD_ID, pipelineName=NAME, category=CATEGORY)
        event = pipelines._event("PUT", {"enabled": False}, path=f"/database/{GLOBAL}/pipelines/{RECORD_ID}",
                                 params={"databaseId": GLOBAL, "pipelineId": RECORD_ID})
        resp, table = pipelines._call(event, stored)
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["enabled"] is False and saved["isSystem"] is True and saved["category"] == CATEGORY


@pytest.mark.unit
class TestItsRunsAreSystemRuns:
    def test_the_registered_workflow_is_a_system_workflow(self):
        workflow = _registered_workflow()
        assert workflow["isSystem"] is True and workflow["category"] == CATEGORY

    def test_a_launch_stamps_the_run(self):
        assert launch._main_row_for(_registered_workflow())["isSystem"] is True

    def test_the_lists_hide_its_runs_by_default_and_show_them_scoped_to_it(self):
        rows = [listing._row("m", 0, True, workflow=RECORD_ID, db=GLOBAL), listing._row("u", 1, False)]
        definition = {"isSystem": _registered_workflow()["isSystem"]}
        _status, unscoped = listing._run_global(listing._ScriptedIndex(rows), {"pageSize": "50"},
                                                workflow_definition=definition)
        assert listing._ids(unscoped) == ["u001"] and unscoped["includeSystem"] is False
        _status, scoped = listing._run_global(
            listing._ScriptedIndex(rows),
            {"pageSize": "50", "workflowId": RECORD_ID, "workflowDatabaseId": GLOBAL},
            workflow_definition=definition)
        assert listing._ids(scoped) == ["m000"] and scoped["includeSystem"] is True
