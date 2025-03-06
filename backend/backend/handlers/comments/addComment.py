#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.dynamodb import get_asset_object_from_id
from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger

claims_and_roles = {}

# Create a logger object to log the events
logger = safeLogger(service="AddComment")

dynamodb = boto3.resource("dynamodb")
s3c = boto3.client("s3")

main_rest_response = STANDARD_JSON_RESPONSE

comment_database = None

try:
    comment_database = os.environ["COMMENT_STORAGE_TABLE_NAME"]
except:
    logger.info("Failed Loading Environment Variables")
    main_rest_response["statusCode"] = 500
    main_rest_response["body"] = json.dumps({"message": "Failed Loading Environment Variables"})


def add_comment(assetId: str, assetVersionIdAndCommentId: str, userId: str, event: dict) -> dict:
    """
    Creates the JSON for a comment based on the parameters and adds the comment to the database
    :param assetId: string containing the assetId that the comment will be attached to
    :param assetVersionIdAndCommentId: string with the asset version id the comment will be attached to and the unique comment id
    :param event: Lambda event dictionary
    :returns: dictionary with status code and success info
    """
    response = {"statusCode": 200, "message": "Succeeded"}
    logger.info("Setting Table")
    logger.info(comment_database)
    table = dynamodb.Table(comment_database)
    logger.info("Setting Time Stamp")
    dtNow = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    logger.info("current time in ISO8601:" + dtNow)
    item = {
        "assetId": assetId,
        "assetVersionId:commentId": assetVersionIdAndCommentId,
        "commentBody": event["body"]["commentBody"],
        "commentOwnerID": userId,
        "commentOwnerUsername": userId,
        "dateCreated": dtNow,
    }
    try:
        table.put_item(Item=item)
    except Exception as e:
        logger.exception(e)
        response["statusCode"] = 500
        response["message"] = "Internal Server Error"
        return response

    logger.info(item)

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

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        httpMethod = event['requestContext']['http']['method']
        method_allowed_on_api = False
        userId = None

        asset_object = get_asset_object_from_id(pathParameters["assetId"])
        asset_object.update({"object__type": "asset"})

        # Add Casbin Enforcer to check if the current user has permissions to POST the Comment
        if len(claims_and_roles["tokens"]) > 0:
            userId = claims_and_roles["tokens"][0]
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(asset_object, httpMethod) and casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api:
            logger.info("Trying to add comment")
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

            if not valid:
                logger.warning(message)
                response["body"] = json.dumps({"message": message})
                response["statusCode"] = 400
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

            # call the add_comment function if everything is valid
            returned = add_comment(pathParameters["assetId"], pathParameters["assetVersionId:commentId"], userId, event)
            response["statusCode"] = returned["statusCode"]
            response["body"] = json.dumps({"message": returned["message"]})
            logger.info(response)
            return response
        else:
            response["statusCode"] = 403
            response["body"] = json.dumps({"message": "Action not allowed"})
            return response
    except Exception as e:
        logger.exception(f"caught exception")
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            response["statusCode"] = 400
            response["body"] = json.dumps(
                {"message": "comment " + str(event["body"]["assetVersionId:commentId"] + " already exists.")}
            )
        else:
            response["statusCode"] = 500
            response["body"] = json.dumps({"message": "Internal Server Error"})
            logger.exception(e)
        return response
