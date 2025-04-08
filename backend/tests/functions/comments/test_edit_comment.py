import pytest
import datetime
import sys
import os
from unittest.mock import patch, MagicMock

# Add the necessary paths to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from tests.conftest import TestComment

# Import boto3 for the tests
import boto3

# Import the actual implementation
import backend.backend.handlers.comments.editComment as editComment
import backend.backend.handlers.comments.commentService as commentService


@pytest.fixture(scope="function")
def edit_event():
    """
    Generates an event mocking what the API sends when it attempts to add a comment
    :returns: Lambda event dictionary
    """
    return {
        "body": {
            "commentBody": "new test comment body",
        },
        "requestContext": {
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "test_sub",
                        "email": "test_email@amazon.com",
                    }
                }
            },
            "http": {
                "method": "PUT"
            }
        },
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer test-token"
        }
    }


# Mock the dependencies
@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    """
    Mock the dependencies needed by the editComment module
    """
    # Mock the comment_database
    monkeypatch.setattr(editComment, "comment_database", "commentStorageTable")
    monkeypatch.setattr(commentService, "comment_database", "commentStorageTable")
    
    # Mock the request_to_claims function
    def mock_request_to_claims(event):
        return {"tokens": ["test_token"]}
    
    monkeypatch.setattr(editComment, "request_to_claims", mock_request_to_claims)
    
    # Mock the CasbinEnforcer class
    class MockCasbinEnforcer:
        def __init__(self, claims_and_roles):
            pass
        
        def enforce(self, asset_object, action):
            return True
        
        def enforceAPI(self, event):
            return True
    
    monkeypatch.setattr(editComment, "CasbinEnforcer", MockCasbinEnforcer)
    
    # Mock the get_asset_object_from_id function
    def mock_get_asset_object_from_id(asset_id):
        return {"assetId": asset_id}
    
    monkeypatch.setattr(editComment, "get_asset_object_from_id", mock_get_asset_object_from_id)
    
    # Mock the validate function
    def mock_validate(params):
        return (True, "")
    
    monkeypatch.setattr(editComment, "validate", mock_validate)
    
    # Mock the logger
    class MockLogger:
        def info(self, message):
            pass
        
        def warning(self, message):
            pass
        
        def error(self, message):
            pass
        
        def exception(self, message):
            pass
    
    monkeypatch.setattr(editComment, "logger", MockLogger())


def test_edit_comment(comments_table, edit_event, monkeypatch):
    """
    Testing the edit comment function with a valid event
    :param comments_table: mocked dynamoDB commentStorageTable
    :param edit_event: Lamdba event dictionary for editing a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(editComment, "dynamodb", boto3.resource("dynamodb"))
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    # Mock the get_single_comment function to use the mocked table
    def mock_get_single_comment(assetId, assetVersionIdAndCommentId, showDeleted=False):
        table = boto3.resource("dynamodb").Table("commentStorageTable")
        response = table.get_item(Key={"assetId": assetId, "assetVersionId:commentId": assetVersionIdAndCommentId})
        return response.get("Item", {})
    
    monkeypatch.setattr(editComment, "get_single_comment", mock_get_single_comment)
    
    asset_id = "test-id"
    asset_version_id_and_comment_id = "test-version-id:test-comment-id"
    test_valid_comment = TestComment(
        asset_id=asset_id,
        asset_version_id_and_comment_id=asset_version_id_and_comment_id,
        comment_body="test comment body",
        comment_owner_id="test_sub",
    ).get_comment()

    comments_table.put_item(Item=test_valid_comment)
    response = editComment.edit_comment(asset_id, asset_version_id_and_comment_id, edit_event)
    assert response["statusCode"] == 200

    response = comments_table.get_item(
        Key={
            "assetId": asset_id,
            "assetVersionId:commentId": asset_version_id_and_comment_id,
        }
    )
    print(response)

    # compute the difference between the dateEdited attribute and the current time
    assert response["Item"]["commentBody"] == "new test comment body"
    time_difference = datetime.datetime.utcnow() - datetime.datetime.strptime(
        response["Item"]["dateEdited"], "%Y-%m-%dT%H:%M:%S.%fZ"
    )
    # assert the difference between the two times is less than 100 seconds
    assert time_difference.total_seconds() < 100


def test_edit_comment_not_exist(comments_table, edit_event, monkeypatch):
    """
    Testing the edit comment function with an invalid event (nonexistent comment)
    :param comments_table: mocked dynamoDB commentStorageTable
    :param edit_event: Lamdba event dictionary for editing a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(editComment, "dynamodb", boto3.resource("dynamodb"))
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    # Mock the get_single_comment function to use the mocked table
    def mock_get_single_comment(assetId, assetVersionIdAndCommentId, showDeleted=False):
        table = boto3.resource("dynamodb").Table("commentStorageTable")
        response = table.get_item(Key={"assetId": assetId, "assetVersionId:commentId": assetVersionIdAndCommentId})
        return response.get("Item", {})
    
    monkeypatch.setattr(editComment, "get_single_comment", mock_get_single_comment)
    
    asset_id = "test-id"
    asset_version_id_and_comment_id = "test-version-id:test-comment-id"

    response = editComment.edit_comment(asset_id, asset_version_id_and_comment_id, edit_event)
    assert response["statusCode"] == 404


def test_edit_comment_wrong_owner(comments_table, edit_event, monkeypatch):
    """
    Testing the edit comment function with a valid comment but an invalid owner
    :param comments_table: mocked dynamoDB commentStorageTable
    :param edit_event: Lamdba event dictionary for editing a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(editComment, "dynamodb", boto3.resource("dynamodb"))
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    # Mock the get_single_comment function to use the mocked table
    def mock_get_single_comment(assetId, assetVersionIdAndCommentId, showDeleted=False):
        table = boto3.resource("dynamodb").Table("commentStorageTable")
        response = table.get_item(Key={"assetId": assetId, "assetVersionId:commentId": assetVersionIdAndCommentId})
        return response.get("Item", {})
    
    monkeypatch.setattr(editComment, "get_single_comment", mock_get_single_comment)
    
    asset_id = "test-id"
    asset_version_id_and_comment_id = "test-version-id:test-comment-id"

    test_unowned_comment = TestComment(
        asset_id="test-id",
        asset_version_id_and_comment_id=asset_version_id_and_comment_id,
        comment_owner_id="test_sub_2",
    ).get_comment()

    comments_table.put_item(Item=test_unowned_comment)

    response = editComment.edit_comment(asset_id, asset_version_id_and_comment_id, edit_event)
    assert response["statusCode"] == 403
    assert response["message"] == "Unauthorized"
