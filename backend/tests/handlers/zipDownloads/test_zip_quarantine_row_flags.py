# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 D36: the shared-view row flag `canDownload` follows the quarantine block, as the URL route does.

With `app.compliance.quarantineBlocksDownload` on, the URL route refuses a quarantined asset without a
granted exception (D35). The all-users list used to report `canDownload: true` for a finished job on
such an asset, so the web app offered a Download that failed. The list now asks the same check the
create and URL routes run (`check_quarantine_block`), once per call for the one asset:

- a quarantined asset: every finished row reports `canDownload: false` (own and another user's);
- a granted exception, a compliant or unrecorded asset, or the block off: the flag is as before, and
  with the block off the asset-state table is never read;
- a call with no finished job still asks once, never per row;
- an unreadable state is advisory: the flag stays as it was and the URL route decides.
"""

from unittest.mock import MagicMock, patch

import pytest

import common.compliance.quarantineGuard as guard
from backend.tests.handlers.zipDownloads import zipHarness as h
from backend.tests.handlers.zipDownloads.test_zip_quarantine_block import (
    _guard, _quarantined, _state_table,
)
from backend.tests.handlers.zipDownloads.test_zip_shared_view import (
    _all_users, _create, _succeed,
)
from backend.tests.quarantineRefusalBody import assert_is_the_quarantine_refusal

pytestmark = pytest.mark.unit


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    return e


def _two_finished_jobs(env):
    mine, theirs = _create(env, "alice"), _create(env, "bob")
    _succeed(env, "alice", mine)
    _succeed(env, "bob", theirs)
    return mine, theirs


def _flags(env, user="alice"):
    return {i["requestId"]: i["canDownload"] for i in h.body_of(_all_users(env, user))["Items"]}


def test_regression_a_quarantined_asset_offers_no_download_on_any_row(env):
    # Jobs finished while the asset was compliant; it is quarantined afterwards.
    mine, theirs = _two_finished_jobs(env)
    assert _flags(env) == {mine: True, theirs: True}
    with _guard(_state_table(_quarantined())):
        assert _flags(env) == {mine: False, theirs: False}


def test_the_flag_agrees_with_what_the_url_route_answers(env):
    mine, theirs = _two_finished_jobs(env)
    with _guard(_state_table(_quarantined())):
        assert not any(_flags(env).values())
        h.AUTH.user = "alice"
        path = h.asset_path("db1", "asset1", f"/{theirs}/url")
        response = env.svc.lambda_handler(h.api_event(
            "POST", path, {"databaseId": "db1", "assetId": "asset1", "requestId": theirs}), None)
        assert_is_the_quarantine_refusal(response)


def test_a_granted_exception_keeps_the_flag(env):
    mine, theirs = _two_finished_jobs(env)
    with _guard(_state_table(_quarantined(exception_granted=True))):
        assert _flags(env) == {mine: True, theirs: True}


def test_an_unfinished_job_stays_without_a_download_whatever_the_state(env):
    running = _create(env, "alice")
    with _guard(_state_table(None)):
        assert _flags(env) == {running: False}


def test_with_the_block_off_the_flag_is_unchanged_and_no_state_is_read(env):
    mine, theirs = _two_finished_jobs(env)
    table = _state_table(_quarantined())
    with _guard(table, enabled=False):
        assert _flags(env) == {mine: True, theirs: True}
    table.get_item.assert_not_called()


def test_the_state_is_read_once_per_call_not_per_row(env):
    for _ in range(3):
        _succeed(env, "bob", _create(env, "bob"))
    table = _state_table(_quarantined())
    with _guard(table):
        assert len(h.body_of(_all_users(env, "alice"))["Items"]) == 3
    table.get_item.assert_called_once()


def test_an_unreadable_state_leaves_the_flag_to_the_url_route(env):
    mine, theirs = _two_finished_jobs(env)
    table = MagicMock(name="compliance_asset_state_table")
    table.get_item.side_effect = RuntimeError("throttled")
    with _guard(table):
        assert _flags(env) == {mine: True, theirs: True}


def test_the_per_user_and_per_asset_lists_carry_no_flag(env):
    # Their rows are the caller's own; the web follows the route decisions there. Pinned so a flag
    # added to them later is a deliberate change with its own quarantine check.
    _two_finished_jobs(env)
    h.AUTH.user = "alice"
    response = env.svc.lambda_handler(h.api_event(
        "GET", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}), None)
    assert all("canDownload" not in i for i in h.body_of(response)["Items"])
