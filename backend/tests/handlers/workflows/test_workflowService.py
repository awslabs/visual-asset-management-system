import pytest
import json
import boto3
import botocore
from unittest.mock import patch, MagicMock

@pytest.fixture(scope="function")
def get_workflows_event():
    """
    Generates an event for getting workflows
    
    Returns:
        dict: Lambda event dictionary for getting workflows
    """
    return {
        "requestContext": {
            "http": {
                "method": "GET"
            }
        },
        "pathParameters": {
            "databaseId": "test-database-id"
        },
        "queryStringParameters": {
            "maxItems": "10",
            "pageSize": "10",
            "startingToken": ""
        }
    }

@pytest.fixture(scope="function")
def get_workflow_event():
    """
    Generates an event for getting a specific workflow
    
    Returns:
        dict: Lambda event dictionary for getting a specific workflow
    """
    return {
        "requestContext": {
            "http": {
                "method": "GET"
            }
        },
        "pathParameters": {
            "databaseId": "test-database-id",
            "workflowId": "test-workflow-id"
        },
        "queryStringParameters": {
            "maxItems": "10",
            "pageSize": "10",
            "startingToken": ""
        }
    }

@pytest.fixture(scope="function")
def get_all_workflows_event():
    """
    Generates an event for getting all workflows
    
    Returns:
        dict: Lambda event dictionary for getting all workflows
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
def delete_workflow_event():
    """
    Generates an event for deleting a workflow
    
    Returns:
        dict: Lambda event dictionary for deleting a workflow
    """
    return {
        "requestContext": {
            "http": {
                "method": "DELETE"
            }
        },
        "pathParameters": {
            "databaseId": "test-database-id",
            "workflowId": "test-workflow-id"
        }
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
                "databaseId": {"S": "test-database-id"},
                "workflowId": {"S": "test-workflow-id"},
                "workflowName": {"S": "Test Workflow"},
                "workflow_arn": {"S": "arn:aws:states:us-east-1:123456789012:stateMachine:test-workflow"},
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
                "databaseId": "test-database-id",
                "workflowId": "test-workflow-id",
                "workflowName": "Test Workflow",
                "workflow_arn": "arn:aws:states:us-east-1:123456789012:stateMachine:test-workflow",
                "createdOn": "2023-07-06T21:32:15.066148"
            }
        ]
    }
    return mock_query

@pytest.fixture(scope="function")
def mock_workflow_table():
    """
    Mock for workflow table
    
    Returns:
        MagicMock: Mock for workflow table
    """
    mock_table = MagicMock()
    mock_table.get_item.return_value = {
        "Item": {
            "databaseId": "test-database-id",
            "workflowId": "test-workflow-id",
            "workflowName": "Test Workflow",
            "workflow_arn": "arn:aws:states:us-east-1:123456789012:stateMachine:test-workflow",
            "createdOn": "2023-07-06T21:32:15.066148"
        }
    }
    return mock_table

def test_get_workflows(get_workflows_event, mock_dynamodb_query, mock_casbin_enforcer, monkeypatch):
    """
    Test the workflowService lambda handler with a GET request for workflows in a database
    
    Args:
        get_workflows_event: Lambda event dictionary for getting workflows
        mock_dynamodb_query: Mock for DynamoDB query
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
    
    # Mock boto3 clients and resources
    mock_dynamodb = MagicMock()
    mock_meta = MagicMock()
    mock_meta.client.get_paginator.return_value = mock_dynamodb_query
    mock_dynamodb.meta = mock_meta
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Patch the imports
    with patch("boto3.resource", return_value=mock_dynamodb), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.dynamodb.validate_pagination_info"), \
         patch("common.validators.validate", return_value=(True, "")):
        
        # Import the module here to ensure our mocks are in place
        from handlers.workflows import workflowService
        
        # Call the lambda handler
        response = workflowService.lambda_handler(get_workflows_event, None)
        
        # Verify the response
        assert response["statusCode"] == 200
        message = json.loads(response["body"])["message"]
        assert "Items" in message
        assert len(message["Items"]) > 0
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called()

