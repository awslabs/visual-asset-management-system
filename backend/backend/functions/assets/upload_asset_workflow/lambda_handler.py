# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import json
from typing import Any, Dict
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from functions.assets.upload_asset_workflow.request_handler import UploadAssetWorkflowRequestHandler
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer

from models.assets import UploadAssetWorkflowRequestModel
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error


logger = safeLogger(service_name="UploadAssetWorkflow")
handler = UploadAssetWorkflowRequestHandler(
    sfn_client=boto3.client('stepfunctions'),
    state_machine_arn=os.environ["UPLOAD_WORKFLOW_ARN"]
)

tag_type_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
tag_table_name = os.environ["TAG_STORAGE_TABLE_NAME"]

dynamodbClient = boto3.client('dynamodb')
deserializer = TypeDeserializer()
paginator = dynamodbClient.get_paginator('scan')


def getSetTagTypes(tags):
    uniqueSetTagTypes = []

    #If no tags provided, return no tag types
    if tags is None or len(tags) == 0:
        return uniqueSetTagTypes
    
    #Loop to get all tag results (to know their tag types)
    rawTagItems = []
    page_iteratorTags = paginator.paginate(
        TableName=tag_table_name,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
        }
    ).build_full_result()
    if(len(page_iteratorTags["Items"]) > 0):
        rawTagItems.extend(page_iteratorTags["Items"])
        while("NextToken" in page_iteratorTags):
            page_iteratorTags = paginator.paginate(
                TableName=tag_table_name,
                PaginationConfig={
                    'MaxItems': 1000,
                    'PageSize': 1000,
                    'StartingToken': page_iteratorTags["NextToken"]
                }
            ).build_full_result()
            if(len(page_iteratorTags["Items"]) > 0):
                rawTagItems.extend(page_iteratorTags["Items"])

    #Loop through every tag in the database
    for tag in rawTagItems:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tag.items()}

        #If the tags provided matches the tag looked up, add to uniqueSetTagTypes if it's not already part of the array
        if deserialized_document["tagName"] in tags:
            if deserialized_document["tagTypeName"] not in uniqueSetTagTypes:
                uniqueSetTagTypes.append(deserialized_document["tagTypeName"])
    
    return uniqueSetTagTypes

#Function to lookup and scan tagTypes from dynamoDB that are set to required
def getRequiredTagTypes():
    #Loop to get all tag results for tag type
    rawTagTypeItems = []
    page_iteratorTags = paginator.paginate(
        TableName=tag_type_table_name,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
        }
    ).build_full_result()
    if(len(page_iteratorTags["Items"]) > 0):
        rawTagTypeItems.extend(page_iteratorTags["Items"])
        while("NextToken" in page_iteratorTags):
            page_iteratorTags = paginator.paginate(
                TableName=tag_type_table_name,
                PaginationConfig={
                    'MaxItems': 1000,
                    'PageSize': 1000,
                    'StartingToken': page_iteratorTags["NextToken"]
                }
            ).build_full_result()
            if(len(page_iteratorTags["Items"]) > 0):
                rawTagTypeItems.extend(page_iteratorTags["Items"])

    ##Get tags associated and then exclude tag types from required if no tags associated
    #Loop to get all tag results for tag type
    rawTagItems = []
    page_iteratorTags = paginator.paginate(
        TableName=tag_table_name,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
        }
    ).build_full_result()
    if(len(page_iteratorTags["Items"]) > 0):
        rawTagItems.extend(page_iteratorTags["Items"])
        while("NextToken" in page_iteratorTags):
            page_iteratorTags = paginator.paginate(
                TableName=tag_table_name,
                PaginationConfig={
                    'MaxItems': 1000,
                    'PageSize': 1000,
                    'StartingToken': page_iteratorTags["NextToken"]
                }
            ).build_full_result()
            if(len(page_iteratorTags["Items"]) > 0):
                rawTagItems.extend(page_iteratorTags["Items"])

    tags = []
    for tag in rawTagItems:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tag.items()}
        tags.append(deserialized_document)

    formatted_tag_results = {}
    for tagResult in tags:
        tagName = tagResult["tagName"]
        tagTypeName = tagResult["tagTypeName"]

        if tagTypeName not in formatted_tag_results:
            formatted_tag_results[tagTypeName] = [tagName]
        else:
            formatted_tag_results[tagTypeName].append(tagName)

    #Final tag required loops
    tagTypesRequired = []
    for tagType in rawTagTypeItems:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in tagType.items()}

        #if tagtype has "required" set to true and there are tags in formatted_tag_results for the type, add to list
        if bool(deserialized_document.get("required", "False")):
            if deserialized_document["tagTypeName"] in formatted_tag_results:
                tagTypesRequired.append(deserialized_document["tagTypeName"])

    return tagTypesRequired

