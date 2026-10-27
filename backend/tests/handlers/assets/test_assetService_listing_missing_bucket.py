# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset whose bucket record cannot be read is listed with a null bucketName.

The asset listings resolve each distinct bucketId on the page once. A bucketId with no bucket
record (or one missing its name or prefix) makes get_default_bucket_details raise; the page is
still returned, with that asset's bucketName set to None.
"""

import json

import pytest
from unittest.mock import MagicMock, patch

from backend.tests.handlers.assets.test_assetService_history import _load

BUCKETS = {"bucket-a": {"bucketId": "bucket-a", "bucketName": "vams-assets-a", "baseAssetsPrefix": "a/"}}


def _items():
    return [
        {"databaseId": "db1", "assetId": "good", "assetName": "Good", "description": "d",
         "isDistributable": True, "tags": [], "bucketId": "bucket-a",
         "assetLocation": {"Key": "good/"}},
        {"databaseId": "db1", "assetId": "stale", "assetName": "Stale", "description": "d",
         "isDistributable": True, "tags": [], "bucketId": "bucket-gone",
         "assetLocation": {"Key": "stale/"}},
        {"databaseId": "db1", "assetId": "stale-2", "assetName": "Stale 2", "description": "d",
         "isDistributable": True, "tags": [], "bucketId": "bucket-gone",
         "assetLocation": {"Key": "stale-2/"}},
    ]


def _bucket_details(m):
    def lookup(bucket_id):
        if bucket_id not in BUCKETS:
            raise m.VAMSGeneralErrorResponse("Error getting bucket details.")
        return dict(BUCKETS[bucket_id])
    return MagicMock(side_effect=lookup)


@pytest.mark.unit
class TestMissingBucketRecord:
    def test_the_page_keeps_the_asset_with_a_null_bucket_name(self):
        m = _load()
        lookup = _bucket_details(m)
        with patch.object(m, "enhance_assets_with_version_info", side_effect=lambda items: [dict(i) for i in items]), \
                patch.object(m, "get_default_bucket_details", lookup):
            enriched = m.enrich_asset_listing_page(_items())

        assert [(i["assetId"], i["bucketName"]) for i in enriched] == [
            ("good", "vams-assets-a"), ("stale", None), ("stale-2", None)]
        # The failed lookup is memoized like a successful one: one call per distinct bucketId.
        assert sorted(call.args[0] for call in lookup.call_args_list) == ["bucket-a", "bucket-gone"]

    @pytest.mark.parametrize("path_parameters", [{"databaseId": "db1"}, {}], ids=["database", "all"])
    def test_both_listings_return_200_with_every_asset(self, path_parameters):
        m = _load()
        m.claims_and_roles = {"tokens": ["u1"]}
        page = {"Items": _items()}
        event = {
            "requestContext": {"http": {"method": "GET", "path": "/assets"}},
            "pathParameters": path_parameters,
            "queryStringParameters": {"maxItems": "100", "pageSize": "100"},
        }
        with patch.object(m, "get_assets", return_value=page), \
                patch.object(m, "get_all_assets", return_value=page), \
                patch.object(m, "enhance_assets_with_version_info", side_effect=lambda items: [dict(i) for i in items]), \
                patch.object(m, "get_default_bucket_details", _bucket_details(m)):
            response = m.handle_get_request(event)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert {item["assetId"]: item["bucketName"] for item in body["Items"]} == {
            "good": "vams-assets-a", "stale": None, "stale-2": None}

    def test_an_unexpected_error_still_propagates(self):
        """Only the bucket-details error is absorbed; any other failure is not hidden."""
        m = _load()
        with patch.object(m, "enhance_assets_with_version_info", side_effect=lambda items: [dict(i) for i in items]), \
                patch.object(m, "get_default_bucket_details", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                m.enrich_asset_listing_page(_items())
