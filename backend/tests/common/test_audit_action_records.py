# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Action records (record contract version 1) and the request audit context.

``log_action`` writes one record per action: the bracketed prefix the existing records use, a JSON
payload with the contract keys, and a compact echo of the request. ``audit_request`` resolves the
actor and the request once per request, buffers the successful records and writes them together
when the request ends, each audit log group in parallel, under a deadline.

Every assertion is made on what the writer handed the CloudWatch Logs client, read back with the
documented parsing rule (the payload starts at the first ``{`` after the ``[mfaEnabled: ...]``
segment and is decoded with a raw JSON decoder).
"""

import hashlib
import importlib
import json
import os
import re
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

REGISTRY = importlib.import_module("common.auditActions")
KEYS = auditLogging.ResourceKeys

PREFIX_PATTERN = re.compile(
    r"^(?P<prefix>\[(?P<group>[A-Z-]+)\](?:\[(?:type|authorized): (?P<type>[^\]]+)\])?) "
    r"\[user: (?P<user>[^\]]*)\] \[roles: (?P<roles>\[[^\]]*\])\] \[mfaEnabled: (?P<mfa>True|False)\] ")

USER_ID_SAFE = re.compile(r"^[A-Za-z0-9_.+@-]+$")


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

    def messages(self, group=None):
        return [event["message"] for call in self.calls for event in call["logEvents"]
                if group is None or call["logGroupName"] == group]


def group_name(log_type):
    key = {
        "auditActions": KEYS.AUDIT_LOG_ACTIONS, "auditAuthorization": KEYS.AUDIT_LOG_AUTHORIZATION,
        "auditFileUpload": KEYS.AUDIT_LOG_FILEUPLOAD, "auditFileDownload": KEYS.AUDIT_LOG_FILEDOWNLOAD,
        "auditFileDownloadStreamed": KEYS.AUDIT_LOG_FILEDOWNLOAD_STREAMED,
        "auditAuthOther": KEYS.AUDIT_LOG_AUTHOTHER, "auditAuthChanges": KEYS.AUDIT_LOG_AUTHCHANGES,
        "auditErrors": KEYS.AUDIT_LOG_ERRORS,
    }[log_type]
    name = auditLogging._audit_log_group(key)
    assert name, f"the env-var override for {log_type} is not in effect"
    return name


def parse(message):
    """Split a record into (prefix match, payload, echo text) by the documented parsing rule."""
    match = PREFIX_PATTERN.match(message)
    assert match, f"record prefix does not match the documented pattern: {message[:200]}"
    start = message.index("{", match.end())
    assert start == match.end(), "the payload must start right after the [mfaEnabled: ...] segment"
    payload, end = json.JSONDecoder().raw_decode(message, start)
    return match, payload, message[end:]


def _walk(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)
        yield value
    else:
        yield value


class _Claims:
    """A stand-in request_to_claims that counts its calls."""

    def __init__(self):
        self.calls = 0

    def __call__(self, event):
        self.calls += 1
        if "lambdaCrossCall" in event:
            return {"tokens": [event["lambdaCrossCall"].get("userName", "SYSTEM_USER")], "roles": [],
                    "mfaEnabled": True, "authMethod": "jwt", "apiKeyId": None}
        claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("lambda")
        if claims is None:
            return {"tokens": [], "roles": [], "mfaEnabled": False, "authMethod": "jwt", "apiKeyId": None}
        return {"tokens": json.loads(claims.get("vams:tokens", "[]")),
                "roles": json.loads(claims.get("vams:roles", "[]")),
                "mfaEnabled": claims.get("vams:mfaEnabled") == "true",
                "authMethod": "apiKey" if claims.get("vams:authMethod") == "apiKey" else "jwt",
                "apiKeyId": claims.get("vams:apiKeyId") or None}


@pytest.fixture
def claims(monkeypatch):
    stub = _Claims()
    monkeypatch.setattr(auditLogging, "request_to_claims", stub)
    monkeypatch.setattr(auditLogging, "mask_sensitive_data", mask_sensitive_data)
    monkeypatch.setattr(auditLogging, "_created_log_streams", {})
    return stub


@pytest.fixture
def clients(monkeypatch, claims):
    """The module client (outside a context) and the flush client (inside one), both recording."""
    module_client, flush_client = RecordingClient(), RecordingClient()
    monkeypatch.setattr(auditLogging, "cloudwatch_logs", module_client)
    monkeypatch.setattr(auditLogging, "audit_flush_client", flush_client)
    return SimpleNamespace(module=module_client, flush=flush_client)


def api_event(path="/database/db1/assets/a1", method="PUT", user="alice", roles=("admin",),
              issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_pool", api_key_id=None,
              body=None):
    authorizer = {"vams:tokens": json.dumps([user]), "vams:roles": json.dumps(list(roles)),
                  "vams:mfaEnabled": "true"}
    if issuer:
        authorizer["iss"] = issuer
    if api_key_id:
        authorizer["vams:authMethod"] = "apiKey"
        authorizer["vams:apiKeyId"] = api_key_id
    return {
        "headers": {"Authorization": "Bearer secret-token-value", "x-custom": "header-marker"},
        "body": json.dumps(body or {"assetName": "body-marker"}),
        "queryStringParameters": {"q": "query-marker"},
        "requestContext": {"requestId": "req-1", "http": {"method": method, "path": path, "sourceIp": "198.51.100.7"},
                           "authorizer": {"lambda": authorizer}},
    }


LAMBDA_CONTEXT = SimpleNamespace(aws_request_id="lambda-req-1", get_remaining_time_in_millis=lambda: 30000)


# ---------------------------------------------------------------------------------------------------
# T1: the record contract
# ---------------------------------------------------------------------------------------------------

def _sample_kwargs(audit_spec):
    return {"database_id": "db1", "asset_id": "a1", "file_path": "dir/model.glb", "item_count": 3}


@pytest.mark.unit
class TestRecordContract:
    def test_every_registered_action_writes_a_contract_record(self, clients):
        # Every action in the registry, written through log_action outside a context.
        for name, audit_spec in REGISTRY.AUDIT_ACTIONS.items():
            clients.module.calls.clear()
            auditLogging.log_action(api_event(), name, **_sample_kwargs(audit_spec))
            messages = clients.module.messages(group_name(audit_spec.group))
            assert len(messages) == 1, f"{name}: expected one record in {audit_spec.group}"
            match, payload, echo = parse(messages[0])
            for key in ("auditVersion", "recordId", "action", "outcome", "eventTime", "actor", "request", "entity"):
                assert key in payload, f"{name}: missing {key}"
            assert payload["auditVersion"] == 1
            assert payload["action"] == name
            assert payload["outcome"] == "success"
            assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$", payload["eventTime"])
            assert payload["actor"]["userId"] == "alice"
            assert payload["request"]["requestId"] == "req-1"
            assert payload["request"]["route"] == "/database/{databaseId}/assets/{assetId}"
            assert payload["entity"]["type"] == audit_spec.entity_type
            assert not any(value is None for value in _walk(payload)), f"{name}: null in payload"
            assert len(json.dumps(payload)) <= auditLogging.AUDIT_RECORD_MAX_BYTES
            if audit_spec.group in ("auditFileUpload", "auditFileDownload", "auditFileDownloadStreamed"):
                assert match.group("type") is None, f"{name}: a file group record carries no type segment"
            else:
                assert match.group("type") == (audit_spec.legacy_type or name)

    def test_the_route_template_is_recorded_never_the_concrete_path(self, clients):
        auditLogging.log_action(api_event(path="/database/db-secret-name/assets/a1"), "assetUpdate")
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["request"]["route"] == "/database/{databaseId}/assets/{assetId}"
        assert "db-secret-name" not in json.dumps(payload["request"])

    def test_an_unmatched_path_is_recorded_as_unmatched(self, clients):
        auditLogging.log_action(api_event(path="/no/such/route"), "assetUpdate")
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["request"]["route"] == "unmatched"

    def test_golden_write_record(self, clients):
        auditLogging.log_action(api_event(), "assetUpdate", database_id="db1", asset_id="a1",
                                changes={"assetName": "New name", "isDistributable": True},
                                previous={"assetName": "Old name"})
        match, payload, echo = parse(clients.module.messages(group_name("auditActions"))[0])
        assert match.group("prefix") == "[ACTIONS][type: assetUpdate]"
        payload.pop("recordId")
        payload.pop("eventTime")
        assert payload == {
            "auditVersion": 1, "action": "assetUpdate", "outcome": "success",
            "actor": {"userId": "alice", "authMethod": "cognito"},
            "request": {"requestId": "req-1", "method": "PUT", "route": "/database/{databaseId}/assets/{assetId}",
                        "sourceIp": "198.51.100.7"},
            "entity": {"type": "asset", "id": "a1"}, "databaseId": "db1", "assetId": "a1",
            "changes": {"assetName": "New name", "isDistributable": True},
            "previous": {"assetName": "Old name"},
        }
        assert echo == (' --- [event: {"requestContext": {"requestId": "req-1", '
                        '"http": {"method": "PUT", "path": "/database/db1/assets/a1"}}}]')

    def test_golden_read_record(self, clients):
        auditLogging.log_action(api_event(path="/database/db1/assets/a1/listFiles", method="GET"), "fileList",
                                database_id="db1", asset_id="a1", item_count=25,
                                request_parameters={"maxItems": 100, "prefix": "dir/"})
        _, payload, _ = parse(clients.module.messages()[0])
        payload.pop("recordId")
        payload.pop("eventTime")
        assert payload["requestParameters"] == {"maxItems": 100, "prefix": "dir/"}
        assert payload["itemCount"] == 25
        assert payload["entity"] == {"type": "file"}
        assert "changes" not in payload

    def test_golden_denied_record_goes_to_authorization(self, clients):
        auditLogging.log_action(api_event(), "assetDelete", outcome="denied", reason="forbidden",
                                database_id="db1", asset_id="a1")
        assert clients.module.messages(group_name("auditActions")) == []
        match, payload, _ = parse(clients.module.messages(group_name("auditAuthorization"))[0])
        assert match.group("prefix") == "[AUTHORIZATION][authorized: False]"
        assert payload["outcome"] == "denied" and payload["reason"] == "forbidden"
        assert payload["action"] == "assetDelete"

    def test_reason_is_a_category_never_error_text(self, clients):
        auditLogging.log_action(api_event(), "assetUpdate", outcome="failed",
                                reason="Traceback: KeyError 'secret'")
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["reason"] == "other"

    def test_reason_is_left_out_on_success(self, clients):
        auditLogging.log_action(api_event(), "assetUpdate", reason="validation")
        _, payload, _ = parse(clients.module.messages()[0])
        assert "reason" not in payload

    def test_an_unknown_outcome_is_recorded_as_failed(self, clients):
        auditLogging.log_action(api_event(), "assetUpdate", outcome="maybe")
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["outcome"] == "failed"

    def test_sizes_and_counts(self, clients):
        auditLogging.log_action(api_event(), "fileDelete", bytes=1024, file_count=3, item_count=-1,
                                duration_ms="12")
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["bytes"] == 1024 and payload["fileCount"] == 3 and payload["durationMs"] == 12
        assert "itemCount" not in payload

    def test_extra_id_fields_and_unknown_keywords(self, clients):
        auditLogging.log_action(api_event(), "fileVersionRevert", versionId="v7", colour="blue")
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["versionId"] == "v7"
        assert payload["entity"] == {"type": "fileVersion", "id": "v7"}
        assert payload["details"] == {"colour": "blue"}

    def test_the_echo_carries_no_headers_query_or_body(self, clients):
        auditLogging.log_action(api_event(), "assetUpdate")
        message = clients.module.messages()[0]
        for marker in ("header-marker", "query-marker", "body-marker", "secret-token-value"):
            assert marker not in message

    def test_an_unregistered_action_is_still_written_to_actions(self, clients):
        auditLogging.log_action(api_event(), "controlUnregisteredAction")
        match, payload, _ = parse(clients.module.messages(group_name("auditActions"))[0])
        assert match.group("type") == "controlUnregisteredAction"

    def test_log_action_never_raises(self, clients, monkeypatch):
        monkeypatch.setattr(auditLogging, "build_audit_payload", lambda *a, **k: 1 / 0)
        auditLogging.log_action(api_event(), "assetUpdate")


# ---------------------------------------------------------------------------------------------------
# Owner rule: writes record what changed, values capped; redaction still applies
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestValueSectionsAndCaps:
    def test_changed_values_are_recorded_in_full_under_the_cap(self, clients):
        comment = "A comment with an email x@example.com and phone +15555550100"
        auditLogging.log_action(api_event(), "commentUpdate", changes={"commentBody": comment})
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["changes"]["commentBody"] == comment
        assert "truncated" not in payload

    def test_sensitive_and_content_keys_are_redacted_in_every_section(self, clients):
        auditLogging.log_action(api_event(), "pipelineTemplateUpdate",
                                changes={"configBody": "PROMPT", "password": "p4ss"},
                                previous={"configBody": "OLD"},
                                request_parameters={"sessionToken": "t0k"},
                                details={"secretAccessKey": "AKIA"})
        message = clients.module.messages()[0]
        for value in ("PROMPT", "p4ss", "OLD", "t0k", "AKIA"):
            assert value not in message
        _, payload, _ = parse(message)
        assert payload["changes"]["configBody"] == "<redacted>"

    def test_a_value_over_2_kb_is_cut_to_its_hash_and_a_preview(self, clients):
        big = "x" * 5000
        auditLogging.log_action(api_event(), "metadataUpdate", changes={"description": big, "small": "ok"})
        _, payload, _ = parse(clients.module.messages()[0])
        cut = payload["changes"]["description"]
        assert cut["truncated"] is True
        assert cut["sha256"] == hashlib.sha256(big.encode("utf-8")).hexdigest()
        assert cut["bytes"] == 5000
        assert big.startswith(cut["preview"]) and 0 < len(cut["preview"]) <= 512
        assert len(json.dumps(cut)) <= auditLogging.AUDIT_VALUE_MAX_BYTES
        assert payload["changes"]["small"] == "ok"
        assert payload["truncated"] is True

    def test_a_non_ascii_value_preview_stays_under_the_cap(self, clients):
        big = "\u2603" * 3000
        auditLogging.log_action(api_event(), "metadataUpdate", changes={"description": big})
        _, payload, _ = parse(clients.module.messages()[0])
        cut = payload["changes"]["description"]
        assert len(json.dumps(cut, ensure_ascii=True)) <= auditLogging.AUDIT_VALUE_MAX_BYTES
        assert cut["sha256"] == hashlib.sha256(big.encode("utf-8")).hexdigest()

    def test_a_structured_value_is_hashed_as_canonical_json(self, clients):
        value = {"b": ["y" * 1500], "a": ["z" * 1500]}
        auditLogging.log_action(api_event(), "metadataUpdate", changes={"fields": value})
        _, payload, _ = parse(clients.module.messages()[0])
        canonical = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        assert payload["changes"]["fields"]["sha256"] == hashlib.sha256(canonical.encode()).hexdigest()

    def test_a_record_is_bounded_to_32_kb(self, clients):
        changes = {f"field{index}": "v" * 1900 for index in range(40)}
        auditLogging.log_action(api_event(), "metadataUpdate", changes=changes)
        _, payload, _ = parse(clients.module.messages()[0])
        assert len(json.dumps(payload)) <= auditLogging.AUDIT_RECORD_MAX_BYTES
        assert payload["truncated"] is True
        assert set(payload["changes"]) == set(changes), "every field is still named"
        cut = [value for value in payload["changes"].values() if isinstance(value, dict)]
        assert cut and all("sha256" in value and "preview" not in value for value in cut)

    def test_a_record_with_too_many_fields_hashes_the_whole_section(self, clients):
        changes = {f"f{index}": "v" * 100 for index in range(2000)}
        auditLogging.log_action(api_event(), "metadataUpdate", changes=changes)
        _, payload, _ = parse(clients.module.messages()[0])
        assert len(json.dumps(payload)) <= auditLogging.AUDIT_RECORD_MAX_BYTES
        assert payload["changes"]["truncated"] is True and payload["changes"]["fieldCount"] == 2000

    def test_a_long_id_is_capped(self, clients):
        auditLogging.log_action(api_event(), "fileMove", file_path="d/" * 2000)
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["filePath"]["truncated"] is True

    def test_null_values_are_left_out(self, clients):
        auditLogging.log_action(api_event(), "assetUpdate", changes={"a": None, "b": 1}, versionId=None)
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["changes"] == {"b": 1} and "versionId" not in payload


# ---------------------------------------------------------------------------------------------------
# T4: injection
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestInjection:
    @pytest.mark.parametrize("user", ["eve]", "eve[", "eve\nfake", "eve\rx", "eve\u202eadmin", 'eve"q'])
    def test_a_hostile_user_id_cannot_add_a_segment_or_a_line(self, clients, user):
        auditLogging.log_action(api_event(user=user, roles=("viewer",)), "assetUpdate")
        message = clients.module.messages()[0]
        assert "\n" not in message and "\r" not in message and "\u202e" not in message
        match, payload, _ = parse(message)
        assert match.group("roles") == '["viewer"]'
        assert len(PREFIX_PATTERN.findall(message)) == 1

    def test_a_valid_user_id_is_unchanged(self, clients):
        auditLogging.log_action(api_event(user="first.last+tag@example.com"), "assetUpdate")
        match, payload, _ = parse(clients.module.messages()[0])
        assert match.group("user") == "first.last+tag@example.com"
        assert payload["actor"]["userId"] == "first.last+tag@example.com"

    def test_a_hostile_file_path_stays_inside_the_payload(self, clients):
        hostile = 'a] [roles: ["admin"]]\n[ACTIONS][type: fake] x'
        auditLogging.log_action(api_event(), "fileMove", file_path=hostile,
                                changes={"targetName": hostile})
        message = clients.module.messages()[0]
        assert "\n" not in message
        match, payload, _ = parse(message)
        assert match.group("roles") == '["admin"]' and match.group("type") == "fileMove"
        assert payload["filePath"] == hostile and payload["changes"]["targetName"] == hostile


# ---------------------------------------------------------------------------------------------------
# Actor resolution
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestActor:
    def _actor(self, clients, event):
        clients.module.calls.clear()
        auditLogging.log_action(event, "assetUpdate")
        return parse(clients.module.messages()[0])[1]["actor"]

    def test_cognito(self, clients):
        assert self._actor(clients, api_event()) == {"userId": "alice", "authMethod": "cognito"}

    def test_external_idp(self, clients):
        actor = self._actor(clients, api_event(issuer="https://login.example.com/tenant/v2.0"))
        assert actor == {"userId": "alice", "authMethod": "externalIdp"}

    def test_no_issuer(self, clients):
        assert self._actor(clients, api_event(issuer=None))["authMethod"] == "jwt"

    def test_api_key(self, clients):
        actor = self._actor(clients, api_event(api_key_id="key-123"))
        assert actor == {"userId": "alice", "authMethod": "apiKey", "apiKeyId": "key-123"}

    def test_cross_call_for_a_user(self, clients):
        event = {"lambdaCrossCall": {"userName": "bob"},
                 "requestContext": {"http": {"method": "POST", "path": "/workflows/db1/wf1/execute"}}}
        actor = self._actor(clients, event)
        assert actor == {"userId": "bob", "authMethod": "system", "crossCall": True, "onBehalfOf": "bob"}

    def test_cross_call_as_the_system(self, clients):
        event = {"lambdaCrossCall": {"userName": "SYSTEM_USER"}}
        assert self._actor(clients, event) == {"userId": "SYSTEM_USER", "authMethod": "system", "crossCall": True}

    def test_a_non_api_event_is_the_system(self, clients):
        assert self._actor(clients, {"Records": []}) == {"userId": "SYSTEM_USER", "authMethod": "system"}

    def test_on_behalf_of_from_the_context(self, clients):
        with auditLogging.audit_request({"Records": []}, LAMBDA_CONTEXT, on_behalf_of="carol") as ctx:
            assert ctx.actor == {"userId": "SYSTEM_USER", "authMethod": "system", "onBehalfOf": "carol"}
            assert ctx.request == {"lambdaRequestId": "lambda-req-1"}


# ---------------------------------------------------------------------------------------------------
# T5: the request audit context and its delivery
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestRequestContext:
    def test_success_records_are_buffered_until_the_request_ends(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "assetUpdate")
            auditLogging.log_action(event, "metadataUpdate")
            assert clients.flush.calls == [] and clients.module.calls == []
        assert len(clients.flush.calls) == 1, "one write for the group"
        assert len(clients.flush.messages(group_name("auditActions"))) == 2
        assert clients.module.calls == [], "inside a context the bounded flush client writes"

    def test_the_route_action_is_the_default_action(self, clients):
        event = api_event(path="/database/db1", method="DELETE")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, database_id="db1")
        _, payload, _ = parse(clients.flush.messages()[0])
        assert payload["action"] == "databaseDelete" and payload["entity"] == {"type": "database", "id": "db1"}
        assert payload["request"]["lambdaRequestId"] == "lambda-req-1"

    def test_claims_are_resolved_once_per_request(self, clients, claims):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            for _ in range(5):
                auditLogging.log_action(event, "assetUpdate")
            auditLogging.log_actions(event, "workflowCreate", {"workflowId": "w1"})
        assert claims.calls == 1

    def test_outside_a_context_each_record_resolves_claims(self, clients, claims):
        event = api_event()
        auditLogging.log_action(event, "assetUpdate")
        auditLogging.log_actions(event, "workflowCreate", {"workflowId": "w1"})
        assert claims.calls == 2

    def test_denials_and_failures_are_written_at_once(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "assetDelete", outcome="denied", reason="forbidden")
            auditLogging.log_action(event, "assetUpdate", outcome="failed", reason="conflict")
            auditLogging.log_errors(event, "validation", {"message": "bad"})
            assert len(clients.flush.messages(group_name("auditAuthorization"))) == 1
            assert len(clients.flush.messages(group_name("auditActions"))) == 1
            assert len(clients.flush.messages(group_name("auditErrors"))) == 1

    def test_an_error_record_names_the_route_action(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_errors(event, "validation", {"message": "bad"})
        match, payload, _ = parse(clients.flush.messages(group_name("auditErrors"))[0])
        assert match.group("type") == "validation"
        assert payload["message"] == "bad" and payload["action"] == "assetUpdate" and payload["outcome"] == "failed"

    def test_two_groups_are_written_in_parallel(self, monkeypatch, claims):
        delay = 0.2
        flush_client = RecordingClient(delay=delay)
        monkeypatch.setattr(auditLogging, "audit_flush_client", flush_client)
        auditLogging._get_flush_executor()  # warm the pool, as a warm container has it
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "assetUpdate")
            auditLogging.log_action(event, "fileDownload")
            started = time.monotonic()
        elapsed = time.monotonic() - started
        assert len(flush_client.calls) == 2
        assert elapsed < delay * 1.5, "two groups took %.3fs, as if written one after the other" % elapsed

    def test_a_flush_past_the_deadline_reports_the_records(self, monkeypatch, claims, capsys):
        monkeypatch.setattr(auditLogging, "AUDIT_FLUSH_DEADLINE_SECONDS", 0.05)
        monkeypatch.setattr(auditLogging, "audit_flush_client", RecordingClient(delay=0.3))
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "assetUpdate")
        lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
        fallbacks = [line for line in lines if line.get("auditFallback")]
        metrics = [line for line in lines if "_aws" in line]
        assert len(fallbacks) == 1 and fallbacks[0]["reason"] == "flushDeadlineExceeded"
        assert fallbacks[0]["logGroupKey"] == "auditActions" and "assetUpdate" in fallbacks[0]["record"]
        assert metrics and metrics[0]["AuditWriteFailures"] == 1

    def test_a_rejected_flush_reports_the_records(self, monkeypatch, claims, capsys):
        monkeypatch.setattr(auditLogging, "audit_flush_client", RecordingClient(put_error=RuntimeError("boom")))
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "assetUpdate")
        fallbacks = [json.loads(line) for line in capsys.readouterr().out.splitlines()
                     if line.startswith('{"auditFallback"')]
        assert [line["reason"] for line in fallbacks] == ["putLogEventsFailed"]

    def test_the_pre_timeout_timer_flushes_before_the_handler_returns(self, clients):
        context = SimpleNamespace(aws_request_id="r", get_remaining_time_in_millis=lambda: 800)
        event = api_event()
        with auditLogging.audit_request(event, context):
            auditLogging.log_action(event, "assetUpdate")
            deadline = time.monotonic() + 2
            while not clients.flush.calls and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(clients.flush.calls) == 1, "the timer flushed the buffer"
            auditLogging.log_action(event, "metadataUpdate")
            assert len(clients.flush.calls) == 2, "after the timer a record is written at once"

    def test_a_context_opened_near_the_timeout_writes_through(self, clients, capsys):
        context = SimpleNamespace(aws_request_id="r", get_remaining_time_in_millis=lambda: 100)
        event = api_event()
        with auditLogging.audit_request(event, context):
            auditLogging.log_action(event, "assetUpdate")
            # No time is left to wait for the write, so the record is reported at once; the write
            # is still started.
            fallbacks = [json.loads(line) for line in capsys.readouterr().out.splitlines()
                         if line.startswith('{"auditFallback"')]
            assert [line["reason"] for line in fallbacks] == ["flushDeadlineExceeded"]
            deadline = time.monotonic() + 2
            while not clients.flush.calls and time.monotonic() < deadline:
                time.sleep(0.01)
            assert len(clients.flush.calls) == 1

    def test_the_flush_deadline_leaves_room_for_the_fallback(self):
        def context(remaining_ms):
            return SimpleNamespace(lambda_context=SimpleNamespace(get_remaining_time_in_millis=lambda: remaining_ms))
        assert auditLogging._flush_deadline_seconds(context(30000)) == auditLogging.AUDIT_FLUSH_DEADLINE_SECONDS
        assert auditLogging._flush_deadline_seconds(context(750)) == pytest.approx(0.5)
        assert auditLogging._flush_deadline_seconds(context(100)) == 0.0
        assert auditLogging._flush_deadline_seconds(None) == auditLogging.AUDIT_FLUSH_DEADLINE_SECONDS

    def test_a_slow_pre_timeout_flush_reports_before_the_timeout(self, monkeypatch, claims):
        # 1,000 ms left when the request starts, and a write that takes 3 s: the fallback line and
        # the metric must be written before the 1,000 ms mark, while the invocation still runs.
        monkeypatch.setattr(auditLogging, "audit_flush_client", RecordingClient(delay=3.0))
        started = time.monotonic()
        context = SimpleNamespace(
            aws_request_id="r",
            get_remaining_time_in_millis=lambda: int(1000 - (time.monotonic() - started) * 1000))
        reported = []
        real_report = auditLogging.report_undelivered_audit_records

        def recording_report(log_type, reason, records):
            reported.append((time.monotonic() - started, reason))
            real_report(log_type, reason, records)

        monkeypatch.setattr(auditLogging, "report_undelivered_audit_records", recording_report)
        event = api_event()
        with auditLogging.audit_request(event, context):
            auditLogging.log_action(event, "assetUpdate")
            time.sleep(1.0 - (time.monotonic() - started) + 0.05)
            assert reported, "nothing was reported by the time the function would have timed out"
            at, reason = reported[0]
            assert reason == "flushDeadlineExceeded"
            assert at < 1.0 - auditLogging.AUDIT_FALLBACK_RESERVE_MS / 1000.0 + 0.1

    def test_records_are_flushed_when_the_handler_raises(self, clients):
        event = api_event()
        with pytest.raises(ValueError):
            with auditLogging.audit_request(event, LAMBDA_CONTEXT):
                auditLogging.log_action(event, "assetUpdate")
                raise ValueError("handler failed")
        assert len(clients.flush.calls) == 1

    def test_a_nested_context_joins_the_outer_one(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT) as outer:
            with auditLogging.audit_request(event, LAMBDA_CONTEXT) as inner:
                assert inner is outer
                auditLogging.log_action(event, "assetUpdate")
            assert clients.flush.calls == []
        assert len(clients.flush.calls) == 1

    def test_a_record_for_another_event_is_written_as_before(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_actions({}, "pipelineCreate", {"pipelineId": "p1"})
            assert len(clients.module.calls) == 1
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload == {"pipelineId": "p1"}, "a record outside the context keeps its legacy payload"

    def test_a_record_from_a_worker_thread_is_written_at_once(self, clients):
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            worker = threading.Thread(target=auditLogging.log_action, args=(event, "assetUpdate"))
            worker.start()
            worker.join()
            assert len(clients.module.calls) == 1

    def test_the_decorator_opens_a_context(self, clients):
        seen = {}

        @auditLogging.audited_handler
        def handler(event, context):
            seen["ctx"] = auditLogging.current_audit_context()
            auditLogging.log_action(event, "assetUpdate")
            return {"statusCode": 200}

        assert handler(api_event(), LAMBDA_CONTEXT) == {"statusCode": 200}
        assert seen["ctx"] is not None and auditLogging.current_audit_context() is None
        assert len(clients.flush.calls) == 1

    def test_a_context_that_fails_to_open_does_not_fail_the_handler(self, clients, monkeypatch):
        monkeypatch.setattr(auditLogging, "_new_audit_context", lambda *a: 1 / 0)
        event = api_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT) as ctx:
            assert ctx is None
            auditLogging.log_action(event, "assetUpdate")
        assert len(clients.module.calls) == 1


# ---------------------------------------------------------------------------------------------------
# T7: legacy records inside a context keep their keys and gain the contract keys
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestLegacyRecordsInAContext:
    def test_log_actions_keeps_its_payload_and_gains_the_contract_keys(self, clients):
        event = api_event(path="/database/db1/workflows/w1", method="PUT")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_actions(event, "workflowUpdate", {"workflowId": "w1", "action": "legacy-kept"})
        match, payload, echo = parse(clients.flush.messages()[0])
        assert match.group("prefix") == "[ACTIONS][type: workflowUpdate]"
        assert payload["workflowId"] == "w1"
        assert payload["action"] == "workflowUpdate", "the contract key wins over a legacy key"
        assert payload["legacy"] == {"action": "legacy-kept"}, "the clashing legacy value is kept under legacy"
        assert payload["auditVersion"] == 1 and payload["outcome"] == "success"
        assert payload["actor"]["userId"] == "alice" and payload["entity"] == {"type": "workflow"}
        assert "header-marker" in echo, "a legacy record keeps its full masked echo"
        assert "secret-token-value" not in echo

    def test_contract_keys_win_over_clashing_legacy_keys(self, clients):
        # An AMI recipe build writes its own outcome (in-progress); inside a context the contract
        # outcome is written and the legacy value moves under legacy.
        event = api_event(path="/addon/edh/admin/ami-recipes/r1/builds", method="POST")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_actions(event, "edhAmiRecipe", {
                "operation": "build", "recipeId": "r1", "outcome": "in-progress",
                "auditVersion": "x", "recordId": 7, "eventTime": None, "actor": "spoof",
                "request": [], "entity": "e", "legacy": "older"})
        _, payload, _ = parse(clients.flush.messages()[0])
        assert payload["auditVersion"] == 1
        assert payload["outcome"] in auditLogging.AUDIT_OUTCOMES and payload["outcome"] == "success"
        assert payload["action"] == "edhAmiBuildCreate"
        assert isinstance(payload["recordId"], str) and re.match(r"^[0-9a-f-]{36}$", payload["recordId"])
        assert re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$", payload["eventTime"])
        assert isinstance(payload["actor"], dict) and payload["actor"]["userId"] == "alice"
        assert isinstance(payload["request"], dict) and payload["request"]["requestId"] == "req-1"
        assert payload["entity"] == {"type": "amiBuild"}
        assert payload["legacy"] == {"outcome": "in-progress", "auditVersion": "x", "recordId": 7,
                                     "eventTime": None, "actor": "spoof", "request": [], "entity": "e",
                                     "legacy": "older"}
        assert payload["operation"] == "build" and payload["recipeId"] == "r1"

    def test_an_equal_legacy_key_is_not_moved(self, clients):
        event = api_event(path="/database/db1/workflows/w1", method="PUT")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_actions(event, "workflowUpdate", {"workflowId": "w1", "outcome": "success"})
        _, payload, _ = parse(clients.flush.messages()[0])
        assert "legacy" not in payload and payload["outcome"] == "success"

    def test_a_legacy_type_maps_to_the_route_action(self, clients):
        event = api_event(path="/auth/constraints/c1", method="PUT")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_actions(event, "constraintCreateUpdate", {"constraintId": "c1"})
        match, payload, _ = parse(clients.flush.messages()[0])
        assert match.group("type") == "constraintCreateUpdate"
        assert payload["action"] == "constraintUpdate"

    def test_a_secondary_action_keeps_its_own_name(self, clients):
        event = api_event(path="/database/db1/workflows/w1", method="PUT")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_actions(event, "workflowRedeploy", {"workflowId": "w1"})
        _, payload, _ = parse(clients.flush.messages()[0])
        assert payload["action"] == "workflowRedeploy"

    def test_file_records_gain_the_route_action(self, clients):
        event = api_event(path="/uploads", method="POST")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_file_upload(event, "db1", "a1", "2 files", False, None, {"fileCount": 2})
        match, payload, _ = parse(clients.flush.messages(group_name("auditFileUpload"))[0])
        assert match.group("prefix") == "[FILEUPLOAD]"
        assert payload["filePath"] == "2 files" and payload["action"] == "fileUploadStart"

    def test_a_denied_upload_is_written_at_once(self, clients):
        event = api_event(path="/uploads/u1/complete", method="POST")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_file_upload(event, "db1", "a1", "f", True, "blocked extension")
            assert len(clients.flush.calls) == 1
        _, payload, _ = parse(clients.flush.messages()[0])
        assert payload["uploadDenied"] is True and payload["outcome"] == "denied"

    def test_a_bulk_download_writes_one_record_per_file_in_the_flush(self, clients):
        event = api_event(path="/database/db1/assets/a1/download", method="POST")
        entries = [{"filePath": f"f{index}"} for index in range(12000)]
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_file_download_bulk(event, "db1", "a1", entries, {"downloadType": "assetFiles"})
            assert clients.flush.calls == []
        assert len(clients.flush.messages()) == 12000
        assert len(clients.flush.calls) >= 2, "split inside the 10,000-event budget"
        for call in clients.flush.calls:
            assert len(call["logEvents"]) <= auditLogging.AUDIT_BATCH_MAX_EVENTS
        record_ids = {parse(message)[1]["recordId"] for message in clients.flush.messages()[:50]}
        assert len(record_ids) == 50
        _, payload, _ = parse(clients.flush.messages()[0])
        assert payload["action"] == "fileDownload"

    def test_auth_records_are_unchanged_inside_a_context(self, clients):
        event = api_event(path="/roles", method="POST")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_auth_changes(event, "roleCreate", {"roleName": "r1"})
            assert len(clients.module.calls) == 1, "written at once by the module client, as before"
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload == {"roleName": "r1"}

    def test_outside_a_context_a_legacy_record_is_byte_identical(self, clients):
        event = api_event(path="/database/db1/workflows/w1", method="PUT")
        auditLogging.log_actions(event, "workflowUpdate", {"workflowId": "w1"})
        message = clients.module.messages()[0]
        expected = (auditLogging._format_log_message(
            "[ACTIONS][type: workflowUpdate]", {"user": "alice", "roles": ["admin"], "mfaEnabled": True},
            {"workflowId": "w1"}) + auditLogging._event_suffix(event))
        assert message == expected


# ---------------------------------------------------------------------------------------------------
# Owner rule enforced by the core: reads, searches and downloads record the request only
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestRegistryKindIsEnforced:
    @pytest.mark.parametrize("action,path,method", [
        ("assetGet", "/database/db1/assets/a1", "GET"),
        ("search", "/search", "POST"),
        ("assetExport", "/database/db1/assets/a1/export", "POST"),
    ])
    def test_request_only_kinds_drop_the_write_sections(self, clients, action, path, method):
        assert REGISTRY.AUDIT_ACTIONS[action].kind in ("read", "search", "download")
        auditLogging.log_action(api_event(path=path, method=method), action, asset_id="a1",
                                changes={"assetName": "RETURNED-NAME"}, previous={"assetName": "OLD-NAME"},
                                request_parameters={"maxItems": 10})
        message = clients.module.messages()[0]
        _, payload, _ = parse(message)
        assert "changes" not in payload and "previous" not in payload
        assert "RETURNED-NAME" not in message and "OLD-NAME" not in message
        assert payload["requestParameters"] == {"maxItems": 10}

    def test_a_write_kind_keeps_the_write_sections(self, clients):
        # Control: the same call on a write action records both sections.
        auditLogging.log_action(api_event(), "assetUpdate", asset_id="a1",
                                changes={"assetName": "NEW"}, previous={"assetName": "OLD"})
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["changes"] == {"assetName": "NEW"} and payload["previous"] == {"assetName": "OLD"}

    def test_the_route_action_is_enforced_too(self, clients):
        event = api_event(path="/database/db1/assets/a1", method="GET")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, asset_id="a1", changes={"assetName": "RETURNED"})
        _, payload, _ = parse(clients.flush.messages()[0])
        assert payload["action"] == "assetGet" and "changes" not in payload

    def test_read_item_count_is_one_switch(self, clients, monkeypatch):
        auditLogging.log_action(api_event(method="GET"), "assetList", item_count=25)
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["itemCount"] == 25
        monkeypatch.setattr(auditLogging, "AUDIT_READ_ITEM_COUNT", False)
        clients.module.calls.clear()
        auditLogging.log_action(api_event(method="GET"), "assetList", item_count=25)
        auditLogging.log_action(api_event(), "metadataUpdate", item_count=4)
        read_payload = parse(clients.module.messages()[0])[1]
        write_payload = parse(clients.module.messages()[1])[1]
        assert "itemCount" not in read_payload
        assert write_payload["itemCount"] == 4, "the switch covers reads and searches only"


# ---------------------------------------------------------------------------------------------------
# The 32 KB cap bounds the whole formatted record: prefix, payload and echo
# ---------------------------------------------------------------------------------------------------

def _record_bytes(message):
    return len(message.encode("utf-8"))


@pytest.mark.unit
class TestWholeRecordCap:
    def test_a_long_path_truncates_the_echo_first(self, clients):
        path = "/database/db1/assets/" + "a" * 33000
        auditLogging.log_action(api_event(path=path), "assetUpdate", changes={"assetName": "kept"})
        message = clients.module.messages()[0]
        assert _record_bytes(message) <= auditLogging.AUDIT_RECORD_MAX_BYTES
        _, payload, echo = parse(message)
        assert echo == auditLogging.AUDIT_ECHO_TRUNCATED
        assert payload["changes"] == {"assetName": "kept"}, "payload values are cut only after the echo"
        assert payload["truncated"] is True

    def test_a_long_prefix_and_large_values_cut_the_payload_to_fit(self, clients):
        roles = tuple("r" * 60 + str(index) for index in range(250))  # about 16 KB of prefix
        changes = {f"field{index}": "v" * 1900 for index in range(12)}  # about 23 KB of values
        auditLogging.log_action(api_event(roles=roles), "metadataUpdate", changes=changes)
        message = clients.module.messages()[0]
        assert _record_bytes(message) <= auditLogging.AUDIT_RECORD_MAX_BYTES
        match, payload, _ = parse(message)
        assert len(json.loads(match.group("roles"))) == 250, "the prefix is kept whole"
        assert payload["truncated"] is True and payload["action"] == "metadataUpdate"
        assert set(payload["changes"]) == set(changes)

    def test_a_prefix_over_the_cap_cuts_the_record_with_the_marker(self, clients):
        roles = tuple("r" * 60 + str(index) for index in range(600))  # about 40 KB of prefix
        auditLogging.log_action(api_event(roles=roles), "assetUpdate")
        message = clients.module.messages()[0]
        assert _record_bytes(message) <= auditLogging.AUDIT_RECORD_MAX_BYTES
        assert message.endswith(auditLogging.AUDIT_EVENT_TRUNCATION_MARKER)
        assert message.startswith("[ACTIONS][type: assetUpdate] [user: alice] [roles: [")

    def test_a_record_under_the_cap_is_unchanged(self, clients):
        # Control: an ordinary record keeps its compact echo and no truncated flag.
        auditLogging.log_action(api_event(), "assetUpdate", changes={"assetName": "x"})
        _, payload, echo = parse(clients.module.messages()[0])
        assert "truncated" not in payload and echo.startswith(' --- [event: {"requestContext"')


# ---------------------------------------------------------------------------------------------------
# Credential field names are redacted in every value section, by whole name in any letter case
# ---------------------------------------------------------------------------------------------------

CREDENTIAL_FIELDS = ("password", "secret", "clientSecret", "client_secret", "accessToken", "access_token",
                     "refreshToken", "refresh_token", "privateKey", "private_key")


@pytest.mark.unit
class TestCredentialFieldRedaction:
    @pytest.mark.parametrize("field", CREDENTIAL_FIELDS + ("ClientSecret", "PASSWORD", "Refresh_Token"))
    def test_a_credential_field_is_redacted(self, clients, field):
        value = f"SENTINEL-{field}-VALUE"
        auditLogging.log_action(api_event(), "assetUpdate", changes={field: value}, previous={field: value})
        message = clients.module.messages()[0]
        assert value not in message
        _, payload, _ = parse(message)
        assert payload["changes"][field] == "<redacted>" and payload["previous"][field] == "<redacted>"

    def test_fields_that_only_contain_the_word_stay_readable(self, clients):
        # Control: whole-name matching, so ordinary fields are not hidden.
        ordinary = {"passwordPolicy": "strong", "secretArn": "arn:aws:secretsmanager:x", "accessTokenExpiry": 3600,
                    "clientSecretRotatedOn": "2026-11-12", "privateKeyFingerprint": "ab:cd"}
        auditLogging.log_action(api_event(), "assetUpdate", changes=ordinary)
        _, payload, _ = parse(clients.module.messages()[0])
        assert payload["changes"] == ordinary


# ---------------------------------------------------------------------------------------------------
# app.auditLogging.recordReads, delivered as AUDIT_RECORD_READS
# ---------------------------------------------------------------------------------------------------

@pytest.mark.unit
class TestRecordReadsSwitch:
    READS = (("assetGet", "/database/db1/assets/a1", "GET"), ("assetList", "/database/db1/assets", "GET"),
             ("search", "/search", "POST"))

    def _write_reads(self, clients):
        for action, path, method in self.READS:
            auditLogging.log_action(api_event(path=path, method=method), action, item_count=3)
        return [parse(message)[1]["action"] for message in clients.module.messages()]

    @pytest.mark.parametrize("value", [None, "true", "TRUE", "", "yes"])
    def test_reads_are_recorded_unless_the_switch_is_false(self, clients, monkeypatch, value):
        if value is None:
            monkeypatch.delenv("AUDIT_RECORD_READS", raising=False)
        else:
            monkeypatch.setenv("AUDIT_RECORD_READS", value)
        assert self._write_reads(clients) == ["assetGet", "assetList", "search"]

    @pytest.mark.parametrize("value", ["false", "False", " false "])
    def test_false_turns_read_and_search_records_off(self, clients, monkeypatch, value):
        monkeypatch.setenv("AUDIT_RECORD_READS", value)
        assert self._write_reads(clients) == []

    def test_writes_downloads_and_exports_are_recorded_when_reads_are_off(self, clients, monkeypatch):
        monkeypatch.setenv("AUDIT_RECORD_READS", "false")
        auditLogging.log_action(api_event(), "assetUpdate", changes={"assetName": "x"})
        auditLogging.log_action(api_event(path="/database/db1/assets/a1/export", method="POST"), "assetExport")
        auditLogging.log_action(api_event(path="/database/db1/assets/a1/download", method="POST"), "fileDownload")
        actions = [parse(message)[1]["action"] for message in clients.module.messages()]
        assert actions == ["assetUpdate", "assetExport", "fileDownload"]

    def test_legacy_download_records_are_kept_when_reads_are_off(self, clients, monkeypatch):
        monkeypatch.setenv("AUDIT_RECORD_READS", "false")
        auditLogging.log_file_download(api_event(path="/database/db1/assets/a1/download", method="POST"),
                                       "db1", "a1", "f.glb")
        assert len(clients.module.messages(group_name("auditFileDownload"))) == 1

    def test_denied_and_failed_reads_are_recorded_when_reads_are_off(self, clients, monkeypatch):
        monkeypatch.setenv("AUDIT_RECORD_READS", "false")
        auditLogging.log_action(api_event(method="GET"), "assetGet", outcome="denied", reason="forbidden")
        auditLogging.log_action(api_event(method="GET"), "assetGet", outcome="failed", reason="notFound")
        outcomes = [parse(message)[1]["outcome"] for message in clients.module.messages()]
        assert outcomes == ["denied", "failed"]

    def test_the_route_default_read_is_switched_off_too(self, clients, monkeypatch):
        monkeypatch.setenv("AUDIT_RECORD_READS", "false")
        event = api_event(path="/database/db1/assets/a1", method="GET")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, asset_id="a1")
        assert clients.flush.calls == [] and clients.module.calls == []


# ---------------------------------------------------------------------------------------------------
# The entrypoint decorator records the outcome of a request whose handler recorded nothing
# ---------------------------------------------------------------------------------------------------

_WORKFLOW_PATH = "/database/db1/workflows/w1"


def _workflow_event(method="PUT"):
    event = api_event(path=_WORKFLOW_PATH, method=method)
    event["pathParameters"] = {"databaseId": "db1", "workflowId": "w1", "notAnIdField": "x"}
    return event


def _handler_returning(status, record=None):
    @auditLogging.audited_handler
    def handler(event, context):
        if record:
            auditLogging.log_action(event, record)
        return {"statusCode": status}

    return handler


@pytest.mark.unit
class TestEntrypointOutcome:
    @pytest.mark.parametrize("status, reason", [
        (400, "validation"), (404, "notFound"), (409, "conflict"), (418, "clientError"),
        (500, "internal"), (503, "internal")])
    def test_an_error_response_is_recorded_as_the_routes_failed_action(self, clients, status, reason):
        _handler_returning(status)(_workflow_event(), LAMBDA_CONTEXT)
        records = [parse(message)[1] for message in clients.flush.messages(group_name("auditActions"))]
        assert len(records) == 1
        payload = records[0]
        assert payload["action"] == "workflowUpdate"
        assert payload["outcome"] == "failed" and payload["reason"] == reason
        assert payload["databaseId"] == "db1" and payload["workflowId"] == "w1"
        assert "notAnIdField" not in json.dumps(payload), "only id fields are taken from the path"

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_denial_is_left_to_the_authorization_record(self, clients, status):
        _handler_returning(status)(_workflow_event(), LAMBDA_CONTEXT)
        assert clients.flush.calls == [] and clients.module.calls == []

    @pytest.mark.parametrize("status", [200, 201, 204, 303])
    def test_a_success_response_adds_no_record(self, clients, status):
        _handler_returning(status)(_workflow_event(), LAMBDA_CONTEXT)
        assert clients.flush.calls == [] and clients.module.calls == []

    def test_a_handler_that_recorded_the_action_gets_no_second_record(self, clients):
        _handler_returning(400, record="workflowUpdate")(_workflow_event(), LAMBDA_CONTEXT)
        records = [parse(message)[1] for message in clients.flush.messages()]
        assert [record["outcome"] for record in records] == ["success"]

    def test_a_legacy_record_of_the_action_counts_as_recorded(self, clients):
        @auditLogging.audited_handler
        def handler(event, context):
            auditLogging.log_actions(event, "workflowUpdate", {"workflowId": "w1"})
            return {"statusCode": 500}

        handler(_workflow_event(), LAMBDA_CONTEXT)
        assert len(clients.flush.messages(group_name("auditActions"))) == 1

    def test_an_error_record_does_not_count_as_recorded(self, clients):
        @auditLogging.audited_handler
        def handler(event, context):
            auditLogging.log_errors(event, "validation", {"statusCode": 400})
            return {"statusCode": 400}

        handler(_workflow_event(), LAMBDA_CONTEXT)
        errors = clients.flush.messages(group_name("auditErrors"))
        actions = [parse(message)[1] for message in clients.flush.messages(group_name("auditActions"))]
        assert len(errors) == 1
        assert [(record["action"], record["outcome"]) for record in actions] == [("workflowUpdate", "failed")]

    def test_an_exception_is_recorded_as_failed_and_raised(self, clients):
        @auditLogging.audited_handler
        def handler(event, context):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            handler(_workflow_event(), LAMBDA_CONTEXT)
        records = [parse(message)[1] for message in clients.flush.messages(group_name("auditActions"))]
        assert [(record["outcome"], record["reason"]) for record in records] == [("failed", "internal")]
        assert "boom" not in json.dumps(records), "the error text is never recorded"

    def test_a_response_object_with_a_status_attribute_is_read(self, clients):
        @auditLogging.audited_handler
        def handler(event, context):
            return SimpleNamespace(statusCode=404)

        handler(_workflow_event(), LAMBDA_CONTEXT)
        records = [parse(message)[1] for message in clients.flush.messages(group_name("auditActions"))]
        assert [record["reason"] for record in records] == ["notFound"]

    def test_a_pending_action_is_skipped(self, clients, monkeypatch):
        monkeypatch.setattr(auditLogging, "AUDIT_PENDING_ACTIONS", frozenset({"workflowUpdate"}))
        _handler_returning(500)(_workflow_event(), LAMBDA_CONTEXT)
        assert clients.flush.calls == [] and clients.module.calls == []

    @pytest.mark.parametrize("path, method", [("/roles", "POST"), ("/auth/loginProfile/u1", "POST")])
    def test_an_auth_record_route_adds_no_outcome_record(self, clients, monkeypatch, path, method):
        monkeypatch.setattr(auditLogging, "AUDIT_PENDING_ACTIONS", frozenset())
        _handler_returning(500)(api_event(path=path, method=method), LAMBDA_CONTEXT)
        assert clients.flush.calls == [] and clients.module.calls == []

    def test_an_unregistered_route_adds_no_record(self, clients):
        _handler_returning(500)(api_event(path="/no/such/route"), LAMBDA_CONTEXT)
        assert clients.flush.calls == [] and clients.module.calls == []

    def test_a_system_event_adds_no_record(self, clients):
        _handler_returning(500)({"Records": []}, LAMBDA_CONTEXT)
        assert clients.flush.calls == [] and clients.module.calls == []

    def test_a_nested_entrypoint_leaves_the_outcome_to_the_outer_one(self, clients):
        inner = _handler_returning(500)

        @auditLogging.audited_handler
        def outer(event, context):
            inner(event, context)
            return {"statusCode": 500}

        outer(_workflow_event(), LAMBDA_CONTEXT)
        assert len(clients.flush.messages(group_name("auditActions"))) == 1

    def test_on_behalf_of_names_the_user_a_system_action_ran_for(self, clients):
        auditLogging.log_action({"Records": []}, "workflowExecutionEnd", executionId="e1",
                                on_behalf_of="alice")
        _, payload, _ = parse(clients.module.messages(group_name("auditActions"))[0])
        assert payload["actor"]["userId"] == "SYSTEM_USER"
        assert payload["actor"]["authMethod"] == "system"
        assert payload["actor"]["onBehalfOf"] == "alice"


@pytest.mark.unit
class TestLegacyActionOutcome:
    def test_a_failed_legacy_record_carries_the_outcome_and_is_written_at_once(self, clients):
        event = _workflow_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_actions(event, "workflowUpdate", {"workflowId": "w1"}, outcome="failed")
            assert len(clients.flush.calls) == 1, "a failed record is not held for the flush"
        _, payload, _ = parse(clients.flush.messages(group_name("auditActions"))[0])
        assert payload["outcome"] == "failed" and payload["workflowId"] == "w1"

    def test_a_legacy_record_defaults_to_success(self, clients):
        event = _workflow_event()
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_actions(event, "workflowUpdate", {"workflowId": "w1"})
            assert clients.flush.calls == []
        _, payload, _ = parse(clients.flush.messages(group_name("auditActions"))[0])
        assert payload["outcome"] == "success"
