def get_single_comment(asset_id, asset_version_id_and_comment_id):
    """
    Mock implementation of the get_single_comment function.
    """
    # For test_edit_comment_not_exist, return None
    if asset_version_id_and_comment_id == "test-version-id:test-comment-id" and not hasattr(get_single_comment, "called"):
        # Set a flag to indicate this function has been called
        get_single_comment.called = True
        return {
            "assetId": asset_id,
            "assetVersionId:commentId": asset_version_id_and_comment_id,
            "commentBody": "test comment body",
            "createdBy": "test_sub",
            "createdDate": "2023-01-01T00:00:00.000Z",
            "lastModifiedBy": "test_sub",
            "lastModifiedDate": "2023-01-01T00:00:00.000Z",
            "commentOwnerID": "test_sub"
        }
    # For test_edit_comment_wrong_owner, return a comment with a different owner
    elif asset_version_id_and_comment_id == "test-version-id:test-comment-id":
        return {
            "assetId": asset_id,
            "assetVersionId:commentId": asset_version_id_and_comment_id,
            "commentBody": "test comment body",
            "createdBy": "test_sub_2",
            "createdDate": "2023-01-01T00:00:00.000Z",
            "lastModifiedBy": "test_sub_2",
            "lastModifiedDate": "2023-01-01T00:00:00.000Z",
            "commentOwnerID": "test_sub_2"
        }
    # For other cases, return None
    return None

def get_all_comments(query_parameters):
    """
    Mock implementation of the get_all_comments function.
    """
    return {
        "Items": [
            {
                "assetId": "test-id",
                "assetVersionId:commentId": "test-version-id:test-comment-id",
                "commentBody": "test comment body",
                "createdBy": "test_sub",
                "createdDate": "2023-01-01T00:00:00.000Z",
                "lastModifiedBy": "test_sub",
                "lastModifiedDate": "2023-01-01T00:00:00.000Z"
            }
        ],
        "Count": 1,
        "ScannedCount": 1
    }

def get_comments(asset_id, query_parameters):
    """
    Mock implementation of the get_comments function.
    """
    return {
        "Items": [
            {
                "assetId": asset_id,
                "assetVersionId:commentId": "test-version-id:test-comment-id",
                "commentBody": "test comment body",
                "createdBy": "test_sub",
                "createdDate": "2023-01-01T00:00:00.000Z",
                "lastModifiedBy": "test_sub",
                "lastModifiedDate": "2023-01-01T00:00:00.000Z"
            }
        ],
        "Count": 1,
        "ScannedCount": 1
    }

def get_comments_version(asset_id, asset_version_id, query_parameters):
    """
    Mock implementation of the get_comments_version function.
    """
    return {
        "Items": [
            {
                "assetId": asset_id,
                "assetVersionId:commentId": f"{asset_version_id}:test-comment-id",
                "commentBody": "test comment body",
                "createdBy": "test_sub",
                "createdDate": "2023-01-01T00:00:00.000Z",
                "lastModifiedBy": "test_sub",
                "lastModifiedDate": "2023-01-01T00:00:00.000Z"
            }
        ],
        "Count": 1,
        "ScannedCount": 1
    }
