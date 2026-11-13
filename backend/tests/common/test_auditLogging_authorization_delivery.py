# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""When authorization records are written inside and outside the request audit context.

Inside an open context an allowed authorization record (API level from ``log_authorization_api``,
data level from ``log_authorization``) is buffered and written in the request's end-of-request
flush, together with the request's action records. A denial is written at once. Outside a context,
and for a record of another event, every record is written at once as before. The record bytes are
the same in every case: only when the record is written changes.

Every assertion is made on what the writer handed the CloudWatch Logs clients.
"""

import json
import os
import threading
import time
from types import SimpleNamespace

import pytest

for _log_type, _env in (("auditActions", "AUDIT_LOG_ACTIONS"), ("auditAuthorization", "AUDIT_LOG_AUTHORIZATION"),
                        ("auditFileUpload", "AUDIT_LOG_FILEUPLOAD"), ("auditFileDownload", "AUDIT_LOG_FILEDOWNLOAD"),
                        ("auditFileDownloadStreamed", "AUDIT_LOG_FILEDOWNLOAD_STREAMED"),
                        ("auditAuthOther", "AUDIT_LOG_AUTHOTHER"), ("auditAuthChanges", "AUDIT_LOG_AUTHCHANGES"),
                        ("auditErrors", "AUDIT_LOG_ERRORS"), ("auditAuthentication", "AUDIT_LOG_AUTHENTICATION")):
    os.environ.setdefault(_env, f"test-{_log_type}")

from backend.backend.customLogging import auditLogging  # noqa: E402
from backend.backend.customLogging.logger import mask_sensitive_data  # noqa: E402

KEYS = auditLogging.ResourceKeys


class _AlreadyExists(Exception):
    pass


class RecordingClient:
    """A logs client that records every PutLogEvents call, optionally slowly or failing."""

    def __init__(self, delay=0.0, put_error=None):
        self.delay = delay
        self.put_error = put_error
        self.calls = []
        self.lock = threading.Lock()
        self.exceptions = SimpleNamespace(ResourceAlreadyExistsException=_AlreadyExists)

    def create_log_stream(self, **kwargs):
        return {}

    def put_log_events(self, **kwargs):
        if self.delay:
            time.sleep(self.delay)
        with self.lock:
            self.calls.append(kwargs)
        if self.put_error is not None:
            raise self.put_error
        return {}

    def calls_to(self, group):
        return [call for call in self.calls if call["logGroupName"] == group]

    def messages(self, group=None):
        return [event["message"] for call in self.calls for event in call["logEvents"]
                if group is None or call["logGroupName"] == group]


def _group(key):
    name = auditLogging._audit_log_group(key)
    assert name, f"the env-var override for {key.param_key} is not in effect"
    return name


def _claims(event):
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("lambda")
    if claims is None:
        return {"tokens": [], "roles": [], "mfaEnabled": False, "authMethod": "jwt", "apiKeyId": None}
    return {"tokens": json.loads(claims.get("vams:tokens", "[]")),
            "roles": json.loads(claims.get("vams:roles", "[]")),
            "mfaEnabled": claims.get("vams:mfaEnabled") == "true",
            "authMethod": "jwt", "apiKeyId": None}


@pytest.fixture
def clients(monkeypatch):
    """The module client (immediate writes) and the flush client (the context's writes), both recording."""
    monkeypatch.setattr(auditLogging, "request_to_claims", _claims)
    monkeypatch.setattr(auditLogging, "mask_sensitive_data", mask_sensitive_data)
    monkeypatch.setattr(auditLogging, "_created_log_streams", {})
    module_client, flush_client = RecordingClient(), RecordingClient()
    monkeypatch.setattr(auditLogging, "cloudwatch_logs", module_client)
    monkeypatch.setattr(auditLogging, "audit_flush_client", flush_client)
    return SimpleNamespace(module=module_client, flush=flush_client,
                           auth=_group(KEYS.AUDIT_LOG_AUTHORIZATION), actions=_group(KEYS.AUDIT_LOG_ACTIONS))


def api_event(path="/database/db1/assets/a1", method="GET", user="alice", roles=("admin",)):
    authorizer = {"vams:tokens": json.dumps([user]), "vams:roles": json.dumps(list(roles)),
                  "vams:mfaEnabled": "true", "iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pool"}
    return {
        "headers": {"Authorization": "Bearer secret-token-value"},
        "requestContext": {"requestId": "req-1", "http": {"method": method, "path": path, "sourceIp": "198.51.100.7"},
                           "authorizer": {"lambda": authorizer}},
    }


def api_check(event):
    """The custom data CasbinEnforcer.enforceAPI passes for its route check."""
    http = event["requestContext"]["http"]
    return {"action": http["method"], "obj": {"object__type": "api", "route__path": http["path"]}}


CLAIMS = {"tokens": ["alice"], "roles": ["admin"], "mfaEnabled": True}
DATA_CHECK = {"action": "GET", "obj": {"object__type": "asset", "databaseId": "db1", "assetName": "a1"}}
LAMBDA_CONTEXT = SimpleNamespace(aws_request_id="lambda-req-1", get_remaining_time_in_millis=lambda: 30000)


def _fallbacks(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()
            if line.startswith('{"auditFallback"')]


# ---------------------------------------------------------------------------------------------------
# API-level records (log_authorization_api)
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestApiAuthorizationDelivery:
    def test_an_allowed_record_waits_for_the_flush(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, True, api_check(event))
            assert clients.flush.calls == [] and clients.module.calls == [], "nothing written during the handler"
        assert len(clients.flush.calls_to(clients.auth)) == 1
        assert clients.module.calls == [], "the bounded flush client writes inside a context"

    def test_a_request_with_only_its_authorization_record_makes_one_write(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, True, api_check(event))
        assert len(clients.flush.calls) + len(clients.module.calls) == 1

    def test_allowed_and_action_records_are_one_write_per_group(self, clients):
        event = api_event(method="PUT")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, True, api_check(event))
            auditLogging.log_authorization_api(event, True, api_check(event))
            auditLogging.log_action(event, "assetUpdate")
        assert len(clients.flush.calls_to(clients.auth)) == 1
        assert len(clients.flush.messages(clients.auth)) == 2
        assert len(clients.flush.calls_to(clients.actions)) == 1
        assert clients.module.calls == []

    def test_a_denial_is_written_at_once(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, False, api_check(event))
            assert len(clients.module.calls_to(clients.auth)) == 1, "the denial did not wait for the flush"
        assert clients.flush.calls == [], "the flush had nothing left to write"

    @pytest.mark.parametrize("decision", [1, "True", None])
    def test_a_decision_that_is_not_exactly_true_is_written_at_once(self, clients, decision):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, decision, api_check(event))
            assert len(clients.module.calls) == 1

    def test_the_record_is_byte_identical_inside_and_outside_a_context(self, clients):
        event = api_event()
        auditLogging.log_authorization_api(event, True, api_check(event))
        outside = clients.module.messages(clients.auth)
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, True, api_check(event))
        inside = clients.flush.messages(clients.auth)
        assert len(outside) == 1 and inside == outside

    def test_outside_a_context_the_record_is_written_at_once_as_before(self, clients):
        event = api_event()
        auditLogging.log_authorization_api(event, True, api_check(event))
        assert clients.flush.calls == []
        expected = (auditLogging._format_log_message(
            "[AUTHORIZATION][authorized: True]", {"user": "alice", "roles": ["admin"], "mfaEnabled": True},
            api_check(event)) + auditLogging._event_suffix(event))
        assert clients.module.messages(clients.auth) == [expected]

    def test_a_record_for_another_event_is_written_at_once(self, clients):
        event, other = api_event(), api_event(path="/database/db2")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(other, True, api_check(other))
            assert len(clients.module.calls) == 1

    def test_a_record_from_a_worker_thread_is_written_at_once(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            worker = threading.Thread(target=auditLogging.log_authorization_api,
                                      args=(event, True, api_check(event)))
            worker.start()
            worker.join()
            assert len(clients.module.calls) == 1

    def test_the_record_is_flushed_when_the_handler_raises(self, clients):
        event = api_event()
        with pytest.raises(ValueError):
            with auditLogging.audit_request(event, LAMBDA_CONTEXT):
                auditLogging.log_authorization_api(event, True, api_check(event))
                raise ValueError("handler failed")
        assert len(clients.flush.calls_to(clients.auth)) == 1

    def test_the_pre_timeout_timer_flushes_the_record(self, clients):
        context = SimpleNamespace(aws_request_id="r", get_remaining_time_in_millis=lambda: 800)
        event = api_event()
        with auditLogging.audit_request(event, context):
            auditLogging.log_authorization_api(event, True, api_check(event))
            deadline = time.monotonic() + 2
            while not clients.flush.calls and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(clients.flush.calls_to(clients.auth)) == 1, "the timer flushed the buffer"
            auditLogging.log_authorization_api(event, True, api_check(event))
            assert len(clients.flush.calls_to(clients.auth)) == 2, "after the timer a record is written at once"

    def test_a_flush_past_the_deadline_reports_the_record(self, clients, monkeypatch, capsys):
        monkeypatch.setattr(auditLogging, "AUDIT_FLUSH_DEADLINE_SECONDS", 0.05)
        monkeypatch.setattr(auditLogging, "audit_flush_client", RecordingClient(delay=0.3))
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, True, api_check(event))
        fallbacks = _fallbacks(capsys)
        assert [(line["logGroupKey"], line["reason"]) for line in fallbacks] == [
            ("auditAuthorization", "flushDeadlineExceeded")]
        assert fallbacks[0]["record"].startswith("[AUTHORIZATION][authorized: True]")

    def test_a_rejected_flush_reports_the_record(self, clients, monkeypatch, capsys):
        monkeypatch.setattr(auditLogging, "audit_flush_client", RecordingClient(put_error=RuntimeError("throttled")))
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization_api(event, True, api_check(event))
        assert [(line["logGroupKey"], line["reason"]) for line in _fallbacks(capsys)] == [
            ("auditAuthorization", "putLogEventsFailed")]


