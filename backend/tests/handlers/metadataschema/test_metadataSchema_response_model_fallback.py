# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The single metadata-schema GET returns a schema the response model refuses at the top level,
in the same shape as a validated schema, never nested under 'message'."""

import json

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.metadataschema import metadataSchemaService as svc

SCHEMA_ID = "schema-1"


def _get(schema):
    event = {
        "requestContext": {"http": {"method": "GET", "path": f"/database/GLOBAL/metadataschema/{SCHEMA_ID}"}},
        "pathParameters": {"databaseId": "GLOBAL", "metadataSchemaId": SCHEMA_ID},
        "queryStringParameters": None,
    }
    original = svc.claims_and_roles
    svc.claims_and_roles = {"tokens": ["u1"], "roles": [], "mfaEnabled": False}
    try:
        with patch.object(svc, "get_metadata_schema_details", return_value=dict(schema)), \
                patch.object(svc, "CasbinEnforcer", MagicMock()):
            response = svc.handle_get_request(event)
    finally:
        svc.claims_and_roles = original
    return response, json.loads(response["body"])


def _schema(**overrides):
    schema = {"metadataSchemaId": SCHEMA_ID, "databaseId": "GLOBAL", "schemaName": "Legacy",
              "metadataSchemaEntityType": "assetMetadata",
              "fields": {"fields": [{"metadataFieldKeyName": "partNumber",
                                     "metadataFieldValueType": "string", "required": False}]},
              "enabled": True}
    schema.update(overrides)
    return schema


@pytest.mark.unit
class TestSingleMetadataSchemaGet:
    def test_a_schema_the_model_refuses_is_returned_at_the_top_level(self):
        schema = _schema()
        del schema["enabled"]
        response, body = _get(schema)

        assert response["statusCode"] == 200
        assert "message" not in body
        assert body["metadataSchemaId"] == SCHEMA_ID
        assert body["schemaName"] == "Legacy"

    def test_a_valid_schema_is_returned_as_the_model_shapes_it(self):
        """POSITIVE CONTROL."""
        response, body = _get(_schema())

        assert response["statusCode"] == 200
        assert body["metadataSchemaId"] == SCHEMA_ID
        assert body["enabled"] is True
