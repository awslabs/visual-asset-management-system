# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A bound schema's scope is not narrowed past its bindings.

A schema bound in a database (the database binding, or an asset override there) used to move to
another database's scope through PUT, or through POST re-registering its name. The databases left
outside then held a binding the bind rule refuses: their administrators could neither read nor
change it. Now a move to a database scope is refused with 400 while the schema is bound in any
other database; moving it to GLOBAL widens it and stays allowed, and so does a move into the one
database it is bound in. A deleted database's records are history and do not hold it.

Run against moto tables built from the CDK definitions, through the schema service's handler.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    RULES_SCHEMA_BODY, USER, body_of, claims_for, enforcer, rest_event, schema_row,
)
from handlers.compliance import complianceSchemaService as svc

SCHEMA = "moving-schema"
DB_A = "scope-a"
DB_B = "scope-b"
REFUSED = ("The schema is bound in a database the new scope does not include. Remove those "
           "bindings first, or make the schema available to every database (GLOBAL).")


@pytest.fixture
def tables():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, (svc,))
        for active in patches:
            active.start()
        try:
            for database_id in (DB_A, DB_B):
                doubles["databaseStorage"].put_item(Item={"databaseId": database_id})
            yield doubles
        finally:
            for active in reversed(patches):
                active.stop()


def _schema(tables, database_id):
    tables["complianceSchemaStorage"].put_item(Item={
        **schema_row(SCHEMA, database_id=database_id), svc.LATEST_VERSION_ATTRIBUTE:
            svc.LATEST_VERSION_MARKER})


def _bind_database(tables, database_id):
    tables["databaseStorage"].put_item(Item={"databaseId": database_id,
                                             "complianceSchemaName": SCHEMA})
    _record(tables, database_id, "a1", "database")


def _record(tables, database_id, asset_id, source):
    tables["complianceAssetStateStorage"].put_item(Item={
        "databaseId": database_id, "assetId": asset_id, "schemaName": SCHEMA,
        "schemaSource": source, "complianceState": "compliant"})


def _call(method, path, params=None, body=None):
    with patch.object(svc, "request_to_claims", claims_for(USER)), \
            patch.object(svc, "CasbinEnforcer", return_value=enforcer()), \
            patch.object(svc, "may_run_workflow", return_value=True):
        response = svc.lambda_handler(rest_event(method, path, params or {}, body=body),
                                      MagicMock())
    return response["statusCode"], body_of(response)


def _move(scope):
    return _call("PUT", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA},
                 body={"databaseId": scope})


def _re_register(scope):
    return _call("POST", "/compliance/schemas",
                 body={"schemaName": SCHEMA, "databaseId": scope, "schemaBody": RULES_SCHEMA_BODY})


def _versions(tables):
    return sorted(int(row["internalVersion"]) for row in
                  tables["complianceSchemaStorage"].scan()["Items"]
                  if row.get("schemaName") == SCHEMA)


@pytest.mark.unit
class TestAMoveIsRefusedWhileTheSchemaIsBoundOutsideTheNewScope:

    @pytest.mark.parametrize("move", [_move, _re_register], ids=["PUT", "POST"])
    def test_a_global_schema_bound_in_another_database_does_not_move_into_one(self, tables, move):
        _schema(tables, "GLOBAL")
        _bind_database(tables, DB_B)
        status, body = move(DB_A)
        assert (status, body["message"]) == (400, REFUSED)
        assert _versions(tables) == [1]

    def test_an_asset_override_in_another_database_holds_it_too(self, tables):
        _schema(tables, "GLOBAL")
        _record(tables, DB_B, "override", "asset")
        status, body = _move(DB_A)
        assert (status, body["message"]) == (400, REFUSED)

    def test_a_database_schema_bound_in_its_database_does_not_move_to_another(self, tables):
        _schema(tables, DB_A)
        _bind_database(tables, DB_A)
        status, body = _move(DB_B)
        assert (status, body["message"]) == (400, REFUSED)

    def test_the_refusal_names_no_database(self, tables):
        _schema(tables, "GLOBAL")
        _bind_database(tables, DB_B)
        _, body = _move(DB_A)
        assert DB_B not in json.dumps(body)


@pytest.mark.unit
class TestTheMovesTheBindingsAllow:

    def test_moving_into_the_one_database_it_is_bound_in(self, tables):
        _schema(tables, "GLOBAL")
        _bind_database(tables, DB_A)
        status, body = _move(DB_A)
        assert status == 200, body
        assert _versions(tables) == [1, 2]

    @pytest.mark.parametrize("move", [_move, _re_register], ids=["PUT", "POST"])
    def test_widening_to_global_whatever_the_bindings(self, tables, move):
        _schema(tables, DB_A)
        _bind_database(tables, DB_A)
        _record(tables, DB_A, "override", "asset")
        status, body = move("GLOBAL")
        assert status == 200, body

    def test_an_unbound_schema_moves_freely(self, tables):
        _schema(tables, "GLOBAL")
        status, body = _move(DB_A)
        assert status == 200, body

    def test_a_deleted_databases_records_do_not_hold_it(self, tables):
        _schema(tables, "GLOBAL")
        _record(tables, f"{DB_B}#deleted", "a1", "database")
        status, body = _move(DB_A)
        assert status == 200, body

    def test_keeping_the_scope_reads_no_binding(self, tables):
        _schema(tables, DB_A)
        _bind_database(tables, DB_B)
        with patch.object(svc, "_bound_outside", side_effect=AssertionError("read")) as bound:
            status, body = _call("PUT", f"/compliance/schemas/{SCHEMA}", {"schemaName": SCHEMA},
                                 body={"description": "same scope"})
        assert status == 200, body
        bound.assert_not_called()
