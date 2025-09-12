# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.assetLinks.createAssetLink import lambda_handler


@pytest.fixture(autouse=True)
def mock_env_variables(monkeypatch):
    """Set up environment variables for testing"""
    monkeypatch.setenv("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-asset-links-table-v2")
    monkeypatch.setenv("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME", "test-metadata-table")
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    monkeypatch.setenv("AUTH_TABLE_NAME", "test-auth-table")
    monkeypatch.setenv("USER_ROLES_TABLE_NAME", "test-user-roles-table")
    monkeypatch.setenv("ROLES_TABLE_NAME", "test-roles-table")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("COGNITO_AUTH_ENABLED", "true")


@pytest.fixture
def valid_event():
    """Create a valid API Gateway event for testing"""
    return {
        'body': json.dumps({
            'fromAssetId': 'test-asset-id-1',
            'fromAssetDatabaseId': 'test-database-1',
            'toAssetId': 'test-asset-id-2',
            'toAssetDatabaseId': 'test-database-2',
            'relationshipType': 'parentChild'
        }),
        'requestContext': {
            'http': {
                'method': 'POST',
                'path': '/asset-links'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_event_missing_fields():
    """Create an invalid API Gateway event with missing fields"""
    return {
        'body': json.dumps({
            'fromAssetId': 'test-asset-id-1'
            # Missing required fields: fromAssetDatabaseId, toAssetId, toAssetDatabaseId, relationshipType
        }),
        'requestContext': {
            'http': {
                'method': 'POST',
                'path': '/asset-links'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_event_same_ids():
    """Create an invalid API Gateway event with same asset IDs"""
    return {
        'body': json.dumps({
            'fromAssetId': 'test-asset-id-1',
            'fromAssetDatabaseId': 'test-database-1',
            'toAssetId': 'test-asset-id-1',
            'toAssetDatabaseId': 'test-database-1',
            'relationshipType': 'parentChild'
        }),
        'requestContext': {
            'http': {
                'method': 'POST',
                'path': '/asset-links'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_event_unsupported_relationship():
    """Create an invalid API Gateway event with unsupported relationship type"""
    return {
        'body': json.dumps({
            'fromAssetId': 'test-asset-id-1',
            'fromAssetDatabaseId': 'test-database-1',
            'toAssetId': 'test-asset-id-2',
            'toAssetDatabaseId': 'test-database-2',
            'relationshipType': 'unsupported-type'
        }),
        'requestContext': {
            'http': {
                'method': 'POST',
                'path': '/asset-links'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@patch('backend.backend.handlers.assetLinks.createAssetLink.request_to_claims')
@patch('backend.backend.handlers.assetLinks.createAssetLink.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.createAssetLink.validate_assets_exist')
@patch('backend.backend.handlers.assetLinks.createAssetLink.check_existing_relationship')
@patch('backend.backend.handlers.assetLinks.createAssetLink.check_asset_permissions')
@patch('backend.backend.handlers.assetLinks.createAssetLink.asset_links_table')
def test_create_asset_links_success(mock_asset_links_table, mock_check_asset_permissions, mock_check_existing_relationship, mock_validate_assets_exist, 
                                   mock_casbin_enforcer, mock_request_to_claims,
                                   valid_event, mock_env_variables):
    """Test successful creation of asset links"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_assets_exist.return_value = True
    mock_check_existing_relationship.return_value = False
    mock_check_asset_permissions.return_value = True
    
    mock_asset_links_table.put_item.return_value = {}
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert 'assetLinkId' in response_body
    assert response_body['message'] == 'Asset link created successfully'
    
    # Verify mocks were called correctly
    mock_request_to_claims.assert_called_once_with(valid_event)
    mock_casbin_enforcer.assert_called()
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(valid_event)
    mock_validate_assets_exist.assert_called_once()
    mock_check_existing_relationship.assert_called_once()
    mock_check_asset_permissions.assert_called()
    mock_asset_links_table.put_item.assert_called_once()


@patch('backend.backend.handlers.assetLinks.createAssetLink.request_to_claims')
@patch('backend.backend.handlers.assetLinks.createAssetLink.CasbinEnforcer')
def test_missing_fields(mock_casbin_enforcer, mock_request_to_claims, 
                       invalid_event_missing_fields, mock_env_variables):
    """Test handling of missing required fields"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Execute
    response = lambda_handler(invalid_event_missing_fields, {})
    
    # Assert - Handler returns 400 for missing required fields
    assert response['statusCode'] == 400
    assert "Missing required field" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.createAssetLink.request_to_claims')
@patch('backend.backend.handlers.assetLinks.createAssetLink.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.createAssetLink.validate_assets_exist')
def test_same_asset_ids(mock_validate_assets_exist, mock_casbin_enforcer, mock_request_to_claims, 
                       invalid_event_same_ids, mock_env_variables):
    """Test handling of same asset IDs"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_assets_exist.return_value = True
    
    # Execute
    response = lambda_handler(invalid_event_same_ids, {})
    
    # Assert - Handler validates same asset IDs and returns 400
    assert response['statusCode'] == 400
    assert "Cannot create asset link to the same asset" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.createAssetLink.request_to_claims')
@patch('backend.backend.handlers.assetLinks.createAssetLink.CasbinEnforcer')
def test_unsupported_relationship_type(mock_casbin_enforcer, mock_request_to_claims, 
                                      invalid_event_unsupported_relationship, mock_env_variables):
    """Test handling of unsupported relationship type"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Execute
    response = lambda_handler(invalid_event_unsupported_relationship, {})
    
    # Assert - Pydantic validation will catch invalid relationship type
    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert 'message' in response_body


@patch('backend.backend.handlers.assetLinks.createAssetLink.request_to_claims')
@patch('backend.backend.handlers.assetLinks.createAssetLink.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.createAssetLink.validate_assets_exist')
def test_invalid_asset_ids(mock_validate_assets_exist, mock_casbin_enforcer, 
                          mock_request_to_claims, valid_event, mock_env_variables):
    """Test handling of invalid asset IDs"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_assets_exist.return_value = False
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "do not exist" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.createAssetLink.request_to_claims')
@patch('backend.backend.handlers.assetLinks.createAssetLink.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.createAssetLink.validate_assets_exist')
@patch('backend.backend.handlers.assetLinks.createAssetLink.check_existing_relationship')
@patch('backend.backend.handlers.assetLinks.createAssetLink.check_asset_permissions')
def test_existing_relationship(mock_check_asset_permissions, mock_check_existing_relationship, mock_validate_assets_exist, 
                              mock_casbin_enforcer, mock_request_to_claims, 
                              valid_event, mock_env_variables):
    """Test handling of existing relationship"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_assets_exist.return_value = True
    mock_check_asset_permissions.return_value = True
    mock_check_existing_relationship.return_value = True
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert - Handler returns 400 for existing relationships
    assert response['statusCode'] == 400
    assert "already exists" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.createAssetLink.request_to_claims')
@patch('backend.backend.handlers.assetLinks.createAssetLink.CasbinEnforcer')
def test_unauthorized_api_access(mock_casbin_enforcer, mock_request_to_claims, 
                                valid_event, mock_env_variables):
    """Test handling of unauthorized API access"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = False
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    assert json.loads(response['body'])['message'] == 'Not Authorized'


@patch('backend.backend.handlers.assetLinks.createAssetLink.request_to_claims')
@patch('backend.backend.handlers.assetLinks.createAssetLink.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.createAssetLink.validate_assets_exist')
@patch('backend.backend.handlers.assetLinks.createAssetLink.check_asset_permissions')
def test_unauthorized_asset_access(mock_check_asset_permissions, mock_validate_assets_exist, 
                                  mock_casbin_enforcer, mock_request_to_claims, 
                                  valid_event, mock_env_variables):
    """Test handling of unauthorized asset access"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_assets_exist.return_value = True
    # First call for from asset returns True, second call for to asset returns False
    mock_check_asset_permissions.side_effect = [True, False]
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert - Handler returns 403 for permission errors (PermissionError becomes authorization_error)
    assert response['statusCode'] == 403
    assert "authorized" in json.loads(response['body'])['message']


# Remove the asset link limit test as it doesn't apply to createAssetLink handler
# Remove the internal server error test as it doesn't match the createAssetLink logic

@patch('backend.backend.handlers.assetLinks.createAssetLink.request_to_claims')
@patch('backend.backend.handlers.assetLinks.createAssetLink.CasbinEnforcer')
def test_internal_server_error(mock_casbin_enforcer, mock_request_to_claims, 
                              valid_event, mock_env_variables):
    """Test handling of internal server error"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Simulate an internal error by making the enforcer raise an exception
    mock_casbin_enforcer_instance.enforceAPI.side_effect = Exception("Test exception")
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 500
    assert json.loads(response['body'])['message'] == 'Internal Server Error'
