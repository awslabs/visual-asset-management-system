# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from backend.logging.logger import safeLogger
from backend.models.assets import (
    GetUploadAssetWorkflowStepFunctionInput,
    UploadAssetWorkflowRequestModel,
    UploadAssetWorkflowResponseModel,
)
from mypy_boto3_stepfunctions import Client

logger = safeLogger(child=True)


class UploadAssetWorkflowRequestHandler:

    def __init__(self, sfn_client: Client, state_machine_arn: str) -> None:
        self.sfn_client = sfn_client
        self.stat_machine_arn = state_machine_arn

    def process_request(self, request: UploadAssetWorkflowRequestModel) -> UploadAssetWorkflowResponseModel:
        stepfunction_request = GetUploadAssetWorkflowStepFunctionInput(request)
        sfn_response = self.sfn_client.start_execution(
            stateMachineArn=self.stat_machine_arn,
            input=json.dumps(stepfunction_request.dict())
        )
        logger.info(f"Started uploadAssetWorkflow: Execution Id: {sfn_response['executionArn']}")
        response: UploadAssetWorkflowResponseModel = UploadAssetWorkflowResponseModel(message='Success')
        return response
