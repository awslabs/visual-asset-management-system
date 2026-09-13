# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the v2.6 -> v2.7 ``vectorBackfill`` step: the payload the deployed vectorReindexer
receives, how its response is read, and the skip-vs-fail rule when the function is not published or
cannot be resolved.

The Lambda client is a hand-written fake (moto's Lambda mock needs Docker to invoke), so every test
asserts on the exact invoke arguments the step sends.

Run: python -m pytest test_v2_6_to_v2_7_vector_backfill.py -q
"""

import importlib.util
import io
import json
import logging
import os
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "v26_to_v27_migration", os.path.join(_HERE, "v2.6_to_v2.7_migration.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mig = _load_migration_module()


class _FakeLambdaClient:
    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self._error = error

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


class _FakeSession:
    def __init__(self, lambda_client):
        self._lambda = lambda_client
        self.clients = []

    def client(self, name, **kwargs):
        self.clients.append(name)
        return self._lambda


class _FailingLookup:
    """SsmResourceLookup stand-in for a deployment that does not publish the parameter."""

    def __init__(self, *args, **kwargs):
        pass

    def resolve(self, param_key):
        raise KeyError(f"Resource name parameter not found: {param_key}")


def _api_response(body, status=200):
    payload = json.dumps({"statusCode": status, "body": json.dumps(body)}).encode()
    return {"StatusCode": 200, "Payload": io.BytesIO(payload)}


def _reindexer_body(**overrides):
    """The reindexer's response fields, as ``body`` decodes to them."""
    body = {
        "operation": "enqueue", "reindexRunId": "r-1", "phase": "done", "dryRun": False,
        "deleted": 0, "enqueued": 3, "chunks": 1, "continued": False, "invocations": 1,
    }
    body.update(overrides)
    return body


def _args(**overrides):
    values = {"limit": None, "async_invoke": False}
    values.update(overrides)
    return SimpleNamespace(**values)


ENQUEUE = {"operation": "enqueue", "dryRun": False}


def test_build_vector_reindexer_payload_defaults_to_enqueue():
    assert mig.build_vector_reindexer_payload(clear_vectors=False, dry_run=False) == ENQUEUE


def test_build_vector_reindexer_payload_clear_vectors_is_both_with_limit():
    assert mig.build_vector_reindexer_payload(clear_vectors=True, dry_run=True, limit=50) == {
        "operation": "both",
        "dryRun": True,
        "limit": 50,
    }


def test_build_vector_reindexer_payload_uses_the_reindexer_contract_keys_only():
    payload = mig.build_vector_reindexer_payload(clear_vectors=True, dry_run=False, limit=1)
    # The reindexer rejects unknown top-level keys; the v2.6 snake_case names must not leak in.
    assert set(payload) <= {"operation", "dryRun", "limit"}
    assert "dry_run" not in payload and "clear_indexes" not in payload


def test_invoke_vector_reindexer_decodes_the_response_body():
    body = _reindexer_body(reindexRunId="r-1", enqueued=3)
    client = _FakeLambdaClient(_api_response(body))

    result = mig.invoke_vector_reindexer(client, "fn-vector", ENQUEUE)

    assert result == {"statusCode": 200, "body": body}
    call = client.calls[0]
    assert call["FunctionName"] == "fn-vector"
    assert call["InvocationType"] == "RequestResponse"
    assert json.loads(call["Payload"]) == ENQUEUE


def test_describe_vector_backfill_result_prints_the_run_id_as_the_group_id_filter(caplog):
    body = _reindexer_body(reindexRunId="abc123def456", enqueued=2500, chunks=3)

    with caplog.at_level(logging.INFO):
        mig.describe_vector_backfill_result(
            {"statusCode": 200, "body": body}, "fn", clear_vectors=False, dry_run=False
        )

    assert "Reindex run id:  abc123def456" in caplog.text
    assert "vec-abc123def456-0 .. vec-abc123def456-2" in caplog.text
    assert "vamscli execution list --group-id vec-abc123def456-0" in caplog.text


def test_invoke_vector_reindexer_async_reports_submission():
    client = _FakeLambdaClient({"StatusCode": 202, "Payload": io.BytesIO(b"")})

    result = mig.invoke_vector_reindexer(client, "fn", ENQUEUE, invocation_type="Event")

    assert result["statusCode"] == 202 and "error" not in result
    assert client.calls[0]["InvocationType"] == "Event"


def test_invoke_vector_reindexer_function_error_is_an_error():
    payload = json.dumps({"errorMessage": "boom", "errorType": "RuntimeError"}).encode()
    client = _FakeLambdaClient(
        {"StatusCode": 200, "FunctionError": "Unhandled", "Payload": io.BytesIO(payload)}
    )

    result = mig.invoke_vector_reindexer(client, "fn", ENQUEUE)

    assert "error" in result
    assert result["body"]["errorType"] == "RuntimeError"


def test_invoke_vector_reindexer_rejected_payload_is_an_error():
    client = _FakeLambdaClient(_api_response({"error": "unknown payload keys: ['foo']"}, status=400))

    result = mig.invoke_vector_reindexer(client, "fn", ENQUEUE)

    assert "error" in result and result["statusCode"] == 400
    assert result["body"] == {"error": "unknown payload keys: ['foo']"}


def test_invoke_vector_reindexer_read_timeout_is_reported_not_raised():
    client = _FakeLambdaClient(error=ReadTimeoutError(endpoint_url="https://lambda.local"))

    result = mig.invoke_vector_reindexer(client, "fn", ENQUEUE)

    assert result["timeout"] is True and result["function_name"] == "fn"


def test_invoke_vector_reindexer_client_error_is_reported():
    error = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "missing"}}, "Invoke"
    )
    client = _FakeLambdaClient(error=error)

    result = mig.invoke_vector_reindexer(client, "fn", ENQUEUE)

    assert result == {"error": "missing", "error_code": "ResourceNotFoundException"}


