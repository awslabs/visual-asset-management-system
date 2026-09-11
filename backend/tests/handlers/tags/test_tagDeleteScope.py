# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.tags.tagService import delete_tag

CLAIMS = {"tokens": ["u"]}


@pytest.mark.unit
@patch('backend.backend.handlers.tags.tagService.tag_table')
@patch('backend.backend.handlers.tags.tagService.CasbinEnforcer')
class TestDeleteTagScope:
    def test_enforce_uses_stored_databaseid(self, casbin, tag_table):
        tag_table.get_item.return_value = {'Item': {'tagName': 'EquipID', 'databaseId': 'factory-db'}}
        inst = MagicMock(); inst.enforce.return_value = True; casbin.return_value = inst
        delete_tag('EquipID', CLAIMS)
        enforced_obj = inst.enforce.call_args.args[0]
        assert enforced_obj['databaseId'] == 'factory-db'
        assert enforced_obj['object__type'] == 'tag'

    def test_tag_without_databaseid_normalized_to_GLOBAL(self, casbin, tag_table):
        tag_table.get_item.return_value = {'Item': {'tagName': 'Status'}}  # no databaseId
        inst = MagicMock(); inst.enforce.return_value = True; casbin.return_value = inst
        delete_tag('Status', CLAIMS)
        enforced_obj = inst.enforce.call_args.args[0]
        assert enforced_obj['databaseId'] == 'GLOBAL'

    def test_denied_returns_authorization_error(self, casbin, tag_table):
        tag_table.get_item.return_value = {'Item': {'tagName': 'EquipID', 'databaseId': 'factory-db'}}
        inst = MagicMock(); inst.enforce.return_value = False; casbin.return_value = inst
        result = delete_tag('EquipID', CLAIMS)
        assert isinstance(result, dict) and result.get('statusCode') == 403
        tag_table.delete_item.assert_not_called()
