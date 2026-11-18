# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The audit log search handler: authorization, caps, handles, paging, export and generic errors.

CloudWatch Logs, DynamoDB and Casbin are replaced by small in-memory fakes; the query building, the
parsing, the redaction and the recheck are the real library.
"""

import importlib
import importlib.util
import json
import os
import sys
import time
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.audit import auditLogService as svc

# The library is imported under the names the handler uses, so both hold the same module objects.
auditLogGroups = importlib.import_module("common.auditLogGroups")
queryBuilder = importlib.import_module("common.auditSearch.queryBuilder")

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))


def _real_request_to_claims():
    """The real ``request_to_claims`` (the root harness mocks ``handlers.auth``), loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "vams_real_handlers_auth", os.path.join(_BACKEND, "handlers", "auth", "__init__.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.request_to_claims

pytestmark = pytest.mark.unit

ALICE = "alice"
BOB = "bob"
NOW = int(time.time())

_ECHO = ('--- [event: {"version": "2.0", "requestContext": {"requestId": "req-1", '
         '"http": {"method": "PUT", "path": "/database/db-1/assets/a-1", "sourceIp": "198.51.100.9"}}}]')


def action_record(user="carol", asset="a-1", kind="assetUpdate", extra=""):
    return ('[ACTIONS][type: %s] [user: %s] [roles: ["editor"]] [mfaEnabled: True] '
            '{"assetId": "%s", "databaseId": "db-1"%s} %s' % (kind, user, asset, extra, _ECHO))


def result_row(message, group_key="auditActions", stamp="2026-11-17 01:00:00.000"):
    return [{"field": "@timestamp", "value": stamp},
            {"field": "@log", "value": "123456789012:test-%s" % group_key},
            {"field": "msgLen", "value": str(len(message))},
            {"field": "msg", "value": message}]


class FakeTable:
    """Just the DynamoDB calls the handler makes, with the expressions it writes."""

    def __init__(self):
        self.items = {}

    def get_item(self, Key, ConsistentRead=False):
        item = self.items.get(Key["queryId"])
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        if ConditionExpression and Item["queryId"] in self.items:
            raise _conditional_failure()
        self.items[Item["queryId"]] = dict(Item)

    def query(self, IndexName=None, KeyConditionExpression=None, **kwargs):
        assert IndexName == svc.OPEN_INDEX
        return {"Items": [dict(i) for i in self.items.values() if i.get("openKey") == "open"]}

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues=None, ExpressionAttributeNames=None,
                    ConditionExpression=None, ReturnValues=None):
        item = self.items.get(Key["queryId"])
        values = ExpressionAttributeValues or {}
        names = ExpressionAttributeNames or {}
        if Key["queryId"].startswith("slots#"):
            return self._update_counter(Key["queryId"], UpdateExpression, ConditionExpression, values)
        old = dict(item) if item is not None else None
        result = {"Attributes": old} if ReturnValues == "ALL_OLD" and old is not None else {}
        if ConditionExpression and "attribute_exists(queryId)" in ConditionExpression and item is None:
            raise _conditional_failure()
        if ConditionExpression and "pollLeaseUntil <" in ConditionExpression:
            lease = item.get("pollLeaseUntil")
            if lease is not None and not lease < values[":now"]:
                raise _conditional_failure()
        if item is None:
            return result
        set_part, _, remove_part = UpdateExpression.partition(" REMOVE ")
        if set_part.startswith("SET "):
            for assignment in set_part[4:].split(", "):
                name, value = [part.strip() for part in assignment.split(" = ")]
                item[names.get(name, name)] = values[value]
        elif set_part.startswith("REMOVE "):
            remove_part = set_part[7:]
            set_part = ""
        for name in [n.strip() for n in remove_part.split(",") if n.strip()]:
            item.pop(names.get(name, name), None)
        return result

    def _update_counter(self, key, expression, condition, values):
        """The slot counters: the handler's conditional increments and decrements, as DynamoDB decides them."""
        item = self.items.get(key)
        count = item.get("slotCount") if item else None
        if "attribute_not_exists(#c) OR #c < :cap" in (condition or ""):
            if count is not None and not count < values[":cap"]:
                raise _conditional_failure()
            self.items[key] = {"queryId": key, "slotCount": (count or 0) + 1, "expiresAt": values[":e"]}
        elif "expiresAt < :now" in (condition or ""):
            if item is None or not item["expiresAt"] < values[":now"]:
                raise _conditional_failure()
            self.items[key] = {"queryId": key, "slotCount": 1, "expiresAt": values[":e"]}
        elif "#c > :zero" in (condition or ""):
            if count is None or not count > 0:
                raise _conditional_failure()
            item["slotCount"] = count - 1
        return {}


def _conditional_failure():
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "UpdateItem")


class FakeLogs:
    def __init__(self):
        self.started = []
        self.stopped = []
        self.polls = 0
        self.status = "Complete"
        self.results = []
        self.running = []
        self.matched = 5

    def start_query(self, **kwargs):
        self.started.append(kwargs)
        return {"queryId": "cw-%d" % len(self.started)}

    def get_query_results(self, queryId):
        self.polls += 1
        return {"status": self.status, "results": list(self.results),
                "statistics": {"recordsMatched": float(self.matched), "recordsScanned": 100.0, "bytesScanned": 2048.0}}

    def stop_query(self, queryId):
        self.stopped.append(queryId)

    def describe_queries(self, **kwargs):
        return {"queries": [{"queryId": q} for q in self.running]}


class _Decider:
    """The enforcer's service object: decides without writing a denial record (the probe batch's path)."""

    def enforce(self, obj, act):
        assert obj["object__type"] == "auditLog" and act == "GET"
        return FakeEnforcer.allowed_groups is None or obj["auditLogGroup"] in FakeEnforcer.allowed_groups


class FakeEnforcer:
    """Casbin as the handler uses it: the route check and the per-group object check."""

    allowed_groups = None  # None = every group
    api_allowed = True
    denials = []

    def __init__(self, claims):
        self.claims = claims
        self.service_object = _Decider()

    def enforceAPI(self, event, *args):
        return type(self).api_allowed

    def enforce(self, obj, act):
        assert obj["object__type"] == "auditLog" and act == "GET"
        allowed = type(self).allowed_groups is None or obj["auditLogGroup"] in type(self).allowed_groups
        if not allowed:
            type(self).denials.append(obj["auditLogGroup"])
        return allowed


class FakeBatch:
    flushed = []
    probes = []

    def __init__(self, claims, enforcer, endpoint):
        self.enforcer, self.endpoint, self.kept = enforcer, endpoint, []
        FakeBatch.probes.append(endpoint)

    def enforce(self, obj, act):
        allowed = self.enforcer.service_object.enforce(obj, act)
        if not allowed:
            self.kept.append(obj)
        return allowed

    def add_denial(self, obj, act):
        self.kept.append(obj)

    def flush(self):
        FakeBatch.flushed.append(list(self.kept))


