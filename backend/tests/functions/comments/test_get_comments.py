from tests.conftest import TestComment


def test_get_all_comments(comments_table, monkeypatch):
    """
    Testing the get_all_comments function from commentService that should return all comments in the db
    :param comments_table: mocked dynamoDB commentStorageTable
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

    # call get all comments function
    query_params = {}
    commentService.set_pagination_info(query_params)
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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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

    response = commentService.get_comments("test-id")
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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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

    response = commentService.get_comments_version("test-id", "test-version-id")
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
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    import backend.handlers.comments.commentService as commentService

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
