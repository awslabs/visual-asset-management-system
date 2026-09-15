# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch


def make_event(method="GET", path="/compliance/quarantine", body=None, path_params=None):
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
class TestComplianceQuarantineService:
    @patch("backend.backend.handlers.compliance.complianceQuarantineService.compliance_table")
    @patch("backend.backend.handlers.compliance.complianceQuarantineService.request_to_claims")
    def test_list_quarantined(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.query.return_value = {"Items": []}
        mock_table.scan.return_value = {
            "Items": [
                {"databaseId": "db1", "assetId": "a1", "complianceState": "quarantined"},
            ]
        }

        from backend.backend.handlers.compliance.complianceQuarantineService import lambda_handler

        response = lambda_handler(make_event(), None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "quarantinedAssets" in body

    @patch("backend.backend.handlers.compliance.complianceQuarantineService.audit_table")
    @patch("backend.backend.handlers.compliance.complianceQuarantineService.compliance_table")
    @patch("backend.backend.handlers.compliance.complianceQuarantineService.request_to_claims")
    def test_release_quarantine(self, mock_claims, mock_compliance_table, mock_audit_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_compliance_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "a1", "complianceState": "quarantined"}
        }
        mock_compliance_table.update_item.return_value = {}
        mock_audit_table.put_item.return_value = {}

        from backend.backend.handlers.compliance.complianceQuarantineService import lambda_handler

        event = make_event(
            method="POST",
            path="/compliance/quarantine/db1/a1/release",
            path_params={"databaseId": "db1", "assetId": "a1"},
            body={"reason": "test release"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200

    @patch("backend.backend.handlers.compliance.complianceQuarantineService.compliance_table")
    @patch("backend.backend.handlers.compliance.complianceQuarantineService.request_to_claims")
    def test_release_not_quarantined(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "a1", "complianceState": "compliant"}
        }

        from backend.backend.handlers.compliance.complianceQuarantineService import lambda_handler

        event = make_event(
            method="POST",
            path="/compliance/quarantine/db1/a1/release",
            path_params={"databaseId": "db1", "assetId": "a1"},
            body={},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400

    @patch("backend.backend.handlers.compliance.complianceQuarantineService.audit_table")
    @patch("backend.backend.handlers.compliance.complianceQuarantineService.compliance_table")
    @patch("backend.backend.handlers.compliance.complianceQuarantineService.request_to_claims")
    def test_grant_exception(self, mock_claims, mock_compliance_table, mock_audit_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_compliance_table.get_item.return_value = {
            "Item": {"databaseId": "db1", "assetId": "a1", "complianceState": "quarantined"}
        }
        mock_compliance_table.update_item.return_value = {}
        mock_audit_table.put_item.return_value = {}

        from backend.backend.handlers.compliance.complianceQuarantineService import lambda_handler

        event = make_event(
            method="POST",
            path="/compliance/quarantine/db1/a1/exception",
            path_params={"databaseId": "db1", "assetId": "a1"},
            body={"reason": "approved by admin"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200

    @patch("backend.backend.handlers.compliance.complianceQuarantineService.request_to_claims")
    def test_grant_exception_no_reason(self, mock_claims):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}

        from backend.backend.handlers.compliance.complianceQuarantineService import lambda_handler

        event = make_event(
            method="POST",
            path="/compliance/quarantine/db1/a1/exception",
            path_params={"databaseId": "db1", "assetId": "a1"},
            body={},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400
