#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from common.dynamodb import get_asset_object_from_id
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info

claims_and_roles = {}

# Create a logger object to log the events
logger = safeLogger(service="CommentService")

dynamodb = boto3.resource("dynamodb")
dynamodb_client = boto3.client("dynamodb")
main_rest_response = STANDARD_JSON_RESPONSE
comment_database = None

try:
    comment_database = os.environ["COMMENT_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed Loading Comment Storage Environment Variables")
    main_rest_response["body"]["message"] = "Failed Loading Comment Storage Environment Variables"


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

    # Handle empty string for startingToken
    pagination_config = {
        "MaxItems": int(queryParams["maxItems"]),
        "PageSize": int(queryParams["pageSize"]),
    }
    if queryParams["startingToken"] and queryParams["startingToken"] != "":
        pagination_config["StartingToken"] = queryParams["startingToken"]

    try:
        pageIterator = paginator.paginate(
            TableName=comment_database,
            ScanFilter=filter,
            PaginationConfig=pagination_config,
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
    except Exception as e:
        logger.exception(f"Error in get_all_comments: {str(e)}")
        # Return empty result on error
        return {"Items": []}


def get_comments(assetId: str, queryParams: dict, showDeleted=False) -> dict:
    """
    Gets all of the comments associated with a specific asset (using assetId)
    :param assetId: id of the asset to get comments for
    :param queryParams: pagination information
    :param showDeleted: boolean storing if deleted comments should be returned
    :returns: dictionary with all comments for specific asset
    """
    paginator = dynamodb.meta.client.get_paginator('query')

    # Handle empty string for startingToken
    pagination_config = {
        'MaxItems': int(queryParams['maxItems']),
        'PageSize': int(queryParams['pageSize']),
    }
    if queryParams['startingToken'] and queryParams['startingToken'] != "":
        pagination_config['StartingToken'] = queryParams['startingToken']

    try:
        response = paginator.paginate(
            TableName=comment_database,
            KeyConditionExpression=Key("assetId").eq(assetId),
            ScanIndexForward=False,
            PaginationConfig=pagination_config
        ).build_full_result()

        return response["Items"]
    except Exception as e:
        logger.exception(f"Error in get_comments: {str(e)}")
        # Return empty result on error
        return []


def get_comments_version(assetId: str, assetVersionId: str, queryParams: dict, showDeleted=False) -> dict:
    """
    Gets all of the comments for a specific assetId versionId pair (all comments for a specific version of an asset)
    :param assetId: id of the asset to get comments for
    :param assetVersionId: id of the version to get comments for
    :param queryParams: pagination information
    :param showDeleted: boolean storing if deleted comments should be returned
    :returns: dictionary with all comments for a specific version of an asset
    """
    paginator = dynamodb.meta.client.get_paginator('query')

    # Handle empty string for startingToken
    pagination_config = {
        'MaxItems': int(queryParams['maxItems']),
        'PageSize': int(queryParams['pageSize']),
    }
    if queryParams['startingToken'] and queryParams['startingToken'] != "":
        pagination_config['StartingToken'] = queryParams['startingToken']

    try:
        # Queries partition key (assetId) and queries sort keys that begin_with the desired asset version
        response = paginator.paginate(
            TableName=comment_database,
            KeyConditionExpression=Key("assetId").eq(assetId) & Key("assetVersionId:commentId").begins_with(assetVersionId),
            ScanIndexForward=False,
            PaginationConfig=pagination_config
        ).build_full_result()

        return response["Items"]
    except Exception as e:
        logger.exception(f"Error in get_comments_version: {str(e)}")
        # Return empty result on error
        return []


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


def delete_comment(assetId: str, assetVersionIdAndCommentId: str, userId: str, event: dict) -> dict:
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
        comment_user_id = item["commentOwnerID"]
        if userId != comment_user_id:
            logger.warning("invalid user - ownerID mismatch with caller")
            response["statusCode"] = 403
            response["message"] = "Unauthorized - only the creator of the comment can delete it"
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
        except Exception as e:
            logger.exception(e)
            response["statusCode"] = 500
            response["message"] = "Internal Server Error"
            return response

        # Create a new comment with #deleted appended to the assetId
        try:
            table.put_item(Item=item)
        except Exception as e:
            logger.exception(e)
            response["statusCode"] = 500
            response["message"] = "Internal Server Error"
            return response

        response["statusCode"] = 200
        response["message"] = "Comment deleted"
    return response


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

    method_allowed_on_api = False

    asset_object = get_asset_object_from_id(None, pathParameters["assetId"])
    asset_object.update({"object__type": "asset"})

    # Add Casbin Enforcer to check if the current user has permissions to GET the Comment
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(asset_object, "GET"):
            method_allowed_on_api = True

    if method_allowed_on_api:
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
                            queryParameters,
                            showDeleted,
                        )
                    }
                )
                response["statusCode"] = 200
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
                response["body"] = json.dumps({"message": get_comments(pathParameters["assetId"], queryParameters, showDeleted)})
                response["statusCode"] = 200
                return response
            else:
                # if we have nothing, call get_all_comments
                logger.info("Listing All Comments")
                response["body"] = json.dumps({"message": get_all_comments(queryParameters, showDeleted)})
                response["statusCode"] = 200
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
            response["statusCode"] = 200
            return response
    else:
        response["statusCode"] = 403
        response["body"] = json.dumps({"message": "Action not allowed"})
        return response