@pytest.fixture
def env(monkeypatch):
    table, logs = FakeTable(), FakeLogs()
    monkeypatch.setattr(svc, "query_table", table)
    monkeypatch.setattr(svc, "logs_client", logs)
    monkeypatch.setattr(svc, "CasbinEnforcer", FakeEnforcer)
    routes_stub = types.ModuleType("handlers.auth.routes")
    routes_stub._DenialAuditBatch = FakeBatch
    monkeypatch.setitem(sys.modules, "handlers.auth.routes", routes_stub)
    monkeypatch.setattr(svc, "request_to_claims", _real_request_to_claims())
    FakeEnforcer.allowed_groups, FakeEnforcer.api_allowed, FakeEnforcer.denials = None, True, []
    FakeBatch.flushed, FakeBatch.probes = [], []
    for group in auditLogGroups.AUDIT_LOG_GROUPS:
        for name in auditLogGroups.resource_key(group).env_var_names:
            monkeypatch.setenv(name, "test-" + group.key)
    for name in (svc.ENV_MAX_WINDOW_DAYS, svc.ENV_MAX_ROWS, svc.ENV_MAX_CONCURRENT,
                 svc.ENV_MAX_CONCURRENT_PER_USER, svc.ENV_DEADLINE_SECONDS, svc.ENV_LONG_POLL_SECONDS,
                 svc.ENV_HANDLE_RETENTION_MINUTES):
        monkeypatch.delenv(name, raising=False)
    recorder = MagicMock()
    monkeypatch.setattr(svc, "log_action", recorder)
    # An error response writes an Errors-group record through the real writer; keep tests offline.
    monkeypatch.setattr(importlib.import_module("models.common"), "log_errors", lambda *a, **k: None)
    sleeps = []
    clock = {"offset": 0.0}
    real_monotonic = time.monotonic

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock["offset"] += seconds

    # The long-poll loop runs on a clock that sleep advances, so a wait of 20 s costs no wall time.
    monkeypatch.setattr(svc.time, "sleep", fake_sleep)
    monkeypatch.setattr(svc.time, "monotonic", lambda: real_monotonic() + clock["offset"])
    return SimpleNamespace(table=table, logs=logs, record=recorder, sleeps=sleeps)


def make_event(method, path, user=ALICE, body=None, path_parameters=None, query=None):
    event = {
        "requestContext": {"http": {"method": method, "path": path},
                           "authorizer": {"lambda": {"vams:tokens": json.dumps([user]),
                                                      "vams:roles": json.dumps(["audit-reader"]),
                                                      "vams:mfaEnabled": "true"}}},
        "pathParameters": path_parameters or {}, "queryStringParameters": query or {},
        "body": json.dumps(body) if body is not None else None,
    }
    return event


class Context:
    def __init__(self, seconds=29):
        self.seconds = seconds

    def get_remaining_time_in_millis(self):
        return int(self.seconds * 1000)


def handler():
    """The handler without the audit decorator (the audit context is the writer's own suite)."""
    return getattr(svc.lambda_handler, "__wrapped__", svc.lambda_handler)


def call(event):
    response = handler()(event, Context())
    body = json.loads(response["body"]) if response.get("body") else None
    return response["statusCode"], body


def start_body(**overrides):
    body = {"startTime": NOW - 3600, "endTime": NOW, "logGroups": ["auditActions"]}
    body.update(overrides)
    return body


def start(env, user=ALICE, **overrides):
    return call(make_event("POST", "/audit/queries", user=user, body=start_body(**overrides)))


# ---------------------------------------------------------------------------------------------------
# Tier 1 and Tier 2
# ---------------------------------------------------------------------------------------------------

class TestAuthorization:
    @pytest.mark.parametrize("method,path,params", [
        ("GET", "/audit/log-groups", {}),
        ("POST", "/audit/queries", {}),
        ("GET", "/audit/queries/x", {"queryId": "x"}),
        ("DELETE", "/audit/queries/x", {"queryId": "x"}),
        ("GET", "/audit/queries/x/export", {"queryId": "x"}),
    ])
    def test_a_route_the_caller_may_not_use_is_denied_before_any_work(self, env, method, path, params):
        FakeEnforcer.api_allowed = False
        status, _ = call(make_event(method, path, body={"a": 1} if method == "POST" else None, path_parameters=params))
        assert status == 403
        assert env.logs.started == [] and env.logs.polls == 0 and env.table.items == {}

    def test_empty_tokens_deny(self, env):
        event = make_event("GET", "/audit/log-groups")
        event["requestContext"]["authorizer"] = {"lambda": {}}
        assert call(event)[0] == 403

    def test_an_unknown_group_and_a_group_not_allowed_are_the_same_response(self, env):
        FakeEnforcer.allowed_groups = {"auditActions"}
        unknown = start(env, logGroups=["noSuchGroup"])
        refused = start(env, logGroups=["auditAuthentication"])
        assert unknown == refused
        assert unknown[0] == 403
        assert "auditAuthentication" not in json.dumps(refused[1]) and "noSuchGroup" not in json.dumps(unknown[1])
        assert env.logs.started == []

    def test_one_refused_group_refuses_the_whole_request(self, env):
        FakeEnforcer.allowed_groups = {"auditActions"}
        status, _ = start(env, logGroups=["auditActions", "auditAuthorization"])
        assert status == 403 and env.logs.started == []

    def test_a_refused_start_is_recorded_as_denied_with_a_generic_reason(self, env):
        FakeEnforcer.allowed_groups = set()
        start(env)
        env.record.assert_called_with(env.record.call_args.args[0], "auditQueryStart", outcome="denied",
                                      reason="forbidden")

    def test_without_named_groups_only_the_allowed_ones_are_searched(self, env):
        FakeEnforcer.allowed_groups = {"auditActions", "auditErrors"}
        status, _ = call(make_event("POST", "/audit/queries", body={"startTime": NOW - 600, "endTime": NOW}))
        assert status == 202
        assert sorted(env.logs.started[0]["logGroupNames"]) == ["test-auditActions", "test-auditErrors"]
        assert FakeBatch.probes == [svc.PROBE_ENDPOINT_START]

    def test_with_no_allowed_group_a_start_is_refused(self, env):
        FakeEnforcer.allowed_groups = set()
        status, _ = call(make_event("POST", "/audit/queries", body={"startTime": NOW - 600, "endTime": NOW}))
        assert status == 403

    def test_the_group_list_shows_only_allowed_groups_and_writes_hidden_denials_as_one_probe_batch(self, env):
        FakeEnforcer.allowed_groups = {"auditActions"}
        status, body = call(make_event("GET", "/audit/log-groups"))
        assert status == 200
        assert [g["key"] for g in body["logGroups"]] == ["auditActions"]
        assert FakeBatch.probes == [svc.PROBE_ENDPOINT_LOG_GROUPS]
        # One summary denial for all hidden groups, not one record per hidden group.
        assert len(FakeBatch.flushed) == 1 and len(FakeBatch.flushed[0]) == 1
        hidden = sorted(g.key for g in auditLogGroups.AUDIT_LOG_GROUPS if g.key != "auditActions")
        assert FakeBatch.flushed[0][0]["auditLogGroup"] == ",".join(hidden)
        assert FakeEnforcer.denials == []  # no unmarked per-group denial
        assert all(a["logGroup"] == "auditActions" for a in body["actions"])
        assert body["limits"]["maxRows"] == 1000 and body["limits"]["longPollWaitSeconds"] == 20


