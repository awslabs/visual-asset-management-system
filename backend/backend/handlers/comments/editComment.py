#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from common.validators import validate
from handlers.comments.commentService import get_single_comment
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from common.dynamodb import get_asset_object_from_id
from customLogging.logger import safeLogger

claims_and_roles = {}

# Create a logger object to log the events
logger = safeLogger(service="EditComment")

dynamodb = boto3.resource("dynamodb")
s3c = boto3.client("s3")

main_rest_response = STANDARD_JSON_RESPONSE

comment_database = None

try:
    comment_database = os.environ["COMMENT_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed Loading Environment Variables")
    main_rest_response["statusCode"] = 500
    main_rest_response["body"] = json.dumps({"message": "Failed Loading Environment Variables"})


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
            response["statusCode"] = 403
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
            logger.exception(e)
            response["statusCode"] = 500
            response["body"] = {"message": "Internal Server Error"}
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
    response = STANDARD_JSON_RESPONSE

    logger.info(event)

    try:
        if isinstance(event["body"], str):
            event["body"] = json.loads(event["body"])
    except Exception as e:
        response["statusCode"] = 500
        response["body"] = {"message": "Internal Server Error"}
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

        global claims_and_roles
        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False
        asset_object = get_asset_object_from_id(pathParameters["assetId"])
        asset_object.update({"object__type": "asset"})

        # Add Casbin Enforcer to check if the current user has permissions to POST the Comment
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(asset_object, "POST") and casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api:
            logger.info("Trying to get edit comment")
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

            # Check for missing fields - TODO: need to keep these synchronized
            #
            required_field_names = ['commentBody']
            missing_field_names = list(set(required_field_names).difference(event['body']))
            if missing_field_names:
                message = 'Missing body parameter(s) (%s) in API call' % (', '.join(missing_field_names))
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                logger.error(response)
                return response

            logger.info("Validating body")
            (valid, message) = validate(
                {
                    "commentBody": {"value": event["body"]["commentBody"], "validator": "STRING"},
                }
            )
            if not valid:
                logger.error(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

            # call the edit_comment function if everything is valid
            returned = edit_comment(pathParameters["assetId"], pathParameters["assetVersionId:commentId"], event)
            response["statusCode"] = returned["statusCode"]
            response["body"] = json.dumps({"message": returned["message"]})
            logger.info(response)
            return response
        else:
            response["statusCode"] = 403
            response["body"] = json.dumps({"message": "Action not allowed"})
            return response
    except Exception as e:
        response["statusCode"] = 500
        logger.exception(e)
        response["body"] = json.dumps({"message": "Internal Server Error"})

        return response
