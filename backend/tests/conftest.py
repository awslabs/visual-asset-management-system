"""
Pytest configuration file for VAMS backend tests.

This file contains fixtures and configuration for pytest tests.
"""

import boto3
import json
import os
import pytest
from datetime import datetime
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from moto import mock_aws

# Import test utilities
from backend.tests.utils.lambda_test_utils import (
    LambdaContext,
    APIGatewayEvent,
    mock_aws_service
)
from backend.tests.utils.api_test_utils import (
    create_cognito_auth_claims,
    create_api_gateway_event_with_auth
)

# Set default environment variables for tests
os.environ["COMMENT_STORAGE_TABLE_NAME"] = "commentStorageTable"
os.environ["METADATA_STORAGE_TABLE_NAME"] = "metadataStorageTable"
os.environ["ASSET_STORAGE_TABLE_NAME"] = "assetStorageTable"
os.environ["ASSET_BUCKET_NAME"] = "test-asset-bucket"
os.environ["REGION"] = "us-east-1"


@pytest.fixture(scope="function")
def lambda_context():
    """
    Fixture that provides a mock Lambda context object.
    
    Returns:
        LambdaContext: A mock Lambda context object
    """
    return LambdaContext()


@pytest.fixture(scope="function")
def api_gateway_event():
    """
    Fixture that provides a function to create API Gateway events.
    
    Returns:
        function: A function that creates API Gateway events
    """
    def _create_event(
        method="GET",
        path="/",
        headers=None,
        query_params=None,
        path_params=None,
        body=None,
        cognito_auth=None
    ):
        return APIGatewayEvent(
            method=method,
            path=path,
            headers=headers,
            query_params=query_params,
            path_params=path_params,
            body=body,
            cognito_auth=cognito_auth
        ).build()
    
    return _create_event


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
    Create the dynamoDB resource for testing
    
    Returns:
        boto3.resource: Mocked DynamoDB resource
    """
    with mock_aws():
        yield boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture(scope="function")
def s3_resource():
    """
    Create the S3 resource for testing
    
    Returns:
        boto3.resource: Mocked S3 resource
    """
    with mock_aws():
        yield boto3.resource("s3", region_name="us-east-1")


@pytest.fixture(scope="function")
def s3_client():
    """
    Create the S3 client for testing
    
    Returns:
        boto3.client: Mocked S3 client
    """
    with mock_aws():
        yield boto3.client("s3", region_name="us-east-1")


@pytest.fixture(scope="function")
def sfn_client():
    """
    Create the Step Functions client for testing
    
    Returns:
        boto3.client: Mocked Step Functions client
    """
    with mock_aws():
        yield boto3.client("stepfunctions", region_name="us-east-1")


@pytest.fixture(scope="function")
def opensearch_client():
    """
    Create the OpenSearch client for testing
    
    Returns:
        boto3.client: Mocked OpenSearch client
    """
    with mock_aws():
        yield boto3.client("opensearch", region_name="us-east-1")


@pytest.fixture(scope="function")
def comments_table(ddb_resource):
    """
    Create a table to store comments for testing
    
    Args:
        ddb_resource: Mocked DynamoDB resource
        
    Returns:
        boto3.resource.Table: Mocked comments table
    """
    comment_table_name = "commentStorageTable"
    comments_table = ddb_resource.create_table(
        TableName=comment_table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "assetId", "KeyType": "HASH"},
            {"AttributeName": "assetVersionId:commentId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "assetId", "AttributeType": "S"},
            {"AttributeName": "assetVersionId:commentId", "AttributeType": "S"},
        ],
    )

    return comments_table


@pytest.fixture(scope="function")
def metadata_table(ddb_resource):
    """
    Create a table to store metadata for testing
    
    Args:
        ddb_resource: Mocked DynamoDB resource
        
    Returns:
        boto3.resource.Table: Mocked metadata table
    """
    metadata_table_name = "metadataStorageTable"
    metadata_table = ddb_resource.create_table(
        TableName=metadata_table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": "assetId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": "assetId", "AttributeType": "S"},
        ],
    )

    return metadata_table


@pytest.fixture(scope="function")
def asset_table(ddb_resource):
    """
    Create a table to store assets for testing
    
    Args:
        ddb_resource: Mocked DynamoDB resource
        
    Returns:
        boto3.resource.Table: Mocked asset table
    """
    asset_table_name = "assetStorageTable"
    asset_table = ddb_resource.create_table(
        TableName=asset_table_name,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[
            {"AttributeName": "databaseId", "KeyType": "HASH"},
            {"AttributeName": "assetId", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "databaseId", "AttributeType": "S"},
            {"AttributeName": "assetId", "AttributeType": "S"},
        ],
    )

    return asset_table


@pytest.fixture(scope="function")
def asset_bucket(s3_resource):
    """
    Create an S3 bucket for storing assets
    
    Args:
        s3_resource: Mocked S3 resource
        
    Returns:
        boto3.resource.Bucket: Mocked S3 bucket
    """
    bucket_name = "test-asset-bucket"
    bucket = s3_resource.create_bucket(Bucket=bucket_name)
    return bucket


@pytest.fixture(scope="function")
def mock_cognito_auth():
    """
    Fixture to create mock Cognito authentication claims
    
    Returns:
        function: A function that creates Cognito authentication claims
    """
    def _create_auth(
        sub="test-user-id",
        email="test@example.com",
        username="testuser",
        groups=None,
        custom_attributes=None
    ):
        return create_cognito_auth_claims(
            sub=sub,
            email=email,
            username=username,
            groups=groups,
            custom_attributes=custom_attributes
        )
    
    return _create_auth


class TestComment:
    """
    Class to easily create comments for testing purposes
    """

    __test__ = False

    def __init__(
        self,
        asset_id="test-id",
        asset_version_id_and_comment_id="test-version-id:test-comment-id",
        comment_body="test comment body",
        comment_owner_id="test_email@amazon.com",
        comment_owner_username="test_email@amazon.com",
        date_created="2023-07-06T21:32:15.066148Z",
    ) -> None:
        """
        Creates a test_comment object based on the specified parameters
        
        Args:
            asset_id: Asset ID for the test comment to be attached to
            asset_version_id_and_comment_id: Version ID for the comment to be attached to and
                unique comment identifier
            comment_body: Body of the comment
            comment_owner_id: Cognito sub for the creator of the comment
            comment_owner_username: Cognito username for the creator of the comment
            date_created: Date the comment was created
        """
        self.asset_id = asset_id
        self.asset_version_id_and_comment_id = asset_version_id_and_comment_id
        self.comment_body = comment_body
        self.comment_owner_id = comment_owner_id
        self.comment_owner_username = comment_owner_username
        self.date_created = date_created

    def get_comment(self):
        """
        Return a dict that can be converted to JSON based on the comment information stored in the class
        
        Returns:
            dict: Dictionary representation of the test comment
        """
        return {
            "assetId": self.asset_id,
            "assetVersionId:commentId": self.asset_version_id_and_comment_id,
            "commentBody": self.comment_body,
            "commentOwnerID": self.comment_owner_id,
            "commentOwnerUsername": self.comment_owner_username,
            "dateCreated": self.date_created,
        }


class TestAsset:
    """
    Class to easily create assets for testing purposes
    """
    
    __test__ = False
    
    def __init__(
        self,
        asset_id="test-asset-id",
        database_id="test-database-id",
        asset_name="Test Asset",
        asset_type="model/gltf-binary",
        asset_size=1024,
        asset_owner_id="test_email@amazon.com",
        asset_owner_username="test_email@amazon.com",
        date_created=None,
        date_modified=None,
        metadata=None
    ) -> None:
        """
        Creates a test asset object based on the specified parameters
        
        Args:
            asset_id: Asset ID
            database_id: Database ID
            asset_name: Asset name
            asset_type: Asset MIME type
            asset_size: Asset size in bytes
            asset_owner_id: Cognito sub for the creator of the asset
            asset_owner_username: Cognito username for the creator of the asset
            date_created: Date the asset was created
            date_modified: Date the asset was last modified
            metadata: Additional metadata for the asset
        """
        self.asset_id = asset_id
        self.database_id = database_id
        self.asset_name = asset_name
        self.asset_type = asset_type
        self.asset_size = asset_size
        self.asset_owner_id = asset_owner_id
        self.asset_owner_username = asset_owner_username
        self.date_created = date_created or datetime.now().isoformat()
        self.date_modified = date_modified or self.date_created
        self.metadata = metadata or {}
        
    def get_asset(self):
        """
        Return a dict that can be converted to JSON based on the asset information stored in the class
        
        Returns:
            dict: Dictionary representation of the test asset
        """
        asset = {
            "assetId": self.asset_id,
            "databaseId": self.database_id,
            "assetName": self.asset_name,
            "assetType": self.asset_type,
            "assetSize": self.asset_size,
            "assetOwnerID": self.asset_owner_id,
            "assetOwnerUsername": self.asset_owner_username,
            "dateCreated": self.date_created,
            "dateModified": self.date_modified
        }
        
        # Add metadata if provided
        if self.metadata:
            asset.update(self.metadata)
            
        return asset


@pytest.fixture(scope="function")
def mock_lambda_client():
    """
    Fixture to mock the Lambda client
    
    Returns:
        MagicMock: Mocked Lambda client
    """
    mock_client = MagicMock()
    mock_client.invoke.return_value = {
        "StatusCode": 200,
        "Payload": MagicMock(
            read=MagicMock(
                return_value=json.dumps(
                    {"statusCode": 200, "body": json.dumps({"message": "Success"})}
                ).encode()
            )
        )
    }
    
    with patch("boto3.client", return_value=mock_client):
        yield mock_client