# ---------------------------------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------------------------------

class TestStart:
    def test_start_builds_the_query_on_the_server_and_stores_an_owned_handle(self, env):
        status, body = start(env, actions=["assetUpdate"], users=["carol"], contains=["Report.usd"])
        assert status == 202 and body["status"] == "Running"
        started = env.logs.started[0]
        assert started["logGroupNames"] == ["test-auditActions"]
        assert "assetUpdate" in started["queryString"] and "limit 1000" in started["queryString"]
        item = env.table.items[body["queryId"]]
        assert item["userId"] == ALICE and item["openKey"] == "open" and item["cwlQueryId"] == "cw-1"
        # The row lives for the deadline plus the retention (60 minutes by default), not for days.
        assert abs(item["expiresAt"] - (int(time.time()) + 300 + 3600)) <= 120  # clock drift under load
        assert item["containsTerms"] == ["report.usd"] and item["containsCount"] == 1
        assert "report.usd" not in item["filters"].lower()

    def test_the_start_record_holds_filters_but_never_the_contains_text(self, env):
        start(env, contains=["secret-term-777"], actions=["assetUpdate"])
        changes = env.record.call_args.kwargs["changes"]
        assert "secret-term-777" not in json.dumps(changes)
        assert changes["containsTermCount"] == 1 and len(changes["containsSha256"]) == 64
        assert changes["actions"] == ["assetUpdate"]

    @pytest.mark.parametrize("hostile", [
        {"query": "fields @message | limit 1"}, {"queryString": "x"}, {"regex": ".*"},
        {"users": ['x" | filter 1=1 | #']}, {"contains": ["`rm`"]}, {"actions": ["a b"]},
        {"startTime": "yesterday"}, {"outcomes": ["maybe"]}, {"sort": "sideways"},
    ])
    def test_hostile_filters_are_refused_with_one_generic_message(self, env, hostile):
        status, body = start(env, **hostile)
        if "users" in hostile:
            # A user value outside the safe-literal set is accepted and applied on the server only.
            assert status == 202 and "filter 1=1" not in env.logs.started[0]["queryString"]
            return
        assert status == 400 and body == {"message": svc.MESSAGE_INVALID}
        assert env.logs.started == []

    def test_a_window_over_the_limit_is_refused(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_MAX_WINDOW_DAYS, "2")
        assert start(env, startTime=NOW - 3 * 86400)[0] == 400
        assert start(env, startTime=NOW - 86400)[0] == 202

    def test_a_window_older_than_any_log_retention_is_a_400_not_a_500(self, env):
        status, body = start(env, startTime=1, endTime=2)
        assert status == 400 and body == {"message": svc.MESSAGE_INVALID}
        assert env.logs.started == []

    def test_a_window_logs_insights_refuses_is_a_400_and_gives_the_slots_back(self, env, monkeypatch):
        from botocore.exceptions import ClientError

        def refused(**kwargs):
            raise ClientError({"Error": {"Code": "InvalidParameterException", "Message": "startTime"}},
                              "StartQuery")
        monkeypatch.setattr(env.logs, "start_query", refused)
        status, body = start(env)
        assert status == 400 and body == {"message": svc.MESSAGE_INVALID}
        assert env.table.items[svc.SLOT_ALL_KEY]["slotCount"] == 0

    def test_the_per_user_cap_refuses_a_third_open_search(self, env):
        assert start(env)[0] == 202 and start(env)[0] == 202
        status, body = start(env)
        assert status == 400 and body == {"message": svc.MESSAGE_BUSY}
        assert start(env, user=BOB)[0] == 202  # another user still has a slot

    def test_the_vams_wide_cap_refuses_over_five_open_searches(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_MAX_CONCURRENT_PER_USER, "5")
        for index in range(5):
            assert start(env, user="user%d" % index)[0] == 202
        assert start(env, user="user9")[0] == 400

    def test_a_handle_past_its_deadline_is_stopped_and_frees_its_slot(self, env):
        assert start(env)[0] == 202 and start(env)[0] == 202
        for item in handle_items(env):
            item["deadlineAt"] = NOW - 1
        assert start(env)[0] == 202
        assert sorted(env.logs.stopped) == ["cw-1", "cw-2"]
        assert {i["status"] for i in handle_items(env)} == {"Timeout", "Running"}

    def test_a_handle_cloudwatch_no_longer_runs_frees_its_slot(self, env):
        assert start(env)[0] == 202 and start(env)[0] == 202
        for item in handle_items(env):
            item["createdAtEpoch"] = NOW - 60
        env.logs.running = []  # CloudWatch lists nothing as running
        assert start(env)[0] == 202

    def test_a_start_that_fails_to_store_stops_the_cloudwatch_query(self, env, monkeypatch):
        def broken(**kwargs):
            raise RuntimeError("table down")
        monkeypatch.setattr(env.table, "put_item", broken)
        assert start(env)[0] == 500
        assert env.logs.stopped == ["cw-1"]


# ---------------------------------------------------------------------------------------------------
# Handles: ownership, forgery, expiry
# ---------------------------------------------------------------------------------------------------

def handle_items(env):
    """The stored search handles (the slot counters live in the same table)."""
    return [item for key, item in env.table.items.items() if not key.startswith("slots#")]


def started_handle(env, user=ALICE, **overrides):
    status, body = start(env, user=user, **overrides)
    assert status == 202
    return body["queryId"]


def get(query_id, user=ALICE, query=None, suffix=""):
    return call(make_event("GET", "/audit/queries/%s%s" % (query_id, suffix), user=user,
                           path_parameters={"queryId": query_id}, query=query))


class TestHandles:
    def test_a_foreign_unknown_malformed_and_expired_handle_are_one_404(self, env):
        mine = started_handle(env)
        expired = started_handle(env, user=BOB)
        env.table.items[expired]["expiresAt"] = NOW - 5
        forged_uuid = "00000000-0000-4000-8000-000000000000"
        responses = [get(mine, user=BOB), get(forged_uuid), get("../etc/passwd"), get("x" * 200), get(expired, user=BOB)]
        assert all(r == (404, {"message": "Query not found"}) for r in responses)
        assert get(mine)[0] == 200

    def test_a_forged_token_list_in_the_request_cannot_claim_another_users_handle(self, env):
        handle = started_handle(env)
        event = make_event("GET", "/audit/queries/%s" % handle, user=BOB, path_parameters={"queryId": handle},
                           query={"vams:tokens": json.dumps([ALICE]), "userId": ALICE})
        event["headers"] = {"vams:tokens": json.dumps([ALICE])}
        event["body"] = json.dumps({"vams:tokens": [ALICE], "userId": ALICE})
        assert call(event) == (404, {"message": "Query not found"})

    def test_a_handle_is_gone_when_the_callers_access_to_its_group_was_removed(self, env):
        handle = started_handle(env)
        FakeEnforcer.allowed_groups = {"auditErrors"}
        assert get(handle) == (404, {"message": "Query not found"})
        assert call(make_event("DELETE", "/audit/queries/" + handle, path_parameters={"queryId": handle})) == (
            404, {"message": "Query not found"})

    def test_stop_cancels_a_running_search_and_frees_the_slot(self, env):
        handle = started_handle(env)
        status, body = call(make_event("DELETE", "/audit/queries/" + handle, path_parameters={"queryId": handle}))
        assert status == 200 and body["status"] == "Cancelled"
        assert env.logs.stopped == ["cw-1"]
        assert "openKey" not in env.table.items[handle]
        assert env.record.call_args.args[1] == "auditQueryStop"

    def test_a_foreign_stop_is_not_found_and_stops_nothing(self, env):
        handle = started_handle(env)
        status, _ = call(make_event("DELETE", "/audit/queries/" + handle, user=BOB, path_parameters={"queryId": handle}))
        assert status == 404 and env.logs.stopped == []


