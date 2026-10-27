# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset GET keeps its response shape for a record the response model refuses.

A stored asset record can predate a required response field (description, isDistributable), and
its current version is a CurrentVersionModel. Both the single GET and the listings return such a
record at the top level, with the missing fields defaulted and the version as a plain dictionary,
never nested under 'message' and never as a 500.
"""

import json

import pytest
from unittest.mock import patch

from backend.tests.handlers.assets.test_assetService_history import _load


def _record(**overrides):
    record = {
        "databaseId": "db1",
        "assetId": "asset-1",
        "assetName": "Legacy asset",
        "bucketId": "bucket-a",
        "assetLocation": {"Key": "asset-1/"},
        "tags": [],
        "object__type": "asset",
    }
    record.update(overrides)
    return record


def _current_version(m):
    return m.CurrentVersionModel(Version="2", DateModified="2026-01-01T00:00:00Z", Comment="c")


def _single_get(m, record):
    event = {
        "requestContext": {"http": {"method": "GET", "path": "/database/db1/assets/asset-1"}},
        "pathParameters": {"databaseId": "db1", "assetId": "asset-1"},
        "queryStringParameters": None,
    }
    m.claims_and_roles = {"tokens": ["u1"]}
    enhanced = dict(record, currentVersion=_current_version(m))
    with patch.object(m, "get_asset_details", return_value=dict(record)), \
            patch.object(m, "authorize_single_asset", return_value=True), \
            patch.object(m, "enhance_asset_with_version_info", return_value=enhanced), \
            patch.object(m, "get_asset_bucket_details", return_value={"bucketName": "vams-assets-a"}):
        response = m.handle_get_request(event)
    return response, json.loads(response["body"])


def _list(m, record, path_parameters):
    event = {
        "requestContext": {"http": {"method": "GET", "path": "/assets"}},
        "pathParameters": path_parameters,
        "queryStringParameters": {"maxItems": "10", "pageSize": "10"},
    }
    m.claims_and_roles = {"tokens": ["u1"]}
    enhanced = dict(record, currentVersion=_current_version(m), bucketName="vams-assets-a")
    page = {"Items": [dict(record)]}
    with patch.object(m, "get_assets", return_value=page), \
            patch.object(m, "get_all_assets", return_value=page), \
            patch.object(m, "enrich_asset_listing_page", return_value=[enhanced]):
        response = m.handle_get_request(event)
    return response, json.loads(response["body"])


@pytest.fixture
def m():
    return _load()


@pytest.mark.unit
class TestSingleAssetGet:
    def test_a_record_without_a_description_is_returned_at_the_top_level(self, m):
        response, body = _single_get(m, _record())

        assert response["statusCode"] == 200
        assert "message" not in body
        assert body["assetId"] == "asset-1"
        assert body["description"] == ""
        assert body["isDistributable"] is False
        assert body["currentVersion"]["Version"] == "2"
        assert "object__type" not in body

    def test_a_record_the_model_still_refuses_is_returned_at_the_top_level_not_a_500(self, m):
        response, body = _single_get(m, _record(assetName=None))

        assert response["statusCode"] == 200
        assert "message" not in body
        assert body["assetId"] == "asset-1"
        assert body["currentVersion"]["Version"] == "2"
        assert "object__type" not in body

    def test_a_valid_record_is_returned_as_the_model_shapes_it(self, m):
        """POSITIVE CONTROL: a complete record is unchanged by the fallback."""
        response, body = _single_get(m, _record(description="d", isDistributable=True))

        assert response["statusCode"] == 200
        assert body["description"] == "d"
        assert body["isDistributable"] is True
        assert body["status"] == "active"


@pytest.mark.unit
@pytest.mark.parametrize("path_parameters", [{"databaseId": "db1"}, {}], ids=["database", "all"])
class TestAssetListings:
    def test_a_record_without_a_description_is_listed_with_defaults(self, m, path_parameters):
        response, body = _list(m, _record(), path_parameters)

        assert response["statusCode"] == 200
        item = body["Items"][0]
        assert item["description"] == ""
        assert item["isDistributable"] is False
        assert item["currentVersion"]["Version"] == "2"

    def test_a_record_the_model_still_refuses_is_listed_not_a_500(self, m, path_parameters):
        response, body = _list(m, _record(assetName=None), path_parameters)

        assert response["statusCode"] == 200
        item = body["Items"][0]
        assert item["assetId"] == "asset-1"
        assert item["currentVersion"]["Version"] == "2"
        assert "object__type" not in item

