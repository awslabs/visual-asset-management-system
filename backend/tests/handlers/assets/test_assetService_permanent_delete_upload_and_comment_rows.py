# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A permanent delete removes every upload record and every comment of the asset.

Both are found with a query and every row found is deleted, so the query is paged to exhaustion:
a row past the first page would otherwise outlive the asset. An upload record is deleted only when
it names the asset's database. Comments are read from the asset's own partitions, live and
soft-deleted, keyed by its original database id (#1860), so a same-id asset in another database
keeps its comments.
"""

from unittest.mock import MagicMock

import pytest

from backend.tests.pagingStub import Pager
from tests.handlers.assets.test_assetService_history import _asset, _load
from tests.handlers.assets.test_assetService_permanent_delete_notification import (
    _delete_permanently, _prepare)


def _upload(upload_id, database_id="db1"):
    return {"uploadId": upload_id, "assetId": "a1", "databaseId": database_id}


LIVE_PARTITION = "db1:a1"
DELETED_PARTITION = "db1:a1#deleted"


def _comment(comment_id, partition=LIVE_PARTITION):
    return {"databaseId:assetId": partition, "commentId": comment_id}


def _partition_of(kwargs):
    """The partition value a comment query's key condition names."""
    return kwargs["KeyConditionExpression"].get_expression()["values"][1]


@pytest.mark.unit
class TestPermanentDeleteRemovesEveryUploadAndComment:
    def _run(self, monkeypatch, upload_pages, comment_pages, deleted_comment_pages=({"Items": []},)):
        m = _load()
        _prepare(m, monkeypatch, _asset(db="db1#deleted", status="archived"))
        uploads, comments = MagicMock(name="uploads"), MagicMock(name="comments")
        upload_pager = Pager(*upload_pages, name="uploads by asset")
        comment_pager = Pager(*comment_pages, name="comments by asset")
        deleted_pager = Pager(*deleted_comment_pages, name="soft-deleted comments by asset")
        uploads.query.side_effect = upload_pager
        pagers = {LIVE_PARTITION: comment_pager, DELETED_PARTITION: deleted_pager}
        comments.query.side_effect = lambda **kwargs: pagers[_partition_of(kwargs)](**kwargs)
        monkeypatch.setattr(m, "asset_upload_table", uploads, raising=False)
        monkeypatch.setattr(m, "comment_table", comments, raising=False)
        assert _delete_permanently(m).success is True
        return uploads, comments, upload_pager, comment_pager

    def test_rows_past_the_first_page_are_deleted(self, monkeypatch):
        uploads, comments, upload_pager, comment_pager = self._run(
            monkeypatch,
            upload_pages=[{"Items": [_upload("u-1")], "LastEvaluatedKey": {"uploadId": "u-1"}},
                          {"Items": [_upload("u-2")]}],
            comment_pages=[{"Items": [_comment("c-1")], "LastEvaluatedKey": {"k": "c-1"}},
                           {"Items": [_comment("c-2")]}],
            deleted_comment_pages=[{"Items": [_comment("c-3", DELETED_PARTITION)],
                                    "LastEvaluatedKey": {"k": "c-3"}},
                                   {"Items": [_comment("c-4", DELETED_PARTITION)]}])
        upload_pager.assert_paged_to_exhaustion()
        comment_pager.assert_paged_to_exhaustion()
        assert {c.kwargs["Key"]["uploadId"] for c in uploads.delete_item.call_args_list} >= {"u-1", "u-2"}
        deleted = {(c.kwargs["Key"]["databaseId:assetId"], c.kwargs["Key"]["commentId"])
                   for c in comments.delete_item.call_args_list}
        assert deleted >= {(LIVE_PARTITION, "c-1"), (LIVE_PARTITION, "c-2"),
                           (DELETED_PARTITION, "c-3"), (DELETED_PARTITION, "c-4")}

    def test_only_the_assets_own_partitions_are_read(self, monkeypatch):
        _, comments, *_ = self._run(monkeypatch, upload_pages=[{"Items": []}], comment_pages=[{"Items": []}])
        read = {_partition_of(c.kwargs) for c in comments.query.call_args_list}
        # Control: both partitions were read
        assert read == {LIVE_PARTITION, DELETED_PARTITION}

    def test_an_upload_record_of_a_same_id_asset_in_another_database_is_kept(self, monkeypatch):
        # Only the upload record that names the asset's own database is deleted.
        uploads, _, upload_pager, _ = self._run(
            monkeypatch,
            upload_pages=[{"Items": [_upload("u-own"), _upload("u-other", database_id="db2")]}],
            comment_pages=[{"Items": []}])
        upload_pager.assert_paged_to_exhaustion()
        deleted = {c.kwargs["Key"]["uploadId"] for c in uploads.delete_item.call_args_list}
        assert "u-own" in deleted
        assert "u-other" not in deleted

    def test_an_asset_with_no_uploads_or_comments_deletes_none(self, monkeypatch):
        uploads, comments, *_ = self._run(monkeypatch, upload_pages=[{"Items": []}],
                                          comment_pages=[{"Items": []}])
        uploads.delete_item.assert_not_called()
        comments.delete_item.assert_not_called()


@pytest.mark.unit
class TestPermanentDeleteRemovesTheAssetsOwnSubscription:
    def test_the_subscription_row_is_keyed_by_the_original_database(self, monkeypatch):
        m = _load()
        _prepare(m, monkeypatch, _asset(db="db1#deleted", status="archived"))
        assert _delete_permanently(m).success is True
        deleted = {(c.kwargs["Key"]["eventName"], c.kwargs["Key"]["entityName:databaseId:entityId"])
                   for c in m.subscription_table.delete_item.call_args_list}
        # The asset's own row is deleted, and no row of another database's same-id asset
        assert ("Asset Version Change", "Asset:db1:a1") in deleted
        assert all(sort_key.split(":")[1] == "db1" for _, sort_key in deleted)
