# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip retention is a fixed 7 days (#1125, owner decision 2026-11-10).

A job's expiresAt (the zip download requests table's TTL attribute) is createdAt plus 7 days, a
deployment's leftover ZIP_DOWNLOAD_RETENTION_DAYS variable changes nothing, and a job past 7 days is
absent from the per-asset list, the global list, get and the download URL route even while TTL has
not removed the row yet. The backend constant equals the CDK constant the vams.preparedDownloads/
lifecycle rule uses.
"""

import pathlib
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

DAY = 86400
FILE_SELECTION = {"selection": [{"type": "file", "relativePath": "/a.glb"}]}


def _env(extra_env=None):
    h.AUTH.__init__()
    e = h.load_handlers(env=extra_env)
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    return e


@pytest.fixture
def env():
    return _env()


def _create(e, user="alice"):
    h.AUTH.user = user
    response = e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path("db1", "asset1"), {"databaseId": "db1", "assetId": "asset1"},
        body=FILE_SELECTION), None)
    assert response["statusCode"] in (200, 202), response
    return h.body_of(response)["requestId"]


def _route(e, method, request_id, suffix="", user="alice"):
    h.AUTH.user = user
    path = h.asset_path("db1", "asset1", f"/{request_id}{suffix}")
    return e.svc.lambda_handler(h.api_event(method, path, {"databaseId": "db1", "assetId": "asset1",
                                                           "requestId": request_id}), None)


def _list_ids(e, asset=True, user="alice"):
    h.AUTH.user = user
    if asset:
        event = h.api_event("GET", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"})
    else:
        event = h.api_event("GET", "/download/zip", {})
    return [item["requestId"] for item in h.body_of(e.svc.lambda_handler(event, None))["Items"]]


def _age_row(e, request_id, days):
    """Rewrite the row as if it had been created `days` ago (TTL has not removed it yet)."""
    row = next(r for r in e.zip.items.values() if r["requestId"] == request_id)
    created = datetime.now(timezone.utc) - timedelta(days=days)
    row["createdAt"] = e.zd.iso(created)
    row["expiresAt"] = e.zd.expires_at_epoch(created)
    row["status"] = "SUCCEEDED"
    e.s3.add(row["archiveKey"], size=10, bucket=h.AUX)
    return row


def test_the_retention_is_seven_days():
    e = _env()
    assert e.zd.ZIP_DOWNLOAD_RETENTION_DAYS == 7
    assert e.svc.RETENTION_DAYS == 7


def test_a_new_job_expires_seven_days_after_it_was_created(env):
    before = int(time.time())
    request_id = _create(env)
    after = int(time.time())
    row = next(iter(env.zip.items.values()))
    assert row["requestId"] == request_id
    assert before + 7 * DAY <= row["expiresAt"] <= after + 7 * DAY
    assert isinstance(row["expiresAt"], int)


def test_a_leftover_retention_variable_is_ignored():
    # A deployment upgraded from a build that passed the retention as configuration.
    e = _env({"ZIP_DOWNLOAD_RETENTION_DAYS": "30"})
    assert e.svc.RETENTION_DAYS == 7
    before = int(time.time())
    _create(e)
    row = next(iter(e.zip.items.values()))
    assert row["expiresAt"] <= int(time.time()) + 7 * DAY
    assert row["expiresAt"] >= before + 7 * DAY


def test_a_job_past_seven_days_is_absent_from_list_get_and_url(env):
    old_id = _create(env)
    fresh_id = _create(env)
    _age_row(env, old_id, days=8)
    _age_row(env, fresh_id, days=6)

    assert _list_ids(env) == [fresh_id]
    assert _list_ids(env, asset=False) == [fresh_id]
    assert _route(env, "GET", old_id)["statusCode"] == 404
    assert _route(env, "POST", old_id, suffix="/url")["statusCode"] == 404
    assert _route(env, "GET", fresh_id)["statusCode"] == 200
    assert _route(env, "POST", fresh_id, suffix="/url")["statusCode"] == 200


def test_the_cdk_constant_equals_the_backend_constant(env):
    storage_ts = (pathlib.Path(__file__).resolve().parents[4] / "infra" / "lib" / "nestedStacks"
                  / "storage" / "storageBuilder-nestedStack.ts").read_text()
    match = re.search(r"export const ZIP_DOWNLOAD_RETENTION_DAYS = (\d+);", storage_ts)
    assert match, "ZIP_DOWNLOAD_RETENTION_DAYS not found in storageBuilder-nestedStack.ts"
    assert int(match.group(1)) == env.zd.ZIP_DOWNLOAD_RETENTION_DAYS
    # The lifecycle rule reads the constant, never configuration.
    assert "Duration.days(ZIP_DOWNLOAD_RETENTION_DAYS)" in storage_ts
    assert "zipDownloads.retentionDays" not in storage_ts
