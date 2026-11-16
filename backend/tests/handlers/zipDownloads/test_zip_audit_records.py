# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip action records (backend/CLAUDE.md "Audit Logging", the record contract).

Each zip download route records its registered action once, on success, with the ids, counts and
value sections its kind allows: the lists and the get are reads (request parameters and an item
count), the URL is a download (bytes, and both users for another user's archive), create, cancel
and delete are writes (changes and the prior status). The read-time reconcile records the job it
finalized (zipDownloadReconcile) for the request's own event, once, and a delayed sweep pass that
acted records zipDownloadSweep for the job's requester. Error responses record nothing here: the
request outcome (@audited_handler) records them.
"""

from types import SimpleNamespace

import pytest

from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit

FILE_SELECTION = {"selection": [{"type": "file", "relativePath": "/a.glb"}]}


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    return e


def _create(e, user="alice"):
    h.AUTH.user = user
    response = e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"}, body=FILE_SELECTION), None)
    assert response["statusCode"] == 200, response
    return h.body_of(response)["requestId"]


def _job_route(e, method, request_id, user="alice", query=None):
    h.AUTH.user = user
    event = h.api_event(method, h.asset_path("db1", "asset1", f"/{request_id}"),
                        {"databaseId": "db1", "assetId": "asset1", "requestId": request_id}, query=query)
    return e.svc.lambda_handler(event, None)


def _records(e, action=None):
    return [c for c in e.audit.call_args_list if action is None or c.args[1] == action]


class TestReads:
    def test_the_asset_list_records_its_page_bounds_and_count(self, env):
        _create(env)
        env.audit.reset_mock()
        h.AUTH.user = "alice"
        env.svc.lambda_handler(h.api_event("GET", h.asset_path(), {"databaseId": "db1", "assetId": "asset1"},
                                           query={"pageSize": "5", "maxItems": "5"}), None)
        (record,) = _records(env)
        assert record.args[1] == "zipDownloadList"
        assert (record.kwargs["database_id"], record.kwargs["asset_id"]) == ("db1", "asset1")
        assert record.kwargs["item_count"] == 1
        assert record.kwargs["request_parameters"] == {"maxItems": 5, "pageSize": 5}

    def test_the_global_list_records_no_asset_and_never_the_token(self, env):
        _create(env)
        env.audit.reset_mock()
        h.AUTH.user = "alice"
        token = h.encode_token({"userId": "alice", "requestId": "0" * 10})
        env.svc.lambda_handler(h.api_event("GET", "/download/zip", {}, query={"startingToken": token}), None)
        (record,) = _records(env)
        assert record.args[1] == "zipDownloadList"
        assert record.kwargs["database_id"] is None and record.kwargs["asset_id"] is None
        assert record.kwargs["request_parameters"]["continued"] is True
        assert token not in repr(record.kwargs)

    def test_the_all_users_list_is_its_own_action(self, env):
        _create(env, "bob")
        env.audit.reset_mock()
        h.AUTH.user = "alice"
        env.svc.lambda_handler(h.api_event("GET", "/database/db1/assets/asset1/download/zip-all-users",
                                           {"databaseId": "db1", "assetId": "asset1"}), None)
        (record,) = _records(env)
        assert record.args[1] == "zipDownloadListAllUsers"
        assert record.kwargs["item_count"] == 1


class TestWrites:
    def test_a_cancel_records_the_status_it_set_and_the_one_it_replaced(self, env):
        request_id = _create(env)
        env.audit.reset_mock()
        response = _job_route(env, "DELETE", request_id, query={"intent": "cancel"})
        assert h.body_of(response)["action"] == "cancelled"
        (record,) = _records(env)
        assert record.args[1] == "zipDownloadCancel"
        assert record.kwargs["entity_id"] == request_id
        assert record.kwargs["changes"] == {"status": "CANCELLED"}
        assert record.kwargs["previous"] == {"status": "QUEUED"}
        assert record.kwargs["executionId"] == "exec-1"

    def test_a_cancel_of_a_finished_job_records_that_it_had_ended(self, env):
        request_id = _create(env)
        env.zip.items[("alice", request_id)]["status"] = "FAILED"
        env.audit.reset_mock()
        response = _job_route(env, "DELETE", request_id, query={"intent": "cancel"})
        assert h.body_of(response)["action"] == "ended"
        (record,) = _records(env)
        assert record.args[1] == "zipDownloadCancel"
        assert record.kwargs["details"] == {"result": "ended", "status": "FAILED"}
        assert "changes" not in record.kwargs

    def test_a_delete_records_the_prior_status(self, env):
        request_id = _create(env)
        env.zip.items[("alice", request_id)]["status"] = "FAILED"
        env.audit.reset_mock()
        assert h.body_of(_job_route(env, "DELETE", request_id))["action"] == "deleted"
        (record,) = _records(env)
        assert record.args[1] == "zipDownloadDelete"
        assert record.kwargs["entity_id"] == request_id
        assert record.kwargs["previous"]["status"] == "FAILED"

    def test_a_denied_delete_is_recorded_and_answers_like_a_miss(self, env):
        request_id = _create(env)
        env.zip.items[("alice", request_id)]["status"] = "FAILED"
        h.AUTH.denied_assets.add("asset1")
        env.audit.reset_mock()
        response = _job_route(env, "DELETE", request_id)
        assert response["statusCode"] == 404
        assert h.body_of(response)["message"] == env.zd.MSG_NOT_FOUND
        recorded = [(c.args[1], c.kwargs["outcome"], c.kwargs["reason"]) for c in _records(env)]
        assert recorded == [("zipDownloadDelete", "denied", "forbidden")]
        assert ("alice", request_id) in env.zip.items


class TestReconcile:
    def test_a_finalize_is_recorded_once_for_the_requests_own_event(self, env, monkeypatch):
        request_id = _create(env)
        request_event = object()
        monkeypatch.setattr(env.svc, "current_audit_context", lambda: SimpleNamespace(event=request_event))
        h.set_run_status(env, "exec-1", "FAILED", "boom")
        env.audit.reset_mock()
        assert _job_route(env, "GET", request_id)["statusCode"] == 200
        assert _job_route(env, "GET", request_id)["statusCode"] == 200
        reconciled = _records(env, "zipDownloadReconcile")
        assert len(reconciled) == 1
        assert reconciled[0].args[0] is request_event
        assert reconciled[0].kwargs["changes"] == {"status": "FAILED"}
        assert reconciled[0].kwargs["previous"] == {"status": "QUEUED"}
        assert reconciled[0].kwargs["details"] == {"requestedBy": "alice"}
        assert [c.args[1] for c in _records(env, "zipDownloadGet")] == ["zipDownloadGet", "zipDownloadGet"]

    def test_outside_a_request_context_nothing_is_recorded(self, env):
        request_id = _create(env)
        env.audit.reset_mock()
        h.end_run_and_reconcile(env, "alice", request_id, "FAILED")
        assert env.zip.items[("alice", request_id)]["status"] == "FAILED"
        assert _records(env) == []


class TestSweep:
    def _sweep(self, env, monkeypatch, result):
        monkeypatch.setattr(env.zd, "sweep_recorded_uploads", lambda *a, **k: result)
        event = {"action": "sweep", "final": True,
                 "zipSweep": {"userId": "alice", "requestId": "r1", "databaseId": "db1", "assetId": "asset1"}}
        env.audit.reset_mock()
        assert env.svc.lambda_handler(event, None) == result
        return event

    def test_a_pass_that_acted_is_recorded_for_the_requester(self, env, monkeypatch):
        event = self._sweep(env, monkeypatch, {"swept": True, "aborted": 2, "remaining": 0})
        (record,) = _records(env)
        assert record.args[0] is event and record.args[1] == "zipDownloadSweep"
        assert record.kwargs["on_behalf_of"] == "alice" and record.kwargs["entity_id"] == "r1"
        assert record.kwargs["details"] == {"pass": "final", "aborted": 2, "remaining": 0}

    def test_a_pass_with_nothing_to_do_records_nothing(self, env, monkeypatch):
        self._sweep(env, monkeypatch, {"swept": False, "reason": "no recorded upload"})
        assert _records(env) == []
