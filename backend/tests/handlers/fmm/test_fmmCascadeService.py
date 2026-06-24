# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch


def make_event(method="GET", path="/compliance/cascades", body=None, path_params=None):
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
class TestFMMCascadeService:
    @patch("backend.backend.handlers.fmm.fmmCascadeService.cascade_table")
    @patch("backend.backend.handlers.fmm.fmmCascadeService.request_to_claims")
    def test_list_pending_cascades(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.query.return_value = {
            "Items": [{"cascadeId": "c1", "state": "pending_approval"}]
        }

        from backend.backend.handlers.fmm.fmmCascadeService import lambda_handler

        response = lambda_handler(make_event(), None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "cascades" in body

    @patch("backend.backend.handlers.fmm.fmmCascadeService.audit_table")
    @patch("backend.backend.handlers.fmm.fmmCascadeService.cascade_table")
    @patch("backend.backend.handlers.fmm.fmmCascadeService.request_to_claims")
    def test_create_cascade(self, mock_claims, mock_cascade_table, mock_audit_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_cascade_table.put_item.return_value = {}
        mock_audit_table.put_item.return_value = {}

        from backend.backend.handlers.fmm.fmmCascadeService import lambda_handler

        event = make_event(
            method="POST",
            body={"databaseId": "db1", "assetId": "a1", "reason": "test"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "cascadeId" in body

    @patch("backend.backend.handlers.fmm.fmmCascadeService.request_to_claims")
    def test_create_cascade_missing_fields(self, mock_claims):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}

        from backend.backend.handlers.fmm.fmmCascadeService import lambda_handler

        event = make_event(method="POST", body={"reason": "test"})
        response = lambda_handler(event, None)
        assert response["statusCode"] == 400

    @patch("backend.backend.handlers.fmm.fmmCascadeService.cascade_table")
    @patch("backend.backend.handlers.fmm.fmmCascadeService.request_to_claims")
    def test_get_cascade(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.get_item.return_value = {
            "Item": {"cascadeId": "c1", "state": "executing"}
        }

        from backend.backend.handlers.fmm.fmmCascadeService import lambda_handler

        event = make_event(
            path="/compliance/cascades/c1",
            path_params={"cascadeId": "c1"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200

    @patch("backend.backend.handlers.fmm.fmmCascadeService.cascade_table")
    @patch("backend.backend.handlers.fmm.fmmCascadeService.request_to_claims")
    def test_get_cascade_not_found(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.get_item.return_value = {}

        from backend.backend.handlers.fmm.fmmCascadeService import lambda_handler

        event = make_event(
            path="/compliance/cascades/missing",
            path_params={"cascadeId": "missing"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 404

    @patch("backend.backend.handlers.fmm.fmmCascadeService.cascade_table")
    @patch("backend.backend.handlers.fmm.fmmCascadeService.request_to_claims")
    def test_approve_cascade(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.update_item.return_value = {}

        from backend.backend.handlers.fmm.fmmCascadeService import lambda_handler

        event = make_event(
            method="POST",
            path="/compliance/cascades/c1/approve",
            path_params={"cascadeId": "c1"},
            body={"reason": "looks good"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200

    @patch("backend.backend.handlers.fmm.fmmCascadeService.cascade_table")
    @patch("backend.backend.handlers.fmm.fmmCascadeService.request_to_claims")
    def test_reject_cascade(self, mock_claims, mock_table):
        mock_claims.return_value = {"sub": "user1", "tokens": ["t1"]}
        mock_table.update_item.return_value = {}

        from backend.backend.handlers.fmm.fmmCascadeService import lambda_handler

        event = make_event(
            method="POST",
            path="/compliance/cascades/c1/reject",
            path_params={"cascadeId": "c1"},
            body={"reason": "not ready"},
        )
        response = lambda_handler(event, None)
        assert response["statusCode"] == 200
