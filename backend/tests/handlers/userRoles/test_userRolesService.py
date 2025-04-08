import pytest
import json
import boto3
from unittest.mock import patch, MagicMock
import datetime

@pytest.fixture(scope="function")
def get_user_roles_event():
    """
    Generates an event for getting user roles
    
    Returns:
        dict: Lambda event dictionary for getting user roles
    """
    return {
        "requestContext": {
            "http": {
                "method": "GET"
            }
        },
        "queryStringParameters": {
            "maxItems": "10",
            "pageSize": "10",
            "startingToken": ""
        }
    }

@pytest.fixture(scope="function")
def create_user_roles_event():
    """
    Generates an event for creating user roles
    
    Returns:
        dict: Lambda event dictionary for creating user roles
    """
    return {
        "requestContext": {
            "http": {
                "method": "POST"
            }
        },
        "body": json.dumps({
            "userId": "test-user-id",
            "roleName": ["admin", "editor"]
        })
    }

@pytest.fixture(scope="function")
def update_user_roles_event():
    """
    Generates an event for updating user roles
    
    Returns:
        dict: Lambda event dictionary for updating user roles
    """
    return {
        "requestContext": {
            "http": {
                "method": "PUT"
            }
        },
        "body": json.dumps({
            "userId": "test-user-id",
            "roleName": ["viewer"]  # Changed roles
        })
    }

@pytest.fixture(scope="function")
def delete_user_roles_event():
    """
    Generates an event for deleting user roles
    
    Returns:
        dict: Lambda event dictionary for deleting user roles
    """
    return {
        "requestContext": {
            "http": {
                "method": "DELETE"
            }
        },
        "body": json.dumps({
            "userId": "test-user-id"
        })
    }

@pytest.fixture(scope="function")
def mock_claims_and_roles():
    """
    Mock for claims and roles
    
    Returns:
        dict: Mock claims and roles
    """
    return {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }

@pytest.fixture(scope="function")
def mock_casbin_enforcer():
    """
    Mock for CasbinEnforcer
    
    Returns:
        MagicMock: Mock for CasbinEnforcer
    """
    mock_enforcer = MagicMock()
    mock_enforcer.enforce.return_value = True
    mock_enforcer.enforceAPI.return_value = True
    return mock_enforcer

@pytest.fixture(scope="function")
def mock_dynamodb_paginator():
    """
    Mock for DynamoDB paginator
    
    Returns:
        MagicMock: Mock for DynamoDB paginator
    """
    mock_paginator = MagicMock()
    
    # Mock the paginate method to return a result with items
    mock_paginate = MagicMock()
    mock_paginate.build_full_result.return_value = {
        "Items": [
            {
                "userId": {"S": "test-user-id"},
                "roleName": {"S": "admin"},
                "createdOn": {"S": "2023-07-06T21:32:15.066148"}
            },
            {
                "userId": {"S": "test-user-id"},
                "roleName": {"S": "editor"},
                "createdOn": {"S": "2023-07-06T21:32:15.066148"}
            },
            {
                "userId": {"S": "test-user-id-2"},
                "roleName": {"S": "viewer"},
                "createdOn": {"S": "2023-07-06T21:32:15.066148"}
            }
        ]
    }
    
    mock_paginator.paginate.return_value = mock_paginate
    return mock_paginator

@pytest.fixture(scope="function")
def mock_dynamodb_query():
    """
    Mock for DynamoDB query operation
    
    Returns:
        MagicMock: Mock for DynamoDB query
    """
    mock_query = MagicMock()
    mock_query.return_value = {
        "Items": [
            {
                "userId": {"S": "test-user-id"},
                "roleName": {"S": "admin"},
                "createdOn": {"S": "2023-07-06T21:32:15.066148"}
            },
            {
                "userId": {"S": "test-user-id"},
                "roleName": {"S": "editor"},
                "createdOn": {"S": "2023-07-06T21:32:15.066148"}
            }
        ]
    }
    return mock_query

@pytest.fixture(scope="function")
def mock_user_roles_table():
    """
    Mock for user roles table
    
    Returns:
        MagicMock: Mock for user roles table
    """
    mock_table = MagicMock()
    mock_batch_writer = MagicMock()
    mock_table.batch_writer.return_value.__enter__.return_value = mock_batch_writer
    return mock_table, mock_batch_writer

