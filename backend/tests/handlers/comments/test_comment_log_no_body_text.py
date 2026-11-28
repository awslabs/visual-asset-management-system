# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The comment handlers log ids and a length, never the comment text (#1552).

Adding, editing and deleting a comment used to log the whole comment item at INFO, and the item
holds `commentBody`. Each test drives the real function with a distinctive phrase in the stored or
submitted comment and asserts the phrase is in no logger call and that the outcome is unchanged.
"""

import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

import backend.backend.handlers.comments.addComment as add_svc
import backend.backend.handlers.comments.commentService as comment_svc


def _import_edit_comment():
    # See test_comment_audit.py: editComment imports its sibling by the deployed module name.
    package = ModuleType("handlers.comments")
    package.commentService = comment_svc
    saved = {name: sys.modules.get(name) for name in ("handlers.comments", "handlers.comments.commentService")}
    sys.modules["handlers.comments"] = package
    sys.modules["handlers.comments.commentService"] = comment_svc
    try:
        return importlib.import_module("backend.backend.handlers.comments.editComment")
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


edit_svc = _import_edit_comment()

PHRASE = "zq1552-comment-phrase-91ab"
STORED_PHRASE = "zq1552-stored-phrase-92bc"
DATABASE_ID = "db-01"
ASSET_ID = "asset-01"
VERSION_ID = "ver-01"
COMMENT_ID = "cmt-0001"


def _logged_text(logger):
    """Every call made on the mocked logger, as one string."""
    return "\n".join(str(call) for call in logger.method_calls)


def _stored_comment(owner="alice"):
    return {"databaseId:assetId": f"{DATABASE_ID}:{ASSET_ID}", "commentId": COMMENT_ID,
            "databaseId": DATABASE_ID, "assetId": ASSET_ID, "assetVersionId": VERSION_ID, "commentBody": STORED_PHRASE,
            "commentOwnerID": owner, "commentOwnerUsername": owner, "dateCreated": "2026-11-16T00:00:00.000000Z"}


@pytest.mark.unit
def test_add_comment_logs_ids_and_length_only(monkeypatch):
    monkeypatch.setattr(add_svc, "logger", MagicMock())
    monkeypatch.setattr(add_svc, "dynamodb", MagicMock())
    result = add_svc.add_comment(DATABASE_ID, ASSET_ID, VERSION_ID, PHRASE, "alice")
    assert result["statusCode"] == 200 and result["message"] == "Succeeded"
    text = _logged_text(add_svc.logger)
    assert PHRASE not in text
    assert ASSET_ID in text and result["commentId"] in text and f"{len(PHRASE)} char(s)" in text
    # the comment is still stored with its text
    stored = add_svc.dynamodb.Table.return_value.put_item.call_args.kwargs["Item"]
    assert stored["commentBody"] == PHRASE


@pytest.mark.unit
@pytest.mark.parametrize("owner, status", [("alice", 200), ("mallory", 403)])
def test_edit_comment_logs_neither_stored_nor_new_text(monkeypatch, owner, status):
    monkeypatch.setattr(edit_svc, "logger", MagicMock())
    monkeypatch.setattr(edit_svc, "dynamodb", MagicMock())
    monkeypatch.setattr(edit_svc, "get_single_comment", lambda *args, **kwargs: _stored_comment(owner))
    result = edit_svc.edit_comment(DATABASE_ID, ASSET_ID, COMMENT_ID, "alice", PHRASE)
    assert result["statusCode"] == status
    text = _logged_text(edit_svc.logger)
    assert PHRASE not in text and STORED_PHRASE not in text
    assert COMMENT_ID in text
    if status == 200:
        assert result["previousCommentBody"] == STORED_PHRASE  # the response is unchanged


@pytest.mark.unit
@pytest.mark.parametrize("owner, status", [("alice", 200), ("mallory", 403)])
def test_delete_comment_logs_no_comment_text(monkeypatch, owner, status):
    monkeypatch.setattr(comment_svc, "logger", MagicMock())
    monkeypatch.setattr(comment_svc, "dynamodb", MagicMock())
    monkeypatch.setattr(comment_svc, "dynamodb_client", MagicMock())
    monkeypatch.setattr(comment_svc, "get_single_comment", lambda *args, **kwargs: _stored_comment(owner))
    result = comment_svc.delete_comment(DATABASE_ID, ASSET_ID, COMMENT_ID, "alice")
    assert result["statusCode"] == status
    text = _logged_text(comment_svc.logger)
    assert STORED_PHRASE not in text
    assert COMMENT_ID in text
