"""
Lambda Test Utilities for VAMS Backend

This module provides utility functions and classes to help automate unit tests
for AWS Lambda functions and API Gateway endpoints in the VAMS backend.
"""

import json
import os
from typing import Any, Dict, Optional, Union, Callable
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws


class LambdaContext:
    """
    Mock AWS Lambda context object for testing Lambda functions.
    """
    def __init__(
        self,
        function_name: str = "test-function",
        function_version: str = "$LATEST",
        memory_limit_in_mb: int = 128,
        timeout: int = 30,
        aws_request_id: str = "test-request-id",
        log_group_name: str = "/aws/lambda/test-function",
        log_stream_name: str = "2023/02/28/[$LATEST]test-stream",
        invoked_function_arn: str = "arn:aws:lambda:us-east-1:123456789012:function:test-function",
        identity: Optional[Dict[str, str]] = None,
        client_context: Optional[Dict[str, Any]] = None
    ):
        self.function_name = function_name
        self.function_version = function_version
        self.memory_limit_in_mb = memory_limit_in_mb
        self.timeout = timeout
        self.aws_request_id = aws_request_id
        self.log_group_name = log_group_name
        self.log_stream_name = log_stream_name
        self.invoked_function_arn = invoked_function_arn
        self.identity = identity or {}
        self.client_context = client_context or {}
        
    def get_remaining_time_in_millis(self) -> int:
        """Return a mock value for remaining execution time."""
        return self.timeout * 1000


class APIGatewayEvent:
    """
    Builder for API Gateway event objects used in Lambda function testing.
    """
    def __init__(
        self,
        method: str = "GET",
        path: str = "/",
        headers: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, str]] = None,
        path_params: Optional[Dict[str, str]] = None,
        body: Optional[Union[Dict[str, Any], str]] = None,
        is_base64_encoded: bool = False,
        request_context: Optional[Dict[str, Any]] = None,
        stage_variables: Optional[Dict[str, str]] = None,
        cognito_auth: Optional[Dict[str, Any]] = None
    ):
        self.method = method
        self.path = path
        self.headers = headers or {}
        self.query_params = query_params or {}
        self.path_params = path_params or {}
        self.body = body
        self.is_base64_encoded = is_base64_encoded
        self.request_context = request_context or {
            "accountId": "123456789012",
            "apiId": "test-api-id",
            "httpMethod": method,
            "identity": {
                "sourceIp": "127.0.0.1",
                "userAgent": "pytest"
            },
            "requestId": "test-request-id",
            "resourceId": "test-resource-id",
            "resourcePath": path,
            "stage": "test"
        }
        self.stage_variables = stage_variables or {}
        
        # Add Cognito authentication if provided
        if cognito_auth:
            self.request_context["authorizer"] = {
                "claims": cognito_auth
            }
    
    def build(self) -> Dict[str, Any]:
        """
        Build and return the API Gateway event dictionary.
        """
        event = {
            "httpMethod": self.method,
            "path": self.path,
            "headers": self.headers,
            "queryStringParameters": self.query_params,
            "pathParameters": self.path_params,
            "stageVariables": self.stage_variables,
            "requestContext": self.request_context,
            "isBase64Encoded": self.is_base64_encoded
        }
        
        # Handle body based on type
        if self.body is not None:
            if isinstance(self.body, dict):
                event["body"] = json.dumps(self.body)
            else:
                event["body"] = self.body
        else:
            event["body"] = None
            
        return event


class DynamoDBHelper:
    """
    Helper class for setting up DynamoDB tables for testing.
    """
    def __init__(self, region_name: str = "us-east-1"):
        self.region_name = region_name
        
    @staticmethod
    def create_table(
        ddb_resource,
        table_name: str,
        key_schema: list,
        attribute_definitions: list,
        billing_mode: str = "PAY_PER_REQUEST",
        provisioned_throughput: Optional[Dict[str, int]] = None
    ):
        """
        Create a DynamoDB table for testing.
        
        Args:
            ddb_resource: The DynamoDB resource object
            table_name: Name of the table to create
            key_schema: Key schema for the table
            attribute_definitions: Attribute definitions for the table
            billing_mode: Billing mode (PAY_PER_REQUEST or PROVISIONED)
            provisioned_throughput: Provisioned throughput settings (required if billing_mode is PROVISIONED)
        
        Returns:
            The created table object
        """
        create_args = {
            "TableName": table_name,
            "KeySchema": key_schema,
            "AttributeDefinitions": attribute_definitions,
            "BillingMode": billing_mode
        }
        
        if billing_mode == "PROVISIONED":
            if not provisioned_throughput:
                provisioned_throughput = {
                    "ReadCapacityUnits": 5,
                    "WriteCapacityUnits": 5
                }
            create_args["ProvisionedThroughput"] = provisioned_throughput
            
        table = ddb_resource.create_table(**create_args)
        return table
    
    @staticmethod
    def load_table_data(table, items: list):
        """
        Load test data into a DynamoDB table.
        
        Args:
            table: The DynamoDB table object
            items: List of items to load into the table
        """
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)


@pytest.fixture(scope="function")
def lambda_context():
    """
    Fixture that provides a mock Lambda context object.
    """
    return LambdaContext()


@pytest.fixture(scope="function")
def api_gateway_event_builder():
    """
    Fixture that provides an API Gateway event builder.
    """
    return APIGatewayEvent


