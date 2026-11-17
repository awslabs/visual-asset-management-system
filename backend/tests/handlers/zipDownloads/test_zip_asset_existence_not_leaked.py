# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 D29: no zip download route tells a caller whether an asset exists.

For every asset-scoped route, an asset that does not exist and one the caller may not read (Tier-2
GET denied) get the same status and the same body. The asset routes (create, per-asset list,
all-users list) answer 404 "Asset not found"; the job routes (get, url, delete) answer the job-level
404 for both. Only the audit record tells them apart: an unreadable asset is recorded as a denial
of the route's action, and a missing one by the request outcome (@audited_handler, failed notFound).
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


ROUTE_ACTIONS = {"create": "zipDownloadCreate", "list": "zipDownloadList",
                 "listAllUsers": "zipDownloadListAllUsers"}


def _audited(e):
    """(action, outcome, reason, databaseId, assetId) of every record the handler wrote."""
    return [(c.args[1], c.kwargs.get("outcome", "success"), c.kwargs.get("reason"),
             c.kwargs.get("database_id"), c.kwargs.get("asset_id")) for c in e.audit.call_args_list]


@pytest.mark.unit
@pytest.mark.parametrize("route", ["create", "list", "listAllUsers", "get", "url", "delete"])
def test_a_missing_and_an_unreadable_asset_answer_alike(env, route):
    missing = _call(env, route, MISSING)
    denied = _call(env, route, DENIED)
    assert missing == denied
    assert missing[0] == 404


@pytest.mark.unit
@pytest.mark.parametrize("route", ["create", "list", "listAllUsers"])
def test_the_asset_routes_answer_asset_not_found_and_record_the_denial(env, route):
    for asset_id in (MISSING, DENIED):
        env.audit.reset_mock()
        status, body = _call(env, route, asset_id)
        assert (status, body["message"]) == (404, "Asset not found")
        # An asset that exists is a denial of the route's action; a missing one writes nothing here
        # (the request outcome records the 404 as failed, notFound).
        expected = ([(ROUTE_ACTIONS[route], "denied", "forbidden", "db1", asset_id)]
                    if asset_id == DENIED else [])
        assert _audited(env) == expected, (route, asset_id)


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
