# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch


def make_event(method="GET", path="/compliance/schemas", body=None, path_params=None):
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params or {},
        "queryStringParameters": {},
        "headers": {"authorization": "Bearer test-token"},
    }
    if body:
        event["body"] = json.dumps(body)
    return event


@pytest.mark.unit
class TestComplianceSchemaService:
    @patch("backend.backend.handlers.compliance.complianceSchemaService.schema_table")
    @patch("backend.backend.handlers.compliance.complianceSchemaService.request_to_claims")
    def test_list_schemas(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.scan.return_value = {
            "Items": [
                {"schemaName": "schema1", "description": "test"},
                {"schemaName": "schema2", "description": "test2"},
            ]
        }

        from backend.backend.handlers.compliance.complianceSchemaService import lambda_handler

        response = lambda_handler(make_event(), None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "schemas" in body
        assert len(body["schemas"]) == 2

    @patch("backend.backend.handlers.compliance.complianceSchemaService.schema_table")
    @patch("backend.backend.handlers.compliance.complianceSchemaService.request_to_claims")
    def test_get_schema(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.get_item.return_value = {
            "Item": {"schemaName": "test-schema", "description": "desc"}
        }

        from backend.backend.handlers.compliance.complianceSchemaService import lambda_handler

        event = make_event(path="/compliance/schemas/test-schema", path_params={"schemaName": "test-schema"})
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["schemaName"] == "test-schema"

    @patch("backend.backend.handlers.compliance.complianceSchemaService.schema_table")
    @patch("backend.backend.handlers.compliance.complianceSchemaService.request_to_claims")
    def test_get_schema_not_found(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.get_item.return_value = {}

        from backend.backend.handlers.compliance.complianceSchemaService import lambda_handler

        event = make_event(path="/compliance/schemas/missing", path_params={"schemaName": "missing"})
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400

    @patch("backend.backend.handlers.compliance.complianceSchemaService.schema_table")
    @patch("backend.backend.handlers.compliance.complianceSchemaService.request_to_claims")
    def test_create_schema(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.get_item.return_value = {}
        mock_table.put_item.return_value = {}

        from backend.backend.handlers.compliance.complianceSchemaService import lambda_handler

        body = {"schemaName": "new-schema", "description": "desc", "schemaBody": {"fields": []}}
        event = make_event(method="POST", body=body)
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        resp_body = json.loads(response["body"])
        assert "created" in resp_body.get("message", "").lower() or resp_body.get("schemaName") == "new-schema"

    @patch("backend.backend.handlers.compliance.complianceSchemaService.request_to_claims")
    def test_create_schema_missing_name(self, mock_claims):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}

        from backend.backend.handlers.compliance.complianceSchemaService import lambda_handler

        body = {"description": "desc", "schemaBody": {"fields": []}}
        event = make_event(method="POST", body=body)
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400

    @patch("backend.backend.handlers.compliance.complianceSchemaService.request_to_claims")
    def test_auth_error_returns_early(self, mock_claims):
        mock_claims.return_value = {"statusCode": 403, "body": json.dumps({"message": "Forbidden"})}

        from backend.backend.handlers.compliance.complianceSchemaService import lambda_handler

        response = lambda_handler(make_event(), None)
        assert response["statusCode"] == 403

    @patch("backend.backend.handlers.compliance.complianceSchemaService.schema_table")
    @patch("backend.backend.handlers.compliance.complianceSchemaService.request_to_claims")
    def test_method_not_allowed(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}

        from backend.backend.handlers.compliance.complianceSchemaService import lambda_handler

        event = make_event(method="PATCH")
        response = lambda_handler(event, None)
        assert response["statusCode"] == 405
