# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch


def make_event(method="GET", path="/compliance/audit", body=None, path_params=None, query_params=None):
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
        "headers": {"authorization": "Bearer test-token"},
    }
    if body:
        event["body"] = json.dumps(body)
    return event


@pytest.mark.unit
class TestFMMAuditService:
    @patch("backend.backend.handlers.fmm.fmmAuditService.audit_table")
    @patch("backend.backend.handlers.fmm.fmmAuditService.request_to_claims")
    def test_query_audit_no_filter(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.scan.return_value = {
            "Items": [
                {"entryId": "e1", "eventType": "compliance_check", "timestamp": "2024-01-01T00:00:00Z"},
            ]
        }

        from backend.backend.handlers.fmm.fmmAuditService import lambda_handler

        response = lambda_handler(make_event(), None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "entries" in body

    @patch("backend.backend.handlers.fmm.fmmAuditService.audit_table")
    @patch("backend.backend.handlers.fmm.fmmAuditService.request_to_claims")
    def test_query_audit_with_event_type(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.query.return_value = {
            "Items": [
                {"entryId": "e1", "eventType": "quarantine_released"},
            ]
        }

        from backend.backend.handlers.fmm.fmmAuditService import lambda_handler

        event = make_event(query_params={"eventType": "quarantine_released"})
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "entries" in body

    @patch("backend.backend.handlers.fmm.fmmAuditService.audit_table")
    @patch("backend.backend.handlers.fmm.fmmAuditService.request_to_claims")
    def test_get_asset_audit(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.query.return_value = {
            "Items": [
                {"entryId": "e1", "eventType": "compliance_check", "databaseId": "db1", "assetId": "a1"},
            ]
        }

        from backend.backend.handlers.fmm.fmmAuditService import lambda_handler

        event = make_event(
            path="/compliance/audit/db1/a1",
            path_params={"databaseId": "db1", "assetId": "a1"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "entries" in body

    @patch("backend.backend.handlers.fmm.fmmAuditService.audit_table")
    @patch("backend.backend.handlers.fmm.fmmAuditService.request_to_claims")
    def test_get_asset_audit_with_date_range(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.query.return_value = {"Items": []}

        from backend.backend.handlers.fmm.fmmAuditService import lambda_handler

        event = make_event(
            path="/compliance/audit/db1/a1",
            path_params={"databaseId": "db1", "assetId": "a1"},
            query_params={
                "startDate": "2024-01-01T00:00:00Z",
                "endDate": "2024-06-30T23:59:59Z",
            },
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200

        call_kwargs = mock_table.query.call_args[1]
        assert "KeyConditionExpression" in call_kwargs

    @patch("backend.backend.handlers.fmm.fmmAuditService.audit_table")
    @patch("backend.backend.handlers.fmm.fmmAuditService.request_to_claims")
    def test_query_audit_with_event_type_and_date_range(
        self, mock_claims, mock_table
    ):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.query.return_value = {"Items": []}

        from backend.backend.handlers.fmm.fmmAuditService import lambda_handler

        event = make_event(
            query_params={
                "eventType": "compliance_check",
                "startDate": "2024-03-01T00:00:00Z",
            },
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        mock_table.query.assert_called_once()

    @patch("backend.backend.handlers.fmm.fmmAuditService.audit_table")
    @patch("backend.backend.handlers.fmm.fmmAuditService.request_to_claims")
    def test_get_asset_audit_with_start_date_only(
        self, mock_claims, mock_table
    ):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.query.return_value = {"Items": []}

        from backend.backend.handlers.fmm.fmmAuditService import lambda_handler

        event = make_event(
            path="/compliance/audit/db1/a1",
            path_params={"databaseId": "db1", "assetId": "a1"},
            query_params={"startDate": "2024-01-01T00:00:00Z"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200

    @patch("backend.backend.handlers.fmm.fmmAuditService.request_to_claims")
    def test_method_not_allowed(self, mock_claims):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}

        from backend.backend.handlers.fmm.fmmAuditService import lambda_handler

        event = make_event(method="DELETE")
        response = lambda_handler(event, None)
        assert response["statusCode"] == 405