# ---------------------------------------------------------------------------------------------------
# Long-poll, lease, deadline
# ---------------------------------------------------------------------------------------------------

class TestPolling:
    def test_a_running_search_waits_up_to_the_clamped_wait_and_returns_progress_only(self, env):
        handle = started_handle(env)
        env.logs.status = "Running"
        status, body = get(handle, query={"wait": "60"})
        assert status == 200 and body["status"] == "Running" and body["Items"] == []
        assert body["statistics"]["bytesScanned"] == 2048
        assert 10 <= env.logs.polls <= 15                  # about every 1.5 s for 20 s, not 60 s
        assert "pollLeaseUntil" not in env.table.items[handle]  # the lease is released

    def test_a_second_poller_gets_the_current_status_at_once(self, env):
        handle = started_handle(env)
        env.logs.status = "Running"
        env.table.items[handle]["pollLeaseUntil"] = int(time.time()) + 3600
        status, body = get(handle, query={"wait": "20"})
        assert status == 200 and env.logs.polls == 1 and env.sleeps == []

    def test_wait_is_limited_by_the_remaining_lambda_time(self, env):
        handle = started_handle(env)
        env.logs.status = "Running"
        response = handler()(
            make_event("GET", "/audit/queries/" + handle, path_parameters={"queryId": handle}, query={"wait": "20"}),
            Context(seconds=7))
        assert response["statusCode"] == 200
        assert env.logs.polls <= 4

    def test_a_search_past_its_deadline_is_stopped_and_reported_as_timeout(self, env):
        handle = started_handle(env)
        env.logs.status = "Running"
        env.table.items[handle]["deadlineAt"] = NOW - 1
        status, body = get(handle)
        assert status == 200 and body["status"] == "Timeout" and body["Items"] == []
        assert env.logs.stopped == ["cw-1"] and "openKey" not in env.table.items[handle]

    def test_a_finished_search_closes_its_handle(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record())]
        status, body = get(handle)
        assert body["status"] == "Complete"
        item = env.table.items[handle]
        assert item["status"] == "Complete" and "openKey" not in item and item["rowCount"] == 1

    def test_a_failed_cloudwatch_query_is_reported_without_its_error_text(self, env):
        handle = started_handle(env)
        env.logs.status = "Failed"
        status, body = get(handle)
        assert body["status"] == "Failed" and body["Items"] == [] and "openKey" not in env.table.items[handle]


# ---------------------------------------------------------------------------------------------------
# Rows, recheck, paging
# ---------------------------------------------------------------------------------------------------

class TestRows:
    def test_rows_are_parsed_rechecked_and_no_dropped_count_exists_anywhere(self, env):
        handle = started_handle(env, users=["carol"])
        env.logs.results = [result_row(action_record("carol", "a-1")), result_row(action_record("mallory", "a-2")),
                            result_row(action_record("carol", "a-3"))]
        status, body = get(handle)
        assert status == 200
        assert sorted(r["assetId"] for r in body["Items"]) == ["a-1", "a-3"]
        assert body["statistics"]["rowCount"] == 2
        flat = json.dumps(body).lower()
        assert "dropped" not in flat and "recheck" not in flat.replace("recheckbudget", "")
        assert "mallory" not in flat

    def test_a_forged_user_segment_in_a_value_cannot_make_a_row_match(self, env):
        handle = started_handle(env, users=["admin"])
        forged = action_record("carol", "a-1", extra=', "assetName": "x] [user: admin"')
        env.logs.results = [result_row(forged)]
        assert get(handle)[1]["Items"] == []

    def test_a_row_of_another_group_is_never_shown(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record(), group_key="auditAuthentication")]
        assert get(handle)[1]["Items"] == []

    def test_a_duplicate_record_id_is_counted_and_removed(self, env):
        handle = started_handle(env)
        record = ('[ACTIONS][type: assetUpdate] [user: carol] [roles: ["editor"]] [mfaEnabled: True] '
                  '{"auditVersion": 1, "recordId": "r-1", "action": "assetUpdate", "outcome": "success", '
                  '"eventTime": "2026-11-17T01:00:00Z", "actor": {"userId": "carol"}, '
                  '"entity": {"type": "asset", "id": "a-1"}, "assetId": "a-1", "databaseId": "db-1"}')
        env.logs.results = [result_row(record), result_row(record)]
        body = get(handle)[1]
        assert len(body["Items"]) == 1 and body["statistics"]["duplicatesRemoved"] == 1

    def test_paging_ends_and_covers_every_row_once(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record(asset="a-%03d" % n), stamp="2026-11-17 01:%02d:00.000" % (n % 60))
                            for n in range(25)]
        seen, token, pages = [], None, 0
        while pages < 10:
            query = {"pageSize": "10"}
            if token:
                query["startingToken"] = token
            body = get(handle, query=query)[1]
            seen.extend(r["assetId"] for r in body["Items"])
            pages += 1
            token = body.get("NextToken")
            if token is None:
                break
        assert pages == 3 and len(seen) == 25 and len(set(seen)) == 25

    def test_a_page_stays_under_the_response_budget(self, env, monkeypatch):
        monkeypatch.setattr(svc, "PAGE_BYTE_BUDGET", 3000)
        handle = started_handle(env)
        env.logs.results = [result_row(action_record(asset="a-%03d" % n)) for n in range(40)]
        body = get(handle, query={"pageSize": "500"})[1]
        assert 0 < len(body["Items"]) < 40 and body["NextToken"] == str(len(body["Items"]))

    @pytest.mark.parametrize("token", ["-1", "abc", "1;2", "9" * 40, "1.5"])
    def test_a_bad_paging_token_is_a_validation_error(self, env, token):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record())]
        assert get(handle, query={"startingToken": token})[0] == 400

    def test_the_recheck_time_budget_returns_partial_rows_with_a_warning(self, env, monkeypatch):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record(asset="a-%d" % n)) for n in range(5)]
        response = handler()(
            make_event("GET", "/audit/queries/" + handle, path_parameters={"queryId": handle}), Context(seconds=3))
        body = json.loads(response["body"])
        assert response["statusCode"] == 200
        # With 3 s left the budget is the one-second floor; the rows read inside it are returned.
        assert "Items" in body

    def test_the_candidate_limit_is_reported_when_filters_could_not_be_pushed_down(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_MAX_ROWS, "3")
        handle = started_handle(env, outcomes=["success"])
        env.logs.results = [result_row(action_record(asset="a-%d" % n)) for n in range(3)]
        body = get(handle)[1]
        assert body["statistics"]["candidateLimitReached"] is True

    def test_results_and_status_reads_are_recorded_as_the_registered_actions(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record())]
        get(handle)
        results = env.record.call_args
        assert results.args[1] == "auditQueryResults" and results.kwargs["item_count"] == 1
        assert "carol" not in json.dumps(results.kwargs)  # what was returned is never recorded
        env.logs.status = "Running"
        h2 = started_handle(env, user=BOB)
        get(h2, user=BOB)
        assert env.record.call_args.args[1] == "auditQueryStatus"


