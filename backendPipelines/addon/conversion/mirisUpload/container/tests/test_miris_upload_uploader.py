#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json
import os
import sys

import pytest
import requests
import responses

pytestmark = pytest.mark.unit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from miris_uploader import MirisClient, MirisError, _redact_response  # noqa: E402

BASE = "https://app.miris.test"
KEY = "test-integration-key-xxx"
UUID = "11111111-2222-3333-4444-555555555555"
ASSET_URL = f"{BASE}/v1/asset/{UUID}"
COMPLETE_URL = f"{BASE}/v1/asset/upload/{UUID}"
GENERATE_URL = f"{BASE}/v1/asset/{UUID}/generate"


def _client(**kwargs):
    sleeps = []
    return MirisClient(BASE, KEY, sleep=sleeps.append, **kwargs), sleeps


def _start(client):
    return client.start_upload(name="m", content_path="m.usdz", total_bytes=1, tags=[])


@responses.activate
def test_start_upload_posts_the_documented_body_with_the_api_key_header():
    responses.add(responses.POST, f"{BASE}/v1/asset", json={"id": UUID, "upload_path": "s3://b/p"})

    resp = MirisClient(BASE, KEY).start_upload(name="model", content_path="model.usdz", total_bytes=1024, tags=["vams"])

    request = responses.calls[0].request
    assert json.loads(request.body) == {
        "name": "model",
        "content_path": "model.usdz",
        "file_count": 1,
        "input_type": "usd",
        "output_type": "static",
        "tags": ["vams"],
        "total_bytes": 1024,
    }
    assert request.headers["Miris-API-Key"] == KEY
    assert "Authorization" not in request.headers
    assert resp["id"] == UUID


@responses.activate
def test_mark_upload_complete_and_generate_send_their_bodies():
    responses.add(responses.PUT, COMPLETE_URL, json={"id": UUID})
    responses.add(responses.POST, GENERATE_URL, json={"state": "processing_streamable"})

    client = MirisClient(BASE, KEY)
    client.mark_upload_complete(UUID)
    assert client.trigger_generate_best_effort(UUID)["state"] == "processing_streamable"

    assert json.loads(responses.calls[0].request.body) == {"status": "completed"}
    assert responses.calls[1].request.body in (None, b"", "")


@responses.activate
@pytest.mark.parametrize("states, final", [
    (["processing_preview", "processing_preview", "preview"], "preview"),
    (["streamable"], "streamable"),
], ids=["preview", "streamable"])
def test_poll_until_terminal_stops_at_a_success_state(states, final):
    for state in states:
        responses.add(responses.GET, ASSET_URL, json={"id": UUID, "state": state})

    client, _ = _client()
    assert client.poll_until_terminal(UUID, timeout_seconds=10, poll_interval_seconds=0)["state"] == final
    assert len(responses.calls) == len(states)


@responses.activate
def test_poll_until_terminal_raises_on_an_error_state_and_on_timeout():
    responses.add(responses.GET, ASSET_URL, json={"id": UUID, "state": "failed"})
    client, _ = _client()
    with pytest.raises(MirisError, match="failed"):
        client.poll_until_terminal(UUID, timeout_seconds=10, poll_interval_seconds=0)

    responses.replace(responses.GET, ASSET_URL, json={"id": UUID, "state": "processing_preview"})
    with pytest.raises(TimeoutError):
        client.poll_until_terminal(UUID, timeout_seconds=0, poll_interval_seconds=0)


def test_redact_response_masks_the_credentials_without_mutating_its_input():
    original = {"id": "x", "access_key_id": "AK", "secret_key": "SK", "session_token": "ST", "endpoint_url": "e"}
    redacted = _redact_response(original)
    assert redacted == {"id": "x", "access_key_id": "<redacted>", "secret_key": "<redacted>",
                        "session_token": "<redacted>", "endpoint_url": "e"}
    assert original["secret_key"] == "SK"


