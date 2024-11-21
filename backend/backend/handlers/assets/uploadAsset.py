#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import uuid
from boto3.dynamodb.conditions import Key
import datetime
from boto3.dynamodb.types import TypeDeserializer
from common.validators import validate
from common.constants import ALLOWED_ASSET_LINKS, STANDARD_JSON_RESPONSE
from handlers.assets.assetCount import update_asset_count
from collections import defaultdict
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType
from models.common import validation_error

claims_and_roles = {}
logger = safeLogger(service_name="UploadAsset")
dynamodb = boto3.resource('dynamodb')
dynamodbClient = boto3.client('dynamodb')
s3c = boto3.client('s3')
lambda_client = boto3.client('lambda')
sns_client = boto3.client('sns')
deserializer = TypeDeserializer()
paginator = dynamodbClient.get_paginator('scan')

main_response = STANDARD_JSON_RESPONSE
asset_database = None
db_database = None
bucket_name = None

try:
    asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    db_database = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    subscriptions_table_name = os.environ["SUBSCRIPTIONS_STORAGE_TABLE_NAME"]
    asset_link_database = os.environ["ASSET_LINKS_STORAGE_TABLE_NAME"]
    tag_type_table_name = os.environ["TAG_TYPES_STORAGE_TABLE_NAME"]
    tag_table_name = os.environ["TAG_STORAGE_TABLE_NAME"]
    send_email_function_name = os.environ["SEND_EMAIL_FUNCTION_NAME"]
    bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]
except:
    logger.exception("Failed loading environment variables")
    main_response['body'] = json.dumps(
        {"message": "Failed Loading Environment Variables"})


#Used when working with dynamodb "client" (not resource abstraction)
def _deserialize(raw_data):
    result = {}
    if not raw_data:
        return result

    deserializer = TypeDeserializer()

    for key, val in raw_data.items():
        result[key] = deserializer.deserialize(val)

    return result

def getS3MetaData(key: str, asset):
    if asset['isMultiFile']:
        return asset
    # VersionId and ContentLength (bytes)
    else:
        resp = s3c.head_object(Bucket=bucket_name, Key=key)
        asset['currentVersion']['S3Version'] = resp['VersionId']
        asset['currentVersion']['FileSize'] = str(
            resp['ContentLength'] / 1000000) + 'MB'
    return asset


def get_all_subscriber_for_asset(asset_id):
    subscription_table = dynamodb.Table(subscriptions_table_name)
    entityName = "Asset"
    result = subscription_table.get_item(
        Key={
            'eventName': "Asset Version Change",
            'entityName_entityId': f'{entityName}#{asset_id}'
        }
    )
    item = result.get('Item', [])
    return item.get("subscribers", [])


def send_subscription_email(asset_id):
    try:
        payload = {
            'asset_id': asset_id,
        }
        lambda_client.invoke(
            FunctionName=send_email_function_name,
            InvocationType='Event',
            Payload=json.dumps(payload)
        )
    except Exception as e:
        logger.exception(f"Error invoking send_email Lambda function: {e}")


def add_asset_links(asset_links, asset_id):
    table = dynamodb.Table(asset_link_database)
    all_links = []
    # TODO: Make a common function for this and validate inputs
    for parent in asset_links.get("parents", []):
        all_links.append({
            "relationId": str(uuid.uuid4()),
            "assetIdFrom": parent,
            "assetIdTo": asset_id,
            "relationshipType": ALLOWED_ASSET_LINKS["PARENT-CHILD"]
        })

    for child in asset_links.get("child", []):
        all_links.append({
            "relationId": str(uuid.uuid4()),
            "assetIdFrom": asset_id,
            "assetIdTo": child,
            "relationshipType": ALLOWED_ASSET_LINKS["PARENT-CHILD"]
        })

    for related in asset_links.get("related", []):
        all_links.append({
            "relationId": str(uuid.uuid4()),
            "assetIdFrom": asset_id,
            "assetIdTo": related,
            "relationshipType": ALLOWED_ASSET_LINKS["RELATED"]
        })

    # TODO: Add batch size max limit is 25 by default
    with table.batch_writer() as batch_writer:
        for item in all_links:
            batch_writer.put_item(Item=item)

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
        if deserialized_document.get("required", "False") == "True":
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


