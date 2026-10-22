# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The per-database asset listing pages across the active and archived partitions.

With showArchived the listing reads the database's active partition and then its archived
(``#deleted``) partition. A NextToken is one partition's LastEvaluatedKey, so the next page
resumes in that partition only, and DynamoDB refuses an ExclusiveStartKey whose partition key
differs from the query's. The query double here enforces that rule.
"""

import base64
import json

import pytest
from botocore.exceptions import ClientError
from unittest.mock import patch

from backend.tests.handlers.assets.test_assetService_history import _load

DATABASE_ID = "db1"


class _AssetTableQuery:
    """dynamodb_client.query over an asset table with per-partition ordering and Limit."""

    def __init__(self, partitions):
        # partition key -> asset ids in the order the query returns them
        self.partitions = partitions
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        db_id = kwargs["ExpressionAttributeValues"][":dbId"]["S"]
        start = kwargs.get("ExclusiveStartKey")
        if start is not None and start["databaseId"]["S"] != db_id:
            raise ClientError(
                {"Error": {"Code": "ValidationException",
                           "Message": "The provided starting key is invalid"}}, "Query")
        ids = self.partitions.get(db_id, [])
        offset = ids.index(start["assetId"]["S"]) + 1 if start is not None else 0
        page = ids[offset:offset + kwargs["Limit"]]
        response = {"Items": [{"databaseId": {"S": db_id}, "assetId": {"S": a}} for a in page]}
        if offset + kwargs["Limit"] <= len(ids) and page:
            # DynamoDB returns a LastEvaluatedKey whenever it stops at the Limit.
            response["LastEvaluatedKey"] = {"databaseId": {"S": db_id}, "assetId": {"S": page[-1]}}
        return response


def _list_all(m, query, page_size, show_archived=True):
    """Follow NextToken to the end; return (asset ids, pages)."""
    ids, token, pages = [], None, 0
    with patch.object(m, "dynamodb_client") as client:
        client.query.side_effect = query
        while True:
            params = {"pageSize": page_size, "maxItems": 1000, "startingToken": token}
            result = m.get_assets(DATABASE_ID, params, show_archived)
            ids.extend(item["assetId"] for item in result["Items"])
            pages += 1
            token = result.get("NextToken")
            if not token:
                return ids, pages


@pytest.fixture
def m():
    module = _load()
    module.claims_and_roles = {"tokens": ["u1"]}
    return module


@pytest.mark.unit
class TestShowArchivedPaging:
    PARTITIONS = {DATABASE_ID: ["a1", "a2", "a3"], f"{DATABASE_ID}#deleted": ["d1", "d2"]}

    def test_every_asset_comes_back_once_across_pages(self, m):
        ids, _ = _list_all(m, _AssetTableQuery(self.PARTITIONS), page_size=2)

        assert sorted(ids) == ["a1", "a2", "a3", "d1", "d2"]
        assert len(ids) == 5

    def test_no_page_is_larger_than_the_page_size(self, m):
        query = _AssetTableQuery(self.PARTITIONS)
        _list_all(m, query, page_size=2)

        assert all(call["Limit"] <= 2 for call in query.calls)

    def test_a_start_key_is_only_sent_to_its_own_partition(self, m):
        query = _AssetTableQuery(self.PARTITIONS)
        _list_all(m, query, page_size=2)

        for call in query.calls:
            if "ExclusiveStartKey" in call:
                assert call["ExclusiveStartKey"]["databaseId"]["S"] == \
                    call["ExpressionAttributeValues"][":dbId"]["S"]

    def test_the_archived_partition_fills_the_rest_of_a_page(self, m):
        query = _AssetTableQuery({DATABASE_ID: ["a1"], f"{DATABASE_ID}#deleted": ["d1", "d2", "d3"]})
        with patch.object(m, "dynamodb_client") as client:
            client.query.side_effect = query
            first = m.get_assets(DATABASE_ID, {"pageSize": 3, "maxItems": 1000}, True)

        assert [item["assetId"] for item in first["Items"]] == ["a1", "d1", "d2"]
        assert [call["Limit"] for call in query.calls] == [3, 2]
        token = json.loads(base64.b64decode(first["NextToken"]))
        assert token["databaseId"]["S"] == f"{DATABASE_ID}#deleted"

    @pytest.mark.parametrize("page_size", [1, 3, 5, 10])
    def test_all_assets_come_back_once_for_any_page_size(self, m, page_size):
        ids, _ = _list_all(m, _AssetTableQuery(self.PARTITIONS), page_size=page_size)

        assert sorted(ids) == ["a1", "a2", "a3", "d1", "d2"]

    def test_without_show_archived_only_the_active_partition_is_read(self, m):
        query = _AssetTableQuery(self.PARTITIONS)
        ids, _ = _list_all(m, query, page_size=2, show_archived=False)

        assert sorted(ids) == ["a1", "a2", "a3"]
        assert {call["ExpressionAttributeValues"][":dbId"]["S"] for call in query.calls} == {DATABASE_ID}


@pytest.mark.unit
class TestStartingTokenPartition:
    def _token(self, db_id):
        key = {"databaseId": {"S": db_id}, "assetId": {"S": "x"}}
        return base64.b64encode(json.dumps(key).encode("utf-8")).decode("utf-8")

    @pytest.mark.parametrize("db_id", ["other-db", "other-db#deleted", f"{DATABASE_ID}#deleted"])
    def test_a_token_from_a_partition_not_listed_is_refused(self, m, db_id):
        """The archived partition is listed only with showArchived; another database never."""
        with patch.object(m, "dynamodb_client") as client:
            with pytest.raises(m.VAMSGeneralErrorResponse) as err:
                m.get_assets(DATABASE_ID, {"pageSize": 2, "startingToken": self._token(db_id)}, False)

        assert "Invalid pagination token" in str(err.value)
        client.query.assert_not_called()

    def test_a_token_without_a_partition_key_is_refused(self, m):
        token = base64.b64encode(json.dumps({"assetId": {"S": "x"}}).encode("utf-8")).decode("utf-8")
        with patch.object(m, "dynamodb_client") as client:
            with pytest.raises(m.VAMSGeneralErrorResponse) as err:
                m.get_assets(DATABASE_ID, {"pageSize": 2, "startingToken": token}, True)

        assert "Invalid pagination token" in str(err.value)
        client.query.assert_not_called()

    def test_an_archived_token_skips_the_active_partition(self, m):
        query = _AssetTableQuery({DATABASE_ID: ["a1"], f"{DATABASE_ID}#deleted": ["x", "d2"]})
        with patch.object(m, "dynamodb_client") as client:
            client.query.side_effect = query
            result = m.get_assets(
                DATABASE_ID,
                {"pageSize": 2, "startingToken": self._token(f"{DATABASE_ID}#deleted")}, True)

        assert [item["assetId"] for item in result["Items"]] == ["d2"]
        assert [call["ExpressionAttributeValues"][":dbId"]["S"] for call in query.calls] == [
            f"{DATABASE_ID}#deleted"]
