# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The comment handlers record each action after it succeeded, and nothing when it did not.

A comment write records the comment body it wrote (the maintainers' rule: a write records what it
wrote, with the log redaction applied), and the asset's database, resolved from the asset record the
handler already read for its check.
"""

import importlib
import json
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

import backend.backend.handlers.comments.addComment as add_svc
import backend.backend.handlers.comments.commentService as comment_svc


def _import_edit_comment():
    # editComment imports its sibling by its deployed name (handlers.comments.commentService), which
    # the test tree's handlers stand-in does not provide; register the sibling under that name.
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

CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": False}
ASSET = {"assetId": "asset-01", "databaseId": "db1"}


def _event(method, body=None, path=None, query=None):
    return {"body": body, "queryStringParameters": query or {},
            "pathParameters": path or {"assetId": "asset-01", "assetVersionId:commentId": "ver-01:cmt-0001"},
            "requestContext": {"http": {"method": method,
                                        "path": "/comments/assets/asset-01/assetVersionId:commentId/ver-01:cmt-0001"}}}


@pytest.fixture
def wired(monkeypatch):
    recorder = MagicMock()
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = True
    enforcer.return_value.enforce.return_value = True
    for module in (add_svc, edit_svc, comment_svc):
        monkeypatch.setattr(module, "log_action", recorder)
        monkeypatch.setattr(module, "request_to_claims", lambda event: CLAIMS)
        monkeypatch.setattr(module, "CasbinEnforcer", enforcer)
        monkeypatch.setattr(module, "get_asset_object_from_id", lambda *args, **kwargs: dict(ASSET))
    monkeypatch.setattr(comment_svc, "validate_pagination_info", lambda params: None)
    return recorder, enforcer


@pytest.mark.unit
class TestCommentRecords:
    def test_a_create_records_the_comment(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(add_svc, "add_comment", lambda *args: {"statusCode": 200, "message": "Succeeded"})
        event = _event("POST", {"commentBody": "Looks good"})
        assert add_svc.lambda_handler(event, None)["statusCode"] == 200
        recorder.assert_called_once_with(event, "commentCreate", database_id="db1", asset_id="asset-01",
                                         assetVersionId="ver-01", commentId="cmt-0001",
                                         changes={"commentBody": "Looks good"})

    def test_a_denied_create_records_nothing(self, wired):
        recorder, enforcer = wired
        enforcer.return_value.enforce.return_value = False
        assert add_svc.lambda_handler(_event("POST", {"commentBody": "x"}), None)["statusCode"] == 403
        recorder.assert_not_called()

    def test_a_failed_create_records_nothing(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(add_svc, "add_comment", lambda *args: {"statusCode": 500, "message": "x"})
        add_svc.lambda_handler(_event("POST", {"commentBody": "x"}), None)
        recorder.assert_not_called()

    def test_an_edit_records_the_new_and_the_prior_body(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(edit_svc, "edit_comment", lambda *args: {
            "statusCode": 200, "message": "Succeeded", "previousCommentBody": "Old text"})
        event = _event("PUT", {"commentBody": "New text"})
        assert edit_svc.lambda_handler(event, None)["statusCode"] == 200
        kwargs = recorder.call_args.kwargs
        assert recorder.call_args.args == (event, "commentUpdate")
        assert kwargs["changes"] == {"commentBody": "New text"}
        assert kwargs["previous"] == {"commentBody": "Old text"}
        assert "previousCommentBody" not in json.loads(edit_svc.lambda_handler(event, None)["body"])["message"]

    def test_an_edit_by_someone_else_records_nothing(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(edit_svc, "edit_comment", lambda *args: {"statusCode": 403, "message": "Unauthorized"})
        edit_svc.lambda_handler(_event("PUT", {"commentBody": "New text"}), None)
        recorder.assert_not_called()

    def test_a_delete_is_recorded(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(comment_svc, "delete_comment", lambda *args: {"statusCode": 200, "message": "Deleted"})
        event = _event("DELETE")
        assert comment_svc.lambda_handler(event, None)["statusCode"] == 200
        recorder.assert_called_once_with(event, "commentDelete", database_id="db1", asset_id="asset-01",
                                         assetVersionId="ver-01", commentId="cmt-0001")

    def test_a_list_records_the_request(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(comment_svc, "get_comments", lambda *args: {"Items": []})
        event = _event("GET", path={"assetId": "asset-01"})
        assert comment_svc.lambda_handler(event, None)["statusCode"] == 200
        recorder.assert_called_once_with(event, "commentList", database_id="db1", asset_id="asset-01",
                                         assetVersionId=None, request_parameters={})

    def test_a_get_records_the_asset_database(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(comment_svc, "get_single_comment", lambda *args: {"commentId": "cmt-0001"})
        event = _event("GET")
        assert comment_svc.lambda_handler(event, None)["statusCode"] == 200
        recorder.assert_called_once_with(event, "commentGet", database_id="db1", asset_id="asset-01",
                                         assetVersionId="ver-01", commentId="cmt-0001", request_parameters={})

    def test_an_archived_asset_read_records_its_database(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(comment_svc, "asset_authorization_object", lambda asset, *args, **kwargs: asset)
        monkeypatch.setattr(comment_svc, "get_comments", lambda *args: {"Items": []})
        event = _event("GET", path={"assetId": "asset-01"}, query={"showArchived": "true"})
        assert comment_svc.lambda_handler(event, None)["statusCode"] == 200
        assert recorder.call_args.kwargs["database_id"] == "db1"

    def test_a_get_of_a_missing_asset_records_nothing(self, wired, monkeypatch):
        recorder, _ = wired
        monkeypatch.setattr(comment_svc, "get_asset_object_from_id", lambda *args, **kwargs: None)
        assert comment_svc.lambda_handler(_event("GET", path={"assetId": "asset-01"}), None)["statusCode"] == 404
        recorder.assert_not_called()
