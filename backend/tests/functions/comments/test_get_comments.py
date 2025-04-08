import sys
import os
import pytest
from unittest.mock import patch, MagicMock

# Add the necessary paths to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from tests.conftest import TestComment

# Import boto3 for the tests
import boto3

# Import the actual implementation
import backend.backend.handlers.comments.commentService as commentService


# Mock validate_pagination_info function
def validate_pagination_info(queryParams):
    """
    Mock implementation of validate_pagination_info
    """
    if "maxItems" not in queryParams:
        queryParams["maxItems"] = 100
    if "pageSize" not in queryParams:
        queryParams["pageSize"] = 100
    if "startingToken" not in queryParams:
        queryParams["startingToken"] = None
    return queryParams


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
    
    # Mock the validate_pagination_info function
    monkeypatch.setattr(commentService, "validate_pagination_info", validate_pagination_info)


def test_get_all_comments(comments_table, monkeypatch):
    """
    Testing the get_all_comments function from commentService that should return all comments in the db
    :param comments_table: mocked dynamoDB commentStorageTable
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    monkeypatch.setattr(commentService, "dynamodb_client", boto3.client("dynamodb"))
    
    # Generate two seperate comments for testing
    test_valid_comment = TestComment().get_comment()
    test_valid_comment_2 = TestComment(
        asset_version_id_and_comment_id="test-version-id:test-comment-id-2"
    ).get_comment()
    # Generate a third comment with different everything that should also be returned by the function
    test_valid_comment_3 = TestComment(
        asset_id="test-id-2",
        asset_version_id_and_comment_id="test-version-id-2:test-comment-id-3",
    ).get_comment()
    # Add the testing comment to the table
    comments_table.put_item(Item=test_valid_comment)
    comments_table.put_item(Item=test_valid_comment_2)
    comments_table.put_item(Item=test_valid_comment_3)

    # call get all comments function
    query_params = {}
    validate_pagination_info(query_params)
    response = commentService.get_all_comments(query_params)
    assert test_valid_comment in response["Items"]
    assert test_valid_comment_2 in response["Items"]
    assert test_valid_comment_3 in response["Items"]


def test_get_comments_asset(comments_table, monkeypatch):
    """
    Testing the get_comments function that should get all comments from the db with the given asset id
    :param comments_table: mocked dynamoDB commentStorageTable
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    # Generate two seperate comments for testing
    test_valid_comment = TestComment(asset_id="test-id").get_comment()
    test_valid_comment_2 = TestComment(
        asset_id="test-id", asset_version_id_and_comment_id="test-version-id-2"
    ).get_comment()
    # Generate a third comment with a different assetId that should not be returned by the function
    test_invalid_comment = TestComment(asset_id="invalid-test-id").get_comment()
    # Add the testing comment to the table
    comments_table.put_item(Item=test_valid_comment)
    comments_table.put_item(Item=test_valid_comment_2)
    comments_table.put_item(Item=test_invalid_comment)

    # call get comments function
    query_params = {}
    validate_pagination_info(query_params)
    response = commentService.get_comments("test-id", query_params)
    print(response)

    # make sure only two comments were returned
    assert len(response) == 2
    # make sure only the expected comments were returned
    assert test_valid_comment in response
    assert test_valid_comment_2 in response
    assert test_invalid_comment not in response


def test_get_comments_version(comments_table, monkeypatch):
    """
    Testing the get_comments_version function that should get
    all comments with a given asset id and assetVersionId from db
    :param comments_table: mocked dynamoDB commentStorageTable
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    test_valid_comment = TestComment(
        asset_id="test-id",
        asset_version_id_and_comment_id="test-version-id:test-comment-id",
    ).get_comment()
    test_valid_comment_2 = TestComment(
        asset_id="test-id",
        asset_version_id_and_comment_id="test-version-id:test-comment-id-2",
    ).get_comment()
    # Generate a third comment with a different versionId that should not be returned by the function
    test_invalid_comment = TestComment(
        asset_id="test-id",
        asset_version_id_and_comment_id="invalid-test-version-id:test-comment-id-3",
    ).get_comment()
    # Generate a fourth comment with a different assetId
    # but the same versionId that should not be returned by the function
    test_invalid_comment_2 = TestComment(
        asset_id="test-id-2",
        asset_version_id_and_comment_id="test-version-id:test-comment-id-4",
    ).get_comment()
    # Add the testing comment to the table
    comments_table.put_item(Item=test_valid_comment)
    comments_table.put_item(Item=test_valid_comment_2)
    comments_table.put_item(Item=test_invalid_comment)
    comments_table.put_item(Item=test_invalid_comment_2)

    # call get comments version function
    query_params = {}
    validate_pagination_info(query_params)
    response = commentService.get_comments_version("test-id", "test-version-id", query_params)
    # make sure only 2 comments were returned
    assert len(response) == 2
    # make sure only the expected comments were returned
    assert test_valid_comment in response
    assert test_valid_comment_2 in response
    assert test_invalid_comment not in response
    assert test_invalid_comment_2 not in response


def test_get_single_comment(comments_table, monkeypatch):
    """
    Testing the get_single_comment function that should return the comment with the given assetId
    and assetVersionId:commentId pair from db
    :param comments_table: mocked dynamoDB commentStorageTable
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    # Generate a testing comment
    test_valid_comment = TestComment(
        asset_id="test-id",
        asset_version_id_and_comment_id="test-version-id:test-comment-id",
    ).get_comment()
    # Add the testing comment to the table
    comments_table.put_item(Item=test_valid_comment)

    response = commentService.get_single_comment("test-id", "test-version-id:test-comment-id")
    print(response)

    assert test_valid_comment == response
