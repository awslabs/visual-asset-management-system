import json
import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# Add the necessary paths to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from backend.tests.conftest import TestComment

# Import boto3 for the tests
import boto3

# Import the actual implementation
import backend.backend.handlers.comments.commentService as commentService


@pytest.fixture(scope="function")
def delete_event():
    """
    Generates an event mocking what the API sends when it attempts to get all comments for a single asset
    :returns: Lambda event to get all comments
    """
    return {
        "requestContext": {
            "http": {"method": "DELETE"},
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "test_sub",
                        "email": "test_email@amazon.com",
                    }
                }
            },
        },
        "pathParameters": {
            "assetId": "test-id",
            "assetVersionId:commentId": "test-version-id:test-comment-id",
        },
    }


@pytest.fixture(scope="function")
def invalid_delete_event():
    """
    Generates an event mocking what the API sends when it attempts to get all comments for a single asset
    :returns: Lambda event to get all comments
    """
    return {
        "requestContext": {
            "http": {"method": "DELETE"},
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "test_sub",
                        "email": "test_email@amazon.com",
                    }
                }
            },
        },
        "pathParameters": {},
    }


# Mock the dependencies
@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    """
    Mock the dependencies needed by the commentService module
    """
    # Set environment variables
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("COMMENT_STORAGE_TABLE_NAME", "commentStorageTable")
    
    # Mock the comment_database
    monkeypatch.setattr(commentService, "comment_database", "commentStorageTable")
    
    # Mock the request_to_claims function
    def mock_request_to_claims(event):
        return {"tokens": ["test_email@amazon.com"]}
    
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
    Testing the delete comment Lambda handler with  delete a comment from the database
    :param comments_table: mocked dynamoDB commentStorageTable
    :param delete_event: Lambda event to get delete a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    # Generate the testing comment with the same user ID as in the delete_event
    test_comment_instance = TestComment(
        comment_owner_id="test_sub"
    ).get_comment()
    
    # Add the testing comment to the table
    comments_table.put_item(Item=test_comment_instance)
    # Get the testing comment from the table
    response = comments_table.get_item(
        Key={
            "assetId": test_comment_instance["assetId"],
            "assetVersionId:commentId": test_comment_instance["assetVersionId:commentId"],
        }
    )
    # make sure the testing comment was added succesfully
    assert response["Item"] == test_comment_instance

    response = commentService.lambda_handler(delete_event, None)
    # Validate the status code of the response
    assert response["statusCode"] == 200
    response_body = json.loads(response["body"])
    assert response_body["message"] == "Comment deleted"

    response = comments_table.get_item(
        Key={
            "assetId": test_comment_instance["assetId"],
            "assetVersionId:commentId": test_comment_instance["assetVersionId:commentId"],
        }
    )

    assert "Item" not in response


def test_delete_comment_not_exists(comments_table, delete_event, monkeypatch):
    """
    Testing the delete comment Lambda handler for a comment that does not exist in the database
    :param comments_table: mocked dynamoDB commentStorageTable
    :param delete_event: Lambda event to delete a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    response = commentService.lambda_handler(delete_event, None)
    assert response["statusCode"] == 404


def test_delete_comment_wrong_owner(comments_table, delete_event, monkeypatch):
    """
    Testing the delete comment Lambda handler with a valid comment to delete but an invalid owner
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
        asset_id=asset_id,
        asset_version_id_and_comment_id=asset_version_id_and_comment_id,
        comment_owner_id="test_sub_2",
    ).get_comment()

    comments_table.put_item(Item=test_unowned_comment)

    response = commentService.lambda_handler(delete_event, None)
    print(response)
    assert response["statusCode"] == 401
    response_body = json.loads(response["body"])
    assert response_body["message"] == "Unauthorized"


def test_delete_comment_invalid_event(comments_table, invalid_delete_event, monkeypatch):
    """
    Testing the delete comment Lambda handler with an invalid delete event
    :param comments_table: mocked dynamoDB commentStorageTable
    :param delete_event_invalid: invalid Lambda event dictionary for deleting a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    response = commentService.lambda_handler(invalid_delete_event, None)
    print(response)
    assert response["statusCode"] == 400
