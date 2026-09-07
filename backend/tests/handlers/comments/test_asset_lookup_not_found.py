# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unresolvable-asset handling of the asset lookup in the comments handlers (S2-BACKEND-077 / FIX-033).

``common.dynamodb.get_asset_object_from_id`` signals an assetId it cannot resolve to one live
asset in two different ways, and the no-databaseId path these handlers use produces both:

* it **returns None** when nothing live matches -- no such asset, or every match archived;
* it **raises** ``VAMSGeneralErrorResponse`` when the assetId matches more than one live asset.
  assetIds are unique within a database, not across databases, so that match cannot be
  resolved without a databaseId and the ambiguity is surfaced rather than guessed at.

Each of the four comment call sites annotates the returned record with ``object__type``
immediately before the Casbin Tier-2 ``enforce()`` call, so an unguarded ``None`` raises
``AttributeError: 'NoneType' object has no attribute 'update'``, and an unguarded
``VAMSGeneralErrorResponse`` falls through to the handler's generic ``except Exception``.
Both answer 500. The four sites are ``addComment.lambda_handler``,
``commentService.get_handler``, ``commentService.delete_handler``, and
``editComment.lambda_handler``.

Every site is covered three ways, because a guard that denied unconditionally would satisfy
every negative assertion while silently disabling comments:

* the ``None`` case asserts 404 (not 500, not 200), asserts the response carries no echo of
  the submitted assetId (backend/CLAUDE.md Rule 11), and asserts Tier-2 ``enforce()`` was
  never reached -- the denial has to precede enforcement, not follow an annotation of ``None``;
* the ambiguous case asserts 400 (not 500) and that the lookup's own reason reaches the
  caller, so the actionable "provide a database ID" hint is not swallowed;
* the positive control makes the same call with a real asset record and asserts the handler
  still reaches its normal outcome, and that ``enforce()`` received an object annotated
  ``object__type='asset'``.
"""

import json
import sys
import types

import boto3
import pytest

from backend.tests.conftest import TestComment  # noqa: F401

import backend.backend.handlers.comments.addComment as addComment
import backend.backend.handlers.comments.commentService as commentService

# `editComment.py` does `from handlers.comments.commentService import get_single_comment`, and
# the root conftest registers `handlers` as a bare mock without a `comments` submodule - so that
# import fails with "'handlers' is not a package" and this module cannot even be collected on its
# own. No other test in the suite imports editComment (its sibling's cases are all skipped), so
# nothing established this path before.
#
# Registering the REAL commentService under the `handlers.` name is what the test needs anyway,
# not merely a way to satisfy the import: editComment then holds the same module object the
# fixtures patch, so patching `commentService.dynamodb` reaches the code that reads the comment
# record. Binding a mock here instead would leave the fixture patching one module while the
# handler read another.
if "handlers.comments" not in sys.modules:
    _pkg = types.ModuleType("handlers.comments")
    _pkg.__path__ = []  # marks it a package so the submodule import resolves
    sys.modules["handlers.comments"] = _pkg
sys.modules["handlers.comments.commentService"] = commentService

import backend.backend.handlers.comments.editComment as editComment  # noqa: E402

_CALLER = "test_email@amazon.com"
_OTHER_USER = "someone_else@amazon.com"
_COMMENT_TABLE = "commentStorageTable"

# Distinctive so an assertion that the response does not echo it cannot pass by accident.
_MISSING_ASSET_ID = "missing-asset-9f3c"
_AMBIGUOUS_ASSET_ID = "ambiguous-asset-7b2e"
_LIVE_ASSET_ID = "test-id"
_VERSION_AND_COMMENT_ID = "test-version-id:test-comment-id"

# The lookup's own wording for an assetId matching more than one live asset. It names no
# caller-supplied value, which is what makes it safe to return verbatim under Rule 11.
_AMBIGUOUS_MESSAGE = "Asset ID matches more than one asset. Provide a database ID."


def _asset_object(asset_id):
    """An asset record shaped like the real no-databaseId lookup's return value."""
    return {
        "object__type": "asset",
        "assetId": asset_id,
        "assetName": "Test Asset",
        "databaseId": "unit-test-db",
        "assetType": "model/gltf-binary",
        "tags": [],
    }


