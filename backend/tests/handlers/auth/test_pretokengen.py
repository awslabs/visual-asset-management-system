# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from backend.tests.mocks.handlers.auth.pretokengenv1 import lambda_handler as lambda_handler_v1
from backend.backend.handlers.auth.pretokengenv2 import lambda_handler as lambda_handler_v2


@pytest.fixture
def valid_event():
    """Create a valid event for pre-token generation"""
    return {
        'userName': 'test-user-id',
        'request': {
            'userAttributes': {
                'email': 'test@example.com'
            }
        },
        'response': {}
    }


@pytest.fixture
def no_roles_event():
    """Create an event for a user with no roles"""
    return {
        'userName': 'user-with-no-roles',
        'request': {
            'userAttributes': {
                'email': 'no-roles@example.com'
            }
        },
        'response': {}
    }


@patch('backend.backend.handlers.auth.pretokengenv1.userRoleTable')
@patch('backend.backend.handlers.auth.pretokengenv1.authEntTable')
def test_pretokengenv1_success(mock_auth_table, mock_user_role_table, valid_event):
    """Test successful pre-token generation with v1"""
    # Setup mocks
    mock_user_role_table.query.return_value = {
        'Items': [
            {
                'userId': 'test-user-id',
                'roleName': 'admin'
            },
            {
                'userId': 'test-user-id',
                'roleName': 'user'
            }
        ]
    }
    
    # Execute
    response = lambda_handler_v1(valid_event, {})
    
    # Assert
    assert 'response' in response
    assert 'claimsOverrideDetails' in response['response']
    assert 'claimsToAddOrOverride' in response['response']['claimsOverrideDetails']
    
    claims = response['response']['claimsOverrideDetails']['claimsToAddOrOverride']
    assert 'vams:roles' in claims
    assert 'vams:tokens' in claims
    assert 'vams:externalAttributes' in claims
    
    roles = json.loads(claims['vams:roles'])
    tokens = json.loads(claims['vams:tokens'])
    
    assert 'admin' in roles
    assert 'user' in roles
    assert 'test-user-id' in tokens
    
    # Verify the correct methods were called
    mock_user_role_table.query.assert_called_once()
    mock_auth_table.update_item.assert_called_once()


@patch('backend.backend.handlers.auth.pretokengenv2.userRoleTable')
@patch('backend.backend.handlers.auth.pretokengenv2.authEntTable')
def test_pretokengenv2_success(mock_auth_table, mock_user_role_table, valid_event):
    """Test successful pre-token generation with v2"""
    # Setup mocks
    mock_user_role_table.query.return_value = {
        'Items': [
            {
                'userId': 'test-user-id',
                'roleName': 'admin'
            },
            {
                'userId': 'test-user-id',
                'roleName': 'user'
            }
        ]
    }
    
    # Execute
    response = lambda_handler_v2(valid_event, {})
    
    # Assert
    assert 'response' in response
    assert 'claimsAndScopeOverrideDetails' in response['response']
    assert 'idTokenGeneration' in response['response']['claimsAndScopeOverrideDetails']
    assert 'accessTokenGeneration' in response['response']['claimsAndScopeOverrideDetails']
    
    id_claims = response['response']['claimsAndScopeOverrideDetails']['idTokenGeneration']['claimsToAddOrOverride']
    access_claims = response['response']['claimsAndScopeOverrideDetails']['accessTokenGeneration']['claimsToAddOrOverride']
    
    assert 'vams:roles' in id_claims
    assert 'vams:tokens' in id_claims
    assert 'vams:externalAttributes' in id_claims
    
    assert 'vams:roles' in access_claims
    assert 'vams:tokens' in access_claims
    assert 'vams:externalAttributes' in access_claims
    
    id_roles = json.loads(id_claims['vams:roles'])
    id_tokens = json.loads(id_claims['vams:tokens'])
    
    access_roles = json.loads(access_claims['vams:roles'])
    access_tokens = json.loads(access_claims['vams:tokens'])
    
    assert 'admin' in id_roles
    assert 'user' in id_roles
    assert 'test-user-id' in id_tokens
    
    assert 'admin' in access_roles
    assert 'user' in access_roles
    assert 'test-user-id' in access_tokens
    
    # Verify the correct methods were called
    mock_user_role_table.query.assert_called_once()
    mock_auth_table.update_item.assert_called_once()


@patch('backend.backend.handlers.auth.pretokengenv1.userRoleTable')
@patch('backend.backend.handlers.auth.pretokengenv1.authEntTable')
def test_pretokengenv1_no_roles(mock_auth_table, mock_user_role_table, no_roles_event):
    """Test pre-token generation with v1 for user with no roles"""
    # Setup mocks
    mock_user_role_table.query.return_value = {
        'Items': []  # No roles
    }
    
    # Execute
    response = lambda_handler_v1(no_roles_event, {})
    
    # Assert
    assert 'response' in response
    assert 'claimsOverrideDetails' in response['response']
    assert 'claimsToAddOrOverride' in response['response']['claimsOverrideDetails']
    
    claims = response['response']['claimsOverrideDetails']['claimsToAddOrOverride']
    assert 'vams:roles' in claims
    assert 'vams:tokens' in claims
    assert 'vams:externalAttributes' in claims
    
    roles = json.loads(claims['vams:roles'])
    tokens = json.loads(claims['vams:tokens'])
    
    assert len(roles) == 0  # No roles
    assert 'user-with-no-roles' in tokens
    
    # Verify the correct methods were called
    mock_user_role_table.query.assert_called_once()
    # No update_item call since there are no roles to save
    mock_auth_table.update_item.assert_not_called()


