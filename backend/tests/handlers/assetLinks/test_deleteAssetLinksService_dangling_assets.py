# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deleting an asset link whose linked assets have been permanently deleted.

Permanently deleting an asset leaves its link rows in place. The delete path must still be
able to remove such a row: authorization is enforced against whichever linked asset still
exists, and when neither exists the API-level check alone admits the caller.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.assetLinks.assetLinksService import lambda_handler

LINK_ID = '12345678-1234-1234-1234-123456789012'

FROM_ASSET = {'assetId': 'test-asset-id-1', 'databaseId': 'test-db-1', 'assetName': 'From Asset'}
TO_ASSET = {'assetId': 'test-asset-id-2', 'databaseId': 'test-db-1', 'assetName': 'To Asset'}

LINK_ITEM = {
    'assetLinkId': LINK_ID,
    'fromAssetId': 'test-asset-id-1',
    'fromAssetDatabaseId': 'test-db-1',
    'toAssetId': 'test-asset-id-2',
    'toAssetDatabaseId': 'test-db-1',
    'relationshipType': 'PARENT_CHILD',
}

MODULE = 'backend.backend.handlers.assetLinks.assetLinksService'


@pytest.fixture(autouse=True)
def mock_env_variables(monkeypatch):
    """Set up environment variables for testing"""
    monkeypatch.setenv("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-asset-links-table-v2")
    monkeypatch.setenv("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME", "test-metadata-table")
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    monkeypatch.setenv("AUTH_TABLE_NAME", "test-auth-table")
    monkeypatch.setenv("CONSTRAINTS_TABLE_NAME", "test-constraint-table")
    monkeypatch.setenv("USER_ROLES_TABLE_NAME", "test-user-roles-table")
    monkeypatch.setenv("ROLES_TABLE_NAME", "test-roles-table")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_AUTH_ENABLED", "true")


@pytest.fixture
def delete_event():
    return {
        'pathParameters': {'assetLinkId': LINK_ID},
        'requestContext': {'http': {'method': 'DELETE', 'path': f'/assets/links/{LINK_ID}'}},
        'headers': {'Authorization': 'Bearer test-token'},
    }


def _enforcer(api_allowed=True, enforce=True):
    instance = MagicMock()
    instance.enforceAPI.return_value = api_allowed
    if isinstance(enforce, list):
        instance.enforce.side_effect = enforce
    else:
        instance.enforce.return_value = enforce
    return instance


def _run(delete_event, from_asset, to_asset, enforcer):
    """Run one DELETE through lambda_handler with the link row present and the given asset reads."""
    with patch(f'{MODULE}.asset_links_metadata_table') as metadata_table, \
         patch(f'{MODULE}.asset_links_table') as links_table, \
         patch(f'{MODULE}.get_asset_details') as get_asset_details, \
         patch(f'{MODULE}.request_to_claims') as request_to_claims, \
         patch(f'{MODULE}.CasbinEnforcer') as casbin_enforcer:
        request_to_claims.return_value = {"tokens": ["test-token"]}
        casbin_enforcer.return_value = enforcer
        links_table.get_item.return_value = {'Item': dict(LINK_ITEM)}
        metadata_table.query.return_value = {'Items': []}
        # delete_asset_link reads the from end first, then the to end
        get_asset_details.side_effect = [from_asset, to_asset]

        response = lambda_handler(delete_event, {})
        return response, links_table, metadata_table, enforcer


def test_delete_link_with_from_asset_missing_succeeds(delete_event):
    """The from end is gone: the link deletes, authorized against the surviving to end only."""
    enforcer = _enforcer(enforce=True)
    response, links_table, metadata_table, enforcer = _run(delete_event, None, dict(TO_ASSET), enforcer)

    assert response['statusCode'] == 200
    assert "deleted successfully" in json.loads(response['body'])['message']
    links_table.delete_item.assert_called_once_with(Key={'assetLinkId': LINK_ID})
    metadata_table.query.assert_called_once()

    # Tier-2 ran exactly once, and against the asset that still exists
    assert enforcer.enforce.call_count == 1
    enforced_asset, action = enforcer.enforce.call_args[0]
    assert enforced_asset['assetId'] == TO_ASSET['assetId']
    assert enforced_asset['object__type'] == 'asset'
    assert action == 'DELETE'