def create_sns_topic_for_asset(asset_id):
    topic_response = sns_client.create_topic(Name=f'AssetTopic-{asset_id}')
    sns_topic_arn = topic_response['TopicArn']
    return sns_topic_arn


def updateParent(asset, parent):
    table = dynamodb.Table(asset_database)
    try:
        databaseId = asset['databaseId']
        assetId = asset['assetId']
        assetS3Version = asset['currentVersion']['S3Version']
        assetVersion = asset['currentVersion']['Version']
        parentId = parent['assetId']
        parentdbId = parent['databaseId']
        pipeline = parent['specifiedPipeline']
        resp = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                parentdbId) & Key('assetId').eq(parentId),
            ScanIndexForward=False,
        )
        item = ''
        if len(resp['Items']) == 0:
            raise ValueError('No Parent of that AssetId')
        else:
            item = resp['Items'][0]
            child = {
                'databaseId': databaseId,
                'assetId': assetId,
                'S3Version': assetS3Version,
                'Version': assetVersion,
                'specifiedPipeline': pipeline
            }
            item['currentVersion']['objectFamily']['Children'].append(child)
            if isinstance(item['currentVersion']['objectFamily']['Parent'], dict):
                _parent = item['currentVersion']['objectFamily']['Parent']
                updateParent(item, _parent)
        table.put_item(
            Item=item
        )
        return json.dumps({"message": "Succeeded"})
    except Exception as e:
        logger.exception(e)
        raise ValueError('Updating Parent Error ')


def iter_Asset(body, item=None):
    asset = item
    version = 1
    dtNow = datetime.datetime.utcnow().strftime('%B %d %Y - %H:%M:%S')
    if asset == None:
        asset = defaultdict(dict)
        asset['databaseId'] = body['databaseId']
        asset['assetId'] = body['assetId']
        asset['assetType'] = body['assetType']
        asset['assetLocation']['Key'] = body['key']
        asset['snsTopic'] = create_sns_topic_for_asset(body['assetId'])
    else:
        if 'versions' not in asset:
            prevVersions = []
        else:
            prevVersions = asset['versions']
        if 'previewLocation' in asset:
            asset['currentVersion']['previewLocation'] = asset['previewLocation']
        prevVersions.append(asset['currentVersion'])
        version = int(asset['currentVersion']['Version']) + 1
        asset['versions'] = prevVersions

        if 'description' not in body:
            body['description'] = asset['description']

        if 'assetName' not in body:
            body['assetName'] = asset['assetName']

    if 'previewLocation' in body and body['previewLocation'] is not None:
        asset['previewLocation'] = {
            "Key": body['previewLocation']['Key']
        }



    asset['assetLocation'] = {
        "Key": body['key']
    }
    asset['assetType'] = body['assetType']
    asset['currentVersion'] = {
        "Comment": body['Comment'],
        'Version': str(version),
        'S3Version': "",
        'DateModified': dtNow,
        'description': body['description'],
        'specifiedPipelines': body['specifiedPipelines']
    }
    asset["tags"] = body.get('tags', [])
    asset['isMultiFile'] = body.get('isMultiFile', False)
    asset['specifiedPipelines'] = body['specifiedPipelines']
    asset['description'] = body['description']
    asset['isDistributable'] = body['isDistributable']
    # Since we started supporting folders / multiple files as a single asset
    # We will have no idea if the asset upload is complete at this point
    # TODO: Temporarily disabled revisioning information till we complete implementation for it.
    # asset = getS3MetaData(body['key'], asset)

    # attributes for generated assets
    asset['assetName'] = body.get('assetName', body['assetId'])
    asset['pipelineId'] = body.get('pipelineId', "")
    asset['executionId'] = body.get('executionId', "")

    #Do MIME check on whatever is uploaded to S3 at this point for this asset, before we do DynamoDB insertion, to validate it's not malicious
    if(not validateS3AssetExtensionsAndContentType(bucket_name, body['assetId'])):
        #TODO: Delete asset and all versions of it from bucket
        #TODO: Change workflow so files get uplaoded first and then this function/workflow should run, error if no asset files are uploaded yet when running this
        raise ValueError('An uploaded asset contains a potentially malicious executable type object. Unable to process asset upload.')

    if 'Parent' in asset:
        asset['objectFamily']['Parent'] = asset['Parent']
        _parent = asset['Parent']
        updateParent(asset, _parent)
    return asset


