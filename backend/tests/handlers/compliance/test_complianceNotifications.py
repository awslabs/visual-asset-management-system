# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.unit
class TestNotifyQuarantine:
    @patch("backend.backend.handlers.compliance.complianceNotifications.sns_client")
    @patch("backend.backend.handlers.compliance.complianceNotifications.dynamodb_client")
    def test_publishes_to_asset_topic(self, mock_dynamo, mock_sns):
        mock_dynamo.query.return_value = {
            "Items": [
                {
                    "assetName": {"S": "Test Asset"},
                    "snsTopic": {"S": "arn:aws:sns:us-east-1:123:AssetTopic"},
                }
            ]
        }

        from backend.backend.handlers.compliance.complianceNotifications import (
            notify_quarantine,
        )

        notify_quarantine("db1", "asset1", "my-schema", ["rule1", "rule2"])

        mock_sns.publish.assert_called_once()
        call_kwargs = mock_sns.publish.call_args[1]
        assert call_kwargs["TopicArn"] == "arn:aws:sns:us-east-1:123:AssetTopic"
        assert "QUARANTINED" in call_kwargs["Subject"]
        assert "rule1" in call_kwargs["Message"]
        assert "rule2" in call_kwargs["Message"]

    @patch("backend.backend.handlers.compliance.complianceNotifications.sns_client")
    @patch("backend.backend.handlers.compliance.complianceNotifications.dynamodb_client")
    def test_skips_when_no_topic(self, mock_dynamo, mock_sns):
        mock_dynamo.query.return_value = {"Items": []}

        from backend.backend.handlers.compliance.complianceNotifications import (
            notify_quarantine,
        )

        notify_quarantine("db1", "asset1", "my-schema")

        mock_sns.publish.assert_not_called()

    @patch("backend.backend.handlers.compliance.complianceNotifications.sns_client")
    @patch("backend.backend.handlers.compliance.complianceNotifications.dynamodb_client")
    def test_handles_publish_failure_gracefully(self, mock_dynamo, mock_sns):
        mock_dynamo.query.return_value = {
            "Items": [
                {
                    "assetName": {"S": "Test Asset"},
                    "snsTopic": {"S": "arn:aws:sns:us-east-1:123:AssetTopic"},
                }
            ]
        }
        mock_sns.publish.side_effect = Exception("SNS error")

        from backend.backend.handlers.compliance.complianceNotifications import (
            notify_quarantine,
        )

        # Should not raise
        notify_quarantine("db1", "asset1", "my-schema")


@pytest.mark.unit
class TestNotifyCascadePending:
    @patch("backend.backend.handlers.compliance.complianceNotifications.sns_client")
    @patch("backend.backend.handlers.compliance.complianceNotifications.dynamodb_client")
    def test_publishes_pending_notification(self, mock_dynamo, mock_sns):
        mock_dynamo.query.return_value = {
            "Items": [
                {
                    "assetName": {"S": "Parent Asset"},
                    "snsTopic": {"S": "arn:aws:sns:us-east-1:123:Topic"},
                }
            ]
        }

        from backend.backend.handlers.compliance.complianceNotifications import (
            notify_cascade_pending,
        )

        notify_cascade_pending("db1", "asset1", "cascade-id", 5)

        mock_sns.publish.assert_called_once()
        call_kwargs = mock_sns.publish.call_args[1]
        assert "CASCADE PENDING" in call_kwargs["Subject"]
        assert "5" in call_kwargs["Message"]
        assert "cascade-id" in call_kwargs["Message"]


@pytest.mark.unit
class TestNotifyCascadeCompleted:
    @patch("backend.backend.handlers.compliance.complianceNotifications.sns_client")
    @patch("backend.backend.handlers.compliance.complianceNotifications.dynamodb_client")
    def test_publishes_completion_with_summary(self, mock_dynamo, mock_sns):
        mock_dynamo.query.return_value = {
            "Items": [
                {
                    "assetName": {"S": "Parent Asset"},
                    "snsTopic": {"S": "arn:aws:sns:us-east-1:123:Topic"},
                }
            ]
        }

        from backend.backend.handlers.compliance.complianceNotifications import (
            notify_cascade_completed,
        )

        notify_cascade_completed(
            "db1", "asset1", "cascade-id",
            {"compliant": 3, "non_compliant": 1, "skipped": 2},
        )

        mock_sns.publish.assert_called_once()
        call_kwargs = mock_sns.publish.call_args[1]
        assert "CASCADE COMPLETE" in call_kwargs["Subject"]
        assert "compliant: 3" in call_kwargs["Message"]
        assert "non_compliant: 1" in call_kwargs["Message"]
