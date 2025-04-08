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
def get_all_event():
    """
    Generates an event mocking what the API sends when it attempts to get all comments for a single asset
    :returns: Lambda event to get all comments
    """
    return {
        "requestContext": {
            "http": {"method": "GET"},
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
        "queryStringParameters": {
            "maxItems": "10",
            "pageSize": "10",
            "startingToken": ""
        }
    }


@pytest.fixture(scope="function")
def get_one_asset_event(assetId="test-id"):
    """
    Generates an event mocking what the API sends when it attempts to get all comments for a single asset
    :param assetId: assetId to get comments for
    :returns: Lambda event to get all comments for the given assetId
    """
    return {
        "requestContext": {
            "http": {"method": "GET"},
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
            "assetId": assetId,
        },
        "queryStringParameters": {
            "maxItems": "10",
            "pageSize": "10",
            "startingToken": ""
        }
    }


@pytest.fixture(scope="function")
def get_one_version_event(assetId="test-id", assetVersionId="test-version-id"):
    """
    Generates an event mocking what the API sends when it attempts to get all comments for a single asset
    :param assetId: assetId to get comments for
    :param assetVersionId: version id to get comments for
    :returns: Lambda event to get all the comments for the given version of the given asset
    """
    return {
        "requestContext": {
            "http": {"method": "GET"},
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
            "assetId": assetId,
            "assetVersionId": assetVersionId,
        },
        "queryStringParameters": {
            "maxItems": "10",
            "pageSize": "10",
            "startingToken": ""
        }
    }


@pytest.fixture(scope="function")
def get_single_event(assetId="test-id", assetVersionIdAndCommentId="test-version-id:test-comment-id"):
    """
    Generates an event mocking what the API sends when it attempts to get a single comment
    :param assetId: assetId to get comments for
    :param assetVersionIdAndCommentId: version id for asset and unique comment id
    :returns: Lambda event to get the given comment
    """
    return {
        "requestContext": {
            "http": {"method": "GET"},
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
            "assetId": assetId,
            "assetVersionId:commentId": assetVersionIdAndCommentId,
        },
        "queryStringParameters": {
            "maxItems": "10",
            "pageSize": "10",
            "startingToken": ""
        }
    }


@pytest.fixture(scope="function")
def invalid_get_event():
    """
    Generates an event mocking what the API sends when it attempts to get all comments for a single asset
    :returns: Lambda event with an invalid assetId
    """
    return {
        "requestContext": {
            "http": {"method": "GET"},
            "authorizer": {
                "jwt": {
                    "claims": {
                        "sub": "test_sub",
                        "email": "test_email@amazon.com",
                    }
                }
            },
        },
        "pathParameters": {"assetId": "invalidID"},
        "queryStringParameters": {
            "maxItems": "10",
            "pageSize": "10",
            "startingToken": ""
        }
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
    
    # Mock the validate_pagination_info function
    def mock_validate_pagination_info(query_params):
        if not query_params:
            query_params = {}
        if "maxItems" not in query_params:
            query_params["maxItems"] = "10"
        if "pageSize" not in query_params:
            query_params["pageSize"] = "10"
        if "startingToken" not in query_params:
            query_params["startingToken"] = ""
        return query_params
    
    monkeypatch.setattr(commentService, "validate_pagination_info", mock_validate_pagination_info)
    
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


def test_get_all_comments(comments_table, get_all_event, monkeypatch):
    """
    Testing reading all comments for one asset (using assetId)
    :param comments_table: mocked dynamoDB commentStorageTable
    :param get_all_event: Lambda event to get all comments
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
    # Get the testing comments from the table
    response = comments_table.get_item(
        Key={
            "assetId": test_valid_comment["assetId"],
            "assetVersionId:commentId": test_valid_comment["assetVersionId:commentId"],
        }
    )
    # make sure the testing comments were added succesfully
    assert response["Item"] == test_valid_comment
    response = comments_table.get_item(
        Key={
            "assetId": test_valid_comment_2["assetId"],
            "assetVersionId:commentId": test_valid_comment_2["assetVersionId:commentId"],
        }
    )
    assert response["Item"] == test_valid_comment_2
    response = comments_table.get_item(
        Key={
            "assetId": test_valid_comment_3["assetId"],
            "assetVersionId:commentId": test_valid_comment_3["assetVersionId:commentId"],
        }
    )
    assert response["Item"] == test_valid_comment_3

    response = commentService.lambda_handler(get_all_event, None)
    # Validate the status code of the response
    assert response["statusCode"] == 200
    response_body = json.loads(response["body"])
    print(response_body["message"]["Items"])
    # make sure all 3 comments were returned
    assert len(response_body["message"]["Items"]) == 3
    # make sure all expected comments were returned
    assert test_valid_comment in response_body["message"]["Items"]
    assert test_valid_comment_2 in response_body["message"]["Items"]
    assert test_valid_comment_3 in response_body["message"]["Items"]


def test_get_all_asset_comments(comments_table, get_one_asset_event, monkeypatch):
    """
    Testing reading all comments for one asset (using assetId)
    :param comments_table: mocked dynamoDB commentStorageTable
    :param get_one_asset_event: Lambda event to get all comments for a given assetId
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    # Generate two seperate comments for testing
    test_valid_comment = TestComment().get_comment()
    test_valid_comment_2 = TestComment(asset_version_id_and_comment_id="test-version-id-2").get_comment()
    # Generate a third comment with a different assetId that should not be returned by the function
    test_invalid_comment = TestComment(asset_id="invalid-test-id").get_comment()
    # Add the testing comment to the table
    comments_table.put_item(Item=test_valid_comment)
    comments_table.put_item(Item=test_valid_comment_2)
    comments_table.put_item(Item=test_invalid_comment)
    # Get the testing comments from the table
    response = comments_table.get_item(
        Key={
            "assetId": test_valid_comment["assetId"],
            "assetVersionId:commentId": test_valid_comment["assetVersionId:commentId"],
        }
    )
    # make sure the testing comments were added succesfully
    assert response["Item"] == test_valid_comment
    response = comments_table.get_item(
        Key={
            "assetId": test_valid_comment_2["assetId"],
            "assetVersionId:commentId": test_valid_comment_2["assetVersionId:commentId"],
        }
    )
    assert response["Item"] == test_valid_comment_2
    response = comments_table.get_item(
        Key={
            "assetId": test_invalid_comment["assetId"],
            "assetVersionId:commentId": test_invalid_comment["assetVersionId:commentId"],
        }
    )
    assert response["Item"] == test_invalid_comment

    response = commentService.lambda_handler(get_one_asset_event, None)
    # Validate the status code of the response
    assert response["statusCode"] == 200
    response_body = json.loads(response["body"])
    # make sure only two comments were returned
    assert len(response_body["message"]) == 2
    # make sure only the expected comments were returned
    assert test_valid_comment in response_body["message"]
    assert test_valid_comment_2 in response_body["message"]
    assert test_invalid_comment not in response_body["message"]


def test_get_all_version_comments(comments_table, get_one_version_event, monkeypatch):
    """
    Testing reading all comments for one asset (using assetId)
    :param comments_table: mocked dynamoDB commentStorageTable
    :param get_one_version_event: Lambda event to get all comments for a specific assetId versionId pair
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    # Generate two seperate comments for testing
    test_valid_comment = TestComment().get_comment()
    test_valid_comment_2 = TestComment(
        asset_version_id_and_comment_id="test-version-id:test-comment-id-2"
    ).get_comment()
    # Generate a third comment with a different versionId that should not be returned by the function
    test_invalid_comment = TestComment(
        asset_version_id_and_comment_id="invalid-test-version-id:test-comment-id-3"
    ).get_comment()
    # Add the testing comment to the table
    comments_table.put_item(Item=test_valid_comment)
    comments_table.put_item(Item=test_valid_comment_2)
    comments_table.put_item(Item=test_invalid_comment)
    # Get the testing comments from the table
    response = comments_table.get_item(
        Key={
            "assetId": test_valid_comment["assetId"],
            "assetVersionId:commentId": test_valid_comment["assetVersionId:commentId"],
        }
    )
    # make sure the testing comments were added succesfully
    assert response["Item"] == test_valid_comment
    response = comments_table.get_item(
        Key={
            "assetId": test_valid_comment_2["assetId"],
            "assetVersionId:commentId": test_valid_comment_2["assetVersionId:commentId"],
        }
    )
    assert response["Item"] == test_valid_comment_2
    response = comments_table.get_item(
        Key={
            "assetId": test_invalid_comment["assetId"],
            "assetVersionId:commentId": test_invalid_comment["assetVersionId:commentId"],
        }
    )
    assert response["Item"] == test_invalid_comment

    response = commentService.lambda_handler(get_one_version_event, None)
    # Validate the status code of the response
    assert response["statusCode"] == 200
    response_body = json.loads(response["body"])
    print(response_body["message"][0])
    # make sure only 2 comments were returned
    assert len(response_body["message"]) == 2
    # make sure only the expected comments were returned
    assert test_valid_comment in response_body["message"]
    assert test_valid_comment_2 in response_body["message"]
    assert test_invalid_comment not in response_body["message"]


def test_get_single_comment(comments_table, get_single_event, monkeypatch):
    """
    Testing reading a single comment (using assetId and assetVersionId:commentId)
    :param comments_table: mocked dynamoDB commentStorageTable
    :param get_single_event: Lambda event to get a single comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    # Generate the testing comment
    test_comment_instance = TestComment().get_comment()
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

    response = commentService.lambda_handler(get_single_event, None)
    # Validate the status code of the response
    assert response["statusCode"] == 200
    response_body = json.loads(response["body"])
    assert response_body["message"] == test_comment_instance


def test_invalid_get(invalid_get_event, monkeypatch):
    """
    Testing the response when an invalid assetId is passed to the lambda handler
    :param invalid_get_event: Lambda event with invalid assetId
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    # Set the dynamodb resource to use the mocked table
    monkeypatch.setattr(commentService, "dynamodb", boto3.resource("dynamodb"))
    
    response = commentService.lambda_handler(invalid_get_event, None)
    print(response)
    assert response["statusCode"] == 400
