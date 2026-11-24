# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Comments are stored and addressed by database and asset id (#1860).

Two databases may hold assets with the same asset id. Every comment route names both ids
(`/comments/database/{databaseId}/assets/{assetId}[/{commentId}]`), the comment table is keyed by
`databaseId:assetId` + `commentId`, and Tier 2 is decided on the asset of the named database before
the handler reveals whether it exists. These tests drive the three comment handlers against a moto
table of the deployed shape with two same-id assets in databases A and B:

* each request reads and writes only its own database's comments (list, version filter, get, edit,
  soft delete);
* a caller refused on database B gets the same 403 for an asset that exists there and one that does
  not, and the Tier 2 object carries database B, never the other database's record;
* an archived asset's comments are read with showArchived, keyed by the original database id;
* a create returns a server-generated comment id and stores the target version as an attribute.
"""

import json
import sys
import types

import boto3
import pytest

from backend.tests.conftest import TestComment
from backend.backend.common.dynamodb import asset_authorization_object as real_authorization_object

import backend.backend.handlers.comments.addComment as addComment
import backend.backend.handlers.comments.commentService as commentService

# editComment imports its sibling by the deployed name; register the real module under it so the
# fixtures below patch the module editComment reads through.
if "handlers.comments" not in sys.modules:
    _pkg = types.ModuleType("handlers.comments")
    _pkg.__path__ = []
    sys.modules["handlers.comments"] = _pkg
sys.modules["handlers.comments.commentService"] = commentService

import backend.backend.handlers.comments.editComment as editComment  # noqa: E402

CALLER = "alice@example.com"
TABLE = "commentStorageTable"
DB_A, DB_B = "database-a", "database-b"
SHARED_ID = "xshared-asset"
ARCHIVED_ID = "xarchived-asset"
MISSING_ID = "xmissing-asset-4c1d"

ASSETS = {
    (DB_A, SHARED_ID): {"databaseId": DB_A, "assetId": SHARED_ID, "assetName": "A's asset"},
    (DB_B, SHARED_ID): {"databaseId": DB_B, "assetId": SHARED_ID, "assetName": "B's asset"},
    (f"{DB_A}#deleted", ARCHIVED_ID): {"databaseId": f"{DB_A}#deleted", "assetId": ARCHIVED_ID,
                                       "status": "archived"},
}


def _lookup(database_id, asset_id, include_archived=False):
    """The keyed asset read: the live row, else (with include_archived) the archived one."""
    row = ASSETS.get((database_id, asset_id))
    if row is None and include_archived:
        row = ASSETS.get((f"{database_id}#deleted", asset_id))
    return {**row, "object__type": "asset"} if row else None


class _Enforcer:
    """Tier 1 allows; Tier 2 allows the databases in `allowed`, by the object's database id."""

    allowed = {DB_A, DB_B, f"{DB_A}#deleted"}
    objects = []

    def __init__(self, claims_and_roles):
        pass

    def enforceAPI(self, event):
        return True

    def enforce(self, obj, action=None):
        _Enforcer.objects.append(dict(obj))
        return obj.get("databaseId") in _Enforcer.allowed


@pytest.fixture
def table(monkeypatch, ddb_resource, comments_table):
    _Enforcer.allowed = {DB_A, DB_B, f"{DB_A}#deleted"}
    _Enforcer.objects = []
    client = boto3.client("dynamodb", region_name="us-east-1")
    for module in (addComment, commentService, editComment):
        monkeypatch.setattr(module, "dynamodb", ddb_resource)
        monkeypatch.setattr(module, "comment_database", TABLE)
        monkeypatch.setattr(module, "request_to_claims", lambda event: {"tokens": [CALLER]})
        monkeypatch.setattr(module, "CasbinEnforcer", _Enforcer)
        monkeypatch.setattr(module, "get_asset_object_from_id", _lookup)
        # raising=False: the stand-in is installed whether or not the module imported the helper
        monkeypatch.setattr(module, "asset_authorization_object", real_authorization_object, raising=False)
        monkeypatch.setattr(module, "log_action", lambda *args, **kwargs: None)
    monkeypatch.setattr(commentService, "dynamodb_client", client)
    monkeypatch.setattr(commentService, "validate_pagination_info", lambda params: None)
    for db, comment_id, version, body in ((DB_A, "c-a1", "1", "on A v1"), (DB_A, "c-a2", "2", "on A v2"),
                                          (DB_B, "c-b1", "1", "on B v1")):
        comments_table.put_item(Item=TestComment(database_id=db, asset_id=SHARED_ID, comment_id=comment_id,
                                                 asset_version_id=version, comment_body=body,
                                                 comment_owner_id=CALLER).get_comment())
    comments_table.put_item(Item=TestComment(database_id=DB_A, asset_id=ARCHIVED_ID, comment_id="c-arch",
                                             comment_body="on the archived asset").get_comment())
    return comments_table


