import boto3
import pytest
import os
from moto import mock_dynamodb


os.environ["COMMENT_STORAGE_TABLE_NAME"] = "commentStorageTable"


@pytest.fixture(scope="session", autouse=True)
def execute_before_any_test():
    os.environ["COMMENT_STORAGE_TABLE_NAME"] = "COMMENT_STORAGE_TABLE"


@pytest.fixture(scope="function")
def aws_credentials():
    """
    Mocked AWS credentials for moto
    """
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_REGION"] = "us-east-1"


@pytest.fixture(scope="function")
def ddb_resource(aws_credentials):
    """
    Create the dynamoDB resource to store the comments table
    """
    with mock_dynamodb():
        yield boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture(scope="function", autouse=True)
@mock_dynamodb
def comments_table(ddb_resource):
    """
    Create a table to store comments for testing
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


def generate_comment():
    """
    Returns JSON object representing a comment that can be used for testing
    """
    return {
        "assetId": "x5f695ed7-1076-4f29-89e6-bffc7c6f3d7d",
        "assetVersionId:commentId": "zRwqtjAfexK6GZ.7_1vnEzsxUHcJ49T8:xebcaa59a-b53a-4998-a66e-b2bab933085f",
        "commentBody": "test comment body",
        "commentOwnerID": "test_sub",
        "commentOwnerUsername": "test_email@amazon.com",
        "dateCreated": "2023-07-06T21:32:15.066148Z",
    }
