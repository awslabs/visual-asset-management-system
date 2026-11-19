# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The download URL route rechecks the archive's member files for its requester too.

POST .../download/zip/{requestId}/url signs a finished archive only while every member file is still
downloadable: present, not archived, not a deleted version, not in an archive storage class, and
not a blocked type. Another user's archive was already rechecked (test_zip_shared_view.py); the
requester's own archive now is too, so a file archived or permanently deleted after the job
finished refuses its requester's link with the refusal a new zip download gets, instead of the
archive staying downloadable until the job expires.

- each such change refuses the requester's link (400), signs nothing, and is recorded once as
  ``zipDownloadUrl`` failed, ``validation``, without the both-users details;
- a large archive is checked from one listing; an unreadable member record fails closed;
- an unchanged archive, or one whose member has a newer current version, still downloads;
- the requester and another user get the same answer for the same archive;
- with the quarantine block on, a quarantined asset answers the quarantine refusal first and no
  member is read; under a granted exception the members are rechecked.
"""

from unittest.mock import MagicMock, patch

import pytest

import common.compliance.quarantineGuard as guard
from backend.tests.handlers.zipDownloads import zipHarness as h
from backend.tests.quarantineRefusalBody import assert_is_the_quarantine_refusal

pytestmark = pytest.mark.unit

MEMBERS = ("/a.glb", "/b.glb")
SELECTION = {"selection": [{"type": "file", "relativePath": m} for m in MEMBERS]}


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    e.s3.add("xasset1/b.glb", size=100, version="vb1")
    return e


def _finished(e, user="alice"):
    """A job whose archive landed, with both members in its resolved manifest."""
    h.AUTH.user = user
    response = e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, body=SELECTION), None)
    assert response["statusCode"] == 200, response
    request_id = h.body_of(response)["requestId"]
    row = e.zip.items[(user, request_id)]
    row["status"] = "SUCCEEDED"
    e.s3.add(row["archiveKey"], size=4242, bucket=h.AUX)
    h.write_resolved_manifest(e, row, MEMBERS)
    return request_id, row


def _url(e, request_id, user="alice"):
    h.AUTH.user = user
    path = h.asset_path("db1", "asset1", f"/{request_id}/url")
    return e.svc.lambda_handler(h.api_event(
        "POST", path, {"databaseId": "db1", "assetId": "asset1", "requestId": request_id}), None)


def _url_records(e):
    return [c.kwargs for c in e.audit.call_args_list if c.args[1] == "zipDownloadUrl"]


def _archive_member(e):
    e.s3.add("xasset1/b.glb", version="vdm", delete_marker=True)


def _permanently_delete_member(e):
    e.s3.objects.pop((h.ASSET_BUCKET, "xasset1/b.glb"))


CHANGES = {
    "archived": _archive_member,
    "permanently_deleted": _permanently_delete_member,
    "deleted_version": lambda e: e.s3.add("xasset1/b.glb", version="vb2",
                                          metadata={"vams-status": "deleted"}),
    "archive_storage_class": lambda e: e.s3.add("xasset1/b.glb", version="vb2",
                                                storage_class="GLACIER"),
}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_the_requesters_link_is_refused_once_a_member_is_no_longer_downloadable(env, change):
    request_id, _ = _finished(env)
    CHANGES[change](env)
    env.audit.reset_mock()
    response = _url(env, request_id)
    assert response["statusCode"] == 400
    assert h.body_of(response)["message"] == env.zd.MSG_ARCHIVED_IN_SELECTION
    assert env.s3.presigned == []
    refused = _url_records(env)
    assert len(refused) == 1
    assert (refused[0]["outcome"], refused[0]["reason"]) == ("failed", "validation")
    assert refused[0]["entity_id"] == request_id and refused[0]["details"] is None


def test_a_large_archive_is_checked_from_one_listing(env, monkeypatch):
    request_id, _ = _finished(env)
    monkeypatch.setattr(env.svc, "SHARED_RECHECK_HEAD_MAX_FILES", 0)
    assert _url(env, request_id)["statusCode"] == 200
    _archive_member(env)
    assert _url(env, request_id)["statusCode"] == 400
    assert len(env.s3.presigned) == 1


def test_an_unreadable_member_record_fails_closed(env):
    request_id, row = _finished(env)
    env.s3.objects.pop((h.AUX, row["resolvedManifestKey"]))
    response = _url(env, request_id)
    assert response["statusCode"] == 400
    assert env.s3.presigned == []


def test_regression_an_unchanged_archive_still_downloads(env):
    request_id, row = _finished(env)
    env.audit.reset_mock()
    response = _url(env, request_id)
    assert response["statusCode"] == 200
    assert env.s3.presigned[-1][1]["Key"] == row["archiveKey"]
    issued = _url_records(env)
    assert len(issued) == 1 and issued[0].get("outcome", "success") == "success"
    assert issued[0]["details"] is None and issued[0]["bytes"] == 4242


def test_regression_a_member_with_a_newer_version_still_downloads(env):
    request_id, _ = _finished(env)
    env.s3.add("xasset1/b.glb", size=120, version="vb2")
    assert _url(env, request_id)["statusCode"] == 200


def test_the_requester_and_another_user_get_the_same_answer(env):
    request_id, _ = _finished(env, user="alice")
    assert _url(env, request_id, user="alice")["statusCode"] == 200
    assert _url(env, request_id, user="bob")["statusCode"] == 200
    _permanently_delete_member(env)
    env.audit.reset_mock()
    own = _url(env, request_id, user="alice")
    shared = _url(env, request_id, user="bob")
    assert own["statusCode"] == shared["statusCode"] == 400
    assert h.body_of(own) == h.body_of(shared)
    details = [r["details"] for r in _url_records(env)]
    assert details == [None, {"shared": True, "requestedBy": "alice", "downloadedBy": "bob"}]


def _quarantine(exception_granted=False):
    row = {"databaseId": "db1", "assetId": "asset1", "complianceState": "quarantined"}
    if exception_granted:
        row["exceptionGranted"] = True
    table = MagicMock(name="compliance_asset_state_table")
    table.get_item.return_value = {"Item": row}
    return patch.multiple(guard, quarantine_blocks_download=True, compliance_asset_state_table=table)


@pytest.mark.parametrize("user", ["alice", "bob"])
def test_a_quarantined_asset_answers_the_quarantine_refusal_before_any_member_read(
        env, monkeypatch, user):
    request_id, _ = _finished(env, user="alice")
    _archive_member(env)

    def no_member_read(*_args, **_kwargs):
        raise AssertionError("members read for a quarantined asset")

    monkeypatch.setattr(env.svc, "_recheck_members", no_member_read)
    with _quarantine():
        response = _url(env, request_id, user=user)
    assert_is_the_quarantine_refusal(response)
    assert env.s3.presigned == []


def test_under_a_granted_exception_the_members_are_rechecked(env):
    request_id, _ = _finished(env)
    _archive_member(env)
    with _quarantine(exception_granted=True):
        response = _url(env, request_id)
    assert response["statusCode"] == 400
    assert h.body_of(response)["message"] == env.zd.MSG_ARCHIVED_IN_SELECTION
