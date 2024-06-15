#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="EnablePipeline")
main_rest_response = STANDARD_JSON_RESPONSE
dynamodb = boto3.resource('dynamodb')
try:
    pipeline_Database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")

    main_rest_response['body'] = json.dumps({"message": "Failed Loading Environment Variables"})


def enablePipeline(databaseId, pipelineId):
    status_code = None
    table = dynamodb.Table(pipeline_Database)
    db_response = table.get_item(Key={'databaseId': databaseId, 'pipelineId': pipelineId})
    pipeline = db_response.get("Item", {})
    allowed = False

    if pipeline:
        # Add Casbin Enforcer to check if the current user has permissions to POST the pipeline:
        pipeline.update({
            "object__type": "pipeline"
        })
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", pipeline, "POST"):
                allowed = True
                break

        if allowed:
            status_code = 200
            table = dynamodb.Table(pipeline_Database)
            logger.info("Enabling pipeline")
            table.update_item(
                Key={
                    'databaseId': databaseId,
                    'pipelineId': pipelineId
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
            message = "Pipeline enabled"
            #logger.info(response)
        else:
            status_code = 403
            message = "Not Authorized"
    else:
        status_code = 404
        message = "Pipeline not found"
    
    logger.info(message)
    return {
        "statusCode": status_code,
        "body": json.dumps({"message": message})
    }


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    logger.info(event)

    try:
        if 'pipelineId' not in event:
            response['statusCode'] = 400
            logger.error("Pipeline id not provided")
            return response
        if 'databaseId' not in event:
            response['statusCode'] = 400
            logger.error("databaseId id not provided")
            return response
        else:
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
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response

            global claims_and_roles
            claims_and_roles = request_to_claims(event)

            http_method = event['requestContext']['http']['method']
            method_allowed_on_api = False
            request_object = {
                "object__type": "api",
                "route__path": event['requestContext']['http']['path']
            }
            for user_name in claims_and_roles["tokens"]:
                casbin_enforcer = CasbinEnforcer(user_name)
                if casbin_enforcer.enforce(f"user::{user_name}", request_object, http_method):
                    method_allowed_on_api = True
                    break
            if method_allowed_on_api:
                try:
                    response.update(enablePipeline(event['databaseId'], event['pipelineId']))
                    return response
                except Exception as e:
                    response['statusCode'] = 500
                    logger.exception(e)
                    response['body'] = json.dumps({"message": "Internal Server Error"})

                    return response
            else:
                response['statusCode'] = 403
                response['body'] = json.dumps({"message": "Not Authorized"})
                return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response

if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)