# ---------------------------------------------------------------------------------------------------
# Data-level records (log_authorization), with and without deferred_authorization_audit
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestDataAuthorizationDelivery:
    def test_an_allowed_record_waits_for_the_flush(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization(CLAIMS, True, DATA_CHECK)
            assert clients.flush.calls == [] and clients.module.calls == []
        assert len(clients.flush.messages(clients.auth)) == 1

    def test_a_denial_is_written_at_once(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization(CLAIMS, False, DATA_CHECK)
            assert len(clients.module.calls_to(clients.auth)) == 1
        assert clients.flush.calls == []

    def test_the_record_is_byte_identical_inside_and_outside_a_context(self, clients):
        auditLogging.log_authorization(CLAIMS, True, DATA_CHECK)
        outside = clients.module.messages(clients.auth)
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_authorization(CLAIMS, True, DATA_CHECK)
        assert len(outside) == 1 and clients.flush.messages(clients.auth) == outside

    def test_inside_a_deferred_block_allowed_records_join_the_flush_and_denials_the_block(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            with auditLogging.deferred_authorization_audit():
                for _ in range(3):
                    auditLogging.log_authorization(CLAIMS, False, DATA_CHECK)
                    auditLogging.log_authorization(CLAIMS, True, DATA_CHECK)
                assert clients.module.calls == [] and clients.flush.calls == []
            assert len(clients.module.calls) == 1, "the block wrote its denials in one call"
            assert len(clients.module.messages(clients.auth)) == 3
            assert clients.flush.calls == []
        assert len(clients.flush.calls) == 1 and len(clients.flush.messages(clients.auth)) == 3

    def test_outside_a_context_the_deferred_block_is_unchanged(self, clients):
        with auditLogging.deferred_authorization_audit():
            auditLogging.log_authorization(CLAIMS, False, DATA_CHECK)
            auditLogging.log_authorization(CLAIMS, True, DATA_CHECK)
            assert clients.module.calls == []
        assert len(clients.module.calls) == 1 and len(clients.module.messages(clients.auth)) == 2
        assert clients.flush.calls == []

    def test_outside_a_context_each_record_is_written_at_once(self, clients):
        auditLogging.log_authorization(CLAIMS, True, DATA_CHECK)
        auditLogging.log_authorization(CLAIMS, False, DATA_CHECK)
        assert len(clients.module.calls) == 2 and clients.flush.calls == []
