import backend.handlers.comments.addComment as addComment
from tests.functions.comments.conftest import generate_comment


def generate_add_event():
    """
    generates an event mocking what the API sends when it attempts to add a comment
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
            "assetId": "x5f695ed7-1076-4f29-89e6-bffc7c6f3d7d",
            "assetVersionId:commentId": "zRwqtjAfexK6GZ.7_1vnEzsxUHcJ49T8:xebcaa59a-b53a-4998-a66e-b2bab933085f",
        },
    }


def test_add_comment(comments_table):
    """
    Testing the adding comment function and lambda handler with valid data
    """
    event = generate_add_event()
    # Calling the lambda handler with the event
    addComment.lambda_handler(event, None)
    assetId = event["pathParameters"]["assetId"]
    assetVersionIdAndCommentId = event["pathParameters"]["assetVersionId:commentId"]
    # Getting the comment that should have been added by the lambda handler
    response = comments_table.get_item(
        Key={"assetId": assetId, "assetVersionId:commentId": assetVersionIdAndCommentId}
    )
    actual_output = response["Item"]
    expected_output = generate_comment()
    # Removing the dateCreated from the comments bc they will differ (since they are auto generated)
    del actual_output["dateCreated"]
    del expected_output["dateCreated"]
    # Expected comment and actual comment should be the same
    assert actual_output == expected_output
