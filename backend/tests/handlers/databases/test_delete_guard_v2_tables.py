# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Delete-database dependency guard against the V2 pipeline/workflow tables.

A database that still owns pipelines or workflows cannot be deleted. Pipelines and workflows live in
PipelineStorageTableV2 / WorkflowStorageTableV2 (PK databaseId, SK pipelineId/workflowId) and are soft
deleted (archived=true), so the guard must read those tables and ignore archived rows.
"""

import boto3
import pytest
from moto import mock_aws

from backend.backend.common.resourceNames import ResourceKeys, get_table_name
from backend.backend.handlers.databases import databaseService as svc


def _create_table(resource, name, sort_key):
    return resource.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "databaseId", "KeyType": "HASH"},
                   {"AttributeName": sort_key, "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "databaseId", "AttributeType": "S"},
                              {"AttributeName": sort_key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.mark.unit
class TestDeleteGuardV2Tables:

    def test_guard_resolves_the_v2_tables(self):
        assert svc.pipeline_database == get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
        assert svc.workflow_database == get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)

    def test_guard_sees_v2_pipelines_and_workflows(self, monkeypatch):
        with mock_aws():
            resource = boto3.resource("dynamodb", region_name="us-east-1")
            pipelines = _create_table(resource, svc.pipeline_database, "pipelineId")
            workflows = _create_table(resource, svc.workflow_database, "workflowId")
            monkeypatch.setattr(svc, "dynamodb", resource)

            assert svc.check_pipelines("db1") is False
            assert svc.check_workflows("db1") is False

            pipelines.put_item(Item={"databaseId": "db1", "pipelineId": "p1"})
            workflows.put_item(Item={"databaseId": "db1", "workflowId": "w1"})

            assert svc.check_pipelines("db1") is True
            assert svc.check_workflows("db1") is True
            # A different database is unaffected.
            assert svc.check_pipelines("db2") is False

    def test_archived_entities_do_not_block_delete(self, monkeypatch):
        with mock_aws():
            resource = boto3.resource("dynamodb", region_name="us-east-1")
            pipelines = _create_table(resource, svc.pipeline_database, "pipelineId")
            workflows = _create_table(resource, svc.workflow_database, "workflowId")
            monkeypatch.setattr(svc, "dynamodb", resource)

            pipelines.put_item(Item={"databaseId": "db1", "pipelineId": "p1", "archived": True})
            workflows.put_item(Item={"databaseId": "db1", "workflowId": "w1", "archived": True})

            assert svc.check_pipelines("db1") is False
            assert svc.check_workflows("db1") is False

    def test_delete_database_blocked_by_v2_pipeline(self, monkeypatch):
        with mock_aws():
            resource = boto3.resource("dynamodb", region_name="us-east-1")
            pipelines = _create_table(resource, svc.pipeline_database, "pipelineId")
            _create_table(resource, svc.workflow_database, "workflowId")
            monkeypatch.setattr(svc, "dynamodb", resource)

            pipelines.put_item(Item={"databaseId": "db1", "pipelineId": "p1"})

            result = svc.delete_database("db1", claims_and_roles={"tokens": ["user"]})
            assert result.statusCode == 400
            assert result.message == "Database contains active pipelines"
