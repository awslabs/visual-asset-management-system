# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A permanently deleted asset takes its compliance record with it (D27).

The trigger receives the asset table's REMOVE. When the asset has no row left, live or archived,
its compliance asset-state row (state, binding or override, quarantine) is deleted and one
`asset_deleted` audit entry is written; evaluations and the audit trail are history and stay. An
archive or unarchive moves the row between `<databaseId>` and `<databaseId>#deleted`, a REMOVE of
the old key too: the asset still has a row then and its record is left as it is. A change event that
arrives after the delete does not register the asset again."""

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance._harness import (
    ASSET, COMPLIANCE_ASSET_STATE_TABLE_DEFINITION, DB, SCHEMA)
from backend.tests.handlers.compliance.test_complianceTrigger import (
    AUTO_EVAL_DB, _sns_event, _stream_message)
from handlers.compliance import complianceTrigger as trigger

MOD = "handlers.compliance.complianceTrigger"
STORE = "handlers.compliance.complianceEvaluationStore"
ARCHIVED = f"{DB}#deleted"

ASSET_TABLE = {
    "TableName": "assetStorageTable-d27",
    "KeySchema": [{"AttributeName": "databaseId", "KeyType": "HASH"},
                  {"AttributeName": "assetId", "KeyType": "RANGE"}],
    "AttributeDefinitions": [{"AttributeName": "databaseId", "AttributeType": "S"},
                             {"AttributeName": "assetId", "AttributeType": "S"}],
    "BillingMode": "PAY_PER_REQUEST",
}
QUARANTINED = {"databaseId": DB, "assetId": ASSET, "schemaName": SCHEMA, "schemaSource": "asset",
               "complianceState": "quarantined", "lastEvaluationId": "eval-1"}


@pytest.fixture
def tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        yield (resource.create_table(**ASSET_TABLE),
               resource.create_table(**COMPLIANCE_ASSET_STATE_TABLE_DEFINITION))


def _remove(database_id=DB):
    return _stream_message("REMOVE", database_id=database_id)


def _handle(tables, *messages, database_item=None):
    asset_table, state_table = tables
    database_table = MagicMock(name="database_table")
    database_table.get_item.return_value = {"Item": database_item} if database_item else {}
    with patch(f"{MOD}.asset_table", asset_table), \
            patch(f"{MOD}.asset_state_table", state_table), \
            patch(f"{MOD}.database_table", database_table), \
            patch(f"{STORE}.write_audit") as write_audit, \
            patch(f"{STORE}.run_evaluation") as run_evaluation:
        response = trigger.lambda_handler(_sns_event(*messages), MagicMock())
    return response, write_audit, run_evaluation, database_table


def _state(tables):
    return tables[1].get_item(Key={"databaseId": DB, "assetId": ASSET}).get("Item")


@pytest.mark.unit
class TestAPermanentDeleteRemovesTheComplianceRecord:

    def test_the_state_row_is_deleted_and_audited_once(self, tables):
        tables[1].put_item(Item=dict(QUARANTINED))
        response, write_audit, run_evaluation, database_table = _handle(tables, _remove())
        assert response == {"batchItemFailures": []}
        assert _state(tables) is None
        write_audit.assert_called_once()
        call = write_audit.call_args
        assert call.args[:2] == (DB, ASSET)
        assert call.kwargs["event_type"] == "asset_deleted"
        assert call.kwargs["previous_state"] == "quarantined"
        assert call.kwargs["schema_name"] == SCHEMA
        run_evaluation.assert_not_called()
        # In any database, bound or not: the database row is not read.
        database_table.get_item.assert_not_called()

    def test_an_archived_asset_deleted_permanently_loses_its_record(self, tables):
        tables[1].put_item(Item=dict(QUARANTINED))
        _, write_audit, _, _ = _handle(tables, _remove(database_id=ARCHIVED))
        assert _state(tables) is None
        write_audit.assert_called_once()

    def test_both_partitions_removed_in_one_batch_audit_once(self, tables):
        tables[1].put_item(Item=dict(QUARANTINED))
        _, write_audit, _, _ = _handle(tables, _remove(), _remove(database_id=ARCHIVED))
        assert _state(tables) is None
        assert write_audit.call_count == 1

    def test_an_asset_without_a_record_audits_nothing(self, tables):
        _, write_audit, _, _ = _handle(tables, _remove())
        write_audit.assert_not_called()


@pytest.mark.unit
class TestAnArchiveIsNotADelete:

    def test_archiving_keeps_the_record(self, tables):
        tables[0].put_item(Item={"databaseId": ARCHIVED, "assetId": ASSET})
        tables[1].put_item(Item=dict(QUARANTINED))
        _, write_audit, _, _ = _handle(tables, _remove())
        assert _state(tables) == QUARANTINED
        write_audit.assert_not_called()

    def test_unarchiving_keeps_the_record(self, tables):
        tables[0].put_item(Item={"databaseId": DB, "assetId": ASSET})
        tables[1].put_item(Item=dict(QUARANTINED))
        _, write_audit, _, _ = _handle(tables, _remove(database_id=ARCHIVED))
        assert _state(tables) == QUARANTINED
        write_audit.assert_not_called()


@pytest.mark.unit
def test_a_change_event_after_the_delete_does_not_register_the_asset_again(tables):
    """A stamp's MODIFY delivered after the asset was deleted finds no state row and no asset row:
    the asset is not registered under the database binding and nothing is evaluated."""
    _, _, run_evaluation, _ = _handle(
        tables, _stream_message(change_source="metadataEdit",
                                change_at="2026-03-01T12:00:01.000000+00:00"),
        database_item=AUTO_EVAL_DB)
    assert _state(tables) is None
    run_evaluation.assert_not_called()


@pytest.mark.unit
def test_a_delete_followed_by_a_stale_modify_in_one_batch_stays_deleted(tables):
    tables[1].put_item(Item=dict(QUARANTINED))
    _, _, run_evaluation, _ = _handle(
        tables, _remove(), _stream_message(change_source="assetEdit",
                                           change_at="2026-03-01T12:00:01+00:00"),
        database_item=AUTO_EVAL_DB)
    assert _state(tables) is None
    run_evaluation.assert_not_called()
