import pytest
import json
import boto3
from unittest.mock import patch, MagicMock
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEvent

@pytest.fixture(scope="function")
def metadata_schema_table(ddb_resource):
    """
    Create a table to store metadata schema for testing
    
    Args:
        ddb_resource: Mocked DynamoDB resource
        
    Returns:
        boto3.resource.Table: Mocked metadata schema table
    """
    table_name = "metadataSchemaStorageTable"
    table = ddb_resource.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": "field", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": "field", "AttributeType": "S"},
        ],
    )
    
    # Add a test schema
    table.put_item(
        Item={
            "databaseId": "test-database",
            "field": "test-field",
            "datatype": "string",
            "required": True,
            "dependsOn": []
        }
    )
    
    return table

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
def mock_request_to_claims(mock_claims_and_roles):
    """
    Mock for request_to_claims function
    
    Args:
        mock_claims_and_roles: Mock claims and roles
        
    Returns:
        MagicMock: Mock for request_to_claims function
    """
    mock_fn = MagicMock()
    mock_fn.return_value = mock_claims_and_roles
    return mock_fn

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
def get_schema_event():
    """
    Generates an event for getting a schema
    
    Returns:
        dict: Lambda event dictionary for getting a schema
    """
    return {
        "requestContext": {
            "http": {
                "method": "GET"
            },
            "requestId": "test-request-id"
        },
        "pathParameters": {
            "databaseId": "test-database"
        },
        "queryStringParameters": {
            "maxItems": "10",
            "pageSize": "10",
            "startingToken": ""
        }
    }

@pytest.fixture(scope="function")
def post_schema_event():
    """
    Generates an event for posting a schema
    
    Returns:
        dict: Lambda event dictionary for posting a schema
    """
    return {
        "requestContext": {
            "http": {
                "method": "POST"
            },
            "requestId": "test-request-id"
        },
        "pathParameters": {
            "databaseId": "test-database"
        },
        "body": json.dumps({
            "field": "new-field",
            "datatype": "string",
            "required": True,
            "dependsOn": []
        })
    }

@pytest.fixture(scope="function")
def delete_schema_event():
    """
    Generates an event for deleting a schema
    
    Returns:
        dict: Lambda event dictionary for deleting a schema
    """
    return {
        "requestContext": {
            "http": {
                "method": "DELETE"
            },
            "requestId": "test-request-id"
        },
        "pathParameters": {
            "databaseId": "test-database",
            "field": "test-field"
        }
    }

def test_get_schema(metadata_schema_table, get_schema_event, mock_request_to_claims, mock_casbin_enforcer, monkeypatch):
    """
    Test the schema lambda handler with a GET request
    
    Args:
        metadata_schema_table: Mocked metadata schema table
        get_schema_event: Lambda event dictionary for getting a schema
        mock_request_to_claims: Mock for request_to_claims function
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("METADATA_SCHEMA_STORAGE_TABLE_NAME", "metadataSchemaStorageTable")
    
    # Patch the CasbinEnforcer class
    with patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer):
        # Import the module here to ensure our mocks are in place
        from handlers.metadataschema.schema import lambda_handler
        
        # Convert the event to an APIGatewayProxyEvent
        event = APIGatewayProxyEvent(get_schema_event)
        
        # Call the lambda handler
        response = lambda_handler(event, None, claims_fn=mock_request_to_claims)
        
        # Verify the response
        assert response["statusCode"] == 200
        assert "message" in json.loads(response["body"])
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()

def test_post_schema(metadata_schema_table, post_schema_event, mock_request_to_claims, mock_casbin_enforcer, monkeypatch):
    """
    Test the schema lambda handler with a POST request
    
    Args:
        metadata_schema_table: Mocked metadata schema table
        post_schema_event: Lambda event dictionary for posting a schema
        mock_request_to_claims: Mock for request_to_claims function
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("METADATA_SCHEMA_STORAGE_TABLE_NAME", "metadataSchemaStorageTable")
    
    # Patch the CasbinEnforcer class
    with patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer):
        # Import the module here to ensure our mocks are in place
        from handlers.metadataschema.schema import lambda_handler
        
        # Convert the event to an APIGatewayProxyEvent
        event = APIGatewayProxyEvent(post_schema_event)
        
        # Call the lambda handler
        response = lambda_handler(event, None, claims_fn=mock_request_to_claims)
        
        # Verify the response
        assert response["statusCode"] == 200
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called_once()

