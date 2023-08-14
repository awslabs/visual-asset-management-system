import boto3
import pytest
import os
from moto import mock_dynamodb

os.environ["COMMENT_STORAGE_TABLE_NAME"] = "commentStorageTable"


@pytest.fixture(scope="function")
def ddb_resource():
    """
    Create the dynamoDB resource to store the comments table
    :returns: mocked dynabmoDB resource
    """
    with mock_dynamodb():
        yield boto3.resource("dynamodb", region_name="us-east-1")


@pytest.fixture(scope="function", autouse=True)
@mock_dynamodb
def comments_table(ddb_resource):
    """
    Create a table to store comments for testing
    :param ddb_resource: mocked dynamoDB resource
    :returns: mocked commentStorageTable
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
        comment_owner_id="test_sub",
        comment_owner_username="test_email@amazon.com",
        date_created="2023-07-06T21:32:15.066148Z",
    ) -> None:
        """
        Creates a test_comment object based on the specified parameters
        :param asset_id: assetid for the test comment to be attached to
        :param asset_version_id_and_comment_id: version id for the comment to be attached to and
                unique comment identifier
        :param comment_body: body of the comment
        :param comment_owner_id: Cognito sub for the crateor of the comment
        :param comment_owner_username: Cognito username for the creator of the comment
        :param date_created: date the comment was created
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
        :returns: dict of the testComment object
        """
        return {
            "assetId": self.asset_id,
            "assetVersionId:commentId": self.asset_version_id_and_comment_id,
            "commentBody": self.comment_body,
            "commentOwnerID": self.comment_owner_id,
            "commentOwnerUsername": self.comment_owner_username,
            "dateCreated": self.date_created,
        }
