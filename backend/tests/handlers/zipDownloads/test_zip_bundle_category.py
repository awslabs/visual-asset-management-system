# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The Zip Archive records carry the `SYSTEM - Utility` category, and a deployment re-asserts it.

Every shipped system bundle pairs `isSystem: true` with a `SYSTEM - <Area>` category, so a permission
constraint on `category` selects the system records together and never matches a deployment's own
pipelines in an ordinary category. The Zip Archive pipeline and workflow keep their name and their fixed
ids (`utility-zip-archive`); only the category is `SYSTEM - Utility`.

A deployment that registered the records from an earlier build stored `Utility`. The changed bundle
changes the registration's schema hash, so the next deployment runs the import lambda
(`handlers/workflows/importGlobalPipelineWorkflow.register_bundle`), which finds the records and PUTs the
bundle's update bodies (`common/workflows/vamsSchemaImport`, `category` included) through the pipeline and
workflow services with its marked cross-call. The system-record guard exempts that call, so the stored
category becomes the shipped one, the way the 3D Preview Thumbnail moved from `Preview` to
`SYSTEM - Preview`.
"""

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.pipelines import test_pipelineService_isSystem as pipelines
from backend.tests.handlers.workflows import test_importGlobalPipelineWorkflow_source_marker as importer
from backend.tests.handlers.pipelines.test_pipelineService_isSystem import _quiet_side_paths  # noqa: F401
from backend.tests.handlers.workflows import test_workflowService_isSystem as workflows
from backend.tests.handlers.workflows.test_workflowService_isSystem import bind_real_to_update_expr  # noqa: F401
from backend.tests.handlers.zipDownloads import zipBundle

CATEGORY = "SYSTEM - Utility"
EARLIER_CATEGORY = "Utility"
GLOBAL = zipBundle.GLOBAL
RECORD_ID = zipBundle.WORKFLOW_ID
REPO_ROOT = Path(__file__).resolve().parents[4]


def _shipped_system_bundles():
    found = []
    for pipeline_file in sorted((REPO_ROOT / "backendPipelines").rglob("vamsSchema/**/pipeline.json")):
        if json.loads(pipeline_file.read_text()).get("isSystem") is True:
            found.append(pipeline_file.parent)
    return found


@pytest.mark.unit
class TestTheShippedBundle:
    def test_pipeline_and_workflow_declare_the_system_category(self):
        bundle = zipBundle.load_bundle()
        assert bundle["pipeline"]["category"] == CATEGORY
        assert bundle["workflow"]["category"] == CATEGORY

    def test_name_and_ids_are_unchanged(self):
        bundle = zipBundle.load_bundle()
        names = (bundle["pipeline"]["pipelineName"], bundle["workflow"]["workflowName"])
        assert names == ("Zip Archive", "Zip Archive")
        requests = zipBundle.import_requests()
        assert requests["pipeline"]["createBody"]["pipelineId"] == RECORD_ID
        assert requests["workflow"]["createBody"]["workflowId"] == RECORD_ID

    def test_the_import_creates_and_updates_with_it(self):
        requests = zipBundle.import_requests()
        for kind in ("pipeline", "workflow"):
            assert requests[kind]["createBody"]["category"] == CATEGORY, kind
            assert requests[kind]["updateBody"]["category"] == CATEGORY, kind
        _pipeline, workflow = zipBundle.registered_records()
        assert workflow["category"] == CATEGORY

    def test_every_shipped_system_bundle_uses_a_system_category(self):
        bundles = _shipped_system_bundles()
        assert any(b.parent.name == "zipArchive" for b in bundles)  # control: the zip bundle is found
        for bundle in bundles:
            for name in ("pipeline.json", "workflow.json"):
                category = json.loads((bundle / name).read_text()).get("category", "")
                assert category.startswith("SYSTEM - ") and len(category) > len("SYSTEM - "), (
                    f"{bundle.relative_to(REPO_ROOT)}/{name}: {category!r}")


@pytest.mark.unit
class TestTheNextDeploymentReassertsIt:
    """The stored records of an earlier build carry `Utility`; the import's update bodies replace it."""

    def test_the_pipeline_record_is_updated(self):
        body = zipBundle.import_requests()["pipeline"]["updateBody"]
        stored = dict(pipelines.SYSTEM_ROW, pipelineId=RECORD_ID, pipelineName="Zip Archive",
                      category=EARLIER_CATEGORY)
        event = pipelines._event("PUT", body, pipelines.IMPORT_CALL, path=f"/database/{GLOBAL}/pipelines/{RECORD_ID}",
                                 params={"databaseId": GLOBAL, "pipelineId": RECORD_ID})
        resp, table = pipelines._call(event, stored, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        saved = table.put_item.call_args.kwargs["Item"]
        assert saved["category"] == CATEGORY and saved["isSystem"] is True
        assert saved["pipelineName"] == "Zip Archive"

    def test_the_workflow_record_is_updated(self):
        body = dict(zipBundle.import_requests()["workflow"]["updateBody"])
        # The workflow service resolves the referenced pipeline through a stub here (pipe1).
        body["specifiedPipelines"] = [{"pipelineId": "pipe1", "pipelineDatabaseId": GLOBAL}]
        stored = dict(workflows.SYSTEM_ROW, workflowId=RECORD_ID, workflowName="Zip Archive",
                      category=EARLIER_CATEGORY)
        event = workflows._event("PUT", body, workflows.IMPORT_CALL, path=f"/database/{GLOBAL}/workflows/{RECORD_ID}",
                                 params={"databaseId": GLOBAL, "workflowId": RECORD_ID})
        resp, table = workflows._call(event, stored, tokens=("SYSTEM_USER",))
        assert resp["statusCode"] == 200, resp["body"]
        written = workflows._written(table)
        assert written["category"] == CATEGORY and written["isSystem"] is True

    def test_the_import_lambda_sends_the_category_to_records_that_exist(self):
        # The deploy step itself: a changed bundle file changes the registration's schema hash
        # (VamsSchemaRegistration.hashSchema; infra/test/pipelines/vamsSchemaRegistrationHash.test.ts), so
        # CloudFormation sends an Update and the import lambda registers the bundle again. For a pipeline
        # or workflow that already exists it PUTs the bundle's update body through the service.
        calls = []

        def invoke(**kwargs):
            event = json.loads(kwargs["Payload"].decode("utf-8"))
            calls.append(event)
            return {"Payload": io.BytesIO(json.dumps({"statusCode": 200, "body": "{}"}).encode("utf-8"))}

        client = MagicMock()
        client.invoke.side_effect = invoke
        properties = {"inlineBundle": zipBundle.load_bundle(),
                      "resourceOverrides": {"lambdaName": "vamsExecuteZipArchivePipeline"},
                      "idOverrides": dict(zipBundle.ID_OVERRIDES)}
        with patch.object(importer.imp, "lambda_client", client):
            result = importer.imp.register_bundle(properties)
        assert f"pipeline '{RECORD_ID}' updated" in result["applied"]
        assert f"workflow '{RECORD_ID}' updated" in result["applied"]
        puts = {event["requestContext"]["http"]["path"]: json.loads(event["body"])
                for event in calls if event["requestContext"]["http"]["method"] == "PUT"}
        for path in (f"/database/{GLOBAL}/pipelines/{RECORD_ID}", f"/database/{GLOBAL}/workflows/{RECORD_ID}"):
            assert puts[path]["category"] == CATEGORY, path
            assert puts[path]["isSystem"] is True, path

    def test_an_api_caller_still_cannot_change_it(self):
        # The guard that lets the deployment re-assert the category refuses everyone else.
        stored = dict(pipelines.SYSTEM_ROW, pipelineId=RECORD_ID, category=CATEGORY)
        event = pipelines._event("PUT", {"category": EARLIER_CATEGORY},
                                 path=f"/database/{GLOBAL}/pipelines/{RECORD_ID}",
                                 params={"databaseId": GLOBAL, "pipelineId": RECORD_ID})
        resp, table = pipelines._call(event, stored)
        assert resp["statusCode"] == 400
        table.put_item.assert_not_called()
