import pytest
from tests.conftest import TestComment


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
            }
        },
        "pathParameters": {},
    }


def test_add_comment(comments_table, add_valid_event, monkeypatch):
    """
    Testing the add comment lambda handler with valid data
    :param comments_table: mocked dynamoDB commentStorageTable
    :param add_event: Lambda event dictionary for adding a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.addComment as addComment

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
    :param add_event_invalid: Lambda event dictionary for adding an invalid comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.addComment as addComment

    # Calling the lambda handler with the event
    response = addComment.lambda_handler(add_invalid_event, None)
    print(response)
    assert response["statusCode"] == 400
