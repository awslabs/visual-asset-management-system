# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 D29: no zip download route tells a caller whether an asset exists.

For every asset-scoped route, an asset that does not exist and one the caller may not read (Tier-2
GET denied) get the same status and the same body. The asset routes (create, per-asset list,
all-users list) answer 404 "Asset not found" and audit the call as zipDownloadNotFound with the
operation; the job routes (get, url, delete) answer the job-level 404 for both.
"""

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

FILE_SELECTION = {"selection": [{"type": "file", "relativePath": "/a.glb"}]}
MISSING = "assetmissing1"
DENIED = "asset1"


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    h.AUTH.user = "mallory"
    h.AUTH.denied_assets.add(DENIED)
    return e


REQUEST_ID = None


@pytest.fixture(autouse=True)
def _request_id(env):
    global REQUEST_ID
    REQUEST_ID = env.zd.new_request_id()


def _call(e, route, asset_id):
    path_params = {"databaseId": "db1", "assetId": asset_id}
    if route == "create":
        event = h.api_event("POST", h.asset_path("db1", asset_id), path_params, body=FILE_SELECTION)
    elif route == "list":
        event = h.api_event("GET", h.asset_path("db1", asset_id), path_params)
    elif route == "listAllUsers":
        event = h.api_event("GET", f"/database/db1/assets/{asset_id}/download/zip-all-users", path_params)
    else:
        method, suffix = {"get": ("GET", ""), "url": ("POST", "/url"), "delete": ("DELETE", "")}[route]
        event = h.api_event(method, h.asset_path("db1", asset_id, f"/{REQUEST_ID}{suffix}"),
                            {**path_params, "requestId": REQUEST_ID})
    response = e.svc.lambda_handler(event, None)
    return response["statusCode"], h.body_of(response)


def _audited(e, operation):
    return [c.args[2] for c in e.audit.call_args_list
            if c.args[1] == "zipDownloadNotFound" and c.args[2].get("operation") == operation]


@pytest.mark.unit
@pytest.mark.parametrize("route", ["create", "list", "listAllUsers", "get", "url", "delete"])
def test_a_missing_and_an_unreadable_asset_answer_alike(env, route):
    missing = _call(env, route, MISSING)
    denied = _call(env, route, DENIED)
    assert missing == denied
    assert missing[0] == 404


@pytest.mark.unit
@pytest.mark.parametrize("route", ["create", "list", "listAllUsers"])
def test_the_asset_routes_answer_asset_not_found_and_audit_both(env, route):
    for asset_id in (MISSING, DENIED):
        env.audit.reset_mock()
        status, body = _call(env, route, asset_id)
        assert (status, body["message"]) == (404, "Asset not found")
        assert _audited(env, route) == [{"databaseId": "db1", "assetId": asset_id, "operation": route}]


@pytest.mark.unit
def test_a_denied_create_writes_nothing(env):
    _call(env, "create", DENIED)
    assert env.zip.items == {} and env.s3.puts == {} and env.lam.calls == []


@pytest.mark.unit
def test_a_readable_asset_is_still_served(env):
    # Control: the same caller on a readable asset gets a job and a list.
    h.AUTH.denied_assets.clear()
    assert _call(env, "create", DENIED)[0] == 200
    status, body = _call(env, "list", DENIED)
    assert status == 200 and len(body["Items"]) == 1
