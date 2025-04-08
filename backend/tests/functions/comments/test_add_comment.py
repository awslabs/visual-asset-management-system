import pytest
import sys
import os
from unittest.mock import patch, MagicMock

# Add the necessary paths to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

# Import boto3 for the tests
import boto3

# Import the actual implementation
import backend.backend.handlers.comments.addComment as addComment


@pytest.fixture(scope="function")
def add_event():
    """
    Generates an event mocking what the API sends when it attempts to add a comment
    :returns: Lambda event dictionary
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
        "headers": {
            "Content-Type": "application/json",
            "Authorization": "Bearer test-token"
        }
    }


# Mock the dependencies
@pytest.fixture(autouse=True)
def mock_dependencies(monkeypatch):
    """
    Mock the dependencies needed by the addComment module
    """
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


def test_add_comment(comments_table, add_event, monkeypatch):
    """
    Testing the add comment function
    :param comments_table: mocked dynamoDB commentStorageTable
    :param add_event: Lamdba event dictionary for adding a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(addComment, "dynamodb", boto3.resource("dynamodb"))
    
    asset_id = "test-id"
    version_id_and_comment_id = "test-version-id:test-comment-id"
    user_id = "test_email@amazon.com"
    response = addComment.add_comment(asset_id, version_id_and_comment_id, user_id, add_event)
    assert response["statusCode"] == 200
    response = comments_table.get_item(Key={"assetId": asset_id, "assetVersionId:commentId": version_id_and_comment_id})
    assert response["Item"]["commentBody"] == "test comment body"
