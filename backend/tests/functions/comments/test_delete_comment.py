import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# Add the necessary paths to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from tests.conftest import TestComment

# Import boto3 for the tests
import boto3

# Import the actual implementation
import backend.backend.handlers.comments.commentService as commentService


@pytest.fixture(scope="function")
def delete_event():
    """
    Generates an event mocking what the API sends when it attempts to add a comment
    :returns: Lambda event dictionary
    """
    return {
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
                "method": "DELETE"
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
    Mock the dependencies needed by the commentService module
    """
    # Mock the comment_database
    monkeypatch.setattr(commentService, "comment_database", "commentStorageTable")
    
    # Mock the request_to_claims function
    def mock_request_to_claims(event):
        return {"tokens": ["test_token"]}
    
    monkeypatch.setattr(commentService, "request_to_claims", mock_request_to_claims)
    
    # Mock the CasbinEnforcer class
    class MockCasbinEnforcer:
        def __init__(self, claims_and_roles):
            pass
        
        def enforce(self, asset_object, action):
            return True
        
        def enforceAPI(self, event):
            return True
    
    monkeypatch.setattr(commentService, "CasbinEnforcer", MockCasbinEnforcer)
    
    # Mock the get_asset_object_from_id function
    def mock_get_asset_object_from_id(asset_id):
        return {"assetId": asset_id}
    
    monkeypatch.setattr(commentService, "get_asset_object_from_id", mock_get_asset_object_from_id)
    
    # Mock the validate function
    def mock_validate(params):
        return (True, "")
    
    monkeypatch.setattr(commentService, "validate", mock_validate)
    
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
    
    monkeypatch.setattr(commentService, "logger", MockLogger())


def test_delete_comment(comments_table, delete_event, monkeypatch):
    """
    Testing the delete comment function with a valid comment to delete
    :param comments_table: mocked dynamoDB commentStorageTable
    :param delete_event: Lambda event dictionary for deleting a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    asset_id = "test-id"
    asset_version_id_and_comment_id = "test-version-id:test-comment-id"
    comment_owner_id = "test_sub"

    test_valid_comment = TestComment(
        asset_id=asset_id,
        asset_version_id_and_comment_id=asset_version_id_and_comment_id,
        comment_owner_id=comment_owner_id,
    ).get_comment()

    comments_table.put_item(Item=test_valid_comment)

    response = commentService.delete_comment(asset_id, asset_version_id_and_comment_id, delete_event)
    assert response["statusCode"] == 200

    response = comments_table.get_item(
        Key={
            "assetId": asset_id,
            "assetVersionId:commentId": asset_version_id_and_comment_id,
        }
    )

    assert "Item" not in response

    response = comments_table.get_item(
        Key={
            "assetId": asset_id + "#deleted",
            "assetVersionId:commentId": asset_version_id_and_comment_id,
        }
    )

    deleted_comment = TestComment(
        asset_id=asset_id + "#deleted",
        comment_owner_id=comment_owner_id
    ).get_comment()

    # Compare only the fields we care about
    assert response["Item"]["assetId"] == deleted_comment["assetId"]
    assert response["Item"]["assetVersionId:commentId"] == deleted_comment["assetVersionId:commentId"]
    assert response["Item"]["commentBody"] == deleted_comment["commentBody"]
    assert response["Item"]["commentOwnerUsername"] == deleted_comment["commentOwnerUsername"]
    assert response["Item"]["dateCreated"] == deleted_comment["dateCreated"]


def test_delete_comment_not_exist(comments_table, delete_event, monkeypatch):
    """
    Testing the delete function with a comment that does not exist
    :param comments_table: mocked dynamoDB commenStorageTable
    :param delete_event: Lambda event dictionary for deleting a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    asset_id = "test-id"
    asset_version_id_and_comment_id = "test-version-id:test-comment-id"

    response = commentService.delete_comment(asset_id, asset_version_id_and_comment_id, delete_event)
    assert response["statusCode"] == 404


def test_delete_comment_wrong_owner(comments_table, delete_event, monkeypatch):
    """
    Testing the delete comment function with a valid comment to delete but an invalid owner
    :param comments_table: mocked dynamoDB commentStorageTable
    :param delete_event: Lambda event dictionary for deleting a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    asset_id = "test-id"
    asset_version_id_and_comment_id = "test-version-id:test-comment-id"

    test_unowned_comment = TestComment(
        asset_id="test-id",
        asset_version_id_and_comment_id=asset_version_id_and_comment_id,
        comment_owner_id="test_sub_2",
    ).get_comment()

    comments_table.put_item(Item=test_unowned_comment)

    response = commentService.delete_comment(asset_id, asset_version_id_and_comment_id, delete_event)
    assert response["statusCode"] == 401
    assert response["message"] == "Unauthorized"
