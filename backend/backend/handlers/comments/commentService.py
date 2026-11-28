#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import copy
import boto3
from botocore.config import Config
import json
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from common.resourceNames import get_table_name, ResourceKeys
from common.validators import validate, parse_show_archived, SHOW_ARCHIVED_INVALID_MESSAGE
from handlers.auth import request_to_claims
from common.auth.apiEvent import normalize_event
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from common.dynamodb import get_asset_object_from_id, asset_authorization_object
from common.commentSubscriptionKeys import (
    COMMENT_PARTITION_KEY,
    COMMENT_SORT_KEY,
    COMMENT_VERSION_INDEX,
    comment_partition,
    comment_response,
)
from models.common import VAMSGeneralErrorResponse
from customLogging.logger import safeLogger
from common.eventLogSummary import event_log_summary
from customLogging.auditLogging import audited_handler, log_action
from common.dynamodb import (
    validate_pagination_info,
    MAX_PAGINATION_MAX_ITEMS,
    MAX_PAGINATION_PAGE_SIZE,
)

claims_and_roles = {}

logger = safeLogger(service="CommentService")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource("dynamodb", config=retry_config)
dynamodb_client = boto3.client("dynamodb", config=retry_config)
main_rest_response = copy.deepcopy(STANDARD_JSON_RESPONSE)

try:
    comment_database = get_table_name(ResourceKeys.COMMENT_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving comment table name")
    comment_database = None
    main_rest_response["body"]["message"] = "Failed resolving comment table name"


def _pagination_config(queryParams: dict) -> dict:
    # build_full_result() accumulates pages until MaxItems is reached, so the budget is bounded at the
    # shared ceiling here as well as by the caller's validation.
    pagination_config = {
        'MaxItems': min(int(queryParams['maxItems']), MAX_PAGINATION_MAX_ITEMS),
        'PageSize': min(int(queryParams['pageSize']), MAX_PAGINATION_PAGE_SIZE),
    }
    if queryParams['startingToken'] and queryParams['startingToken'] != "":
        pagination_config['StartingToken'] = queryParams['startingToken']
    return pagination_config


def get_comments(databaseId: str, assetId: str, queryParams: dict, assetVersionId: str = None) -> list:
    """
    Gets the comments of one asset, newest first, optionally only those on one asset version
    :param databaseId: database of the asset
    :param assetId: id of the asset to get comments for
    :param queryParams: pagination information
    :param assetVersionId: when set, only the comments on this asset version (a query of the version index)
    :returns: the comments, without the table's composite key attribute
    """
    paginator = dynamodb.meta.client.get_paginator('query')
    key_condition = Key(COMMENT_PARTITION_KEY).eq(comment_partition(databaseId, assetId))
    query_kwargs = {}
    if assetVersionId is not None:
        key_condition = key_condition & Key("assetVersionId").eq(assetVersionId)
        query_kwargs["IndexName"] = COMMENT_VERSION_INDEX

    try:
        response = paginator.paginate(
            TableName=comment_database,
            KeyConditionExpression=key_condition,
            PaginationConfig=_pagination_config(queryParams),
            **query_kwargs,
        ).build_full_result()
    except Exception as e:
        logger.exception(f"Error in get_comments: {str(e)}")
        # A failed read reaches the handler's 500 path rather than reading as no comments
        raise

    items = [comment_response(item) for item in response["Items"]]
    # The sort key is a random comment id, so the order is the creation time's
    items.sort(key=lambda item: str(item.get("dateCreated") or ""), reverse=True)
    return items


def get_single_comment(databaseId: str, assetId: str, commentId: str) -> dict:
    """
    Gets one stored comment row of an asset
    :param databaseId: database of the asset
    :param assetId: id of the asset the comment is attached to
    :param commentId: the comment's id
    :returns: the stored row, or {} when the asset has no such comment
    """
    logger.info("Getting single comment")
    table = dynamodb.Table(comment_database)

    response = table.get_item(Key={
        COMMENT_PARTITION_KEY: comment_partition(databaseId, assetId),
        COMMENT_SORT_KEY: commentId,
    })
    return response.get("Item", {})


def delete_comment(databaseId: str, assetId: str, commentId: str, userId: str) -> dict:
    """
    Soft-deletes a comment: moves it to the asset's "#deleted" partition
    :param databaseId: database of the asset
    :param assetId: id of the asset the comment is attached to
    :param commentId: the comment's id
    :param userId: the caller, who must be the comment's creator
    :returns: dictionary with status code, message and the deleted comment's asset version id
    """
    response = {"statusCode": 404, "message": "Record not found"}
    item = get_single_comment(databaseId, assetId, commentId)
    if item:
        logger.info(
            f"Comment to delete: assetId={assetId}, commentId={commentId}, "
            f"commentBody={len(item.get('commentBody') or '')} char(s)"
        )

        logger.info("Verifying user")
        comment_user_id = item["commentOwnerID"]
        if userId != comment_user_id:
            logger.warning("invalid user - ownerID mismatch with caller")
            response["statusCode"] = 403
            response["message"] = "Unauthorized - only the creator of the comment can delete it"
            return response

        logger.info("Deleting comment")
        deleted_item = dict(item)
        deleted_item[COMMENT_PARTITION_KEY] = comment_partition(databaseId, assetId, deleted=True)

        # Soft-delete atomically: write the #deleted copy and remove the original in a single
        # TransactWriteItems so a partial failure cannot lose the comment.
        try:
            serializer = TypeSerializer()
            serialized_item = {k: serializer.serialize(v) for k, v in deleted_item.items()}
            dynamodb_client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": comment_database,
                            "Item": serialized_item,
                        }
                    },
                    {
                        "Delete": {
                            "TableName": comment_database,
                            "Key": {
                                COMMENT_PARTITION_KEY: {"S": comment_partition(databaseId, assetId)},
                                COMMENT_SORT_KEY: {"S": commentId},
                            },
                        }
                    },
                ]
            )
        except Exception as e:
            logger.exception(e)
            response["statusCode"] = 500
            response["message"] = "Internal Server Error"
            return response

        response["statusCode"] = 200
        response["message"] = "Comment deleted"
        response["assetVersionId"] = item.get("assetVersionId")
    return response


