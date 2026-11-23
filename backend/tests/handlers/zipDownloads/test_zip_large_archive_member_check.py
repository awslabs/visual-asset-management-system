# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The download URL's member check of a large archive, and its time bound.

POST .../download/zip/{requestId}/url signs a finished archive only after every member file is
confirmed still downloadable. An archive of more than HEAD_CHECK_MAX_FILES members is confirmed from
listings of its sorted member keys in runs of 1,000, current objects only, in parallel. Each run
starts just below its first member and skips the unselected files between members a page at a time,
so the call count follows the archive's size, not its folders, its gaps or its old versions. When the
check cannot finish within its time bound, the route answers 503 "try again" and signs nothing,
instead of saying that a file was archived or deleted.

- a 2,001-member archive on an asset with a deep version history and a large unrelated folder
  downloads within a bound that a listing of every version under the asset root cannot meet;
- only runs of the members' own keys are listed, current objects only, and no version listing runs;
- many small folders, one folder larger than a page, and a large unselected folder between or before
  the members' folders each cost a bounded number of calls;
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
    e.s3.new_request()
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
    started = env.s3.max_clock()
    response = _url(env, request_id)
    assert response["statusCode"] == 200, h.body_of(response)
    # One run of up to 1,000 sorted member keys per call: 2,001 members, 3 calls, in parallel.
    assert len(env.s3.list_calls) == 3
    assert env.s3.max_clock() - started < BOUND_SECONDS
    assert len(env.s3.presigned) == 1


def test_only_the_members_keys_are_listed_current_objects_only(large):
    env, request_id, _ = large
    assert _url(env, request_id)["statusCode"] == 200
    assert {operation for operation, _, _ in env.s3.list_calls} == {"list_objects_v2"}
    # Each run is listed under its members' common prefix: never the asset root, the unrelated folder or
    # the old versions.
    assert all(prefix.startswith("xasset1/big/part") for _, prefix, _ in env.s3.list_calls)
    assert all(delimiter is None for _, _, delimiter in env.s3.list_calls)


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
    # 2,503 members in sorted runs of 1,000: one call each for the first two, two for the last, whose
    # first page ends among the 1,500 files that sort between file02499.glb and top.glb, so the second
    # starts just below top.glb.
    assert len(env.s3.list_calls) == 4
    env.s3.add("xasset1/a/b/two.glb", version="vdm", delete_marker=True)
    assert _url(env, request_id)["statusCode"] == 400


def test_an_archive_in_many_small_folders_is_checked_by_its_size_not_its_folder_count(env, monkeypatch):
    # 4,000 single-version members in 2,000 folders: a call per folder, 25 at a time, would be 80 calls
    # on one thread; runs of sorted keys need 4 calls in parallel.
    members = [f"/tiles/{n:05d}/{leaf}.b3dm" for n in range(2000) for leaf in ("a", "b")]
    for rel in members:
        env.s3.add("xasset1" + rel)
    request_id = _finished(env, members, selection={"selection": [
        {"type": "folder", "relativePath": "/tiles/"}]})
    h.use_clock(env, monkeypatch, PAGE_SECONDS)
    started = env.s3.max_clock()
    response = _url(env, request_id)
    assert response["statusCode"] == 200, h.body_of(response)
    assert len(env.s3.list_calls) == 4
    assert env.s3.max_clock() - started <= 2 * PAGE_SECONDS


def test_one_folder_larger_than_a_page_is_split_across_the_pool(env, monkeypatch):
    # 12,000 members in one folder: 12 pages on one thread would pass the bound; 12 runs in parallel do
    # not.
    members = [f"/scan/p{n:06d}.e57" for n in range(12000)]
    for rel in members:
        env.s3.add("xasset1" + rel)
    request_id = _finished(env, members, selection={"selection": [
        {"type": "folder", "relativePath": "/scan/"}]})
    h.use_clock(env, monkeypatch, PAGE_SECONDS)
    started = env.s3.max_clock()
    response = _url(env, request_id)
    assert response["statusCode"] == 200, h.body_of(response)
    assert len(env.s3.list_calls) == 12
    assert env.s3.max_clock() - started <= 2 * PAGE_SECONDS


