#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from aws_lambda_powertools.utilities.typing import LambdaContext
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import (
    APIGatewayProxyResponseV2,
    success,
    validation_error,
    authorization_error,
    internal_error,
    general_error,
    VAMSGeneralErrorResponse,
)

logger = safeLogger(service_name="EnablePipeline")

# Configure AWS clients
dynamodb = boto3.resource('dynamodb')

# Load environment variables
try:
    pipeline_database = os.environ.get("PIPELINE_STORAGE_TABLE_NAME")

    if not pipeline_database:
        logger.exception("Failed loading environment variables")
        raise Exception("Failed Loading Environment Variables")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e


#######################
# Utility Functions
#######################

def enable_pipeline(database_id, pipeline_id, claims_and_roles):
    """Enable a pipeline by setting its enabled flag to True"""
    table = dynamodb.Table(pipeline_database)
    db_response = table.get_item(Key={'databaseId': database_id, 'pipelineId': pipeline_id})
    pipeline = db_response.get("Item", {})

    if not pipeline:
        return {'statusCode': 404, 'message': 'Pipeline not found'}

    # Tier 2: Object-level Casbin check
    pipeline.update({"object__type": "pipeline"})
    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if not casbin_enforcer.enforce(pipeline, "POST"):
            return {'statusCode': 403, 'message': 'Not Authorized'}

    logger.info("Enabling pipeline")
    table.update_item(
        Key={
            'databaseId': database_id,
            'pipelineId': pipeline_id
        },
        UpdateExpression='SET #enabled = :true',
        ExpressionAttributeNames={
            '#enabled': 'enabled'
        },
        ExpressionAttributeValues={
            ':true': True
        }
    )
    logger.info("Pipeline is enabled")
    return {'statusCode': 200, 'message': 'Pipeline enabled'}


#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for enabling a pipeline"""
    logger.info(event)

    try:
        # Validate required fields
        if 'pipelineId' not in event:
            return validation_error(body={'message': 'Pipeline id not provided'}, event=event)
        if 'databaseId' not in event:
            return validation_error(body={'message': 'databaseId not provided'}, event=event)

        # Validate parameters
        logger.info("Validating Parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': event['body']['databaseId'],
                'validator': 'ID'
            },
            'pipelineId': {
                'value': event['body']['pipelineId'],
                'validator': 'ID'
            }
        })
        if not valid:
            logger.error(message)
            return validation_error(body={'message': message}, event=event)

        # Tier 1: API-level authorization
        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Enable the pipeline
        result = enable_pipeline(event['databaseId'], event['pipelineId'], claims_and_roles)
        return APIGatewayProxyResponseV2(
            isBase64Encoded=False,
            statusCode=result['statusCode'],
            headers={
                'Content-Type': 'application/json',
                'Cache-Control': 'no-cache, no-store',
            },
            body=json.dumps({'message': result['message']})
        )

    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Unhandled error in lambda_handler: {e}")
        return internal_error(event=event)
