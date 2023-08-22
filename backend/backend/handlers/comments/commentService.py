#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import logging
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from backend.common.validators import validate
from typing import List

# Create a logger object to log the events
logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
dynamodb_client = boto3.client("dynamodb")
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
    logger.error("Failed Loading Comment Storage Environment Variables")
    response["body"]["message"] = "Failed Loading Comment Storage Environment Variables"


def get_all_comments(queryParams: dict, showDeleted=False) -> dict:
    """
    Gets all of the comments from the database
    NOTE: This is not currently used, but could be helpful to populate the comments section when the page first loads and no asset is selected
    :param queryParams: pagination information
    :param showDeleted: boolean storing if deleted comments should be returned
    :returns: all comments in the database
    """
    deserializer = TypeDeserializer()

    paginator = dynamodb_client.get_paginator("scan")
    operator = "NOT_CONTAINS"
    if showDeleted:
        operator = "CONTAINS"
    filter = {
        "assetId": {
            "AttributeValueList": [{"S": "#deleted"}],
            "ComparisonOperator": f"{operator}",
        }
    }

    pageIterator = paginator.paginate(
        TableName=comment_database,
        ScanFilter=filter,
        PaginationConfig={
            "MaxItems": int(queryParams["maxItems"]),
            "PageSize": int(queryParams["pageSize"]),
            "StartingToken": queryParams["startingToken"],
        },
    ).build_full_result()

    logger.info("Fetching results")
    result = {}
    items = []
    for item in pageIterator["Items"]:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
        items.append(deserialized_document)
    result["Items"] = items
    if "NextToken" in pageIterator:
        result["NextToken"] = pageIterator["NextToken"]
    return result


def get_comments(assetId: str, showDeleted=False) -> dict:
    """
    Gets all of the comments associated with a specific asset (using assetId)
    :param assetId: id of the asset to get comments for
    :param showDeleted: boolean storing if deleted comments should be returned
    :returns: dictionary with all comments for specific asset
    """
    table = dynamodb.Table(comment_database)

    response = table.query(
        KeyConditionExpression=Key("assetId").eq(assetId),
        ScanIndexForward=False,
        Limit=1000,
    )
    return response["Items"]


def get_comments_version(assetId: str, assetVersionId: str, showDeleted=False) -> dict:
    """
    Gets all of the comments for a specific assetId versionId pair (all comments for a specific version of an asset)
    :param assetId: id of the asset to get comments for
    :param assetVersionId: id of the version to get comments for
    :param showDeleted: boolean storing if deleted comments should be returned
    :returns: dictionary with all comments for a specific version of an asset
    """
    table = dynamodb.Table(comment_database)

    # Queries partition key (assetId) and queries sort keys that begin_with the desired asset version
    response = table.query(
        KeyConditionExpression=Key("assetId").eq(assetId) & Key("assetVersionId:commentId").begins_with(assetVersionId),
        ScanIndexForward=False,
        Limit=1000,
    )
    return response["Items"]


def get_single_comment(assetId: str, assetVersionIdAndCommentId: str, showDeleted=False) -> dict:
    """
    Gets a specific comment from the assetId and the assetVersionId:commentId
    :param assetId: id of the asset to get comments for
    :param assetVersionIdAndCommentId: id of the version to get comments for and the unique comment Id
    :param showDeleted: boolean storing if deleted comments should be returned
    :returns: dictionary with the specific comment
    """
    logger.info("Getting single comment")
    table = dynamodb.Table(comment_database)

    response = table.get_item(Key={"assetId": assetId, "assetVersionId:commentId": assetVersionIdAndCommentId})
    return response.get("Item", {})


def delete_comment(assetId: str, assetVersionIdAndCommentId: str, event: dict) -> dict:
    """
    Deletes a specific comment from the database (actually just adds #deleted tag)
    :param assetId: id of the asset the comment is attached to
    :param assetVersionIdAndCommmentId: id of the version the comment is attached to and unique identifier for the comment
    :returns: Http response object (statusCode, headers, body)
    """
    response = {"statusCode": 404, "message": "Record not found"}
    table = dynamodb.Table(comment_database)
    if "#deleted" in assetId:
        return response
    item = get_single_comment(assetId, assetVersionIdAndCommentId)
    if item:
        logger.info(f"Got comment:")
        logger.info(item)

        logger.info("Verifying user")
        api_call_user_id = event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
        comment_user_id = item["commentOwnerID"]
        if api_call_user_id != comment_user_id:
            logger.warning("invalid user")
            response["statusCode"] = 401
            response["message"] = "Unauthorized"
            return response

        logger.info("Deleting comment")
        item["assetId"] = assetId + "#deleted"

        # Delete the old comment from the table
        try:
            table.delete_item(
                Key={
                    "assetId": assetId,
                    "assetVersionId:commentId": assetVersionIdAndCommentId,
                }
            )
        except:
            logger.error(e)
            response["statusCode"] = 400
            response["message"] = e
            return response

        # Create a new comment with #deleted appended to the assetId
        try:
            table.put_item(Item=item)
        except Exception as e:
            logger.error(e)
            response["statusCode"] = 400
            response["message"] = e
            return response

        response["statusCode"] = 200
        response["message"] = "Comment deleted"
    return response