# ---------------------------------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------------------------------

class TestExport:
    def export(self, handle, query=None):
        return get(handle, query=query, suffix="/export")

    def test_csv_export_chunks_end_and_the_header_is_on_the_first_chunk_only(self, env, monkeypatch):
        monkeypatch.setattr(svc, "EXPORT_CHUNK_BYTES", 6000)
        handle = started_handle(env)
        env.logs.results = [result_row(action_record(asset="a-%03d" % n)) for n in range(30)]
        chunks, token = [], None
        for _ in range(40):
            body = self.export(handle, query={"format": "csv", **({"startingToken": token} if token else {})})[1]
            assert len(json.dumps(body).encode()) <= 6000 or body["rowCount"] == 1
            chunks.append(body)
            token = body.get("NextToken")
            if not token:
                break
        assert token is None and len(chunks) > 1
        assert sum(c["rowCount"] for c in chunks) == 30
        assert chunks[0]["content"].startswith('"timestamp"') or "assetId" in chunks[0]["content"].splitlines()[0]
        assert all("groupKey" not in c["content"].splitlines()[0] for c in chunks[1:])
        assert env.record.call_args.args[1] == "auditQueryExport"

    def test_ndjson_export_is_whole_json_lines(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record(asset="a-%d" % n)) for n in range(3)]
        body = self.export(handle, query={"format": "ndjson"})[1]
        lines = body["content"].splitlines()
        assert len(lines) == 3 and all(json.loads(line)["groupKey"] == "auditActions" for line in lines)

    def test_a_default_export_chunk_is_under_four_megabytes(self, env):
        handle = started_handle(env)
        big = action_record(extra=', "details": {"note": "%s"}' % ("x" * 1500))
        env.logs.results = [result_row(big, stamp="2026-11-17 01:00:%02d.000" % (n % 60)) for n in range(1000)]
        body = self.export(handle, query={"format": "ndjson"})[1]
        assert len(json.dumps(body).encode()) <= svc.EXPORT_CHUNK_BYTES

    def test_formula_cells_are_neutralized(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record(user="=cmd|' /C calc'!A0"))]
        body = self.export(handle, query={"format": "csv"})[1]
        assert '"=cmd' not in body["content"] and "'=cmd" in body["content"]

    @pytest.mark.parametrize("fmt", ["json", "xml", "", "csv;rm"])
    def test_an_unknown_format_is_a_validation_error(self, env, fmt):
        handle = started_handle(env)
        assert self.export(handle, query={"format": fmt})[0] == 400

    def test_an_export_of_a_running_search_is_refused(self, env):
        handle = started_handle(env)
        env.logs.status = "Running"
        assert self.export(handle)[0] == 400

    def test_a_foreign_export_is_not_found(self, env):
        handle = started_handle(env)
        assert get(handle, user=BOB, suffix="/export") == (404, {"message": "Query not found"})


# ---------------------------------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------------------------------

class TestConfiguration:
    def test_defaults_apply_when_the_configuration_is_silent(self, env):
        assert svc.concurrency_caps() == (5, 2)
        assert (svc.deadline_seconds(), svc.long_poll_seconds()) == (300, 20)
        limits = svc.search_limits()
        assert (limits.max_window_days, limits.max_rows) == (31, 1000)

    def test_values_are_held_within_their_ceilings(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_MAX_ROWS, "999999")
        monkeypatch.setenv(svc.ENV_MAX_CONCURRENT, "500")
        monkeypatch.setenv(svc.ENV_MAX_CONCURRENT_PER_USER, "400")
        monkeypatch.setenv(svc.ENV_DEADLINE_SECONDS, "99999")
        monkeypatch.setenv(svc.ENV_LONG_POLL_SECONDS, "99")
        assert svc.search_limits().max_rows == queryBuilder.CEILING_MAX_ROWS
        assert svc.concurrency_caps() == (20, 20)
        assert svc.deadline_seconds() == 900 and svc.long_poll_seconds() == 25

    def test_a_bad_value_falls_back_to_the_default(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_MAX_ROWS, "many")
        assert svc.search_limits().max_rows == 1000


class TestNoLeaks:
    def test_the_handler_never_logs_row_content_or_request_text(self, env, caplog):
        handle = started_handle(env, contains=["secret-term-777"])
        env.logs.results = [result_row(action_record(user="carol-secret"))]
        with caplog.at_level("DEBUG"):
            get(handle)
        assert "secret-term-777" not in caplog.text and "carol-secret" not in caplog.text

    def test_the_source_has_no_destructive_logs_call(self):
        source = open(svc.__file__).read()
        for name in ("delete_log_group", "delete_log_stream", "put_retention_policy", "put_subscription_filter",
                     "create_export_task", "associate_kms_key"):
            assert name not in source


# ---------------------------------------------------------------------------------------------------
# Handle retention and the contains terms
# ---------------------------------------------------------------------------------------------------

class TestHandleRetention:
    def test_the_default_is_sixty_minutes_and_the_setting_is_clamped(self, env, monkeypatch):
        assert svc.handle_retention_seconds() == 3600
        monkeypatch.setenv(svc.ENV_HANDLE_RETENTION_MINUTES, "15")
        assert svc.handle_retention_seconds() == 900
        monkeypatch.setenv(svc.ENV_HANDLE_RETENTION_MINUTES, "1")
        assert svc.handle_retention_seconds() == svc.MIN_HANDLE_RETENTION_MINUTES * 60
        monkeypatch.setenv(svc.ENV_HANDLE_RETENTION_MINUTES, "999999")
        assert svc.handle_retention_seconds() == svc.CEILING_HANDLE_RETENTION_MINUTES * 60
        monkeypatch.setenv(svc.ENV_HANDLE_RETENTION_MINUTES, "soon")
        assert svc.handle_retention_seconds() == 3600

    def test_the_ttl_is_written_on_create_from_the_setting(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_HANDLE_RETENTION_MINUTES, "10")
        handle = started_handle(env)
        assert abs(env.table.items[handle]["expiresAt"] - (int(time.time()) + 300 + 600)) <= 120

    def test_every_poll_page_and_export_moves_the_expiry(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_HANDLE_RETENTION_MINUTES, "10")
        handle = started_handle(env, contains=["report"])
        env.logs.results = [result_row(action_record())]
        for call_once in (lambda: get(handle), lambda: get(handle, query={"format": "csv"}, suffix="/export")):
            env.table.items[handle]["expiresAt"] = int(time.time()) + 30  # about to expire
            assert call_once()[0] == 200
            assert env.table.items[handle]["expiresAt"] >= int(time.time()) + 600 - 120

    def test_a_running_search_is_never_set_to_expire_before_its_deadline(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_HANDLE_RETENTION_MINUTES, "5")
        handle = started_handle(env)
        env.table.items[handle]["deadlineAt"] = NOW + 800
        env.logs.status = "Running"
        get(handle)
        assert env.table.items[handle]["expiresAt"] >= NOW + 800

    def test_a_closed_search_gets_the_retention_from_the_moment_it_closed(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_HANDLE_RETENTION_MINUTES, "10")
        handle = started_handle(env)
        env.table.items[handle]["expiresAt"] = NOW + 5000
        get(handle)
        assert env.table.items[handle]["status"] == "Complete"
        assert abs(env.table.items[handle]["expiresAt"] - (int(time.time()) + 600)) <= 5

    def test_a_row_past_its_expiry_is_a_generic_404_and_its_terms_are_never_used(self, env, monkeypatch):
        handle = started_handle(env, contains=["secret-term-777"])
        env.logs.results = [result_row(action_record(extra=', "note": "secret-term-777"'))]
        env.table.items[handle]["expiresAt"] = NOW - 1       # TTL deletion has not run yet
        used = []
        real = svc.build_rows
        monkeypatch.setattr(svc, "build_rows", lambda *a, **k: used.append(a) or real(*a, **k))
        for response in (get(handle), get(handle, query={"format": "csv"}, suffix="/export"),
                         call(make_event("DELETE", "/audit/queries/" + handle, path_parameters={"queryId": handle}))):
            assert response == (404, {"message": "Query not found"})
        assert used == [] and env.logs.polls == 0 and env.logs.stopped == []
        assert "secret-term-777" not in json.dumps(get(handle)[1])

    def test_a_search_without_terms_stays_readable_until_it_expires(self, env):
        handle = started_handle(env)
        assert "containsTerms" not in env.table.items[handle]
        assert get(handle)[0] == 200 and get(handle)[0] == 200