def _comment(asset_id=_LIVE_ASSET_ID, owner=_CALLER, body="test comment body"):
    return {
        "assetId": asset_id,
        "assetVersionId:commentId": _VERSION_AND_COMMENT_ID,
        "commentBody": body,
        "commentOwnerID": owner,
        "commentOwnerUsername": owner,
        "dateCreated": "2023-07-06T21:32:15.066148Z",
    }


def _raise_ambiguous(module):
    """A lookup stand-in that raises the handler module's own VAMSGeneralErrorResponse.

    The class is taken off the module under test rather than imported here: `models.common`
    and `backend.backend.models.common` load from the same file as two distinct module
    objects, so a class imported by the other path would not be caught by the `except` clause
    the handler actually names, and the test would measure the generic 500 instead.
    """

    def _lookup(database_id, asset_id):
        raise module.VAMSGeneralErrorResponse(_AMBIGUOUS_MESSAGE)

    return _lookup


def _route_path(asset_id):
    """The registered single-comment route (apiRoutes.API_COMMENTS_ASSET_VERSION_COMMENT)."""
    return f"/comments/assets/{asset_id}/assetVersionId:commentId/{_VERSION_AND_COMMENT_ID}"


def _rest_event(method, path, path_parameters, body=None, query=None):
    """An API Gateway REST (v1) proxy event, the shape these handlers actually receive."""
    event = {
        "path": path,
        "httpMethod": method,
        "resource": path,
        "headers": {"Content-Type": "application/json"},
        "requestContext": {
            "identity": {"sourceIp": "10.0.0.1"},
            "authorizer": {"jwt": {"claims": {"sub": "test_sub", "email": _CALLER}}},
        },
        "pathParameters": path_parameters,
        "queryStringParameters": query,
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _install_claims(monkeypatch, module, user=_CALLER):
    monkeypatch.setattr(module, "request_to_claims", lambda event: {"tokens": [user]})


def _install_enforcer(monkeypatch, module, calls, enforce_result=True):
    """Replace the module's CasbinEnforcer with one that records Tier-2 enforce() calls.

    enforceAPI always allows, so Tier-1 never masks what is being measured here; every
    entry appended to `calls` is one object that reached Tier-2 object authorization.
    """

    class _RecordingEnforcer:
        def __init__(self, claims_and_roles):
            pass

        def enforce(self, asset_object, action=None):
            calls.append((asset_object, action))
            return enforce_result

        def enforceAPI(self, event):
            return True

    monkeypatch.setattr(module, "CasbinEnforcer", _RecordingEnforcer)


@pytest.fixture
def add_comment_module(monkeypatch, ddb_resource, comments_table):
    monkeypatch.setattr(addComment, "dynamodb", ddb_resource)
    monkeypatch.setattr(addComment, "comment_database", _COMMENT_TABLE)
    _install_claims(monkeypatch, addComment)
    return addComment


@pytest.fixture
def comment_service_module(monkeypatch, ddb_resource, comments_table):
    monkeypatch.setattr(commentService, "dynamodb", ddb_resource)
    # A standalone low-level client, not ddb_resource.meta.client: the resource registers
    # boto3's attribute-value transformations on its own client, which would re-serialize the
    # already-serialized items the soft delete hands to transact_write_items.
    monkeypatch.setattr(
        commentService, "dynamodb_client", boto3.client("dynamodb", region_name="us-east-1")
    )
    monkeypatch.setattr(commentService, "comment_database", _COMMENT_TABLE)
    _install_claims(monkeypatch, commentService)
    return commentService


@pytest.fixture
def edit_comment_module(monkeypatch, ddb_resource, comments_table):
    # edit_comment reads the record through commentService.get_single_comment, which uses
    # commentService's own module globals, and writes through editComment's.
    monkeypatch.setattr(editComment, "dynamodb", ddb_resource)
    monkeypatch.setattr(editComment, "comment_database", _COMMENT_TABLE)
    monkeypatch.setattr(commentService, "dynamodb", ddb_resource)
    monkeypatch.setattr(commentService, "comment_database", _COMMENT_TABLE)
    _install_claims(monkeypatch, editComment)
    return editComment


def _assert_not_found(response, calls):
    """A not-found asset must deny with 404, generically, before Tier-2 enforcement."""
    assert response["statusCode"] == 404
    body = json.loads(response["body"])
    assert body["message"] == "Asset not found"
    assert _MISSING_ASSET_ID not in response["body"]
    assert calls == []


def _assert_ambiguous(response, calls):
    """An assetId matching more than one live asset must be a 4xx carrying the reason."""
    assert response["statusCode"] == 400
    body = json.loads(response["body"])
    assert _AMBIGUOUS_MESSAGE in body["message"]
    assert _AMBIGUOUS_ASSET_ID not in response["body"]
    assert calls == []


def _annotated_objects(calls):
    return [asset_object for asset_object, _action in calls]


@pytest.mark.unit
class TestAddCommentAssetLookup:
    """POST /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}

    Handled by addComment.lambda_handler.
    """

    def test_returns_404_when_asset_does_not_resolve(self, add_comment_module, monkeypatch,
                                                     comments_table):
        calls = []
        _install_enforcer(monkeypatch, addComment, calls)
        monkeypatch.setattr(addComment, "get_asset_object_from_id", lambda db, asset: None)

        response = addComment.lambda_handler(
            _rest_event(
                "POST",
                _route_path(_MISSING_ASSET_ID),
                {"assetId": _MISSING_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID},
                body={"commentBody": "test comment body"},
            ),
            None,
        )

        _assert_not_found(response, calls)
        stored = comments_table.get_item(
            Key={"assetId": _MISSING_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID}
        )
        assert "Item" not in stored

    def test_returns_400_when_asset_id_is_ambiguous(self, add_comment_module, monkeypatch,
                                                    comments_table):
        calls = []
        _install_enforcer(monkeypatch, addComment, calls)
        monkeypatch.setattr(
            addComment, "get_asset_object_from_id", _raise_ambiguous(addComment)
        )

        response = addComment.lambda_handler(
            _rest_event(
                "POST",
                _route_path(_AMBIGUOUS_ASSET_ID),
                {"assetId": _AMBIGUOUS_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID},
                body={"commentBody": "test comment body"},
            ),
            None,
        )

        _assert_ambiguous(response, calls)
        stored = comments_table.get_item(
            Key={"assetId": _AMBIGUOUS_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID}
        )
        assert "Item" not in stored

    def test_adds_comment_when_asset_resolves(self, add_comment_module, monkeypatch,
                                              comments_table):
        calls = []
        _install_enforcer(monkeypatch, addComment, calls)
        monkeypatch.setattr(
            addComment, "get_asset_object_from_id", lambda db, asset: _asset_object(asset)
        )

        response = addComment.lambda_handler(
            _rest_event(
                "POST",
                _route_path(_LIVE_ASSET_ID),
                {"assetId": _LIVE_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID},
                body={"commentBody": "test comment body"},
            ),
            None,
        )

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "Succeeded"
        assert [obj["object__type"] for obj in _annotated_objects(calls)] == ["asset"]
        stored = comments_table.get_item(
            Key={"assetId": _LIVE_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID}
        )["Item"]
        assert stored["commentBody"] == "test comment body"
        assert stored["commentOwnerID"] == _CALLER


@pytest.mark.unit
class TestCommentServiceGetAssetLookup:
    """GET /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}

    Handled by commentService.get_handler.
    """

    def test_returns_404_when_asset_does_not_resolve(self, comment_service_module, monkeypatch):
        calls = []
        _install_enforcer(monkeypatch, commentService, calls)
        monkeypatch.setattr(commentService, "get_asset_object_from_id", lambda db, asset: None)

        response = commentService.lambda_handler(
            _rest_event(
                "GET",
                _route_path(_MISSING_ASSET_ID),
                {"assetId": _MISSING_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID},
                query={"maxItems": "10", "pageSize": "10", "startingToken": ""},
            ),
            None,
        )

        _assert_not_found(response, calls)

    def test_returns_400_when_asset_id_is_ambiguous(self, comment_service_module, monkeypatch):
        calls = []
        _install_enforcer(monkeypatch, commentService, calls)
        monkeypatch.setattr(
            commentService, "get_asset_object_from_id", _raise_ambiguous(commentService)
        )

        response = commentService.lambda_handler(
            _rest_event(
                "GET",
                _route_path(_AMBIGUOUS_ASSET_ID),
                {"assetId": _AMBIGUOUS_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID},
                query={"maxItems": "10", "pageSize": "10", "startingToken": ""},
            ),
            None,
        )

        _assert_ambiguous(response, calls)

    def test_returns_comment_when_asset_resolves(self, comment_service_module, monkeypatch,
                                                 comments_table):
        comments_table.put_item(Item=_comment())
        calls = []
        _install_enforcer(monkeypatch, commentService, calls)
        monkeypatch.setattr(
            commentService, "get_asset_object_from_id", lambda db, asset: _asset_object(asset)
        )

        response = commentService.lambda_handler(
            _rest_event(
                "GET",
                _route_path(_LIVE_ASSET_ID),
                {"assetId": _LIVE_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID},
                query={"maxItems": "10", "pageSize": "10", "startingToken": ""},
            ),
            None,
        )

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == _comment()
        assert [obj["object__type"] for obj in _annotated_objects(calls)] == ["asset"]


@pytest.mark.unit
class TestCommentServiceDeleteAssetLookup:
    """DELETE /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}

    Handled by commentService.delete_handler.
    """

    def _delete_event(self, asset_id):
        return _rest_event(
            "DELETE",
            _route_path(asset_id),
            {"assetId": asset_id, "assetVersionId:commentId": _VERSION_AND_COMMENT_ID},
            query={"maxItems": "10", "pageSize": "10", "startingToken": ""},
        )

    def test_returns_404_when_asset_does_not_resolve(self, comment_service_module, monkeypatch):
        calls = []
        _install_enforcer(monkeypatch, commentService, calls)
        monkeypatch.setattr(commentService, "get_asset_object_from_id", lambda db, asset: None)

        response = commentService.lambda_handler(self._delete_event(_MISSING_ASSET_ID), None)

        _assert_not_found(response, calls)

    def test_returns_400_when_asset_id_is_ambiguous(self, comment_service_module, monkeypatch):
        calls = []
        _install_enforcer(monkeypatch, commentService, calls)
        monkeypatch.setattr(
            commentService, "get_asset_object_from_id", _raise_ambiguous(commentService)
        )

        response = commentService.lambda_handler(self._delete_event(_AMBIGUOUS_ASSET_ID), None)

        _assert_ambiguous(response, calls)

    def test_deletes_comment_when_asset_resolves(self, comment_service_module, monkeypatch,
                                                 comments_table):
        comments_table.put_item(Item=_comment())
        calls = []
        _install_enforcer(monkeypatch, commentService, calls)
        monkeypatch.setattr(
            commentService, "get_asset_object_from_id", lambda db, asset: _asset_object(asset)
        )

        response = commentService.lambda_handler(self._delete_event(_LIVE_ASSET_ID), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "Comment deleted"
        assert [obj["object__type"] for obj in _annotated_objects(calls)] == ["asset"]
        # Soft delete rewrites the record under the "#deleted" partition.
        assert "Item" not in comments_table.get_item(
            Key={"assetId": _LIVE_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID}
        )
        assert comments_table.get_item(
            Key={"assetId": _LIVE_ASSET_ID + "#deleted",
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID}
        )["Item"]["commentBody"] == "test comment body"

    def test_rejects_non_owner_when_asset_resolves(self, comment_service_module, monkeypatch,
                                                   comments_table):
        """Second positive control: the delete path's own ownership check still runs."""
        comments_table.put_item(Item=_comment(owner=_OTHER_USER))
        calls = []
        _install_enforcer(monkeypatch, commentService, calls)
        monkeypatch.setattr(
            commentService, "get_asset_object_from_id", lambda db, asset: _asset_object(asset)
        )

        response = commentService.lambda_handler(self._delete_event(_LIVE_ASSET_ID), None)

        assert response["statusCode"] == 403
        assert json.loads(response["body"])["message"] == (
            "Unauthorized - only the creator of the comment can delete it"
        )
        assert [obj["object__type"] for obj in _annotated_objects(calls)] == ["asset"]


@pytest.mark.unit
class TestEditCommentAssetLookup:
    """PUT /comments/assets/{assetId}/assetVersionId:commentId/{assetVersionId:commentId}

    Handled by editComment.lambda_handler.
    """

    def _edit_event(self, asset_id, body="edited comment body"):
        return _rest_event(
            "PUT",
            _route_path(asset_id),
            {"assetId": asset_id, "assetVersionId:commentId": _VERSION_AND_COMMENT_ID},
            body={"commentBody": body},
        )

    def test_returns_404_when_asset_does_not_resolve(self, edit_comment_module, monkeypatch,
                                                     comments_table):
        comments_table.put_item(Item=_comment(asset_id=_MISSING_ASSET_ID))
        calls = []
        _install_enforcer(monkeypatch, editComment, calls)
        monkeypatch.setattr(editComment, "get_asset_object_from_id", lambda db, asset: None)

        response = editComment.lambda_handler(self._edit_event(_MISSING_ASSET_ID), None)

        _assert_not_found(response, calls)
        # The stored comment is untouched: the denial precedes the edit.
        assert comments_table.get_item(
            Key={"assetId": _MISSING_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID}
        )["Item"]["commentBody"] == "test comment body"

    def test_returns_400_when_asset_id_is_ambiguous(self, edit_comment_module, monkeypatch,
                                                    comments_table):
        comments_table.put_item(Item=_comment(asset_id=_AMBIGUOUS_ASSET_ID))
        calls = []
        _install_enforcer(monkeypatch, editComment, calls)
        monkeypatch.setattr(
            editComment, "get_asset_object_from_id", _raise_ambiguous(editComment)
        )

        response = editComment.lambda_handler(self._edit_event(_AMBIGUOUS_ASSET_ID), None)

        _assert_ambiguous(response, calls)
        # The stored comment is untouched: the rejection precedes the edit.
        assert comments_table.get_item(
            Key={"assetId": _AMBIGUOUS_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID}
        )["Item"]["commentBody"] == "test comment body"

    def test_edits_comment_when_asset_resolves(self, edit_comment_module, monkeypatch,
                                               comments_table):
        comments_table.put_item(Item=_comment())
        calls = []
        _install_enforcer(monkeypatch, editComment, calls)
        monkeypatch.setattr(
            editComment, "get_asset_object_from_id", lambda db, asset: _asset_object(asset)
        )

        response = editComment.lambda_handler(self._edit_event(_LIVE_ASSET_ID), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "Succeeded"
        assert [obj["object__type"] for obj in _annotated_objects(calls)] == ["asset"]
        assert comments_table.get_item(
            Key={"assetId": _LIVE_ASSET_ID,
                 "assetVersionId:commentId": _VERSION_AND_COMMENT_ID}
        )["Item"]["commentBody"] == "edited comment body"
