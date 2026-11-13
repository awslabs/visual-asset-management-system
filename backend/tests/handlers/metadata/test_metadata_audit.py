# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The metadata handlers record one record per target entity for each write that changed something.

A write records each entry it wrote (key, value, value type) or each key it deleted, with the number of
entries that succeeded. A read records the request and the number of entries returned, never the
entries. A write in which no entry succeeded, a denial and a validation failure record nothing.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.metadata import metadataService as svc

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}
PATH = {"databaseId": "db1", "assetId": "asset-01"}


def _bulk(successful, total=None):
    return svc.BulkOperationResponseModel(
        success=bool(successful), totalItems=total or len(successful), successCount=len(successful),
        failureCount=(total or len(successful)) - len(successful), successfulItems=successful,
        failedItems=[], message="done", timestamp="2026-11-13T00:00:00Z")


@pytest.fixture
def recorder(monkeypatch):
    recorder = MagicMock()
    monkeypatch.setattr(svc, "log_action", recorder)
    monkeypatch.setattr(svc, "claims_and_roles", CLAIMS, raising=False)
    return recorder


def _event(body):
    return {"pathParameters": dict(PATH), "body": json.dumps(body), "queryStringParameters": {}}


@pytest.mark.unit
class TestMetadataWriteRecords:
    def test_a_create_records_the_entries_written(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "create_asset_metadata", lambda *args: _bulk(["partNo"], total=2))
        event = _event({"metadata": [
            {"metadataKey": "partNo", "metadataValue": "PN-100", "metadataValueType": "string"},
            {"metadataKey": "weight", "metadataValue": "12", "metadataValueType": "number"}]})
        assert svc.handle_asset_metadata_post(event)["statusCode"] == 200
        recorder.assert_called_once()
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "metadataCreate")
        assert kwargs["database_id"] == "db1" and kwargs["asset_id"] == "asset-01"
        assert kwargs["changes"] == {"partNo": {"value": "PN-100", "valueType": "string"}}
        assert kwargs["item_count"] == 1

    def test_a_write_in_which_nothing_succeeded_records_nothing(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "create_asset_metadata", lambda *args: _bulk([], total=1))
        svc.handle_asset_metadata_post(_event({"metadata": [
            {"metadataKey": "partNo", "metadataValue": "PN-100", "metadataValueType": "string"}]}))
        recorder.assert_not_called()

    def test_a_denied_write_records_nothing(self, recorder, monkeypatch):
        def refuse(*args):
            raise PermissionError("Not authorized")
        monkeypatch.setattr(svc, "create_asset_metadata", refuse)
        response = svc.handle_asset_metadata_post(_event({"metadata": [
            {"metadataKey": "partNo", "metadataValue": "PN-100", "metadataValueType": "string"}]}))
        assert response["statusCode"] == 403
        recorder.assert_not_called()

    def test_a_delete_records_the_keys_deleted(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "delete_asset_metadata", lambda *args: _bulk(["partNo", "weight"]))
        event = _event({"metadataKeys": ["partNo", "weight"]})
        assert svc.handle_asset_metadata_delete(event)["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "metadataDelete")
        assert kwargs["changes"] is None and kwargs["details"] == {"deletedKeys": ["partNo", "weight"]}
        assert kwargs["item_count"] == 2

    def test_an_update_records_the_update_type(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "update_database_metadata", lambda *args: _bulk(["region"]))
        event = {"pathParameters": {"databaseId": "db1"}, "queryStringParameters": {},
                 "body": json.dumps({"updateType": "replace_all", "metadata": [
                     {"metadataKey": "region", "metadataValue": "EU", "metadataValueType": "string"}]})}
        assert svc.handle_database_metadata_put(event)["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "metadataUpdate")
        assert kwargs["database_id"] == "db1" and kwargs["details"] == {"updateType": "replace_all"}
        assert kwargs["changes"] == {"region": {"value": "EU", "valueType": "string"}}


@pytest.mark.unit
class TestMetadataReadRecords:
    def test_a_read_records_the_request_and_the_count(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "get_database_metadata", lambda *args: svc.GetDatabaseMetadataResponseModel(
            metadata=[], restrictMetadataOutsideSchemas=False))
        event = {"pathParameters": {"databaseId": "db1"}, "queryStringParameters": {}}
        assert svc.handle_database_metadata_get(event)["statusCode"] == 200
        assert recorder.call_args.args == (event, "metadataGet")
        assert recorder.call_args.kwargs["item_count"] == 0 and recorder.call_args.kwargs["database_id"] == "db1"

    def test_the_count_helper_counts_and_never_returns_the_entries(self):
        assert svc._returned_count(svc.GetDatabaseMetadataResponseModel(
            metadata=[], restrictMetadataOutsideSchemas=False)) == 0
        assert svc._returned_count({"Items": [1, 2]}) == 2
        assert svc._returned_count(object()) is None
