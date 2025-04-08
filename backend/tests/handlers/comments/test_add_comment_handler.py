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
import backend.backend.handlers.comments.addComment as addComment


@pytest.fixture(scope="function")
def add_valid_event(assetId="test-id", assetVersionIdAndCommentId="test-version-id:test-comment-id"):
    """
    Generates an event mocking what the API sends when it attempts to add a comment
    :param assetId: assetId for the comment to be added to
    :param assetVersionIdAndCommentId: version id for comment to be added to and unique identifier for the comment
    :returns: Lambda event dictionary for valid add event
    """
    return {
        "body": {
            "commentBody": "test comment body",
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
                "method": "POST"
            }
        },
        "pathParameters": {
            "assetId": assetId,
            "assetVersionId:commentId": assetVersionIdAndCommentId,
        },
    }


@pytest.fixture(scope="function")
def add_invalid_event():
    """
    Generates an event mocking what the API sends when it attempts to add a comment
    :returns: Lambda event dictionary for invalid add event
    """
    return {
        "body": {
            "commentBody": "test comment body",
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
                "method": "POST"
            }
        },
        "pathParameters": {},
    }


# Mock the dependencies
@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    """
    Mock the dependencies needed by the addComment module
    """
    # Set environment variables
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("COMMENT_STORAGE_TABLE_NAME", "commentStorageTable")
    
    # Mock the comment_database
    monkeypatch.setattr(addComment, "comment_database", "commentStorageTable")
    
    # Mock the request_to_claims function
    def mock_request_to_claims(event):
        return {"tokens": ["test_email@amazon.com"]}
    
    monkeypatch.setattr(addComment, "request_to_claims", mock_request_to_claims)
    
    # Mock the CasbinEnforcer class
    class MockCasbinEnforcer:
        def __init__(self, claims_and_roles):
            pass
        
        def enforce(self, asset_object, action):
            return True
        
        def enforceAPI(self, event):
            return True
    
    monkeypatch.setattr(addComment, "CasbinEnforcer", MockCasbinEnforcer)
    
    # Mock the get_asset_object_from_id function
    def mock_get_asset_object_from_id(asset_id):
        return {"assetId": asset_id}
    
    monkeypatch.setattr(addComment, "get_asset_object_from_id", mock_get_asset_object_from_id)
    
    # Mock the validate function
    def mock_validate(params):
        return (True, "")
    
    monkeypatch.setattr(addComment, "validate", mock_validate)
    
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
    
    monkeypatch.setattr(addComment, "logger", MockLogger())


def test_add_comment(comments_table, add_valid_event, monkeypatch):
    """
    Testing the add comment lambda handler with valid data
    :param comments_table: mocked dynamoDB commentStorageTable
    :param add_valid_event: Lambda event dictionary for adding a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(addComment, "dynamodb", boto3.resource("dynamodb"))
    
    # Calling the lambda handler with the event
    response = addComment.lambda_handler(add_valid_event, None)
    print(response)
    assert response["statusCode"] == 200
    assetId = add_valid_event["pathParameters"]["assetId"]
    assetVersionIdAndCommentId = add_valid_event["pathParameters"]["assetVersionId:commentId"]
    # Getting the comment that should have been added by the lambda handler
    response = comments_table.get_item(Key={"assetId": assetId, "assetVersionId:commentId": assetVersionIdAndCommentId})
    actual_output = response["Item"]
    expected_output = TestComment().get_comment()
    # Removing the dateCreated from the comments bc they will differ (since they are auto generated)
    del actual_output["dateCreated"]
    del expected_output["dateCreated"]
    # Expected comment and actual comment should be the same
    assert actual_output == expected_output


def test_add_comment_invalid(comments_table, add_invalid_event, monkeypatch):
    """
    Testing the add comment lambda handler with valid data
    :param comments_table: mocked dynamoDB commentStorageTable
    :param add_invalid_event: Lambda event dictionary for adding an invalid comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(addComment, "dynamodb", boto3.resource("dynamodb"))
    
    # Calling the lambda handler with the event
    response = addComment.lambda_handler(add_invalid_event, None)
    print(response)
    assert response["statusCode"] == 400
