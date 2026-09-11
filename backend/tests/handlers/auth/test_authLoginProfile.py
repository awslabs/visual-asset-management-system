# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.auth.authLoginProfile import lambda_handler


@pytest.fixture
def create_user_event():
    """Create a valid API Gateway event for creating a user"""
    return {
        'requestContext': {
            'http': {
                'method': 'POST'
            }
        },
        'pathParameters': {
            'userId': 'test-user-id'
        },
        'body': json.dumps({
            'email': 'test@example.com'
        }),
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def get_user_event():
    """Create a valid API Gateway event for getting a user"""
    return {
        'requestContext': {
            'http': {
                'method': 'GET'
            }
        },
        'pathParameters': {
            'userId': 'test-user-id'
        },
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_user_id_event():
    """Create an invalid API Gateway event with invalid user ID"""
    return {
        'requestContext': {
            'http': {
                'method': 'POST'
            }
        },
        'pathParameters': {
            'userId': ''  # Empty user ID
        },
        'body': json.dumps({
            'email': 'test@example.com'
        }),
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def invalid_email_event():
    """Create an invalid API Gateway event with invalid email"""
    return {
        'requestContext': {
            'http': {
                'method': 'POST'
            }
        },
        'pathParameters': {
            'userId': 'test-user-id'
        },
        'body': json.dumps({
            'email': 'invalid-email'
        }),
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@pytest.fixture
def unauthorized_event():
    """Create an API Gateway event with mismatched user ID"""
    return {
        'requestContext': {
            'http': {
                'method': 'POST'
            }
        },
        'pathParameters': {
            'userId': 'different-user-id'
        },
        'body': json.dumps({
            'email': 'test@example.com'
        }),
        'headers': {
            'Authorization': 'Bearer test-token'
        }
    }


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.customAuthProfileLoginWriteOverride')
def test_create_user_success(mock_custom_override, mock_user_table, mock_request_to_claims, create_user_event):
    """Test successful creation of a user"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}

    # Mock the custom override function to return the original profile
    mock_custom_override.return_value = {
        'userId': 'test-user-id',
        'email': 'test@example.com'
    }

    # Execute
    response = lambda_handler(create_user_event, {})

    # Assert
    assert response['statusCode'] == 200
    body = json.loads(response['body'])
    assert body['userId'] == 'test-user-id'
    assert body['email'] == 'test@example.com'

    # Verify the correct table methods were called
    mock_user_table.put_item.assert_called_once_with(
        Item={
            'userId': 'test-user-id',
            'email': 'test@example.com'
        }
    )

    # Verify the custom override function was called
    mock_custom_override.assert_called_once()


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.customAuthProfileLoginWriteOverride')
def test_create_user_with_custom_override(mock_custom_override, mock_user_table, mock_request_to_claims, create_user_event):
    """Test creation of a user with custom profile override"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}

    # Mock the custom override function to add additional fields
    mock_custom_override.return_value = {
        'userId': 'test-user-id',
        'email': 'test@example.com',
        'displayName': 'Test User',
        'role': 'user'
    }

    # Execute
    response = lambda_handler(create_user_event, {})

    # Assert
    assert response['statusCode'] == 200
    body = json.loads(response['body'])
    assert body['userId'] == 'test-user-id'
    assert body['email'] == 'test@example.com'
    assert body['displayName'] == 'Test User'
    assert body['role'] == 'user'

    # Verify the correct table methods were called
    mock_user_table.put_item.assert_called_once_with(
        Item={
            'userId': 'test-user-id',
            'email': 'test@example.com',
            'displayName': 'Test User',
            'role': 'user'
        }
    )

    # Verify the custom override function was called
    mock_custom_override.assert_called_once()


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
def test_get_user_success(mock_user_table, mock_request_to_claims, get_user_event):
    """Test successful retrieval of a user"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}

    mock_user_table.get_item.return_value = {
        'Item': {
            'userId': 'test-user-id',
            'email': 'test@example.com',
            'displayName': 'Test User'
        }
    }

    # Execute
    response = lambda_handler(get_user_event, {})

    # Assert
    assert response['statusCode'] == 200
    body = json.loads(response['body'])
    assert body['userId'] == 'test-user-id'
    assert body['email'] == 'test@example.com'
    assert body['displayName'] == 'Test User'

    # Verify the correct table methods were called
    mock_user_table.get_item.assert_called_once_with(
        Key={
            'userId': 'test-user-id'
        }
    )


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.validate')
def test_invalid_user_id(mock_validate, mock_request_to_claims, invalid_user_id_event):
    """An invalid path userId is rejected with a 400 before any routing."""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}
    # The shared test harness stubs validate() to always pass; force the failure path here.
    mock_validate.return_value = (False, "userId is invalid")

    # Execute
    response = lambda_handler(invalid_user_id_event, {})

    # Assert
    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert 'message' in response_body
    assert 'userid' in response_body['message'].lower()  # Error message should mention userId


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('models.authLoginProfile.validate')
def test_invalid_email(mock_model_validate, mock_request_to_claims, invalid_email_event):
    """An invalid POST body email is rejected with a 400 from model validation."""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}
    # The shared test harness stubs validate() to always pass; force the failure path
    # in the request-body model's root_validator (the handler imports the model as
    # `models.authLoginProfile`, so patch that module identity).
    mock_model_validate.return_value = (False, "email is invalid")

    # Execute
    response = lambda_handler(invalid_email_event, {})

    # Assert
    assert response['statusCode'] == 400
    response_body = json.loads(response['body'])
    assert 'message' in response_body
    assert 'email' in response_body['message'].lower()  # Error message should mention email


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
def test_unauthorized_access(mock_request_to_claims, unauthorized_event):
    """Test unauthorized access (mismatched user IDs)"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}  # Different from the one in the path
    
    # Execute
    response = lambda_handler(unauthorized_event, {})
    
    # Assert
    assert response['statusCode'] == 403
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Not Authorized'


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
def test_get_user_no_profile_returns_identity(mock_user_table, mock_request_to_claims, get_user_event):
    """A user with no stored profile row (e.g. assigned no roles) must still get a
    200 with an identity-only profile body, not a 500 from a missing 'Item' key."""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}
    # DynamoDB get_item omits 'Item' entirely when the key is not found
    mock_user_table.get_item.return_value = {'ResponseMetadata': {'HTTPStatusCode': 200}}

    # Execute
    response = lambda_handler(get_user_event, {})

    # Assert
    assert response['statusCode'] == 200
    body = json.loads(response['body'])
    assert body['userId'] == 'test-user-id'
    assert body.get('email') in (None, '')


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
def test_get_user_with_profile_returns_flat_body(mock_user_table, mock_request_to_claims, get_user_event):
    """GET for a stored profile returns the profile entity directly, including
    organization-custom fields."""
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}
    mock_user_table.get_item.return_value = {
        'Item': {'userId': 'test-user-id', 'email': 'test@example.com', 'displayName': 'Test User'}
    }

    response = lambda_handler(get_user_event, {})

    assert response['statusCode'] == 200
    body = json.loads(response['body'])
    assert body['userId'] == 'test-user-id'
    assert body['email'] == 'test@example.com'
    assert body['displayName'] == 'Test User'  # org-custom field preserved


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
def test_internal_server_error(mock_user_table, mock_request_to_claims, create_user_event):
    """Test handling of internal server error"""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}

    # Simulate an error during the DynamoDB write
    mock_user_table.put_item.side_effect = Exception("Test exception")

    # Execute
    response = lambda_handler(create_user_event, {})

    # Assert
    assert response['statusCode'] == 500
    response_body = json.loads(response['body'])
    assert response_body['message'] == 'Internal Server Error'


@patch('backend.backend.handlers.auth.authLoginProfile.request_to_claims')
@patch('backend.backend.handlers.auth.authLoginProfile.user_table')
@patch('backend.backend.handlers.auth.authLoginProfile.customAuthProfileLoginWriteOverride')
def test_custom_override_returns_none(mock_custom_override, mock_user_table, mock_request_to_claims, create_user_event):
    """When the org override returns None, the handler falls back to the base profile."""
    # Setup mocks
    mock_request_to_claims.return_value = {"tokens": ["test-user-id"]}

    # Mock the custom override function to return None
    mock_custom_override.return_value = None

    # Execute
    response = lambda_handler(create_user_event, {})

    # Assert
    assert response['statusCode'] == 200
    body = json.loads(response['body'])
    assert body['userId'] == 'test-user-id'
    assert body['email'] == 'test@example.com'

    # Verify the original profile was used
    mock_user_table.put_item.assert_called_once()
    put_item_args = mock_user_table.put_item.call_args[1]
    assert 'Item' in put_item_args
    assert put_item_args['Item']['userId'] == 'test-user-id'
    assert put_item_args['Item']['email'] == 'test@example.com'


def test_update_login_profile_model_accepts_optional_email():
    """The POST body model accepts no email or a valid email."""
    from backend.backend.models.authLoginProfile import UpdateLoginProfileRequestModel
    m = UpdateLoginProfileRequestModel()
    assert m.email is None
    m2 = UpdateLoginProfileRequestModel(email="user@example.com")
    assert m2.email == "user@example.com"
