# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.tagTypes import createTagTypes
from backend.backend.handlers.tagTypes import tagTypeService
from backend.backend.handlers.tagTypes.createTagTypes import create_tag_type, update_tag_type
from backend.backend.handlers.tagTypes.tagTypeService import get_tag_types as list_tag_types
from backend.backend.handlers.tagTypes.tagTypeService import delete_tag_type
from backend.backend.models.tag import CreateTagTypeRequestModel, UpdateTagTypeRequestModel

# Reference the exception through the module under test so the asserted class is
# the exact object raised (distinct module objects load from the same file).
VAMSGeneralErrorResponse = createTagTypes.VAMSGeneralErrorResponse
# tagTypeService loads its own module object; reference its class for delete tests.
TagTypeServiceError = tagTypeService.VAMSGeneralErrorResponse

CLAIMS = {"tokens": ["u"]}


def _enf(allow=True):
    inst = MagicMock(); inst.enforce.return_value = allow
    return inst


@pytest.mark.unit
@patch('backend.backend.handlers.tagTypes.createTagTypes.database_table')
@patch('backend.backend.handlers.tagTypes.createTagTypes.tag_type_table')
@patch('backend.backend.handlers.tagTypes.createTagTypes.CasbinEnforcer')
class TestCreateTagTypeScope:
    def test_scoped_tagtype_stored_and_db_verified(self, casbin, tag_type_table, database_table):
        casbin.return_value = _enf(True)
        tag_type_table.get_item.return_value = {}  # not existing
        database_table.get_item.return_value = {'Item': {'databaseId': 'factory-db'}}
        model = CreateTagTypeRequestModel(tagTypeName="Custom", description="d", databaseId="factory-db")
        create_tag_type(model, CLAIMS)
        stored = tag_type_table.put_item.call_args.kwargs['Item']
        assert stored['databaseId'] == 'factory-db'

    def test_nonexistent_db_rejected(self, casbin, tag_type_table, database_table):
        casbin.return_value = _enf(True)
        tag_type_table.get_item.return_value = {}
        database_table.get_item.return_value = {}  # missing
        model = CreateTagTypeRequestModel(tagTypeName="Custom", description="d", databaseId="factory-db")
        with pytest.raises(VAMSGeneralErrorResponse):
            create_tag_type(model, CLAIMS)

    def _wire(self, tag_type_table, database_table, rows=None, db_exists=True):
        """rows: dict keyed by (scope, tagTypeName)."""
        rows = rows or {}

        def tt_get(Key):
            row = rows.get((Key['databaseId'], Key['tagTypeName']))
            return {'Item': row} if row is not None else {}

        def tt_query(IndexName=None, KeyConditionExpression=None):
            name = KeyConditionExpression._values[1]
            items = [r for (scope, n), r in rows.items() if n == name]
            return {'Items': items}

        tag_type_table.get_item.side_effect = tt_get
        tag_type_table.query.side_effect = tt_query
        database_table.get_item.return_value = {'Item': {'databaseId': 'factory-db'}} if db_exists else {}

    def test_same_name_allowed_in_two_databases(self, casbin, tag_type_table, database_table):
        casbin.return_value = _enf(True)
        # 'Custom' exists in hospital-db; creating it in factory-db must be allowed
        self._wire(tag_type_table, database_table,
                   rows={('hospital-db', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'hospital-db'}})
        model = CreateTagTypeRequestModel(tagTypeName="Custom", description="d", databaseId="factory-db")
        create_tag_type(model, CLAIMS)
        stored = tag_type_table.put_item.call_args.kwargs['Item']
        assert stored['databaseId'] == 'factory-db'

    def test_db_tagtype_rejected_when_global_exists(self, casbin, tag_type_table, database_table):
        casbin.return_value = _enf(True)
        self._wire(tag_type_table, database_table,
                   rows={('GLOBAL', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'GLOBAL'}})
        model = CreateTagTypeRequestModel(tagTypeName="Custom", description="d", databaseId="factory-db")
        with pytest.raises(VAMSGeneralErrorResponse):
            create_tag_type(model, CLAIMS)
        tag_type_table.put_item.assert_not_called()

    def test_global_tagtype_allowed_when_db_uses_name_but_warns(self, casbin, tag_type_table,
                                                                database_table):
        casbin.return_value = _enf(True)
        # Asymmetric rule: GLOBAL may be created over a database-specific name (with an advisory), but
        # not the reverse — a database may not shadow the shared vocabulary.
        self._wire(tag_type_table, database_table,
                   rows={('factory-db', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'factory-db'}})
        model = CreateTagTypeRequestModel(tagTypeName="Custom", description="d")  # global
        response = create_tag_type(model, CLAIMS)

        tag_type_table.put_item.assert_called_once()
        assert tag_type_table.put_item.call_args.kwargs['Item']['databaseId'] == 'GLOBAL'
        assert response.warnings and len(response.warnings) == 1
        assert 'factory-db' not in response.warnings[0]
        assert 'database-specific' in response.warnings[0]

    def test_global_tagtype_without_a_database_duplicate_carries_no_warning(
        self, casbin, tag_type_table, database_table
    ):
        casbin.return_value = _enf(True)
        # Control: the advisory is conditional.
        self._wire(tag_type_table, database_table, rows={})
        response = create_tag_type(
            CreateTagTypeRequestModel(tagTypeName="Custom", description="d"), CLAIMS
        )
        assert response.warnings is None


