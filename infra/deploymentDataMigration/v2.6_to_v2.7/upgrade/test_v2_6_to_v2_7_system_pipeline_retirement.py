# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""moto tests for the v2.6 -> v2.7 ``systemPipelineRetirement`` report step.

Seeds executions of retired and live workflows, user workflows that do and do not reference a
retired pipeline, and the retired definitions themselves; asserts what the report lists and that the
step writes nothing.

Run: python -m pytest test_v2_6_to_v2_7_system_pipeline_retirement.py -q
"""

import importlib.util
import json
import os
from types import SimpleNamespace

import boto3
from moto import mock_aws

_HERE = os.path.dirname(os.path.abspath(__file__))
_REGION = "us-east-1"

EXECUTIONS_TABLE = "WorkflowExecutionsStorageTableV2"
WORKFLOWS_TABLE = "WorkflowStorageTableV2"
PIPELINES_TABLE = "PipelineStorageTableV2"

GENAI = "genai-metadata-3d-labeling-obj-glb-fbx-ply-stl-usd"
CAD = "metadata-extraction-cad-mesh"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "v26_to_v27_migration", os.path.join(_HERE, "v2.6_to_v2.7_migration.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_migration_module()


def _create_executions_table(client):
    client.create_table(
        TableName=EXECUTIONS_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"},
            {"AttributeName": "executionStartDate", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
            {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "WorkflowExecutionsByWorkflowGSI",
                "KeySchema": [
                    {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "HASH"},
                    {"AttributeName": "executionStartDate", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_composite_table(client, name, sort_key):
    client.create_table(
        TableName=name,
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": sort_key, "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": sort_key, "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _put_execution(client, execution_id, workflow_id, status, start, database_id="GLOBAL"):
    terminal = status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED_OUT")
    client.put_item(
        TableName=EXECUTIONS_TABLE,
        Item={
            "workflowExecutionId": {"S": execution_id},
            "workflowDatabaseId:workflowId": {"S": f"{database_id}:{workflow_id}"},
            "workflowDatabaseId": {"S": database_id},
            "workflowId": {"S": workflow_id},
            "executionStatus": {"S": status},
            "executionStartDate": {"S": start},
            "executionStopDate": {"S": "2026-09-01T01:00:00Z" if terminal else ""},
            "allListPartition": {"S": "execution"},
        },
    )


def _put_workflow(client, database_id, workflow_id, pipeline_refs, archived=False):
    specified = [
        {
            "M": {
                "pipelineDatabaseId": {"S": db},
                "pipelineId": {"S": pid},
                "pipelineDatabaseId:pipelineId": {"S": f"{db}:{pid}"},
                "jobName": {"S": f"step{i + 1}"},
                "defaultTemplateId": {"S": ""},
            }
        }
        for i, (db, pid) in enumerate(pipeline_refs)
    ]
    client.put_item(
        TableName=WORKFLOWS_TABLE,
        Item={
            "databaseId": {"S": database_id},
            "workflowId": {"S": workflow_id},
            "workflowName": {"S": workflow_id},
            "specifiedPipelines": {"L": specified},
            "archived": {"BOOL": archived},
            "enabled": {"BOOL": not archived},
        },
    )


def _put_pipeline(client, database_id, pipeline_id, archived=False):
    client.put_item(
        TableName=PIPELINES_TABLE,
        Item={
            "databaseId": {"S": database_id},
            "pipelineId": {"S": pipeline_id},
            "pipelineName": {"S": pipeline_id},
            "archived": {"BOOL": archived},
            "enabled": {"BOOL": not archived},
        },
    )


def _seed(client):
    _create_executions_table(client)
    _create_composite_table(client, WORKFLOWS_TABLE, "workflowId")
    _create_composite_table(client, PIPELINES_TABLE, "pipelineId")
    # Executions: two in flight on retired workflows, one finished, one in flight on a live workflow.
    _put_execution(client, "e-run-genai", GENAI, "RUNNING", "2026-09-01T00:00:00Z")
    _put_execution(client, "e-done-genai", GENAI, "SUCCEEDED", "2026-08-01T00:00:00Z")
    _put_execution(client, "e-new-cad", CAD, "NEW", "2026-09-02T00:00:00Z")
    _put_execution(client, "e-run-basic", "conversion-3d-basic", "RUNNING", "2026-09-01T00:00:00Z")
    # Workflows: wf-a and wf-c reference a retired pipeline; wf-b does not; the retired rows themselves
    # reference their own pipelines and must not be reported as user workflows.
    _put_workflow(client, "user-db", "wf-a", [("GLOBAL", CAD), ("user-db", "my-pipe")])
    _put_workflow(client, "user-db", "wf-b", [("GLOBAL", "conversion-3d-basic")])
    _put_workflow(client, "user-db", "wf-c", [("GLOBAL", GENAI)], archived=True)
    _put_workflow(client, "GLOBAL", GENAI, [("GLOBAL", GENAI)], archived=True)
    _put_workflow(client, "GLOBAL", CAD, [("GLOBAL", CAD)])
    # Pipelines: the GenAI one archived by the deploy; the CAD one absent.
    _put_pipeline(client, "GLOBAL", GENAI, archived=True)


_CFG = {
    "workflow_executions_storage_table_name_v2": EXECUTIONS_TABLE,
    "workflow_storage_table_name_v2": WORKFLOWS_TABLE,
    "pipeline_storage_table_name_v2": PIPELINES_TABLE,
}


def _snapshot(client):
    """Every row of the three tables, as a canonical string."""
    rows = []
    for table in (EXECUTIONS_TABLE, WORKFLOWS_TABLE, PIPELINES_TABLE):
        rows.append(sorted(json.dumps(item, sort_keys=True) for item in client.scan(TableName=table)["Items"]))
    return json.dumps(rows)


class _MotoSession:
    def client(self, name, **kwargs):
        return boto3.client(name, region_name=_REGION)


@mock_aws
def test_list_inflight_retired_executions_returns_non_terminal_rows_of_retired_workflows_only():
    client = boto3.client("dynamodb", region_name=_REGION)
    _seed(client)

    found = mig.list_inflight_retired_executions(client, EXECUTIONS_TABLE)

    assert sorted(item["executionId"] for item in found) == ["e-new-cad", "e-run-genai"]
    by_id = {item["executionId"]: item for item in found}
    assert by_id["e-run-genai"]["status"] == "RUNNING"
    assert by_id["e-run-genai"]["workflowId"] == GENAI
    assert by_id["e-run-genai"]["taskTimeoutSeconds"] == 18000
    assert by_id["e-new-cad"]["status"] == "NEW"
    assert by_id["e-new-cad"]["taskTimeoutSeconds"] == 900
    assert by_id["e-new-cad"]["startDate"] == "2026-09-02T00:00:00Z"


@mock_aws
def test_find_workflows_referencing_retired_pipelines_flags_user_workflows_not_the_retired_rows():
    client = boto3.client("dynamodb", region_name=_REGION)
    _seed(client)

    found = mig.find_workflows_referencing_retired_pipelines(client, WORKFLOWS_TABLE)

    assert sorted((item["databaseId"], item["workflowId"]) for item in found) == [
        ("user-db", "wf-a"),
        ("user-db", "wf-c"),
    ]
    by_key = {(item["databaseId"], item["workflowId"]): item for item in found}
    assert by_key[("user-db", "wf-a")]["retiredReferences"] == [f"GLOBAL:{CAD}"]
    assert by_key[("user-db", "wf-a")]["archived"] is False
    assert by_key[("user-db", "wf-c")]["retiredReferences"] == [f"GLOBAL:{GENAI}"]
    assert by_key[("user-db", "wf-c")]["archived"] is True


@mock_aws
def test_describe_retired_definitions_reports_presence_and_archive_state():
    client = boto3.client("dynamodb", region_name=_REGION)
    _seed(client)

    rows = mig.describe_retired_definitions(client, WORKFLOWS_TABLE, PIPELINES_TABLE)

    by_key = {(row["kind"], row["id"]): row for row in rows}
    assert len(rows) == 4
    assert by_key[("pipeline", GENAI)] == {
        "kind": "pipeline", "id": GENAI, "present": True, "archived": True, "enabled": False,
    }
    assert by_key[("pipeline", CAD)] == {
        "kind": "pipeline", "id": CAD, "present": False, "archived": None, "enabled": None,
    }
    assert by_key[("workflow", GENAI)]["archived"] is True
    assert by_key[("workflow", CAD)]["present"] is True
    assert by_key[("workflow", CAD)]["archived"] is False


@mock_aws
def test_build_report_limit_caps_each_listing():
    client = boto3.client("dynamodb", region_name=_REGION)
    _seed(client)
    _put_execution(client, "e-run-genai-2", GENAI, "RUNNING", "2026-09-03T00:00:00Z")

    report = mig.build_system_pipeline_retirement_report(client, _CFG, limit=1)

    genai_inflight = [e for e in report["inflightExecutions"] if e["workflowId"] == GENAI]
    assert len(genai_inflight) == 1
    assert len(report["referencingWorkflows"]) <= 1


@mock_aws
def test_run_system_pipeline_retirement_step_writes_nothing_and_returns_zero(monkeypatch):
    client = boto3.client("dynamodb", region_name=_REGION)
    _seed(client)
    before = _snapshot(client)
    monkeypatch.setattr(mig, "_session", lambda profile, region: _MotoSession())

    rc = mig.run_system_pipeline_retirement_step(
        dict(_CFG), SimpleNamespace(limit=None), None, None, None, False
    )

    assert rc == 0
    assert _snapshot(client) == before


@mock_aws
def test_run_system_pipeline_retirement_step_returns_zero_with_nothing_to_report(monkeypatch):
    client = boto3.client("dynamodb", region_name=_REGION)
    _create_executions_table(client)
    _create_composite_table(client, WORKFLOWS_TABLE, "workflowId")
    _create_composite_table(client, PIPELINES_TABLE, "pipelineId")
    monkeypatch.setattr(mig, "_session", lambda profile, region: _MotoSession())

    rc = mig.run_system_pipeline_retirement_step(
        dict(_CFG), SimpleNamespace(limit=None), None, None, None, False
    )

    assert rc == 0


def test_run_system_pipeline_retirement_step_fails_when_names_cannot_be_resolved():
    rc = mig.run_system_pipeline_retirement_step(
        {}, SimpleNamespace(limit=None), None, None, None, False
    )
    assert rc == 1
