# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch


def make_event(method="GET", path="/compliance/state/db1/asset1", body=None, path_params=None):
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params or {"databaseId": "db1", "assetId": "asset1"},
        "queryStringParameters": {},
        "headers": {"authorization": "Bearer test-token"},
    }
    if body:
        event["body"] = json.dumps(body)
    return event


@pytest.mark.unit
class TestComplianceEvaluateService:
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.compliance_table")
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.request_to_claims")
    def test_get_compliance_state_found(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.get_item.return_value = {
            "Item": {
                "databaseId": "db1",
                "assetId": "asset1",
                "complianceState": "compliant",
                "schemaName": "test-schema",
            }
        }

        from backend.backend.handlers.compliance.complianceEvaluateService import lambda_handler

        response = lambda_handler(make_event(), None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["complianceState"] == "compliant"

    @patch("backend.backend.handlers.compliance.complianceEvaluateService.compliance_table")
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.request_to_claims")
    def test_get_compliance_state_not_found(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.get_item.return_value = {}

        from backend.backend.handlers.compliance.complianceEvaluateService import lambda_handler

        response = lambda_handler(make_event(), None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["complianceState"] == "unknown"

    @patch("backend.backend.handlers.compliance.complianceEvaluateService.compliance_table")
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.request_to_claims")
    def test_get_database_overview(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.query.return_value = {
            "Items": [
                {"databaseId": "db1", "assetId": "a1", "complianceState": "compliant"},
                {"databaseId": "db1", "assetId": "a2", "complianceState": "non_compliant"},
                {"databaseId": "db1", "assetId": "a3", "complianceState": "pending_evaluation"},
            ]
        }

        from backend.backend.handlers.compliance.complianceEvaluateService import lambda_handler

        event = make_event(
            path="/compliance/state/db1",
            path_params={"databaseId": "db1"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["totalAssets"] == 3
        assert body["summary"]["compliant"] == 1
        assert body["summary"]["non_compliant"] == 1
        assert body["summary"]["pending_evaluation"] == 1

    @patch("backend.backend.handlers.compliance.complianceEvaluateService.audit_table")
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.compliance_table")
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.asset_table")
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.evaluation_table")
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.request_to_claims")
    def test_evaluate_asset(self, mock_claims, mock_eval_table, mock_asset_table, mock_compliance_table, mock_audit_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_asset_table.get_item.return_value = {"Item": {"databaseId": "db1", "assetId": "asset1"}}
        mock_compliance_table.get_item.return_value = {
            "Item": {"schemaName": "schema1", "schemaSource": "database"}
        }
        mock_eval_table.put_item.return_value = {}
        mock_compliance_table.update_item.return_value = {}
        mock_audit_table.put_item.return_value = {}

        from backend.backend.handlers.compliance.complianceEvaluateService import lambda_handler

        event = make_event(
            method="POST",
            path="/compliance/evaluate/db1/asset1",
            path_params={"databaseId": "db1", "assetId": "asset1"},
            body={},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "evaluationId" in body

    @patch("backend.backend.handlers.compliance.complianceEvaluateService.asset_table")
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.request_to_claims")
    def test_evaluate_asset_not_found(self, mock_claims, mock_asset_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_asset_table.get_item.return_value = {}

        from backend.backend.handlers.compliance.complianceEvaluateService import lambda_handler

        event = make_event(
            method="POST",
            path="/compliance/evaluate/db1/missing",
            path_params={"databaseId": "db1", "assetId": "missing"},
            body={},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400

    @patch("backend.backend.handlers.compliance.complianceEvaluateService.evaluation_table")
    @patch("backend.backend.handlers.compliance.complianceEvaluateService.request_to_claims")
    def test_get_evaluations(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.query.return_value = {
            "Items": [{"evaluationId": "e1", "status": "pending"}]
        }

        from backend.backend.handlers.compliance.complianceEvaluateService import lambda_handler

        event = make_event(
            path="/compliance/evaluations/db1/asset1",
            path_params={"databaseId": "db1", "assetId": "asset1"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "evaluations" in body

    @patch("backend.backend.handlers.compliance.complianceEvaluateService.request_to_claims")
    def test_auth_error(self, mock_claims):
        mock_claims.return_value = {"statusCode": 403, "body": json.dumps({"message": "Forbidden"})}

        from backend.backend.handlers.compliance.complianceEvaluateService import lambda_handler

        response = lambda_handler(make_event(), None)
        assert response["statusCode"] == 403
