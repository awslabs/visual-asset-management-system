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
    monkeypatch.setenv("ASSET_LINKS_STORAGE_TABLE_NAME", "test-asset-links-table")
    monkeypatch.setenv("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
    monkeypatch.setenv("COGNITO_AUTH_ENABLED", "true")


@pytest.fixture
def valid_event():
    """Create a valid API Gateway event for testing"""
    return {
        'body': {
            'assetIdFrom': 'test-asset-id-1',
            'assetIdTo': 'test-asset-id-2',
            'relationshipType': 'parent-child'
        },
        'requestContext': {
            'http': {
                'method': 'POST'
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
        'body': {
            'assetIdFrom': 'test-asset-id-1'
        },
        'requestContext': {
            'http': {
                'method': 'POST'
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
        'body': {
            'assetIdFrom': 'test-asset-id-1',
            'assetIdTo': 'test-asset-id-1',
            'relationshipType': 'parent-child'
        },
        'requestContext': {
            'http': {
                'method': 'POST'
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
        'body': {
            'assetIdFrom': 'test-asset-id-1',
            'assetIdTo': 'test-asset-id-2',
            'relationshipType': 'unsupported-type'
        },
        'requestContext': {
            'http': {
                'method': 'POST'
            }
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.assetLinksService.assets_ids_are_valid')
@patch('backend.backend.handlers.assetLinks.assetLinksService.dynamodb')
@patch('backend.backend.handlers.assetLinks.assetLinksService.dynamodb_client')
@patch('backend.backend.handlers.assetLinks.assetLinksService.get_asset_object_from_id')
def test_create_asset_links_success(mock_get_asset_object, mock_dynamodb_client, mock_dynamodb, mock_assets_ids_are_valid, 
                                   mock_casbin_enforcer, mock_request_to_claims, mock_boto3_resource, mock_boto3_client,
                                   valid_event, mock_env_variables):
    """Test successful creation of asset links"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_assets_ids_are_valid.return_value = True
    
    mock_get_asset_object.return_value = {}
    
    mock_table = MagicMock()
    mock_table.query.return_value = {'Items': []}
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    assert json.loads(response['body'])['message'] == 'success'
    
    # Verify mocks were called correctly
    mock_request_to_claims.assert_called_once_with(valid_event)
    mock_casbin_enforcer.assert_called()
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(valid_event)
    mock_assets_ids_are_valid.assert_called_once_with([valid_event['body']['assetIdFrom'], valid_event['body']['assetIdTo']])
    mock_table.put_item.assert_called_once()


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
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
    
    # Assert
    assert response['statusCode'] == 400
    assert "required fields" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
def test_same_asset_ids(mock_casbin_enforcer, mock_request_to_claims, 
                       invalid_event_same_ids, mock_env_variables):
    """Test handling of same asset IDs"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Execute
    response = lambda_handler(invalid_event_same_ids, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "can't be same" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
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
    
    # Assert
    assert response['statusCode'] == 400
    assert "isn't supported" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.assetLinksService.assets_ids_are_valid')
def test_invalid_asset_ids(mock_assets_ids_are_valid, mock_casbin_enforcer, 
                          mock_request_to_claims, valid_event, mock_env_variables):
    """Test handling of invalid asset IDs"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_assets_ids_are_valid.return_value = False
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "should be valid and existing" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.assetLinksService.assets_ids_are_valid')
@patch('backend.backend.handlers.assetLinks.assetLinksService.dynamodb')
def test_existing_relationship(mock_dynamodb, mock_assets_ids_are_valid, 
                              mock_casbin_enforcer, mock_request_to_claims, 
                              valid_event, mock_env_variables):
    """Test handling of existing relationship"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_assets_ids_are_valid.return_value = True
    
    mock_table = MagicMock()
    mock_table.query.return_value = {'Items': [{'relationId': 'test-relation-id'}]}
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "already exists" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('boto3.client')
@patch('boto3.resource')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.assetLinksService.assets_ids_are_valid')
@patch('backend.backend.handlers.assetLinks.assetLinksService.get_asset_object_from_id')
def test_unauthorized_api_access(mock_get_asset_object, mock_assets_ids_are_valid, mock_casbin_enforcer, mock_request_to_claims, 
                                mock_boto3_resource, mock_boto3_client, valid_event, mock_env_variables):
    """Test handling of unauthorized API access"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = False
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_assets_ids_are_valid.return_value = True
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    assert json.loads(response['body'])['message'] == 'Not Authorized'


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.assetLinksService.assets_ids_are_valid')
@patch('backend.backend.handlers.assetLinks.assetLinksService.dynamodb')
@patch('backend.backend.handlers.assetLinks.assetLinksService.get_asset_object_from_id')
def test_unauthorized_asset_access(mock_get_asset_object, mock_dynamodb, 
                                  mock_assets_ids_are_valid, mock_casbin_enforcer, 
                                  mock_request_to_claims, valid_event, mock_env_variables):
    """Test handling of unauthorized asset access"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    # First call for from asset, second call for to asset
    mock_casbin_enforcer_instance.enforce.side_effect = [True, False]
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_assets_ids_are_valid.return_value = True
    
    mock_table = MagicMock()
    mock_table.query.return_value = {'Items': []}
    mock_dynamodb.Table.return_value = mock_table
    
    mock_get_asset_object.return_value = {}
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    assert "missing permissions" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.assetLinksService.assets_ids_are_valid')
@patch('backend.backend.handlers.assetLinks.assetLinksService.dynamodb')
def test_asset_link_limit_exceeded(mock_dynamodb, mock_assets_ids_are_valid, 
                                  mock_casbin_enforcer, mock_request_to_claims, 
                                  valid_event, mock_env_variables):
    """Test handling of asset link limit exceeded"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_assets_ids_are_valid.return_value = True
    
    mock_table = MagicMock()
    mock_table.query.side_effect = [
        {'Items': []},  # First query for existing relation
        {'Items': [{}] * 501},  # Second query for from asset links (exceeds limit)
        {'Items': []}   # Third query for to asset links
    ]
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    assert "exceeds the 500 asset link total limit" in json.loads(response['body'])['message']


@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_links_db_table_name', 'test-asset-links-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.asset_table_name', 'test-asset-table')
@patch('backend.backend.handlers.assetLinks.assetLinksService.request_to_claims')
@patch('backend.backend.handlers.assetLinks.assetLinksService.CasbinEnforcer')
@patch('backend.backend.handlers.assetLinks.assetLinksService.assets_ids_are_valid')
def test_internal_server_error(mock_assets_ids_are_valid, mock_casbin_enforcer, 
                              mock_request_to_claims, valid_event, mock_env_variables):
    """Test handling of internal server error"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_assets_ids_are_valid.side_effect = Exception("Test exception")
    
    # Execute
    response = lambda_handler(valid_event, {})
    
    # Assert
    assert response['statusCode'] == 500
    assert json.loads(response['body'])['message'] == 'Internal Server Error'
