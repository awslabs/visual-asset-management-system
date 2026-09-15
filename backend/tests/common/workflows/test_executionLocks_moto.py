# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The lock protocol against moto's DynamoDB: the conditional PutItem takes over an expired row and
refuses a live one (returning the holder), the conditional DeleteItem releases only the holder's rows,
and the row-derived terminal release walks the real workflow and input tables."""

import os

import boto3
import pytest
from moto import mock_aws

from backend.backend.common.workflows import executionLocks as el

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
NOW = 1_800_000_000
K1 = el.build_lock_key("GLOBAL", "wf", "db", "a1", "/a1/one.glb", "v1")
K2 = el.build_lock_key("GLOBAL", "wf", "db", "a1", "/a1/two.glb", "v2")


def _resource():
    return boto3.resource("dynamodb", region_name=REGION)


def _lock_table(ddb, name="t-locks"):
    table = ddb.create_table(
        TableName=name,
        KeySchema=[{"AttributeName": "lockKey", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "lockKey", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


def _workflow_table(ddb):
    table = ddb.create_table(
        TableName="t-workflows",
        KeySchema=[{"AttributeName": "databaseId", "KeyType": "HASH"},
                   {"AttributeName": "workflowId", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "databaseId", "AttributeType": "S"},
                              {"AttributeName": "workflowId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


def _inputs_table(ddb):
    table = ddb.create_table(
        TableName="t-inputs",
        KeySchema=[{"AttributeName": "workflowExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "databaseId:assetId:inputAssetFileKey", "KeyType": "RANGE"}],
        AttributeDefinitions=[
            {"AttributeName": "workflowExecutionId", "AttributeType": "S"},
            {"AttributeName": "databaseId:assetId:inputAssetFileKey", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


@pytest.mark.integration
@mock_aws
class TestExecutionLocksOnDynamoDb:
    def test_acquire_then_conflict_reports_the_holder_and_rolls_back(self):
        table = _lock_table(_resource())
        assert el.acquire_locks(table, [K2], "e-holder", 60, now=NOW) == [K2]
        with pytest.raises(el.ExecutionLockConflict) as raised:
            el.acquire_locks(table, [K1, K2], "e-new", 60, now=NOW)
        assert raised.value.lock_key == K2
        assert raised.value.holder_execution_id == "e-holder"
        assert "Item" not in table.get_item(Key={"lockKey": K1})
        assert table.get_item(Key={"lockKey": K2})["Item"]["workflowExecutionId"] == "e-holder"

    def test_an_expired_lock_is_taken_over_and_re_timed(self):
        table = _lock_table(_resource())
        el.acquire_locks(table, [K1], "e-old", 60, now=NOW)
        assert el.acquire_locks(table, [K1], "e-new", 60, now=NOW + 61) == [K1]
        item = table.get_item(Key={"lockKey": K1})["Item"]
        assert item["workflowExecutionId"] == "e-new"
        assert int(item["expiresAt"]) == NOW + 61 + 60
        assert item["acquiredAt"] == "2027-01-15T08:01:01Z"

    def test_a_lock_expiring_exactly_now_is_still_held(self):
        table = _lock_table(_resource())
        el.acquire_locks(table, [K1], "e-old", 60, now=NOW)
        with pytest.raises(el.ExecutionLockConflict):
            el.acquire_locks(table, [K1], "e-new", 60, now=NOW + 60)

    def test_release_is_conditional_on_the_holder(self):
        table = _lock_table(_resource())
        el.acquire_locks(table, [K1], "e1", 60, now=NOW)
        assert el.release_locks(table, [K1], "e-other") == 0
        assert "Item" in table.get_item(Key={"lockKey": K1})
        assert el.release_locks(table, [K1], "e1") == 1
        assert "Item" not in table.get_item(Key={"lockKey": K1})
        assert el.release_locks(table, [K1], "e1") == 0

    def test_row_derived_release_walks_the_workflow_and_input_tables(self):
        ddb = _resource()
        locks = _lock_table(ddb)
        workflows = _workflow_table(ddb)
        inputs = _inputs_table(ddb)
        workflows.put_item(Item={"databaseId": "GLOBAL", "workflowId": "wf",
                                 "systemConfig": {"concurrencyRestriction": "perInputFileVersion"}})
        for path, version in (("/a1/one.glb", "v1"), ("/a1/two.glb", "v2")):
            inputs.put_item(Item={
                "workflowExecutionId": "E1", "databaseId:assetId:inputAssetFileKey": f"db:a1:{path}",
                "databaseId": "db", "assetId": "a1", "inputAssetFileKey": path, "versionId": version})
        el.acquire_locks(locks, [K1, K2], "E1", 60, now=NOW)
        # A row held by a DIFFERENT execution of the same workflow stays put.
        other = el.build_lock_key("GLOBAL", "wf", "db", "a9", "/a9/x.glb", "v9")
        el.acquire_locks(locks, [other], "E2", 60, now=NOW)

        released = el.release_locks_for_execution(
            ddb, locks_table_name="t-locks", workflow_table_name="t-workflows",
            inputs_table_name="t-inputs", workflow_execution_id="E1",
            workflow_database_id="GLOBAL", workflow_id="wf")

        assert released == 2
        assert locks.scan()["Items"] == [
            {"lockKey": other, "workflowExecutionId": "E2",
             "acquiredAt": "2027-01-15T08:00:00Z", "expiresAt": NOW + 60}]

    def test_row_derived_release_under_another_restriction_touches_no_lock_row(self):
        ddb = _resource()
        locks = _lock_table(ddb)
        workflows = _workflow_table(ddb)
        _inputs_table(ddb)
        workflows.put_item(Item={"databaseId": "GLOBAL", "workflowId": "wf",
                                 "systemConfig": {"concurrencyRestriction": "perInputFile"}})
        el.acquire_locks(locks, [K1], "E1", 60, now=NOW)
        assert el.release_locks_for_execution(
            ddb, locks_table_name="t-locks", workflow_table_name="t-workflows",
            inputs_table_name="t-inputs", workflow_execution_id="E1",
            workflow_database_id="GLOBAL", workflow_id="wf") == 0
        assert "Item" in locks.get_item(Key={"lockKey": K1})
