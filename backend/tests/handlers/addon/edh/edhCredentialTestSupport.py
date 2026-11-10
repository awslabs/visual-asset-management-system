# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The two services behind a session credential, faked for tests that run the real edhSessionBootstrap
module: apiKeyService as its cross-call reaches it (key records by id, a name unique deployment-wide, a
delete of an unknown id answered as not found) and Secrets Manager (one value per secret name, every
value ever written kept in order). Tests read from them which key values a secret was ever given and
which key records were deleted, which is what the session-key invariant is stated over."""

import io
import json
from datetime import datetime, timezone

from botocore.exceptions import ClientError

SECRET_ARN_PREFIX = "arn:aws:secretsmanager:us-east-1:123456789012:secret:"


def _answer(status_code, body):
    return {"Payload": io.BytesIO(json.dumps({"statusCode": status_code, "body": json.dumps(body)}).encode("utf-8"))}


def _client_error(code, operation):
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeApiKeyService:
    """apiKeyService behind ``lambda_client.invoke``: POST creates a key under a name no record holds, dated
    the instant it is created, DELETE removes a record, GET lists them all. ``refuse_create`` answers every
    POST, and ``refuse_listing`` every GET, with the given (status, body); ``after_create`` runs once a key
    has been created, with its id."""

    def __init__(self):
        self.records = {}
        self.plaintext = {}
        self.created = []
        self.deleted = []
        self.refuse_create = None
        self.refuse_listing = None
        self.after_create = None

    def seed(self, api_key_id, name, *, user_id, expires_at, created_at, key_value=None):
        self.records[api_key_id] = {"apiKeyId": api_key_id, "apiKeyName": name, "userId": user_id,
                                    "expiresAt": expires_at, "createdAt": created_at, "isActive": "true"}
        self.plaintext[api_key_id] = key_value or f"vams_{api_key_id}"

    def key_id_of(self, key_value):
        """The id of the record whose plaintext is ``key_value``, or None."""
        return next((key_id for key_id, value in self.plaintext.items() if value == key_value), None)

    def invoke(self, **kwargs):
        event = json.loads(kwargs["Payload"].decode("utf-8"))
        method = event["requestContext"]["http"]["method"]
        if method == "POST":
            return self._create(json.loads(event["body"]))
        if method == "DELETE":
            api_key_id = event["pathParameters"]["apiKeyId"]
            if api_key_id not in self.records:
                return _answer(400, {"message": "API key not found"})
            del self.records[api_key_id]
            self.deleted.append(api_key_id)
            return _answer(200, {"message": "API key deleted"})
        if self.refuse_listing is not None:
            return _answer(*self.refuse_listing)
        return _answer(200, {"Items": [dict(record) for record in self.records.values()]})

    def _create(self, body):
        if self.refuse_create is not None:
            return _answer(*self.refuse_create)
        if any(record["apiKeyName"] == body["apiKeyName"] for record in self.records.values()):
            return _answer(400, {"message": f"API key name '{body['apiKeyName']}' already exists"})
        api_key_id = f"key-minted-{len(self.created) + 1}"
        self.seed(api_key_id, body["apiKeyName"], user_id=body["userId"], expires_at=body["expiresAt"],
                  created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.created.append(api_key_id)
        if self.after_create is not None:
            self.after_create(api_key_id)
        return _answer(200, {**self.records[api_key_id], "apiKey": self.plaintext[api_key_id]})


class FakeSecretsManager:
    """Secrets Manager as the session bootstrap uses it. ``fail_writes`` makes every value write raise
    the given error code; ``history`` is every (name, value) seeded or written, in order. A deleted
    secret takes its resource policy with it."""

    def __init__(self):
        self.values = {}
        self.policies = {}
        self.history = []
        self.fail_writes = None

    def seed(self, name, value):
        self._write(name, value)

    def create_secret(self, **kwargs):
        name = kwargs["Name"]
        if self.fail_writes is not None:
            raise _client_error(self.fail_writes, "CreateSecret")
        if name in self.values:
            raise _client_error("ResourceExistsException", "CreateSecret")
        self._write(name, kwargs["SecretString"])
        return {"ARN": f"{SECRET_ARN_PREFIX}{name}-AbCdEf", "Name": name}

    def put_secret_value(self, **kwargs):
        name = kwargs["SecretId"]
        if self.fail_writes is not None:
            raise _client_error(self.fail_writes, "PutSecretValue")
        if name not in self.values:
            raise _client_error("ResourceNotFoundException", "PutSecretValue")
        self._write(name, kwargs["SecretString"])
        return {"ARN": f"{SECRET_ARN_PREFIX}{name}-AbCdEf", "Name": name}

    def delete_secret(self, **kwargs):
        if self.values.pop(kwargs["SecretId"], None) is None:
            raise _client_error("ResourceNotFoundException", "DeleteSecret")
        self.policies.pop(kwargs["SecretId"], None)
        return {}

    def put_resource_policy(self, **kwargs):
        self.policies[kwargs["SecretId"]] = json.loads(kwargs["ResourcePolicy"])
        return {}

    def _write(self, name, value):
        self.values[name] = value
        self.history.append((name, value))

    def key_values_written(self, name):
        """Every API key value ``name`` was given, in order; the revoked marker is not a key."""
        out = []
        for written_name, value in self.history:
            if written_name == name:
                api_key = json.loads(value).get("apiKey")
                if api_key:
                    out.append(api_key)
        return out

    def key_value_held(self, name):
        """The API key value the secret holds now, or None."""
        value = self.values.get(name)
        return json.loads(value).get("apiKey") if value else None
