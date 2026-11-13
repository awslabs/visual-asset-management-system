# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`GET /database/{databaseId}` and the database listing expose the database's compliance schema
binding -- `complianceSchemaName` and `complianceAutoEval`, the attributes the compliance binding
service writes on the database row -- and read them as null when the database has no binding, so a
client can tell "unbound" from "auto-evaluation off" without a second call."""

from unittest.mock import MagicMock

import pytest
from boto3.dynamodb.types import TypeSerializer

from backend.backend.handlers.databases import databaseService as svc

# The model as the handler holds it: the harness registers the source tree under two module
# names, so the class imported through `backend.backend.models` is a distinct object.
GetDatabaseResponseModel = svc.GetDatabaseResponseModel

DATABASE_ID = "factory-db"
SCHEMA = "engineering-asset-standard"
AUTHENTICATED = {"tokens": ["some-user"], "roles": ["admin"], "mfaEnabled": False}


def _row(**binding):
    return {"databaseId": DATABASE_ID, "description": "a database", "dateCreated": "2026-01-01",
            **binding}


class _AllowingEnforcer:
    def __init__(self, claims_and_roles):
        pass

    def enforce(self, obj, act):
        return True


@pytest.fixture
def allow(monkeypatch):
    monkeypatch.setattr(svc, "CasbinEnforcer", _AllowingEnforcer)


@pytest.fixture
def tables(monkeypatch):
    """The database row `get_database` reads (through the resource) and the page the listing scans
    (through the low-level client)."""
    table = MagicMock(name="database_table")
    resource = MagicMock(name="dynamodb")
    resource.Table.return_value = table
    monkeypatch.setattr(svc, "dynamodb", resource)
    client = MagicMock(name="dbClient")
    monkeypatch.setattr(svc, "dbClient", client)
    return table, client


def _serialize(row):
    serializer = TypeSerializer()
    return {k: serializer.serialize(v) for k, v in row.items()}


@pytest.mark.unit
class TestGetDatabaseComplianceBinding:

    def test_a_bound_database_reports_its_schema_and_auto_evaluation_flag(self, allow, tables):
        table, _ = tables
        table.get_item.return_value = {
            "Item": _row(complianceSchemaName=SCHEMA, complianceAutoEval=False)}
        result = svc.get_database(DATABASE_ID, claims_and_roles=AUTHENTICATED)
        assert isinstance(result, GetDatabaseResponseModel)
        assert result.complianceSchemaName == SCHEMA
        assert result.complianceAutoEval is False
        body = result.dict()
        assert body["complianceSchemaName"] == SCHEMA
        assert body["complianceAutoEval"] is False

    def test_an_unbound_database_reports_both_fields_as_null(self, allow, tables):
        table, _ = tables
        table.get_item.return_value = {"Item": _row()}
        body = svc.get_database(DATABASE_ID, claims_and_roles=AUTHENTICATED).dict()
        assert "complianceSchemaName" in body and body["complianceSchemaName"] is None
        assert "complianceAutoEval" in body and body["complianceAutoEval"] is None

    def test_the_listing_rows_carry_the_binding_too(self, allow, tables):
        _, client = tables
        client.scan.return_value = {"Items": [
            _serialize(_row(complianceSchemaName=SCHEMA, complianceAutoEval=True)),
            _serialize(dict(_row(), databaseId="other-db")),
        ]}
        result = svc.get_databases({}, claims_and_roles=AUTHENTICATED)
        by_id = {item.databaseId: item for item in result.Items}
        assert by_id[DATABASE_ID].complianceSchemaName == SCHEMA
        assert by_id[DATABASE_ID].complianceAutoEval is True
        assert by_id["other-db"].complianceSchemaName is None
        assert by_id["other-db"].complianceAutoEval is None


@pytest.mark.unit
class TestTheModelDeclaresTheFields:

    def test_the_fields_are_optional_and_default_to_null(self):
        model = GetDatabaseResponseModel(databaseId=DATABASE_ID, description="d")
        assert model.complianceSchemaName is None
        assert model.complianceAutoEval is None
        assert GetDatabaseResponseModel.__fields__["complianceSchemaName"].required is False
        assert GetDatabaseResponseModel.__fields__["complianceAutoEval"].required is False
