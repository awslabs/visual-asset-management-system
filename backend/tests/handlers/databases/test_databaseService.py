# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.databases.databaseService import lambda_handler


@pytest.fixture
def get_all_databases_event():
    """Create a valid API Gateway event for getting all databases"""
    return {
        'requestContext': {
            'http': {
                'method': 'GET'
            }
        },
        'pathParameters': {},
        'queryStringParameters': {
            'maxItems': '10',
            'pageSize': '10',
            'startingToken': ''
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def get_specific_database_event():
    """Create a valid API Gateway event for getting a specific database"""
    return {
        'requestContext': {
            'http': {
                'method': 'GET'
            }
        },
        'pathParameters': {
            'databaseId': 'test-database-id'
        },
        'queryStringParameters': {
            'maxItems': '10',
            'pageSize': '10',
            'startingToken': ''
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def delete_database_event():
    """Create a valid API Gateway event for deleting a database"""
    return {
        'requestContext': {
            'http': {
                'method': 'DELETE'
            }
        },
        'pathParameters': {
            'databaseId': 'test-database-id'
        },
        'queryStringParameters': {
            'maxItems': '10',
            'pageSize': '10',
            'startingToken': ''
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_delete_event_missing_database_id():
    """Create an invalid API Gateway event for deleting a database with missing database ID"""
    return {
        'requestContext': {
            'http': {
                'method': 'DELETE'
            }
        },
        'pathParameters': {},
        'queryStringParameters': {
            'maxItems': '10',
            'pageSize': '10',
            'startingToken': ''
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def mock_dynamodb_paginator():
    """Create a mock DynamoDB paginator"""
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value.build_full_result.return_value = {
        'Items': [
            {
                'databaseId': {'S': 'test-database-id-1'},
                'databaseName': {'S': 'Test Database 1'},
                'description': {'S': 'Test description 1'}
            },
            {
                'databaseId': {'S': 'test-database-id-2'},
                'databaseName': {'S': 'Test Database 2'},
                'description': {'S': 'Test description 2'}
            }
        ]
    }
    return mock_paginator


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
@patch('backend.backend.handlers.databases.databaseService.boto3.client')
def test_get_all_databases(mock_boto3_client, mock_validate_pagination, 
                          mock_casbin_enforcer, mock_request_to_claims, 
                          get_all_databases_event, mock_dynamodb_paginator):
    """Test getting all databases"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    mock_db_client = MagicMock()
    mock_db_client.get_paginator.return_value = mock_dynamodb_paginator
    mock_boto3_client.return_value = mock_db_client
    
    # Execute
    response = lambda_handler(get_all_databases_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert 'message' in response_body
    
    # Verify the correct paginator was called
    mock_db_client.get_paginator.assert_called_once_with('scan')
    
    # Verify the paginator was called with the correct parameters
    mock_paginator_call = mock_dynamodb_paginator.paginate.call_args
    assert mock_paginator_call[1]['TableName'] == 'test-database-table'
    assert 'ScanFilter' in mock_paginator_call[1]
    assert 'PaginationConfig' in mock_paginator_call[1]
    
    # Verify authorization was checked
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(get_all_databases_event)
    assert mock_casbin_enforcer_instance.enforce.call_count == 2  # Once for each database


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
@patch('backend.backend.handlers.databases.databaseService.dynamodb')
def test_get_specific_database(mock_dynamodb, mock_validate_pagination, 
                              mock_casbin_enforcer, mock_request_to_claims, 
                              get_specific_database_event):
    """Test getting a specific database"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        'Item': {
            'databaseId': 'test-database-id',
            'databaseName': 'Test Database',
            'description': 'Test description'
        }
    }
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(get_specific_database_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert 'message' in response_body
    
    # Verify the correct table method was called
    mock_dynamodb.Table.assert_called_once_with('test-database-table')
    mock_table.get_item.assert_called_once_with(
        Key={'databaseId': 'test-database-id'}
    )
    
    # Verify authorization was checked
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(get_specific_database_event)
    mock_casbin_enforcer_instance.enforce.assert_called_once()


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
def test_get_database_unauthorized(mock_validate_pagination, mock_casbin_enforcer, 
                                  mock_request_to_claims, get_specific_database_event):
    """Test unauthorized access to get database"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = False  # Unauthorized
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # Execute
    response = lambda_handler(get_specific_database_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Not Authorized'
    
    # Verify authorization was checked
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(get_specific_database_event)
    mock_casbin_enforcer_instance.enforce.assert_not_called()


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
@patch('backend.backend.handlers.databases.databaseService.check_workflows')
@patch('backend.backend.handlers.databases.databaseService.check_pipelines')
@patch('backend.backend.handlers.databases.databaseService.check_assets')
@patch('backend.backend.handlers.databases.databaseService.dynamodb')
def test_delete_database_success(mock_dynamodb, mock_check_assets, mock_check_pipelines, 
                                mock_check_workflows, mock_validate_pagination, 
                                mock_casbin_enforcer, mock_request_to_claims, 
                                delete_database_event):
    """Test successful deletion of a database"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # No active workflows, pipelines, or assets
    mock_check_workflows.return_value = False
    mock_check_pipelines.return_value = False
    mock_check_assets.return_value = False
    
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        'Item': {
            'databaseId': 'test-database-id',
            'databaseName': 'Test Database',
            'description': 'Test description'
        }
    }
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(delete_database_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Database deleted'
    
    # Verify the correct table methods were called
    mock_dynamodb.Table.assert_called_with('test-database-table')
    mock_table.get_item.assert_called_once_with(
        Key={'databaseId': 'test-database-id'}
    )
    mock_table.put_item.assert_called_once()
    mock_table.delete_item.assert_called_once_with(
        Key={'databaseId': 'test-database-id'}
    )
    
    # Verify checks were performed
    mock_check_workflows.assert_called_once_with('test-database-id')
    mock_check_pipelines.assert_called_once_with('test-database-id')
    mock_check_assets.assert_called_once_with('test-database-id')
    
    # Verify authorization was checked
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once_with(delete_database_event)
    mock_casbin_enforcer_instance.enforce.assert_called_once()


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
@patch('backend.backend.handlers.databases.databaseService.check_workflows')
@patch('backend.backend.handlers.databases.databaseService.dynamodb')
def test_delete_database_with_workflows(mock_dynamodb, mock_check_workflows, 
                                       mock_validate_pagination, mock_casbin_enforcer, 
                                       mock_request_to_claims, delete_database_event):
    """Test deletion of a database with active workflows"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # Database has active workflows
    mock_check_workflows.return_value = True
    
    # Execute
    response = lambda_handler(delete_database_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Database contains active workflows'
    
    # Verify check was performed
    mock_check_workflows.assert_called_once_with('test-database-id')
    
    # Verify no deletion was attempted
    mock_dynamodb.Table.assert_not_called()


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
@patch('backend.backend.handlers.databases.databaseService.check_workflows')
@patch('backend.backend.handlers.databases.databaseService.check_pipelines')
@patch('backend.backend.handlers.databases.databaseService.dynamodb')
def test_delete_database_with_pipelines(mock_dynamodb, mock_check_pipelines, 
                                       mock_check_workflows, mock_validate_pagination, 
                                       mock_casbin_enforcer, mock_request_to_claims, 
                                       delete_database_event):
    """Test deletion of a database with active pipelines"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # No active workflows, but has active pipelines
    mock_check_workflows.return_value = False
    mock_check_pipelines.return_value = True
    
    # Execute
    response = lambda_handler(delete_database_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Database contains active pipelines'
    
    # Verify checks were performed
    mock_check_workflows.assert_called_once_with('test-database-id')
    mock_check_pipelines.assert_called_once_with('test-database-id')
    
    # Verify no deletion was attempted
    mock_dynamodb.Table.assert_not_called()


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
@patch('backend.backend.handlers.databases.databaseService.check_workflows')
@patch('backend.backend.handlers.databases.databaseService.check_pipelines')
@patch('backend.backend.handlers.databases.databaseService.check_assets')
@patch('backend.backend.handlers.databases.databaseService.dynamodb')
def test_delete_database_with_assets(mock_dynamodb, mock_check_assets, 
                                    mock_check_pipelines, mock_check_workflows, 
                                    mock_validate_pagination, mock_casbin_enforcer, 
                                    mock_request_to_claims, delete_database_event):
    """Test deletion of a database with active assets"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # No active workflows or pipelines, but has active assets
    mock_check_workflows.return_value = False
    mock_check_pipelines.return_value = False
    mock_check_assets.return_value = True
    
    # Execute
    response = lambda_handler(delete_database_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Database contains active assets'
    
    # Verify checks were performed
    mock_check_workflows.assert_called_once_with('test-database-id')
    mock_check_pipelines.assert_called_once_with('test-database-id')
    mock_check_assets.assert_called_once_with('test-database-id')
    
    # Verify no deletion was attempted
    mock_dynamodb.Table.assert_not_called()


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
@patch('backend.backend.handlers.databases.databaseService.check_workflows')
@patch('backend.backend.handlers.databases.databaseService.check_pipelines')
@patch('backend.backend.handlers.databases.databaseService.check_assets')
@patch('backend.backend.handlers.databases.databaseService.dynamodb')
def test_delete_database_not_found(mock_dynamodb, mock_check_assets, 
                                  mock_check_pipelines, mock_check_workflows, 
                                  mock_validate_pagination, mock_casbin_enforcer, 
                                  mock_request_to_claims, delete_database_event):
    """Test deletion of a non-existent database"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # No active workflows, pipelines, or assets
    mock_check_workflows.return_value = False
    mock_check_pipelines.return_value = False
    mock_check_assets.return_value = False
    
    mock_table = MagicMock()
    mock_table.get_item.return_value = {}  # No item found
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(delete_database_event, {})
    
    # Assert
    assert response['statusCode'] == 404
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Record not found'
    
    # Verify the correct table methods were called
    mock_dynamodb.Table.assert_called_with('test-database-table')
    mock_table.get_item.assert_called_once_with(
        Key={'databaseId': 'test-database-id'}
    )
    mock_table.put_item.assert_not_called()
    mock_table.delete_item.assert_not_called()


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
@patch('backend.backend.handlers.databases.databaseService.check_workflows')
@patch('backend.backend.handlers.databases.databaseService.check_pipelines')
@patch('backend.backend.handlers.databases.databaseService.check_assets')
@patch('backend.backend.handlers.databases.databaseService.dynamodb')
def test_delete_database_unauthorized(mock_dynamodb, mock_check_assets, 
                                     mock_check_pipelines, mock_check_workflows, 
                                     mock_validate_pagination, mock_casbin_enforcer, 
                                     mock_request_to_claims, delete_database_event):
    """Test unauthorized deletion of a database"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = False  # Unauthorized for this specific database
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # No active workflows, pipelines, or assets
    mock_check_workflows.return_value = False
    mock_check_pipelines.return_value = False
    mock_check_assets.return_value = False
    
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        'Item': {
            'databaseId': 'test-database-id',
            'databaseName': 'Test Database',
            'description': 'Test description'
        }
    }
    mock_dynamodb.Table.return_value = mock_table
    
    # Execute
    response = lambda_handler(delete_database_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Action not allowed'
    
    # Verify the correct table methods were called
    mock_dynamodb.Table.assert_called_with('test-database-table')
    mock_table.get_item.assert_called_once_with(
        Key={'databaseId': 'test-database-id'}
    )
    mock_table.put_item.assert_not_called()
    mock_table.delete_item.assert_not_called()


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
def test_delete_database_missing_id(mock_validate_pagination, mock_casbin_enforcer, 
                                   mock_request_to_claims, 
                                   invalid_delete_event_missing_database_id):
    """Test deletion with missing database ID"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_validate_pagination.return_value = None
    
    # Execute
    response = lambda_handler(invalid_delete_event_missing_database_id, {})
    
    # Assert
    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'No database ID in API Call'


@patch('backend.backend.handlers.databases.databaseService.request_to_claims')
@patch('backend.backend.handlers.databases.databaseService.CasbinEnforcer')
@patch('backend.backend.handlers.databases.databaseService.validate_pagination_info')
def test_internal_server_error(mock_validate_pagination, mock_casbin_enforcer, 
                              mock_request_to_claims, get_all_databases_event):
    """Test handling of internal server error"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Simulate an error
    mock_validate_pagination.side_effect = Exception("Test exception")
    
    # Execute
    try:
        response = lambda_handler(get_all_databases_event, {})
        
        # Assert
        assert response['statusCode'] == 500
        response_body = json.loads(response['body'])
        assert response_body['message'] == 'Internal Server Error'
    except Exception as e:
        # If an exception is raised, the test should fail
        assert False, f"lambda_handler did not handle the exception: {str(e)}"
