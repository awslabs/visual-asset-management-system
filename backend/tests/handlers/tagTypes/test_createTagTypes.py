import pytest
import json
import boto3
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError

@pytest.fixture(scope="function")
def tag_type_table(ddb_resource):
    """
    Create a table to store tag types for testing
    
    Args:
        ddb_resource: Mocked DynamoDB resource
        
    Returns:
        boto3.resource.Table: Mocked tag type table
    """
    table_name = "tagTypesStorageTable"
    table = ddb_resource.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "tagTypeName", "KeyType": "HASH"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "tagTypeName", "AttributeType": "S"},
        ],
    )
    
    # Add a test tag type
    table.put_item(
        Item={
            "tagTypeName": "existing-tag-type",
            "description": "Existing tag type description",
            "required": "False"
        }
    )
    
    return table

@pytest.fixture(scope="function")
def create_tag_type_event():
    """
    Generates an event for creating a tag type
    
    Returns:
        dict: Lambda event dictionary for creating a tag type
    """
    return {
        "requestContext": {
            "http": {
                "method": "POST"
            }
        },
        "body": {
            "tagTypeName": "new-tag-type",
            "description": "New tag type description",
            "required": "True"
        }
    }

@pytest.fixture(scope="function")
def update_tag_type_event():
    """
    Generates an event for updating a tag type
    
    Returns:
        dict: Lambda event dictionary for updating a tag type
    """
    return {
        "requestContext": {
            "http": {
                "method": "PUT"
            }
        },
        "body": {
            "tagTypeName": "existing-tag-type",
            "description": "Updated tag type description",
            "required": "True"
        }
    }

@pytest.fixture(scope="function")
def invalid_tag_type_event():
    """
    Generates an event with invalid tag type data
    
    Returns:
        dict: Lambda event dictionary with invalid tag type data
    """
    return {
        "requestContext": {
            "http": {
                "method": "POST"
            }
        },
        "body": {
            "description": "Missing tag type name"
        }
    }

def test_create_tag_type_success(tag_type_table, create_tag_type_event, monkeypatch):
    """
    Test the createTagTypes lambda handler with a successful POST request
    
    Args:
        tag_type_table: Mocked tag type table
        create_tag_type_event: Lambda event dictionary for creating a tag type
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Mock the CasbinEnforcer class
    mock_enforcer = MagicMock()
    mock_enforcer.enforce.return_value = True
    mock_enforcer.enforceAPI.return_value = True
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_enforcer):
        # Import the module here to ensure our mocks are in place
        from handlers.tagTypes import createTagTypes
        
        # Call the lambda handler
        response = createTagTypes.lambda_handler(create_tag_type_event, None)
        
        # Verify the response
        assert json.loads(response) == {"message": "Succeeded"}
        
        # Verify the tag type was created
        response = tag_type_table.get_item(Key={"tagTypeName": "new-tag-type"})
        assert "Item" in response
        assert response["Item"]["description"] == "New tag type description"
        assert response["Item"]["required"] == "True"
        
        # Verify the enforcer was called
        mock_enforcer.enforce.assert_called_once()
        mock_enforcer.enforceAPI.assert_called_once()

def test_update_tag_type_success(tag_type_table, update_tag_type_event, monkeypatch):
    """
    Test the createTagTypes lambda handler with a successful PUT request
    
    Args:
        tag_type_table: Mocked tag type table
        update_tag_type_event: Lambda event dictionary for updating a tag type
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Mock the CasbinEnforcer class
    mock_enforcer = MagicMock()
    mock_enforcer.enforce.return_value = True
    mock_enforcer.enforceAPI.return_value = True
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_enforcer):
        # Import the module here to ensure our mocks are in place
        from handlers.tagTypes import createTagTypes
        
        # Call the lambda handler
        response = createTagTypes.lambda_handler(update_tag_type_event, None)
        
        # Verify the response
        assert json.loads(response) == {"message": "Succeeded"}
        
        # Verify the tag type was updated
        response = tag_type_table.get_item(Key={"tagTypeName": "existing-tag-type"})
        assert "Item" in response
        assert response["Item"]["description"] == "Updated tag type description"
        assert response["Item"]["required"] == "True"
        
        # Verify the enforcer was called
        mock_enforcer.enforce.assert_called_once()
        mock_enforcer.enforceAPI.assert_called_once()

