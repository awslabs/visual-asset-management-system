# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fakes for the Miris handlers: Secrets Manager, the HTTP seam, the asset and file-metadata
tables, the enforcer, the Lambda context and the loggers."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.addon.miris import (
    getAssetStatus,
    mintViewerKey,
    mirisIntegration,
    viewerKeyCleanup,
)

INTEGRATION_KEY = "test-integration-key-do-not-log-7f3a9c"
SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:test-miris-api-key-AbCdEf"
BASE_URL = "https://miris.example.test"
DATABASE_ID = "db-1"
ASSET_ID = "asset-1"
STORED_UUID = "8d65af7c-5412-404b-a494-fbd2cdb62442"
OTHER_UUID = "bc336bab-e3e4-4c50-bd00-9d6cbbd9f194"
SORT_KEY_FIELD = "databaseId:assetId:filePath"


def metadata_table(rows_by_file=None):
    """A file-metadata table answering get_item from {databaseId:assetId:filePath: {key: value}}."""
    rows_by_file = rows_by_file or {}
    table = MagicMock()

    def get_item(Key):
        value = rows_by_file.get(Key[SORT_KEY_FIELD], {}).get(Key["metadataKey"])
        if value is None:
            return {}
        return {"Item": {"metadataKey": Key["metadataKey"], SORT_KEY_FIELD: Key[SORT_KEY_FIELD], "metadataValue": value}}

    table.get_item.side_effect = get_item
    return table


def asset_table(found=True, root_key=None):
    table = MagicMock()
    item = {"databaseId": DATABASE_ID, "assetId": ASSET_ID, "bucketId": "bucket-1"}
    if root_key is not None:
        item["assetLocation"] = {"Key": root_key}
    table.get_item.return_value = {"Item": item} if found else {}
    return table


def enforcer(api=True, entity=True):
    casbin = MagicMock()
    casbin.enforceAPI.return_value = api
    casbin.enforce.return_value = entity
    return casbin


def rest_event(method, path, query=None, body=None):
    """An API Gateway REST (v1) proxy event, as the deployed API delivers it.

    `requestTimeEpoch` is the time API Gateway received the request; a test that needs it sets it.
    """
    return {
        "resource": path,
        "path": path,
        "httpMethod": method,
        "headers": {"authorization": "Bearer test-token"},
        "pathParameters": None,
        "queryStringParameters": query,
        "requestContext": {
            "resourcePath": path,
            "httpMethod": method,
            "path": f"/api{path}",
            "stage": "api",
            "identity": {"sourceIp": "203.0.113.10"},
        },
        "body": body,
    }


def claims_after_normalizing(claims):
    """A `request_to_claims` stand-in that normalizes the event in place, as the real one does."""
    from common.auth.apiEvent import normalize_event

    def request_to_claims(event):
        normalize_event(event)
        return claims

    return request_to_claims


def miris_response(status_code, body=""):
    """A fake `requests.Response`. A dict or list `body` is served as JSON, a string as text."""
    response = MagicMock()
    response.status_code = status_code
    if isinstance(body, (dict, list)):
        response.text = json.dumps(body)
        response.json.return_value = body
    else:
        response.text = body
        response.json.side_effect = ValueError("not JSON")
    return response


@pytest.fixture
def miris_api(monkeypatch):
    """Configure the client and replace Secrets Manager and the shared HTTP session.

    Append to `responses` an int status, a `(status, body)` tuple (a dict body is JSON), or an
    exception to raise; each request consumes one. `calls` records every request as a dict of its method, URL and kwargs.
    """
    mirisIntegration._invalidate_api_key_cache()
    monkeypatch.setattr(mirisIntegration, "MIRIS_API_BASE_URL", BASE_URL)
    monkeypatch.setattr(mirisIntegration, "MIRIS_API_KEY_SECRET_ARN", SECRET_ARN)

    secretsmanager = MagicMock()
    secretsmanager.get_secret_value.return_value = {"SecretString": INTEGRATION_KEY}
    monkeypatch.setattr(mirisIntegration, "_get_secretsmanager_client", lambda: secretsmanager)

    calls = []
    responses = []

    def fake_request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        outcome = responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, tuple):
            return miris_response(*outcome)
        return miris_response(outcome)

    monkeypatch.setattr(mirisIntegration, "_get_session", lambda: SimpleNamespace(request=fake_request))
    yield SimpleNamespace(
        integration_key=INTEGRATION_KEY,
        secret_arn=SECRET_ARN,
        base_url=BASE_URL,
        secretsmanager=secretsmanager,
        calls=calls,
        responses=responses,
    )
    mirisIntegration._invalidate_api_key_cache()


class FakeLambdaContext:
    """A Lambda context reporting a fixed remaining time."""

    def __init__(self, remaining_ms=30000):
        self.remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self):
        return self.remaining_ms


class LogRecorder:
    """Stands in for a module logger and records every line logged through any level."""

    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        def record(*args, **kwargs):
            self.lines.append(f"{level}: " + " ".join(str(a) for a in args) + f" {kwargs}")

        return record

    def text(self):
        return "\n".join(self.lines)


@pytest.fixture
def log_recorder(monkeypatch):
    """Replace the client's and the Miris handlers' loggers with one recorder. The root conftest's
    logger stand-in discards every line, so neither it nor caplog would show a leaked secret."""
    recorder = LogRecorder()
    for module in (mirisIntegration, viewerKeyCleanup, mintViewerKey, getAssetStatus):
        monkeypatch.setattr(module, "logger", recorder)
    return recorder
