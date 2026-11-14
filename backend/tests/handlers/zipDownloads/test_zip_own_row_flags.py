# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 D37 (owner request): the caller's own lists carry the API's advisory `canDownload` / `canRemove`.

The per-user list (`GET /download/zip`) and the per-asset list (`GET .../download/zip`) used to carry no
row flags, so the web app showed Download from the role's route decision alone, including on a finished
job of a quarantined asset (the URL route refuses it, D35). Both lists now return the flags the
all-users list returns, from one definition (`_row_flags`):

- canDownload: SUCCEEDED, the URL route allowed for the caller, and the asset not blocked by quarantine
  (an own job is not held to the asset's file download route, as the URL route does not hold it);
- canRemove: the caller's own job and the delete route allowed;
- the quarantine state is read once per distinct asset per call, never per row, and never for a row
  that could not be downloaded anyway; an unreadable state leaves the flag to the URL route.
"""

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h
from backend.tests.handlers.zipDownloads.test_zip_quarantine_block import (
    _guard, _quarantined, _state_table,
)
from backend.tests.handlers.zipDownloads.test_zip_shared_view import _all_users, _create, _succeed

pytestmark = pytest.mark.unit

URL_ROUTE = ("POST", r"/database/db1/assets/asset1/download/zip/[^/]+/url")
DELETE_ROUTE = ("DELETE", r"/database/db1/assets/asset1/download/zip/[^/]+")


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    return e


def _per_user(e, user):
    h.AUTH.user = user
    return h.body_of(e.svc.lambda_handler(h.api_event("GET", "/download/zip"), None))["Items"]


def _per_asset(e, user):
    h.AUTH.user = user
    return h.body_of(e.svc.lambda_handler(h.api_event(
        "GET", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}), None))["Items"]


LISTS = [pytest.param(_per_user, id="per-user"), pytest.param(_per_asset, id="per-asset")]


def _flags(items):
    return {i["requestId"]: (i["canDownload"], i["canRemove"]) for i in items}


@pytest.mark.parametrize("lister", LISTS)
def test_regression_a_quarantined_asset_offers_no_download_on_the_callers_own_rows(env, lister):
    mine = _create(env, "alice")
    _succeed(env, "alice", mine)
    assert _flags(lister(env, "alice")) == {mine: (True, True)}
    with _guard(_state_table(_quarantined())):
        # Download is refused; cancelling or deleting the job is still the caller's.
        assert _flags(lister(env, "alice")) == {mine: (False, True)}


@pytest.mark.parametrize("lister", LISTS)
def test_a_granted_exception_or_the_block_off_keeps_the_download(env, lister):
    mine = _create(env, "alice")
    _succeed(env, "alice", mine)
    with _guard(_state_table(_quarantined(exception_granted=True))):
        assert _flags(lister(env, "alice")) == {mine: (True, True)}
    table = _state_table(_quarantined())
    with _guard(table, enabled=False):
        assert _flags(lister(env, "alice")) == {mine: (True, True)}
    table.get_item.assert_not_called()


@pytest.mark.parametrize("lister", LISTS)
def test_an_unfinished_job_has_no_download_and_reads_no_state(env, lister):
    running = _create(env, "alice")
    table = _state_table(_quarantined())
    with _guard(table):
        assert _flags(lister(env, "alice")) == {running: (False, True)}
    table.get_item.assert_not_called()


@pytest.mark.parametrize("lister", LISTS)
def test_the_flags_follow_the_role_route_decisions(env, lister):
    mine = _create(env, "alice")
    _succeed(env, "alice", mine)
    h.AUTH.denied_routes.add(URL_ROUTE)
    assert _flags(lister(env, "alice")) == {mine: (False, True)}
    h.AUTH.denied_routes.clear()
    h.AUTH.denied_routes.add(DELETE_ROUTE)
    assert _flags(lister(env, "alice")) == {mine: (True, False)}


@pytest.mark.parametrize("lister", LISTS)
def test_an_own_job_is_not_held_to_the_asset_file_download_route(env, lister):
    mine = _create(env, "alice")
    _succeed(env, "alice", mine)
    h.AUTH.denied_routes.add(("POST", r"/database/db1/assets/asset1/download"))
    assert _flags(lister(env, "alice")) == {mine: (True, True)}


def test_the_state_is_read_once_per_asset_per_call_not_per_row(env):
    for _ in range(3):
        _succeed(env, "alice", _create(env, "alice"))
    # A second asset with one finished job.
    h.put_asset(env, asset_id="asset2")
    env.s3.add("xasset2/a.glb", size=100, version="va2")
    h.AUTH.user = "alice"
    other = h.body_of(env.svc.lambda_handler(h.api_event(
        "POST", h.asset_path("db1", "asset2"), {"databaseId": "db1", "assetId": "asset2"},
        body={"selection": [{"type": "file", "relativePath": "/a.glb"}]}), None))["requestId"]
    row = env.zip.items[("alice", other)]
    row["status"] = "SUCCEEDED"
    table = _state_table(_quarantined())
    with _guard(table):
        items = _per_user(env, "alice")
    assert len(items) == 4
    assert all(i["canDownload"] is False for i in items)
    assert table.get_item.call_count == 2


def test_the_per_user_and_all_users_lists_agree_on_the_callers_own_rows(env):
    mine = _create(env, "alice")
    _succeed(env, "alice", mine)
    with _guard(_state_table(_quarantined())):
        own = _flags(_per_user(env, "alice"))
        shared = {i["requestId"]: (i["canDownload"], i["canRemove"])
                  for i in h.body_of(_all_users(env, "alice"))["Items"]}
    assert own == shared == {mine: (False, True)}