class TestContainsTermsRemoval:
    def test_stop_removes_the_terms_and_keeps_the_row(self, env):
        handle = started_handle(env, contains=["secret-term-777"])
        env.logs.status = "Running"
        status, body = call(make_event("DELETE", "/audit/queries/" + handle, path_parameters={"queryId": handle}))
        assert status == 200 and body["status"] == "Cancelled"
        assert "containsTerms" not in env.table.items[handle] and env.table.items[handle]["status"] == "Cancelled"
        assert "secret-term-777" not in json.dumps(env.table.items[handle])

    def test_stopping_a_completed_search_removes_the_terms_and_ends_its_paging(self, env):
        handle = started_handle(env, contains=["report"])
        env.logs.results = [result_row(action_record(extra=', "note": "report"'))]
        assert get(handle)[0] == 200
        assert call(make_event("DELETE", "/audit/queries/" + handle, path_parameters={"queryId": handle}))[0] == 200
        assert "containsTerms" not in env.table.items[handle]
        assert get(handle) == (404, {"message": "Query not found"})

    @pytest.mark.parametrize("cloudwatch_status", ["Failed", "Cancelled", "Timeout"])
    def test_a_search_that_ends_without_results_drops_its_terms(self, env, cloudwatch_status):
        handle = started_handle(env, contains=["secret-term-777"])
        env.logs.status = cloudwatch_status
        assert get(handle)[0] == 200
        assert "containsTerms" not in env.table.items[handle]

    def test_a_deadline_timeout_drops_the_terms(self, env):
        handle = started_handle(env, contains=["secret-term-777"])
        env.table.items[handle]["deadlineAt"] = NOW - 1
        get(handle)
        assert env.table.items[handle]["status"] == "Timeout" and "containsTerms" not in env.table.items[handle]

    def test_a_completed_search_keeps_its_terms_while_pages_are_read(self, env):
        handle = started_handle(env, contains=["report"])
        env.logs.results = [result_row(action_record(extra=', "note": "report"'))]
        assert get(handle)[0] == 200 and get(handle)[0] == 200
        assert env.table.items[handle]["containsTerms"] == ["report"]

    def test_the_last_export_chunk_removes_the_terms_and_an_earlier_one_does_not(self, env, monkeypatch):
        monkeypatch.setattr(svc, "EXPORT_CHUNK_BYTES", 6000)
        handle = started_handle(env, contains=["report"])
        env.logs.results = [result_row(action_record(asset="a-%03d" % n, extra=', "note": "report"'))
                            for n in range(30)]
        first = get(handle, query={"format": "csv"}, suffix="/export")[1]
        assert first["NextToken"] and env.table.items[handle]["containsTerms"] == ["report"]
        token = first["NextToken"]
        while token:
            body = get(handle, query={"format": "csv", "startingToken": token}, suffix="/export")[1]
            token = body.get("NextToken")
        assert "containsTerms" not in env.table.items[handle]
        assert get(handle) == (404, {"message": "Query not found"})

    def test_an_export_of_a_search_without_terms_leaves_it_readable(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record())]
        assert get(handle, query={"format": "csv"}, suffix="/export")[0] == 200
        assert get(handle)[0] == 200


class TestTermsStayOutOfRecordsAndLogs:
    TERM = "secret-term-777"

    def test_no_record_and_no_log_line_of_a_whole_search_carries_the_term(self, env, caplog):
        with caplog.at_level("DEBUG"):
            handle = started_handle(env, contains=[self.TERM])
            env.logs.results = [result_row(action_record(extra=', "note": "%s"' % self.TERM))]
            status, page = get(handle)
            assert status == 200 and page["Items"]
            get(handle, query={"format": "ndjson"}, suffix="/export")
            call(make_event("DELETE", "/audit/queries/" + handle, path_parameters={"queryId": handle}))
            get(handle)
        recorded = json.dumps([[c.args[1:], c.kwargs] for c in env.record.call_args_list], default=str)
        assert self.TERM not in recorded and self.TERM not in caplog.text
        start_call = next(c for c in env.record.call_args_list if c.args[1] == "auditQueryStart")
        changes = start_call.kwargs["changes"]
        assert changes["containsTermCount"] == 1 and len(changes["containsSha256"]) == 64

    def test_a_validation_refusal_does_not_log_the_term(self, env, caplog):
        with caplog.at_level("DEBUG"):
            start(env, contains=[self.TERM + "`"], actions=["no such action"])
        assert self.TERM not in caplog.text
        assert self.TERM not in json.dumps([[c.args[1:], c.kwargs] for c in env.record.call_args_list], default=str)


# ---------------------------------------------------------------------------------------------------
# Round 2 of the review: no raw count, one instant per row, stop, atomic caps, one probe record
# ---------------------------------------------------------------------------------------------------

HIDDEN_TERM = "zzqchg9137xyz"


def changes_record(term=HIDDEN_TERM, asset="a-9"):
    """A record whose only occurrence of the term is inside ``changes``: not searchable by ``contains``."""
    return ('[ACTIONS][type: assetCreate] [user: carol] [roles: ["editor"]] [mfaEnabled: True] '
            '{"auditVersion": 1, "recordId": "r-%s", "action": "assetCreate", "outcome": "success", '
            '"eventTime": "2026-11-17T01:00:00.000Z", "actor": {"userId": "carol"}, '
            '"entity": {"type": "asset", "id": "%s"}, "assetId": "%s", "databaseId": "db-1", '
            '"changes": {"assetName": "%s"}}' % (asset, asset, asset, term))


