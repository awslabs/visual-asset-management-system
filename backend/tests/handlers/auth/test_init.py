# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import patch, MagicMock

from backend.backend.handlers.auth import request_to_claims


@pytest.fixture
def event_with_vams_claims():
    """Create an event with vams claims"""
    return {
        'requestContext': {
            'authorizer': {
                'jwt': {
                    'claims': {
                        'vams:tokens': json.dumps(['test-user-id']),
                        'vams:roles': json.dumps(['admin', 'user']),
                        'vams:externalAttributes': json.dumps(['attr1', 'attr2'])
                    }
                }
            }
        }
    }


@pytest.fixture
def event_with_cognito_username():
    """Create an event with cognito username"""
    return {
        'requestContext': {
            'authorizer': {
                'jwt': {
                    'claims': {
                        'cognito:username': 'test-user-id'
                    }
                }
            }
        }
    }


@pytest.fixture
def event_with_username():
    """Create an event with username"""
    return {
        'requestContext': {
            'authorizer': {
                'jwt': {
                    'claims': {
                        'username': 'test-user-id'
                    }
                }
            }
        }
    }


@pytest.fixture
def event_with_sub():
    """Create an event with sub"""
    return {
        'requestContext': {
            'authorizer': {
                'jwt': {
                    'claims': {
                        'sub': 'test-user-id'
                    }
                }
            }
        }
    }


@pytest.fixture
def event_with_upn():
    """Create an event with upn"""
    return {
        'requestContext': {
            'authorizer': {
                'jwt': {
                    'claims': {
                        'upn': 'test-user-id'
                    }
                }
            }
        }
    }


@pytest.fixture
def event_with_email():
    """Create an event with email"""
    return {
        'requestContext': {
            'authorizer': {
                'jwt': {
                    'claims': {
                        'email': 'test@example.com'
                    }
                }
            }
        }
    }


@pytest.fixture
def event_without_requestContext():
    """Create an event without requestContext"""
    return {
        'body': 'test-body'
    }


@patch('backend.backend.handlers.auth.customAuthClaimsCheckOverride')
def test_request_to_claims_with_vams_claims(mock_custom_override, event_with_vams_claims):
    """Test request_to_claims with vams claims"""
    # Setup mocks
    mock_custom_override.return_value = {
        "tokens": ["test-user-id"],
        "roles": ["admin", "user"],
        "externalAttributes": ["attr1", "attr2"],
        "mfaEnabled": True
    }
    
    # Execute
    result = request_to_claims(event_with_vams_claims)
    
    # Assert
    assert result["tokens"] == ["test-user-id"]
    assert result["roles"] == ["admin", "user"]
    assert result["externalAttributes"] == ["attr1", "attr2"]
    assert result["mfaEnabled"] == True
    
    # Verify the correct methods were called
    mock_custom_override.assert_called_once_with({
        "tokens": ["test-user-id"],
        "roles": ["admin", "user"],
        "externalAttributes": ["attr1", "attr2"],
        "mfaEnabled": False
    }, event_with_vams_claims)


