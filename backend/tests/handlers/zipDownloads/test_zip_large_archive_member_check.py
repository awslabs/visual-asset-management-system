# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The download URL's member check of a large archive, and its time bound.

POST .../download/zip/{requestId}/url signs a finished archive only after every member file is
confirmed still downloadable. An archive of more than HEAD_CHECK_MAX_FILES members is confirmed from
listings of the folders that hold its members, current objects only, so the asset's other files
and its old versions are not listed. When the check cannot finish within its time bound, the route
answers 503 "try again" and signs nothing, instead of saying that a file was archived or deleted.

- a 2,001-member archive on an asset with a deep version history and a large unrelated folder
  downloads within a bound that a listing of every version under the asset root cannot meet;
- only the members' own folders are listed, and no version listing runs;
- an archived, deleted, cold-storage or blocked member still refuses it with the archived-or-deleted
  refusal (400);
- members in several folders and a folder listed over several pages are confirmed;
- when the bound runs out, on the listing or on the head checks of a smaller archive, the route
  answers 503 with MSG_ARCHIVE_CHECK_INCOMPLETE, signs nothing and records zipDownloadUrl failed;
- another user's link (the shared view) gets the same answers, recorded with both users.
"""

import time

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

# A listing page costs this long on the fake bucket's clock, and the check may take this long. A
# listing of every version under the asset root takes 21 pages here, over the bound; the members'
# folder takes 3.
PAGE_SECONDS = 1.0
BOUND_SECONDS = 10.0
OLD_VERSIONS = 7
UNRELATED_FILES = 4000


@pytest.fixture
def env(monkeypatch):
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    monkeypatch.setattr(e.svc, "SHARED_RECHECK_MAX_SECONDS", BOUND_SECONDS)
    return e


def _members(e):
    return [f"/big/part{n:05d}.glb" for n in range(e.svc.HEAD_CHECK_MAX_FILES + 1)]


def _large_asset(e):
    """More than HEAD_CHECK_MAX_FILES members in one folder, each with a deep version history, and a
    large folder the archive does not include."""
    members = _members(e)
    for rel in members:
        for n in range(OLD_VERSIONS):
            e.s3.add("xasset1" + rel, version=f"old{n}")
        e.s3.add("xasset1" + rel, version="current")
    for n in range(UNRELATED_FILES):
        e.s3.add(f"xasset1/scans/scan{n:05d}.e57", version="v1")
    return members


def _finished(e, members, user="alice", selection=None):
    """A job whose archive landed, with ``members`` in its resolved manifest."""
    h.AUTH.user = user
    body = selection or {"selection": [{"type": "folder", "relativePath": "/big/"}]}
    response = e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, body=body), None)
    assert response["statusCode"] == 200, response
    request_id = h.body_of(response)["requestId"]
    row = e.zip.items[(user, request_id)]
    row["status"] = "SUCCEEDED"
    e.s3.add(row["archiveKey"], size=4242, bucket=h.AUX)
    h.write_resolved_manifest(e, row, members)
    return request_id


def _url(e, request_id, user="alice"):
    h.AUTH.user = user
    e.s3.list_calls.clear()
    path = h.asset_path("db1", "asset1", f"/{request_id}/url")
    return e.svc.lambda_handler(h.api_event(
        "POST", path, {"databaseId": "db1", "assetId": "asset1", "requestId": request_id}), None)


def _url_records(e):
    return [c.kwargs for c in e.audit.call_args_list if c.args[1] == "zipDownloadUrl"]


@pytest.fixture
def large(env, monkeypatch):
    members = _large_asset(env)
    request_id = _finished(env, members)
    h.use_clock(env, monkeypatch, PAGE_SECONDS)
    return env, request_id, members


def test_a_large_archive_on_a_deep_version_history_downloads_within_the_bound(large):
    env, request_id, members = large
    assert len(members) > env.svc.HEAD_CHECK_MAX_FILES
    started = env.s3.clock
    response = _url(env, request_id)
    assert response["statusCode"] == 200, h.body_of(response)
    assert len(env.s3.list_calls) == 3
    assert env.s3.clock - started < BOUND_SECONDS
    assert len(env.s3.presigned) == 1


def test_only_the_members_folders_are_listed_and_no_version_listing_runs(large):
    env, request_id, _ = large
    assert _url(env, request_id)["statusCode"] == 200
    listed = {(operation, prefix) for operation, prefix, _ in env.s3.list_calls}
    assert listed == {("list_objects_v2", "xasset1/big/")}
    assert all(delimiter == "/" for _, _, delimiter in env.s3.list_calls)


CHANGES = {
    "archived": lambda e, key: e.s3.add(key, version="vdm", delete_marker=True),
    "permanently_deleted": lambda e, key: e.s3.objects.pop((h.ASSET_BUCKET, key)),
    "archive_storage_class": lambda e, key: e.s3.add(key, version="cold", storage_class="GLACIER"),
}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_member_no_longer_downloadable_still_refuses_a_large_archive(large, change):
    env, request_id, members = large
    CHANGES[change](env, "xasset1" + members[-1])
    env.audit.reset_mock()
    response = _url(env, request_id)
    assert response["statusCode"] == 400
    assert h.body_of(response)["message"] == env.zd.MSG_ARCHIVE_FILE_UNAVAILABLE
    assert env.s3.presigned == []
    refused = _url_records(env)
    assert len(refused) == 1
    assert (refused[0]["outcome"], refused[0]["reason"]) == ("failed", "validation")


def test_a_blocked_member_type_still_refuses_a_large_archive(env, monkeypatch):
    members = [f"/big/part{n:05d}.glb" for n in range(3)]
    for rel in members:
        env.s3.add("xasset1" + rel)
    request_id = _finished(env, members)
    # A member of a blocked type (the create would have refused it; the manifest names it).
    env.s3.add("xasset1/big/tool.exe")
    h.write_resolved_manifest(env, env.zip.items[("alice", request_id)], members + ["/big/tool.exe"])
    monkeypatch.setattr(env.svc, "SHARED_RECHECK_HEAD_MAX_FILES", 0)
    response = _url(env, request_id)
    assert response["statusCode"] == 400
    assert h.body_of(response)["message"] == env.zd.MSG_ARCHIVE_FILE_UNAVAILABLE


def test_members_in_several_folders_and_a_folder_over_several_pages_are_confirmed(env, monkeypatch):
    members = ["/top.glb", "/a/one.glb", "/a/b/two.glb"]
    members += [f"/many/file{n:05d}.glb" for n in range(2500)]
    for rel in members:
        env.s3.add("xasset1" + rel)
    for n in range(1500):
        env.s3.add(f"xasset1/many/zz-not-in-archive{n:05d}.glb")
    request_id = _finished(env, members, selection={"selection": [
        {"type": "asset", "relativePath": "/"}]})
    monkeypatch.setattr(env.svc, "SHARED_RECHECK_HEAD_MAX_FILES", 0)
    assert _url(env, request_id)["statusCode"] == 200
    prefixes = sorted({prefix for _, prefix, _ in env.s3.list_calls})
    assert prefixes == ["xasset1/", "xasset1/a/", "xasset1/a/b/", "xasset1/many/"]
    # Listing stops once every member of a folder is confirmed: the trailing files are not read.
    assert sum(1 for _, prefix, _ in env.s3.list_calls if prefix == "xasset1/many/") == 3
    env.s3.add("xasset1/a/b/two.glb", version="vdm", delete_marker=True)
    assert _url(env, request_id)["statusCode"] == 400


def _assert_check_incomplete(env, response, request_id, details=None):
    assert response["statusCode"] == 503
    assert h.body_of(response)["message"] == env.zd.MSG_ARCHIVE_CHECK_INCOMPLETE
    assert h.body_of(response)["message"] != env.zd.MSG_ARCHIVE_FILE_UNAVAILABLE
    assert env.s3.presigned == []
    records = _url_records(env)
    assert len(records) == 1
    assert (records[0]["outcome"], records[0]["reason"]) == ("failed", "internal")
    assert records[0]["entity_id"] == request_id and records[0]["details"] == details


def test_a_listing_that_outruns_the_bound_answers_try_again(large, monkeypatch):
    env, request_id, _ = large
    monkeypatch.setattr(env.svc, "SHARED_RECHECK_MAX_SECONDS", PAGE_SECONDS / 2)
    env.audit.reset_mock()
    _assert_check_incomplete(env, _url(env, request_id), request_id)


def test_head_checks_that_outrun_the_bound_answer_try_again(env, monkeypatch):
    members = [f"/part{n}.glb" for n in range(40)]
    for rel in members:
        env.s3.add("xasset1" + rel)
    request_id = _finished(env, members, selection={"selection": [
        {"type": "asset", "relativePath": "/"}]})
    assert len(members) <= env.svc.SHARED_RECHECK_HEAD_MAX_FILES

    def slow_head(*_args, **_kwargs):
        time.sleep(2.0)
        return True

    monkeypatch.setattr(env.svc, "_current_member_ok", slow_head)
    monkeypatch.setattr(env.svc, "SHARED_RECHECK_MAX_SECONDS", 0.05)
    env.audit.reset_mock()
    started = time.monotonic()
    response = _url(env, request_id)
    # Answered at the bound, without waiting for the head checks still running.
    assert time.monotonic() - started < 1.5
    _assert_check_incomplete(env, response, request_id)


def test_another_users_link_gets_the_same_answers(large, monkeypatch):
    env, request_id, members = large
    assert _url(env, request_id, user="bob")["statusCode"] == 200
    monkeypatch.setattr(env.svc, "SHARED_RECHECK_MAX_SECONDS", PAGE_SECONDS / 2)
    env.audit.reset_mock()
    env.s3.presigned.clear()
    _assert_check_incomplete(env, _url(env, request_id, user="bob"), request_id,
                             details={"shared": True, "requestedBy": "alice", "downloadedBy": "bob"})
    monkeypatch.setattr(env.svc, "SHARED_RECHECK_MAX_SECONDS", BOUND_SECONDS)
    CHANGES["archived"](env, "xasset1" + members[0])
    own, shared = _url(env, request_id, user="alice"), _url(env, request_id, user="bob")
    assert own["statusCode"] == shared["statusCode"] == 400
    assert h.body_of(own) == h.body_of(shared)