def _gap_archive(env, monkeypatch, selected, gap_folder, gap_files):
    """Members in the selected {folder: count} folders, and gap_files unselected files in gap_folder."""
    members = [f"/{folder}/f{n:05d}.glb" for folder, count in selected.items() for n in range(count)]
    for rel in members:
        env.s3.add("xasset1" + rel)
    for n in range(gap_files):
        env.s3.add(f"xasset1/{gap_folder}/g{n:05d}.e57")
    request_id = _finished(env, members, selection={"selection": [
        {"type": "folder", "relativePath": f"/{folder}/"} for folder in selected]})
    h.use_clock(env, monkeypatch, PAGE_SECONDS)
    return request_id


def test_an_unselected_folder_between_two_selected_folders_is_skipped(env, monkeypatch):
    # /a/ and /c/ are selected, 60,000 files in /b/ are not. The run that spans /a/ and /c/ reads one page
    # into /b/, then restarts just below /c/'s first member: 4 calls, not 60 pages on one thread.
    request_id = _gap_archive(env, monkeypatch, {"a": 1500, "c": 1500}, "b", 60000)
    started = env.s3.max_clock()
    response = _url(env, request_id)
    assert response["statusCode"] == 200, h.body_of(response)
    assert len(env.s3.list_calls) == 4
    assert env.s3.max_clock() - started <= 2 * PAGE_SECONDS
    # The first member after the gap archived: the restarted listing finds the next key past it.
    env.s3.add("xasset1/c/f00000.glb", version="vdm", delete_marker=True)
    response = _url(env, request_id)
    assert response["statusCode"] == 400
    assert h.body_of(response)["message"] == env.zd.MSG_ARCHIVE_FILE_UNAVAILABLE


def test_an_unselected_folder_before_a_run_that_spans_two_folders_is_skipped(env, monkeypatch):
    # /b/ and /d/ are selected, 60,000 files in /a/ sort before both. The first run (all of /b/ and part
    # of /d/) is listed under the asset root but starts just below its first member: 3 calls.
    request_id = _gap_archive(env, monkeypatch, {"b": 500, "d": 1600}, "a", 60000)
    started = env.s3.max_clock()
    response = _url(env, request_id)
    assert response["statusCode"] == 200, h.body_of(response)
    assert len(env.s3.list_calls) == 3
    assert env.s3.max_clock() - started <= PAGE_SECONDS


def test_member_keys_with_special_characters_are_confirmed_and_refused(env, monkeypatch):
    names = ["/sp ace/a b.glb", "/plus/x+y.glb", "/pct/a%20b.glb", "/hash/a#b.glb", "/acc/foldér/é.glb",
             "/cjk/模型.glb", "/q/it's \"x\".glb"]
    members = names + [f"/fill/f{n:05d}.glb" for n in range(2000)]
    for rel in members:
        env.s3.add("xasset1" + rel)
    request_id = _finished(env, members, selection={"selection": [
        {"type": "asset", "relativePath": "/"}]})
    assert len(members) > env.svc.SHARED_RECHECK_HEAD_MAX_FILES
    assert _url(env, request_id)["statusCode"] == 200
    env.s3.add("xasset1/acc/foldér/é.glb", version="vdm", delete_marker=True)
    response = _url(env, request_id)
    assert response["statusCode"] == 400
    assert h.body_of(response)["message"] == env.zd.MSG_ARCHIVE_FILE_UNAVAILABLE


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
    # One run over all 2,001 members, so the listing needs a second page after the bound has passed.
    monkeypatch.setattr(env.svc, "SHARED_RECHECK_LIST_RUN", 5000)
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
    monkeypatch.setattr(env.svc, "SHARED_RECHECK_LIST_RUN", 5000)
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