def test_run_vector_backfill_step_skips_when_vector_search_is_off_under_all(monkeypatch):
    monkeypatch.setattr(mig, "SsmResourceLookup", _FailingLookup)
    fake = _FakeLambdaClient(_api_response({}))
    monkeypatch.setattr(mig, "_session", lambda profile, region: _FakeSession(fake))
    config = {"vector_reindexer_function_name": None}

    rc = mig.run_vector_backfill_step(
        config, _args(), "/vams-x/resourceNames", None, None, False,
        clear_vectors=False, explicit=False,
    )

    assert rc == 0
    assert fake.calls == []


def test_run_vector_backfill_step_fails_when_named_explicitly_and_vector_search_is_off(monkeypatch):
    monkeypatch.setattr(mig, "SsmResourceLookup", _FailingLookup)
    fake = _FakeLambdaClient(_api_response({}))
    monkeypatch.setattr(mig, "_session", lambda profile, region: _FakeSession(fake))
    config = {"vector_reindexer_function_name": None}

    rc = mig.run_vector_backfill_step(
        config, _args(), "/vams-x/resourceNames", None, None, False,
        clear_vectors=False, explicit=True,
    )

    assert rc == 1
    assert fake.calls == []


@pytest.mark.parametrize("explicit,expected_rc", [(False, 0), (True, 1)])
def test_run_vector_backfill_step_applies_the_skip_rule_when_nothing_can_resolve_the_name(
    monkeypatch, explicit, expected_rc
):
    # Overrides-only config with the override unset: make_resolver raises ValueError (no prefix to fall
    # back to), not the SSM KeyError; the explicit/all rule must hold for both.
    fake = _FakeLambdaClient(_api_response({}))
    monkeypatch.setattr(mig, "_session", lambda profile, region: _FakeSession(fake))
    config = {"vector_reindexer_function_name": None}

    rc = mig.run_vector_backfill_step(
        config, _args(), None, None, None, False, clear_vectors=False, explicit=explicit,
    )

    assert rc == expected_rc
    assert fake.calls == []


def test_run_vector_backfill_step_sends_both_when_clear_vectors(monkeypatch):
    fake = _FakeLambdaClient(
        _api_response(_reindexer_body(operation="both", reindexRunId="r-9", dryRun=True, tableEmpty=False))
    )
    monkeypatch.setattr(mig, "_session", lambda profile, region: _FakeSession(fake))
    config = {"vector_reindexer_function_name": "vams-vectorReindexer", "limit": None}

    rc = mig.run_vector_backfill_step(
        config, _args(limit=25), None, None, None, True, clear_vectors=True, explicit=True,
    )

    assert rc == 0
    assert fake.calls[0]["FunctionName"] == "vams-vectorReindexer"
    assert json.loads(fake.calls[0]["Payload"]) == {"operation": "both", "dryRun": True, "limit": 25}


def test_run_vector_backfill_step_config_limit_applies_when_the_flag_is_absent(monkeypatch):
    fake = _FakeLambdaClient(_api_response(_reindexer_body(reindexRunId="r-3")))
    monkeypatch.setattr(mig, "_session", lambda profile, region: _FakeSession(fake))
    config = {"vector_reindexer_function_name": "fn", "limit": 7}

    mig.run_vector_backfill_step(
        config, _args(), None, None, None, False, clear_vectors=False, explicit=True,
    )

    assert json.loads(fake.calls[0]["Payload"]) == {"operation": "enqueue", "dryRun": False, "limit": 7}


def test_run_vector_backfill_step_async_flag_uses_event_invocation(monkeypatch):
    fake = _FakeLambdaClient({"StatusCode": 202, "Payload": io.BytesIO(b"")})
    monkeypatch.setattr(mig, "_session", lambda profile, region: _FakeSession(fake))
    config = {"vector_reindexer_function_name": "fn"}

    rc = mig.run_vector_backfill_step(
        config, _args(async_invoke=True), None, None, None, False,
        clear_vectors=False, explicit=True,
    )

    assert rc == 0
    assert fake.calls[0]["InvocationType"] == "Event"


def test_run_vector_backfill_step_returns_one_on_function_error(monkeypatch):
    payload = json.dumps({"errorMessage": "boom"}).encode()
    fake = _FakeLambdaClient(
        {"StatusCode": 200, "FunctionError": "Unhandled", "Payload": io.BytesIO(payload)}
    )
    monkeypatch.setattr(mig, "_session", lambda profile, region: _FakeSession(fake))
    config = {"vector_reindexer_function_name": "fn"}

    rc = mig.run_vector_backfill_step(
        config, _args(), None, None, None, False, clear_vectors=False, explicit=True,
    )

    assert rc == 1


def test_run_vector_backfill_step_treats_a_timeout_as_background_continuation(monkeypatch):
    fake = _FakeLambdaClient(error=ReadTimeoutError(endpoint_url="https://lambda.local"))
    monkeypatch.setattr(mig, "_session", lambda profile, region: _FakeSession(fake))
    config = {"vector_reindexer_function_name": "fn"}

    rc = mig.run_vector_backfill_step(
        config, _args(), None, None, None, False, clear_vectors=False, explicit=True,
    )

    assert rc == 0
