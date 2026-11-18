# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A permanent delete removes every upload record and every comment of the asset.

Both are found with a query (the upload table's AssetIdGSI, the comment table's assetId partition)
and every row found is deleted, so the query is paged to exhaustion: a row past the first page
would otherwise outlive the asset.
"""

from unittest.mock import MagicMock

import pytest

from backend.tests.pagingStub import Pager
from tests.handlers.assets.test_assetService_history import _asset, _load
from tests.handlers.assets.test_assetService_permanent_delete_notification import (
    _delete_permanently, _prepare)


def _upload(upload_id):
    return {"uploadId": upload_id, "assetId": "a1"}


def _comment(comment_key):
    return {"assetId": "a1", "assetVersionId:commentId": comment_key}


@pytest.mark.unit
class TestPermanentDeleteRemovesEveryUploadAndComment:
    def _run(self, monkeypatch, upload_pages, comment_pages):
        m = _load()
        _prepare(m, monkeypatch, _asset(db="db1#deleted", status="archived"))
        uploads, comments = MagicMock(name="uploads"), MagicMock(name="comments")
        upload_pager = Pager(*upload_pages, name="uploads by asset")
        comment_pager = Pager(*comment_pages, name="comments by asset")
        uploads.query.side_effect = upload_pager
        comments.query.side_effect = comment_pager
        monkeypatch.setattr(m, "asset_upload_table", uploads, raising=False)
        monkeypatch.setattr(m, "comment_table", comments, raising=False)
        assert _delete_permanently(m).success is True
        return uploads, comments, upload_pager, comment_pager

    def test_rows_past_the_first_page_are_deleted(self, monkeypatch):
        uploads, comments, upload_pager, comment_pager = self._run(
            monkeypatch,
            upload_pages=[{"Items": [_upload("u-1")], "LastEvaluatedKey": {"uploadId": "u-1"}},
                          {"Items": [_upload("u-2")]}],
            comment_pages=[{"Items": [_comment("1:c-1")], "LastEvaluatedKey": {"k": "c-1"}},
                           {"Items": [_comment("1:c-2")]}])
        upload_pager.assert_paged_to_exhaustion()
        comment_pager.assert_paged_to_exhaustion()
        assert {c.kwargs["Key"]["uploadId"] for c in uploads.delete_item.call_args_list} >= {"u-1", "u-2"}
        assert {c.kwargs["Key"]["assetVersionId:commentId"]
                for c in comments.delete_item.call_args_list} >= {"1:c-1", "1:c-2"}

    def test_an_asset_with_no_uploads_or_comments_deletes_none(self, monkeypatch):
        uploads, comments, *_ = self._run(monkeypatch, upload_pages=[{"Items": []}],
                                          comment_pages=[{"Items": []}])
        uploads.delete_item.assert_not_called()
        comments.delete_item.assert_not_called()
