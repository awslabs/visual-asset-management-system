# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""#1125 D34: a leak-free 404 that the handler recorded as a denial is the request's one outcome.

The 404 a denial answers is the same as a miss's (D29), but a denial is recorded by the handler as
`denied` under the route's own action. So the request outcome of `@audited_handler` sees the action
as recorded and adds no `failed` record, and the 404 is built without the event, so `general_error`
writes no errors-group record (which inside the audit context carries the route's action, failed).
A missing asset or job is not a denial: its 404 keeps the event and the request outcome records it.

Every leak-free 404 path is covered: create, the per-asset list, the all-users list, get, the URL
(own job and another user's) and delete (both intents).
"""

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

FILE_SELECTION = {"selection": [{"type": "file", "relativePath": "/a.glb"}]}
DOWNLOAD_ROUTE = ("POST", r"/database/db1/assets/asset1/download")
ROUTE_ACTIONS = {
    ("POST", ""): "zipDownloadCreate", ("GET", ""): "zipDownloadList",
    ("GET", "-all-users"): "zipDownloadListAllUsers", ("GET", "/job"): "zipDownloadGet",
    ("POST", "/job/url"): "zipDownloadUrl", ("DELETE", "/job"): "zipDownloadDelete",
}


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    real = e.svc.general_error
    e.error_events = []

    def recording_general_error(*args, **kwargs):
        # The errors-group record is written by general_error only when it is given the event.
        e.error_events.append(kwargs.get("event"))
        return real(*args, **kwargs)

    e.svc.general_error = recording_general_error
    return e


def _create(e, user="alice"):
    h.AUTH.user = user
    response = e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, body=FILE_SELECTION), None)
    assert response["statusCode"] == 200, response
    return h.body_of(response)["requestId"]


def _call(e, method, route, user, asset_id="asset1", request_id=None, query=None, body=None):
    h.AUTH.user = user
    params = {"databaseId": "db1", "assetId": asset_id}
    if route == "-all-users":
        path = f"/database/db1/assets/{asset_id}/download/zip-all-users"
    elif route.startswith("/job"):
        params["requestId"] = request_id
        path = h.asset_path("db1", asset_id, f"/{request_id}{route[len('/job'):]}")
    else:
        path = h.asset_path("db1", asset_id)
    e.audit.reset_mock()
    e.error_events.clear()
    event = h.api_event(method, path, params, body=body, query=query)
    return event, e.svc.lambda_handler(event, None)


def _outcomes(e):
    return [(c.args[1], c.kwargs.get("outcome", "success")) for c in e.audit.call_args_list]


def _assert_one_denial(e, response, action):
    assert response["statusCode"] == 404
    assert _outcomes(e) == [(action, "denied")]
    assert e.error_events == [None], "the denial's 404 also wrote an errors-group record"


@pytest.mark.parametrize("method,route", [("POST", ""), ("GET", ""), ("GET", "-all-users")])
def test_an_asset_route_denial_is_one_outcome(env, method, route):
    h.AUTH.denied_assets.add("asset1")
    _, response = _call(env, method, route, "mallory",
                        body=FILE_SELECTION if method == "POST" else None)
    _assert_one_denial(env, response, ROUTE_ACTIONS[(method, route)])


@pytest.mark.parametrize("method,route", [("POST", ""), ("GET", ""), ("GET", "-all-users")])
def test_a_missing_asset_is_left_to_the_request_outcome(env, method, route):
    event, response = _call(env, method, route, "mallory", asset_id="nosuchasset",
                            body=FILE_SELECTION if method == "POST" else None)
    assert response["statusCode"] == 404
    assert _outcomes(env) == []
    assert env.error_events == [event]


@pytest.mark.parametrize("method,route", [("GET", "/job"), ("POST", "/job/url")])
def test_a_denied_read_of_the_callers_own_job_is_one_outcome(env, method, route):
    request_id = _create(env)
    h.AUTH.denied_assets.add("asset1")
    _, response = _call(env, method, route, "alice", request_id=request_id)
    _assert_one_denial(env, response, ROUTE_ACTIONS[(method, route)])


def test_a_denied_url_of_another_users_job_is_one_outcome(env):
    request_id = _create(env, "bob")
    h.AUTH.denied_routes.add(DOWNLOAD_ROUTE)
    _, response = _call(env, "POST", "/job/url", "alice", request_id=request_id)
    _assert_one_denial(env, response, "zipDownloadUrl")


@pytest.mark.parametrize("intent", [None, "cancel"])
def test_a_denied_delete_is_one_outcome_under_the_routes_action(env, intent):
    # An active job with or without intent=cancel is a cancel; the denial is still recorded under the
    # DELETE route's action, with the intent in details.
    request_id = _create(env)
    h.AUTH.denied_assets.add("asset1")
    _, response = _call(env, "DELETE", "/job", "alice", request_id=request_id,
                        query={"intent": intent} if intent else None)
    _assert_one_denial(env, response, "zipDownloadDelete")
    assert env.audit.call_args.kwargs["details"] == {"intent": "cancel"}
    assert ("alice", request_id) in env.zip.items


def test_another_users_job_on_get_and_delete_is_a_miss_not_a_denial(env):
    # Keyed by the caller, the lookup never reads another user's job: a miss, left to the request
    # outcome, never a denial.
    request_id = _create(env, "bob")
    for method in ("GET", "DELETE"):
        event, response = _call(env, method, "/job", "alice", request_id=request_id)
        assert response["statusCode"] == 404
        assert _outcomes(env) == []
        assert env.error_events == [event]
