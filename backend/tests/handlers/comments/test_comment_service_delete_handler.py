import json
import pytest
from tests.conftest import TestComment


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


def test_delete_comment(comments_table, delete_event, monkeypatch):
    """
    Testing the delete comment Lambda handler with  delete a comment from the database
    :param comments_table: mocked dynamoDB commentStorageTable
    :param delete_event: Lambda event to get delete a comment
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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

    response = commentService.lambda_handler(invalid_delete_event, None)
    print(response)
    assert response["statusCode"] == 400
