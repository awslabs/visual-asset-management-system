# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""EDH mints each desktop's session key under the core temporary session key prefix.

``edhSessionBootstrap.SESSION_KEY_NAME_PREFIX`` is ``TEMPORARY_SESSION_KEY_NAME_PREFIX`` followed by the
add-on's ``edh-``, so apiKeyService refuses key create and update to a desktop that authenticates with
its session key, and reserves the names for the Lambda cross-call the bootstrap mints through. These
tests send the names the bootstrap produces through the real apiKeyService handler and the real claims
reader.
"""

import io
import json
import os
import re
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("API_KEY_STORAGE_TABLE_NAME", "test-api-key-table")
os.environ.setdefault("USER_ROLES_STORAGE_TABLE_NAME", "test-user-roles-table")

from backend.backend.common.constants import TEMPORARY_SESSION_KEY_NAME_PREFIX
from backend.backend.common.validators import object_name_pattern
from backend.backend.handlers import auth as auth_module
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.auth import apiKeyService
from backend.backend.models.apiKeys import CreateApiKeyRequestModel

USER = "user@example.com"
BOOTSTRAP_ID = "5d1c8a2e-0b1f-4a6c-9d3e-7f8a9b0c1d2e"
REISSUED_AT = datetime(2026, 9, 28, 17, 5, 9, tzinfo=timezone.utc)
LAUNCH_KEY_NAME = bootstrap.session_key_name(BOOTSTRAP_ID)
REISSUED_KEY_NAME = bootstrap.session_key_name(BOOTSTRAP_ID, rotated_at=REISSUED_AT)
SESSION_KEY_NAMES = [LAUNCH_KEY_NAME, REISSUED_KEY_NAME]
SESSION_KEY_NAME_IDS = ["launch or start", "re-issue"]
SESSION_KEY_ID = "11111111-1111-4111-8111-111111111111"
TARGET_KEY_ID = "33333333-3333-4333-8333-333333333333"
EVENT = {
    "requestContext": {"http": {"method": "POST", "path": "/addon/edh/workstations"}},
    "pathParameters": None,
    "queryStringParameters": None,
    "headers": {"authorization": "Bearer test-token"},
}
REFUSAL = "API keys cannot be created or updated with a temporary session key"
RESERVED_NAME_REFUSAL = ("API key names starting with the temporary session key prefix are reserved. "
                         "Please choose a different name.")
# The key routes a desktop holding its session key is refused: (method, path, body).
KEY_WRITES = [
    ("POST", "/auth/api-keys", {"apiKeyName": "forever", "userId": USER, "description": "x"}),
    ("POST", "/auth/user/api-keys", {"apiKeyName": "mine", "description": "x", "expiresAt": "2027-01-01T00:00:00Z"}),
    ("PUT", f"/auth/api-keys/{TARGET_KEY_ID}", {"description": "changed"}),
    ("PUT", f"/auth/user/api-keys/{TARGET_KEY_ID}", {"description": "changed"}),
]
KEY_WRITE_IDS = ["admin create", "user create", "admin update", "user update"]


def _record(api_key_id, name):
    return {"apiKeyId": api_key_id, "apiKeyName": name, "apiKeyHash": "h", "description": "", "userId": USER,
            "createdBy": USER, "createdAt": "2026-09-08T10:00:00+00:00", "updatedAt": "2026-09-08T10:00:00+00:00",
            "expiresAt": "2026-09-08T20:00:00Z", "isActive": "true"}


def _real_service(monkeypatch, records):
    """apiKeyService as deployed, reading claims through the real claims reader, with its tables, Casbin
    and the audit writer doubled. Returns the key table and the audit double."""
    table = MagicMock()
    table.get_item.side_effect = lambda **kwargs: (
        {"Item": dict(records[kwargs["Key"]["apiKeyId"]])} if kwargs["Key"]["apiKeyId"] in records else {})
    table.scan.return_value = {"Items": []}
    table.query.return_value = {"Items": []}
    roles = MagicMock()
    roles.query.return_value = {"Items": [{"userId": USER, "roleName": "workstation-user"}]}
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    audit = MagicMock()
    # The suite stands a mock in for handlers.auth at import; the handler gets the real claims reader
    # and the constant it writes.
    monkeypatch.setattr(apiKeyService, "request_to_claims", auth_module.request_to_claims)
    monkeypatch.setattr(apiKeyService, "AUTH_METHOD_API_KEY", auth_module.AUTH_METHOD_API_KEY)
    monkeypatch.setattr(apiKeyService, "CasbinEnforcer", MagicMock(return_value=enforcer))
    monkeypatch.setattr(apiKeyService, "log_auth_changes", audit)
    monkeypatch.setattr(apiKeyService, "api_key_table", table)
    monkeypatch.setattr(apiKeyService, "user_roles_table", roles)
    return table, audit


def _gateway_event(method, path, body, **authorizer_context):
    """A request as API Gateway hands it to the handler, with the authorizer's context."""
    authorizer = {"vams:tokens": json.dumps([USER]), "vams:roles": json.dumps(["workstation-user"])}
    authorizer.update(authorizer_context)
    event = {"requestContext": {"http": {"method": method, "path": path}, "authorizer": authorizer},
             "headers": {"authorization": "Bearer vams_session"}, "body": json.dumps(body)}
    if "/api-keys/" in path:
        event["pathParameters"] = {"apiKeyId": path.rsplit("/", 1)[1]}
    return event


