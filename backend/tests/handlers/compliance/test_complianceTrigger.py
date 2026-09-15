# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.unit
class TestCheckAndTriggerCascade:
    @patch("backend.backend.handlers.compliance.complianceTrigger.audit_table")
    @patch("backend.backend.handlers.compliance.complianceTrigger.cascade_table")
    @patch("backend.backend.handlers.compliance.complianceTrigger.asset_links_table")
    def test_creates_cascade_when_children_exist(
        self, mock_links, mock_cascade, mock_audit
    ):
        mock_links.query.side_effect = [
            {"Items": [{"toAssetDatabaseId": "db1", "toAssetId": "child1"}]},
            {"Count": 3},
        ]

        from backend.backend.handlers.compliance.complianceTrigger import (
            check_and_trigger_cascade,
        )

        check_and_trigger_cascade("db1", "parent1")

        mock_cascade.put_item.assert_called_once()
        item = mock_cascade.put_item.call_args[1]["Item"]
        assert item["state"] == "pending_approval"
        assert item["triggeredByDatabaseId"] == "db1"
        assert item["triggeredByAssetId"] == "parent1"

        mock_audit.put_item.assert_called_once()
        audit_item = mock_audit.put_item.call_args[1]["Item"]
        assert audit_item["eventType"] == "cascade_auto_triggered"

    @patch("backend.backend.handlers.compliance.complianceTrigger.asset_links_table")
    def test_does_nothing_when_no_children(self, mock_links):
        mock_links.query.return_value = {"Items": []}

        from backend.backend.handlers.compliance.complianceTrigger import (
            check_and_trigger_cascade,
        )

        check_and_trigger_cascade("db1", "parent1")


@pytest.mark.unit
class TestProcessAssetEvent:
    @patch("backend.backend.handlers.compliance.complianceTrigger.compliance_table")
    def test_skips_when_no_database_or_asset_id(self, mock_compliance):
        from backend.backend.handlers.compliance.complianceTrigger import (
            process_asset_event,
        )

        process_asset_event({})
        mock_compliance.get_item.assert_not_called()

    @patch("backend.backend.handlers.compliance.complianceTrigger.database_table")
    @patch("backend.backend.handlers.compliance.complianceTrigger.compliance_table")
    def test_skips_when_no_compliance_record_and_no_default(
        self, mock_compliance, mock_database
    ):
        mock_compliance.get_item.return_value = {}
        mock_database.get_item.return_value = {"Item": {}}

        from backend.backend.handlers.compliance.complianceTrigger import (
            process_asset_event,
        )

        process_asset_event({"databaseId": "db1", "assetId": "asset1"})

    @patch("backend.backend.handlers.compliance.complianceTrigger.audit_table")
    @patch("backend.backend.handlers.compliance.complianceTrigger.evaluation_table")
    @patch("backend.backend.handlers.compliance.complianceTrigger.schema_table")
    @patch("backend.backend.handlers.compliance.complianceTrigger.compliance_table")
    def test_triggers_evaluation_for_legacy_schema(
        self, mock_compliance, mock_schema, mock_evaluation, mock_audit
    ):
        mock_compliance.get_item.return_value = {
            "Item": {
                "databaseId": "db1",
                "assetId": "asset1",
                "schemaName": "legacy-schema",
                "schemaSource": "database",
            }
        }
        mock_schema.query.return_value = {
            "Items": [
                {
                    "schemaName": "legacy-schema",
                    "schemaBody": json.dumps({"type": "object"}),
                }
            ]
        }

        from backend.backend.handlers.compliance.complianceTrigger import (
            process_asset_event,
        )

        process_asset_event({"databaseId": "db1", "assetId": "asset1"})

        mock_evaluation.put_item.assert_called_once()
        item = mock_evaluation.put_item.call_args[1]["Item"]
        assert item["status"] == "pending"
        assert item["schemaName"] == "legacy-schema"


@pytest.mark.unit
class TestLambdaHandler:
    @patch("backend.backend.handlers.compliance.complianceTrigger.database_table")
    @patch("backend.backend.handlers.compliance.complianceTrigger.compliance_table")
    def test_processes_sns_records(self, mock_compliance, mock_database):
        mock_compliance.get_item.return_value = {}
        mock_database.get_item.return_value = {"Item": {}}

        from backend.backend.handlers.compliance.complianceTrigger import (
            lambda_handler,
        )

        event = {
            "Records": [
                {
                    "Sns": {
                        "Message": json.dumps({
                            "databaseId": "db1",
                            "assetId": "asset1",
                        })
                    }
                }
            ]
        }

        lambda_handler(event, None)
        mock_compliance.get_item.assert_called_once()
