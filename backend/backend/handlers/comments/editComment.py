#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import copy
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import json
from common.timestamps import utc_now
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate
from handlers.comments.commentService import get_single_comment
from common.auth.apiEvent import normalize_event
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from common.dynamodb import get_asset_object_from_id, asset_authorization_object
from common.commentSubscriptionKeys import COMMENT_PARTITION_KEY, COMMENT_SORT_KEY, comment_partition
from models.common import VAMSGeneralErrorResponse
from customLogging.logger import safeLogger
from common.eventLogSummary import event_log_summary
from customLogging.auditLogging import audited_handler, log_action

claims_and_roles = {}

logger = safeLogger(service="EditComment")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource("dynamodb", config=retry_config)

main_rest_response = copy.deepcopy(STANDARD_JSON_RESPONSE)

try:
    comment_database = get_table_name(ResourceKeys.COMMENT_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving comment table name")
    comment_database = None
    main_rest_response["statusCode"] = 500
    main_rest_response["body"] = json.dumps({"message": "Failed resolving comment table name"})


def edit_comment(databaseId: str, assetId: str, commentId: str, userId: str, commentBody: str) -> dict:
    """
    Checks comment ownership then edits the comment to reflect the changes
    :param databaseId: database of the asset the comment is attached to
    :param assetId: id of the asset the comment is attached to
    :param commentId: the comment's id
    :param userId: the caller, who must be the comment's creator
    :param commentBody: the new comment text
    :returns: dictionary with status code, message, and the previous text and version of the comment
    """
    response = {"statusCode": 404, "message": "Record not found"}
    table = dynamodb.Table(comment_database)
    dtNow = utc_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    item = get_single_comment(databaseId, assetId, commentId)
    if item:
        logger.info(
            f"Loaded comment: assetId={assetId}, commentId={commentId}, "
            f"commentBody={len(item.get('commentBody') or '')} char(s)"
        )

        if item["commentOwnerID"] != userId:
            response["statusCode"] = 403
            response["message"] = "Unauthorized - only the creator can edit the comment"
            return response

        try:
            # Conditional on the row still existing: a comment deleted since the read above is not
            # re-created as a row holding only the key and the new text
            table.update_item(
                Key={
                    COMMENT_PARTITION_KEY: comment_partition(databaseId, assetId),
                    COMMENT_SORT_KEY: commentId,
                },
                UpdateExpression="set commentBody=:b, dateEdited=:d",
                ConditionExpression="attribute_exists(commentId)",
                ExpressionAttributeValues={
                    ":b": commentBody,
                    ":d": dtNow,
                },
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return response
            logger.exception(e)
            response["statusCode"] = 500
            response["message"] = "Internal Server Error"
            return response
        except Exception as e:
            logger.exception(e)
            response["statusCode"] = 500
            response["message"] = "Internal Server Error"
            return response

        response["statusCode"] = 200
        response["message"] = "Succeeded"
        response["previousCommentBody"] = item.get("commentBody")
        response["assetVersionId"] = item.get("assetVersionId")
    return response


@audited_handler
def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda handler for API calls that edit a comment
    :param event: Lamdba event dictionary
    :param context: Lambda context disctionary
    :returns: Http response object (statusCode, headers, body)
    """
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)

    # This handler reads pathParameters before request_to_claims, so normalize the REST
    # event first (coerces null pathParameters to {} for the reads below).
    normalize_event(event)

    logger.info(f"Event: {event_log_summary(event)}")

    # Parse request body
    if not event.get('body'):
        message = 'Request body is required'
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        logger.error(response)
        return response

    try:
        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])
    except json.JSONDecodeError as e:
        logger.exception(f"Invalid JSON in request body: {e}")
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Invalid JSON in request body"})
        return response

    if not isinstance(event['body'], dict):
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Request body must be a JSON object"})
        return response

    pathParameters = event.get("pathParameters", {})

    try:
        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        # Tier 1: may the caller call this route at all
        if len(claims_and_roles["tokens"]) == 0 or not CasbinEnforcer(claims_and_roles).enforceAPI(event):
            response["statusCode"] = 403
            response["body"] = json.dumps({"message": "Action not allowed"})
            return response

        required_field_names = ['databaseId', 'assetId', 'commentId']
        missing_field_names = [name for name in required_field_names if not pathParameters.get(name)]
        if missing_field_names:
            message = 'Missing path parameter(s) (%s) in API call' % (', '.join(missing_field_names))
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        (valid, message) = validate(
            {
                "databaseId": {"value": pathParameters["databaseId"], "validator": "ID"},
                "assetId": {"value": pathParameters["assetId"], "validator": "ASSET_ID"},
                "commentId": {"value": pathParameters["commentId"], "validator": "ID"},
            }
        )
        if not valid:
            logger.warning(message)
            response["body"] = json.dumps({"message": message})
            response["statusCode"] = 400
            return response

        databaseId = pathParameters["databaseId"]
        assetId = pathParameters["assetId"]
        commentId = pathParameters["commentId"]

        # Tier 2 on the asset of the named database, decided before its existence is revealed. An edit
        # is a POST on the asset, as it was before comments were keyed by database.
        asset_object = get_asset_object_from_id(databaseId, assetId)
        authorization_object = asset_authorization_object(asset_object, databaseId, assetId)
        if not CasbinEnforcer(claims_and_roles).enforce(authorization_object, "POST"):
            response["statusCode"] = 403
            response["body"] = json.dumps({"message": "Action not allowed"})
            return response
        if asset_object is None:
            response["statusCode"] = 404
            response["body"] = json.dumps({"message": "Asset not found"})
            return response

        if 'commentBody' not in event['body']:
            message = 'Missing body parameter(s) (commentBody) in API call'
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        (valid, message) = validate(
            {
                "commentBody": {"value": event["body"]["commentBody"], "validator": "STRING_16384"},
            }
        )
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        # Get user ID of person making request
        userId = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]

        returned = edit_comment(databaseId, assetId, commentId, userId, event["body"]["commentBody"])
        if returned["statusCode"] == 200:
            log_action(event, "commentUpdate", database_id=databaseId, asset_id=assetId,
                       assetVersionId=returned.get("assetVersionId"), commentId=commentId,
                       changes={"commentBody": event["body"]["commentBody"]},
                       previous={"commentBody": returned.get("previousCommentBody")})
        response["statusCode"] = returned["statusCode"]
        response["body"] = json.dumps({"message": returned["message"]})
        return response
    except VAMSGeneralErrorResponse as v:
        # Raised by the asset lookup when an id is empty or the read fails. The message names no
        # caller-supplied value, so it is returned as the client-facing reason.
        logger.exception(v)
        response["statusCode"] = 400
        response["body"] = json.dumps({"message": str(v)})
        return response
    except Exception as e:
        response["statusCode"] = 500
        logger.exception(e)
        response["body"] = json.dumps({"message": "Internal Server Error"})
        return response
