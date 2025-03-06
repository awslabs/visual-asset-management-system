#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json

from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.validators import validate
from common.dynamodb import validate_pagination_info
from common.constants import STANDARD_JSON_RESPONSE

claims_and_roles = {}
logger = safeLogger(service="TagTypeService")
dynamodb = boto3.resource('dynamodb')
dynamodbClient = boto3.client('dynamodb')
main_rest_response = STANDARD_JSON_RESPONSE

try:
    tag_db_table_name = os.environ["TAGS_STORAGE_TABLE_NAME"]
    tag_type_db_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def get_tag_types(response, query_params):
    deserializer = TypeDeserializer()
    paginator = dynamodbClient.get_paginator('scan')


    page_iteratorTagTypes = paginator.paginate(
        TableName=tag_type_db_table_name,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    #Loop to get all tag results for tag type
    rawTagItems = []
    page_iteratorTags = paginator.paginate(
        TableName=tag_db_table_name,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
        }
    ).build_full_result()
    if(len(page_iteratorTags["Items"]) > 0):
        rawTagItems.extend(page_iteratorTags["Items"])
        while("NextToken" in page_iteratorTags):
            page_iteratorTags = paginator.paginate(
                TableName=tag_db_table_name,
                PaginationConfig={
                    'MaxItems': 1000,
                    'PageSize': 1000,
                    'StartingToken': page_iteratorTags["NextToken"]
                }
            ).build_full_result()
            if(len(page_iteratorTags["Items"]) > 0):
                rawTagItems.extend(page_iteratorTags["Items"])


    authorized_tags = []
    for tag in rawTagItems:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tag.items()}

        #Commented out permissions check as it behooves users who do have access to see tag types to see all the tags associated, even if they don't have permission to use them
        # # Add Casbin Enforcer to check if the current user has permissions to GET the Tag
        # deserialized_document.update({
        #     "object__type": "tag"
        # })
        # if len(claims_and_roles["tokens"]) > 0:
        #     casbin_enforcer = CasbinEnforcer(claims_and_roles)
        #     if casbin_enforcer.enforce(deserialized_document, "GET"):

        authorized_tags.append(deserialized_document)

    formatted_tag_results = {}

    for tagResult in authorized_tags:
        tagName = tagResult["tagName"]
        tagTypeName = tagResult["tagTypeName"]

        if tagTypeName not in formatted_tag_results:
            formatted_tag_results[tagTypeName] = [tagName]
        else:
            formatted_tag_results[tagTypeName].append(tagName)

    formattedTagTypeResults = {
        "Items": []
    }

    for tagTypeResult in page_iteratorTagTypes["Items"]:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tagTypeResult.items()}

        tagType = {
            "tagTypeName": deserialized_document["tagTypeName"],
            "description": deserialized_document["description"],
            "required": deserialized_document.get("required", "False"),
            "tags": [] if deserialized_document["tagTypeName"] not in formatted_tag_results else formatted_tag_results[
                deserialized_document["tagTypeName"]]
        }

        # Add Casbin Enforcer to check if the current user has permissions to GET the Tag Type
        tagType.update({
            "object__type": "tagType"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(tagType, "GET"):
                formattedTagTypeResults["Items"].append(tagType)

    if 'NextToken' in page_iteratorTagTypes:
        formattedTagTypeResults['NextToken'] = page_iteratorTagTypes['NextToken']

    response['body'] = json.dumps({"message": formattedTagTypeResults})
    return response


def delete_tag_type(response, pathParameters):
    tag_type_table = dynamodb.Table(tag_type_db_table_name)
    tag_type_name = pathParameters.get("tagTypeId")

    if tag_type_name is None or len(tag_type_name) == 0:
        message = "TagTypeName is a required path parameter."
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": message})
        return response

    (valid, message) = validate({
        'tagTypeName': {
            'value': tag_type_name,
            'validator': 'OBJECT_NAME'
        }
    })

    if not valid:
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response

    tag_type_response = tag_type_table.get_item(Key={'tagTypeName': tag_type_name})
    tag_type = tag_type_response.get("Item", {})

    if tag_type:

        #Scan tag table to see if we have any tags that currently use the type, if so error
        tag_table = dynamodb.Table(tag_db_table_name)
        tagResults = tag_table.scan().get('Items', [])

        for tag in tagResults:
            tagTypeName = tag["tagTypeName"]
            if tagTypeName == tag_type_name:
                response['statusCode'] = 400
                response['body'] = json.dumps({"message": "Cannot delete tag type that is currently in use by a tag"})
                return response

        # Add Casbin Enforcer to check if the current user has permissions to DELETE the Tag
        allowed = False
        tag_type.update({
            "object__type": "tagType"
        })
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforce(tag_type, "DELETE"):
                allowed = True

        if allowed:
            logger.info("Deleting Tag Type: "+tag_type_name)
            tag_type_table.delete_item(
                Key={'tagTypeName': tag_type_name},
                ConditionExpression='attribute_exists(tagTypeName)'
            )
            response['statusCode'] = 200
            response['body'] = json.dumps({"message": "Success"})
            return response
        else:
            response['statusCode'] = 403
            response['message'] = "Action not allowed"
            return response
    else:
        response['statusCode'] = 404
        response['message'] = "Record not found"
        return response


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    logger.info(event)

    pathParameters = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})

    try:
        httpMethod = event['requestContext']['http']['method']

        validate_pagination_info(queryParameters)

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
        if httpMethod == 'GET' and method_allowed_on_api:
            return get_tag_types(response, queryParameters)
        elif httpMethod == 'DELETE' and method_allowed_on_api:
            return delete_tag_type(response, pathParameters)
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
