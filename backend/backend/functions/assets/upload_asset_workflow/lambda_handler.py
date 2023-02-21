# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
from typing import Any, Dict
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from backend.functions.assets.upload_asset_workflow.request_handler import UploadAssetWorkflowRequestHandler
from backend.logging.logger import safeLogger

from backend.models.assets import UploadAssetWorkflowRequestModel
from backend.models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error
import boto3

logger = safeLogger(service_name="UploadAssetWorkflow")
handler = UploadAssetWorkflowRequestHandler(
    sfn_client=boto3.client('stepfunctions'),
    state_machine_arn=os.environ["UPLOAD_WORKFLOW_ARN"]
)


def lambda_handler(event: Dict[Any, Any], context: LambdaContext) -> APIGatewayProxyResponseV2:
    try:
        request = parse(event['body'], model=UploadAssetWorkflowRequestModel)
        logger.info(request)
        response = handler.process_request(request=request)
        return success(body=response.dict())
    except ValidationError as v:
        logger.exception("ValidationError")
        return validation_error(body={
            'message': str(v)
        })
    except Exception as e:
        logger.exception("Exception")
        return internal_error(body={'message': str(e)})