def _event(method, database_id, asset_id, comment_id=None, body=None, query=None):
    path = {"databaseId": database_id, "assetId": asset_id}
    route = f"/comments/database/{database_id}/assets/{asset_id}"
    if comment_id:
        path["commentId"] = comment_id
        route += f"/{comment_id}"
    query = dict(query or {})
    query.setdefault("maxItems", "100")
    query.setdefault("pageSize", "100")
    query.setdefault("startingToken", None)
    event = {"requestContext": {"http": {"method": method, "path": route}},
             "pathParameters": path, "queryStringParameters": query}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _message(response):
    return json.loads(response["body"])["message"]


@pytest.mark.unit
class TestEachDatabaseSeesOnlyItsComments:
    def test_list_returns_only_the_named_databases_comments(self, table):
        a = commentService.lambda_handler(_event("GET", DB_A, SHARED_ID), None)
        b = commentService.lambda_handler(_event("GET", DB_B, SHARED_ID), None)
        assert a["statusCode"] == 200 and b["statusCode"] == 200
        assert sorted(c["commentId"] for c in _message(a)) == ["c-a1", "c-a2"]
        assert [c["commentId"] for c in _message(b)] == ["c-b1"]
        # The table's composite key attribute is not part of the response
        assert all("databaseId:assetId" not in c for c in _message(a) + _message(b))
        assert all(c["databaseId"] == DB_A for c in _message(a))

    def test_the_version_filter_reads_one_version_of_one_database(self, table):
        response = commentService.lambda_handler(
            _event("GET", DB_A, SHARED_ID, query={"assetVersionId": "1"}), None)
        assert response["statusCode"] == 200
        assert [c["commentId"] for c in _message(response)] == ["c-a1"]

    def test_get_one_comment_is_keyed_by_the_database(self, table):
        hit = commentService.lambda_handler(_event("GET", DB_B, SHARED_ID, "c-b1"), None)
        miss = commentService.lambda_handler(_event("GET", DB_B, SHARED_ID, "c-a1"), None)
        assert _message(hit)["commentBody"] == "on B v1"
        assert _message(miss) == {}

    def test_edit_changes_only_the_named_databases_comment(self, table):
        response = editComment.lambda_handler(
            _event("PUT", DB_B, SHARED_ID, "c-b1", body={"commentBody": "edited"}), None)
        assert response["statusCode"] == 200
        b_row = table.get_item(Key={"databaseId:assetId": f"{DB_B}:{SHARED_ID}", "commentId": "c-b1"})["Item"]
        a_row = table.get_item(Key={"databaseId:assetId": f"{DB_A}:{SHARED_ID}", "commentId": "c-a1"})["Item"]
        assert b_row["commentBody"] == "edited" and "dateEdited" in b_row
        assert a_row["commentBody"] == "on A v1" and "dateEdited" not in a_row

    def test_editing_another_databases_comment_id_creates_nothing(self, table):
        response = editComment.lambda_handler(
            _event("PUT", DB_B, SHARED_ID, "c-a1", body={"commentBody": "edited"}), None)
        assert response["statusCode"] == 404
        assert "Item" not in table.get_item(Key={"databaseId:assetId": f"{DB_B}:{SHARED_ID}", "commentId": "c-a1"})

    def test_delete_moves_the_comment_to_its_databases_deleted_partition(self, table):
        response = commentService.lambda_handler(_event("DELETE", DB_A, SHARED_ID, "c-a1"), None)
        assert response["statusCode"] == 200
        assert "Item" not in table.get_item(Key={"databaseId:assetId": f"{DB_A}:{SHARED_ID}", "commentId": "c-a1"})
        moved = table.get_item(Key={"databaseId:assetId": f"{DB_A}:{SHARED_ID}#deleted", "commentId": "c-a1"})
        assert moved["Item"]["commentBody"] == "on A v1"
        listed = commentService.lambda_handler(_event("GET", DB_A, SHARED_ID), None)
        assert [c["commentId"] for c in _message(listed)] == ["c-a2"]
        # B's same-id asset keeps its comment
        assert [c["commentId"] for c in _message(commentService.lambda_handler(_event("GET", DB_B, SHARED_ID), None))] == ["c-b1"]