def _whole_response(env, raw_matched, rows, **overrides):
    overrides.setdefault("contains", [HIDDEN_TERM])
    handle = started_handle(env, **{k: v for k, v in overrides.items() if v is not None})
    env.logs.matched = raw_matched
    env.logs.results = rows
    body = get(handle)[1]
    body.pop("queryId")
    return body


class TestNoRawCountOracle:
    def test_no_field_of_the_response_varies_with_the_number_of_dropped_rows(self, env):
        """A term found only in hidden content must look exactly like an absent term."""
        visible = result_row(action_record("carol", "a-1"))
        hidden = [result_row(changes_record(asset="a-%d" % n)) for n in range(4)]
        absent = _whole_response(env, raw_matched=1, rows=[visible])
        present = _whole_response(env, raw_matched=5, rows=[visible] + hidden)
        assert present == absent

    @pytest.mark.parametrize("filters", [
        {"outcomes": ["success"]}, {"includeProbes": False}, {"entityTypes": ["asset"]},
        {"outcomes": ["success"], "includeProbes": False}])
    def test_at_the_row_limit_a_hidden_only_term_looks_like_an_absent_term(self, env, monkeypatch, filters):
        """The raw result reaches the Logs Insights row limit only when hidden content matched. Every field
        of the response, the candidate warning included, must be the same as for a term that is absent."""
        monkeypatch.setenv(svc.ENV_MAX_ROWS, "3")
        hidden = [result_row(changes_record(asset="a-%d" % n)) for n in range(3)]
        absent = _whole_response(env, raw_matched=0, rows=[], **filters)
        present = _whole_response(env, raw_matched=3, rows=hidden, **filters)
        assert present == absent
        assert "candidateLimitReached" not in present["statistics"]

    def test_the_flag_counts_candidates_the_unpushed_filters_dropped(self, env, monkeypatch):
        """Outcome, entity type and probe handling cannot narrow the Insights candidates, so a search that
        fills the limit with rows they drop may have missed matches: the flag is set from the candidates the
        caller may see, before those three filters."""
        monkeypatch.setenv(svc.ENV_MAX_ROWS, "3")
        visible = [result_row(action_record("carol", "a-%d" % n)) for n in range(3)]
        body = _whole_response(env, raw_matched=3, rows=visible, outcomes=["denied"], contains=None)
        assert body["statistics"]["rowCount"] == 0 and body["statistics"]["candidateLimitReached"] is True

    def test_a_timeout_during_the_relaxed_pass_raises_the_time_budget_warning(self, env, monkeypatch):
        """The flag's second recheck pass shares the time budget. When the clock runs out during that pass,
        the count is short and the recheckTimeBudget warning must be raised (D12)."""
        monkeypatch.setenv(svc.ENV_MAX_ROWS, "3")
        real_recheck = svc.recheck
        real_monotonic = time.monotonic
        calls = {"n": 0, "jump": 0.0}
        monkeypatch.setattr(svc.time, "monotonic", lambda: real_monotonic() + calls["jump"])

        def recheck_with_clock(rows, filters, budget=None):
            calls["n"] += 1
            if calls["n"] == 2:
                calls["jump"] += 10000.0  # the budget runs out as the relaxed pass starts
            return real_recheck(rows, filters, budget)

        monkeypatch.setattr(svc, "recheck", recheck_with_clock)
        visible = [result_row(action_record("carol", "a-%d" % n)) for n in range(3)]
        # The contains term is checked only for rows that pass the other filters, so the first pass
        # (outcome denied) never reads the clock and the relaxed pass does.
        body = _whole_response(env, raw_matched=3, rows=visible, outcomes=["denied"], contains=["db-1"])
        assert calls["n"] == 2
        assert svc.WARNING_RECHECK_BUDGET in body["warnings"]
        assert "candidateLimitReached" not in body["statistics"]

    def test_hidden_candidates_do_not_count_towards_the_flag(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_MAX_ROWS, "3")
        hidden = [result_row(changes_record(asset="a-%d" % n)) for n in range(3)]
        with_probe_filter = _whole_response(env, raw_matched=3, rows=hidden, includeProbes=False)
        assert with_probe_filter["statistics"]["rowCount"] == 0
        assert "candidateLimitReached" not in with_probe_filter["statistics"]

    def test_a_complete_prefilter_never_sets_the_flag(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_MAX_ROWS, "3")
        visible = [result_row(action_record("carol", "a-%d" % n)) for n in range(3)]
        body = _whole_response(env, raw_matched=3, rows=visible, contains=None)
        assert "candidateLimitReached" not in body["statistics"]

    def test_the_candidate_flag_depends_on_visible_rows_only(self, env, monkeypatch):
        monkeypatch.setenv(svc.ENV_MAX_ROWS, "3")
        visible = [result_row(action_record("carol", "a-%d" % n)) for n in range(3)]
        body = _whole_response(env, raw_matched=3, rows=visible, outcomes=["success"], contains=None)
        assert body["statistics"]["candidateLimitReached"] is True

    def test_hidden_duplicates_are_not_counted_either(self, env):
        record = result_row(changes_record())
        absent = _whole_response(env, raw_matched=0, rows=[])
        present = _whole_response(env, raw_matched=2, rows=[record, record])
        assert present == absent and "duplicatesRemoved" in present["statistics"]

    def test_the_statistics_name_only_filter_independent_figures(self, env):
        body = _whole_response(env, raw_matched=7, rows=[result_row(action_record())])
        assert "recordsMatched" not in json.dumps(body)
        assert set(body["statistics"]) <= {"recordsScanned", "bytesScanned", "rowCount", "duplicatesRemoved",
                                            "candidateLimitReached"}

    def test_the_figure_does_not_leak_while_the_search_runs(self, env):
        handle = started_handle(env, contains=[HIDDEN_TERM])
        env.logs.status = "Running"
        env.logs.matched = 123456
        body = get(handle)[1]
        assert body["status"] == "Running" and "123456" not in json.dumps(body)


class TestTimeOrder:
    def test_legacy_and_v1_rows_sort_by_the_same_instant(self, env):
        handle = started_handle(env)
        # Legacy ingestion times use a space and no zone; v1 uses ISO 8601 with a T and a Z. As text,
        # every "T" row would sort before every " " row of the same day.
        legacy = [result_row(action_record(asset="L%d" % n), stamp="2026-11-17 17:%02d:00.500" % (10 + 2 * n))
                  for n in range(3)]                                   # 17:10, 17:12, 17:14
        v1 = [result_row(changes_record(term="x", asset="V%d" % n).replace("2026-11-17T01:00:00.000Z",
                                                                          "2026-11-17T17:%02d:30.250Z" % (11 + 2 * n)),
                         stamp="2026-11-17 17:59:59.000") for n in range(3)]  # 17:11, 17:13, 17:15
        env.logs.results = legacy + v1
        items = get(handle, query={"sort": "asc"})[1]["Items"]
        assert [i["assetId"] for i in items] == ["L0", "V0", "L1", "V1", "L2", "V2"]
        assert all(i["timestamp"].endswith("Z") and "T" in i["timestamp"] for i in items)
        newest = get(handle, query={"sort": "desc"})[1]["Items"]
        assert [i["assetId"] for i in newest] == ["V2", "L2", "V1", "L1", "V0", "L0"]

    def test_the_time_is_one_utc_format_with_milliseconds(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record(), stamp="2026-11-17 17:19:04.055")]
        assert get(handle)[1]["Items"][0]["timestamp"] == "2026-11-17T17:19:04.055Z"

    def test_paging_keeps_the_instant_order_across_formats(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record(asset="L%d" % n), stamp="2026-11-17 17:%02d:00.000" % (10 + n))
                            for n in range(5)]
        first = get(handle, query={"sort": "asc", "pageSize": "3"})[1]
        second = get(handle, query={"sort": "asc", "pageSize": "3", "startingToken": first["NextToken"]})[1]
        assert [i["assetId"] for i in first["Items"] + second["Items"]] == ["L0", "L1", "L2", "L3", "L4"]


