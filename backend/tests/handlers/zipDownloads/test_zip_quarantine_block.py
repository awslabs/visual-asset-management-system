# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 D35: compliance's quarantine block (#749) covers Download as Zip like every download route.

With `app.compliance.quarantineBlocksDownload` on, a quarantined asset without a granted exception
cannot be downloaded (common/compliance/quarantineGuard.py). A zip archive hands out the asset's file
bytes, so the zip service runs the same guard through `require_distributable_asset(check_quarantine=)`:

- create refuses a quarantined asset with the documented refusal, and starts no job;
- the download URL refuses a job whose asset was quarantined after the job was created, and signs
  nothing; another user's archive (the shared view) is refused the same way;
- an asset under a granted exception, or any asset while the block is off, is not refused, and with
  the block off the asset-state table is never read;
- a caller the data-level check denies gets the leak-free 404 before the guard reads any state, so
  the zip routes are no compliance-state oracle.
"""

from unittest.mock import MagicMock, patch

import pytest

import common.compliance.quarantineGuard as guard
from backend.tests.handlers.zipDownloads import zipHarness as h
from backend.tests.quarantineRefusalBody import assert_is_the_quarantine_refusal

pytestmark = pytest.mark.unit

FILE_SELECTION = {"selection": [{"type": "file", "relativePath": "/a.glb"}]}
DOWNLOAD_ROUTE = ("POST", r"/database/db1/assets/asset1/download")


def _state_table(item):
    table = MagicMock(name="compliance_asset_state_table")
    table.get_item.return_value = {"Item": item} if item is not None else {}
    return table


def _quarantined(exception_granted=False):
    row = {"databaseId": "db1", "assetId": "asset1", "complianceState": "quarantined"}
    if exception_granted:
        row["exceptionGranted"] = True
    return row


def _guard(table, enabled=True):
    return patch.multiple(guard, quarantine_blocks_download=enabled,
                          compliance_asset_state_table=table)


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    return e


def _create(e, user="alice"):
    h.AUTH.user = user
    return e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, body=FILE_SELECTION), None)


def _url(e, request_id, user="alice"):
    h.AUTH.user = user
    path = h.asset_path("db1", "asset1", f"/{request_id}/url")
    return e.svc.lambda_handler(h.api_event(
        "POST", path, {"databaseId": "db1", "assetId": "asset1", "requestId": request_id}), None)


def _succeeded_job(e, user="alice"):
    """A job created while the asset was compliant, whose archive has landed."""
    response = _create(e, user)
    assert response["statusCode"] == 200, response
    row = next(iter(e.zip.items.values()))
    row["status"] = "SUCCEEDED"
    e.s3.add(row["archiveKey"], size=4242, bucket=h.AUX)
    return h.body_of(response)["requestId"]


def test_create_refuses_a_quarantined_asset_and_starts_no_job(env):
    table = _state_table(_quarantined())
    with _guard(table):
        response = _create(env)
    assert_is_the_quarantine_refusal(response)
    assert env.zip.items == {}
    assert env.lam.calls == []
    table.get_item.assert_called_once_with(Key={"databaseId": "db1", "assetId": "asset1"})


def test_the_url_refuses_an_asset_quarantined_after_the_job_was_created(env):
    request_id = _succeeded_job(env)
    with _guard(_state_table(_quarantined())):
        response = _url(env, request_id)
    assert_is_the_quarantine_refusal(response)
    assert env.s3.presigned == []


def test_another_users_archive_is_refused_the_same_way(env):
    request_id = _succeeded_job(env, user="bob")
    with _guard(_state_table(_quarantined())):
        response = _url(env, request_id, user="alice")
    assert_is_the_quarantine_refusal(response)
    assert env.s3.presigned == []


def test_a_granted_exception_is_not_refused(env):
    with _guard(_state_table(_quarantined(exception_granted=True))):
        request_id = _succeeded_job(env)
        assert _url(env, request_id)["statusCode"] == 200
    assert len(env.s3.presigned) == 1


def test_with_the_block_off_nothing_is_refused_and_no_state_is_read(env):
    table = _state_table(_quarantined())
    with _guard(table, enabled=False):
        request_id = _succeeded_job(env)
        assert _url(env, request_id)["statusCode"] == 200
    table.get_item.assert_not_called()


def test_a_denied_caller_gets_the_leak_free_404_before_any_state_read(env):
    request_id = _succeeded_job(env, user="bob")
    h.AUTH.denied_routes.add(DOWNLOAD_ROUTE)
    table = _state_table(_quarantined())
    with _guard(table):
        url_response = _url(env, request_id, user="alice")
        h.AUTH.denied_assets.add("asset1")
        create_response = _create(env, user="mallory")
    assert url_response["statusCode"] == 404
    assert create_response["statusCode"] == 404
    table.get_item.assert_not_called()
