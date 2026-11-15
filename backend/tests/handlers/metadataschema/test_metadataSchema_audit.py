# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The metadata schema handlers record each action after it succeeded, and nothing on a denial."""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.metadataschema import metadataSchemaService as svc

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}
SCHEMA_ID = "schema-0001"


def _operation(operation):
    return svc.MetadataSchemaOperationResponseModel(success=True, message="ok", metadataSchemaId=SCHEMA_ID,
                                                    operation=operation, timestamp="2026-11-13T00:00:00Z")


@pytest.fixture
def recorder(monkeypatch):
    recorder = MagicMock()
    monkeypatch.setattr(svc, "log_action", recorder)
    monkeypatch.setattr(svc, "claims_and_roles", CLAIMS, raising=False)
    return recorder


CREATE_BODY = {"databaseId": "db1", "metadataSchemaEntityType": "assetMetadata", "schemaName": "Part data",
               "fields": {"fields": [{"metadataFieldKeyName": "partNo", "metadataFieldValueType": "string",
                                      "required": False}]}}


@pytest.mark.unit
class TestMetadataSchemaRecords:
    def test_a_create_records_the_written_schema(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "create_metadata_schema", lambda data, claims: _operation("create"))
        event = {"body": json.dumps(CREATE_BODY)}
        response = svc.handle_post_request(event)
        assert response["statusCode"] == 200, response
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "metadataSchemaCreate")
        assert kwargs["metadataSchemaId"] == SCHEMA_ID and kwargs["database_id"] == "db1"
        assert kwargs["changes"]["schemaName"] == "Part data"

    def test_a_denied_create_records_nothing(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "create_metadata_schema", lambda data, claims: svc.authorization_error())
        assert svc.handle_post_request({"body": json.dumps(CREATE_BODY)})["statusCode"] == 403
        recorder.assert_not_called()

    def test_a_delete_is_recorded(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "delete_metadata_schema", lambda schema_id, claims, database_id: _operation("delete"))
        event = {"pathParameters": {"databaseId": "db1", "metadataSchemaId": SCHEMA_ID},
                 "body": json.dumps({"confirmDelete": True})}
        response = svc.handle_delete_request(event)
        assert response["statusCode"] == 200, response
        recorder.assert_called_once_with(event, "metadataSchemaDelete", metadataSchemaId=SCHEMA_ID,
                                         database_id="db1")

    def test_a_denied_delete_records_nothing(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "delete_metadata_schema",
                            lambda schema_id, claims, database_id: svc.authorization_error())
        event = {"pathParameters": {"databaseId": "db1", "metadataSchemaId": SCHEMA_ID},
                 "body": json.dumps({"confirmDelete": True})}
        assert svc.handle_delete_request(event)["statusCode"] == 403
        recorder.assert_not_called()

    def test_a_list_records_the_filters_and_the_count(self, recorder, monkeypatch):
        monkeypatch.setattr(svc, "get_metadata_schemas_by_database", lambda database_id, params: {"Items": []})
        event = {"pathParameters": {}, "queryStringParameters": {"databaseId": "db1"}}
        assert svc.handle_get_request(event)["statusCode"] == 200
        assert recorder.call_args.args == (event, "metadataSchemaList")
        assert recorder.call_args.kwargs["item_count"] == 0
        assert recorder.call_args.kwargs["request_parameters"]["databaseId"] == "db1"
