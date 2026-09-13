# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for common/workflows/executionLocks.py against an in-memory table fake.

The fake evaluates exactly the two condition expressions the module writes — `attribute_not_exists
(lockKey) OR expiresAt < :now` on put and `workflowExecutionId = :e` on delete — and raises the same
ConditionalCheckFailedException shape DynamoDB does, with the wire-format `Item` that
ReturnValuesOnConditionCheckFailure=ALL_OLD returns. test_executionLocks_moto.py runs the same
contract against moto's DynamoDB, so a fake that drifts from the service is caught there."""

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock

from backend.backend.common.workflows import executionLocks as el
from backend.tests.pagingStub import Pager

NOW = 1_800_000_000
PUT_CONDITION = "attribute_not_exists(lockKey) OR expiresAt < :now"
DELETE_CONDITION = "workflowExecutionId = :e"


def _condition_failed(item=None, operation="PutItem"):
    response = {"Error": {"Code": "ConditionalCheckFailedException",
                          "Message": "The conditional request failed"}}
    if item is not None:
        response["Item"] = {k: ({"S": v} if isinstance(v, str) else {"N": str(v)})
                            for k, v in item.items()}
    return ClientError(response, operation)


class _FakeLockTable:
    """put_item / delete_item with the module's two conditions evaluated for real."""

    def __init__(self):
        self.items = {}
        self.puts = []
        self.deletes = []

    def put_item(self, Item, ConditionExpression, ExpressionAttributeValues, **kwargs):
        self.puts.append({"Item": dict(Item), "ConditionExpression": ConditionExpression,
                          "ExpressionAttributeValues": dict(ExpressionAttributeValues), **kwargs})
        assert ConditionExpression == PUT_CONDITION
        existing = self.items.get(Item["lockKey"])
        if existing is not None and not existing["expiresAt"] < ExpressionAttributeValues[":now"]:
            returned = existing if kwargs.get("ReturnValuesOnConditionCheckFailure") == "ALL_OLD" else None
            raise _condition_failed(returned)
        self.items[Item["lockKey"]] = dict(Item)
        return {}

    def delete_item(self, Key, ConditionExpression, ExpressionAttributeValues):
        self.deletes.append({"Key": dict(Key), "ConditionExpression": ConditionExpression,
                             "ExpressionAttributeValues": dict(ExpressionAttributeValues)})
        assert ConditionExpression == DELETE_CONDITION
        existing = self.items.get(Key["lockKey"])
        if existing is None or existing["workflowExecutionId"] != ExpressionAttributeValues[":e"]:
            raise _condition_failed(operation="DeleteItem")
        del self.items[Key["lockKey"]]
        return {}


K1 = el.build_lock_key("GLOBAL", "wf", "db", "a1", "/a1/one.glb", "v1")
K2 = el.build_lock_key("GLOBAL", "wf", "db", "a1", "/a1/two.glb", "v2")


@pytest.mark.unit
class TestLockKey:
    def test_the_key_is_the_documented_composite(self):
        assert K1 == "GLOBAL:wf|db:a1:/a1/one.glb|v1"

    def test_an_empty_version_keeps_its_separator(self):
        # Whole-asset/folder selections and unversioned buckets lock with an empty version segment,
        # which must still be a different key from any real version of the same file.
        assert el.build_lock_key("GLOBAL", "wf", "db", "a1", "/a1/", "") == "GLOBAL:wf|db:a1:/a1/|"
        assert el.build_lock_key("GLOBAL", "wf", "db", "a1", "/a1/", None) == "GLOBAL:wf|db:a1:/a1/|"

    def test_the_conflict_message_is_the_literal_the_cli_maps(self):
        assert el.LOCK_CONFLICT_MESSAGE == (
            "A conflicting execution of this workflow is already running for this file version.")
        assert "already running" in el.LOCK_CONFLICT_MESSAGE
        assert "conflicting execution" in el.LOCK_CONFLICT_MESSAGE

    def test_the_restriction_constant(self):
        assert el.CONCURRENCY_PER_INPUT_FILE_VERSION == "perInputFileVersion"


@pytest.mark.unit
class TestLockTtl:
    def test_no_pipelines_yields_the_floor_plus_margin(self):
        assert el.lock_ttl_seconds([]) == 86400 + 1800

    def test_a_short_task_timeout_is_lifted_to_the_floor(self):
        assert el.lock_ttl_seconds([{"executionConfig": {"taskTimeout": "3600"}}]) == 86400 + 1800

    def test_the_longest_task_timeout_wins_when_it_exceeds_the_floor(self):
        records = [{"executionConfig": {"taskTimeout": "100000"}},
                   {"executionConfig": {"taskTimeout": "604800"}},
                   {"executionConfig": {"taskTimeout": ""}}]
        assert el.lock_ttl_seconds(records) == 604800 + 1800

    def test_a_malformed_or_missing_timeout_counts_as_the_floor(self):
        assert el.lock_ttl_seconds([{"executionConfig": {"taskTimeout": "abc"}}, {}, None]) == 86400 + 1800


