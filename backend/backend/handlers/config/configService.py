#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger

logger = safeLogger(service="ConfigService")
dynamo_client = boto3.client('dynamodb')
ssm_client = boto3.client('ssm')
geo_client = boto3.client('location')
deserializer = TypeDeserializer()


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    try:
        logger.info("Looking up the requested resource")
        appFeatureEnabledDynamoDBTable = os.getenv("APPFEATUREENABLED_STORAGE_TABLE_NAME", None)

        # Specify the column name you want to aggregate
        appFeatureEnableDynamoDB_feature_column_name = 'featureName'

        # Initialize an empty list to store column values
        appFeatureEnableDynamoDB_column_values = []

        logger.info("Scanning and paginating the table")
        paginator = dynamo_client.get_paginator('scan')
        pageIterator = paginator.paginate(
            TableName=appFeatureEnabledDynamoDBTable,
            PaginationConfig={
                'MaxItems': 500,
                'PageSize': 500,
                'StartingToken': None
            }
        ).build_full_result()

        pageIteratorItems = []
        pageIteratorItems.extend(pageIterator['Items'])

        while 'NextToken' in pageIterator:
            nextToken = pageIterator['NextToken']
            pageIterator = paginator.paginate(
                TableName=appFeatureEnabledDynamoDBTable,
                PaginationConfig={
                    'MaxItems': 500,
                    'PageSize': 500,
                    'StartingToken': nextToken
                }
            ).build_full_result()
            pageIteratorItems.extend(pageIterator['Items'])
        
        logger.info("Constructing results")
        result = {}
        items = []
        for item in pageIteratorItems:
            deserialized_document = {
                k: deserializer.deserialize(v) for k, v in item.items()}
            items.append(deserialized_document)
        result['Items'] = items

        for item in items:
            appFeatureEnableDynamoDB_column_values.append(
                item[appFeatureEnableDynamoDB_feature_column_name])

        logger.info(appFeatureEnableDynamoDB_column_values)

        # Create a concatenated string from the column values
        appFeatureEnabledConcatenated_string = ','.join(
            appFeatureEnableDynamoDB_column_values)

        response = {
            "featuresEnabled": appFeatureEnabledConcatenated_string
        }

        # Attempt to retrieve Location Service API Key from SSM Parameter Store
        location_service_api_key_arn_ssm_param = os.getenv("LOCATION_SERVICE_API_KEY_ARN_SSM_PARAM", None)
        location_service_url_format = os.getenv("LOCATION_SERVICE_URL_FORMAT", None)
        
        #Set response initially to empty
        response['locationServiceApiUrl'] = ""
        response['webDeployedUrl'] = ""

        if location_service_api_key_arn_ssm_param and location_service_url_format and location_service_url_format != "":
            try:
                logger.info(f"Attempting to retrieve Location Service API Key from SSM: {location_service_api_key_arn_ssm_param}")
                ssm_response = ssm_client.get_parameter(
                    Name=location_service_api_key_arn_ssm_param,
                    WithDecryption=True
                )
                
                api_key_arn = ssm_response.get('Parameter', {}).get('Value', None)
                
                if api_key_arn:
                    logger.info(f"Successfully retrieved Location Service API Key ARN from SSM: {api_key_arn}")
                    
                    # Extract the key name from the ARN
                    # ARN format: arn:aws:geo:region:account:api-key/key-name
                    key_name = api_key_arn.split('/')[-1]
                    logger.info(f"Extracted key name from ARN: {key_name}")
                    
                    # Get the actual API key value from AWS Location Services
                    try:
                        logger.info(f"Attempting to retrieve API key value from Location Services for key: {key_name}")
                        geo_response = geo_client.describe_key(
                            KeyName=key_name
                        )
                        
                        api_key_value = geo_response.get('Key', None)
                        
                        if api_key_value:
                            logger.info("Successfully retrieved Location Service API Key value")
                            response['locationServiceApiUrl'] = location_service_url_format.replace("<apiKey>", api_key_value)
                        else:
                            logger.warning("Location Service API Key retrieved but has no value")
                            
                    except ClientError as geo_error:
                        logger.error(f"Error retrieving API key value from Location Services: {geo_error}")
                    except Exception as geo_ex:
                        logger.error(f"Unexpected error retrieving API key value from Location Services: {geo_ex}")
                else:
                    logger.warning("Location Service API Key ARN SSM parameter exists but has no value")
                    
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code == 'ParameterNotFound':
                    logger.info("Location Service API Key SSM parameter not found - Location Services may not be enabled")
                else:
                    logger.warning(f"Error retrieving Location Service API Key from SSM: {e}")
            except Exception as e:
                logger.warning(f"Unexpected error retrieving Location Service API Key from SSM: {e}")
        else:
            logger.info("Location Service API Key SSM parameter name or location service URL not configured")

        # Attempt to retrieve Web Deployed URL from SSM Parameter Store
        web_deployed_url_ssm_param = os.getenv("WEB_DEPLOYED_URL_SSM_PARAM", None)
        
        if web_deployed_url_ssm_param:
            try:
                logger.info(f"Attempting to retrieve Web Deployed URL from SSM: {web_deployed_url_ssm_param}")
                ssm_response = ssm_client.get_parameter(
                    Name=web_deployed_url_ssm_param,
                    WithDecryption=False
                )
                
                web_url = ssm_response.get('Parameter', {}).get('Value', None)
                
                if web_url and web_url.strip():
                    logger.info("Successfully retrieved Web Deployed URL from SSM")
                    response['webDeployedUrl'] = web_url.strip()
                else:
                    logger.info("Web Deployed URL SSM parameter exists but has no value")
                    
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code == 'ParameterNotFound':
                    logger.info("Web Deployed URL SSM parameter not found - web deployment may not be configured")
                else:
                    logger.warning(f"Error retrieving Web Deployed URL from SSM: {e}")
            except Exception as e:
                logger.warning(f"Unexpected error retrieving Web Deployed URL from SSM: {e}")
        else:
            logger.info("Web Deployed URL SSM parameter name not configured")

        logger.info("Success")
        return {
            "statusCode": "200",
            "body": json.dumps(response),
            "headers": {
                "Content-Type": "application/json",
                'Cache-Control': 'no-cache, no-store',
            },
        }
    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
