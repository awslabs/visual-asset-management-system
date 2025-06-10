# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import pytest
from unittest.mock import patch, MagicMock, call

# Import the lambda_handler with proper mocking
import sys
from unittest.mock import patch, MagicMock

# Now import the lambda_handler
from backend.backend.handlers.assets.ingestAsset import lambda_handler

@pytest.fixture
def initialize_ingest_event():
    """Create a valid API Gateway event for initializing an asset ingest operation"""
    return {
        'requestContext': {
            'http': {
                'method': 'POST'
            }
        },
        'body': json.dumps({
            'databaseId': 'test-database-id',
            'assetId': 'test-asset-id',
            'assetName': 'Test Asset',
            'description': 'Test asset description',
            'isDistributable': True,
            'tags': ['tag1', 'tag2'],
            'files': [
                {
                    'key': 'test-asset-id/file1.txt',
                    'file_size': 1024
                }
            ]
        }),
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }

@pytest.fixture
def complete_ingest_event():
    """Create a valid API Gateway event for completing an asset ingest operation"""
    return {
        'requestContext': {
            'http': {
                'method': 'POST'
            }
        },
        'body': json.dumps({
            'databaseId': 'test-database-id',
            'assetId': 'test-asset-id',
            'assetName': 'Test Asset',
            'description': 'Test asset description',
            'isDistributable': True,
            'tags': ['tag1', 'tag2'],
            'uploadId': 'test-upload-id',
            'files': [
                {
                    'key': 'test-asset-id/file1.txt',
                    'uploadIdS3': 'test-upload-id-s3',
                    'parts': [
                        {
                            'PartNumber': 1,
                            'ETag': 'test-etag'
                        }
                    ]
                }
            ]
        }),
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }

@pytest.fixture
def mock_env_vars():
    """Mock environment variables"""
    with patch.dict('os.environ', {
        'AWS_REGION': 'us-east-1',
        'CRED_TOKEN_TIMEOUT_SECONDS': '3600',
        'S3_ASSET_STORAGE_BUCKET': 'test-bucket',
        'DATABASE_STORAGE_TABLE_NAME': 'test-database-table',
        'ASSET_STORAGE_TABLE_NAME': 'test-asset-table',
        'METADATA_STORAGE_TABLE_NAME': 'test-metadata-table',
        'CREATE_ASSET_LAMBDA_FUNCTION_NAME': 'test-create-asset-lambda',
        'FILE_UPLOAD_LAMBDA_FUNCTION_NAME': 'test-file-upload-lambda'
    }):
        yield

