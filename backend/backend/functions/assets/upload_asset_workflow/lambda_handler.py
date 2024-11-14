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

from models.assets import UploadAssetWorkflowRequestModel
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error
import boto3

logger = safeLogger(service_name="UploadAssetWorkflow")
handler = UploadAssetWorkflowRequestHandler(
    sfn_client=boto3.client('stepfunctions'),
    state_machine_arn=os.environ["UPLOAD_WORKFLOW_ARN"]
)

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
    except Exception as e:
        logger.exception("Exception")
        return internal_error(body={'message': "Internal Server Error"})
