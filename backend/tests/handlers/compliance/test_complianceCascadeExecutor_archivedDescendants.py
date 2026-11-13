# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A cascade does not evaluate a descendant that is archived when the executor reaches it.

Archiving an asset keeps its links, so a cascade still discovers an archived descendant under its
live database id, and its compliance record still names a schema. The executor used to evaluate it:
the evaluation could not read the asset and recorded an `error` evaluation ("Asset could not be
read") and `lastEvaluationStatus: error` on its record. Now the executor asks the decision the
trigger and the sweeps use (`complianceTrigger.automatic_evaluation_target`) right before each
node, so an asset archived before approval, or while the cascade runs, is recorded
`skipped_archived` and nothing is written to its compliance record or history. A descendant with no
row left has its record removed, as its permanent delete removes it, and is recorded `skipped`.

Run against DynamoDB doubles built from the CDK table definitions (`_cdkTables.table_doubles`),
with the real evaluation.
"""

import json
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import REAL_TO_UPDATE_EXPR, schema_row
from handlers.compliance import complianceCascadeExecutor as executor
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger

DB = "cascade-db"
SCHEMA = "cascade-schema"
PARENT = "parent"
CASCADE_ID = "5b1e2c3d-0000-4000-8000-0000000000c5"
# An asset-attributes rule reads the asset row, so an evaluation of an archived asset errors.
ATTRIBUTE_RULE_BODY = {
    "schemaFormat": "vams-rules-v1",
    "rules": {"naming": {"ruleType": "assetAttributes", "enforcement": "quarantine",
                         "checks": [{"name": "name", "field": "assetName", "matches": "^[A-Z]"}]}},
}


def _record(asset_id):
    name = f"Child {asset_id}"
    return {"databaseId": DB, "assetId": asset_id, "schemaName": SCHEMA, "schemaSource": "database",
            "complianceState": "quarantined", "lastEvaluationId": "eval-0",
            "lastEvaluatedAt": "2026-10-01T00:00:00+00:00", "lastEvaluationStatus": "completed",
            "assetName": name, "assetNameSort": name.lower(),
            "assetSearchText": f"{name.lower()} {asset_id}"}


@pytest.fixture
def tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (store, executor, trigger))
        # The root harness mocks `common.dynamodb`; the store unpacks the real helper's result.
        patches.append(patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR))
        for active in patches:
            active.start()
        try:
            doubles["complianceSchemaStorage"].put_item(
                Item=schema_row(SCHEMA, body=ATTRIBUTE_RULE_BODY, database_id=DB))
            doubles["databaseStorage"].put_item(Item={"databaseId": DB, "complianceSchemaName": SCHEMA})
            yield doubles
        finally:
            for active in reversed(patches):
                active.stop()


def _world(tables, children, archived=(), gone=()):
    """A live parent with `parentChild` links to `children`, in this order. Each child has a
    compliance record; an archived child's row sits only under `<db>#deleted` (the archive keeps
    its links), a gone child has no row at all."""
    for asset_id in (PARENT,) + tuple(children):
        if asset_id in gone:
            continue
        database_id = f"{DB}#deleted" if asset_id in archived else DB
        tables["assetStorage"].put_item(Item={"databaseId": database_id, "assetId": asset_id,
                                              "assetName": f"Child {asset_id}"})
    for index, asset_id in enumerate(children):
        tables["complianceAssetStateStorage"].put_item(Item=_record(asset_id))
        tables["assetLinksStorageV2"].put_item(Item={
            "assetLinkId": f"link-{index}", "relationshipType": "parentChild",
            "fromAssetDatabaseId": DB, "fromAssetId": PARENT,
            "toAssetDatabaseId": DB, "toAssetId": asset_id,
            "fromAssetDatabaseId:fromAssetId": f"{DB}:{PARENT}",
            "toAssetDatabaseId:toAssetId": f"{DB}:{asset_id}"})
    tables["complianceCascadeStorage"].put_item(Item={
        "cascadeId": CASCADE_ID, "state": "executing", "createdAt": "2026-10-28T08:00:00+00:00",
        "triggeredByDatabaseId": DB, "triggeredByAssetId": PARENT,
        "authorizedDatabaseIds": json.dumps([DB])})


def _archive(tables, asset_id):
    """What the asset service's archive does to the row: the archived row first, then the live one
    is deleted."""
    tables["assetStorage"].put_item(Item={"databaseId": f"{DB}#deleted", "assetId": asset_id,
                                          "assetName": f"Child {asset_id}"})
    tables["assetStorage"].delete_item(Key={"databaseId": DB, "assetId": asset_id})


def _evaluations(tables, asset_id):
    return [row for row in tables["complianceEvaluationStorage"].scan(ConsistentRead=True)["Items"]
            if row.get("assetId") == asset_id]


def _audit(tables, asset_id):
    return [row for row in tables["complianceAuditStorage"].scan(ConsistentRead=True)["Items"]
            if row.get("assetId") == asset_id]


def _state(tables, asset_id):
    return tables["complianceAssetStateStorage"].get_item(
        Key={"databaseId": DB, "assetId": asset_id}, ConsistentRead=True).get("Item")


def _nodes(tables):
    row = tables["complianceCascadeStorage"].get_item(Key={"cascadeId": CASCADE_ID},
                                                     ConsistentRead=True)["Item"]
    return row["state"], json.loads(row["nodes"]), json.loads(row.get("results", "[]"))


@pytest.mark.unit
class TestAnArchivedDescendantIsNotEvaluated:

    def test_a_child_archived_before_approval_is_skipped_archived(self, tables):
        _world(tables, ["a", "b"], archived={"b"})
        result = executor.execute_cascade(CASCADE_ID)
        assert result["status"] == "completed"
        # Nothing is written for the archived child: no evaluation, no claim, no audit entry.
        assert _evaluations(tables, "b") == []
        assert _state(tables, "b") == _record("b")
        assert _audit(tables, "b") == []
        state, nodes, results = _nodes(tables)
        assert state == "completed"
        assert nodes == {f"{DB}:a": "compliant", f"{DB}:b": "skipped_archived"}
        assert {entry["node"]: entry["status"] for entry in results} == nodes
        # The live sibling is evaluated as before.
        assert [row["status"] for row in _evaluations(tables, "a")] == ["completed"]

    def test_a_child_archived_while_the_cascade_runs_is_skipped_archived(self, tables):
        """The decision is made for each node just before its evaluation, not when the cascade
        started: `b` is archived while `a` is being evaluated."""
        _world(tables, ["a", "b"])
        real_run = store.run_evaluation

        def run_and_archive_b(database_id, asset_id, schema_name, actor):
            result = real_run(database_id, asset_id, schema_name, actor)
            if asset_id == "a":
                _archive(tables, "b")
            return result

        with patch.object(store, "run_evaluation", side_effect=run_and_archive_b) as run:
            executor.execute_cascade(CASCADE_ID)
        assert [call.args[1] for call in run.call_args_list] == ["a"]
        _, nodes, _ = _nodes(tables)
        assert nodes[f"{DB}:b"] == "skipped_archived"
        assert _evaluations(tables, "b") == []
        assert _state(tables, "b") == _record("b")

    def test_a_child_with_no_row_left_loses_its_record_and_is_skipped(self, tables):
        _world(tables, ["a", "gone"], gone={"gone"})
        executor.execute_cascade(CASCADE_ID)
        _, nodes, _ = _nodes(tables)
        assert nodes[f"{DB}:gone"] == "skipped"
        assert _evaluations(tables, "gone") == []
        assert _state(tables, "gone") is None
        entries = _audit(tables, "gone")
        assert [entry["eventType"] for entry in entries] == ["asset_deleted"]

    def test_live_children_are_all_evaluated(self, tables):
        """The control: the same world with every child live evaluates each, so the skips above
        are the archive's doing."""
        _world(tables, ["a", "b"])
        executor.execute_cascade(CASCADE_ID)
        _, nodes, _ = _nodes(tables)
        assert nodes == {f"{DB}:a": "compliant", f"{DB}:b": "compliant"}
        assert [row["status"] for row in _evaluations(tables, "b")] == ["completed"]
