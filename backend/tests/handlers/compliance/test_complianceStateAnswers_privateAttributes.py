# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""No compliance API answer returns the asset-state attributes kept for the listings (D163).

A compliance record carries `assetNameSort` (the sort key of the `DatabaseAssetNameIndex`) and
`assetSearchText` (what the listings' `text` filter matches) beside the fields the API reference
documents. The database overview and the quarantine listing dropped them; the asset-state read,
`GET /compliance/state/{databaseId}/{assetId}`, returned both. Every answer that returns state
records now builds them with `listing.state_record_answer`, so:

- none of them carries either attribute, for a live or an archived asset, under every sort and
  filter (the overview and quarantine cases guard what those answers already did);
- every attribute each record carries is a documented property of the `complianceState` schema in
  documentation/VAMS_API.yaml (the quarantine rows add their documented `permissions`);
- the stored record keeps both attributes, so the name sort and the text filter still find it.

The evaluate and quarantine services run on moto tables built from the CDK definitions
(`_cdkTables`), with a stand-in enforcer that allows everything.
"""

import pathlib
from unittest.mock import MagicMock, patch

import boto3
import pytest
import yaml
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import USER, body_of, claims_for, rest_event
from backend.tests.handlers.compliance.test_complianceUnknownDatabase import _stand_in
from common.compliance import listing
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc

OPENAPI = pathlib.Path(__file__).resolve().parents[4] / "documentation" / "VAMS_API.yaml"

DB = "answers-db"
QUARANTINED = "q-asset"
COMPLIANT = "c-asset"
ARCHIVED = "z-archived"
NAMES = {QUARANTINED: "Turbine Blade 7", COMPLIANT: "Housing Cover", ARCHIVED: "Old Bracket"}


def _record(asset_id, compliance_state, **extra):
    """A compliance record as the trigger and the evaluations write it: the documented fields and
    the listing attributes (`listing.asset_name_attributes`)."""
    return {
        "databaseId": DB, "assetId": asset_id, "complianceState": compliance_state,
        "schemaName": "std", "schemaSource": "database", "lastEvaluationId": f"ev-{asset_id}",
        "lastEvaluatedAt": "2026-11-07T01:00:00+00:00", "lastEvaluationStatus": "completed",
        "updatedAt": "2026-11-07T01:00:05+00:00", "registeredAt": "2026-11-01T00:00:00+00:00",
        store.RECORD_REVISION_FIELD: 3,
        **listing.asset_name_attributes(asset_id, NAMES[asset_id]),
        **extra,
    }


class World:

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, self.t = _cdkTables.table_doubles(
            resource, (store, evaluate_svc, quarantine_svc))
        for module in (evaluate_svc, quarantine_svc):
            self.patches += [
                patch.object(module, "CasbinEnforcer", return_value=_stand_in({"*"})),
                patch.object(module, "request_to_claims", claims_for(USER)),
            ]
        for active in self.patches:
            active.start()
        self.t["databaseStorage"].put_item(Item={"databaseId": DB, "complianceSchemaName": "std"})
        for asset_id in (QUARANTINED, COMPLIANT):
            self.t["assetStorage"].put_item(Item={"databaseId": DB, "assetId": asset_id,
                                                  "assetName": NAMES[asset_id]})
        # An archived asset's row lives under `<databaseId>#deleted`; its record stays under DB.
        self.t["assetStorage"].put_item(Item={"databaseId": f"{DB}#deleted", "assetId": ARCHIVED,
                                              "assetName": NAMES[ARCHIVED]})
        state = self.t["complianceAssetStateStorage"]
        state.put_item(Item=_record(
            QUARANTINED, "quarantined", quarantineReason="metadata missing",
            quarantinedAt="2026-11-07T01:00:05+00:00"))
        state.put_item(Item=_record(COMPLIANT, "compliant", exceptionGranted=False))
        state.put_item(Item=_record(ARCHIVED, "compliant"))
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()

    def get(self, service, path, params=None, query=None):
        response = service.lambda_handler(rest_event("GET", path, params, query), MagicMock())
        assert response["statusCode"] == 200, response
        return body_of(response)

    def state(self, asset_id):
        return self.get(evaluate_svc, f"/compliance/state/{DB}/{asset_id}",
                        {"databaseId": DB, "assetId": asset_id})

    def overview(self, **query):
        return self.get(evaluate_svc, f"/compliance/state/{DB}", {"databaseId": DB},
                        query or None)["assets"]

    def quarantine(self, **query):
        return self.get(quarantine_svc, "/compliance/quarantine", None,
                        query or None)["quarantinedAssets"]


def _documented_state_properties():
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return set(spec["components"]["schemas"]["complianceState"]["properties"])


# Spelled out rather than read from `listing`, so the answers are checked against the attributes a
# record carries, not against the list the code keeps of them.
LISTING_ATTRIBUTES = ("assetNameSort", "assetSearchText")
# And the id of the background binding change that last wrote the record (D161).
PRIVATE_ATTRIBUTES = LISTING_ATTRIBUTES + ("bindingApplyId",)


def _private(record):
    return sorted(set(record) & set(PRIVATE_ATTRIBUTES))


@pytest.fixture
def world():
    with World() as active:
        yield active


@pytest.mark.unit
class TestStateAnswersLeaveOutTheListingAttributes:

    @pytest.mark.parametrize("asset_id", [QUARANTINED, COMPLIANT, ARCHIVED])
    def test_the_asset_state_read(self, world, asset_id):
        record = world.state(asset_id)
        assert record["assetId"] == asset_id
        assert record["assetName"] == NAMES[asset_id]
        assert _private(record) == []

    def test_the_state_read_of_an_archived_asset_reports_it_archived(self, world):
        record = world.state(ARCHIVED)
        assert (record["complianceState"], record["recordedComplianceState"]) == (
            "archived", "compliant")
        assert _private(record) == []

    @pytest.mark.parametrize("query", [
        {}, {"sort": "name"}, {"state": "quarantined", "sort": "name"}, {"text": "turbine"},
        {"text": "housing", "sort": "name"}, {"schemaSource": "database"},
    ], ids=["default", "name-sort", "state-and-name-sort", "text", "text-and-name-sort",
            "schema-source"])
    def test_the_database_overview(self, world, query):
        rows = world.overview(**query)
        assert rows
        assert [_private(row) for row in rows] == [[] for _ in rows]

    @pytest.mark.parametrize("query", [{}, {"databaseId": DB}, {"text": "blade"}],
                             ids=["all", "database", "text"])
    def test_the_quarantine_listing(self, world, query):
        (row,) = world.quarantine(**query)
        assert row["assetId"] == QUARANTINED
        assert row["permissions"] == {"release": True, "exception": True}
        assert _private(row) == []


@pytest.mark.unit
class TestStateAnswersCarryOnlyDocumentedAttributes:

    def test_the_code_keeps_the_listing_attributes_private(self):
        assert listing.LISTING_STATE_ATTRIBUTES == LISTING_ATTRIBUTES
        assert listing.PRIVATE_STATE_ATTRIBUTES == PRIVATE_ATTRIBUTES

    def test_the_listing_attributes_are_not_documented(self):
        assert set(PRIVATE_ATTRIBUTES).isdisjoint(_documented_state_properties())

    def test_every_attribute_the_name_attributes_add_besides_the_name_is_private(self):
        """A listing attribute added to `asset_name_attributes` must be added to
        PRIVATE_STATE_ATTRIBUTES too, or the answers return it."""
        written = set(listing.asset_name_attributes("a1", "Name")) - {"assetName"}
        assert written == set(LISTING_ATTRIBUTES) == set(listing.LISTING_STATE_ATTRIBUTES)
        assert written < set(listing.PRIVATE_STATE_ATTRIBUTES)

    @pytest.mark.parametrize("asset_id", [QUARANTINED, COMPLIANT, ARCHIVED])
    def test_the_asset_state_read(self, world, asset_id):
        record = world.state(asset_id)
        assert set(record) - _documented_state_properties() == set()

    def test_the_database_overview(self, world):
        documented = _documented_state_properties()
        for row in world.overview(sort="name"):
            assert set(row) - documented == set(), row["assetId"]

    def test_the_quarantine_listing(self, world):
        (row,) = world.quarantine()
        assert set(row) - _documented_state_properties() == {"permissions"}


@pytest.mark.unit
class TestTheStoredRecordKeepsTheListingAttributes:

    def test_the_record_still_carries_them_and_the_listings_still_use_them(self, world):
        stored = world.t["complianceAssetStateStorage"].get_item(
            Key={"databaseId": DB, "assetId": QUARANTINED})["Item"]
        assert _private(stored) == sorted(LISTING_ATTRIBUTES)
        world.state(QUARANTINED)
        world.overview()
        stored_after = world.t["complianceAssetStateStorage"].get_item(
            Key={"databaseId": DB, "assetId": QUARANTINED})["Item"]
        assert stored_after == stored
        # The name sort reads the name index; the text filter matches the search text.
        assert [row["assetId"] for row in world.overview(sort="name")] == [COMPLIANT, QUARANTINED]
        assert [row["assetId"] for row in world.overview(text="blade")] == [QUARANTINED]
        assert [row["assetId"] for row in world.quarantine(text="turbine")] == [QUARANTINED]