def upload_Asset(event, body, queryParameters, returnAsset=False, uploadTempLocation=False):
    table = dynamodb.Table(asset_database)
    try:
        db_response = table.query(
            KeyConditionExpression=Key('databaseId').eq(
                body['databaseId']) & Key('assetId').eq(body['assetId']),
            ScanIndexForward=False,
        )

        operation_allowed_on_asset = False
        # upload a new asset
        if not db_response['Items'] and len(db_response['Items']) == 0:
            # Add Casbin Enforcer to check if the current user has permissions to PUT the Asset
            http_method = "PUT"
            asset = {
                "object__type": "asset",
                "databaseId": body['databaseId'],
                "assetType": body['assetType'],
                "assetName": body.get('assetName', body['assetId']),
                "tags": body.get('tags', [])
            }

            for user_name in claims_and_roles["tokens"]:
                casbin_enforcer = CasbinEnforcer(user_name)
                if casbin_enforcer.enforce(f"user::{user_name}", asset, http_method) and casbin_enforcer.enforceAPI(
                            event):
                    operation_allowed_on_asset = True
                    break

            if operation_allowed_on_asset:

                #Do check / lookup on which tagTypes are required to be set on an asset and compare to tags set in body
                #Will throw error if it does nto validate
                verifyAllRequiredTagsSatisfied(event['body'].get("tags", []))

                #If true, asset was uploaded to a temporary location within the S3 bucket and we need to move it now to the correct asset location
                if uploadTempLocation:
                    tempLocationKeyPrefix = os.path.dirname(body['key'])
                    fileNameKey = body['key'].split("/")[-1]
                    finalKeyLocation = body['assetId'] + "/" + fileNameKey

                    #Do MIME check on whatever is uploaded to S3 at this point for this temporary location (ahead of a copy)
                    if(not validateS3AssetExtensionsAndContentType(bucket_name, tempLocationKeyPrefix)):
                        raise ValueError('An uploaded asset at the provided temporary location contains a potentially malicious executable type object. Unable to process asset upload.')

                    copy_source = {
                        'Bucket': bucket_name,
                        'Key': body['key']
                    }
                    s3c.copy_object(CopySource=copy_source, Bucket=bucket_name, Key=finalKeyLocation)

                    #Update the final key location
                    body['key'] = finalKeyLocation

                up = iter_Asset(body)
                table.put_item(Item=up)
                logger.info(up)
                # update assetCount after successful update of new asset
                update_asset_count(db_database, asset_database, queryParameters, body['databaseId'])
                logger.info("up check for assetID in this")
                logger.info(up)

                # Add asset links
                if body.get("assetLinks", []):
                    add_asset_links(body["assetLinks"], body["assetId"])
            else:
                return {
                    "statusCode": 403,
                    "body": json.dumps({"message": "Not Authorized"})
                }

        # update an existing asset
        else:
            # Add Casbin Enforcer to check if the current user has permissions to POST the Asset
            http_method = "POST"
            items = db_response.get('Items')
            asset = items[0] if items else None

            if asset:
                asset.update({
                    "object__type": "asset"
                })
                for user_name in claims_and_roles["tokens"]:
                    casbin_enforcer = CasbinEnforcer(user_name)
                    if casbin_enforcer.enforce(f"user::{user_name}", asset, http_method) and casbin_enforcer.enforceAPI(
                            event):
                        operation_allowed_on_asset = True
                        break

                if operation_allowed_on_asset:

                    #Do check / lookup on which tagTypes are required to be set on an asset and compare to tags set in body
                    #Will throw error if it does nto validate
                    verifyAllRequiredTagsSatisfied(event['body'].get("tags", []))

                    #If true, asset was uploaded to a temporary location within the S3 bucket and we need to move it now to the correct asset location
                    if uploadTempLocation:
                        tempLocationKeyPrefix = os.path.dirname(body['key'])
                        fileNameKey = body['key'].split("/")[-1]
                        finalKeyLocation = body['assetId'] + "/" + fileNameKey

                        #Do MIME check on whatever is uploaded to S3 at this point for this temporary location (ahead of a copy)
                        if(not validateS3AssetExtensionsAndContentType(bucket_name, tempLocationKeyPrefix)):
                            raise ValueError('An uploaded asset at the provided temporary location contains a potentially malicious executable type object. Unable to process asset upload.')

                        copy_source = {
                            'Bucket': bucket_name,
                            'Key': body['key']
                        }
                        s3c.copy_object(CopySource=copy_source, Bucket=bucket_name, Key=finalKeyLocation)

                        #Update the final key location
                        body['key'] = finalKeyLocation

                    up = iter_Asset(body, asset)
                    table.put_item(Item=up)
                    # Send notification on asset version change to subscribers
                    send_subscription_email(asset['assetId'])
                else:
                    return {
                        "statusCode": 403,
                        "body": json.dumps({"message": "Not Authorized"})
                    }
            else:
                return {
                    "statusCode": 404,
                    "body": json.dumps({"message": "Asset not found"})
                }

        if returnAsset:
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "Succeeded", "asset": up})
            }
        else:
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "Succeeded"})
            }
    except Exception as e:
        logger.exception(e)
        raise e


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    logger.info(event)
    global claims_and_roles
    claims_and_roles = request_to_claims(event)
    logger.info("claims and roles", claims_and_roles)

    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])

    try:
        if 'databaseId' not in event['body']:
            message = "No databaseId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        if 'assetId' not in event['body']:
            message = "No assetId in API Call"
            logger.error(message)
            response['statusCode'] = 400
            response['body'] = json.dumps({"message": message})
            return response

        #Required params
        logger.info("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': event['body']['databaseId'],
                'validator': 'ID'
            },
            'assetId': {
                'value': event['body']['assetId'],
                'validator': 'ID'
            },
            'description': {
                'value': event['body'].get('description', ""),
                'validator': 'STRING_256',
                'optional': True
            },
            'assetName': {
                'value': event['body'].get('assetName', ""),
                'validator': 'OBJECT_NAME',
                'optional': True
            },
            'assetPathKey': {
                'value': event['body']['key'],
                'validator': 'ASSET_PATH'
            }    
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response
        
        #optional params
        if 'previewLocation' in event['body'] and event['body']['previewLocation'] is not None:
            (valid, message) = validate({
                'assetPathKey': {
                    'value': event['body'].get("previewLocation", {}).get('Key', ""),
                    'validator': 'ASSET_PATH',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

        returnAsset = False
        if 'returnAsset' in event:
            returnAsset = True

        uploadTempLocation = event['body'].get('uploadTempLocation', False)

        logger.info("Trying to get Data")

        # prepare pagination query parameters for update asset count
        queryParameters = event.get('queryStringParameters', {})
        if 'maxItems' not in queryParameters:
            queryParameters['maxItems'] = 100
            queryParameters['pageSize'] = 100
        else:
            queryParameters['pageSize'] = queryParameters['maxItems']
        if 'startingToken' not in queryParameters:
            queryParameters['startingToken'] = None

        response.update(upload_Asset(event, event['body'], queryParameters, returnAsset, uploadTempLocation))
        logger.info(response)
        return response
    except ValueError as v:
        logger.exception("ValueError")
        return validation_error(body={
            'message': str(v)
        })
    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
