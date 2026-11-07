# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The zipArchive pipeline and workflow records as a deployment registers them (#1125 D9).

Reads the SHIPPED bundle (backendPipelines/utility/zipArchive/vamsSchema), builds the import requests
with the REAL importer (common/workflows/vamsSchemaImport.build_import_requests, with the CDK's
idOverrides), parses each create body with the REAL create request model, and stores the systemConfig
the way the create services do (verbatim from the parsed request). Tests that launch zip jobs read the
records from here, never from a hand-written systemConfig: #1125 D9 hid behind a test that wrote the
workflow's systemConfig the way the zip service needed it, while the shipped bundle declared none and
the importer stored the defaults (one input file, no folders, no whole asset)."""

import json
import os

from backend.backend.common.workflows import vamsSchemaImport as vsi
from backend.backend.common.workflows import workflowRecords as wr
from backend.backend.models.pipelines import CreatePipelineRequestModel
from backend.backend.models.workflows import CreateWorkflowRequestModel

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
BUNDLE_DIR = os.path.join(_REPO_ROOT, "backendPipelines", "utility", "zipArchive", "vamsSchema")

GLOBAL = "GLOBAL"
WORKFLOW_ID = "utility-zip-archive"
# ZipArchiveConstruct's idOverrides (infra/lib/nestedStacks/pipelines/utility/zipArchive).
ID_OVERRIDES = {"pipelineId": WORKFLOW_ID, "pipelineDatabaseId": GLOBAL,
                "workflowId": WORKFLOW_ID, "workflowDatabaseId": GLOBAL}


def _read(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_bundle():
    """The bundle as importGlobalPipelineWorkflow.assemble_bundle builds it from the uploaded files."""
    bundle = {"pipeline": _read(os.path.join(BUNDLE_DIR, "pipeline.json"))}
    workflow_path = os.path.join(BUNDLE_DIR, "workflow.json")
    if os.path.exists(workflow_path):
        bundle["workflow"] = _read(workflow_path)
    template_dir = os.path.join(BUNDLE_DIR, "templates")
    if os.path.isdir(template_dir):
        bundle["templates"] = [_read(os.path.join(template_dir, name))
                               for name in sorted(os.listdir(template_dir)) if name.endswith(".json")]
    return bundle


def import_requests(bundle=None):
    """{kind: request} of the importer's create/update requests for the shipped bundle."""
    requests = vsi.build_import_requests(bundle or load_bundle(),
                                         {"lambdaName": "vamsExecuteZipArchivePipeline"},
                                         dict(ID_OVERRIDES))
    return {request["kind"]: request for request in requests if request["kind"] in ("pipeline", "workflow")}


def registered_records(bundle=None):
    """(pipeline record, workflow record) as the create services store them for the shipped bundle."""
    requests = import_requests(bundle)
    pipeline_body = requests["pipeline"]["createBody"]
    pipeline_request = CreatePipelineRequestModel(**pipeline_body)
    pipeline = {
        "databaseId": GLOBAL, "pipelineId": WORKFLOW_ID,
        "pipelineName": pipeline_request.pipelineName, "enabled": True, "archived": False,
        "isSystem": True, "executionConfig": dict(pipeline_request.executionConfig),
        "systemConfig": dict(pipeline_request.systemConfig),
    }
    workflow_body = requests["workflow"]["createBody"]
    workflow_request = CreateWorkflowRequestModel(**workflow_body)
    record = wr.build_workflow_record(
        database_id=GLOBAL, workflow_id=WORKFLOW_ID, workflow_name=workflow_request.workflowName,
        category=workflow_request.category or "", description=workflow_request.description or "",
        specified_pipelines=[wr.build_specified_pipeline_ref(GLOBAL, WORKFLOW_ID, "p1")],
        # workflowService.create_workflow: the parsed request's systemConfig, else the defaults.
        system_config=workflow_request.systemConfig or wr.build_workflow_system_config(),
        workflow_arn="arn:aws:states:us-east-1:1:stateMachine:vams-zip", job_names=["job-p1"],
        is_system=True)
    return pipeline, record
