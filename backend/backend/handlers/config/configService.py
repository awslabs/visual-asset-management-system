#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import ValidationError
from common.apiRoutes import API_SECURE_CONFIG
from common.resourceNames import get_table_name, ResourceKeys
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.config import SecureConfigResponseModel

# Configure AWS clients with retry configuration
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

dynamodb_client = boto3.client('dynamodb', config=retry_config)
ssm_client = boto3.client('ssm', config=retry_config)
geo_client = boto3.client('location', config=retry_config)
deserializer = TypeDeserializer()
logger = safeLogger(service_name="ConfigService")

# Global variables for claims and roles
claims_and_roles = {}

# Load resource names and environment variables
try:
    app_feature_enabled_table_name = get_table_name(ResourceKeys.APP_FEATURE_ENABLED_STORAGE_TABLE)

    # Handler-specific env vars (direct from os.environ)
    location_service_api_key_arn_ssm_param = os.environ.get("LOCATION_SERVICE_API_KEY_ARN_SSM_PARAM")
    location_service_url_format = os.environ.get("LOCATION_SERVICE_URL_FORMAT")
    web_deployed_url_ssm_param = os.environ.get("WEB_DEPLOYED_URL_SSM_PARAM")
except Exception as e:
    logger.exception("Failed loading resource names and environment variables")
    raise e

#######################
# Business Logic Functions
#######################

def get_enabled_features():
    """Get the enabled application feature names as a comma-separated string

    Returns:
        Comma-separated string of enabled feature names
    """
    try:
        logger.info("Scanning and paginating the app feature enabled table")
        paginator = dynamodb_client.get_paginator('scan')
        page_iterator = paginator.paginate(
            TableName=app_feature_enabled_table_name,
            PaginationConfig={
                'MaxItems': 500,
                'PageSize': 500,
                'StartingToken': None
            }
        ).build_full_result()

        items = []
        items.extend(page_iterator['Items'])

        while 'NextToken' in page_iterator:
            next_token = page_iterator['NextToken']
            page_iterator = paginator.paginate(
                TableName=app_feature_enabled_table_name,
                PaginationConfig={
                    'MaxItems': 500,
                    'PageSize': 500,
                    'StartingToken': next_token
                }
            ).build_full_result()
            items.extend(page_iterator['Items'])

        feature_names = []
        for item in items:
            deserialized_document = {k: deserializer.deserialize(v) for k, v in item.items()}
            feature_names.append(deserialized_document['featureName'])

        logger.info(feature_names)
        return ','.join(feature_names)
    except Exception as e:
        logger.exception(f"Error retrieving enabled features: {e}")
        raise e

def get_location_service_api_url():
    """Get the Location Service API URL with the resolved API key

    Retrieves the Location Service API Key ARN from SSM Parameter Store, then the
    key value from AWS Location Services, and substitutes it into the configured
    URL format. Failures are non-fatal and return an empty string.

    Returns:
        The Location Service API URL or empty string if not configured/available
    """
    if not location_service_api_key_arn_ssm_param or not location_service_url_format:
        logger.info("Location Service API Key SSM parameter name or location service URL not configured")
        return ""

    try:
        logger.info(f"Attempting to retrieve Location Service API Key from SSM: {location_service_api_key_arn_ssm_param}")
        ssm_response = ssm_client.get_parameter(
            Name=location_service_api_key_arn_ssm_param,
            WithDecryption=True
        )

        api_key_arn = ssm_response.get('Parameter', {}).get('Value')

        if not api_key_arn:
            logger.warning("Location Service API Key ARN SSM parameter exists but has no value")
            return ""

        logger.info(f"Successfully retrieved Location Service API Key ARN from SSM: {api_key_arn}")

        # Extract the key name from the ARN
        # ARN format: arn:aws:geo:region:account:api-key/key-name
        key_name = api_key_arn.split('/')[-1]
        logger.info(f"Extracted key name from ARN: {key_name}")

        # Get the actual API key value from AWS Location Services
        try:
            logger.info(f"Attempting to retrieve API key value from Location Services for key: {key_name}")
            geo_response = geo_client.describe_key(KeyName=key_name)

            api_key_value = geo_response.get('Key')

            if api_key_value:
                logger.info("Successfully retrieved Location Service API Key value")
                return location_service_url_format.replace("<apiKey>", api_key_value)
            else:
                logger.warning("Location Service API Key retrieved but has no value")
        except ClientError as geo_error:
            logger.error(f"Error retrieving API key value from Location Services: {geo_error}")
        except Exception as geo_ex:
            logger.error(f"Unexpected error retrieving API key value from Location Services: {geo_ex}")

    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', '')
        if error_code == 'ParameterNotFound':
            logger.info("Location Service API Key SSM parameter not found - Location Services may not be enabled")
        else:
            logger.warning(f"Error retrieving Location Service API Key from SSM: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error retrieving Location Service API Key from SSM: {e}")

    return ""

def get_web_deployed_url():
    """Get the deployed web application URL from SSM Parameter Store

    Failures are non-fatal and return an empty string.

    Returns:
        The web deployed URL or empty string if not configured/available
    """
    if not web_deployed_url_ssm_param:
        logger.info("Web Deployed URL SSM parameter name not configured")
        return ""

    try:
        logger.info(f"Attempting to retrieve Web Deployed URL from SSM: {web_deployed_url_ssm_param}")
        ssm_response = ssm_client.get_parameter(
            Name=web_deployed_url_ssm_param,
            WithDecryption=False
        )

        web_url = ssm_response.get('Parameter', {}).get('Value')

        if web_url and web_url.strip():
            logger.info("Successfully retrieved Web Deployed URL from SSM")
            return web_url.strip()
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

    return ""

#######################
# Request Handlers
#######################

def handle_get_request(event):
    """Handle GET requests for the secure runtime configuration

    Args:
        event: API Gateway event

    Returns:
        APIGatewayProxyResponseV2 response
    """
    try:
        response_model = SecureConfigResponseModel(
            featuresEnabled=get_enabled_features(),
            locationServiceApiUrl=get_location_service_api_url(),
            webDeployedUrl=get_web_deployed_url()
        )
        logger.info("Success")
        return success(body=response_model.dict())
    except VAMSGeneralErrorResponse as e:
        return general_error(body={"message": str(e)}, event=event)
    except Exception as e:
        logger.exception(f"Error handling GET request: {e}")
        return internal_error(event=event)

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for config service APIs"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Route to appropriate handler
        if method == 'GET' and API_SECURE_CONFIG.matches(path):
            return handle_get_request(event)
        else:
            return validation_error(body={'message': "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
