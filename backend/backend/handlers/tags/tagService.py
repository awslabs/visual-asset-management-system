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
logger = safeLogger(service="TagService")
dynamodb = boto3.resource('dynamodb')
dynamodbClient = boto3.client('dynamodb')
main_rest_response = STANDARD_JSON_RESPONSE
deserializer = TypeDeserializer()
paginator = dynamodbClient.get_paginator('scan')

try:
    tag_db_table_name = os.environ["TAGS_STORAGE_TABLE_NAME"]
    tag_type_db_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


def delete_handler(response, pathParameters):
    tag_table = dynamodb.Table(tag_db_table_name)
    tag_name = pathParameters.get("tagId")

    if tag_name is None or len(tag_name) == 0:
        message = "TagName is a required path parameter."
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": message})
        return response
    
    (valid, message) = validate({
        'tagName': {
            'value': tag_name,
            'validator': 'OBJECT_NAME'
        }
    })

    if not valid:
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response

    tag_response = tag_table.get_item(Key={'tagName': tag_name})
    tag = tag_response.get("Item", {})

    if tag:
        allowed = False
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the Tag
        tag.update({
            "object__type": "tag"
        })
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", tag, "DELETE"):
                allowed = True
                break

        if allowed:
            logger.info("Deleting Tag:", tag_name)
            tag_table.delete_item(
                Key={'tagName': tag_name},
                ConditionExpression='attribute_exists(tagName)'
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

def get_tag_types():

    rawTagTypeItems = []
    page_iteratorTagTypes = paginator.paginate(
        TableName=tag_type_db_table_name,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
        }
    ).build_full_result()
    if(len(page_iteratorTagTypes["Items"]) > 0):
        rawTagTypeItems.extend(page_iteratorTagTypes["Items"])
        while("NextToken" in page_iteratorTagTypes):
            page_iteratorTagTypes = paginator.paginate(
                TableName=tag_type_db_table_name,
                PaginationConfig={
                    'MaxItems': 1000,
                    'PageSize': 1000,
                    'StartingToken': page_iteratorTagTypes["NextToken"]
                }
            ).build_full_result()
            if(len(page_iteratorTagTypes["Items"]) > 0):
                rawTagTypeItems.extend(page_iteratorTagTypes["Items"])

    tagTypeResults = []
    for tagTypeResult in rawTagTypeItems:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tagTypeResult.items()}

        tagType = {
            "tagTypeName": deserialized_document["tagTypeName"],
            "required": deserialized_document.get("required", "False"),
        }

        tagTypeResults.append(tagType)

    return tagTypeResults


def get_tags(query_params):

    #Get tag types for required tags designation
    tagTypes = get_tag_types()

    page_iteratorTags = paginator.paginate(
        TableName=tag_db_table_name,
        PaginationConfig={
            'MaxItems': int(query_params['maxItems']),
            'PageSize': int(query_params['pageSize']),
            'StartingToken': query_params['startingToken']
        }
    ).build_full_result()

    authorized_tags = {
        "Items":[]
        }
    
    for tag in page_iteratorTags["Items"]:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tag.items()}

        #For each tag type coming back from tags, add "[R]" to the end if it matches to a required tag type
        for tagType in tagTypes:
            if deserialized_document["tagTypeName"] == tagType["tagTypeName"]:
                if tagType["required"] == "True":
                    deserialized_document["tagTypeName"] = deserialized_document["tagTypeName"] + " [R]"
                break

        # Add Casbin Enforcer to check if the current user has permissions to GET the Tag
        deserialized_document.update({
            "object__type": "tag"
        })

        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", deserialized_document, "GET"):
                authorized_tags["Items"].append(deserialized_document)

    if 'NextToken' in page_iteratorTags:
        authorized_tags['NextToken'] = page_iteratorTags['NextToken']

    return authorized_tags


def get_handler(response, queryParameters):
    response['statusCode'] = 200
    response['body'] = json.dumps({"message": get_tags(queryParameters)})
    return response


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE

    pathParameters = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})

    try:
        httpMethod = event['requestContext']['http']['method']

        validate_pagination_info(queryParameters)

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        method_allowed_on_api = False
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if httpMethod == 'GET' and method_allowed_on_api:
            return get_handler(response, queryParameters)
        elif httpMethod == 'DELETE' and method_allowed_on_api:
            return delete_handler(response, pathParameters)
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except Exception as e:
        logger.exception(e)
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": "TagName doesn't exists."})
        else:
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