@pytest.mark.unit
class TestAcquire:
    def test_acquire_writes_one_row_per_key_with_holder_and_expiry(self):
        table = _FakeLockTable()
        assert el.acquire_locks(table, [K1, K2], "e1", 100, now=NOW) == [K1, K2]
        assert set(table.items) == {K1, K2}
        for key in (K1, K2):
            item = table.items[key]
            assert item["workflowExecutionId"] == "e1"
            assert item["expiresAt"] == NOW + 100
            assert isinstance(item["acquiredAt"], str) and item["acquiredAt"].startswith("2027-")
        for put in table.puts:
            assert put["ReturnValuesOnConditionCheckFailure"] == "ALL_OLD"
            assert put["ExpressionAttributeValues"] == {":now": NOW}

    def test_duplicate_keys_in_one_call_are_taken_once(self):
        table = _FakeLockTable()
        assert el.acquire_locks(table, [K1, K1], "e1", 100, now=NOW) == [K1]
        assert len(table.puts) == 1

    def test_a_held_unexpired_lock_conflicts_and_names_the_holder(self):
        table = _FakeLockTable()
        el.acquire_locks(table, [K1], "e-holder", 100, now=NOW)
        with pytest.raises(el.ExecutionLockConflict) as raised:
            el.acquire_locks(table, [K1], "e-new", 100, now=NOW + 50)
        assert raised.value.lock_key == K1
        assert raised.value.holder_execution_id == "e-holder"
        assert table.items[K1]["workflowExecutionId"] == "e-holder"

    def test_a_conflict_on_the_second_key_releases_the_first(self):
        # Multi-file rollback: a partially locked selection would block the holder's own retry.
        table = _FakeLockTable()
        el.acquire_locks(table, [K2], "e-holder", 100, now=NOW)
        with pytest.raises(el.ExecutionLockConflict) as raised:
            el.acquire_locks(table, [K1, K2], "e-new", 100, now=NOW)
        assert raised.value.lock_key == K2
        assert K1 not in table.items
        assert table.deletes == [{"Key": {"lockKey": K1}, "ConditionExpression": DELETE_CONDITION,
                                  "ExpressionAttributeValues": {":e": "e-new"}}]

    def test_an_expired_lock_is_taken_over(self):
        table = _FakeLockTable()
        el.acquire_locks(table, [K1], "e-old", 100, now=NOW)
        assert el.acquire_locks(table, [K1], "e-new", 100, now=NOW + 101) == [K1]
        assert table.items[K1]["workflowExecutionId"] == "e-new"
        assert table.items[K1]["expiresAt"] == NOW + 101 + 100

    def test_a_lock_expiring_exactly_now_is_still_held(self):
        # The condition is a strict `<`: a row whose expiresAt equals :now has not expired yet.
        table = _FakeLockTable()
        el.acquire_locks(table, [K1], "e-old", 100, now=NOW)
        with pytest.raises(el.ExecutionLockConflict):
            el.acquire_locks(table, [K1], "e-new", 100, now=NOW + 100)

    def test_a_non_condition_error_releases_what_was_taken_and_reraises(self):
        # First put succeeds (K1), second put throttles: K1 must be released and the error surfaced.
        fresh = _FakeLockTable()

        def _throttle_second(Item, ConditionExpression, ExpressionAttributeValues, **kwargs):
            if Item["lockKey"] == K2:
                raise ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                                             "Message": "slow down"}}, "PutItem")
            return _FakeLockTable.put_item(fresh, Item, ConditionExpression,
                                           ExpressionAttributeValues, **kwargs)

        fresh.put_item = _throttle_second
        with pytest.raises(ClientError) as raised:
            el.acquire_locks(fresh, [K1, K2], "e1", 100, now=NOW)
        assert raised.value.response["Error"]["Code"] == "ProvisionedThroughputExceededException"
        assert K1 not in fresh.items

    def test_the_holder_is_read_from_a_wire_format_item(self):
        error = _condition_failed({"lockKey": K1, "workflowExecutionId": "e-holder", "expiresAt": 5})
        assert el._holder_from_conflict(error) == "e-holder"

    def test_a_conflict_without_a_returned_item_has_an_empty_holder(self):
        assert el._holder_from_conflict(_condition_failed()) == ""


@pytest.mark.unit
class TestRelease:
    def test_release_deletes_only_rows_this_execution_holds(self):
        table = _FakeLockTable()
        el.acquire_locks(table, [K1], "e1", 100, now=NOW)
        el.acquire_locks(table, [K2], "e2", 100, now=NOW)
        assert el.release_locks(table, [K1, K2], "e1") == 1
        assert set(table.items) == {K2}

    def test_release_of_a_missing_row_is_a_no_op(self):
        table = _FakeLockTable()
        assert el.release_locks(table, [K1], "e1") == 0
        assert el.release_locks(table, [], "e1") == 0
        assert el.release_locks(table, None, "e1") == 0

    def test_an_unexpected_delete_error_is_logged_and_the_rest_still_released(self):
        table = _FakeLockTable()
        el.acquire_locks(table, [K1, K2], "e1", 100, now=NOW)
        real_delete = table.delete_item

        def _fail_first(Key, ConditionExpression, ExpressionAttributeValues):
            if Key["lockKey"] == K1:
                raise ClientError({"Error": {"Code": "InternalServerError", "Message": "boom"}},
                                  "DeleteItem")
            return real_delete(Key, ConditionExpression, ExpressionAttributeValues)

        table.delete_item = _fail_first
        assert el.release_locks(table, [K1, K2], "e1") == 1
        assert set(table.items) == {K1}


