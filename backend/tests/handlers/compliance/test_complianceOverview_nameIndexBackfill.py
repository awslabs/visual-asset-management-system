# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The database overview's name sort while DynamoDB backfills `DatabaseAssetNameIndex` (D62).

The CloudFormation update that adds the index to an existing asset-state table completes before
DynamoDB has backfilled it, and until it is ACTIVE every read of it is refused with
`ValidationException: Cannot read from backfilling global secondary index`. The overview then
answers `sort=name` in the default (assetId) order with `nameSortUnavailable: true`, and its
`NextToken` continues in that order; every other error, and every other path, is unchanged.
"""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from backend.tests.handlers.compliance._harness import (
    COMPLIANCE_ASSET_STATE_TABLE_DEFINITION, USER, body_of, claims_for, enforcer, rest_event,
)
from common.compliance import listing
from handlers.compliance import complianceEvaluateService as evaluate_svc

DB = "db-backfill"
BACKFILLING = ("Cannot read from backfilling global secondary index: "
               + listing.DATABASE_ASSET_NAME_INDEX)


class IndexRefusingTable:
    """A table whose reads of the name index fail with `message`; everything else is delegated."""

    def __init__(self, table, message=BACKFILLING, code="ValidationException"):
        self._table = table
        self._error = {"Error": {"Code": code, "Message": message}}
        self.name_index_reads = 0

    def query(self, **kwargs):
        if kwargs.get("IndexName") == listing.DATABASE_ASSET_NAME_INDEX:
            self.name_index_reads += 1
            raise ClientError(self._error, "Query")
        return self._table.query(**kwargs)

    def __getattr__(self, name):
        return getattr(self._table, name)


@pytest.fixture
def state_table():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        table = resource.create_table(**COMPLIANCE_ASSET_STATE_TABLE_DEFINITION)
        # Names in the reverse order of the ids, so the two orders differ.
        for index in range(5):
            asset_id = f"asset-{index}"
            table.put_item(Item={"databaseId": DB, "assetId": asset_id,
                                 "complianceState": "compliant" if index % 2 else "quarantined",
                                 **listing.asset_name_attributes(asset_id, f"Name {9 - index}")})
        yield table


def _overview(table, **params):
    with patch.object(evaluate_svc, "request_to_claims", claims_for(USER)), \
            patch.object(evaluate_svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(evaluate_svc, "asset_state_table", table):
        return evaluate_svc.lambda_handler(
            rest_event("GET", f"/compliance/state/{DB}", {"databaseId": DB},
                       query_params=params or None), MagicMock())


def _ids(response):
    return [row["assetId"] for row in body_of(response)["assets"]]


@pytest.mark.unit
class TestNameSortWhileTheIndexBackfills:

    def test_the_page_is_answered_in_asset_id_order_and_flagged(self, state_table):
        table = IndexRefusingTable(state_table)
        response = _overview(table, sort="name")
        assert response["statusCode"] == 200, response
        body = body_of(response)
        assert body["nameSortUnavailable"] is True
        assert _ids(response) == [f"asset-{index}" for index in range(5)]
        assert body["totalAssets"] == 5
        assert table.name_index_reads == 1

    def test_the_token_of_a_fallback_page_continues_in_that_order(self, state_table):
        table = IndexRefusingTable(state_table)
        first = _overview(table, sort="name", maxItems="2")
        token = body_of(first)["NextToken"]
        second = _overview(table, sort="name", maxItems="2", startingToken=token)
        assert second["statusCode"] == 200, second
        assert body_of(second)["nameSortUnavailable"] is True
        assert _ids(first) + _ids(second) == [f"asset-{index}" for index in range(4)]
        # The continuation reads the table, not the index it knows is unavailable.
        assert table.name_index_reads == 1

    def test_the_text_filter_applies_to_the_fallback_page(self, state_table):
        response = _overview(IndexRefusingTable(state_table), sort="name", text="name 7")
        assert response["statusCode"] == 200, response
        assert _ids(response) == ["asset-2"]
        assert body_of(response)["nameSortUnavailable"] is True

    def test_once_the_index_is_active_the_page_is_in_name_order_without_the_flag(self, state_table):
        response = _overview(state_table, sort="name")
        assert response["statusCode"] == 200, response
        assert "nameSortUnavailable" not in body_of(response)
        assert _ids(response) == [f"asset-{index}" for index in range(4, -1, -1)]

    def test_a_state_filter_with_the_name_sort_does_not_read_the_index(self, state_table):
        table = IndexRefusingTable(state_table)
        response = _overview(table, sort="name", state="quarantined")
        assert response["statusCode"] == 200, response
        assert "nameSortUnavailable" not in body_of(response)
        assert table.name_index_reads == 0
        assert _ids(response) == ["asset-4", "asset-2", "asset-0"]

    def test_another_validation_error_is_not_taken_for_a_backfill(self, state_table):
        table = IndexRefusingTable(state_table, message="Some other problem")
        assert _overview(table, sort="name")["statusCode"] == 500

    def test_another_error_code_is_not_taken_for_a_backfill(self, state_table):
        table = IndexRefusingTable(state_table, code="ProvisionedThroughputExceededException")
        assert _overview(table, sort="name")["statusCode"] == 500