def test_get_workflow(get_workflow_event, mock_workflow_table, mock_casbin_enforcer, monkeypatch):
    """
    Test the workflowService lambda handler with a GET request for a specific workflow
    
    Args:
        get_workflow_event: Lambda event dictionary for getting a specific workflow
        mock_workflow_table: Mock for workflow table
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
    
    # Mock boto3 clients and resources
    mock_dynamodb = MagicMock()
    mock_dynamodb.Table.return_value = mock_workflow_table
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Patch the imports
    with patch("boto3.resource", return_value=mock_dynamodb), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.dynamodb.validate_pagination_info"), \
         patch("common.validators.validate", return_value=(True, "")):
        
        # Import the module here to ensure our mocks are in place
        from handlers.workflows import workflowService
        
        # Call the lambda handler
        response = workflowService.lambda_handler(get_workflow_event, None)
        
        # Verify the response
        assert response["statusCode"] == 200
        message = json.loads(response["body"])["message"]
        assert message["workflowId"] == "test-workflow-id"
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called_once()

def test_get_all_workflows(get_all_workflows_event, mock_dynamodb_paginator, mock_casbin_enforcer, monkeypatch):
    """
    Test the workflowService lambda handler with a GET request for all workflows
    
    Args:
        get_all_workflows_event: Lambda event dictionary for getting all workflows
        mock_dynamodb_paginator: Mock for DynamoDB paginator
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
    
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
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.dynamodb.validate_pagination_info"):
        
        # Import the module here to ensure our mocks are in place
        from handlers.workflows import workflowService
        
        # Call the lambda handler
        response = workflowService.lambda_handler(get_all_workflows_event, None)
        
        # Verify the response
        assert response["statusCode"] == 200
        message = json.loads(response["body"])["message"]
        assert "Items" in message
        assert len(message["Items"]) > 0
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called()

def test_delete_workflow(delete_workflow_event, mock_workflow_table, mock_casbin_enforcer, monkeypatch):
    """
    Test the workflowService lambda handler with a DELETE request
    
    Args:
        delete_workflow_event: Lambda event dictionary for deleting a workflow
        mock_workflow_table: Mock for workflow table
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
    
    # Mock boto3 clients and resources
    mock_dynamodb = MagicMock()
    mock_dynamodb.Table.return_value = mock_workflow_table
    
    mock_sf_client = MagicMock()
    mock_sf_client.delete_state_machine.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Patch the imports
    with patch("boto3.resource", return_value=mock_dynamodb), \
         patch("boto3.client", return_value=mock_sf_client), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.validators.validate", return_value=(True, "")):
        
        # Import the module here to ensure our mocks are in place
        from handlers.workflows import workflowService
        
        # Call the lambda handler
        response = workflowService.lambda_handler(delete_workflow_event, None)
        
        # Verify the response
        assert response["statusCode"] == 200
        assert json.loads(response["body"])["message"] == "Workflow deleted"
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
        mock_casbin_enforcer.enforce.assert_called_once()
        
        # Verify Step Functions client was called to delete the state machine
        mock_sf_client.delete_state_machine.assert_called_once_with(
            stateMachineArn="arn:aws:states:us-east-1:123456789012:stateMachine:test-workflow"
        )

def test_workflow_not_found(get_workflow_event, mock_casbin_enforcer, monkeypatch):
    """
    Test the workflowService lambda handler when a workflow is not found
    
    Args:
        get_workflow_event: Lambda event dictionary for getting a specific workflow
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
    
    # Mock boto3 clients and resources
    mock_dynamodb = MagicMock()
    mock_table = MagicMock()
    mock_table.get_item.return_value = {}  # No item found
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
    with patch("boto3.resource", return_value=mock_dynamodb), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.dynamodb.validate_pagination_info"), \
         patch("common.validators.validate", return_value=(True, "")):
        
        # Import the module here to ensure our mocks are in place
        from handlers.workflows import workflowService
        
        # Call the lambda handler
        response = workflowService.lambda_handler(get_workflow_event, None)
        
        # Verify the response
        assert response["statusCode"] == 404
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()

