import pytest
from tests.conftest import TestComment


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
            }
        },
    }


def test_delete_comment(comments_table, delete_event, monkeypatch):
    """
    Testing the delete comment function with a valid comment to delete
    :param comments_table: mocked dynamoDB commentStorageTable
    :param delete_event: Lambda event dictionary for deleting a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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

    deleted_comment = TestComment(asset_id=asset_id + "#deleted").get_comment()

    assert response["Item"] == deleted_comment


def test_delete_comment_not_exist(comments_table, delete_event, monkeypatch):
    """
    Testing the delete function with a comment that does not exist
    :param comments_table: mocked dynamoDB commenStorageTable
    :param delete_event: Lambda event dictionary for deleting a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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
