# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""``request_to_claims`` reports how the caller authenticated, and with which key.

The REST authorizer marks an API-key principal with ``vams:authMethod = apiKey`` and the key's id in
``vams:apiKeyId``, and marks a JWT principal with nothing; a Lambda cross-call carries no authorizer at
all. Handlers that mint or seed a credential refuse the ``apiKey`` method so one key cannot be chained
into another, which is only safe if the default is the least-privileged reading: anything that is not
the API-key literal is ``jwt``. The key id lets a handler decide by the key itself; it is None for every
caller that is not an API-key principal.
"""

import json
import os
from unittest.mock import patch

import pytest

from backend.backend.handlers import auth as auth_module
from backend.backend.handlers.auth import request_to_claims

_AUTHORIZER_CORE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "common", "auth", "authorizerCore.py")


def _rest_event(**context):
    authorizer = {"vams:tokens": json.dumps(["user-1"]), "vams:roles": json.dumps(["basic"])}
    authorizer.update(context)
    return {"requestContext": {"authorizer": authorizer}}


@pytest.mark.unit
class TestAuthMethod:
    def test_a_jwt_caller_defaults_to_jwt(self):
        assert request_to_claims(_rest_event())["authMethod"] == "jwt"

    def test_an_api_key_caller_is_reported(self):
        out = request_to_claims(_rest_event(**{"vams:authMethod": "apiKey", "vams:apiKeyId": "k-1"}))
        assert out["authMethod"] == "apiKey"
        assert out["tokens"] == ["user-1"]

    def test_an_api_key_caller_carries_the_id_of_its_key(self):
        out = request_to_claims(_rest_event(**{"vams:authMethod": "apiKey", "vams:apiKeyId": "k-1"}))
        assert out["apiKeyId"] == "k-1"

    def test_a_jwt_caller_carries_no_key_id(self):
        assert request_to_claims(_rest_event())["apiKeyId"] is None

    def test_a_blank_key_id_reads_as_none(self):
        out = request_to_claims(_rest_event(**{"vams:authMethod": "apiKey", "vams:apiKeyId": ""}))
        assert out["apiKeyId"] is None

    def test_the_authorizer_emits_the_key_id_this_reads(self):
        with open(os.path.abspath(_AUTHORIZER_CORE), "r", encoding="utf-8") as handle:
            source = handle.read()
        assert "'vams:apiKeyId': api_key_record.get('apiKeyId', '')" in source

    def test_the_http_lambda_authorizer_shape_is_read_too(self):
        event = {"requestContext": {"authorizer": {"lambda": {
            "vams:tokens": json.dumps(["user-2"]), "vams:authMethod": "apiKey"}}}}
        assert request_to_claims(event)["authMethod"] == "apiKey"

    def test_the_jwt_claims_shape_defaults_to_jwt(self):
        event = {"requestContext": {"authorizer": {"jwt": {"claims": {"cognito:username": "user-3"}}}}}
        assert request_to_claims(event)["authMethod"] == "jwt"

    def test_an_unknown_value_falls_back_to_jwt(self):
        # Only the literal the authorizer emits for API keys means apiKey.
        assert request_to_claims(_rest_event(**{"vams:authMethod": "saml"}))["authMethod"] == "jwt"

    def test_a_cross_call_reads_as_jwt(self):
        # A Lambda cross-call has no authorizer and is never an API-key principal; it takes the
        # default, so the apiKey deny in the mint path does not refuse system-initiated work.
        out = request_to_claims({"lambdaCrossCall": {"userName": "alice", "mfaEnabled": False}})
        assert out["authMethod"] == "jwt"
        assert out["tokens"] == ["alice"]
        assert out["mfaEnabled"] is False
        assert out["apiKeyId"] is None

    def test_an_event_without_an_authorizer_defaults_to_jwt(self):
        out = request_to_claims({"body": "x"})
        assert out == {"tokens": [], "roles": [], "externalAttributes": [], "mfaEnabled": False,
                       "authMethod": "jwt", "apiKeyId": None}

    def test_the_method_reaches_the_custom_claims_hook(self):
        with patch.object(auth_module, "customAuthClaimsCheckOverride",
                          side_effect=lambda claims, event: claims) as hook:
            request_to_claims(_rest_event(**{"vams:authMethod": "apiKey"}))
        assert hook.call_args[0][0]["authMethod"] == "apiKey"

    def test_the_constants_are_the_two_methods(self):
        assert (auth_module.AUTH_METHOD_JWT, auth_module.AUTH_METHOD_API_KEY) == ("jwt", "apiKey")
        assert not [name for name in vars(auth_module) if name.startswith("AUTH_METHOD_")
                    and name not in ("AUTH_METHOD_JWT", "AUTH_METHOD_API_KEY")]

    def test_the_authorizer_emits_the_literal_this_reads(self):
        # The REST authorizer writes this exact context entry for an API-key principal; the reader
        # compares against the same literal.
        with open(os.path.abspath(_AUTHORIZER_CORE), "r", encoding="utf-8") as handle:
            source = handle.read()
        assert "'vams:authMethod': 'apiKey'" in source
        assert auth_module.AUTH_METHOD_API_KEY == "apiKey"