def test_workflow_unauthorized(get_workflow_event, mock_workflow_table, monkeypatch):
    """
    Test the workflowService lambda handler with an unauthorized request
    
    Args:
        get_workflow_event: Lambda event dictionary for getting a specific workflow
        mock_workflow_table: Mock for workflow table
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
    
    # Mock boto3 clients and resources
    mock_dynamodb = MagicMock()
    mock_dynamodb.Table.return_value = mock_workflow_table
    
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
    with patch("boto3.resource", return_value=mock_dynamodb), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_enforcer), \
         patch("common.dynamodb.validate_pagination_info"), \
         patch("common.validators.validate", return_value=(True, "")):
        
        # Import the module here to ensure our mocks are in place
        from handlers.workflows import workflowService
        
        # Call the lambda handler
        response = workflowService.lambda_handler(get_workflow_event, None)
        
        # Verify the response
        assert response["statusCode"] == 403
        assert json.loads(response["body"])["message"] == "Not Authorized"
        
        # Verify the enforcer was called
        mock_enforcer.enforceAPI.assert_called_once()

def test_workflow_validation_error(get_workflow_event, mock_casbin_enforcer, monkeypatch):
    """
    Test the workflowService lambda handler with invalid input
    
    Args:
        get_workflow_event: Lambda event dictionary for getting a specific workflow
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
    
    # Modify the event to have invalid parameters
    get_workflow_event["pathParameters"]["workflowId"] = "invalid-id-with-special-chars!@#"
    
    # Mock the request_to_claims function
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.return_value = {
        "tokens": ["test-token"],
        "roles": ["admin"],
        "sub": "test-user",
        "email": "test@example.com"
    }
    
    # Mock the validate function to return an error
    mock_validate = MagicMock()
    mock_validate.return_value = (False, "Invalid workflow ID format")
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.validators.validate", mock_validate):
        
        # Import the module here to ensure our mocks are in place
        from handlers.workflows import workflowService
        
        # Call the lambda handler
        response = workflowService.lambda_handler(get_workflow_event, None)
        
        # Verify the response
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"] == "Invalid workflow ID format"
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()

def test_workflow_error(get_workflow_event, monkeypatch):
    """
    Test the workflowService lambda handler when an error occurs
    
    Args:
        get_workflow_event: Lambda event dictionary for getting a specific workflow
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
    
    # Mock the request_to_claims function to raise an exception
    mock_request_to_claims = MagicMock()
    mock_request_to_claims.side_effect = Exception("Test error")
    
    # Patch the imports
    with patch("handlers.auth.request_to_claims", mock_request_to_claims):
        
        # Import the module here to ensure our mocks are in place
        from handlers.workflows import workflowService
        
        # Call the lambda handler
        response = workflowService.lambda_handler(get_workflow_event, None)
        
        # Verify the response
        assert response["statusCode"] == 500
        assert json.loads(response["body"])["message"] == "Internal Server Error"

def test_workflow_throttling_error(get_workflow_event, mock_casbin_enforcer, monkeypatch):
    """
    Test the workflowService lambda handler when a throttling error occurs
    
    Args:
        get_workflow_event: Lambda event dictionary for getting a specific workflow
        mock_casbin_enforcer: Mock for CasbinEnforcer
        monkeypatch: Pytest monkeypatch fixture
    """
    # Set up environment variables
    monkeypatch.setenv("WORKFLOW_STORAGE_TABLE_NAME", "test-workflow-table")
    
    # Mock boto3 clients and resources
    mock_dynamodb = MagicMock()
    mock_table = MagicMock()
    
    # Create a throttling exception
    throttling_exception = botocore.exceptions.ClientError(
        {
            "Error": {
                "Code": "ThrottlingException",
                "Message": "Rate exceeded"
            },
            "ResponseMetadata": {
                "HTTPStatusCode": 429
            }
        },
        "GetItem"
    )
    
    mock_table.get_item.side_effect = throttling_exception
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
    with patch("boto3.resource", return_value=mock_dynamodb), \
         patch("handlers.auth.request_to_claims", mock_request_to_claims), \
         patch("handlers.authz.CasbinEnforcer", return_value=mock_casbin_enforcer), \
         patch("common.dynamodb.validate_pagination_info"), \
         patch("common.validators.validate", return_value=(True, "")):
        
        # Import the module here to ensure our mocks are in place
        from handlers.workflows import workflowService
        
        # Call the lambda handler
        response = workflowService.lambda_handler(get_workflow_event, None)
        
        # Verify the response
        assert response["statusCode"] == 429
        assert "ThrottlingException" in json.loads(response["body"])["message"]
        
        # Verify the enforcer was called
        mock_casbin_enforcer.enforceAPI.assert_called_once()