@pytest.mark.unit
class TestCreate:
    def test_create_returns_a_server_generated_id_and_stores_the_version(self, table):
        response = addComment.lambda_handler(
            _event("POST", DB_B, SHARED_ID, body={"commentBody": "new", "assetVersionId": "3"}), None)
        assert response["statusCode"] == 200
        comment_id = json.loads(response["body"])["commentId"]
        row = table.get_item(Key={"databaseId:assetId": f"{DB_B}:{SHARED_ID}", "commentId": comment_id})["Item"]
        assert (row["databaseId"], row["assetId"], row["assetVersionId"], row["commentBody"]) == (
            DB_B, SHARED_ID, "3", "new")
        assert row["commentOwnerID"] == CALLER

    def test_two_creates_get_distinct_ids(self, table):
        ids = {json.loads(addComment.lambda_handler(
            _event("POST", DB_A, SHARED_ID, body={"commentBody": "x", "assetVersionId": "1"}), None)["body"])["commentId"]
            for _ in range(2)}
        assert len(ids) == 2

    @pytest.mark.parametrize("body", [{"commentBody": "x"}, {"assetVersionId": "1"},
                                      {"commentBody": "x", "assetVersionId": ""}])
    def test_create_requires_a_body_and_a_version(self, table, body):
        response = addComment.lambda_handler(_event("POST", DB_A, SHARED_ID, body=body), None)
        assert response["statusCode"] == 400

    def test_create_on_a_missing_asset_is_404_once_allowed(self, table):
        response = addComment.lambda_handler(
            _event("POST", DB_A, MISSING_ID, body={"commentBody": "x", "assetVersionId": "1"}), None)
        assert response["statusCode"] == 404


@pytest.mark.unit
class TestAuthorizationBeforeExistence:
    """A caller refused on database B learns nothing about B's assets."""

    @pytest.mark.parametrize("call", [
        lambda asset_id: commentService.lambda_handler(_event("GET", DB_B, asset_id), None),
        lambda asset_id: commentService.lambda_handler(_event("GET", DB_B, asset_id, "c-b1"), None),
        lambda asset_id: commentService.lambda_handler(_event("DELETE", DB_B, asset_id, "c-b1"), None),
        lambda asset_id: editComment.lambda_handler(
            _event("PUT", DB_B, asset_id, "c-b1", body={"commentBody": "x"}), None),
        lambda asset_id: addComment.lambda_handler(
            _event("POST", DB_B, asset_id, body={"commentBody": "x", "assetVersionId": "1"}), None),
    ], ids=["list", "get", "delete", "edit", "create"])
    def test_present_and_missing_answer_alike(self, table, call):
        _Enforcer.allowed = {DB_A}
        present, missing = call(SHARED_ID), call(MISSING_ID)
        assert present["statusCode"] == missing["statusCode"] == 403
        assert present["body"] == missing["body"]
        assert SHARED_ID not in present["body"] and DB_B not in present["body"]
        # Tier 2 was asked about database B each time, never about A's same-id record
        assert {obj["databaseId"] for obj in _Enforcer.objects} == {DB_B}
        # Nothing was written to B
        assert table.get_item(Key={"databaseId:assetId": f"{DB_B}:{SHARED_ID}", "commentId": "c-b1"})["Item"][
            "commentBody"] == "on B v1"

    def test_an_allowed_caller_learns_a_missing_asset(self, table):
        response = commentService.lambda_handler(_event("GET", DB_B, MISSING_ID), None)
        assert response["statusCode"] == 404

    def test_tier2_is_asked_about_the_annotated_asset_record(self, table):
        response = commentService.lambda_handler(_event("GET", DB_B, SHARED_ID), None)
        assert response["statusCode"] == 200
        assert _Enforcer.objects[-1] == {"databaseId": DB_B, "assetId": SHARED_ID, "assetName": "B's asset",
                                         "object__type": "asset"}

    def test_refused_archived_and_missing_answer_alike(self, table):
        _Enforcer.allowed = {DB_A}
        archived = commentService.lambda_handler(
            _event("GET", DB_A, ARCHIVED_ID, query={"showArchived": "true"}), None)
        missing = commentService.lambda_handler(
            _event("GET", DB_A, MISSING_ID, query={"showArchived": "true"}), None)
        assert archived["statusCode"] == missing["statusCode"] == 403
        assert archived["body"] == missing["body"]

    @pytest.mark.parametrize("handler, method, comment_id, body", [
        (commentService, "GET", None, None),
        (commentService, "GET", "c-a1", None),
        (commentService, "DELETE", "c-a1", None),
        (editComment, "PUT", "c-a1", {"commentBody": "x"}),
        (addComment, "POST", None, {"commentBody": "x", "assetVersionId": "1"}),
    ], ids=["list", "get", "delete", "edit", "create"])
    def test_no_identity_is_refused(self, table, monkeypatch, handler, method, comment_id, body):
        monkeypatch.setattr(handler, "request_to_claims", lambda event: {"tokens": []})
        response = handler.lambda_handler(_event(method, DB_A, SHARED_ID, comment_id, body=body), None)
        assert response["statusCode"] == 403
        assert _Enforcer.objects == []