def _invoke_the_real_service(**kwargs):
    """A lambda_client.invoke double that hands the bootstrap's cross-call payload to apiKeyService."""
    response = apiKeyService.lambda_handler(json.loads(kwargs["Payload"].decode("utf-8")), {})
    return {"Payload": io.BytesIO(json.dumps(response).encode("utf-8"))}


def _message(response):
    return json.loads(response["body"])["message"]


@pytest.mark.unit
class TestTheEdhSessionKeyPrefix:
    def test_the_prefix_is_the_core_temporary_session_prefix_and_the_add_ons_own(self):
        assert bootstrap.SESSION_KEY_NAME_PREFIX == TEMPORARY_SESSION_KEY_NAME_PREFIX + "edh-"
        assert bootstrap.SESSION_KEY_NAME_PREFIX == "temporary-session-edh-"

    @pytest.mark.parametrize("name", SESSION_KEY_NAMES, ids=SESSION_KEY_NAME_IDS)
    def test_every_name_the_bootstrap_mints_carries_the_core_prefix(self, name):
        assert name.startswith(TEMPORARY_SESSION_KEY_NAME_PREFIX)
        assert bootstrap.bootstrap_id_from_key_name(name) == BOOTSTRAP_ID

    def test_the_longest_name_is_one_the_create_route_accepts(self):
        # A re-issue's name is the longest the bootstrap mints: the prefix, a UUID and a 16-character stamp.
        assert len(REISSUED_KEY_NAME) == len(bootstrap.SESSION_KEY_NAME_PREFIX) + 36 + 16 <= 256
        assert re.fullmatch(object_name_pattern, REISSUED_KEY_NAME)
        request = CreateApiKeyRequestModel(apiKeyName=REISSUED_KEY_NAME, userId=USER, description="x")
        assert request.apiKeyName == REISSUED_KEY_NAME


@pytest.mark.unit
class TestTheRealApiKeyServiceHoldsTheEdhSessionKey:
    @pytest.mark.parametrize("name", SESSION_KEY_NAMES, ids=SESSION_KEY_NAME_IDS)
    def test_the_bootstrap_cross_call_creates_the_key(self, monkeypatch, name):
        table, _audit = _real_service(monkeypatch, {})
        client = MagicMock()
        client.invoke.side_effect = _invoke_the_real_service
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            minted = bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False, key_name=name)
        assert minted and minted["apiKeyId"] and minted["apiKey"].startswith("vams_")
        item = table.put_item.call_args.kwargs["Item"]
        assert (item["apiKeyName"], item["userId"]) == (name, USER)

    @pytest.mark.parametrize("name", SESSION_KEY_NAMES, ids=SESSION_KEY_NAME_IDS)
    @pytest.mark.parametrize("method,path,body", KEY_WRITES, ids=KEY_WRITE_IDS)
    def test_a_desktop_holding_its_session_key_cannot_create_or_update_a_key(self, monkeypatch, name, method,
                                                                             path, body):
        records = {SESSION_KEY_ID: _record(SESSION_KEY_ID, name), TARGET_KEY_ID: _record(TARGET_KEY_ID, "ci")}
        table, audit = _real_service(monkeypatch, records)
        event = _gateway_event(method, path, body, **{"vams:authMethod": "apiKey", "vams:apiKeyId": SESSION_KEY_ID})
        response = apiKeyService.lambda_handler(event, {})
        assert response["statusCode"] == 403
        assert _message(response) == REFUSAL
        table.put_item.assert_not_called()
        table.update_item.assert_not_called()
        audit.assert_not_called()

    def test_an_api_key_that_is_not_a_session_key_is_not_refused(self, monkeypatch):
        # Control for the refusals above: the same request from an ordinary key creates the key.
        records = {SESSION_KEY_ID: _record(SESSION_KEY_ID, "ci-deploy")}
        table, _audit = _real_service(monkeypatch, records)
        method, path, body = KEY_WRITES[0]
        event = _gateway_event(method, path, body, **{"vams:authMethod": "apiKey", "vams:apiKeyId": SESSION_KEY_ID})
        response = apiKeyService.lambda_handler(event, {})
        assert response["statusCode"] == 200
        table.put_item.assert_called_once()

    @pytest.mark.parametrize("name", SESSION_KEY_NAMES, ids=SESSION_KEY_NAME_IDS)
    def test_a_signed_in_user_cannot_create_a_key_under_a_session_key_name(self, monkeypatch, name):
        table, _audit = _real_service(monkeypatch, {})
        event = _gateway_event("POST", "/auth/user/api-keys",
                               {"apiKeyName": name, "description": "x", "expiresAt": "2027-01-01T00:00:00Z"})
        response = apiKeyService.lambda_handler(event, {})
        assert response["statusCode"] == 400
        assert _message(response) == RESERVED_NAME_REFUSAL
        table.put_item.assert_not_called()
