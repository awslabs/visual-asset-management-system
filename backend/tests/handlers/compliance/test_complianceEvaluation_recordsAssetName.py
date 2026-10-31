# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An evaluation writes the asset's current name onto its compliance record (D65).

Records written before compliance records kept the asset's name carry no `assetName`,
`assetNameSort` or `assetSearchText`, so they are missing from the overview's name sort and match
its text filter by id only. An evaluation — on demand, Evaluate All (`vamscli compliance sweep -d`)
or a schema sweep (`-n`) — now fills them from the asset row: it reads the asset for a record
without a name, and corrects a differing name when it read the asset for its rules anyway. A record
that already carries the current name is not written for it.

Run against DynamoDB doubles built from the CDK table definitions (`_cdkTables.table_doubles`).
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    REAL_TO_UPDATE_EXPR, USER, body_of, claims_for, enforcer, rest_event, schema_row,
)
from common.compliance import listing
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceTrigger as trigger

DB = "db-names"
SCHEMA = "names-schema"
# One rule that reads neither the asset row nor its files: the asset is read for the name only.
LINK_RULE_BODY = {
    "schemaFormat": "vams-rules-v1",
    "rules": {"has-parent": {"ruleType": "relationship", "enforcement": "warn",
                             "checks": [{"name": "parent", "direction": "parents",
                                         "relationshipType": "parentChild", "minCount": 1}]}},
}
# An asset-attributes rule: the evaluation reads the asset row for the rule.
ATTRIBUTE_RULE_BODY = {
    "schemaFormat": "vams-rules-v1",
    "rules": {"naming": {"ruleType": "assetAttributes", "enforcement": "inform",
                         "checks": [{"name": "name", "field": "assetName", "matches": "^[A-Z]"}]}},
}


@pytest.fixture
def tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (store, evaluate_svc, trigger))
        # The root harness mocks `common.dynamodb`; the store unpacks the real helper's result.
        patches.append(patch.object(store, "to_update_expr", REAL_TO_UPDATE_EXPR))
        for active in patches:
            active.start()
        try:
            doubles["complianceSchemaStorage"].put_item(
                Item=schema_row(SCHEMA, body=LINK_RULE_BODY, database_id=DB))
            doubles["complianceSchemaStorage"].put_item(
                Item=schema_row("attribute-schema", body=ATTRIBUTE_RULE_BODY, database_id=DB))
            doubles["databaseStorage"].put_item(
                Item={"databaseId": DB, "complianceSchemaName": SCHEMA})
            yield doubles
        finally:
            for active in reversed(patches):
                active.stop()


def _asset(tables, asset_id, name):
    tables["assetStorage"].put_item(Item={"databaseId": DB, "assetId": asset_id,
                                          "assetName": name, "assetType": "folder"})


def _legacy_record(tables, asset_id, schema=SCHEMA):
    """A compliance record as written before records kept the asset's name."""
    tables["complianceAssetStateStorage"].put_item(Item={
        "databaseId": DB, "assetId": asset_id, "schemaName": schema, "schemaSource": "database",
        "complianceState": "compliant", "registeredAt": "2026-10-01T00:00:00+00:00"})


def _record(tables, asset_id):
    return tables["complianceAssetStateStorage"].get_item(
        Key={"databaseId": DB, "assetId": asset_id}, ConsistentRead=True)["Item"]


def _names(record):
    return {name: record.get(name) for name in ("assetName", "assetNameSort", "assetSearchText")}


