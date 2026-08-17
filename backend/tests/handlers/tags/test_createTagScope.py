# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.tags import createTag
from backend.backend.handlers.tags.createTag import create_tag, update_tag

# Reference the exception through the module under test so the asserted class is
# the exact object raised. models.common and backend.backend.models.common load
# from the same file but as two distinct module objects (distinct classes).
VAMSGeneralErrorResponse = createTag.VAMSGeneralErrorResponse

CLAIMS = {"tokens": ["test-user"]}


def _enforcer(allow=True):
    inst = MagicMock()
    inst.enforce.return_value = allow
    return inst


@pytest.mark.unit
@patch('backend.backend.handlers.tags.createTag.database_table')
@patch('backend.backend.handlers.tags.createTag.tag_type_table')
@patch('backend.backend.handlers.tags.createTag.tag_table')
@patch('backend.backend.handlers.tags.createTag.CasbinEnforcer')
class TestCreateTagScope:
    """Composite-key ((databaseId, name)) creation + no-conflict rule.

    Fixtures wire key-aware get_item / query mocks so a lookup on the GLOBAL
    partition is distinct from a lookup on a database partition.
    """

    def _wire(self, tag_table, tag_type_table, database_table,
              tag_rows=None, tag_type_rows=None, db_exists=True):
        """tag_rows / tag_type_rows are dicts keyed by (scope, name)."""
        tag_rows = tag_rows or {}
        tag_type_rows = tag_type_rows or {}

        def tag_get(Key):
            row = tag_rows.get((Key['databaseId'], Key['tagName']))
            return {'Item': row} if row is not None else {}

        def tag_query(IndexName=None, KeyConditionExpression=None):
            # Return every row whose tagName matches the equality condition.
            name = KeyConditionExpression._values[1]
            items = [r for (scope, n), r in tag_rows.items() if n == name]
            return {'Items': items}

        def tt_get(Key):
            row = tag_type_rows.get((Key['databaseId'], Key['tagTypeName']))
            return {'Item': row} if row is not None else {}

        tag_table.get_item.side_effect = tag_get
        tag_table.query.side_effect = tag_query
        tag_type_table.get_item.side_effect = tt_get
        database_table.get_item.return_value = {'Item': {'databaseId': 'factory-db'}} if db_exists else {}

    def test_scoped_tag_stored_with_databaseid(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        self._wire(tag_table, tag_type_table, database_table,
                   tag_type_rows={('factory-db', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'factory-db'}})
        create_tag({'tagName': 'EquipID', 'description': 'd', 'tagTypeName': 'Custom',
                    'databaseId': 'factory-db'}, CLAIMS)
        stored = tag_table.put_item.call_args.kwargs['Item']
        assert stored['databaseId'] == 'factory-db'
        assert stored['tagName'] == 'EquipID'

    def test_global_tag_stored_as_GLOBAL(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        self._wire(tag_table, tag_type_table, database_table,
                   tag_type_rows={('GLOBAL', 'System'): {'tagTypeName': 'System', 'databaseId': 'GLOBAL'}})
        create_tag({'tagName': 'Status', 'description': 'd', 'tagTypeName': 'System'}, CLAIMS)
        stored = tag_table.put_item.call_args.kwargs['Item']
        assert stored['databaseId'] == 'GLOBAL'

    def test_nonexistent_database_rejected(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        self._wire(tag_table, tag_type_table, database_table,
                   tag_type_rows={('factory-db', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'factory-db'}},
                   db_exists=False)
        with pytest.raises(VAMSGeneralErrorResponse):
            create_tag({'tagName': 'EquipID', 'description': 'd', 'tagTypeName': 'Custom',
                        'databaseId': 'factory-db'}, CLAIMS)

    def test_scoped_tag_with_other_db_type_rejected(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        # tag type 'Custom' exists only under hospital-db; tag scoped to factory-db
        self._wire(tag_table, tag_type_table, database_table,
                   tag_type_rows={('hospital-db', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'hospital-db'}})
        with pytest.raises(VAMSGeneralErrorResponse):
            create_tag({'tagName': 'EquipID', 'description': 'd', 'tagTypeName': 'Custom',
                        'databaseId': 'factory-db'}, CLAIMS)

    def test_global_tag_with_scoped_type_rejected(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        # tag type 'Custom' exists only under factory-db; a GLOBAL tag may only use a GLOBAL type
        self._wire(tag_table, tag_type_table, database_table,
                   tag_type_rows={('factory-db', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'factory-db'}})
        with pytest.raises(VAMSGeneralErrorResponse):
            create_tag({'tagName': 'Status', 'description': 'd', 'tagTypeName': 'Custom'}, CLAIMS)

    def test_scoped_tag_with_global_type_rejected(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        # A tag's type must live in the tag's OWN scope. 'System' exists only as GLOBAL, so a tag
        # scoped to factory-db may not use it: a database's tags are described only by that
        # database's own categories.
        self._wire(tag_table, tag_type_table, database_table,
                   tag_type_rows={('GLOBAL', 'System'): {'tagTypeName': 'System', 'databaseId': 'GLOBAL'}})
        with pytest.raises(VAMSGeneralErrorResponse):
            create_tag({'tagName': 'EquipID', 'description': 'd', 'tagTypeName': 'System',
                        'databaseId': 'factory-db'}, CLAIMS)
        tag_table.put_item.assert_not_called()

    def test_scoped_tag_with_same_db_type_accepted_even_when_a_global_type_shares_the_name(
        self, mock_enf, tag_table, tag_type_table, database_table
    ):
        mock_enf.return_value = _enforcer(allow=True)
        # The same type name exists in both scopes; the tag's own scope is the one that must match.
        self._wire(tag_table, tag_type_table, database_table,
                   tag_type_rows={
                       ('GLOBAL', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'GLOBAL'},
                       ('factory-db', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'factory-db'},
                   })
        create_tag({'tagName': 'EquipID', 'description': 'd', 'tagTypeName': 'Custom',
                    'databaseId': 'factory-db'}, CLAIMS)
        assert tag_table.put_item.call_args.kwargs['Item']['databaseId'] == 'factory-db'
    def test_same_name_allowed_in_two_databases(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        # 'Status' already exists in hospital-db; creating it in factory-db must be allowed
        self._wire(
            tag_table, tag_type_table, database_table,
            tag_rows={('hospital-db', 'Status'): {'databaseId': 'hospital-db', 'tagName': 'Status',
                                                  'tagTypeName': 'Custom'}},
            tag_type_rows={('factory-db', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'factory-db'}},
        )
        create_tag({'tagName': 'Status', 'description': 'd', 'tagTypeName': 'Custom',
                    'databaseId': 'factory-db'}, CLAIMS)
        stored = tag_table.put_item.call_args.kwargs['Item']
        assert stored['databaseId'] == 'factory-db'
        assert stored['tagName'] == 'Status'

    def test_db_tag_rejected_when_global_exists(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        # 'Status' exists as a GLOBAL tag -> creating a database-specific one is rejected
        self._wire(
            tag_table, tag_type_table, database_table,
            tag_rows={('GLOBAL', 'Status'): {'databaseId': 'GLOBAL', 'tagName': 'Status',
                                             'tagTypeName': 'System'}},
            tag_type_rows={('factory-db', 'Custom'): {'tagTypeName': 'Custom', 'databaseId': 'factory-db'}},
        )
        with pytest.raises(VAMSGeneralErrorResponse):
            create_tag({'tagName': 'Status', 'description': 'd', 'tagTypeName': 'Custom',
                        'databaseId': 'factory-db'}, CLAIMS)
        tag_table.put_item.assert_not_called()

    def test_global_tag_allowed_when_db_uses_name_but_warns(self, mock_enf, tag_table, tag_type_table,
                                                            database_table):
        mock_enf.return_value = _enforcer(allow=True)
        # 'Status' is already used by factory-db. Promoting the name to the shared vocabulary is
        # ALLOWED — blocking it would force every database copy to be deleted first — but the caller
        # is told, because asset forms then list both entries until the database copy is removed.
        self._wire(
            tag_table, tag_type_table, database_table,
            tag_rows={('factory-db', 'Status'): {'databaseId': 'factory-db', 'tagName': 'Status',
                                                 'tagTypeName': 'Custom'}},
            tag_type_rows={('GLOBAL', 'System'): {'tagTypeName': 'System', 'databaseId': 'GLOBAL'}},
        )
        response = create_tag({'tagName': 'Status', 'description': 'd', 'tagTypeName': 'System'},
                              CLAIMS)

        tag_table.put_item.assert_called_once()
        stored = tag_table.put_item.call_args.kwargs['Item']
        assert stored['databaseId'] == 'GLOBAL'
        assert response.warnings and len(response.warnings) == 1
        # Generic by Rule 11: it may not name the other database.
        assert 'factory-db' not in response.warnings[0]
        assert 'database-specific' in response.warnings[0]

    def test_global_tag_without_a_database_duplicate_carries_no_warning(
        self, mock_enf, tag_table, tag_type_table, database_table
    ):
        mock_enf.return_value = _enforcer(allow=True)
        # Control: the advisory is conditional, not attached to every global create.
        self._wire(
            tag_table, tag_type_table, database_table,
            tag_type_rows={('GLOBAL', 'System'): {'tagTypeName': 'System', 'databaseId': 'GLOBAL'}},
        )
        response = create_tag({'tagName': 'Status', 'description': 'd', 'tagTypeName': 'System'},
                              CLAIMS)
        assert response.warnings is None


@pytest.mark.unit
@patch('backend.backend.handlers.tags.createTag.database_table')
@patch('backend.backend.handlers.tags.createTag.tag_type_table')
@patch('backend.backend.handlers.tags.createTag.tag_table')
@patch('backend.backend.handlers.tags.createTag.CasbinEnforcer')
class TestUpdateTagImmutableScope:
    def test_changing_scope_rejected(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        # stored tag is global; PUT tries to move it to factory-db
        tag_table.get_item.return_value = {'Item': {'tagName': 'Status', 'databaseId': 'GLOBAL',
                                                    'tagTypeName': 'System', 'description': 'd'}}
        tag_type_table.get_item.return_value = {'Item': {'tagTypeName': 'System'}}
        with pytest.raises(VAMSGeneralErrorResponse):
            update_tag({'tagName': 'Status', 'description': 'd2', 'tagTypeName': 'System',
                        'databaseId': 'factory-db'}, CLAIMS)

    def test_same_scope_update_ok(self, mock_enf, tag_table, tag_type_table, database_table):
        mock_enf.return_value = _enforcer(allow=True)
        tag_table.get_item.return_value = {'Item': {'tagName': 'Status', 'databaseId': 'GLOBAL',
                                                    'tagTypeName': 'System', 'description': 'd'}}
        tag_type_table.get_item.return_value = {'Item': {'tagTypeName': 'System'}}
        # databaseId omitted (unchanged) -> allowed
        result = update_tag({'tagName': 'Status', 'description': 'new desc', 'tagTypeName': 'System'}, CLAIMS)
        assert result.success is True

    def test_update_auth_uses_stored_scope(self, mock_enf, tag_table, tag_type_table, database_table):
        enf = _enforcer(allow=False)  # deny
        mock_enf.return_value = enf
        tag_table.get_item.return_value = {'Item': {'tagName': 'Status', 'databaseId': 'factory-db',
                                                    'tagTypeName': 'Custom', 'description': 'd'}}
        result = update_tag({'tagName': 'Status', 'description': 'd2', 'tagTypeName': 'Custom'}, CLAIMS)
        # denied -> handler returns authorization_error() dict (statusCode 403)
        assert isinstance(result, dict) and result.get('statusCode') == 403
        # the enforced object carried the stored databaseId
        enforced_obj = enf.enforce.call_args.args[0]
        assert enforced_obj['databaseId'] == 'factory-db'
