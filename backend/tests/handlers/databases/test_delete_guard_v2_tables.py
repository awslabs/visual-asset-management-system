# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Delete-database dependency guard against the V2 pipeline/workflow tables.

A database that still owns pipelines or workflows cannot be deleted. Pipelines and workflows live in
PipelineStorageTableV2 / WorkflowStorageTableV2 (PK databaseId, SK pipelineId/workflowId) and are soft
deleted (archived=true), so the guard must read those tables and ignore archived rows.

The guard runs after the database record is read and the caller is authorized to delete it, so a
caller who is denied on the database, or names one that does not exist, learns nothing about what
it contains: the answer is 403 or 404 whatever the contents.
"""

import boto3
import pytest
from moto import mock_aws

from backend.backend.common.resourceNames import ResourceKeys, get_table_name
from backend.backend.handlers.databases import databaseService as svc

AUTHENTICATED = {"tokens": ["user"], "roles": ["admin"], "mfaEnabled": False}


def _create_table(resource, name, sort_key):
    return resource.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "databaseId", "KeyType": "HASH"},
                   {"AttributeName": sort_key, "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "databaseId", "AttributeType": "S"},
                              {"AttributeName": sort_key, "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def _create_database_table(resource, *database_ids):
    table = resource.create_table(
        TableName=svc.db_database,
        KeySchema=[{"AttributeName": "databaseId", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "databaseId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    for database_id in database_ids:
        table.put_item(Item={"databaseId": database_id, "description": "test"})
    return table


class _Enforcer:
    """Stands in for CasbinEnforcer with a fixed verdict, recording every decision asked for."""

    calls = []
    verdict = True

    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, act):
        _Enforcer.calls.append((obj.get("object__type"), obj.get("databaseId"), act))
        return _Enforcer.verdict


@pytest.fixture
def enforcer(monkeypatch):
    _Enforcer.calls = []
    _Enforcer.verdict = True
    monkeypatch.setattr(svc, "CasbinEnforcer", _Enforcer)
    return _Enforcer


@pytest.fixture
def tables(monkeypatch):
    """The database, pipeline and workflow tables, with database db1 present and owning a pipeline."""
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        databases = _create_database_table(resource, "db1")
        pipelines = _create_table(resource, svc.pipeline_database, "pipelineId")
        _create_table(resource, svc.workflow_database, "workflowId")
        pipelines.put_item(Item={"databaseId": "db1", "pipelineId": "p1"})
        monkeypatch.setattr(svc, "dynamodb", resource)
        yield databases


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

    def test_delete_database_blocked_by_v2_pipeline(self, enforcer, tables):
        result = svc.delete_database("db1", claims_and_roles=AUTHENTICATED)

        assert result.statusCode == 400
        assert result.message == "Database contains active pipelines"
        assert tables.get_item(Key={"databaseId": "db1"}).get("Item") is not None


@pytest.mark.unit
class TestDeleteGuardRunsAfterAuthorization:
    @pytest.fixture
    def checks(self, monkeypatch):
        """Records each content check that runs, while still running it."""
        ran = []
        for name in ("check_workflows", "check_pipelines", "check_assets"):
            real = getattr(svc, name)
            monkeypatch.setattr(svc, name, lambda database_id, _real=real, _name=name: (
                ran.append(_name) or _real(database_id)))
        return ran

    def test_a_denied_caller_is_refused_before_the_content_checks(self, enforcer, tables, checks):
        enforcer.verdict = False

        result = svc.delete_database("db1", claims_and_roles=AUTHENTICATED)

        assert result.statusCode == 403
        assert checks == []
        assert enforcer.calls == [("database", "db1", "DELETE")]
        assert tables.get_item(Key={"databaseId": "db1"}).get("Item") is not None

    def test_a_missing_database_is_not_found_before_the_content_checks(self, enforcer, tables, checks):
        """db2 has no record but does own a pipeline row, so a guard that ran first would answer 400."""
        svc.dynamodb.Table(svc.pipeline_database).put_item(
            Item={"databaseId": "db2", "pipelineId": "p2"})

        result = svc.delete_database("db2", claims_and_roles=AUTHENTICATED)

        assert result.statusCode == 404
        assert checks == []
        assert enforcer.calls == []

    def test_an_empty_identity_is_refused_without_consulting_casbin(self, enforcer, tables, checks):
        result = svc.delete_database("db1", claims_and_roles={"tokens": [], "roles": []})

        assert result.statusCode == 403
        assert checks == []
        assert enforcer.calls == []

    def test_a_denial_reads_the_same_whatever_the_database_contains(self, enforcer, tables):
        enforcer.verdict = False
        with_contents = svc.delete_database("db1", claims_and_roles=AUTHENTICATED)
        svc.dynamodb.Table(svc.pipeline_database).delete_item(
            Key={"databaseId": "db1", "pipelineId": "p1"})
        without_contents = svc.delete_database("db1", claims_and_roles=AUTHENTICATED)

        assert ((with_contents.statusCode, with_contents.message)
                == (without_contents.statusCode, without_contents.message))

    def test_a_permitted_caller_still_reaches_the_content_checks(self, enforcer, tables, checks):
        """Positive control: authorization first must not skip the guard."""
        result = svc.delete_database("db1", claims_and_roles=AUTHENTICATED)

        assert result.statusCode == 400
        assert checks[:2] == ["check_workflows", "check_pipelines"]