def test_delete_schema(metadata_schema_table, delete_schema_event, mock_request_to_claims, mock_casbin_enforcer, monkeypatch):
    """
    Test the schema lambda handler with a DELETE request
    
    Args:
        metadata_schema_table: Mocked metadata schema table
        delete_schema_event: Lambda event dictionary for deleting a schema
        mock_request_to_claims: Mock for request_to_claims function
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("METADATA_SCHEMA_STORAGE_TABLE_NAME", "metadataSchemaStorageTable")
    
    # Patch the CasbinEnforcer class
    with patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer):
        # Import the module here to ensure our mocks are in place
        from handlers.metadataschema.schema import lambda_handler
        
        # Convert the event to an APIGatewayProxyEvent
        event = APIGatewayProxyEvent(delete_schema_event)
        
        # Call the lambda handler
        response = lambda_handler(event, None, claims_fn=mock_request_to_claims)
        
        # Verify the response
        assert response["statusCode"] == 200
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called_once()

def test_get_schema_unauthorized(metadata_schema_table, get_schema_event, mock_request_to_claims, monkeypatch):
    """
    Test the schema lambda handler with an unauthorized GET request
    
    Args:
        metadata_schema_table: Mocked metadata schema table
        get_schema_event: Lambda event dictionary for getting a schema
        mock_request_to_claims: Mock for request_to_claims function
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("METADATA_SCHEMA_STORAGE_TABLE_NAME", "metadataSchemaStorageTable")
    
    # Create a mock enforcer that denies access
    mock_enforcer = MagicMock()
    mock_enforcer.enforceAPI.return_value = False
    
    # Patch the CasbinEnforcer class
    with patch("handlers.authz.CasbinEnforcer", return_value=mock_enforcer):
        # Import the module here to ensure our mocks are in place
        from handlers.metadataschema.schema import lambda_handler
        
        # Convert the event to an APIGatewayProxyEvent
        event = APIGatewayProxyEvent(get_schema_event)
        
        # Call the lambda handler
        response = lambda_handler(event, None, claims_fn=mock_request_to_claims)
        
        # Verify the response
        assert response["statusCode"] == 403
        assert json.loads(response["body"])["message"] == "Not Authorized"
        
        # Verify the enforcer was called
        mock_enforcer.enforceAPI.assert_called_once()

def test_get_schema_missing_database_id(get_schema_event, mock_request_to_claims, mock_casbin_enforcer, monkeypatch):
    """
    Test the schema lambda handler with a GET request missing the database ID
    
    Args:
        get_schema_event: Lambda event dictionary for getting a schema
        mock_request_to_claims: Mock for request_to_claims function
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("METADATA_SCHEMA_STORAGE_TABLE_NAME", "metadataSchemaStorageTable")
    
    # Remove the database ID from the event
    get_schema_event["pathParameters"] = {}
    
    # Patch the CasbinEnforcer class
    with patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer):
        # Import the module here to ensure our mocks are in place
        from handlers.metadataschema.schema import lambda_handler
        
        # Convert the event to an APIGatewayProxyEvent
        event = APIGatewayProxyEvent(get_schema_event)
        
        # Call the lambda handler
        response = lambda_handler(event, None, claims_fn=mock_request_to_claims)
        
        # Verify the response
        assert response["statusCode"] == 400
        assert "No database ID in API Call" in json.loads(response["body"])["message"]

def test_delete_schema_missing_field(delete_schema_event, mock_request_to_claims, mock_casbin_enforcer, monkeypatch):
    """
    Test the schema lambda handler with a DELETE request missing the field
    
    Args:
        delete_schema_event: Lambda event dictionary for deleting a schema
        mock_request_to_claims: Mock for request_to_claims function
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("METADATA_SCHEMA_STORAGE_TABLE_NAME", "metadataSchemaStorageTable")
    
    # Remove the field from the event
    delete_schema_event["pathParameters"] = {"databaseId": "test-database"}
    
    # Patch the CasbinEnforcer class
    with patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer):
        # Import the module here to ensure our mocks are in place
        from handlers.metadataschema.schema import lambda_handler
        
        # Convert the event to an APIGatewayProxyEvent
        event = APIGatewayProxyEvent(delete_schema_event)
        
        # Call the lambda handler
        response = lambda_handler(event, None, claims_fn=mock_request_to_claims)
        
        # Verify the response
        assert response["statusCode"] == 400
        assert "Missing field in path on delete request" in json.loads(response["body"])["error"]