@pytest.mark.unit
class TestRequestValidation:
    @pytest.mark.parametrize("path", [
        {"databaseId": "bad#db", "assetId": SHARED_ID},
        {"databaseId": DB_A, "assetId": ""},
        {"assetId": SHARED_ID},
        {},
    ], ids=["invalid database id", "empty asset id", "no database id", "no path parameters"])
    @pytest.mark.parametrize("handler, method, comment_id, body", [
        (commentService, "GET", None, None),
        (commentService, "DELETE", "c-a1", None),
        (editComment, "PUT", "c-a1", {"commentBody": "x"}),
        (addComment, "POST", None, {"commentBody": "x", "assetVersionId": "1"}),
    ], ids=["list", "delete", "edit", "create"])
    def test_ids_are_validated(self, table, path, handler, method, comment_id, body):
        event = _event(method, DB_A, SHARED_ID, comment_id, body=body)
        event["pathParameters"] = dict(path, **({"commentId": comment_id} if comment_id else {}))
        assert handler.lambda_handler(event, None)["statusCode"] == 400
        assert _Enforcer.objects == []

    @pytest.mark.parametrize("handler, method", [(commentService, "DELETE"), (editComment, "PUT")])
    def test_a_comment_route_needs_the_comment_id(self, table, handler, method):
        event = _event(method, DB_A, SHARED_ID, body={"commentBody": "x"} if method == "PUT" else None)
        assert handler.lambda_handler(event, None)["statusCode"] == 400

    def test_an_invalid_comment_id_is_refused(self, table):
        assert commentService.lambda_handler(_event("GET", DB_A, SHARED_ID, "bad#id"), None)["statusCode"] == 400


@pytest.mark.unit
class TestOwnership:
    """Only a comment's creator edits or deletes it; a refused change leaves the row as it was."""

    def test_someone_else_cannot_edit_the_comment(self, table, monkeypatch):
        monkeypatch.setattr(editComment, "request_to_claims", lambda event: {"tokens": ["mallory@example.com"]})
        response = editComment.lambda_handler(
            _event("PUT", DB_A, SHARED_ID, "c-a1", body={"commentBody": "taken over"}), None)
        assert response["statusCode"] == 403
        row = table.get_item(Key={"databaseId:assetId": f"{DB_A}:{SHARED_ID}", "commentId": "c-a1"})["Item"]
        assert row["commentBody"] == "on A v1"

    def test_someone_else_cannot_delete_the_comment(self, table, monkeypatch):
        monkeypatch.setattr(commentService, "request_to_claims", lambda event: {"tokens": ["mallory@example.com"]})
        response = commentService.lambda_handler(_event("DELETE", DB_A, SHARED_ID, "c-a1"), None)
        assert response["statusCode"] == 403
        assert "Item" in table.get_item(Key={"databaseId:assetId": f"{DB_A}:{SHARED_ID}", "commentId": "c-a1"})

    def test_deleting_a_comment_the_asset_does_not_have_is_404(self, table):
        response = commentService.lambda_handler(_event("DELETE", DB_A, SHARED_ID, "c-none"), None)
        assert response["statusCode"] == 404

    def test_a_soft_deleted_comment_cannot_be_deleted_again(self, table):
        assert commentService.lambda_handler(_event("DELETE", DB_A, SHARED_ID, "c-a1"), None)["statusCode"] == 200
        assert commentService.lambda_handler(_event("DELETE", DB_A, SHARED_ID, "c-a1"), None)["statusCode"] == 404


@pytest.mark.unit
class TestArchivedAsset:
    def test_show_archived_reads_the_original_databases_comments(self, table):
        response = commentService.lambda_handler(
            _event("GET", DB_A, ARCHIVED_ID, query={"showArchived": "true"}), None)
        assert response["statusCode"] == 200
        assert [c["commentId"] for c in _message(response)] == ["c-arch"]
        assert _Enforcer.objects[-1]["databaseId"] == f"{DB_A}#deleted"

    def test_without_the_flag_an_archived_asset_is_not_found(self, table):
        response = commentService.lambda_handler(_event("GET", DB_A, ARCHIVED_ID), None)
        assert response["statusCode"] == 404

    def test_a_missing_id_is_decided_on_the_archived_stand_in(self, table):
        _Enforcer.allowed = {DB_A}
        response = commentService.lambda_handler(
            _event("GET", DB_A, MISSING_ID, query={"showArchived": "true"}), None)
        assert response["statusCode"] == 403
        assert _Enforcer.objects[-1]["databaseId"] == f"{DB_A}#deleted"