def test_get_user_roles(get_user_roles_event, mock_dynamodb_paginator, mock_casbin_enforcer, monkeypatch):
    """
    Test the userRolesService lambda handler with a GET request
    
    Args:
        get_user_roles_event: Lambda event dictionary for getting user roles
        mock_dynamodb_paginator: Mock for DynamoDB paginator
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ROLES_TABLE_NAME", "test-roles-table")
    monkeypatch.setenv("USER_ROLES_TABLE_NAME", "test-user-roles-table")
    
    # Mock boto3 clients and resources
    mock_dynamodb_client = MagicMock()
    mock_dynamodb_client.get_paginator.return_value = mock_dynamodb_paginator
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Patch the imports
    with patch("boto3.client", return_value=mock_dynamodb_client), \
         patch("boto3.resource"), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.dynamodb.validate_pagination_info"):
        
        # Import the module here to ensure our mocks are in place
        from handlers.userRoles import userRolesService
        
        # Call the lambda handler
        response = userRolesService.lambda_handler(get_user_roles_event, None)
        
        # Verify the response
        assert response["statusCode"] == 200
        message = json.loads(response["body"])["message"]
        assert "Items" in message
        assert len(message["Items"]) > 0
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called()

def test_create_user_roles(create_user_roles_event, mock_dynamodb_query, mock_casbin_enforcer, mock_user_roles_table, monkeypatch):
    """
    Test the userRolesService lambda handler with a POST request
    
    Args:
        create_user_roles_event: Lambda event dictionary for creating user roles
        mock_dynamodb_query: Mock for DynamoDB query
        mock_casbin_enforcer: Mock for CasbinEnforcer
        mock_user_roles_table: Mock for user roles table
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ROLES_TABLE_NAME", "test-roles-table")
    monkeypatch.setenv("USER_ROLES_TABLE_NAME", "test-user-roles-table")
    
    # Unpack the mock_user_roles_table fixture
    mock_table, mock_batch_writer = mock_user_roles_table
    
    # Mock boto3 clients and resources
    mock_dynamodb_client = MagicMock()
    mock_dynamodb_client.query = mock_dynamodb_query
    
    # For the first call (checking existing user roles), return empty list
    # For the second call (checking if role exists), return a role
    mock_dynamodb_query.side_effect = [
        {"Items": []},  # No existing user roles
        {"Items": [{"roleName": {"S": "admin"}}]},  # Role "admin" exists
        {"Items": [{"roleName": {"S": "editor"}}]}  # Role "editor" exists
    ]
    
    mock_dynamodb = MagicMock()
    mock_dynamodb.Table.return_value = mock_table
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Mock datetime.datetime.now() to return a fixed date
    mock_now = MagicMock()
    mock_now.strftime.return_value = "2023-07-06T21:32:15.066148"
    
    # Patch the imports
    with patch("boto3.client", return_value=mock_dynamodb_client), \
         patch("boto3.resource", return_value=mock_dynamodb), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.validators.validate", return_value=(True, "")), \
         patch("datetime.datetime") as mock_datetime:
        
        # Set up the mock datetime
        mock_datetime.now.return_value = mock_now
        
        # Import the module here to ensure our mocks are in place
        from handlers.userRoles import userRolesService
        
        # Call the lambda handler
        response = userRolesService.lambda_handler(create_user_roles_event, None)
        
        # Verify the response
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "success"
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called()
        
        # Verify batch writer was called to create user roles
        assert mock_batch_writer.put_item.call_count == 2  # Two roles to create

