import json
import pytest
from tests.conftest import TestComment


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
    }


def test_get_all_comments(comments_table, get_all_event, monkeypatch):
    """
    Testing reading all comments for one asset (using assetId)
    :param comments_table: mocked dynamoDB commentStorageTable
    :param get_all_event: Lambda event to get all comments
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

    response = commentService.lambda_handler(invalid_get_event, None)
    print(response)
    assert response["statusCode"] == 400