def _validate_path(pathParameters: dict, response: dict, require_comment: bool):
    """Validate the database, asset and comment ids of the path. Returns an error response or None."""
    required_field_names = ['databaseId', 'assetId'] + (['commentId'] if require_comment else [])
    missing_field_names = [name for name in required_field_names if not pathParameters.get(name)]
    if missing_field_names:
        response['body'] = json.dumps(
            {"message": 'Missing path parameter(s) (%s) in API call' % (', '.join(missing_field_names))})
        response['statusCode'] = 400
        return response

    values = {
        "databaseId": {"value": pathParameters["databaseId"], "validator": "ID"},
        "assetId": {"value": pathParameters["assetId"], "validator": "ASSET_ID"},
    }
    if "commentId" in pathParameters:
        values["commentId"] = {"value": pathParameters["commentId"], "validator": "ID"}
    (valid, message) = validate(values)
    if not valid:
        logger.warning(message)
        response["body"] = json.dumps({"message": message})
        response["statusCode"] = 400
        return response
    return None


def _authorize_asset(databaseId: str, assetId: str, action: str, include_archived: bool = False):
    """Tier 2 on the asset of the named database, decided before its existence is revealed.

    Returns (allowed, asset): asset is the stored record or None. A refused caller gets the same
    answer whether or not the asset exists; the read includes the archived row only when asked.
    """
    asset_object = get_asset_object_from_id(databaseId, assetId, include_archived=include_archived)
    authorization_object = asset_authorization_object(
        asset_object, databaseId, assetId, include_archived=include_archived)
    if len(claims_and_roles["tokens"]) == 0:
        return False, asset_object
    return CasbinEnforcer(claims_and_roles).enforce(authorization_object, action), asset_object