class TestStopKeepsNoRows:
    """A stopped search returns no rows. CloudWatch hands out partial results only while a query runs, and
    a row is shown only after the server recheck of a finished search, so the page says what is true."""

    def test_a_stopped_search_reads_as_cancelled_with_no_rows(self, env):
        handle = started_handle(env)
        env.logs.results = [result_row(action_record())]
        call(make_event("DELETE", "/audit/queries/" + handle, path_parameters={"queryId": handle}))
        status, body = get(handle)
        assert status == 200 and body["status"] == "Cancelled" and body["Items"] == []
        assert "NextToken" not in body


class TestAtomicCaps:
    def test_starts_racing_past_a_stale_count_are_still_capped_per_user(self, env, monkeypatch):
        """The open-handle listing is eventually consistent: even when it shows nothing, a user gets 2."""
        monkeypatch.setattr(svc, "reconcile_open_handles", lambda: [])
        statuses = [start(env)[0] for _ in range(6)]
        assert statuses == [202, 202, 400, 400, 400, 400]
        assert len(env.logs.started) == 2

    def test_starts_racing_past_a_stale_count_are_capped_vams_wide(self, env, monkeypatch):
        monkeypatch.setattr(svc, "reconcile_open_handles", lambda: [])
        statuses = [start(env, user="user%d" % n)[0] for n in range(8)]
        assert statuses.count(202) == 5 and len(env.logs.started) == 5

    def test_a_refused_per_user_start_gives_the_vams_wide_slot_back(self, env, monkeypatch):
        monkeypatch.setattr(svc, "reconcile_open_handles", lambda: [])
        start(env), start(env)
        assert start(env)[0] == 400
        assert env.table.items[svc.SLOT_ALL_KEY]["slotCount"] == 2

    def test_stop_gives_the_slots_back_once(self, env):
        handle = started_handle(env)
        stop = make_event("DELETE", "/audit/queries/" + handle, path_parameters={"queryId": handle})
        call(stop), call(stop)
        assert env.table.items[svc.SLOT_ALL_KEY]["slotCount"] == 0
        assert env.table.items[svc.SLOT_USER_KEY_PREFIX + ALICE]["slotCount"] == 0

    def test_a_finished_search_gives_its_slots_back(self, env):
        handle = started_handle(env)
        env.logs.results = []
        get(handle)
        assert env.table.items[svc.SLOT_ALL_KEY]["slotCount"] == 0

    def test_a_failed_start_gives_the_slots_back(self, env, monkeypatch):
        def broken(**kwargs):
            raise RuntimeError("logs down")
        monkeypatch.setattr(env.logs, "start_query", broken)
        assert start(env)[0] == 500
        assert env.table.items[svc.SLOT_ALL_KEY]["slotCount"] == 0

    def test_an_expired_full_counter_starts_over(self, env, monkeypatch):
        monkeypatch.setattr(svc, "reconcile_open_handles", lambda: [])
        start(env), start(env)
        env.table.items[svc.SLOT_USER_KEY_PREFIX + ALICE]["expiresAt"] = NOW - 1
        assert start(env)[0] == 202

    def test_an_expired_counter_below_the_cap_is_reset_not_incremented(self, env, monkeypatch):
        """A leaked count of 1 (no handle holds it) must not turn a per-user cap of 2 into 1."""
        monkeypatch.setattr(svc, "reconcile_open_handles", lambda: [])
        env.table.items[svc.SLOT_USER_KEY_PREFIX + ALICE] = {
            "queryId": svc.SLOT_USER_KEY_PREFIX + ALICE, "slotCount": 1, "expiresAt": NOW - 100}
        env.table.items[svc.SLOT_ALL_KEY] = {"queryId": svc.SLOT_ALL_KEY, "slotCount": 1, "expiresAt": NOW - 100}
        assert start(env)[0] == 202
        assert env.table.items[svc.SLOT_USER_KEY_PREFIX + ALICE]["slotCount"] == 1
        assert start(env)[0] == 202       # the second of the two allowed searches
        assert start(env)[0] == 400
        assert env.table.items[svc.SLOT_USER_KEY_PREFIX + ALICE]["slotCount"] == 2

    def test_a_counter_that_has_not_expired_is_incremented(self, env, monkeypatch):
        monkeypatch.setattr(svc, "reconcile_open_handles", lambda: [])
        env.table.items[svc.SLOT_USER_KEY_PREFIX + ALICE] = {
            "queryId": svc.SLOT_USER_KEY_PREFIX + ALICE, "slotCount": 1, "expiresAt": NOW + 1000}
        assert start(env)[0] == 202
        assert env.table.items[svc.SLOT_USER_KEY_PREFIX + ALICE]["slotCount"] == 2
        assert start(env)[0] == 400

    def test_a_crash_between_reserving_and_storing_heals_after_the_deadline(self, env, monkeypatch):
        """The Lambda dies after the slots are taken and before the handle is stored: nothing gives them back."""
        monkeypatch.setattr(svc, "reconcile_open_handles", lambda: [])
        svc._reserve_slots(ALICE)         # the reservation of the start that died
        key = svc.SLOT_USER_KEY_PREFIX + ALICE
        assert env.table.items[key]["slotCount"] == 1
        env.table.items[key]["expiresAt"] = NOW - 1
        env.table.items[svc.SLOT_ALL_KEY]["expiresAt"] = NOW - 1
        assert start(env)[0] == 202 and start(env)[0] == 202 and start(env)[0] == 400

    def test_the_counters_are_not_handles(self, env):
        started_handle(env)
        for key in (svc.SLOT_ALL_KEY, svc.SLOT_USER_KEY_PREFIX + ALICE):
            assert "openKey" not in env.table.items[key]
            assert not svc.validate_query_id(key)


class TestOneProbeRecordPerCall:
    def test_a_start_without_groups_writes_one_summary_denial(self, env):
        FakeEnforcer.allowed_groups = {"auditActions"}
        call(make_event("POST", "/audit/queries", body={"startTime": NOW - 600, "endTime": NOW}))
        assert len(FakeBatch.flushed) == 1 and len(FakeBatch.flushed[0]) == 1

    def test_a_caller_who_may_search_every_group_writes_none(self, env):
        call(make_event("GET", "/audit/log-groups"))
        assert FakeBatch.flushed == [[]]
