# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A database create on an unknown bucket tells the caller the bucket is the problem.

`create_database` raises `VAMSGeneralErrorResponse("Bucket ID not found")` inside its `try` block,
and the trailing `except Exception` used to replace it with "Error creating database", so a caller
who sent an unknown or retired `defaultBucketId` could not tell what to fix (#1906).
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.databases import createDatabase as handler
from models.databases import CreateDatabaseRequestModel


BODY = {
    "databaseId": "factory-db",
    "description": "a database on a bucket that is not registered",
    "defaultBucketId": "550e8400-e29b-41d4-a716-446655440000",
}
REQUEST = CreateDatabaseRequestModel(**BODY)


@pytest.fixture
def tables(monkeypatch):
    """The database and bucket tables; the bucket query answers no registration by default."""
    database_table = MagicMock()
    buckets_table = MagicMock()
    buckets_table.query.return_value = {"Items": []}

    resource = MagicMock()

    def _table(name):
        if name == handler.db_database:
            return database_table
        if name == handler.s3_asset_buckets_table:
            return buckets_table
        raise AssertionError(f"unexpected table {name}")

    resource.Table.side_effect = _table
    monkeypatch.setattr(handler, "dynamodb", resource)
    return {"database": database_table, "buckets": buckets_table}


@pytest.fixture
def authorized(monkeypatch):
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.return_value = True
    monkeypatch.setattr(handler, "CasbinEnforcer", MagicMock(return_value=enforcer))
    monkeypatch.setattr(handler, "request_to_claims", lambda event: {"tokens": ["tester"], "roles": []})


def _event():
    return {
        "requestContext": {"http": {"method": "POST", "path": "/database"}},
        "body": json.dumps(BODY),
    }


@pytest.mark.unit
class TestCreateDatabaseNamesTheBucket:
    def test_unknown_bucket_keeps_its_reason(self, tables):
        with pytest.raises(handler.VAMSGeneralErrorResponse) as raised:
            handler.create_database(REQUEST)

        assert raised.value.user_message == "Bucket ID not found"
        tables["database"].put_item.assert_not_called()

    def test_retired_bucket_keeps_its_reason(self, tables, monkeypatch):
        tables["buckets"].query.return_value = {"Items": [{"bucketId": REQUEST.defaultBucketId}]}
        monkeypatch.setattr(handler, "is_retired", lambda item: True)

        with pytest.raises(handler.VAMSGeneralErrorResponse) as raised:
            handler.create_database(REQUEST)

        assert raised.value.user_message == "Bucket ID not found"
        tables["database"].put_item.assert_not_called()

    def test_the_api_answers_400_with_the_reason(self, tables, authorized):
        response = handler.lambda_handler(_event(), None)

        assert response["statusCode"] == 400
        assert "Bucket ID not found" in json.loads(response["body"])["message"]
        assert "Error creating database" not in response["body"]

    def test_an_unexpected_error_stays_generic(self, tables):
        """CONTROL: an error that is not the handler's own refusal is still not passed on."""
        tables["buckets"].query.side_effect = RuntimeError("table arn:aws:dynamodb:... unreachable")

        with pytest.raises(handler.VAMSGeneralErrorResponse) as raised:
            handler.create_database(REQUEST)

        assert raised.value.user_message == "Error creating database"
