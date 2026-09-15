# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""moto tests for the v2.6 -> v2.7 ``orphanedTriggers`` step.

Seeds trigger rows for live, archived, missing and retired-built-in workflows and asserts which rows
``delete_orphaned_triggers`` removes. A zero-row run proves nothing, so every case seeds rows.

Run: python -m pytest test_v2_6_to_v2_7_orphaned_triggers.py -q
"""

import importlib.util
import os

import boto3
from moto import mock_aws

_HERE = os.path.dirname(os.path.abspath(__file__))
_REGION = "us-east-1"

TRIGGERS_TABLE = "WorkflowTriggersStorageTable"
WORKFLOWS_TABLE = "WorkflowStorageTableV2"

RETIRED_GENAI = "genai-metadata-3d-labeling-obj-glb-fbx-ply-stl-usd"
RETIRED_CAD = "metadata-extraction-cad-mesh"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "v26_to_v27_migration", os.path.join(_HERE, "v2.6_to_v2.7_migration.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_migration_module()


def _create_triggers_table(client):
    client.create_table(
        TableName=TRIGGERS_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "workflowDatabaseId:workflowId", "AttributeType": "S"},
            {"AttributeName": "triggerType", "AttributeType": "S"},
            {"AttributeName": "triggerBaseType", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "HASH"},
            {"AttributeName": "triggerType", "KeyType": "RANGE"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "TriggersByBaseTypeGSI",
                "KeySchema": [
                    {"AttributeName": "triggerBaseType", "KeyType": "HASH"},
                    {"AttributeName": "workflowDatabaseId:workflowId", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_workflows_table(client):
    client.create_table(
        TableName=WORKFLOWS_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": "workflowId", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": "workflowId", "KeyType": "RANGE"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _put_trigger(client, database_id, workflow_id, trigger_id=""):
    sort_key = f"fileUpload#{trigger_id}" if trigger_id else "fileUpload"
    client.put_item(
        TableName=TRIGGERS_TABLE,
        Item={
            "workflowDatabaseId:workflowId": {"S": f"{database_id}:{workflow_id}"},
            "triggerType": {"S": sort_key},
            "triggerBaseType": {"S": "fileUpload"},
            "triggerId": {"S": trigger_id},
            "workflowDatabaseId": {"S": database_id},
            "workflowId": {"S": workflow_id},
            "triggerConfig": {
                "M": {
                    "inputFileFilters": {
                        "M": {"allow": {"L": [{"S": "*.glb"}]}, "exclude": {"L": []}}
                    },
                    "defaultTemplateIds": {"M": {}},
                }
            },
            "enabled": {"BOOL": True},
        },
    )


def _put_workflow(client, database_id, workflow_id, archived=False):
    client.put_item(
        TableName=WORKFLOWS_TABLE,
        Item={
            "databaseId": {"S": database_id},
            "workflowId": {"S": workflow_id},
            "workflowName": {"S": workflow_id},
            "archived": {"BOOL": archived},
            "enabled": {"BOOL": not archived},
        },
    )


def _seed(client):
    """Six trigger rows: one belongs to a live workflow, the other five are orphans of distinct kinds."""
    _create_triggers_table(client)
    _create_workflows_table(client)
    # keep: live workflow
    _put_workflow(client, "user-db", "wf-live")
    _put_trigger(client, "user-db", "wf-live")
    # delete: archived workflow, bare and suffixed trigger keys
    _put_workflow(client, "user-db", "wf-archived", archived=True)
    _put_trigger(client, "user-db", "wf-archived")
    _put_trigger(client, "user-db", "wf-archived", trigger_id="7f3a91")
    # delete: workflow row gone
    _put_trigger(client, "user-db", "wf-gone")
    # delete: retired built-in whose deploy-time archive did not run (row present, not archived)
    _put_workflow(client, "GLOBAL", RETIRED_GENAI)
    _put_trigger(client, "GLOBAL", RETIRED_GENAI)
    # delete: retired built-in archived by the deploy
    _put_workflow(client, "GLOBAL", RETIRED_CAD, archived=True)
    _put_trigger(client, "GLOBAL", RETIRED_CAD)


_CFG = {
    "workflow_triggers_storage_table_name": TRIGGERS_TABLE,
    "workflow_storage_table_name_v2": WORKFLOWS_TABLE,
}


def _remaining_trigger_keys(client):
    return sorted(
        (item["workflowDatabaseId:workflowId"]["S"], item["triggerType"]["S"])
        for item in client.scan(TableName=TRIGGERS_TABLE)["Items"]
    )


@mock_aws
def test_delete_orphaned_triggers_removes_missing_archived_and_retired_rows():
    client = boto3.client("dynamodb", region_name=_REGION)
    _seed(client)

    counts = mig.delete_orphaned_triggers(client, _CFG, dry_run=False, limit=None)

    assert counts == {
        "scanned": 6,
        "deleted": 5,
        "kept": 1,
        "workflow_missing": 1,
        "workflow_archived": 2,
        "retired_builtin": 2,
        "errors": 0,
    }
    assert _remaining_trigger_keys(client) == [("user-db:wf-live", "fileUpload")]


@mock_aws
def test_delete_orphaned_triggers_is_idempotent():
    client = boto3.client("dynamodb", region_name=_REGION)
    _seed(client)
    mig.delete_orphaned_triggers(client, _CFG, dry_run=False, limit=None)

    counts = mig.delete_orphaned_triggers(client, _CFG, dry_run=False, limit=None)

    assert counts["scanned"] == 1 and counts["deleted"] == 0 and counts["kept"] == 1
    assert _remaining_trigger_keys(client) == [("user-db:wf-live", "fileUpload")]


@mock_aws
def test_delete_orphaned_triggers_dry_run_deletes_nothing():
    client = boto3.client("dynamodb", region_name=_REGION)
    _seed(client)

    counts = mig.delete_orphaned_triggers(client, _CFG, dry_run=True, limit=None)

    # Under dry run "deleted" is the would-delete count; the table is untouched.
    assert counts["deleted"] == 5 and counts["kept"] == 1 and counts["errors"] == 0
    assert len(_remaining_trigger_keys(client)) == 6


@mock_aws
def test_delete_orphaned_triggers_limit_caps_rows_examined():
    client = boto3.client("dynamodb", region_name=_REGION)
    _seed(client)

    counts = mig.delete_orphaned_triggers(client, _CFG, dry_run=True, limit=2)

    assert counts["scanned"] == 2
    assert counts["deleted"] + counts["kept"] == 2


def test_classify_trigger_row_decisions():
    workflows = {
        ("user-db", "wf-live"): {"archived": {"BOOL": False}},
        ("user-db", "wf-archived"): {"archived": {"BOOL": True}},
        ("user-db", "wf-string-archived"): {"archived": {"S": "true"}},
        ("GLOBAL", RETIRED_CAD): {"archived": {"BOOL": False}},
    }

    def lookup(database_id, workflow_id):
        return workflows.get((database_id, workflow_id))

    def row(database_id, workflow_id):
        return {
            "workflowDatabaseId:workflowId": {"S": f"{database_id}:{workflow_id}"},
            "triggerType": {"S": "fileUpload"},
            "workflowDatabaseId": {"S": database_id},
            "workflowId": {"S": workflow_id},
        }

    assert mig.classify_trigger_row(row("user-db", "wf-live"), lookup) == ("keep", "workflow-live")
    assert mig.classify_trigger_row(row("user-db", "wf-archived"), lookup) == (
        "delete",
        "workflow-archived",
    )
    assert mig.classify_trigger_row(row("user-db", "wf-string-archived"), lookup) == (
        "delete",
        "workflow-archived",
    )
    assert mig.classify_trigger_row(row("user-db", "wf-gone"), lookup) == (
        "delete",
        "workflow-missing",
    )
    # A retired built-in is deleted whether or not the deploy-time archive reached its row.
    assert mig.classify_trigger_row(row("GLOBAL", RETIRED_CAD), lookup) == (
        "delete",
        "retired-builtin",
    )
    # A user workflow that happens to share a retired id is NOT a built-in.
    assert mig.classify_trigger_row(row("user-db", RETIRED_CAD), lookup) == (
        "delete",
        "workflow-missing",
    )


def test_classify_trigger_row_falls_back_to_the_composite_key():
    calls = []

    def lookup(database_id, workflow_id):
        calls.append((database_id, workflow_id))
        return None

    row = {
        "workflowDatabaseId:workflowId": {"S": "user-db:wf-legacy"},
        "triggerType": {"S": "fileUpload"},
    }
    assert mig.classify_trigger_row(row, lookup) == ("delete", "workflow-missing")
    assert calls == [("user-db", "wf-legacy")]


def test_retired_ids_are_the_two_removed_builtins():
    assert mig.RETIRED_PIPELINE_IDS == (RETIRED_GENAI, RETIRED_CAD)
    assert mig.RETIRED_WORKFLOW_IDS == mig.RETIRED_PIPELINE_IDS
    assert mig.RETIRED_TASK_TIMEOUT_SECONDS == {RETIRED_GENAI: 18000, RETIRED_CAD: 900}
    assert mig.is_retired_builtin_workflow("GLOBAL", RETIRED_GENAI) is True
    assert mig.is_retired_builtin_workflow("user-db", RETIRED_GENAI) is False