def delete_handler(response: dict, pathParameters: dict, event: dict) -> dict:
    """
    Function to handle the delete request and route it to the right function
    :param response: dictionary holding information about the response
    :param pathParameters: dictionary holding information about the path (like versionId and/or assetId)
    :param event: Lambda event dictionary
    :returns: Http response object (statusCode, headers, body)
    """
    # Check for missing fields - TODO:  keep these synchronized
    #
    required_field_names = ['assetId', 'assetVersionId:commentId']
    missing_field_names = list(set(required_field_names).difference(pathParameters))
    if missing_field_names:
        message = 'Missing path parameter(s) (%s) in API call' % (', '.join(missing_field_names))
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response

    logger.info("Validating parameters")
    split_arr = pathParameters["assetVersionId:commentId"].split(":")

    #if split_arr length is not 2, generate a validation error
    if len(split_arr) != 2:
        message = "Invalid assetVersionId:commentId format"
        response["body"] = json.dumps({"message": message})
        response["statusCode"] = 400
        return response

    (valid, message) = validate(
        {
            "assetId": 
            {
                "value": pathParameters["assetId"], 
                "validator": "ASSET_ID"
            },
            "commentId": 
            {
                "value": split_arr[1], 
                "validator": "ID"
                },
        }
    )

    if not valid:
        logger.warning(message)
        response["body"] = json.dumps({"message": message})
        response["statusCode"] = 400
        return response

    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    method_allowed_on_api = False

    asset_object = get_asset_object_from_id(None, pathParameters["assetId"])
    asset_object.update({"object__type": "asset"})

    # Add Casbin Enforcer to check if the current user has permissions to DELETE the Comment
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(asset_object, "DELETE"):
            method_allowed_on_api = True

    if method_allowed_on_api:

        #Get user ID of person making request
        userId = claims_and_roles.get("tokens", ["system"])[0]

        logger.info(
            f"Deleting comment for assetId: {pathParameters['assetId']} and versionId:commentId: {pathParameters['assetVersionId:commentId']}",
        )

        result = delete_comment(pathParameters["assetId"], pathParameters["assetVersionId:commentId"], userId, event)
        
        response["body"] = json.dumps({"message": result["message"]})
        response["statusCode"] = result["statusCode"]
        return response
    else:
        response["statusCode"] = 403
        response["body"] = json.dumps({"message": "Action not allowed"})
        return response


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda handler for the API calls directed to commentService
    :param event: Lambda event dictionary
    :param context: lambda context dictionary
    :returns: Http response object (statusCode, headers, body)
    """
    response = STANDARD_JSON_RESPONSE
    logger.info(event)
    pathParameters = event.get("pathParameters", {})
    queryParameters = event.get("queryStringParameters", {})

    validate_pagination_info(queryParameters)

    try:
        # route the api call based on tags
        httpMethod = event["requestContext"]["http"]["method"]
        logger.info(httpMethod)

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        method_allowed_on_api = False

        # Add Casbin Enforcer to check if the current user has permissions to GET the Comment
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        

        if httpMethod == "GET" and method_allowed_on_api:
            result = get_handler(response, pathParameters, queryParameters)
            return result
        if httpMethod == "DELETE" and method_allowed_on_api:
            result = delete_handler(response, pathParameters, event)
            return result
        
        # If we get here, we didn't handle the request
        logger.error("No handler matched")
        response["statusCode"] = 405
        response["body"] = json.dumps({"message": "Method not allowed"})
        return response

    except Exception as e:
        response["statusCode"] = 500
        logger.exception(e)
        response["body"] = json.dumps({"message": "Internal Server Error"})
        return response


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)
