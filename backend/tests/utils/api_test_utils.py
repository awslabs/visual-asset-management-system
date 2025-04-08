"""
API Test Utilities for VAMS Backend

This module provides utility functions and classes to help automate unit tests
for API endpoints in the VAMS backend.
"""

import json
import os
from typing import Any, Dict, List, Optional, Union, Callable
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.utils.lambda_test_utils import (
    APIGatewayEvent,
    LambdaContext,
    assert_lambda_response,
    create_lambda_response
)


class APITestCase:
    """
    Base class for API endpoint test cases.
    
    This class provides common functionality for testing API endpoints,
    including setting up mock AWS services, environment variables, and
    helper methods for making API requests.
    """
    
    def setup_method(self):
        """Set up the test case."""
        # Set up environment variables
        self.original_environ = dict(os.environ)
        self.setup_env_vars()
        
        # Set up mock AWS services
        self.setup_mocks()
        
    def teardown_method(self):
        """Tear down the test case."""
        # Restore original environment variables
        os.environ.clear()
        os.environ.update(self.original_environ)
        
    def setup_env_vars(self):
        """Set up environment variables for the test case."""
        # Override in subclasses to set specific environment variables
        pass
        
    def setup_mocks(self):
        """Set up mock AWS services for the test case."""
        # Override in subclasses to set up specific mocks
        pass
        
    def create_event(
        self,
        method: str = "GET",
        path: str = "/",
        headers: Optional[Dict[str, str]] = None,
        query_params: Optional[Dict[str, str]] = None,
        path_params: Optional[Dict[str, str]] = None,
        body: Optional[Union[Dict[str, Any], str]] = None,
        cognito_auth: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create an API Gateway event for testing.
        
        Args:
            method: HTTP method
            path: API path
            headers: HTTP headers
            query_params: Query string parameters
            path_params: Path parameters
            body: Request body
            cognito_auth: Cognito authentication claims
            
        Returns:
            API Gateway event dictionary
        """
        return APIGatewayEvent(
            method=method,
            path=path,
            headers=headers,
            query_params=query_params,
            path_params=path_params,
            body=body,
            cognito_auth=cognito_auth
        ).build()
        
    def create_context(self) -> LambdaContext:
        """
        Create a Lambda context for testing.
        
        Returns:
            Lambda context object
        """
        return LambdaContext()
        
    def invoke_lambda(
        self,
        handler: Callable,
        event: Dict[str, Any],
        context: Optional[LambdaContext] = None
    ) -> Dict[str, Any]:
        """
        Invoke a Lambda handler function with the given event and context.
        
        Args:
            handler: Lambda handler function
            event: API Gateway event
            context: Lambda context (optional)
            
        Returns:
            Lambda response
        """
        if context is None:
            context = self.create_context()
            
        return handler(event, context)
        
    def assert_response(
        self,
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
        assert_lambda_response(
            response,
            expected_status_code,
            expected_body,
            expected_headers
        )


class DynamoDBTestCase(APITestCase):
    """
    Base class for API endpoint test cases that use DynamoDB.
    """
    
    # Define table definitions as class variables
    # Override in subclasses to define specific tables
    TABLES: List[Dict[str, Any]] = []
    
    def setup_mocks(self):
        """Set up mock DynamoDB for the test case."""
        super().setup_mocks()
        
        # Set up mock AWS
        self.aws_mock = mock_aws()
        self.aws_mock.start()
        
        # Create DynamoDB client and resource
        self.dynamodb_client = boto3.client("dynamodb", region_name="us-east-1")
        self.dynamodb_resource = boto3.resource("dynamodb", region_name="us-east-1")
        
        # Create tables
        self.create_tables()
        
    def teardown_method(self):
        """Tear down the test case."""
        super().teardown_method()
        
        # Stop mock AWS
        self.aws_mock.stop()
        
    def create_tables(self):
        """Create DynamoDB tables for the test case."""
        for table_def in self.TABLES:
            self.create_table(**table_def)
            
    def create_table(
        self,
        table_name: str,
        key_schema: List[Dict[str, str]],
        attribute_definitions: List[Dict[str, str]],
        billing_mode: str = "PAY_PER_REQUEST",
        provisioned_throughput: Optional[Dict[str, int]] = None,
        global_secondary_indexes: Optional[List[Dict[str, Any]]] = None,
        local_secondary_indexes: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Create a DynamoDB table.
        
        Args:
            table_name: Name of the table
            key_schema: Key schema for the table
            attribute_definitions: Attribute definitions for the table
            billing_mode: Billing mode (PAY_PER_REQUEST or PROVISIONED)
            provisioned_throughput: Provisioned throughput settings
            global_secondary_indexes: Global secondary indexes
            local_secondary_indexes: Local secondary indexes
            
        Returns:
            The created table
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
            
        if global_secondary_indexes:
            create_args["GlobalSecondaryIndexes"] = global_secondary_indexes
            
        if local_secondary_indexes:
            create_args["LocalSecondaryIndexes"] = local_secondary_indexes
            
        table = self.dynamodb_resource.create_table(**create_args)
        return table
        
    def put_item(self, table_name: str, item: Dict[str, Any]):
        """
        Put an item into a DynamoDB table.
        
        Args:
            table_name: Name of the table
            item: Item to put into the table
        """
        table = self.dynamodb_resource.Table(table_name)
        table.put_item(Item=item)
        
    def batch_write_items(self, table_name: str, items: List[Dict[str, Any]]):
        """
        Write multiple items to a DynamoDB table in batch.
        
        Args:
            table_name: Name of the table
            items: Items to write to the table
        """
        table = self.dynamodb_resource.Table(table_name)
        with table.batch_writer() as batch:
            for item in items:
                batch.put_item(Item=item)
                
    def get_item(
        self,
        table_name: str,
        key: Dict[str, Any],
        consistent_read: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Get an item from a DynamoDB table.
        
        Args:
            table_name: Name of the table
            key: Key of the item to get
            consistent_read: Whether to use consistent read
            
        Returns:
            The item, or None if not found
        """
        table = self.dynamodb_resource.Table(table_name)
        response = table.get_item(
            Key=key,
            ConsistentRead=consistent_read
        )
        return response.get("Item")
        
    def query_items(
        self,
        table_name: str,
        key_condition_expression,
        expression_attribute_values: Optional[Dict[str, Any]] = None,
        expression_attribute_names: Optional[Dict[str, str]] = None,
        filter_expression=None,
        index_name: Optional[str] = None,
        consistent_read: bool = False,
        scan_index_forward: bool = True,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Query items from a DynamoDB table.
        
        Args:
            table_name: Name of the table
            key_condition_expression: Key condition expression
            expression_attribute_values: Expression attribute values
            expression_attribute_names: Expression attribute names
            filter_expression: Filter expression
            index_name: Name of the index to query
            consistent_read: Whether to use consistent read
            scan_index_forward: Whether to scan the index forward
            limit: Maximum number of items to return
            
        Returns:
            List of items matching the query
        """
        table = self.dynamodb_resource.Table(table_name)
        
        query_args = {
            "KeyConditionExpression": key_condition_expression,
            "ConsistentRead": consistent_read,
            "ScanIndexForward": scan_index_forward
        }
        
        if expression_attribute_values:
            query_args["ExpressionAttributeValues"] = expression_attribute_values
            
        if expression_attribute_names:
            query_args["ExpressionAttributeNames"] = expression_attribute_names
            
        if filter_expression:
            query_args["FilterExpression"] = filter_expression
            
        if index_name:
            query_args["IndexName"] = index_name
            
        if limit:
            query_args["Limit"] = limit
            
        response = table.query(**query_args)
        return response.get("Items", [])


class S3TestCase(APITestCase):
    """
    Base class for API endpoint test cases that use S3.
    """
    
    # Define bucket names as class variables
    # Override in subclasses to define specific buckets
    BUCKETS: List[str] = []
    
    def setup_mocks(self):
        """Set up mock S3 for the test case."""
        super().setup_mocks()
        
        # Set up mock AWS
        self.aws_mock = mock_aws()
        self.aws_mock.start()
        
        # Create S3 client and resource
        self.s3_client = boto3.client("s3", region_name="us-east-1")
        self.s3_resource = boto3.resource("s3", region_name="us-east-1")
        
        # Create buckets
        self.create_buckets()
        
    def teardown_method(self):
        """Tear down the test case."""
        super().teardown_method()
        
        # Stop mock AWS
        self.aws_mock.stop()
        
    def create_buckets(self):
        """Create S3 buckets for the test case."""
        for bucket_name in self.BUCKETS:
            self.create_bucket(bucket_name)
            
    def create_bucket(self, bucket_name: str):
        """
        Create an S3 bucket.
        
        Args:
            bucket_name: Name of the bucket
            
        Returns:
            The created bucket
        """
        return self.s3_resource.create_bucket(Bucket=bucket_name)
        
    def put_object(
        self,
        bucket_name: str,
        key: str,
        body: Union[str, bytes],
        metadata: Optional[Dict[str, str]] = None
    ):
        """
        Put an object into an S3 bucket.
        
        Args:
            bucket_name: Name of the bucket
            key: Key of the object
            body: Content of the object
            metadata: Metadata for the object
        """
        put_args = {
            "Bucket": bucket_name,
            "Key": key,
            "Body": body
        }
        
        if metadata:
            put_args["Metadata"] = metadata
            
        self.s3_client.put_object(**put_args)
        
    def get_object(self, bucket_name: str, key: str) -> Dict[str, Any]:
        """
        Get an object from an S3 bucket.
        
        Args:
            bucket_name: Name of the bucket
            key: Key of the object
            
        Returns:
            The object
        """
        return self.s3_client.get_object(
            Bucket=bucket_name,
            Key=key
        )
        
    def list_objects(
        self,
        bucket_name: str,
        prefix: Optional[str] = None,
        delimiter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List objects in an S3 bucket.
        
        Args:
            bucket_name: Name of the bucket
            prefix: Prefix to filter objects
            delimiter: Delimiter to group objects
            
        Returns:
            List of objects
        """
        list_args = {
            "Bucket": bucket_name
        }
        
        if prefix:
            list_args["Prefix"] = prefix
            
        if delimiter:
            list_args["Delimiter"] = delimiter
            
        response = self.s3_client.list_objects_v2(**list_args)
        return response.get("Contents", [])


class CombinedTestCase(DynamoDBTestCase, S3TestCase):
    """
    Base class for API endpoint test cases that use both DynamoDB and S3.
    """
    
    def setup_mocks(self):
        """Set up mock AWS services for the test case."""
        # Since both parent classes use mock_aws, we only need to call one of them
        # to set up the mock AWS environment
        APITestCase.setup_mocks(self)
        
        # Set up mock AWS
        self.aws_mock = mock_aws()
        self.aws_mock.start()
        
        # Create DynamoDB client and resource
        self.dynamodb_client = boto3.client("dynamodb", region_name="us-east-1")
        self.dynamodb_resource = boto3.resource("dynamodb", region_name="us-east-1")
        
        # Create S3 client and resource
        self.s3_client = boto3.client("s3", region_name="us-east-1")
        self.s3_resource = boto3.resource("s3", region_name="us-east-1")
        
        # Create tables and buckets
        self.create_tables()
        self.create_buckets()
        
    def teardown_method(self):
        """Tear down the test case."""
        # Restore original environment variables
        os.environ.clear()
        os.environ.update(self.original_environ)
        
        # Stop mock AWS
        self.aws_mock.stop()


# Utility functions for API testing

def create_cognito_auth_claims(
    sub: str = "test-user-id",
    email: str = "test@example.com",
    username: str = "testuser",
    groups: Optional[List[str]] = None,
    custom_attributes: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Create Cognito authentication claims for testing.
    
    Args:
        sub: Subject (user ID)
        email: Email address
        username: Username
        groups: User groups
        custom_attributes: Custom attributes
        
    Returns:
        Cognito authentication claims
    """
    claims = {
        "sub": sub,
        "email": email,
        "cognito:username": username,
        "token_use": "id"
    }
    
    if groups:
        claims["cognito:groups"] = groups
        
    if custom_attributes:
        claims.update(custom_attributes)
        
    return claims


def create_api_gateway_event_with_auth(
    method: str = "GET",
    path: str = "/",
    headers: Optional[Dict[str, str]] = None,
    query_params: Optional[Dict[str, str]] = None,
    path_params: Optional[Dict[str, str]] = None,
    body: Optional[Union[Dict[str, Any], str]] = None,
    cognito_auth: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Create an API Gateway event with Cognito authentication for testing.
    
    Args:
        method: HTTP method
        path: API path
        headers: HTTP headers
        query_params: Query string parameters
        path_params: Path parameters
        body: Request body
        cognito_auth: Cognito authentication claims
        
    Returns:
        API Gateway event dictionary
    """
    if cognito_auth is None:
        cognito_auth = create_cognito_auth_claims()
        
    return APIGatewayEvent(
        method=method,
        path=path,
        headers=headers,
        query_params=query_params,
        path_params=path_params,
        body=body,
        cognito_auth=cognito_auth
    ).build()
