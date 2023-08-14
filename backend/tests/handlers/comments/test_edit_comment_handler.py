import pytest
import datetime
from tests.conftest import TestComment


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
            }
        },
        "pathParameters": {
            "assetId": "test-id",
            "assetVersionId:commentId": "test-version-id:test-comment-id",
        },
    }


def test_edit_comment(comments_table, edit_event, monkeypatch):
    """
    Testing the edit comment function with a valid event
    :param comments_table: mocked dynamoDB commentStorageTable
    :param edit_event: Lamdba event dictionary for editing a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.editComment as editComment

    asset_id = "test-id"
    asset_version_id_and_comment_id = "test-version-id:test-comment-id"
    test_valid_comment = TestComment(
        asset_id=asset_id,
        asset_version_id_and_comment_id=asset_version_id_and_comment_id,
        comment_body="test comment body",
    ).get_comment()

    comments_table.put_item(Item=test_valid_comment)
    editComment.lambda_handler(edit_event, None)

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.editComment as editComment

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.editComment as editComment

    asset_id = "test-id"
    asset_version_id_and_comment_id = "test-version-id:test-comment-id"

    test_unowned_comment = TestComment(
        asset_id="test-id",
        asset_version_id_and_comment_id=asset_version_id_and_comment_id,
        comment_owner_id="test_sub_2",
    ).get_comment()

    comments_table.put_item(Item=test_unowned_comment)

    response = editComment.edit_comment(asset_id, asset_version_id_and_comment_id, edit_event)
    assert response["statusCode"] == 401
    assert response["message"] == "Unauthorized"
