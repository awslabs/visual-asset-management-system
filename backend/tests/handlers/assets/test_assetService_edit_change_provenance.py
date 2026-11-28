# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset edit that changes the asset's name, description or tags records the edit as the asset
row's latest change: the targeted update also sets `lastChangeSource: assetEdit` and a fresh
`lastChangeAt`, removes `lastChangeWorkflowExecutionId`, and leaves `lastUploadAt` as it is. That
write is the stream MODIFY the compliance trigger evaluates on, so an asset-attribute verdict follows
the edit. An edit that changes none of the three — a resubmission of the stored values, or a change
of `isDistributable` alone — writes no provenance."""

import sys
import types
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest

# Reuse the real assetService loader and the in-memory asset table that applies SET and REMOVE.
from tests.handlers.assets.test_asset_record_sibling_targeted_update import (  # noqa: E402
    FakeAssetTable,
    _asset,
    _run_update_asset,
)

LAST_UPLOAD_AT = "2026-03-01T12:00:00+00:00"
PROVENANCE = {"lastChangeSource", "lastChangeAt"}


def _stored_row():
    """An asset whose latest change was a workflow execution's output write after a user upload."""
    return _asset(assetName="Name", tags=["keep"], lastChangeSource="workflowExecution",
                  lastChangeAt=LAST_UPLOAD_AT, lastChangeWorkflowExecutionId="exec-1",
                  lastUploadAt=LAST_UPLOAD_AT)


@contextmanager
def _tag_validation_stubbed():
    """update_asset imports the tag-scope validators from handlers.assets.createAsset at call time;
    tag scoping is covered by test_assetService_update_tag_scope.py, so they are no-ops here."""
    saved = sys.modules.get("handlers.assets.createAsset")
    stub = types.ModuleType("handlers.assets.createAsset")
    stub.validate_tags_exist = MagicMock()
    stub.verify_all_required_tags_satisfied = MagicMock()
    sys.modules["handlers.assets.createAsset"] = stub
    try:
        yield
    finally:
        if saved is not None:
            sys.modules["handlers.assets.createAsset"] = saved
        else:
            sys.modules.pop("handlers.assets.createAsset", None)


def _edit(update_data):
    table = FakeAssetTable(_stored_row())
    with _tag_validation_stubbed():
        _run_update_asset(table, _stored_row(), update_data)
    return table


@pytest.mark.unit
@pytest.mark.parametrize("update_data", [
    {"assetName": "Renamed"},
    {"description": "a new description"},
    {"tags": ["keep", "released"]},
], ids=["assetName", "description", "tags"])
class TestAnEditOfAStampedFieldRecordsTheEdit:

    def test_the_row_records_the_edit_as_its_latest_change(self, update_data):
        row = _edit(update_data).stored()
        assert row["lastChangeSource"] == "assetEdit"
        assert datetime.fromisoformat(row["lastChangeAt"]) > datetime.fromisoformat(LAST_UPLOAD_AT)

    def test_a_previous_workflow_execution_id_is_removed(self, update_data):
        table = _edit(update_data)
        assert table.removed_attributes == [["lastChangeWorkflowExecutionId"]]
        assert "lastChangeWorkflowExecutionId" not in table.stored()

    def test_the_last_upload_instant_is_left_as_it_is(self, update_data):
        table = _edit(update_data)
        assert "lastUploadAt" not in table.updated_attributes[0]
        assert table.stored()["lastUploadAt"] == LAST_UPLOAD_AT

    def test_only_the_edited_field_and_the_provenance_are_written(self, update_data):
        """The sibling-write property of the targeted update holds with the provenance added."""
        table = _edit(update_data)
        assert table.put_item_calls == []
        assert set(table.updated_attributes[0]) == set(update_data) | PROVENANCE


@pytest.mark.unit
class TestAnEditOfNoStampedFieldIsNotStamped:

    @pytest.mark.parametrize("update_data", [
        {"assetName": "Name"},
        {"description": "original"},
        {"tags": ["keep"]},
        {"assetName": "Name", "description": "original", "tags": ["keep"]},
        {"isDistributable": True},
    ], ids=["same-name", "same-description", "same-tags", "all-three-unchanged",
            "isDistributable-only"])
    def test_no_provenance_is_written(self, update_data):
        table = _edit(update_data)
        assert table.updated_attributes == [update_data]
        assert table.removed_attributes == [[]]
        row = table.stored()
        assert row["lastChangeSource"] == "workflowExecution"
        assert row["lastChangeAt"] == LAST_UPLOAD_AT
        assert row["lastChangeWorkflowExecutionId"] == "exec-1"

    def test_reordered_tags_are_not_a_change(self):
        table = FakeAssetTable(_asset(tags=["a", "b"]))
        with _tag_validation_stubbed():
            _run_update_asset(table, _asset(tags=["a", "b"]), {"tags": ["b", "a"]})
        assert table.updated_attributes == [{"tags": ["b", "a"]}]

    def test_an_empty_tag_list_on_an_untagged_asset_is_not_a_change(self):
        untagged = _asset()
        untagged.pop("tags")
        table = FakeAssetTable(dict(untagged))
        with _tag_validation_stubbed():
            _run_update_asset(table, dict(untagged), {"tags": []})
        assert table.updated_attributes == [{"tags": []}]


@pytest.mark.unit
class TestTheProvenanceHelper:
    """`asset_edit_provenance` on its own: the stored row is compared before the edit is applied."""

    @staticmethod
    def _helper():
        from common.assetProvenance import asset_edit_provenance
        return asset_edit_provenance

    def test_a_changed_field_returns_the_stamp_and_the_removal(self):
        attributes, removed = self._helper()({"assetName": "A"}, {"assetName": "B"})
        assert attributes["lastChangeSource"] == "assetEdit"
        assert set(attributes) == PROVENANCE
        assert removed == ["lastChangeWorkflowExecutionId"]

    def test_an_unchanged_edit_returns_nothing(self):
        assert self._helper()({"assetName": "A"}, {"assetName": "A"}) == ({}, [])

    def test_a_field_set_where_the_row_had_none_is_a_change(self):
        attributes, _ = self._helper()({}, {"description": "first description"})
        assert attributes["lastChangeSource"] == "assetEdit"

    def test_the_source_is_not_a_workflow_execution_write(self):
        from common.assetProvenance import ASSET_CHANGE_SOURCE_ASSET_EDIT
        from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
        assert ASSET_CHANGE_SOURCE_ASSET_EDIT == "assetEdit"
        assert ASSET_CHANGE_SOURCE_ASSET_EDIT != VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