@patch('backend.backend.handlers.assets.ingestAsset.request_to_claims')
@patch('backend.backend.handlers.assets.ingestAsset.CasbinEnforcer')
@patch('backend.backend.handlers.assets.ingestAsset.verify_database_exists')
@patch('backend.backend.handlers.assets.ingestAsset.invoke_lambda')
def test_initialize_ingest_success(mock_invoke_lambda, mock_verify_database, 
                                  mock_casbin_enforcer, mock_request_to_claims,
                                  initialize_ingest_event, mock_env_vars):
    """Test successful initialization of an asset ingest operation"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_verify_database.return_value = True
    
    # Mock the file upload lambda response
    mock_invoke_lambda.return_value = {
        'statusCode': 200,
        'body': json.dumps({
            'uploadId': 'test-upload-id',
            'files': [
                {
                    'key': 'test-asset-id/file1.txt',
                    'uploadIdS3': 'test-upload-id-s3',
                    'numParts': 1,
                    'partUploadUrls': [
                        {
                            'PartNumber': 1,
                            'UploadUrl': 'https://test-url.com'
                        }
                    ]
                }
            ]
        })
    }
    
    # Execute
    response = lambda_handler(initialize_ingest_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['uploadId'] == 'test-upload-id'
    assert len(response_body['files']) == 1
    
    # Verify the correct lambda was called
    mock_invoke_lambda.assert_called_once()
    args, kwargs = mock_invoke_lambda.call_args
    assert args[0] == 'test-file-upload-lambda'
    
    # Verify authorization was checked
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once()
    mock_casbin_enforcer_instance.enforce.assert_called_once()

@patch('backend.backend.handlers.assets.ingestAsset.request_to_claims')
@patch('backend.backend.handlers.assets.ingestAsset.CasbinEnforcer')
@patch('backend.backend.handlers.assets.ingestAsset.verify_database_exists')
@patch('backend.backend.handlers.assets.ingestAsset.verify_asset_exists')
@patch('backend.backend.handlers.assets.ingestAsset.invoke_lambda')
@patch('backend.backend.handlers.assets.ingestAsset.update_metadata')
def test_complete_ingest_existing_asset(mock_update_metadata, mock_invoke_lambda, 
                                       mock_verify_asset, mock_verify_database,
                                       mock_casbin_enforcer, mock_request_to_claims,
                                       complete_ingest_event, mock_env_vars):
    """Test completing an asset ingest operation with an existing asset"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_verify_database.return_value = True
    mock_verify_asset.return_value = True  # Asset exists
    
    # Mock the file upload lambda response
    mock_invoke_lambda.return_value = {
        'statusCode': 200,
        'body': json.dumps({
            'uploadId': 'test-upload-id',
            'assetId': 'test-asset-id',
            'fileResults': [
                {
                    'key': 'test-asset-id/file1.txt',
                    'uploadIdS3': 'test-upload-id-s3',
                    'success': True
                }
            ],
            'overallSuccess': True
        })
    }
    
    mock_update_metadata.return_value = True
    
    # Execute
    response = lambda_handler(complete_ingest_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['uploadId'] == 'test-upload-id'
    assert response_body['assetId'] == 'test-asset-id'
    assert response_body['overallSuccess'] == True
    
    # Verify the correct lambda was called
    assert mock_invoke_lambda.call_count == 1  # Only file upload lambda should be called
    args, kwargs = mock_invoke_lambda.call_args
    assert args[0] == 'test-file-upload-lambda'
    
    # Verify authorization was checked
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once()
    mock_casbin_enforcer_instance.enforce.assert_called_once()
    
    # Verify metadata was updated
    mock_update_metadata.assert_called_once_with('test-database-id', 'test-asset-id')

@patch('backend.backend.handlers.assets.ingestAsset.request_to_claims')
@patch('backend.backend.handlers.assets.ingestAsset.CasbinEnforcer')
@patch('backend.backend.handlers.assets.ingestAsset.verify_database_exists')
@patch('backend.backend.handlers.assets.ingestAsset.verify_asset_exists')
@patch('backend.backend.handlers.assets.ingestAsset.invoke_lambda')
@patch('backend.backend.handlers.assets.ingestAsset.update_metadata')
def test_complete_ingest_new_asset(mock_update_metadata, mock_invoke_lambda, 
                                  mock_verify_asset, mock_verify_database,
                                  mock_casbin_enforcer, mock_request_to_claims,
                                  complete_ingest_event, mock_env_vars):
    """Test completing an asset ingest operation with a new asset"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_verify_database.return_value = True
    mock_verify_asset.return_value = False  # Asset does not exist
    
    # Mock the create asset lambda response
    create_asset_response = {
        'statusCode': 200,
        'body': json.dumps({
            'assetId': 'test-asset-id',
            'message': 'Asset created successfully'
        })
    }
    
    # Mock the file upload lambda response
    file_upload_response = {
        'statusCode': 200,
        'body': json.dumps({
            'uploadId': 'test-upload-id',
            'assetId': 'test-asset-id',
            'fileResults': [
                {
                    'key': 'test-asset-id/file1.txt',
                    'uploadIdS3': 'test-upload-id-s3',
                    'success': True
                }
            ],
            'overallSuccess': True
        })
    }
    
    # Set up the invoke_lambda mock to return different responses based on which lambda is called
    def mock_invoke_lambda_side_effect(function_name, payload, invocation_type="RequestResponse"):
        if function_name == 'test-create-asset-lambda':
            return create_asset_response
        elif function_name == 'test-file-upload-lambda':
            return file_upload_response
        return None
    
    mock_invoke_lambda.side_effect = mock_invoke_lambda_side_effect
    
    mock_update_metadata.return_value = True
    
    # Execute
    response = lambda_handler(complete_ingest_event, {})
    
    # Assert
    assert response['statusCode'] == 200
    response_body = json.loads(response['body'])
    assert response_body['uploadId'] == 'test-upload-id'
    assert response_body['assetId'] == 'test-asset-id'
    assert response_body['overallSuccess'] == True
    
    # Verify both lambdas were called
    assert mock_invoke_lambda.call_count == 2
    
    # First call should be to create asset lambda
    first_call_args = mock_invoke_lambda.call_args_list[0][0]
    assert first_call_args[0] == 'test-create-asset-lambda'
    
    # Second call should be to file upload lambda
    second_call_args = mock_invoke_lambda.call_args_list[1][0]
    assert second_call_args[0] == 'test-file-upload-lambda'
    
    # Verify authorization was checked
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once()
    mock_casbin_enforcer_instance.enforce.assert_called_once()
    
    # Verify metadata was updated
    mock_update_metadata.assert_called_once_with('test-database-id', 'test-asset-id')

@patch('backend.backend.handlers.assets.ingestAsset.request_to_claims')
@patch('backend.backend.handlers.assets.ingestAsset.CasbinEnforcer')
def test_unauthorized_access(mock_casbin_enforcer, mock_request_to_claims,
                            initialize_ingest_event, mock_env_vars):
    """Test unauthorized access to ingest asset"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = False  # Unauthorized
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Execute
    response = lambda_handler(initialize_ingest_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Not Authorized'
    
    # Verify authorization was checked
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once()
    mock_casbin_enforcer_instance.enforce.assert_not_called()

@patch('backend.backend.handlers.assets.ingestAsset.request_to_claims')
@patch('backend.backend.handlers.assets.ingestAsset.CasbinEnforcer')
@patch('backend.backend.handlers.assets.ingestAsset.verify_database_exists')
def test_database_not_found(mock_verify_database, mock_casbin_enforcer, 
                           mock_request_to_claims, initialize_ingest_event, 
                           mock_env_vars):
    """Test database not found error"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    # Simulate database not found
    mock_verify_database.side_effect = Exception("Database not found")
    
    # Execute
    response = lambda_handler(initialize_ingest_event, {})
    
    # Assert
    assert response['statusCode'] == 500
    response_body = json.loads(response['body'])
    assert 'Internal Server Error' in response_body['message']
    
    # Verify authorization was checked
    mock_casbin_enforcer_instance.enforceAPI.assert_called_once()
    mock_casbin_enforcer_instance.enforce.assert_called_once()

@patch('backend.backend.handlers.assets.ingestAsset.request_to_claims')
@patch('backend.backend.handlers.assets.ingestAsset.CasbinEnforcer')
@patch('backend.backend.handlers.assets.ingestAsset.verify_database_exists')
@patch('backend.backend.handlers.assets.ingestAsset.invoke_lambda')
def test_file_upload_lambda_error(mock_invoke_lambda, mock_verify_database, 
                                 mock_casbin_enforcer, mock_request_to_claims,
                                 initialize_ingest_event, mock_env_vars):
    """Test error from file upload lambda"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-token"]}
    
    mock_casbin_enforcer_instance = MagicMock()
    mock_casbin_enforcer_instance.enforceAPI.return_value = True
    mock_casbin_enforcer_instance.enforce.return_value = True
    mock_casbin_enforcer.return_value = mock_casbin_enforcer_instance
    
    mock_verify_database.return_value = True
    
    # Mock the file upload lambda error response
    mock_invoke_lambda.return_value = {
        'statusCode': 400,
        'body': json.dumps({
            'message': 'Error initializing upload'
        })
    }
    
    # Execute
    response = lambda_handler(initialize_ingest_event, {})
    
    # Assert
    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert 'Error initializing upload' in response_body['message']
    
    # Verify the correct lambda was called
    mock_invoke_lambda.assert_called_once()
    args, kwargs = mock_invoke_lambda.call_args
    assert args[0] == 'test-file-upload-lambda'
