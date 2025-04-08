# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.assetLinks.getAssetLinksService import lambda_handler


@pytest.fixture(autouse=True)
def mock_env_variables(monkeypatch):
    """Set up environment variables for testing"""
    monkeypatch.setenv("ASSET_LINKS_STORAGE_TABLE_NAME", "test-asset-links-table")
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    monkeypatch.setenv("COGNITO_AUTH_ENABLED", "true")


@pytest.fixture
def valid_event():
    """Create a valid API Gateway event for testing"""
    return {
        'pathParameters': {
            'assetId': 'test-asset-id-1'
        },
        'queryStringParameters': {
            'maxItems': '10',
            'pageSize': '10'
        },
        'requestContext': {
            'http': {
                'method': 'GET'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_event_missing_asset_id():
    """Create an invalid API Gateway event with missing asset ID"""
    return {
        'pathParameters': {},
        'queryStringParameters': {},
        'requestContext': {
            'http': {
                'method': 'GET'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_event_invalid_asset_id():
    """Create an invalid API Gateway event with invalid asset ID format"""
    return {
        'pathParameters': {
            'assetId': ''  # Empty asset ID
        },
        'queryStringParameters': {},
        'requestContext': {
            'http': {
                'method': 'GET'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_table_name', 'test-asset-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.dynamodb')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.dynamodb_client')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.validate_pagination_info')
def test_get_asset_links_success(mock_validate_pagination, mock_dynamodb_client, 
                                mock_dynamodb, mock_casbin_enforcer, 
                                mock_request_to_claims, mock_boto3_resource, mock_boto3_client,
                                valid_event, mock_env_variables):
    """Test successful retrieval of asset links"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # Mock DynamoDB table responses
    mock_table = MagicMock()
    mock_table.query.side_effect = [
        # First query for assetIdFrom
        {
            'Items': [
                {
                    'assetIdFrom': 'test-asset-id-1',
                    'assetIdTo': 'test-asset-id-2',
                    'relationId': 'relation-1',
                    'relationshipType': 'parent-child'
                }
            ]
        },
        # Second query for assetIdTo
        {
            'Items': [
                {
                    'assetIdFrom': 'test-asset-id-3',
                    'assetIdTo': 'test-asset-id-1',
                    'relationId': 'relation-2',
                    'relationshipType': 'parent-child'
                }
            ]
        }
    ]
    mock_dynamodb.Table.return_value = mock_table
    
    # Mock DynamoDB client scan response for asset names
    mock_dynamodb_client.scan.return_value = {
        'Items': [
            {
                'assetId': {'S': 'test-asset-id-1'},
                'assetName': {'S': 'Test Asset 1'},
                'databaseId': {'S': 'test-db-1'},
                'assetType': {'S': 'model/gltf-binary'},
                'tags': {'L': [{'S': 'tag1'}, {'S': 'tag2'}]}
            },
            {
                'assetId': {'S': 'test-asset-id-2'},
                'assetName': {'S': 'Test Asset 2'},
                'databaseId': {'S': 'test-db-1'},
                'assetType': {'S': 'model/gltf-binary'},
                'tags': {'L': [{'S': 'tag1'}, {'S': 'tag2'}]}
            },
            {
                'assetId': {'S': 'test-asset-id-3'},
                'assetName': {'S': 'Test Asset 3'},
                'databaseId': {'S': 'test-db-2'},
                'assetType': {'S': 'model/gltf-binary'},
                'tags': {'L': [{'S': 'tag1'}, {'S': 'tag2'}]}
            }
        ]
    }
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert 'message' in response_body
    
    relationships = response_body['message']
    assert 'parent' in relationships
    assert 'child' in relationships
    assert 'relatedTo' in relationships
    
    # Verify parent relationship
    assert len(relationships['parent']) == 1
    assert relationships['parent'][0]['assetId'] == 'test-asset-id-3'
    
    # Verify child relationship
    assert len(relationships['child']) == 1
    assert relationships['child'][0]['assetId'] == 'test-asset-id-2'
    
    # Verify mocks were called correctly
    mock_request_to_claims.assert_called_once_with(valid_event)
    mock_casbin_enforcer.assert_called()
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(valid_event)
    mock_table.query.assert_called()
    mock_dynamodb_client.scan.assert_called_once()


@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.validate_pagination_info')
def test_missing_asset_id(mock_validate_pagination, mock_casbin_enforcer, 
                         mock_request_to_claims, invalid_event_missing_asset_id, 
                         mock_env_variables):
    """Test handling of missing asset ID"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # Execute
    response = lambda_handler(invalid_event_missing_asset_id, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "not valid" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.validate_pagination_info')
def test_invalid_asset_id(mock_validate_pagination, mock_casbin_enforcer, 
                         mock_request_to_claims, invalid_event_invalid_asset_id, 
                         mock_env_variables):
    """Test handling of invalid asset ID"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # Execute
    response = lambda_handler(invalid_event_invalid_asset_id, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "not valid" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.validate_pagination_info')
def test_unauthorized_api_access(mock_validate_pagination, mock_casbin_enforcer, 
                                mock_request_to_claims, valid_event, mock_env_variables):
    """Test handling of unauthorized API access"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = False
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    assert json.loads(response['body'])['message'] == 'Not Authorized'


@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_table_name', 'test-asset-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.dynamodb')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.dynamodb_client')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.validate_pagination_info')
def test_related_assets_filtering(mock_validate_pagination, mock_dynamodb_client, 
                                 mock_dynamodb, mock_casbin_enforcer, 
                                 mock_request_to_claims, mock_boto3_resource, mock_boto3_client,
                                 valid_event, mock_env_variables):
    """Test filtering of related assets based on permissions"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    # Allow access to first asset, deny access to second asset
    mock_casbin_enforcer_instance.enforce.side_effect = [True, False]
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # Mock DynamoDB table responses
    mock_table = MagicMock()
    mock_table.query.side_effect = [
        # First query for assetIdFrom
        {
            'Items': [
                {
                    'assetIdFrom': 'test-asset-id-1',
                    'assetIdTo': 'test-asset-id-2',
                    'relationId': 'relation-1',
                    'relationshipType': 'related'  # Using 'related' type
                },
                {
                    'assetIdFrom': 'test-asset-id-1',
                    'assetIdTo': 'test-asset-id-3',
                    'relationId': 'relation-2',
                    'relationshipType': 'related'  # Using 'related' type
                }
            ]
        },
        # Second query for assetIdTo
        {
            'Items': []
        }
    ]
    mock_dynamodb.Table.return_value = mock_table
    
    # Mock DynamoDB client scan response for asset names
    mock_dynamodb_client.scan.return_value = {
        'Items': [
            {
                'assetId': {'S': 'test-asset-id-1'},
                'assetName': {'S': 'Test Asset 1'},
                'databaseId': {'S': 'test-db-1'},
                'assetType': {'S': 'model/gltf-binary'},
                'tags': {'L': [{'S': 'tag1'}, {'S': 'tag2'}]}
            },
            {
                'assetId': {'S': 'test-asset-id-2'},
                'assetName': {'S': 'Test Asset 2'},
                'databaseId': {'S': 'test-db-1'},
                'assetType': {'S': 'model/gltf-binary'},
                'tags': {'L': [{'S': 'tag1'}, {'S': 'tag2'}]}
            },
            {
                'assetId': {'S': 'test-asset-id-3'},
                'assetName': {'S': 'Test Asset 3'},
                'databaseId': {'S': 'test-db-2'},
                'assetType': {'S': 'model/gltf-binary'},
                'tags': {'L': [{'S': 'tag1'}, {'S': 'tag2'}]}
            }
        ]
    }
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert 'message' in response_body
    
    relationships = response_body['message']
    assert 'relatedTo' in relationships
    
    # Verify only one related asset is included (the one with permission)
    assert len(relationships['relatedTo']) == 1
    assert relationships['relatedTo'][0]['assetId'] == 'test-asset-id-2'


@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_table_name', 'test-asset-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.validate_pagination_info')
def test_pagination_validation_error(mock_validate_pagination, mock_casbin_enforcer, 
                                    mock_request_to_claims, mock_boto3_resource, mock_boto3_client,
                                    valid_event, mock_env_variables):
    """Test handling of pagination validation error"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Simulate pagination validation error
    mock_validate_pagination.side_effect = ValueError("Invalid pagination parameters")
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 500
    assert json.loads(response['body'])['message'] == 'Internal Server Error'


@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_table_name', 'test-asset-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.validate_pagination_info')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.dynamodb')
def test_conditional_check_exception(mock_dynamodb, mock_validate_pagination, 
                                    mock_casbin_enforcer, mock_request_to_claims, 
                                    mock_boto3_resource, mock_boto3_client,
                                    valid_event, mock_env_variables):
    """Test handling of ConditionalCheckFailedException"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # Create a boto3 ClientError exception with ConditionalCheckFailedException
    class MockClientError(Exception):
        def __init__(self):
            self.response = {'Error': {'Code': 'ConditionalCheckFailedException'}}
    
    mock_table = MagicMock()
    mock_table.query.side_effect = MockClientError()
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "doesn't exists" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.asset_table_name', 'test-asset-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.validate_pagination_info')
@patch('backend.backend.handlers.assetLinks.getAssetLinksService.dynamodb')
def test_internal_server_error(mock_dynamodb, mock_validate_pagination, 
                              mock_casbin_enforcer, mock_request_to_claims, 
                              mock_boto3_resource, mock_boto3_client,
                              valid_event, mock_env_variables):
    """Test handling of internal server error"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    mock_table = MagicMock()
    mock_table.query.side_effect = Exception("Test exception")
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 500
    assert json.loads(response['body'])['message'] == 'Internal Server Error'
