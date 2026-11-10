# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""One function serves the zip download API routes and the delayed sweep passes: lambda_handler
dispatches on the event's envelope. There is no terminal callback branch: a job follows its run by
its stored workflowExecutionId and the read-time reconcile.

- An API Gateway proxy event (it always has ``requestContext``) is an API request, whatever its body
  imitates and whatever top-level keys sit next to the ``requestContext``; it runs the API path with
  Tier-1 authorization and never the sweep branch.
- A sweep pass (top-level ``action: sweep``) without a ``requestContext`` reaches its own branch.
- Any other shape, a former terminal callback payload (top-level ``callbackType``) included, is
  ignored: no read, no write, no invoke.
"""

import json
import os

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

REQUEST_ID = "0mw4e6byk-" + "d" * 32
SERVICE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers",
                       "assets", "zipDownloadService.py")


def _callback_payload(status="ABORTED"):
    """The terminal callback payload the workflow core used to send (no longer handled)."""
    return {"callbackType": "systemPipelineTerminal", "schemaVersion": 1,
            "idempotencyKey": "exec-1:" + status, "workflowExecutionId": "exec-1",
            "triggerType": "Zip-Download", "executionStatus": status, "executionError": "",
            "writer": "abort",
            "systemExecutionContext": {"v": 1, "requestId": REQUEST_ID, "userId": "alice",
                                       "databaseId": "db1", "assetId": "asset1", "manifestKey": "m"}}


def _sweep_payload(final=False):
    return {"action": "sweep", "final": final,
            "zipSweep": {"userId": "alice", "requestId": REQUEST_ID}}


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    prefix = e.zd.request_prefix("db1", "asset1", "alice", REQUEST_ID)
    e.zip.items[("alice", REQUEST_ID)] = {
        "userId": "alice", "requestId": REQUEST_ID, "databaseId": "db1", "assetId": "asset1",
        "userAssetKey": "alice#db1#asset1", "status": "RUNNING", "workflowExecutionId": "exec-1",
        "archiveKey": prefix + "x.zip", "manifestKey": prefix + "manifest.json",
        "createdAt": "2026-11-09T00:00:00Z", "expiresAt": 4102444800}
    e.s3.uploads.append({"Bucket": h.AUX, "Key": prefix + "x.zip", "UploadId": "u1"})
    e.calls = []
    # Spies on the two branches (a module without one of them has nothing to spy on, and every
    # test then fails on its own assertion).
    real = {name: getattr(e.svc, name) for name in ("handle_api_request", "handle_sweep")
            if hasattr(e.svc, name)}

    def spy(name):
        def wrapper(*args, **kwargs):
            e.calls.append(name)
            return real[name](*args, **kwargs)
        return wrapper

    for name in real:
        setattr(e.svc, name, spy(name))
    return e


def _row(env):
    return env.zip.items[("alice", REQUEST_ID)]


def _snapshot(env):
    items = {"|".join(key): row for key, row in env.zip.items.items()}
    return (json.dumps(items, default=str, sort_keys=True), list(env.zip.writes),
            list(env.s3.aborted), dict(env.s3.puts), list(env.lam.calls))


class TestEachShapeReachesItsBranch:
    def test_an_api_gateway_event_runs_the_api_path(self, env):
        response = env.svc.lambda_handler(
            h.api_event("GET", h.asset_path(suffix=f"/{REQUEST_ID}"),
                        {"databaseId": "db1", "assetId": "asset1", "requestId": REQUEST_ID}), None)
        assert env.calls == ["handle_api_request"]
        assert response["statusCode"] == 200

    def test_a_sweep_pass_runs_the_sweep_branch(self, env):
        env.svc.lambda_handler(_sweep_payload(), None)
        assert env.calls == ["handle_sweep"]


class TestApiRequestsNeverTakeAnEventBranch:
    @pytest.mark.parametrize("body", [_callback_payload("ABORTED"), _sweep_payload(final=True)])
    @pytest.mark.parametrize("method", ["POST", "GET", "DELETE"])
    def test_an_event_like_body_is_an_api_request(self, env, body, method):
        aborted_before = list(env.s3.aborted)
        response = env.svc.lambda_handler(
            h.api_event(method, h.asset_path(suffix=f"/{REQUEST_ID}"),
                        {"databaseId": "db1", "assetId": "asset1", "requestId": REQUEST_ID},
                        body=body), None)
        assert env.calls == ["handle_api_request"]
        assert "statusCode" in response
        # Nothing ends this RUNNING job FAILED "Cancelled from workflow executions" (cancelledBy
        # execution) and the sweep does not abort its upload.
        assert _row(env).get("cancelledBy") != "execution"
        assert _row(env).get("errorMessage") != "Cancelled from workflow executions"
        if method != "DELETE":
            # GET reads the caller's own job; POST on the job path is not a route.
            assert _row(env)["status"] == "RUNNING"
            assert env.s3.aborted == aborted_before

    @pytest.mark.parametrize("extra", [
        {"callbackType": "systemPipelineTerminal", "schemaVersion": 1, "triggerType": "Zip-Download"},
        {"action": "sweep", "zipSweep": {"userId": "alice", "requestId": REQUEST_ID}},
    ])
    def test_top_level_marker_next_to_a_request_context_is_still_an_api_request(self, env, extra):
        event = dict(h.api_event("GET", h.asset_path(suffix=f"/{REQUEST_ID}"),
                                 {"databaseId": "db1", "assetId": "asset1", "requestId": REQUEST_ID}),
                     **extra)
        env.svc.lambda_handler(event, None)
        assert env.calls == ["handle_api_request"]
        assert _row(env)["status"] == "RUNNING"

    def test_an_unauthenticated_event_like_request_is_denied_without_a_write(self, env):
        h.AUTH.user = None
        before = _snapshot(env)
        response = env.svc.lambda_handler(
            h.api_event("POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"},
                        body=_callback_payload("ABORTED")), None)
        assert env.calls == ["handle_api_request"]
        assert response["statusCode"] == 403
        assert _snapshot(env) == before

    def test_a_request_context_of_any_value_is_an_api_request(self, env):
        event = dict(_callback_payload("ABORTED"), requestContext=None)
        env.svc.lambda_handler(event, None)
        assert env.calls == ["handle_api_request"]
        assert _row(env)["status"] == "RUNNING"


class TestUnknownShapesAreIgnored:
    @pytest.mark.parametrize("event", [
        {},
        None,
        "a string",
        ["a", "list"],
        {"source": "aws.states", "detail-type": "Step Functions Execution Status Change",
         "detail": {"name": "exec-1", "status": "ABORTED"}},
        {"action": "other", "zipSweep": {"userId": "alice", "requestId": REQUEST_ID}},
        {"callbackType": "other", "systemExecutionContext": {"userId": "alice", "requestId": REQUEST_ID}},
        _callback_payload("ABORTED"),
        _callback_payload("FAILED"),
        {"body": json.dumps(_callback_payload("ABORTED"))},
    ])
    def test_ignored_with_no_side_effect(self, env, event):
        before = _snapshot(env)
        result = env.svc.lambda_handler(event, None)
        assert result == {"status": "ignored", "reason": "unknown event shape"}
        assert env.calls == []
        assert _snapshot(env) == before
        assert _row(env)["status"] == "RUNNING"


def test_no_callback_module_or_branch_exists(env):
    """No separate callback handler file, and no callback branch in the service."""
    handlers_dir = os.path.dirname(os.path.dirname(SERVICE))
    assert os.path.isfile(SERVICE)
    assert not os.path.exists(os.path.join(handlers_dir, "zipDownloads"))
    assert not hasattr(env.svc, "handle_callback")
    with open(SERVICE, encoding="utf-8") as source:
        assert "callbackType" not in source.read()
