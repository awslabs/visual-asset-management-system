#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
import logging
from backend.common.validators import validate
from backend.handlers.comments.commentService import get_single_comment

# Create a logger object to log the events
logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
s3c = boto3.client("s3")

response = {
    "statusCode": 200,
    "body": "",
    "headers": {
        "Content-Type": "application/json",
        "Access-Control-Allow-Credentials": True,
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
    },
}

comment_database = None

try:
    comment_database = os.environ["COMMENT_STORAGE_TABLE_NAME"]
except:
    logger.info("Failed Loading Environment Variables")
    response["statusCode"] = 500
    response["body"] = json.dumps({"message": "Failed Loading Environment Variables"})


def edit_comment(assetId: str, assetVersionIdAndCommentId: str, event: dict) -> dict:
    """
    Checks comment ownership then edits the comment to reflect the changes
    :param assetId: string containing the assetId of the comment
    :param assetVersionIdAndCommentId: string with the asset version id and the unique comment id of the comment
    :param event: Lambda event dictionary
    :returns: dictionary with status code and success info
    """
    response = {"statusCode": 404, "message": "Record not found"}
    logger.info("Setting Table")
    logger.info(comment_database)
    table = dynamodb.Table(comment_database)
    logger.info("Setting Time Stamp")
    dtNow = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    logger.info("current time in ISO8601:" + dtNow)

    item = get_single_comment(assetId, assetVersionIdAndCommentId)
    if item:
        logger.info(item)
        logger.info("Validating owner")

        if item["commentOwnerID"] != event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]:
            response["statusCode"] = 401
            response["message"] = "Unauthorized"
            return response

        try:
            table.update_item(
                Key={
                    "assetId": assetId,
                    "assetVersionId:commentId": assetVersionIdAndCommentId,
                },
                UpdateExpression="set commentBody=:b, dateEdited=:d",
                ExpressionAttributeValues={
                    ":b": event["body"]["commentBody"],
                    ":d": dtNow,
                },
            )
        except Exception as e:
            logger.error(e)
            response["statusCode"] = 400
            response["message"] = e
            return response

        response["statusCode"] = 200
        response["message"] = "Succeeded"
    return response


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda handler for API calls that try to add a comment
    :param event: Lamdba event dictionary
    :param context: Lambda context disctionary
    :returns: Http response object (statusCode, headers, body)
    """
    logger.info(event)
    response = {
        "statusCode": 200,
        "body": "",
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Credentials": True,
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "OPTIONS,POST,GET",
        },
    }

    try:
        if isinstance(event["body"], str):
            event["body"] = json.loads(event["body"])
    except Exception as e:
        response["statusCode"] = 400
        response["body"] = {"message": e}
        return response

    pathParameters = event.get("pathParameters", {})
    logger.info(pathParameters)

    try:
        # error if no assetId in api call
        if "assetId" not in pathParameters:
            message = "No assetId in API Call"
            response["statusCode"] = 400
            response["body"] = json.dumps({"message": message})
            return response

        split_arr = pathParameters["assetVersionId:commentId"].split(":")
        logger.info("Validating parameters")
        (valid, message) = validate(
            {
                "assetId": {"value": pathParameters["assetId"], "validator": "ID"},
                "commentId": {"value": split_arr[1], "validator": "ID"},
            }
        )

        if not valid:
            logger.warning(message)
            response["body"] = json.dumps({"message": message})
            response["statusCode"] = 400
            return response

        logger.info("Trying to get edit comment")
        # call the edit_comment function if everything is valid
        returned = edit_comment(pathParameters["assetId"], pathParameters["assetVersionId:commentId"], event)
        response["statusCode"] = returned["statusCode"]
        response["body"] = json.dumps({"message": returned["message"]})
        logger.info(response)
        return response
    except Exception as e:
        response["statusCode"] = 500
        logger.error("Error!", e.__class__, "occurred.")
        try:
            logger.info(e)
            response["body"] = json.dumps({"message": str(e)})
        except:
            logger.info("Can't Read Error")
            response["body"] = json.dumps({"message": "An unexpected error occurred while executing the request"})
        return response