@patch('backend.backend.handlers.auth.pretokengenv2.userRoleTable')
@patch('backend.backend.handlers.auth.pretokengenv2.authEntTable')
def test_pretokengenv2_no_roles(mock_auth_table, mock_user_role_table, no_roles_event):
    """Test pre-token generation with v2 for user with no roles"""
    # Setup mocks
    mock_user_role_table.query.return_value = {
        'Items': []  # No roles
    }
    
    # Execute
    response = lambda_handler_v2(no_roles_event, {})
    
    # Assert
    assert 'response' in response
    assert 'claimsAndScopeOverrideDetails' in response['response']
    assert 'idTokenGeneration' in response['response']['claimsAndScopeOverrideDetails']
    assert 'accessTokenGeneration' in response['response']['claimsAndScopeOverrideDetails']
    
    id_claims = response['response']['claimsAndScopeOverrideDetails']['idTokenGeneration']['claimsToAddOrOverride']
    access_claims = response['response']['claimsAndScopeOverrideDetails']['accessTokenGeneration']['claimsToAddOrOverride']
    
    assert 'vams:roles' in id_claims
    assert 'vams:tokens' in id_claims
    assert 'vams:externalAttributes' in id_claims
    
    assert 'vams:roles' in access_claims
    assert 'vams:tokens' in access_claims
    assert 'vams:externalAttributes' in access_claims
    
    id_roles = json.loads(id_claims['vams:roles'])
    id_tokens = json.loads(id_claims['vams:tokens'])
    
    access_roles = json.loads(access_claims['vams:roles'])
    access_tokens = json.loads(access_claims['vams:tokens'])
    
    assert len(id_roles) == 0  # No roles
    assert 'user-with-no-roles' in id_tokens
    
    assert len(access_roles) == 0  # No roles
    assert 'user-with-no-roles' in access_tokens
    
    # Verify the correct methods were called
    mock_user_role_table.query.assert_called_once()
    # No update_item call since there are no roles to save
    mock_auth_table.update_item.assert_not_called()


@patch('backend.backend.handlers.auth.pretokengenv1.userRoleTable')
def test_pretokengenv1_query_error(mock_user_role_table, valid_event):
    """Test handling of query error in v1"""
    # Setup mocks
    mock_user_role_table.query.side_effect = Exception("Test exception")
    
    # Execute
    response = lambda_handler_v1(valid_event, {})
    
    # Assert
    assert 'response' in response
    assert 'claimsOverrideDetails' in response['response']
    assert 'claimsToAddOrOverride' in response['response']['claimsOverrideDetails']
    
    claims = response['response']['claimsOverrideDetails']['claimsToAddOrOverride']
    assert 'vams:roles' in claims
    assert 'vams:tokens' in claims
    assert 'vams:externalAttributes' in claims
    
    roles = json.loads(claims['vams:roles'])
    tokens = json.loads(claims['vams:tokens'])
    
    assert len(roles) == 0  # No roles due to error
    assert 'test-user-id' in tokens
    
    # Verify the correct methods were called
    mock_user_role_table.query.assert_called_once()


@patch('backend.backend.handlers.auth.pretokengenv2.userRoleTable')
def test_pretokengenv2_query_error(mock_user_role_table, valid_event):
    """Test handling of query error in v2"""
    # Setup mocks
    mock_user_role_table.query.side_effect = Exception("Test exception")
    
    # Execute
    response = lambda_handler_v2(valid_event, {})
    
    # Assert
    assert 'response' in response
    assert 'claimsAndScopeOverrideDetails' in response['response']
    assert 'idTokenGeneration' in response['response']['claimsAndScopeOverrideDetails']
    assert 'accessTokenGeneration' in response['response']['claimsAndScopeOverrideDetails']
    
    id_claims = response['response']['claimsAndScopeOverrideDetails']['idTokenGeneration']['claimsToAddOrOverride']
    access_claims = response['response']['claimsAndScopeOverrideDetails']['accessTokenGeneration']['claimsToAddOrOverride']
    
    assert 'vams:roles' in id_claims
    assert 'vams:tokens' in id_claims
    assert 'vams:externalAttributes' in id_claims
    
    assert 'vams:roles' in access_claims
    assert 'vams:tokens' in access_claims
    assert 'vams:externalAttributes' in access_claims
    
    id_roles = json.loads(id_claims['vams:roles'])
    id_tokens = json.loads(id_claims['vams:tokens'])
    
    access_roles = json.loads(access_claims['vams:roles'])
    access_tokens = json.loads(access_claims['vams:tokens'])
    
    assert len(id_roles) == 0  # No roles due to error
    assert 'test-user-id' in id_tokens
    
    assert len(access_roles) == 0  # No roles due to error
    assert 'test-user-id' in access_tokens
    
    # Verify the correct methods were called
    mock_user_role_table.query.assert_called_once()