def test_delete_link_with_to_asset_missing_still_requires_permission_on_surviving_end(delete_event):
    """The to end is gone but the caller lacks DELETE on the surviving from end: refused, nothing deleted."""
    enforcer = _enforcer(enforce=False)
    response, links_table, metadata_table, enforcer = _run(delete_event, dict(FROM_ASSET), None, enforcer)

    assert response['statusCode'] == 403
    assert "Not authorized" in json.loads(response['body'])['message']
    links_table.delete_item.assert_not_called()
    metadata_table.query.assert_not_called()

    assert enforcer.enforce.call_count == 1
    enforced_asset, action = enforcer.enforce.call_args[0]
    assert enforced_asset['assetId'] == FROM_ASSET['assetId']
    assert action == 'DELETE'


def test_delete_link_with_to_asset_missing_succeeds_when_permitted(delete_event):
    """The to end is gone and the caller holds DELETE on the surviving from end: the link deletes."""
    enforcer = _enforcer(enforce=True)
    response, links_table, metadata_table, enforcer = _run(delete_event, dict(FROM_ASSET), None, enforcer)

    assert response['statusCode'] == 200
    links_table.delete_item.assert_called_once_with(Key={'assetLinkId': LINK_ID})
    assert enforcer.enforce.call_count == 1
    assert enforcer.enforce.call_args[0][0]['assetId'] == FROM_ASSET['assetId']


def test_delete_link_with_both_assets_missing_succeeds_on_api_permission_alone(delete_event):
    """Both ends are gone: nothing is left to authorize against, so the API-level check suffices."""
    enforcer = _enforcer(enforce=False)  # would deny any Tier-2 check, but none should run
    response, links_table, metadata_table, enforcer = _run(delete_event, None, None, enforcer)

    assert response['statusCode'] == 200
    assert "deleted successfully" in json.loads(response['body'])['message']
    links_table.delete_item.assert_called_once_with(Key={'assetLinkId': LINK_ID})
    metadata_table.query.assert_called_once()
    enforcer.enforce.assert_not_called()
    enforcer.enforceAPI.assert_called_once_with(delete_event)


def test_delete_link_with_both_assets_missing_still_requires_api_permission(delete_event):
    """Both ends gone does not bypass Tier-1: a caller without API access is refused before any read."""
    enforcer = _enforcer(api_allowed=False)
    response, links_table, metadata_table, enforcer = _run(delete_event, None, None, enforcer)

    assert response['statusCode'] == 403
    assert json.loads(response['body'])['message'] == 'Not Authorized'
    links_table.get_item.assert_not_called()
    links_table.delete_item.assert_not_called()
    enforcer.enforce.assert_not_called()


def test_delete_link_with_both_assets_present_is_unchanged(delete_event):
    """Control: both ends exist, so both are authorized and the link deletes as before."""
    enforcer = _enforcer(enforce=True)
    response, links_table, metadata_table, enforcer = _run(delete_event, dict(FROM_ASSET), dict(TO_ASSET), enforcer)

    assert response['statusCode'] == 200
    links_table.delete_item.assert_called_once_with(Key={'assetLinkId': LINK_ID})
    enforced = [(call[0][0]['assetId'], call[0][1]) for call in enforcer.enforce.call_args_list]
    assert enforced, "no Tier-2 check ran although both ends exist"
    assert set(enforced) >= {(FROM_ASSET['assetId'], 'DELETE'), (TO_ASSET['assetId'], 'DELETE')}
    assert enforcer.enforce.call_count <= 2


def test_delete_link_with_both_assets_present_denied_on_one_end_is_unchanged(delete_event):
    """Control: both ends exist and one denies DELETE, so the link is not deleted."""
    enforcer = _enforcer(enforce=[True, False])
    response, links_table, metadata_table, enforcer = _run(delete_event, dict(FROM_ASSET), dict(TO_ASSET), enforcer)

    assert response['statusCode'] == 403
    links_table.delete_item.assert_not_called()
    assert enforcer.enforce.call_count == 2