def verifyAllRequiredTagsSatisfied(assetTags):

    assetTagTypes = getSetTagTypes(assetTags)
    requiredTagTypes = getRequiredTagTypes()
    missingTagTypesForError =[]

    if requiredTagTypes is None or len(requiredTagTypes) == 0:
        return True
    else:
        for requiredTagType in requiredTagTypes:
            if requiredTagType not in assetTagTypes:
                missingTagTypesForError.append(requiredTagType)

    if len(missingTagTypesForError) == 0:
        return True
    
    #Raise error with list of required tag types missing from assets
    if len(missingTagTypesForError) > 0:
        raise ValueError(f"Asset Details are missing tags of required tag types: {missingTagTypesForError}")


def lambda_handler(event: Dict[Any, Any], context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    response = STANDARD_JSON_RESPONSE

    try:
        logger.info(event['body'])
        request = parse(event['body'], model=UploadAssetWorkflowRequestModel)
        request_context = event.get("requestContext")
        logger.info(request)

        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])

        #Input validation
        if 'databaseId' not in event['body']['uploadAssetBody']:
            message = "No databaseId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        if 'assetId' not in event['body']['uploadAssetBody']:
            message = "No assetId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        logger.info("Validating parameters")
        #required fields
        (valid, message) = validate({
            'databaseId': {
                'value': event['body']['uploadAssetBody']['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': event['body']['uploadAssetBody']['assetId'],
                'validator': 'ID'
            },
            'description': {
                'value': event['body']['uploadAssetBody']['description'],
                'validator': 'STRING_256'
            },
            'assetName': {
                'value': event['body']['uploadAssetBody']['assetName'],
                'validator': 'OBJECT_NAME'
            },
            'assetPathKey': {
                'value': event['body']['uploadAssetBody']['key'],
                'validator': 'ASSET_PATH'
            }
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response
        
        #optional field
        if 'previewLocation' in event['body']['uploadAssetBody'] and event['body']['uploadAssetBody']['previewLocation'] is not None:
            (valid, message) = validate({
                'assetPathKey': {
                    'value': event['body']['uploadAssetBody']['previewLocation']['Key'],
                    'validator': 'ASSET_PATH'
                }
            })
            if not valid:
                logger.error(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

        # Add Casbin Enforcer to check if the current user has permissions to PUT the Asset
        operation_allowed_on_asset = False
        http_method = event['requestContext']['http']['method']
        asset = {
            "object__type": "asset",
            "databaseId": event['body']['uploadAssetBody']['databaseId'],
            "assetType": event['body']['uploadAssetBody']['assetType'],
            "assetName": event['body']['uploadAssetBody'].get('assetName', event['body']['uploadAssetBody']['assetId']),
            "tags": event['body']['uploadAssetBody'].get('tags', [])
        }
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", asset, http_method) and casbin_enforcer.enforceAPI(event):
                operation_allowed_on_asset = True
                break

        # upload a new asset workflow
        if operation_allowed_on_asset:

            #Check for required tags on assets and throw error otherwise
            verifyAllRequiredTagsSatisfied(event['body']['uploadAssetBody'].get('tags', []))

            response = handler.process_request(request=request, request_context=request_context)
            return success(body=response.dict())
        else:
            return {
                "statusCode": 403,
                "body": json.dumps({"message": "Not Authorized"})
            }

    except ValidationError as v:
        logger.exception("ValidationError")
        return validation_error(body={
            'message': str(v)
        })
    except ValueError as v:
        logger.exception("ValueError")
        return validation_error(body={
            'message': str(v)
        })
    except Exception as e:
        logger.exception("Exception")
        return internal_error(body={'message': "Internal Server Error"})