@responses.activate
@pytest.mark.parametrize("url, call", [
    (ASSET_URL, lambda c: c.get_asset(UUID)),
    (COMPLETE_URL, lambda c: c.mark_upload_complete(UUID)),
], ids=["GET", "PUT"])
@pytest.mark.parametrize("failure", [
    {"json": {}, "status": 503},
    {"body": requests.ConnectionError("connection reset")},
    {"body": requests.exceptions.ChunkedEncodingError("connection broken")},
], ids=["5xx", "connection-error", "cut-off-body"])
def test_idempotent_calls_retry_a_transient_failure_with_backoff(url, call, failure):
    method = responses.PUT if url == COMPLETE_URL else responses.GET
    responses.add(method, url, **failure)
    responses.add(method, url, **failure)
    responses.add(method, url, json={"id": UUID, "state": "preview"})

    client, sleeps = _client()
    assert call(client)
    assert len(responses.calls) == 3
    # Exponential: each wait at least as long as the one before it.
    assert len(sleeps) == 2 and 0 < sleeps[0] <= sleeps[1]


@responses.activate
def test_get_asset_gives_up_after_the_attempt_limit():
    responses.add(responses.GET, ASSET_URL, json={}, status=503)

    client, sleeps = _client(max_attempts=3)
    with pytest.raises(MirisError, match="503"):
        client.get_asset(UUID)
    assert (len(responses.calls), len(sleeps)) == (3, 2)


@responses.activate
@pytest.mark.parametrize("method, url, call, failure", [
    (responses.GET, ASSET_URL, lambda c: c.get_asset(UUID), {"json": {}, "status": 404}),
    (responses.GET, ASSET_URL, lambda c: c.get_asset(UUID), {"body": requests.exceptions.InvalidURL("bad")}),
    (responses.POST, f"{BASE}/v1/asset", _start, {"json": {}, "status": 503}),
    (responses.POST, f"{BASE}/v1/asset", _start, {"body": requests.exceptions.ChunkedEncodingError("broken")}),
], ids=["4xx", "malformed-request", "create-5xx", "create-cut-off-body"])
def test_a_permanent_failure_or_a_create_is_not_retried(method, url, call, failure):
    # POST /v1/asset creates an asset; a retry after a lost response would create a second one.
    responses.add(method, url, **failure)

    client, sleeps = _client()
    with pytest.raises(MirisError):
        call(client)
    assert len(responses.calls) == 1
    assert sleeps == []


@responses.activate
def test_poll_survives_a_transient_error_between_polls():
    responses.add(responses.GET, ASSET_URL, json={"state": "processing_preview"})
    responses.add(responses.GET, ASSET_URL, json={}, status=500)
    responses.add(responses.GET, ASSET_URL, json={"state": "preview"})

    client, _ = _client()
    assert client.poll_until_terminal(UUID, timeout_seconds=10, poll_interval_seconds=0)["state"] == "preview"


@responses.activate
@pytest.mark.parametrize("answer", [
    {"json": {"detail": "not found"}, "status": 404},
    {"json": {}, "status": 500},
    {"body": "not json", "status": 200},
    {"body": requests.ConnectionError("connection reset")},
], ids=["4xx", "5xx", "unreadable-body", "connection-error"])
def test_trigger_generate_best_effort_returns_none_on_failure(answer):
    responses.add(responses.POST, GENERATE_URL, **answer)

    client, _ = _client()
    assert client.trigger_generate_best_effort(UUID) is None


@responses.activate
def test_an_unreadable_asset_body_is_a_miris_error():
    responses.add(responses.GET, ASSET_URL, body="<html>", status=200)

    client, _ = _client()
    with pytest.raises(MirisError, match="unreadable"):
        client.get_asset(UUID)


ELSEWHERE = "https://elsewhere.test/collect"


@responses.activate
@pytest.mark.parametrize("method, url, call", [
    (responses.POST, f"{BASE}/v1/asset", _start),
    (responses.PUT, COMPLETE_URL, lambda c: c.mark_upload_complete(UUID)),
    (responses.GET, ASSET_URL, lambda c: c.get_asset(UUID)),
    (responses.POST, GENERATE_URL, lambda c: c.trigger_generate_best_effort(UUID)),
], ids=["create", "complete", "get", "generate"])
def test_a_redirect_is_not_followed(method, url, call):
    # The other host also answers, so a followed redirect would be recorded as a second call.
    responses.add(method, url, status=302, headers={"Location": ELSEWHERE})
    for other in (responses.GET, responses.POST, responses.PUT):
        responses.add(other, ELSEWHERE, json={"id": UUID, "state": "preview"})

    client, sleeps = _client()
    if url == GENERATE_URL:
        assert call(client) is None
    else:
        with pytest.raises(MirisError, match="302"):
            call(client)
    assert len(responses.calls) == 1
    assert sleeps == []
