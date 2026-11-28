#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import copy
import uuid
import boto3
from botocore.config import Config
import json
from common.timestamps import utc_now
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate
from handlers.auth import request_to_claims
from common.auth.apiEvent import normalize_event
from handlers.authz import CasbinEnforcer
from common.dynamodb import get_asset_object_from_id, asset_authorization_object
from common.commentSubscriptionKeys import COMMENT_PARTITION_KEY, COMMENT_SORT_KEY, comment_partition
from common.constants import STANDARD_JSON_RESPONSE
from models.common import VAMSGeneralErrorResponse
from customLogging.logger import safeLogger
from common.eventLogSummary import event_log_summary
from customLogging.auditLogging import audited_handler, log_action

claims_and_roles = {}

logger = safeLogger(service="AddComment")

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


def add_comment(databaseId: str, assetId: str, assetVersionId: str, commentBody: str, userId: str) -> dict:
    """
    Creates a comment on one asset version under a new server-generated comment id
    :param databaseId: database of the asset the comment is attached to
    :param assetId: id of the asset the comment is attached to
    :param assetVersionId: the asset version the comment is about
    :param commentBody: the comment text
    :param userId: the caller, recorded as the comment's owner
    :returns: dictionary with status code, message and the new comment's id
    """
    response = {"statusCode": 200, "message": "Succeeded"}
    table = dynamodb.Table(comment_database)
    dtNow = utc_now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    comment_id = str(uuid.uuid4())
    item = {
        COMMENT_PARTITION_KEY: comment_partition(databaseId, assetId),
        COMMENT_SORT_KEY: comment_id,
        "databaseId": databaseId,
        "assetId": assetId,
        "assetVersionId": assetVersionId,
        "commentBody": commentBody,
        "commentOwnerID": userId,
        "commentOwnerUsername": userId,
        "dateCreated": dtNow,
    }
    try:
        # A comment id is never reused: the write refuses a key the table already holds
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(commentId)")
    except Exception as e:
        logger.exception(e)
        response["statusCode"] = 500
        response["message"] = "Internal Server Error"
        return response

    logger.info(
        f"Comment added: assetId={assetId}, commentId={comment_id}, "
        f"commentBody={len(commentBody)} char(s)"
    )
    response["commentId"] = comment_id
    return response


@audited_handler
def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda handler for API calls that add a comment
    :param event: Lamdba event dictionary
    :param context: Lambda context disctionary
    :returns: Http response object (statusCode, headers, body)
    """
    normalize_event(event)
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
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

        httpMethod = event['requestContext']['http']['method']

        # Tier 1: may the caller call this route at all
        if len(claims_and_roles["tokens"]) == 0 or not CasbinEnforcer(claims_and_roles).enforceAPI(event):
            response["statusCode"] = 403
            response["body"] = json.dumps({"message": "Action not allowed"})
            return response

        required_field_names = ['databaseId', 'assetId']
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
            }
        )
        if not valid:
            logger.warning(message)
            response["body"] = json.dumps({"message": message})
            response["statusCode"] = 400
            return response

        databaseId = pathParameters["databaseId"]
        assetId = pathParameters["assetId"]

        # Tier 2 on the asset of the named database, decided before its existence is revealed
        asset_object = get_asset_object_from_id(databaseId, assetId)
        authorization_object = asset_authorization_object(asset_object, databaseId, assetId)
        userId = claims_and_roles["tokens"][0]
        if not CasbinEnforcer(claims_and_roles).enforce(authorization_object, httpMethod):
            response["statusCode"] = 403
            response["body"] = json.dumps({"message": "Action not allowed"})
            return response
        if asset_object is None:
            response["statusCode"] = 404
            response["body"] = json.dumps({"message": "Asset not found"})
            return response

        required_field_names = ['commentBody', 'assetVersionId']
        missing_field_names = [name for name in required_field_names if name not in event['body']]
        if missing_field_names:
            message = 'Missing body parameter(s) (%s) in API call' % (', '.join(missing_field_names))
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        (valid, message) = validate(
            {
                "commentBody": {"value": event["body"]["commentBody"], "validator": "STRING_16384"},
                "assetVersionId": {"value": event["body"]["assetVersionId"], "validator": "STRING_256"},
            }
        )
        if valid and not str(event["body"]["assetVersionId"]).strip():
            (valid, message) = (False, "assetVersionId must not be empty")
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        assetVersionId = str(event["body"]["assetVersionId"])
        returned = add_comment(databaseId, assetId, assetVersionId, event["body"]["commentBody"], userId)
        body = {"message": returned["message"]}
        if returned["statusCode"] == 200:
            body["commentId"] = returned["commentId"]
            log_action(event, "commentCreate", database_id=databaseId, asset_id=assetId,
                       assetVersionId=assetVersionId, commentId=returned["commentId"],
                       changes={"commentBody": event["body"]["commentBody"]})
        response["statusCode"] = returned["statusCode"]
        response["body"] = json.dumps(body)
        return response
    except VAMSGeneralErrorResponse as v:
        # Raised by the asset lookup when an id is empty or the read fails. The message names no
        # caller-supplied value, so it is returned as the client-facing reason.
        logger.exception(v)
        response["statusCode"] = 400
        response["body"] = json.dumps({"message": str(v)})
        return response
    except Exception as e:
        logger.exception("caught exception")
        response["statusCode"] = 500
        response["body"] = json.dumps({"message": "Internal Server Error"})
        return response