@pytest.fixture(scope="function")
def mock_env_vars():
    """
    Fixture to mock environment variables for testing.
    
    Usage:
        def test_something(mock_env_vars):
            mock_env_vars({
                "TABLE_NAME": "test-table",
                "REGION": "us-east-1"
            })
            # Test code that uses these environment variables
    """
    original_environ = dict(os.environ)
    
    def _set_env_vars(env_vars: Dict[str, str]):
        os.environ.update(env_vars)
        
    yield _set_env_vars
    
    # Restore original environment variables
    os.environ.clear()
    os.environ.update(original_environ)


@pytest.fixture(scope="function")
def ddb_resource():
    """
    Fixture that provides a mocked DynamoDB resource.
    """
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture(scope="function")
def s3_resource():
    """
    Fixture that provides a mocked S3 resource.
    """
    with mock_aws():
        yield boto3.resource("s3", region_name="us-east-1")


@pytest.fixture(scope="function")
def s3_client():
    """
    Fixture that provides a mocked S3 client.
    """
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


@pytest.fixture(scope="function")
def sfn_client():
    """
    Fixture that provides a mocked Step Functions client.
    """
    with mock_aws():
        yield boto3.client("stepfunctions", region_name="us-east-1")


def mock_aws_service(service_name: str):
    """
    Decorator to mock an AWS service for a test function.
    
    Args:
        service_name: Name of the AWS service to mock (e.g., 'dynamodb', 's3')
    
    Usage:
        @mock_aws_service('dynamodb')
        def test_something():
            # Test code that uses DynamoDB
    """
    def decorator(func):
        # Use mock_aws instead of specific service mocks
        return mock_aws(func)
    return decorator


def create_lambda_response(
    status_code: int = 200,
    body: Optional[Union[Dict[str, Any], str]] = None,
    headers: Optional[Dict[str, str]] = None,
    is_base64_encoded: bool = False
) -> Dict[str, Any]:
    """
    Create a standardized Lambda response object for API Gateway.
    
    Args:
        status_code: HTTP status code
        body: Response body (dict will be JSON serialized)
        headers: Response headers
        is_base64_encoded: Whether the body is base64 encoded
    
    Returns:
        Lambda response dictionary
    """
    response = {
        "statusCode": status_code,
        "headers": headers or {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,PUT,DELETE,OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type,Authorization"
        },
        "isBase64Encoded": is_base64_encoded
    }
    
    if body is not None:
        if isinstance(body, dict):
            response["body"] = json.dumps(body)
        else:
            response["body"] = body
    else:
        response["body"] = ""
        
    return response


def assert_lambda_response(
    response: Dict[str, Any],
    expected_status_code: int = 200,
    expected_body: Optional[Dict[str, Any]] = None,
    expected_headers: Optional[Dict[str, str]] = None
):
    """
    Assert that a Lambda response matches expected values.
    
    Args:
        response: The Lambda response to check
        expected_status_code: Expected HTTP status code
        expected_body: Expected response body (as dict)
        expected_headers: Expected response headers
    """
    assert response["statusCode"] == expected_status_code
    
    if expected_body is not None:
        response_body = json.loads(response["body"]) if response["body"] else {}
        assert response_body == expected_body
        
    if expected_headers is not None:
        for key, value in expected_headers.items():
            assert response["headers"].get(key) == value


class MockLambdaInvoker:
    """
    Helper class for mocking Lambda invocations.
    """
    def __init__(self):
        self.responses = {}
        self.invocations = []
        
    def add_response(self, function_name: str, response: Dict[str, Any]):
        """
        Add a mock response for a Lambda function.
        
        Args:
            function_name: Name of the Lambda function
            response: Response to return when the function is invoked
        """
        self.responses[function_name] = response
        
    def mock_invoke(self, **kwargs):
        """
        Mock the Lambda invoke method.
        
        Args:
            **kwargs: Arguments passed to the invoke method
            
        Returns:
            Mock response for the Lambda function
        """
        function_name = kwargs.get("FunctionName", "")
        payload = kwargs.get("Payload", "{}")
        
        # Record the invocation
        self.invocations.append({
            "function_name": function_name,
            "payload": payload
        })
        
        # Return the mock response or a default response
        if function_name in self.responses:
            response_payload = self.responses[function_name]
        else:
            response_payload = {"statusCode": 200, "body": "{}"}
            
        return {
            "StatusCode": 200,
            "Payload": MagicMock(
                read=MagicMock(return_value=json.dumps(response_payload).encode())
            )
        }
        
    def setup_mock(self):
        """
        Set up the mock for Lambda invocations.
        
        Returns:
            Patch object for the Lambda client's invoke method
        """
        return patch("boto3.client", return_value=MagicMock(invoke=self.mock_invoke))


def create_test_decorator(marker_name: str, description: str) -> Callable:
    """
    Create a test decorator for marking tests.
    
    Args:
        marker_name: Name of the pytest marker
        description: Description of the marker
        
    Returns:
        Decorator function
    """
    def decorator(func):
        return pytest.mark.marker_name(func)
    
    # Add the marker to pytest's configuration
    if not hasattr(pytest.mark, marker_name):
        setattr(pytest.mark, marker_name, pytest.mark.marker_name)
        
    return decorator


# Create common test markers
unit_test = create_test_decorator("unit", "Mark test as a unit test")
integration_test = create_test_decorator("integration", "Mark test as an integration test")
slow_test = create_test_decorator("slow", "Mark test as a slow test")
aws_test = create_test_decorator("aws", "Mark test as requiring AWS services")
