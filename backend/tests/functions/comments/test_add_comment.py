import pytest


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
            }
        },
    }


def test_add_comment(comments_table, add_event, monkeypatch):
    """
    Testing the add comment function
    :param comments_table: mocked dynamoDB commentStorageTable
    :param add_event: Lamdba event dictionary for adding a comment
    :param monkeypatch: monkeypatch allows for setting environment variables before importing function
                        so we don't get an error
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.addComment as addComment

    asset_id = "test-id"
    version_id_and_comment_id = "test-version-id:test-comment-id"
    response = addComment.add_comment(asset_id, version_id_and_comment_id, add_event)
    assert response["statusCode"] == 200
    response = comments_table.get_item(Key={"assetId": asset_id, "assetVersionId:commentId": version_id_and_comment_id})
    assert response["Item"]["commentBody"] == "test comment body"
