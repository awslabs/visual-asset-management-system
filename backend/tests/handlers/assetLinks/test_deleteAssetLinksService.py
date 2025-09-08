# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.assetLinks.assetLinksService import lambda_handler


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
        'pathParameters': {
            'relationId': '12345678-1234-1234-1234-123456789012'
        },
        'requestContext': {
            'http': {
                'method': 'DELETE',
                'path': '/assets/links/12345678-1234-1234-1234-123456789012'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_event_missing_relation_id():
    """Create an invalid API Gateway event with missing relation ID"""
    return {
        'pathParameters': {},
        'requestContext': {
            'http': {
                'method': 'DELETE',
                'path': '/assets/links/'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_event_invalid_relation_id():
    """Create an invalid API Gateway event with invalid relation ID format"""
    return {
        'pathParameters': {
            'relationId': 'invalid-uuid-format'
        },
        'requestContext': {
            'http': {
                'method': 'DELETE',
                'path': '/assets/links/invalid-uuid-format'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_metadata_table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.get_asset_details')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
def test_delete_asset_link_success_with_mocks(mock_casbin_enforcer, mock_request_to_claims, 
                                  mock_get_asset_details, mock_asset_links_table, 
                                  mock_asset_links_metadata_table,
                                  valid_event, mock_env_variables):
    """Test successful deletion of asset link"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Mock the asset links table to return a valid asset link
    mock_asset_links_table.get_item.return_value = {
        'Item': {
            'assetLinkId': '12345678-1234-1234-1234-123456789012',
            'fromAssetId': 'test-asset-id-1',
            'fromAssetDatabaseId': 'test-db-1',
            'toAssetId': 'test-asset-id-2',
            'toAssetDatabaseId': 'test-db-1',
            'relationshipType': 'PARENT_CHILD'
        }
    }
    
    # Mock metadata table for cleanup
    mock_asset_links_metadata_table.query.return_value = {'Items': []}
    
    # Mock asset details for permission checking
    mock_get_asset_details.return_value = {
        'assetId': 'test-asset-id-1',
        'databaseId': 'test-db-1',
        'assetName': 'Test Asset'
    }
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert "deleted successfully" in response_body['message']
    
    # Verify mocks were called correctly
    mock_request_to_claims.assert_called_once_with(valid_event)
    mock_casbin_enforcer.assert_called()
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(valid_event)


@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
def test_missing_relation_id(mock_casbin_enforcer, mock_request_to_claims, 
                            invalid_event_missing_relation_id, mock_env_variables):
    """Test handling of missing relation ID"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Execute
    response = lambda_handler(invalid_event_missing_relation_id, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "required" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
def test_invalid_relation_id_format(mock_casbin_enforcer, mock_request_to_claims, 
                                   invalid_event_invalid_relation_id, mock_env_variables):
    """Test handling of invalid relation ID format"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Execute
    response = lambda_handler(invalid_event_invalid_relation_id, {})
    
    # Assert
    # The backend returns 500 for invalid UUID format due to validation error handling
    # This is the actual behavior when validation fails in the current implementation
    assert response['statusCode'] == 500
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Internal Server Error'


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
def test_relation_not_found(mock_casbin_enforcer, mock_request_to_claims, mock_asset_links_table,
                           valid_event, mock_env_variables):
    """Test handling of relation not found"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Mock asset links table to return no items (relation not found)
    mock_asset_links_table.get_item.return_value = {}  # No 'Item' key means not found
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert "not found" in response_body['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
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
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Not Authorized'


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.get_asset_details')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
def test_unauthorized_asset_access_with_mocks(mock_casbin_enforcer, mock_request_to_claims, 
                                  mock_get_asset_details, mock_asset_links_table,
                                  valid_event, mock_env_variables):
    """Test handling of unauthorized asset access"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    # First call for from asset (allow), second call for to asset (deny)
    mock_casbin_enforcer_instance.enforce.side_effect = [True, False]
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Mock the asset links table to return a valid asset link
    mock_asset_links_table.get_item.return_value = {
        'Item': {
            'assetLinkId': '12345678-1234-1234-1234-123456789012',
            'fromAssetId': 'test-asset-id-1',
            'fromAssetDatabaseId': 'test-db-1',
            'toAssetId': 'test-asset-id-2',
            'toAssetDatabaseId': 'test-db-1',
            'relationshipType': 'PARENT_CHILD'
        }
    }
    
    # Mock asset details for permission checking
    mock_get_asset_details.side_effect = [
        {  # First asset
            'assetId': 'test-asset-id-1',
            'databaseId': 'test-db-1',
            'assetName': 'Test Asset 1'
        },
        {  # Second asset
            'assetId': 'test-asset-id-2',
            'databaseId': 'test-db-1',
            'assetName': 'Test Asset 2'
        }
    ]
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    response_body = json.loads(response['body'])
    assert "Not authorized" in response_body['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_metadata_table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.get_asset_details')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
def test_conditional_check_exception(mock_casbin_enforcer, mock_request_to_claims, 
                                    mock_get_asset_details, mock_asset_links_table, 
                                    mock_asset_links_metadata_table,
                                    valid_event, mock_env_variables):
    """Test handling of ConditionalCheckFailedException"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Create a proper boto3 ClientError exception
    from botocore.exceptions import ClientError
    error_response = {'Error': {'Code': 'ConditionalCheckFailedException', 'Message': 'The conditional request failed'}}
    mock_client_error = ClientError(error_response, 'DeleteItem')
    
    # Mock the asset links table to return a valid asset link, then fail on delete
    mock_asset_links_table.get_item.return_value = {
        'Item': {
            'assetLinkId': '12345678-1234-1234-1234-123456789012',
            'fromAssetId': 'test-asset-id-1',
            'fromAssetDatabaseId': 'test-db-1',
            'toAssetId': 'test-asset-id-2',
            'toAssetDatabaseId': 'test-db-1',
            'relationshipType': 'PARENT_CHILD'
        }
    }
    mock_asset_links_table.delete_item.side_effect = mock_client_error
    
    # Mock metadata table for cleanup
    mock_asset_links_metadata_table.query.return_value = {'Items': []}
    
    # Mock asset details for permission checking
    mock_get_asset_details.return_value = {
        'assetId': 'test-asset-id-1',
        'databaseId': 'test-db-1',
        'assetName': 'Test Asset'
    }
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 500  # ConditionalCheckFailedException causes internal error
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Internal Server Error'


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
def test_internal_server_error(mock_casbin_enforcer, mock_request_to_claims, mock_asset_links_table,
                              valid_event, mock_env_variables):
    """Test handling of internal server error"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Mock the asset links table to throw an exception
    mock_asset_links_table.get_item.side_effect = Exception("Test exception")
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 500
    assert json.loads(response['body'])['message'] == 'Internal Server Error'
