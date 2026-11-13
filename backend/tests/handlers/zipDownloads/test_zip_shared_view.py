# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download Queue shared view: every user's jobs for one asset, and downloading another user's
archive.

- GET /database/{databaseId}/assets/{assetId}/download/zip-all-users lists every user's jobs for
  the asset to a caller with the route (Tier 1) who may read the asset (Tier 2); a non-reader gets
  the 404 of a missing asset, audited.
- POST .../download/zip/{requestId}/url signs another user's finished archive only for a caller who
  may download from the asset (Tier 1 on the asset's file download route, Tier 2 asset GET), after
  the archive's member files pass the download eligibility rules again.
- Cancel and delete of another user's job stay refused.

The handlers run against the in-memory doubles of zipHarness.
"""

import json

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

FILE_SELECTION = {"selection": [{"type": "file", "relativePath": "/a.glb"}]}
DOWNLOAD_ROUTE = ("POST", r"/database/db1/assets/asset1/download")


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    return e


def _create(e, user):
    h.AUTH.user = user
    response = e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, body=FILE_SELECTION), None)
    assert response["statusCode"] == 200, response
    return h.body_of(response)["requestId"]


def _succeed(e, user, request_id, members=("/a.glb",)):
    """The container finished the job: the archive landed and its resolved manifest lists members."""
    row = e.zip.items[(user, request_id)]
    row["status"] = "SUCCEEDED"
    row["archiveSizeBytes"] = 4242
    e.s3.add(row["archiveKey"], size=4242, bucket=h.AUX)
    resolved = e.zd.resolved_manifest_key("db1", "asset1", user, request_id)
    row["resolvedManifestKey"] = resolved
    body = "".join(json.dumps({"relativePath": m, "versionId": "va1", "size": 100}) + "\n"
                   for m in members)
    e.s3.put_object(Bucket=h.AUX, Key=resolved, Body=body.encode("utf-8"))
    return row


def _all_users(e, user, query=None, asset_id="asset1"):
    h.AUTH.user = user
    path = f"/database/db1/assets/{asset_id}/download/zip-all-users"
    return e.svc.lambda_handler(h.api_event("GET", path, {"databaseId": "db1", "assetId": asset_id},
                                            query=query), None)


def _job_route(e, method, request_id, user, suffix=""):
    h.AUTH.user = user
    path = h.asset_path("db1", "asset1", f"/{request_id}{suffix}")
    return e.svc.lambda_handler(h.api_event(method, path, {"databaseId": "db1", "assetId": "asset1",
                                                           "requestId": request_id}), None)


class TestAllUsersList:
    def test_reader_sees_every_users_jobs_with_requester_and_flags(self, env):
        mine = _create(env, "alice")
        theirs = _create(env, "bob")
        _succeed(env, "bob", theirs)
        response = _all_users(env, "alice")
        assert response["statusCode"] == 200
        items = {i["requestId"]: i for i in h.body_of(response)["Items"]}
        assert set(items) == {mine, theirs}
        assert items[theirs]["requestedBy"] == "bob" and items[theirs]["isOwn"] is False
        assert items[theirs]["canDownload"] is True and items[theirs]["canRemove"] is False
        assert items[mine]["requestedBy"] == "alice" and items[mine]["isOwn"] is True
        # Not finished yet: no download; the requester may cancel it.
        assert items[mine]["canDownload"] is False and items[mine]["canRemove"] is True

    def test_other_users_rows_carry_no_preview_or_internal_fields(self, env):
        theirs = _create(env, "bob")
        item = h.body_of(_all_users(env, "alice"))["Items"][0]
        assert item["requestId"] == theirs
        assert item["selectionPreview"] == []
        assert item["selectionSummary"] == {"files": 1, "folders": 0, "wholeAsset": False}
        for hidden in ("userId", "archiveKey", "manifestKey", "workflowExecutionId", "assetKey",
                       "userAssetKey", "url"):
            assert hidden not in item

    def test_non_reader_gets_the_missing_asset_404_and_is_audited(self, env):
        _create(env, "bob")
        h.AUTH.denied_assets.add("asset1")
        env.audit.reset_mock()
        denied = _all_users(env, "alice")
        missing = _all_users(env, "alice", asset_id="nosuchasset")
        assert denied["statusCode"] == missing["statusCode"] == 404
        assert h.body_of(denied) == h.body_of(missing)
        # The unreadable asset is recorded as a denial; the missing one by the request outcome.
        denials = [(c.args[1], c.kwargs.get("asset_id")) for c in env.audit.call_args_list
                   if c.kwargs.get("outcome") == "denied"]
        assert denials == [("zipDownloadListAllUsers", "asset1")]

    def test_without_the_route_grant_it_is_403(self, env):
        _create(env, "bob")
        h.AUTH.api_allowed = False
        assert _all_users(env, "alice")["statusCode"] == 403

    def test_download_flag_follows_the_asset_download_decision(self, env):
        theirs = _create(env, "bob")
        _succeed(env, "bob", theirs)
        h.AUTH.denied_routes.add(DOWNLOAD_ROUTE)
        item = h.body_of(_all_users(env, "alice"))["Items"][0]
        assert item["canDownload"] is False
        # The requester's own row is not held to the file download route (unchanged own downloads).
        assert h.body_of(_all_users(env, "bob"))["Items"][0]["canDownload"] is True

    def test_pages_are_bounded_and_the_token_stays_on_the_asset(self, env):
        ids = [_create(env, user) for user in ("alice", "bob", "carol")]
        first = h.body_of(_all_users(env, "alice", query={"pageSize": "2", "maxItems": "2"}))
        assert [i["requestId"] for i in first["Items"]] == sorted(ids, reverse=True)[:2]
        query = {"pageSize": "2", "maxItems": "2", "startingToken": first["NextToken"]}
        second = h.body_of(_all_users(env, "alice", query=query))
        assert [i["requestId"] for i in second["Items"]] == sorted(ids, reverse=True)[2:]
        foreign = h.encode_token({"assetKey": "db1#other", "requestId": ids[0], "userId": "alice"})
        assert _all_users(env, "alice", query={"startingToken": foreign})["statusCode"] == 400

    def test_rows_without_the_asset_key_are_listed_to_their_requester_only(self, env):
        old = _create(env, "bob")
        env.zip.items[("bob", old)].pop("assetKey")
        assert h.body_of(_all_users(env, "alice"))["Items"] == []
        h.AUTH.user = "bob"
        listed = env.svc.lambda_handler(h.api_event(
            "GET", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}), None)
        assert [i["requestId"] for i in h.body_of(listed)["Items"]] == [old]

    def test_create_writes_the_asset_key(self, env):
        request_id = _create(env, "bob")
        assert env.zip.items[("bob", request_id)]["assetKey"] == "db1#asset1"


class TestOtherUsersArchive:
    def test_download_allowed_with_asset_download_and_audited_with_both_users(self, env):
        theirs = _create(env, "bob")
        row = _succeed(env, "bob", theirs)
        env.audit.reset_mock()
        response = _job_route(env, "POST", theirs, "alice", "/url")
        assert response["statusCode"] == 200
        assert env.s3.presigned[-1][1]["Key"] == row["archiveKey"]
        issued = [c.kwargs for c in env.audit.call_args_list if c.args[1] == "zipDownloadUrl"]
        assert len(issued) == 1 and issued[0].get("outcome", "success") == "success"
        assert issued[0]["details"] == {"shared": True, "requestedBy": "bob", "downloadedBy": "alice"}
        assert issued[0]["entity_id"] == theirs and issued[0]["bytes"] == 4242

    def test_refused_without_asset_download(self, env):
        theirs = _create(env, "bob")
        _succeed(env, "bob", theirs)
        h.AUTH.denied_routes.add(DOWNLOAD_ROUTE)
        env.audit.reset_mock()
        response = _job_route(env, "POST", theirs, "alice", "/url")
        assert response["statusCode"] == 404
        assert h.body_of(response)["message"] == env.zd.MSG_NOT_FOUND
        assert env.s3.presigned == []
        denials = [(c.args[1], c.kwargs["outcome"], c.kwargs["details"]) for c in env.audit.call_args_list]
        assert denials == [("zipDownloadUrl", "denied",
                            {"shared": True, "requestedBy": "bob", "downloadedBy": "alice"})]

    def test_refused_without_asset_read(self, env):
        theirs = _create(env, "bob")
        _succeed(env, "bob", theirs)
        h.AUTH.denied_assets.add("asset1")
        assert _job_route(env, "POST", theirs, "alice", "/url")["statusCode"] == 404
        assert env.s3.presigned == []

    @pytest.mark.parametrize("change", ["archived", "deleted", "glacier", "missing"])
    def test_refused_when_a_member_is_no_longer_downloadable(self, env, change):
        theirs = _create(env, "bob")
        _succeed(env, "bob", theirs)
        if change == "archived":
            env.s3.add("xasset1/a.glb", version="vdm", delete_marker=True)
        elif change == "deleted":
            env.s3.add("xasset1/a.glb", version="va2", metadata={"vams-status": "deleted"})
        elif change == "glacier":
            env.s3.add("xasset1/a.glb", version="va2", storage_class="GLACIER")
        else:
            env.s3.objects.pop((h.ASSET_BUCKET, "xasset1/a.glb"))
        env.audit.reset_mock()
        response = _job_route(env, "POST", theirs, "alice", "/url")
        assert response["statusCode"] == 400
        assert h.body_of(response)["message"] == env.zd.MSG_ARCHIVED_IN_SELECTION
        assert env.s3.presigned == []
        refused = [c.kwargs for c in env.audit.call_args_list if c.args[1] == "zipDownloadUrl"]
        assert len(refused) == 1
        assert (refused[0]["outcome"], refused[0]["reason"]) == ("failed", "validation")
        assert refused[0]["details"]["requestedBy"] == "bob"

    def test_refused_when_the_asset_is_no_longer_distributable(self, env):
        theirs = _create(env, "bob")
        _succeed(env, "bob", theirs)
        env.assets.items[("db1", "asset1")]["isDistributable"] = False
        assert _job_route(env, "POST", theirs, "alice", "/url")["statusCode"] == 400
        assert env.s3.presigned == []

    def test_refused_when_the_member_record_is_unreadable(self, env):
        theirs = _create(env, "bob")
        row = _succeed(env, "bob", theirs)
        env.s3.objects.pop((h.AUX, row["resolvedManifestKey"]))
        assert _job_route(env, "POST", theirs, "alice", "/url")["statusCode"] == 400

    def test_a_large_archive_is_checked_from_one_listing(self, env, monkeypatch):
        theirs = _create(env, "bob")
        _succeed(env, "bob", theirs)
        monkeypatch.setattr(env.svc, "SHARED_RECHECK_HEAD_MAX_FILES", 0)
        assert _job_route(env, "POST", theirs, "alice", "/url")["statusCode"] == 200
        env.s3.add("xasset1/a.glb", version="vdm", delete_marker=True)
        assert _job_route(env, "POST", theirs, "alice", "/url")["statusCode"] == 400

    def test_the_owners_own_download_needs_no_member_recheck(self, env):
        mine = _create(env, "bob")
        _succeed(env, "bob", mine)
        env.s3.add("xasset1/a.glb", version="vdm", delete_marker=True)
        assert _job_route(env, "POST", mine, "bob", "/url")["statusCode"] == 200

    def test_cancel_and_delete_of_another_users_job_are_refused(self, env):
        running = _create(env, "bob")
        done = _create(env, "bob")
        _succeed(env, "bob", done)
        for request_id, query in ((running, None), (done, None)):
            h.AUTH.user = "alice"
            path = h.asset_path("db1", "asset1", f"/{request_id}")
            event = h.api_event("DELETE", path, {"databaseId": "db1", "assetId": "asset1",
                                                 "requestId": request_id}, query=query)
            response = env.svc.lambda_handler(event, None)
            assert response["statusCode"] == 404
        cancel = h.api_event("DELETE", h.asset_path("db1", "asset1", f"/{running}"),
                             {"databaseId": "db1", "assetId": "asset1", "requestId": running},
                             query={"intent": "cancel"})
        h.AUTH.user = "alice"
        assert env.svc.lambda_handler(cancel, None)["statusCode"] == 404
        assert env.zip.items[("bob", running)]["status"] == "QUEUED"
        assert env.zip.items[("bob", done)]["status"] == "SUCCEEDED"