def get_handler(response: dict, pathParameters: dict, queryParameters: dict) -> dict:
    """
    Function to handle the get request and route it to the right function
    :param response: dictionary holding information about the response
    :param pathParameters: the database, asset and (for one comment) comment ids
    :param queryParameters: pagination information, showArchived and the optional assetVersionId filter
    :returns: Http response object (statusCode, headers, body) with comments stored in the body (if successful)
    """
    show_archived = parse_show_archived(queryParameters)
    if show_archived is None:
        response["statusCode"] = 400
        response["body"] = json.dumps({"message": SHOW_ARCHIVED_INVALID_MESSAGE})
        return response

    error = _validate_path(pathParameters, response, require_comment=False)
    if error:
        return error

    asset_version_id = queryParameters.get("assetVersionId")
    if asset_version_id is not None:
        (valid, message) = validate({"assetVersionId": {"value": asset_version_id, "validator": "STRING_256"}})
        if not valid or not str(asset_version_id).strip():
            response["statusCode"] = 400
            response["body"] = json.dumps({"message": message or "assetVersionId must not be empty"})
            return response

    databaseId = pathParameters["databaseId"]
    assetId = pathParameters["assetId"]

    # showArchived also resolves an archived asset; its comments stay keyed by the original
    # database id. Authorization is decided before existence.
    allowed, asset_object = _authorize_asset(databaseId, assetId, "GET", include_archived=show_archived)
    if not allowed:
        response["statusCode"] = 403
        response["body"] = json.dumps({"message": "Action not allowed"})
        return response
    if asset_object is None:
        response["statusCode"] = 404
        response["body"] = json.dumps({"message": "Asset not found"})
        return response

    if pathParameters.get("commentId"):
        logger.info(f"Getting comment {pathParameters['commentId']} of asset {assetId}")
        response["body"] = json.dumps(
            {"message": comment_response(get_single_comment(databaseId, assetId, pathParameters["commentId"]))})
        response["statusCode"] = 200
        return response

    logger.info(f"Listing comments for asset: {assetId}")
    response["body"] = json.dumps(
        {"message": get_comments(databaseId, assetId, queryParameters, asset_version_id)})
    response["statusCode"] = 200
    return response


def delete_handler(response: dict, pathParameters: dict, event: dict) -> dict:
    """
    Function to handle the delete request
    :param response: dictionary holding information about the response
    :param pathParameters: the database, asset and comment ids
    :param event: Lambda event dictionary
    :returns: Http response object (statusCode, headers, body)
    """
    error = _validate_path(pathParameters, response, require_comment=True)
    if error:
        return error

    databaseId = pathParameters["databaseId"]
    assetId = pathParameters["assetId"]
    commentId = pathParameters["commentId"]

    allowed, asset_object = _authorize_asset(databaseId, assetId, "DELETE")
    if not allowed:
        response["statusCode"] = 403
        response["body"] = json.dumps({"message": "Action not allowed"})
        return response
    if asset_object is None:
        response["statusCode"] = 404
        response["body"] = json.dumps({"message": "Asset not found"})
        return response

    # Get user ID of person making request
    userId = claims_and_roles.get("tokens", ["SYSTEM_USER"])[0]

    logger.info(f"Deleting comment {commentId} of asset {assetId}")
    result = delete_comment(databaseId, assetId, commentId, userId)
    if result["statusCode"] == 200:
        log_action(event, "commentDelete", database_id=databaseId, asset_id=assetId,
                   assetVersionId=result.get("assetVersionId"), commentId=commentId)

    response["body"] = json.dumps({"message": result["message"]})
    response["statusCode"] = result["statusCode"]
    return response


def _log_comment_read(event, path_parameters, query_parameters):
    request_parameters = dict(query_parameters or {})
    if path_parameters.get("commentId"):
        log_action(event, "commentGet", database_id=path_parameters.get("databaseId"),
                   asset_id=path_parameters.get("assetId"), commentId=path_parameters["commentId"],
                   request_parameters=request_parameters)
    else:
        log_action(event, "commentList", database_id=path_parameters.get("databaseId"),
                   asset_id=path_parameters.get("assetId"),
                   assetVersionId=request_parameters.get("assetVersionId"),
                   request_parameters=request_parameters)


@audited_handler
def lambda_handler(event: dict, context: dict) -> dict:
    """
    Lambda handler for the API calls directed to commentService
    :param event: Lambda event dictionary
    :param context: lambda context dictionary
    :returns: Http response object (statusCode, headers, body)
    """
    normalize_event(event)
    response = copy.deepcopy(STANDARD_JSON_RESPONSE)
    logger.info(f"Event: {event_log_summary(event)}")
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

        # Tier 1: may the caller call this route at all
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            response["statusCode"] = 403
            response["body"] = json.dumps({"message": "Action not allowed"})
            return response

        if httpMethod == "GET":
            result = get_handler(response, pathParameters, queryParameters)
            if result.get("statusCode") == 200:
                _log_comment_read(event, pathParameters, queryParameters)
            return result
        if httpMethod == "DELETE":
            result = delete_handler(response, pathParameters, event)
            return result

        # If we get here, we didn't handle the request
        logger.error("No handler matched")
        response["statusCode"] = 405
        response["body"] = json.dumps({"message": "Method not allowed"})
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


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)