def test_update_user_roles(update_user_roles_event, mock_dynamodb_query, mock_casbin_enforcer, mock_user_roles_table, monkeypatch):
    """
    Test the userRolesService lambda handler with a PUT request
    
    Args:
        update_user_roles_event: Lambda event dictionary for updating user roles
        mock_dynamodb_query: Mock for DynamoDB query
        mock_casbin_enforcer: Mock for CasbinEnforcer
        mock_user_roles_table: Mock for user roles table
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ROLES_TABLE_NAME", "test-roles-table")
    monkeypatch.setenv("USER_ROLES_TABLE_NAME", "test-user-roles-table")
    
    # Unpack the mock_user_roles_table fixture
    mock_table, mock_batch_writer = mock_user_roles_table
    
    # Mock boto3 clients and resources
    mock_dynamodb_client = MagicMock()
    mock_dynamodb_client.query = mock_dynamodb_query
    
    # For the first call (getting existing user roles), return admin and editor roles
    # For the second call (checking if viewer role exists), return a role
    mock_dynamodb_query.side_effect = [
        {
            "Items": [
                {"userId": {"S": "test-user-id"}, "roleName": {"S": "admin"}},
                {"userId": {"S": "test-user-id"}, "roleName": {"S": "editor"}}
            ]
        },
        {"Items": [{"roleName": {"S": "viewer"}}]}  # Role "viewer" exists
    ]
    
    mock_dynamodb = MagicMock()
    mock_dynamodb.Table.return_value = mock_table
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Mock datetime.datetime.now() to return a fixed date
    mock_now = MagicMock()
    mock_now.strftime.return_value = "2023-07-06T21:32:15.066148"
    
    # Patch the imports
    with patch("boto3.client", return_value=mock_dynamodb_client), \
         patch("boto3.resource", return_value=mock_dynamodb), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.validators.validate", return_value=(True, "")), \
         patch("datetime.datetime") as mock_datetime:
        
        # Set up the mock datetime
        mock_datetime.now.return_value = mock_now
        
        # Import the module here to ensure our mocks are in place
        from handlers.userRoles import userRolesService
        
        # Call the lambda handler
        response = userRolesService.lambda_handler(update_user_roles_event, None)
        
        # Verify the response
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "success"
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called()
        
        # Verify batch writer was called to delete old roles and create new ones
        assert mock_batch_writer.delete_item.call_count == 2  # Two roles to delete
        assert mock_batch_writer.put_item.call_count == 1  # One role to create

def test_delete_user_roles(delete_user_roles_event, mock_dynamodb_query, mock_casbin_enforcer, mock_user_roles_table, monkeypatch):
    """
    Test the userRolesService lambda handler with a DELETE request
    
    Args:
        delete_user_roles_event: Lambda event dictionary for deleting user roles
        mock_dynamodb_query: Mock for DynamoDB query
        mock_casbin_enforcer: Mock for CasbinEnforcer
        mock_user_roles_table: Mock for user roles table
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ROLES_TABLE_NAME", "test-roles-table")
    monkeypatch.setenv("USER_ROLES_TABLE_NAME", "test-user-roles-table")
    
    # Unpack the mock_user_roles_table fixture
    mock_table, mock_batch_writer = mock_user_roles_table
    
    # Mock boto3 clients and resources
    mock_dynamodb_client = MagicMock()
    mock_dynamodb_client.query = mock_dynamodb_query
    
    # Return admin and editor roles for the user
    mock_dynamodb_query.return_value = {
        "Items": [
            {"userId": {"S": "test-user-id"}, "roleName": {"S": "admin"}},
            {"userId": {"S": "test-user-id"}, "roleName": {"S": "editor"}}
        ]
    }
    
    mock_dynamodb = MagicMock()
    mock_dynamodb.Table.return_value = mock_table
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Patch the imports
    with patch("boto3.client", return_value=mock_dynamodb_client), \
         patch("boto3.resource", return_value=mock_dynamodb), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.validators.validate", return_value=(True, "")):
        
        # Import the module here to ensure our mocks are in place
        from handlers.userRoles import userRolesService
        
        # Call the lambda handler
        response = userRolesService.lambda_handler(delete_user_roles_event, None)
        
        # Verify the response
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "success"
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called()
        
        # Verify batch writer was called to delete roles
        assert mock_batch_writer.delete_item.call_count == 2  # Two roles to delete

def test_user_roles_unauthorized(get_user_roles_event, monkeypatch):
    """
    Test the userRolesService lambda handler with an unauthorized request
    
    Args:
        get_user_roles_event: Lambda event dictionary for getting user roles
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ROLES_TABLE_NAME", "test-roles-table")
    monkeypatch.setenv("USER_ROLES_TABLE_NAME", "test-user-roles-table")
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["user"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Mock the CasbinEnforcer class to deny access
    mock_enforcer = MagicMock()
    mock_enforcer.enforceAPI.return_value = False
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_enforcer), \
         patch("common.dynamodb.validate_pagination_info"):
        
        # Import the module here to ensure our mocks are in place
        from handlers.userRoles import userRolesService
        
        # Call the lambda handler
        response = userRolesService.lambda_handler(get_user_roles_event, None)
        
        # Verify the response
        assert response["statusCode"] == 403
        assert json.loads(response["body"])["message"] == "Not Authorized"
        
        # Verify the enforcer was called
        mock_enforcer.enforceAPI.assert_called_once()

def test_user_roles_validation_error(create_user_roles_event, mock_casbin_enforcer, monkeypatch):
    """
    Test the userRolesService lambda handler with invalid input
    
    Args:
        create_user_roles_event: Lambda event dictionary for creating user roles
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ROLES_TABLE_NAME", "test-roles-table")
    monkeypatch.setenv("USER_ROLES_TABLE_NAME", "test-user-roles-table")
    
    # Modify the event to have missing required fields
    create_user_roles_event["body"] = json.dumps({
        "userId": "test-user-id"
        # Missing roleName
    })
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer):
        
        # Import the module here to ensure our mocks are in place
        from handlers.userRoles import userRolesService
        
        # Call the lambda handler
        response = userRolesService.lambda_handler(create_user_roles_event, None)
        
        # Verify the response
        assert response["statusCode"] == 400
        assert "RoleName and userId are required" in json.loads(response["body"])["message"]
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()

def test_user_roles_error(get_user_roles_event, monkeypatch):
    """
    Test the userRolesService lambda handler when an error occurs
    
    Args:
        get_user_roles_event: Lambda event dictionary for getting user roles
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("ROLES_TABLE_NAME", "test-roles-table")
    monkeypatch.setenv("USER_ROLES_TABLE_NAME", "test-user-roles-table")
    
    # Mock the request_to_claims function to raise an exception
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.side_effect = Exception("Test error")
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims):
        
        # Import the module here to ensure our mocks are in place
        from handlers.userRoles import userRolesService
        
        # Call the lambda handler
        response = userRolesService.lambda_handler(get_user_roles_event, None)
        
        # Verify the response
        assert response["statusCode"] == 500
        assert json.loads(response["body"])["message"] == "Internal Server Error"
