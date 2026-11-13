# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A sweep removes, rather than evaluates, the compliance record of an asset that no longer
exists (D70), and skips an archived asset.

A sweep walks compliance records, not assets. A record left behind by a permanent delete (a
failed record removal, or a delete the trigger never saw) used to get a fresh evaluation — a
`non_compliant` verdict for an asset that is gone — and stayed counted in the overview. Now the
database evaluation and the schema sweep check the asset first: a record whose asset has no row
in the database or its archive is removed the way the asset's permanent deletion removes it (the
record with its state and quarantine, one `asset_deleted` audit entry) and counted under
`removedOrphans`. An archived asset keeps its record and is not evaluated (its row cannot be
read, so an evaluation could only record an error): it is counted in `skipped` and
`skippedArchived`, as the automatic trigger skips it too.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from boto3.dynamodb.conditions import Key
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    SYNC_RULES_SCHEMA_BODY, USER, body_of, claims_for, enforcer, rest_event, schema_row,
)
from backend.tests.handlers.compliance.test_complianceSweep_continuation import Evaluations
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger

SCHEMA = "orphan-schema"
DB = "orphan-db"
LIVE = ("live-1", "live-2")
ARCHIVED = "archived-1"
GONE = ("gone-1", "gone-quarantined")


@pytest.fixture
def tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (store, evaluate_svc, trigger))
        for active in patches:
            active.start()
        try:
            doubles["complianceSchemaStorage"].put_item(
                Item=schema_row(SCHEMA, body=SYNC_RULES_SCHEMA_BODY))
            doubles["databaseStorage"].put_item(Item={"databaseId": DB})
            for asset_id in LIVE:
                doubles["assetStorage"].put_item(Item={"databaseId": DB, "assetId": asset_id})
            doubles["assetStorage"].put_item(
                Item={"databaseId": f"{DB}#deleted", "assetId": ARCHIVED})
            for asset_id in LIVE + (ARCHIVED,) + GONE:
                doubles["complianceAssetStateStorage"].put_item(Item={
                    "databaseId": DB, "assetId": asset_id, "schemaName": SCHEMA,
                    "schemaSource": "database",
                    "complianceState": ("quarantined" if asset_id == "gone-quarantined"
                                        else "compliant"),
                    "lastEvaluatedAt": "2026-10-01T00:00:00+00:00"})
            yield doubles
        finally:
            for active in reversed(patches):
                active.stop()


def _call(evaluations, event):
    with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
            patch.object(evaluate_svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()), \
            patch.object(store, "run_evaluation", evaluations), \
            patch.object(store, "register_unrecorded_assets", return_value=0):
        response = evaluate_svc.lambda_handler(event, MagicMock())
    assert response["statusCode"] == 200, response
    return body_of(response)


def _records(tables):
    return {row["assetId"] for row in tables["complianceAssetStateStorage"].query(
        KeyConditionExpression=Key("databaseId").eq(DB), ConsistentRead=True)["Items"]}


def _details(entry):
    details = entry.get("details")
    return json.loads(details) if isinstance(details, str) else details


def _asset_deleted_entries(tables):
    return [item for item in tables["complianceAuditStorage"].scan()["Items"]
            if item.get("eventType") == "asset_deleted"]


@pytest.mark.unit
class TestASweepRemovesTheRecordsOfDeletedAssets:

    @pytest.mark.parametrize("event", [
        rest_event("POST", f"/compliance/evaluate/{DB}", {"databaseId": DB}, body={}),
        rest_event("POST", f"/compliance/sweep/{SCHEMA}", {"schemaName": SCHEMA}, body={}),
    ], ids=["database", "schema"])
    def test_the_records_of_deleted_assets_are_removed_not_evaluated(self, tables, event):
        evaluations = Evaluations(tables)
        body = _call(evaluations, event)
        evaluated = {asset_id for _, asset_id in evaluations.calls}
        assert evaluated == set(LIVE)
        assert {entry["assetId"] for entry in body["assetsTriggered"]} == evaluated
        assert body["removedOrphans"] == 2
        assert body["skippedArchived"] == 1
        assert body["skipped"] == 1
        assert body["assetsRemaining"] == 0
        # The archived asset's record is left exactly as it was: no evaluation claimed it.
        archived_row = tables["complianceAssetStateStorage"].get_item(
            Key={"databaseId": DB, "assetId": ARCHIVED}, ConsistentRead=True)["Item"]
        assert archived_row["lastEvaluatedAt"] == "2026-10-01T00:00:00+00:00"
        assert archived_row["complianceState"] == "compliant"
        assert "lastEvaluationStatus" not in archived_row
        # The records of the deleted assets are gone (the quarantined one's quarantine with it);
        # the archived asset keeps its record.
        assert _records(tables) == set(LIVE) | {ARCHIVED}
        entries = _asset_deleted_entries(tables)
        assert sorted(entry["assetId"] for entry in entries) == sorted(GONE)
        assert all(_details(entry)["detectedBy"] == "sweep" for entry in entries)
        quarantined = next(e for e in entries if e["assetId"] == "gone-quarantined")
        assert quarantined["previousState"] == "quarantined"

    def test_the_overview_no_longer_counts_them(self, tables):
        _call(Evaluations(tables),
              rest_event("POST", f"/compliance/evaluate/{DB}", {"databaseId": DB}, body={}))
        overview = _call(Evaluations(tables),
                         rest_event("GET", f"/compliance/state/{DB}", {"databaseId": DB}))
        # The two live assets. The archived asset keeps its record but is left out of the
        # overview, as the asset listing leaves it out.
        assert overview["totalAssets"] == 2
        assert overview["summary"]["quarantined"] == 0
        assert ARCHIVED in _records(tables)

    def test_a_second_sweep_finds_nothing_to_remove(self, tables):
        event = rest_event("POST", f"/compliance/evaluate/{DB}", {"databaseId": DB}, body={})
        _call(Evaluations(tables), event)
        again = _call(Evaluations(tables), event)
        assert again["removedOrphans"] == 0
        assert len(_asset_deleted_entries(tables)) == 2