def test_create_tag_type_missing_fields(invalid_tag_type_event, monkeypatch):
    """
    Test the createTagTypes lambda handler with missing required fields
    
    Args:
        invalid_tag_type_event: Lambda event dictionary with invalid tag type data
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims):
        # Import the module here to ensure our mocks are in place
        from handlers.tagTypes import createTagTypes
        
        # Call the lambda handler
        response = createTagTypes.lambda_handler(invalid_tag_type_event, None)
        
        # Verify the response
        assert json.loads(response["body"])["message"] == "TagTypeName and description are required in API Call"
        assert response["statusCode"] == 400

def test_create_tag_type_invalid_name(create_tag_type_event, monkeypatch):
    """
    Test the createTagTypes lambda handler with an invalid tag type name
    
    Args:
        create_tag_type_event: Lambda event dictionary for creating a tag type
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    
    # Set an invalid tag type name
    create_tag_type_event["body"]["tagTypeName"] = "invalid@name"
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims):
        # Import the module here to ensure our mocks are in place
        from handlers.tagTypes import createTagTypes
        
        # Call the lambda handler
        response = createTagTypes.lambda_handler(create_tag_type_event, None)
        
        # Verify the response
        assert "Invalid" in json.loads(response["body"])["message"]
        assert response["statusCode"] == 400

def test_create_tag_type_unauthorized(create_tag_type_event, monkeypatch):
    """
    Test the createTagTypes lambda handler with an unauthorized request
    
    Args:
        create_tag_type_event: Lambda event dictionary for creating a tag type
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Mock the CasbinEnforcer class
    mock_enforcer = MagicMock()
    mock_enforcer.enforce.return_value = False
    mock_enforcer.enforceAPI.return_value = True
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_enforcer):
        # Import the module here to ensure our mocks are in place
        from handlers.tagTypes import createTagTypes
        
        # Call the lambda handler
        response = createTagTypes.lambda_handler(create_tag_type_event, None)
        
        # Verify the response
        assert json.loads(response["body"])["message"] == "Not Authorized"
        assert response["statusCode"] == 403
        
        # Verify the enforcer was called
        mock_enforcer.enforce.assert_called_once()
        mock_enforcer.enforceAPI.assert_called_once()

def test_create_tag_type_already_exists(tag_type_table, create_tag_type_event, monkeypatch):
    """
    Test the createTagTypes lambda handler when the tag type already exists
    
    Args:
        tag_type_table: Mocked tag type table
        create_tag_type_event: Lambda event dictionary for creating a tag type
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    
    # Change the tag type name to one that already exists
    create_tag_type_event["body"]["tagTypeName"] = "existing-tag-type"
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Mock the CasbinEnforcer class
    mock_enforcer = MagicMock()
    mock_enforcer.enforce.return_value = True
    mock_enforcer.enforceAPI.return_value = True
    
    # Create a mock error response
    error_response = {
        'Error': {
            'Code': 'ConditionalCheckFailedException',
            'Message': 'The conditional request failed'
        }
    }
    
    # Patch the imports and DynamoDB Table
    with patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_enforcer), \
         patch("boto3.resource") as mock_resource:
        # Set up the mock resource to raise an exception
        mock_table = MagicMock()
        mock_table.put_item.side_effect = ClientError(error_response, 'PutItem')
        mock_resource.return_value.Table.return_value = mock_table
        
        # Import the module here to ensure our mocks are in place
        from handlers.tagTypes import createTagTypes
        
        # Call the lambda handler
        response = createTagTypes.lambda_handler(create_tag_type_event, None)
        
        # Verify the response
        assert "already exists" in json.loads(response["body"])["message"]
        assert response["statusCode"] == 400
        
        # Verify the enforcer was called
        mock_enforcer.enforce.assert_called_once()
        mock_enforcer.enforceAPI.assert_called_once()

def test_create_tag_type_internal_error(create_tag_type_event, monkeypatch):
    """
    Test the createTagTypes lambda handler when an internal error occurs
    
    Args:
        create_tag_type_event: Lambda event dictionary for creating a tag type
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Mock the CasbinEnforcer class
    mock_enforcer = MagicMock()
    mock_enforcer.enforce.return_value = True
    mock_enforcer.enforceAPI.return_value = True
    
    # Patch the imports and DynamoDB Table
    with patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_enforcer), \
         patch("boto3.resource") as mock_resource:
        # Set up the mock resource to raise an exception
        mock_table = MagicMock()
        mock_table.put_item.side_effect = Exception("Internal error")
        mock_resource.return_value.Table.return_value = mock_table
        
        # Import the module here to ensure our mocks are in place
        from handlers.tagTypes import createTagTypes
        
        # Call the lambda handler
        response = createTagTypes.lambda_handler(create_tag_type_event, None)
        
        # Verify the response
        assert json.loads(response["body"])["message"] == "Internal Server Error"
        assert response["statusCode"] == 500
        
        # Verify the enforcer was called
        mock_enforcer.enforce.assert_called_once()
        mock_enforcer.enforceAPI.assert_called_once()
