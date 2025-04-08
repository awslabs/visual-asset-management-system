# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.assetLinks.deleteAssetLinksService import lambda_handler


@pytest.fixture(autouse=True)
def mock_env_variables(monkeypatch):
    """Set up environment variables for testing"""
    monkeypatch.setenv("ASSET_LINKS_STORAGE_TABLE_NAME", "test-asset-links-table")
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
                'method': 'DELETE'
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
                'method': 'DELETE'
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
                'method': 'DELETE'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.dynamodb')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.get_asset_object_from_id')
def test_delete_asset_link_success_with_mocks(mock_get_asset_object, mock_dynamodb, 
                                  mock_casbin_enforcer, mock_request_to_claims, 
                                  mock_boto3_resource, mock_boto3_client,
                                  valid_event, mock_env_variables):
    """Test successful deletion of asset link"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_table = MagicMock()
    mock_table.scan.return_value = {
        'Items': [
            {
                'assetIdFrom': 'test-asset-id-1',
                'assetIdTo': 'test-asset-id-2',
                'relationId': '12345678-1234-1234-1234-123456789012'
            }
        ]
    }
    mock_dynamodb.Table.return_value = mock_table
    
    mock_get_asset_object.return_value = {}
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    assert json.loads(response['body'])['message'] == 'success'
    
    # Verify mocks were called correctly
    mock_request_to_claims.assert_called_once_with(valid_event)
    mock_casbin_enforcer.assert_called()
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(valid_event)
    mock_table.scan.assert_called_once()
    mock_table.delete_item.assert_called_once()


@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.CasbinEnforcer')
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
    assert "not valid" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.CasbinEnforcer')
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
    assert response['statusCode'] == 400
    # The validator should reject the invalid UUID format


@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.dynamodb')
def test_relation_not_found(mock_dynamodb, mock_casbin_enforcer, 
                           mock_request_to_claims, valid_event, mock_env_variables):
    """Test handling of relation not found"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_table = MagicMock()
    mock_table.scan.return_value = {'Items': []}
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "not valid" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.CasbinEnforcer')
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


@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.dynamodb')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.get_asset_object_from_id')
def test_unauthorized_asset_access_with_mocks(mock_get_asset_object, mock_dynamodb, 
                                  mock_casbin_enforcer, mock_request_to_claims, 
                                  mock_boto3_resource, mock_boto3_client,
                                  valid_event, mock_env_variables):
    """Test handling of unauthorized asset access"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    # First call for from asset, second call for to asset
    mock_casbin_enforcer_instance.enforce.side_effect = [True, False]
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_table = MagicMock()
    mock_table.scan.return_value = {
        'Items': [
            {
                'assetIdFrom': 'test-asset-id-1',
                'assetIdTo': 'test-asset-id-2',
                'relationId': '12345678-1234-1234-1234-123456789012'
            }
        ]
    }
    mock_dynamodb.Table.return_value = mock_table
    
    mock_get_asset_object.return_value = {}
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    assert json.loads(response['body'])['message'] == 'Action not Allowed'


@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.dynamodb')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.get_asset_object_from_id')
def test_conditional_check_exception(mock_get_asset_object, mock_dynamodb, mock_casbin_enforcer, 
                                    mock_request_to_claims, mock_boto3_resource, mock_boto3_client,
                                    valid_event, mock_env_variables):
    """Test handling of ConditionalCheckFailedException"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Create a boto3 ClientError exception with ConditionalCheckFailedException
    class MockClientError(Exception):
        def __init__(self):
            self.response = {'Error': {'Code': 'ConditionalCheckFailedException'}}
    
    mock_table = MagicMock()
    mock_table.scan.return_value = {
        'Items': [
            {
                'assetIdFrom': 'test-asset-id-1',
                'assetIdTo': 'test-asset-id-2',
                'relationId': '12345678-1234-1234-1234-123456789012'
            }
        ]
    }
    mock_table.delete_item.side_effect = MockClientError()
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "doesn't exists" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.dynamodb')
@patch('backend.backend.handlers.assetLinks.deleteAssetLinksService.get_asset_object_from_id')
def test_internal_server_error(mock_get_asset_object, mock_dynamodb, mock_casbin_enforcer, 
                              mock_request_to_claims, mock_boto3_resource, mock_boto3_client,
                              valid_event, mock_env_variables):
    """Test handling of internal server error"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_table = MagicMock()
    mock_table.scan.side_effect = Exception("Test exception")
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 500
    assert json.loads(response['body'])['message'] == 'Internal Server Error'