@pytest.mark.unit
@patch('backend.backend.handlers.tagTypes.createTagTypes.database_table')
@patch('backend.backend.handlers.tagTypes.createTagTypes.tag_type_table')
@patch('backend.backend.handlers.tagTypes.createTagTypes.CasbinEnforcer')
class TestUpdateTagTypeImmutableScope:
    def test_changing_scope_rejected(self, casbin, tag_type_table, database_table):
        casbin.return_value = _enf(True)
        tag_type_table.get_item.return_value = {'Item': {'tagTypeName': 'Custom',
                                                         'databaseId': 'GLOBAL', 'description': 'd',
                                                         'required': 'False'}}
        model = UpdateTagTypeRequestModel(tagTypeName="Custom", description="d2",
                                          required="False", databaseId="factory-db")
        with pytest.raises(VAMSGeneralErrorResponse):
            update_tag_type(model, CLAIMS)


TT_PARTITIONS = {
    "GLOBAL": [{"databaseId": "GLOBAL", "tagTypeName": "System", "description": "d", "required": "False"}],
    "factory-db": [{"databaseId": "factory-db", "tagTypeName": "Custom", "description": "d", "required": "False"}],
    "hospital-db": [{"databaseId": "hospital-db", "tagTypeName": "PatientT", "description": "d", "required": "False"}],
}

TT_CLAIMS = {"tokens": ["u"]}


@pytest.mark.unit
@patch('backend.backend.handlers.tagTypes.tagTypeService.dynamodb_client')
@patch('backend.backend.handlers.tagTypes.tagTypeService.tag_type_table')
@patch('backend.backend.handlers.tagTypes.tagTypeService.CasbinEnforcer')
class TestGetTagTypesScope:
    def _setup(self, casbin, tag_type_table, dynamodb_client, allow=True):
        def query(IndexName=None, KeyConditionExpression=None):
            scope = KeyConditionExpression._values[1]
            return {"Items": [dict(r) for r in TT_PARTITIONS.get(scope, [])]}
        tag_type_table.query.side_effect = query
        # tags association lookup uses a scan paginator -> return no tags
        dynamodb_client.get_paginator.return_value.paginate.return_value.build_full_result.return_value = {"Items": []}
        inst = MagicMock(); inst.enforce.return_value = allow
        casbin.return_value = inst

    def test_databaseid_scope_returns_only_that_db(self, casbin, tag_type_table, dynamodb_client):
        self._setup(casbin, tag_type_table, dynamodb_client)
        result = list_tag_types({"maxItems": 100, "pageSize": 100, "startingToken": None,
                                 "databaseId": "factory-db"}, TT_CLAIMS)
        names = {t["tagTypeName"] for t in result["Items"]}
        assert names == {"Custom"}
        tag_type_table.query.assert_called_once()

    def test_scope_global_returns_only_global(self, casbin, tag_type_table, dynamodb_client):
        self._setup(casbin, tag_type_table, dynamodb_client)
        result = list_tag_types({"maxItems": 100, "pageSize": 100, "startingToken": None,
                                 "scope": "global"}, TT_CLAIMS)
        names = {t["tagTypeName"] for t in result["Items"]}
        assert names == {"System"}


@pytest.mark.unit
@patch('backend.backend.handlers.tagTypes.tagTypeService.tag_table')
@patch('backend.backend.handlers.tagTypes.tagTypeService.tag_type_table')
@patch('backend.backend.handlers.tagTypes.tagTypeService.CasbinEnforcer')
class TestDeleteTagTypeInUseScope:
    def test_global_tagtype_delete_blocked_by_db_scoped_tag(self, casbin, tag_type_table, tag_table):
        """A db-scoped tag referencing a GLOBAL tag type must block the GLOBAL type's delete."""
        casbin.return_value = _enf(True)
        # GLOBAL tag type being deleted
        tag_type_table.get_item.return_value = {
            'Item': {'databaseId': 'GLOBAL', 'tagTypeName': 'Custom',
                     'description': 'd', 'required': 'False'}
        }
        # A db-a-scoped tag names this type, and db-a has NO tag type of that name of its own, so
        # the tag can only mean the shared one. (Rows like this predate the same-scope coupling.)
        tag_type_table.query.return_value = {'Items': [
            {'databaseId': 'GLOBAL', 'tagTypeName': 'Custom'},
        ]}
        tag_table.scan.return_value = {
            'Items': [{'databaseId': 'db-a', 'tagName': 'EquipID', 'tagTypeName': 'Custom'}]
        }
        with pytest.raises(TagTypeServiceError) as exc:
            delete_tag_type('Custom', CLAIMS)  # database_id defaults to GLOBAL
        assert exc.value.status_code == 400
        tag_type_table.delete_item.assert_not_called()

    def test_global_tagtype_delete_not_blocked_by_a_database_with_its_own_same_named_type(
        self, casbin, tag_type_table, tag_table
    ):
        """A database's tags belong to ITS type, so they must not block the shared type's delete."""
        casbin.return_value = _enf(True)
        tag_type_table.get_item.return_value = {
            'Item': {'databaseId': 'GLOBAL', 'tagTypeName': 'Custom',
                     'description': 'd', 'required': 'False'}
        }
        # The name exists in BOTH scopes (allowed: a GLOBAL create over a database name warns).
        tag_type_table.query.return_value = {'Items': [
            {'databaseId': 'GLOBAL', 'tagTypeName': 'Custom'},
            {'databaseId': 'db-a', 'tagTypeName': 'Custom'},
        ]}
        # db-a's tag resolves to db-a's own type, not the GLOBAL one.
        tag_table.scan.return_value = {
            'Items': [{'databaseId': 'db-a', 'tagName': 'EquipID', 'tagTypeName': 'Custom'}]
        }

        delete_tag_type('Custom', CLAIMS)

        tag_type_table.delete_item.assert_called_once()
        assert tag_type_table.delete_item.call_args.kwargs['Key']['databaseId'] == 'GLOBAL'