@patch('backend.backend.handlers.auth.customAuthClaimsCheckOverride')
def test_request_to_claims_with_cognito_username(mock_custom_override, event_with_cognito_username):
    """Test request_to_claims with cognito username"""
    # Setup mocks
    mock_custom_override.return_value = {
        "tokens": ["test-user-id"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }
    
    # Execute
    result = request_to_claims(event_with_cognito_username)
    
    # Assert
    assert result["tokens"] == ["test-user-id"]
    assert result["roles"] == []
    assert result["externalAttributes"] == []
    assert result["mfaEnabled"] == False
    
    # Verify the correct methods were called
    mock_custom_override.assert_called_once_with({
        "tokens": ["test-user-id"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }, event_with_cognito_username)


@patch('backend.backend.handlers.auth.customAuthClaimsCheckOverride')
def test_request_to_claims_with_username(mock_custom_override, event_with_username):
    """Test request_to_claims with username"""
    # Setup mocks
    mock_custom_override.return_value = {
        "tokens": ["test-user-id"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }
    
    # Execute
    result = request_to_claims(event_with_username)
    
    # Assert
    assert result["tokens"] == ["test-user-id"]
    assert result["roles"] == []
    assert result["externalAttributes"] == []
    assert result["mfaEnabled"] == False
    
    # Verify the correct methods were called
    mock_custom_override.assert_called_once_with({
        "tokens": ["test-user-id"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }, event_with_username)


@patch('backend.backend.handlers.auth.customAuthClaimsCheckOverride')
def test_request_to_claims_with_sub(mock_custom_override, event_with_sub):
    """Test request_to_claims with sub"""
    # Setup mocks
    mock_custom_override.return_value = {
        "tokens": ["test-user-id"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }
    
    # Execute
    result = request_to_claims(event_with_sub)
    
    # Assert
    assert result["tokens"] == ["test-user-id"]
    assert result["roles"] == []
    assert result["externalAttributes"] == []
    assert result["mfaEnabled"] == False
    
    # Verify the correct methods were called
    mock_custom_override.assert_called_once_with({
        "tokens": ["test-user-id"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }, event_with_sub)


@patch('backend.backend.handlers.auth.customAuthClaimsCheckOverride')
def test_request_to_claims_with_upn(mock_custom_override, event_with_upn):
    """Test request_to_claims with upn"""
    # Setup mocks
    mock_custom_override.return_value = {
        "tokens": ["test-user-id"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }
    
    # Execute
    result = request_to_claims(event_with_upn)
    
    # Assert
    assert result["tokens"] == ["test-user-id"]
    assert result["roles"] == []
    assert result["externalAttributes"] == []
    assert result["mfaEnabled"] == False
    
    # Verify the correct methods were called
    mock_custom_override.assert_called_once_with({
        "tokens": ["test-user-id"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }, event_with_upn)


@patch('backend.backend.handlers.auth.customAuthClaimsCheckOverride')
def test_request_to_claims_with_email(mock_custom_override, event_with_email):
    """Test request_to_claims with email"""
    # Setup mocks
    mock_custom_override.return_value = {
        "tokens": ["test@example.com"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }
    
    # Execute
    result = request_to_claims(event_with_email)
    
    # Assert
    assert result["tokens"] == ["test@example.com"]
    assert result["roles"] == []
    assert result["externalAttributes"] == []
    assert result["mfaEnabled"] == False
    
    # Verify the correct methods were called
    mock_custom_override.assert_called_once_with({
        "tokens": ["test@example.com"],
        "roles": [],
        "externalAttributes": [],
        "mfaEnabled": False
    }, event_with_email)


@patch('backend.backend.handlers.auth.customAuthClaimsCheckOverride')
def test_request_to_claims_without_requestContext(mock_custom_override, event_without_requestContext):
    """Test request_to_claims without requestContext"""
    # Setup mocks
    mock_custom_override.side_effect = lambda x, y: x  # Just return the input
    
    # Execute
    result = request_to_claims(event_without_requestContext)
    
    # Assert
    assert result["tokens"] == []
    assert result["roles"] == []
    assert result["externalAttributes"] == []
    assert result["mfaEnabled"] == False


@patch('backend.backend.handlers.auth.customAuthClaimsCheckOverride')
def test_request_to_claims_with_custom_override_exception(mock_custom_override, event_with_vams_claims):
    """Test request_to_claims with custom override exception"""
    # Setup mocks
    mock_custom_override.side_effect = Exception("Test exception")
    
    # Execute
    result = request_to_claims(event_with_vams_claims)
    
    # Assert
    assert result["tokens"] == ["test-user-id"]
    assert result["roles"] == ["admin", "user"]
    assert result["externalAttributes"] == ["attr1", "attr2"]
    assert result["mfaEnabled"] == False
    
    # Verify the correct methods were called
    mock_custom_override.assert_called_once()