def set_pagination_info(queryParameters: dict):
    """
    Sets the pagination infor from the query parameters
    :param queryParameters: dictionary containing pagination info
    """
    if "maxItems" not in queryParameters:
        queryParameters["maxItems"] = 100
        queryParameters["pageSize"] = 100
    else:
        queryParameters["pageSize"] = queryParameters["maxItems"]
    if "startingToken" not in queryParameters:
        queryParameters["startingToken"] = None


def get_handler(response: dict, pathParameters: dict, queryParameters: dict) -> dict:
    """
    Function to handle the get request and route it to the right function
    :param response: dictionary holding information about the response
    :param pathParameters: dictionary holding information about the path (like versionId and/or assetId)
    :param queryParameters: dictionary holding pagination information
    :returns: Http response object (statusCode, headers, body) with comments stored in the body (if successful)
    """
    showDeleted = False

    if "showDeleted" in queryParameters:
        showDeleted = queryParameters["showDeleted"]

    if "assetVersionId:commentId" not in pathParameters:
        # if we have an assetVersionId and assetId, call get_comments_version
        if "assetVersionId" in pathParameters and "assetId" in pathParameters:
            logger.info("Validating parameters")
            (valid, message) = validate({"assetId": {"value": pathParameters["assetId"], "validator": "ID"}})
            if not valid:
                logger.warning(message)
                response["body"] = json.dumps({"message": message})
                response["statusCode"] = 400
                return response

            logger.info(
                f"Listing comments for asset: {pathParameters['assetId']} and version {pathParameters['assetVersionId']}",
            )
            response["body"] = json.dumps(
                {
                    "message": get_comments_version(
                        pathParameters["assetId"],
                        pathParameters["assetVersionId"],
                        showDeleted,
                    )
                }
            )
            return response

        # if we just have assetId, call get_comments
        if "assetId" in pathParameters:
            logger.info("Validating parameters")
            (valid, message) = validate({"assetId": {"value": pathParameters["assetId"], "validator": "ID"}})
            if not valid:
                logger.warning(message)
                response["body"] = json.dumps({"message": message})
                response["statusCode"] = 400
                return response

            logger.info(f"Listing comments for asset: {pathParameters['assetId']}")
            response["body"] = json.dumps({"message": get_comments(pathParameters["assetId"], showDeleted)})
            return response
        else:
            # if we have nothing, call get_all_comments
            logger.info("Listing All Comments")
            response["body"] = json.dumps({"message": get_all_comments(queryParameters, showDeleted)})
            return response
    else:
        # error, no assetId in call
        if "assetId" not in pathParameters:
            message = "No asset ID in API Call"
            response["body"] = json.dumps({"message": message})
            response["statusCode"] = 400
            return response

        logger.info("Validating parameters")

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

        logger.info(
            f"Getting comment with assetId {pathParameters['assetId']} and assetVersionId:commentId {pathParameters['assetVersionId:commentId']}"
        )
        response["body"] = json.dumps(
            {
                "message": get_single_comment(
                    pathParameters["assetId"],
                    pathParameters["assetVersionId:commentId"],
                    showDeleted,
                )
            }
        )
        return response


def delete_handler(response: dict, pathParameters: dict, event: dict) -> dict:
    """
    Function to handle the delete request and route it to the right function
    :param response: dictionary holding information about the response
    :param pathParameters: dictionary holding information about the path (like versionId and/or assetId)
    :param event: Lambda event dictionary
    :returns: Http response object (statusCode, headers, body)
    """
    if "assetId" not in pathParameters:
        message = "No asset ID in API Call"
        response["body"] = json.dumps({"message": message})
        response["statusCode"] = 400
        return response
    if "assetVersionId:commentId" not in pathParameters:
        message = "No assetVersionId:commentId in API Call"
        response["body"] = json.dumps({"message": message})
        response["statusCode"] = 400
        return response

    logger.info("Validating parameters")
    split_arr = pathParameters["assetVersionId:commentId"].split(":")
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

    logger.info(
        f"Deleting comment for assetId: {pathParameters['assetId']} and versionId:commentId: {pathParameters['assetVersionId:commentId']}",
    )
    result = delete_comment(pathParameters["assetId"], pathParameters["assetVersionId:commentId"], event)
    response["body"] = json.dumps({"message": result["message"]})
    response["statusCode"] = result["statusCode"]
    return response


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda handler for the API calls directed to commentService
    :param event: Lambda event dictionary
    :param context: lambda context dictionary
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
    pathParameters = event.get("pathParameters", {})
    queryParameters = event.get("queryStringParameters", {})

    set_pagination_info(queryParameters)

    try:
        # route the api call based on tags
        httpMethod = event["requestContext"]["http"]["method"]
        logger.info(httpMethod)

        if httpMethod == "GET":
            return get_handler(response, pathParameters, queryParameters)
        if httpMethod == "DELETE":
            return delete_handler(response, pathParameters, event)

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


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)