@pytest.mark.unit
class TestAnEvaluationRecordsTheAssetName:

    def test_a_record_without_a_name_gains_the_assets_name(self, tables):
        _asset(tables, "a1", "Turbine Blade")
        _legacy_record(tables, "a1")
        store.run_evaluation(DB, "a1", SCHEMA, USER)
        assert _names(_record(tables, "a1")) == listing.asset_name_attributes("a1", "Turbine Blade")

    def test_a_differing_name_is_corrected_when_the_rules_read_the_asset(self, tables):
        _asset(tables, "a2", "New Name")
        tables["complianceAssetStateStorage"].put_item(Item={
            "databaseId": DB, "assetId": "a2", "schemaName": "attribute-schema",
            "schemaSource": "asset", "complianceState": "compliant",
            **listing.asset_name_attributes("a2", "Old Name")})
        store.run_evaluation(DB, "a2", "attribute-schema", USER)
        assert _names(_record(tables, "a2")) == listing.asset_name_attributes("a2", "New Name")

    def test_a_record_with_the_current_name_is_not_written_for_it(self, tables):
        _asset(tables, "a3", "Same Name")
        tables["complianceAssetStateStorage"].put_item(Item={
            "databaseId": DB, "assetId": "a3", "schemaName": SCHEMA, "schemaSource": "database",
            "complianceState": "compliant", **listing.asset_name_attributes("a3", "Same Name")})
        with patch.object(store, "refresh_asset_state_name",
                          wraps=store.refresh_asset_state_name) as refresh, \
                patch.object(store, "get_asset_item", wraps=store.get_asset_item) as read_asset:
            store.run_evaluation(DB, "a3", SCHEMA, USER)
        refresh.assert_not_called()
        # The link rule does not read the asset, and the record already has its name.
        read_asset.assert_not_called()

    def test_the_asset_row_is_read_once_when_the_rules_need_it_too(self, tables):
        _asset(tables, "a4", "Bracket")
        _legacy_record(tables, "a4", schema="attribute-schema")
        with patch.object(store, "get_asset_item", wraps=store.get_asset_item) as read_asset:
            result = store.run_evaluation(DB, "a4", "attribute-schema", USER)
        assert read_asset.call_count == 1
        assert result["verdict"] == "compliant"
        assert _record(tables, "a4")["assetName"] == "Bracket"

    def test_an_asset_without_a_row_is_evaluated_and_left_without_a_name(self, tables):
        _legacy_record(tables, "gone")
        result = store.run_evaluation(DB, "gone", SCHEMA, USER)
        assert result["evaluationId"]
        assert "assetName" not in _record(tables, "gone")

    def test_a_failed_name_write_does_not_fail_the_evaluation(self, tables):
        _asset(tables, "a5", "Turbine Blade")
        _legacy_record(tables, "a5")
        with patch.object(store, "refresh_asset_state_name", side_effect=RuntimeError("boom")):
            result = store.run_evaluation(DB, "a5", SCHEMA, USER)
        assert result["verdict"] in ("compliant", "non_compliant")


def _call(event):
    with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
            patch.object(evaluate_svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(evaluate_svc, "check_and_trigger_cascade", MagicMock()):
        response = evaluate_svc.lambda_handler(event, MagicMock())
    assert response["statusCode"] == 200, response
    return body_of(response)


@pytest.mark.unit
class TestASweepFillsTheNamesOfOlderRecords:

    def test_evaluate_all_of_a_database_names_every_record_it_evaluates(self, tables):
        for asset_id, name in (("b1", "Turbine Blade"), ("b2", "Bracket")):
            _asset(tables, asset_id, name)
            _legacy_record(tables, asset_id)
        _call(rest_event("POST", f"/compliance/evaluate/{DB}", {"databaseId": DB}))
        assert _record(tables, "b1")["assetName"] == "Turbine Blade"
        assert _record(tables, "b2")["assetName"] == "Bracket"
        # Named now, so the name sort lists them.
        overview = _call(rest_event("GET", f"/compliance/state/{DB}", {"databaseId": DB},
                                    query_params={"sort": "name"}))
        assert [row["assetName"] for row in overview["assets"]] == ["Bracket", "Turbine Blade"]

    def test_a_schema_sweep_names_every_record_it_evaluates(self, tables):
        _asset(tables, "c1", "Housing")
        _legacy_record(tables, "c1")
        _call(rest_event("POST", f"/compliance/sweep/{SCHEMA}", {"schemaName": SCHEMA},
                         body=json.dumps({})))
        assert _record(tables, "c1")["assetName"] == "Housing"