def _workflow_record(restriction="perInputFileVersion"):
    return {"databaseId": "GLOBAL", "workflowId": "wf",
            "systemConfig": {"concurrencyRestriction": restriction}}


def _input_row(file_key, version_id, database_id="db", asset_id="a1"):
    return {"workflowExecutionId": "E1", "databaseId": database_id, "assetId": asset_id,
            "inputAssetFileKey": file_key, "versionId": version_id}


@pytest.mark.unit
class TestRowDerivedKeys:
    def test_keys_are_rebuilt_from_the_input_rows_in_row_order_without_duplicates(self):
        rows = [_input_row("/a1/one.glb", "v1"), _input_row("/a1/two.glb", "v2"),
                _input_row("/a1/one.glb", "v1")]
        assert el.lock_keys_for_execution(_workflow_record(), rows) == [K1, K2]

    def test_other_restrictions_yield_no_keys(self):
        rows = [_input_row("/a1/one.glb", "v1")]
        for restriction in ("none", "perAsset", "perInputFile", None):
            assert el.lock_keys_for_execution(_workflow_record(restriction), rows) == []
        assert el.lock_keys_for_execution({}, rows) == []
        assert el.lock_keys_for_execution(None, rows) == []


@pytest.mark.unit
class TestReleaseForExecution:
    """The terminal-side release: restriction from the workflow row, keys from the input rows."""

    def _dynamo(self, workflow_record, inputs_pager, locks_table):
        workflow_table = MagicMock()
        workflow_table.get_item.return_value = {"Item": workflow_record} if workflow_record else {}
        inputs_table = MagicMock()
        inputs_table.query.side_effect = inputs_pager
        dynamo = MagicMock()
        dynamo.Table.side_effect = lambda name: {
            "t-workflows": workflow_table, "t-inputs": inputs_table, "t-locks": locks_table}[name]
        return dynamo, workflow_table, inputs_table

    def _release(self, dynamo):
        return el.release_locks_for_execution(
            dynamo, locks_table_name="t-locks", workflow_table_name="t-workflows",
            inputs_table_name="t-inputs", workflow_execution_id="E1",
            workflow_database_id="GLOBAL", workflow_id="wf")

    def test_releases_every_key_the_rows_describe_across_pages(self):
        locks = _FakeLockTable()
        el.acquire_locks(locks, [K1, K2], "E1", 100, now=NOW)
        pager = Pager(
            {"Items": [_input_row("/a1/one.glb", "v1")], "LastEvaluatedKey": {"k": "p2"}},
            {"Items": [_input_row("/a1/two.glb", "v2")]},
            name="inputs by execution")
        dynamo, workflow_table, inputs_table = self._dynamo(_workflow_record(), pager, locks)
        assert self._release(dynamo) == 2
        assert locks.items == {}
        pager.assert_paged_to_exhaustion()
        workflow_table.get_item.assert_called_once_with(Key={"databaseId": "GLOBAL", "workflowId": "wf"})
        inputs_table.query.assert_called()

    def test_another_restriction_reads_no_input_rows_and_deletes_nothing(self):
        locks = _FakeLockTable()
        pager = Pager({"Items": [_input_row("/a1/one.glb", "v1")]}, name="inputs by execution")
        dynamo, _workflow_table, inputs_table = self._dynamo(_workflow_record("perInputFile"), pager, locks)
        assert self._release(dynamo) == 0
        inputs_table.query.assert_not_called()
        assert locks.deletes == []

    def test_a_missing_workflow_row_releases_nothing(self):
        locks = _FakeLockTable()
        dynamo, _w, inputs_table = self._dynamo(None, Pager({"Items": []}), locks)
        assert self._release(dynamo) == 0
        inputs_table.query.assert_not_called()

    def test_a_failing_read_is_swallowed_and_reported_as_zero(self):
        # Best-effort by contract: the terminal status write that precedes this must never fail on
        # the lock table. An unreleased row expires through the table's TTL.
        locks = _FakeLockTable()
        dynamo, workflow_table, _inputs = self._dynamo(_workflow_record(), Pager({"Items": []}), locks)
        workflow_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}}, "GetItem")
        assert self._release(dynamo) == 0

    def test_an_empty_execution_id_is_a_no_op(self):
        dynamo = MagicMock()
        assert el.release_locks_for_execution(
            dynamo, locks_table_name="t-locks", workflow_table_name="t-workflows",
            inputs_table_name="t-inputs", workflow_execution_id="",
            workflow_database_id="GLOBAL", workflow_id="wf") == 0
        dynamo.Table.assert_not_called()
