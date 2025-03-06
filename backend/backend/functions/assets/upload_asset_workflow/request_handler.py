# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from customLogging.logger import safeLogger
from models.assets import (
    GetUploadAssetWorkflowStepFunctionInput,
    UploadAssetWorkflowRequestModel,
    UploadAssetWorkflowResponseModel,
)
from mypy_boto3_stepfunctions import Client

logger = safeLogger(service_name="UploadAssetWorkflowRequestHandler")


class UploadAssetWorkflowRequestHandler:

    def __init__(self, sfn_client: Client, state_machine_arn: str) -> None:
        self.sfn_client = sfn_client
        self.stat_machine_arn = state_machine_arn

    def process_request(self, request: UploadAssetWorkflowRequestModel, request_context, request_headers) -> UploadAssetWorkflowResponseModel:
        stepfunction_request = GetUploadAssetWorkflowStepFunctionInput(request)
        step_function_input = stepfunction_request.dict()
        if step_function_input.get("uploadAssetBody"):
            step_function_input["uploadAssetBody"].update({
                "requestContext": request_context,
                "headers": request_headers
            })
        if step_function_input.get("updateMetadataBody"):
            step_function_input["updateMetadataBody"].update({
                "requestContext": request_context,
                "headers": request_headers
            })
            
        if step_function_input.get("copyObjectBody"):
            step_function_input["copyObjectBody"].update({
                    "headers": request_headers
                })
        if step_function_input.get("executeWorkflowBody"):
            step_function_input["executeWorkflowBody"].update({
                    "headers": request_headers
                })
            
        sfn_response = self.sfn_client.start_execution(
            stateMachineArn=self.stat_machine_arn,
            input=json.dumps(step_function_input)
        )
        logger.info(f"Started uploadAssetWorkflow: Execution Id: {sfn_response['executionArn']}")
        response: UploadAssetWorkflowResponseModel = UploadAssetWorkflowResponseModel(message='Success')
        return response
