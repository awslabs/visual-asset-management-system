# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The lock protocol against moto's DynamoDB: the conditional PutItem takes over an expired row and
refuses a live one (returning the holder), the conditional DeleteItem releases only the holder's rows,
and the row-derived terminal release walks the real workflow and input tables."""

import os
import threading

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
            {"lockKey": other, "workflowExecutionId": "E2", "lockScope": "assetFileVersion",
             "acquiredAt": "2027-01-15T08:00:00Z", "expiresAt": NOW + 60}]

    def test_row_derived_release_under_the_none_restriction_touches_no_lock_row(self):
        ddb = _resource()
        locks = _lock_table(ddb)
        workflows = _workflow_table(ddb)
        _inputs_table(ddb)
        workflows.put_item(Item={"databaseId": "GLOBAL", "workflowId": "wf",
                                 "systemConfig": {"concurrencyRestriction": "none"}})
        el.acquire_locks(locks, [K1], "E1", 60, now=NOW)
        assert el.release_locks_for_execution(
            ddb, locks_table_name="t-locks", workflow_table_name="t-workflows",
            inputs_table_name="t-inputs", workflow_execution_id="E1",
            workflow_database_id="GLOBAL", workflow_id="wf") == 0
        assert "Item" in locks.get_item(Key={"lockKey": K1})

    def test_a_late_release_by_an_aborted_run_leaves_the_next_runs_row(self):
        # Run 1 is aborted (its release runs), run 2 takes the same scope, then run 1's end-state step
        # fires late and releases again. The release is conditional on the holder, so run 2 keeps its row.
        ddb = _resource()
        locks = _lock_table(ddb)
        workflows = _workflow_table(ddb)
        inputs = _inputs_table(ddb)
        workflows.put_item(Item={"databaseId": "GLOBAL", "workflowId": "wf",
                                 "systemConfig": {"concurrencyRestriction": "perInputFileVersion"}})
        for execution_id in ("E1", "E2"):
            inputs.put_item(Item={
                "workflowExecutionId": execution_id,
                "databaseId:assetId:inputAssetFileKey": "db:a1:/a1/one.glb",
                "databaseId": "db", "assetId": "a1", "inputAssetFileKey": "/a1/one.glb",
                "versionId": "v1"})
        release = dict(dynamo=ddb, locks_table_name="t-locks", workflow_table_name="t-workflows",
                       inputs_table_name="t-inputs", workflow_database_id="GLOBAL", workflow_id="wf")

        el.acquire_locks(locks, [K1], "E1", 60, now=NOW)
        assert el.release_locks_for_execution(workflow_execution_id="E1", **release) == 1  # abort
        el.acquire_locks(locks, [K1], "E2", 60, now=NOW)
        assert el.release_locks_for_execution(workflow_execution_id="E1", **release) == 0  # late step
        assert locks.get_item(Key={"lockKey": K1})["Item"]["workflowExecutionId"] == "E2"

    def test_a_release_rebuilds_the_key_shape_of_the_workflows_own_restriction(self):
        # One execution, its inputs rows, and a row for EVERY scope of its file: the release under
        # perInputFile deletes exactly the assetFile row and leaves the other two scopes' rows, which a
        # different restriction (or a stale row from an earlier one) would own.
        ddb = _resource()
        locks = _lock_table(ddb)
        workflows = _workflow_table(ddb)
        inputs = _inputs_table(ddb)
        workflows.put_item(Item={"databaseId": "GLOBAL", "workflowId": "wf",
                                 "systemConfig": {"concurrencyRestriction": "perInputFile"}})
        inputs.put_item(Item={"workflowExecutionId": "E1", "databaseId:assetId:inputAssetFileKey":
                              "db:a1:/a1/one.glb", "databaseId": "db", "assetId": "a1",
                              "inputAssetFileKey": "/a1/one.glb", "versionId": "v1"})
        keys = {scope: el.build_lock_key("GLOBAL", "wf", "db", "a1", "/a1/one.glb", "v1", scope=scope)
                for scope in el.LOCK_SCOPE_BY_RESTRICTION.values()}
        el.acquire_locks(locks, list(keys.values()), "E1", 60, now=NOW)
        assert el.release_locks_for_execution(
            ddb, locks_table_name="t-locks", workflow_table_name="t-workflows",
            inputs_table_name="t-inputs", workflow_execution_id="E1",
            workflow_database_id="GLOBAL", workflow_id="wf") == 1
        remaining = {item["lockKey"] for item in locks.scan()["Items"]}
        assert remaining == {keys[el.LOCK_SCOPE_ASSET], keys[el.LOCK_SCOPE_ASSET_FILE_VERSION]}


@pytest.fixture
def item_atomic_moto(monkeypatch):
    """moto checks a write's condition and then applies it as two Python steps with nothing between
    them, so two threads can both pass the check; DynamoDB evaluates a conditional write atomically per
    item. This wraps moto's put and delete in one lock to give the emulator that service guarantee,
    which is the property the lock code is built on. Everything else is moto unchanged."""
    from moto.dynamodb.models import DynamoDBBackend

    guard = threading.Lock()
    for name in ("put_item", "delete_item"):
        original = getattr(DynamoDBBackend, name)

        def _atomic(self, *args, __original=original, **kwargs):
            with guard:
                return __original(self, *args, **kwargs)

        monkeypatch.setattr(DynamoDBBackend, name, _atomic)


def _race(table, launches):
    """Start every (execution id, keys) acquire at once; returns {execution id: keys or conflict}."""
    barrier = threading.Barrier(len(launches))
    results = {}

    def _run(execution_id, keys):
        barrier.wait(timeout=30)
        try:
            results[execution_id] = el.acquire_locks(table, keys, execution_id, 60, now=NOW)
        except el.ExecutionLockConflict as conflict:
            results[execution_id] = conflict

    threads = [threading.Thread(target=_run, args=launch) for launch in launches]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "a racing acquire did not finish"
    return results


def _winners(results):
    return [e for e, outcome in results.items() if isinstance(outcome, list)]


@pytest.mark.integration
@mock_aws
@pytest.mark.usefixtures("item_atomic_moto")
class TestRacingLaunchesOnDynamoDb:
    @pytest.mark.parametrize("scope", sorted(el.LOCK_SCOPE_BY_RESTRICTION.values()))
    def test_twenty_simultaneous_launches_on_one_scope_start_exactly_one(self, scope):
        table = _lock_table(_resource())
        key = el.build_lock_key("GLOBAL", "wf", "db", "a1", "/a1/one.glb", "v1", scope=scope)
        results = _race(table, [(f"E{i:02d}", [key]) for i in range(20)])
        winners = _winners(results)
        assert len(winners) == 1, results
        conflicts = [outcome for outcome in results.values()
                     if isinstance(outcome, el.ExecutionLockConflict)]
        assert len(conflicts) == 19
        assert {c.scope for c in conflicts} == {scope}
        assert table.scan()["Items"] == [
            {"lockKey": key, "workflowExecutionId": winners[0], "lockScope": scope,
             "acquiredAt": "2027-01-15T08:00:00Z", "expiresAt": NOW + 60}]

    def test_overlapping_multi_file_launches_in_opposite_order_always_start_one(self):
        # Ten rounds of X = a,b,c against Y = c,b,a, started together: every round starts exactly one
        # launch, and the table then holds exactly that launch's three rows.
        keys = [el.build_lock_key("GLOBAL", "wf", "db", "a1", f"/a1/{name}.glb", "v1")
                for name in ("a", "b", "c")]
        for round_number in range(10):
            table = _lock_table(_resource(), name=f"t-locks-{round_number}")
            results = _race(table, [("X", keys), ("Y", list(reversed(keys)))])
            winners = _winners(results)
            assert len(winners) == 1, (round_number, results)
            assert sorted(item["lockKey"] for item in table.scan()["Items"]) == sorted(keys)
            assert {item["workflowExecutionId"] for item in table.scan()["Items"]} == set(winners)

    def test_simultaneous_releases_and_relaunches_never_leave_two_holders(self):
        # A held row, its holder releasing, and five launches racing for the freed scope: at most one of
        # the launches takes it, and the row ends with a single holder or none.
        table = _lock_table(_resource())
        el.acquire_locks(table, [K1], "E-held", 60, now=NOW)
        barrier = threading.Barrier(6)
        results = {}

        def _release():
            barrier.wait(timeout=30)
            results["release"] = el.release_locks(table, [K1], "E-held")

        def _launch(execution_id):
            barrier.wait(timeout=30)
            try:
                results[execution_id] = el.acquire_locks(table, [K1], execution_id, 60, now=NOW)
            except el.ExecutionLockConflict as conflict:
                results[execution_id] = conflict

        threads = [threading.Thread(target=_release)] + [
            threading.Thread(target=_launch, args=(f"E{i}",)) for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert results["release"] == 1
        launch_winners = [e for e, outcome in results.items()
                          if e != "release" and isinstance(outcome, list)]
        assert len(launch_winners) <= 1
        rows = table.scan()["Items"]
        assert [row["workflowExecutionId"] for row in rows] == launch_winners
