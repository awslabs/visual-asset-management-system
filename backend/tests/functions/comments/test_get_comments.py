import json
import backend.handlers.comments.commentService as commentService
from tests.functions.comments.conftest import generate_comment


def generate_get_single_event():
    """
    generates an event mocking what the API sends when it attempts to get a single comment
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
            "assetId": "x5f695ed7-1076-4f29-89e6-bffc7c6f3d7d",
            "assetVersionId:commentId": "zRwqtjAfexK6GZ.7_1vnEzsxUHcJ49T8:xebcaa59a-b53a-4998-a66e-b2bab933085f",
        },
    }


def generate_get_one_asset_event():
    """
    generates an event mocking what the API sends when it attempts to get all comments for a single asset
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
            "assetId": "x5f695ed7-1076-4f29-89e6-bffc7c6f3d7d",
        },
    }


def generate_get_one_version_event():
    """
    generates an event mocking what the API sends when it attempts to get all comments for a single asset
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
            "assetId": "x5f695ed7-1076-4f29-89e6-bffc7c6f3d7d",
            "assetVersionId": "zRwqtjAfexK6GZ.7_1vnEzsxUHcJ49T8",
        },
    }


def generate_get_all_event():
    """
    generates an event mocking what the API sends when it attempts to get all comments for a single asset
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


def test_get_single_comment(comments_table):
    """
    Testing reading a single comment (using assetId and assetVersionId:commentId)
    """
    event = generate_get_single_event()
    # Generate the testing comment
    test_comment = generate_comment()
    # Add the testing comment to the table
    comments_table.put_item(Item=test_comment)
    # Get the testing comment from the table
    response = comments_table.get_item(
        Key={
            "assetId": test_comment["assetId"],
            "assetVersionId:commentId": test_comment["assetVersionId:commentId"],
        }
    )
    # make sure the testing comment was added succesfully
    assert response["Item"] == test_comment

    response = commentService.lambda_handler(event, None)
    response_body = json.loads(response["body"])
    assert response_body["message"] == test_comment


def test_get_all_asset_comments(comments_table):
    """
    Testing reading all comments for one asset (using assetId)
    """
    event = generate_get_one_asset_event()
    # Generate two seperate comments for testing
    test_valid_comment = generate_comment()
    test_valid_comment_2 = generate_comment()
    test_valid_comment_2[
        "assetVersionId:commentId"
    ] = "zKwqtjAfexK6GZ.7_1vnEzsxUHcJ49T8:xfdabb4f3-353a-4b98-a66e-b2bab933085f"
    # Generate a third comment with a different assetId that should not be returned by the function
    test_invalid_comment = generate_comment()
    test_invalid_comment["assetId"] = "x7f695ed7-1076-4f29-89e6-bffc7c6f3d7d"
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
            "assetVersionId:commentId": test_valid_comment_2[
                "assetVersionId:commentId"
            ],
        }
    )
    assert response["Item"] == test_valid_comment_2
    response = comments_table.get_item(
        Key={
            "assetId": test_invalid_comment["assetId"],
            "assetVersionId:commentId": test_invalid_comment[
                "assetVersionId:commentId"
            ],
        }
    )
    assert response["Item"] == test_invalid_comment

    response = commentService.lambda_handler(event, None)
    response_body = json.loads(response["body"])
    print(response_body["message"][0])
    # make sure only two comments were returned
    assert len(response_body["message"]) == 2
    # make sure only the expected comments were returned
    assert test_valid_comment in response_body["message"]
    assert test_valid_comment_2 in response_body["message"]
    assert test_invalid_comment not in response_body["message"]


def test_get_all_version_comments(comments_table):
    """
    Testing reading all comments for one asset (using assetId)
    """
    event = generate_get_one_version_event()
    # Generate two seperate comments for testing
    test_valid_comment = generate_comment()
    test_valid_comment_2 = generate_comment()
    test_valid_comment_2[
        "assetVersionId:commentId"
    ] = "zRwqtjAfexK6GZ.7_1vnEzsxUHcJ49T8:x44caa59a-b53a-4998-a66e-b2bab933085f"
    # Generate a third comment with a different assetId that should not be returned by the function
    test_invalid_comment = generate_comment()
    test_invalid_comment[
        "assetVersionId:commentId"
    ] = "zKwqtjAfexK6GZ.7_1vnEzsxUHcJ49T8:xfdabb4f3-353a-4b98-a66e-b2bab933085f"
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
            "assetVersionId:commentId": test_valid_comment_2[
                "assetVersionId:commentId"
            ],
        }
    )
    assert response["Item"] == test_valid_comment_2
    response = comments_table.get_item(
        Key={
            "assetId": test_invalid_comment["assetId"],
            "assetVersionId:commentId": test_invalid_comment[
                "assetVersionId:commentId"
            ],
        }
    )
    assert response["Item"] == test_invalid_comment

    response = commentService.lambda_handler(event, None)
    response_body = json.loads(response["body"])
    print(response_body["message"][0])
    # make sure only 2 comments were returned
    assert len(response_body["message"]) == 2
    # make sure only the expected comments were returned
    assert test_valid_comment in response_body["message"]
    assert test_valid_comment_2 in response_body["message"]
    assert test_invalid_comment not in response_body["message"]


def test_get_all_comments(comments_table):
    """
    Testing reading all comments for one asset (using assetId)
    """
    event = generate_get_all_event()
    # Generate two seperate comments for testing
    test_valid_comment = generate_comment()
    test_valid_comment_2 = generate_comment()
    test_valid_comment_2[
        "assetVersionId:commentId"
    ] = "zRwqtjAfexK6GZ.7_1vnEzsxUHcJ49T8:x44caa59a-b53a-4998-a66e-b2bab933085f"
    # Generate a third comment with a different assetId that should not be returned by the function
    test_valid_comment_3 = generate_comment()
    test_valid_comment_3[
        "assetVersionId:commentId"
    ] = "zKwqtjAfexK6GZ.7_1vnEzsxUHcJ49T8:xfdabb4f3-353a-4b98-a66e-b2bab933085f"
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
            "assetVersionId:commentId": test_valid_comment_2[
                "assetVersionId:commentId"
            ],
        }
    )
    assert response["Item"] == test_valid_comment_2
    response = comments_table.get_item(
        Key={
            "assetId": test_valid_comment_3["assetId"],
            "assetVersionId:commentId": test_valid_comment_3[
                "assetVersionId:commentId"
            ],
        }
    )
    assert response["Item"] == test_valid_comment_3

    response = commentService.lambda_handler(event, None)
    response_body = json.loads(response["body"])
    print(response_body["message"]["Items"])
    # make sure all 3 comments were returned
    assert len(response_body["message"]["Items"]) == 3
    # make sure all expected comments were returned
    assert test_valid_comment in response_body["message"]["Items"]
    assert test_valid_comment_2 in response_body["message"]["Items"]
    assert test_valid_comment_3 in response_body["message"]["Items"]
