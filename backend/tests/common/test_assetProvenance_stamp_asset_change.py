# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`stamp_asset_change` records a change made outside the asset row -- a metadata write
(`metadataEdit`), a link create or delete (`linkChange`), a workflow execution's metadata output
(`workflowExecution` with its id) -- as the row's latest change, with one targeted update: only
`lastChangeSource` and `lastChangeAt` are SET (plus the execution id for a workflow write), so
`lastUploadAt`, `lastAssetVersionAt` and every sibling attribute keep their values. The write is
conditioned on the live row existing, so an archived asset is not stamped and no row is created, and
a failed stamp is logged and reported as False, never raised."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from common.assetProvenance import (
    ASSET_CHANGE_SOURCE_LINK_CHANGE,
    ASSET_CHANGE_SOURCE_METADATA_EDIT,
    stamp_asset_change,
)
from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
from tests.handlers.assets.test_asset_record_sibling_targeted_update import (  # noqa: E402
    ASSET_ID,
    DATABASE_ID,
    FakeAssetTable,
    _asset,
)

EARLIER = "2026-03-01T12:00:00+00:00"


def _stored_row():
    """A live asset whose latest change was a workflow execution's output after a user upload and an
    asset version."""
    return _asset(assetName="Turbine", lastChangeSource="workflowExecution", lastChangeAt=EARLIER,
                  lastChangeWorkflowExecutionId="exec-0", lastUploadAt=EARLIER,
                  lastAssetVersionAt=EARLIER)


@pytest.mark.unit
@pytest.mark.parametrize("source", [ASSET_CHANGE_SOURCE_METADATA_EDIT,
                                    ASSET_CHANGE_SOURCE_LINK_CHANGE])
class TestAUserChangeStamp:

    def test_the_row_records_the_change_and_drops_the_execution_id(self, source):
        table = FakeAssetTable(_stored_row())
        assert stamp_asset_change(table, DATABASE_ID, ASSET_ID, source) is True
        row = table.stored()
        assert row["lastChangeSource"] == source
        assert datetime.fromisoformat(row["lastChangeAt"]) > datetime.fromisoformat(EARLIER)
        assert "lastChangeWorkflowExecutionId" not in row

    def test_only_the_two_provenance_attributes_are_set_and_one_removed(self, source):
        table = FakeAssetTable(_stored_row())
        stamp_asset_change(table, DATABASE_ID, ASSET_ID, source)
        assert table.put_item_calls == []
        assert set(table.updated_attributes[0]) == {"lastChangeSource", "lastChangeAt"}
        assert table.removed_attributes == [["lastChangeWorkflowExecutionId"]]

    def test_the_sibling_attributes_are_not_blanked(self, source):
        table = FakeAssetTable(_stored_row())
        stamp_asset_change(table, DATABASE_ID, ASSET_ID, source)
        row = table.stored()
        assert row["lastUploadAt"] == EARLIER
        assert row["lastAssetVersionAt"] == EARLIER
        assert row["assetName"] == "Turbine"
        assert row["tags"] == ["keep"]


@pytest.mark.unit
class TestAWorkflowExecutionStamp:

    def test_the_execution_is_recorded_as_the_source_with_its_id(self):
        table = FakeAssetTable(_stored_row())
        assert stamp_asset_change(table, DATABASE_ID, ASSET_ID, ASSET_CHANGE_SOURCE_METADATA_EDIT,
                                  workflow_execution_id="exec-7") is True
        row = table.stored()
        assert row["lastChangeSource"] == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
        assert row["lastChangeWorkflowExecutionId"] == "exec-7"
        assert table.removed_attributes == [[]]
        assert set(table.updated_attributes[0]) == {
            "lastChangeSource", "lastChangeAt", "lastChangeWorkflowExecutionId"}
        assert row["lastUploadAt"] == EARLIER


@pytest.mark.unit
class TestTheStampIsBestEffort:

    def test_an_archived_or_missing_asset_is_not_stamped_and_no_row_is_created(self):
        table = FakeAssetTable()
        assert stamp_asset_change(table, DATABASE_ID, ASSET_ID,
                                  ASSET_CHANGE_SOURCE_LINK_CHANGE) is False
        assert table.items == {}

    def test_the_write_is_conditioned_on_the_live_row(self):
        table = MagicMock()
        stamp_asset_change(table, DATABASE_ID, ASSET_ID, ASSET_CHANGE_SOURCE_METADATA_EDIT)
        call = table.update_item.call_args.kwargs
        assert call["Key"] == {"databaseId": DATABASE_ID, "assetId": ASSET_ID}
        assert "attribute_exists(#asset)" in call["ConditionExpression"]
        assert call["ExpressionAttributeNames"]["#asset"] == "assetId"

    def test_any_failure_is_reported_not_raised(self):
        table = MagicMock()
        table.update_item.side_effect = RuntimeError("AccessDeniedException")
        assert stamp_asset_change(table, DATABASE_ID, ASSET_ID,
                                  ASSET_CHANGE_SOURCE_METADATA_EDIT) is False

    def test_the_sources_are_not_a_workflow_execution_write(self):
        assert ASSET_CHANGE_SOURCE_METADATA_EDIT == "metadataEdit"
        assert ASSET_CHANGE_SOURCE_LINK_CHANGE == "linkChange"
        assert VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION not in (
            ASSET_CHANGE_SOURCE_METADATA_EDIT, ASSET_CHANGE_SOURCE_LINK_CHANGE)
