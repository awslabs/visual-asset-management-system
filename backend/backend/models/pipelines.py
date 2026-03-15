# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import List, Optional, Literal
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate, id_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="PipelineModels")

# Type aliases
PipelineExecutionType = Literal["Lambda", "SQS", "EventBridge"]
PipelineType = Literal["standardFile", "previewFile"]
WaitForCallback = Literal["Enabled", "Disabled"]

######################## Pipeline Resource Models ##########################
class UserProvidedResource(BaseModel, extra='ignore'):
    """Model for a user-provided pipeline resource (Lambda, SQS queue, or EventBridge bus)"""
    resourceId: str  # Lambda name, SQS URL, or EventBridge bus ARN
    resourceType: Optional[str] = "Lambda"  # Defaults to Lambda for backwards compat
    isProvided: Optional[bool] = True  # True=user provided, False=VAMS auto-created (Lambda only)
    eventSource: Optional[str] = None  # EventBridge only
    eventDetailType: Optional[str] = None  # EventBridge only

######################## Create Pipeline API Models ##########################
class CreatePipelineRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new pipeline"""
    pipelineId: str = Field(..., min_length=4, max_length=64, pattern=id_pattern)
    databaseId: str
    pipelineType: PipelineType
    pipelineExecutionType: PipelineExecutionType
    description: str = Field(..., min_length=4, max_length=256)
    assetType: str
    outputType: str
    waitForCallback: WaitForCallback = "Disabled"
    taskTimeout: Optional[str] = None
    taskHeartbeatTimeout: Optional[str] = None
    lambdaName: Optional[str] = None  # Lambda only
    sqsQueueUrl: Optional[str] = None  # SQS only
    eventBridgeBusArn: Optional[str] = None  # EventBridge only, optional - default bus
    eventBridgeSource: Optional[str] = None  # EventBridge only, optional
    eventBridgeDetailType: Optional[str] = None  # EventBridge only, optional
    inputParameters: Optional[str] = None
    updateAssociatedWorkflows: bool = False
    enabled: Optional[bool] = True

    @root_validator
    def validate_fields(cls, values):
        """Validate fields based on pipeline execution type and input format"""
        logger.info("Validating pipeline request parameters")

        # Validate execution-type-specific requirements
        execution_type = values.get('pipelineExecutionType')
        if execution_type == 'SQS' and not values.get('sqsQueueUrl'):
            raise ValueError("sqsQueueUrl is required when pipelineExecutionType is 'SQS'")

        # Validate fields using the common validator framework
        validation_dict = {
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID',
                'allowGlobalKeyword': True
            },
            'pipelineId': {
                'value': values.get('pipelineId'),
                'validator': 'ID'
            },
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            },
            'assetType': {
                'value': values.get('assetType'),
                'validator': 'FILE_EXTENSION'
            },
            'outputType': {
                'value': values.get('outputType'),
                'validator': 'FILE_EXTENSION',
                'optional': True
            },
            'inputParameters': {
                'value': values.get('inputParameters') or '',
                'validator': 'STRING_JSON',
                'optional': True
            }
        }

        # Add execution-type-specific resource validation
        if execution_type == 'SQS':
            validation_dict['sqsQueueUrl'] = {
                'value': values.get('sqsQueueUrl'),
                'validator': 'SQS_QUEUE_URL'
            }
        elif execution_type == 'EventBridge':
            if values.get('eventBridgeBusArn'):
                validation_dict['eventBridgeBusArn'] = {
                    'value': values.get('eventBridgeBusArn'),
                    'validator': 'EVENTBRIDGE_BUS_ARN',
                    'optional': True
                }
            if values.get('eventBridgeSource'):
                validation_dict['eventBridgeSource'] = {
                    'value': values.get('eventBridgeSource'),
                    'validator': 'EVENTBRIDGE_SOURCE',
                    'optional': True
                }
            if values.get('eventBridgeDetailType'):
                validation_dict['eventBridgeDetailType'] = {
                    'value': values.get('eventBridgeDetailType'),
                    'validator': 'EVENTBRIDGE_DETAIL_TYPE',
                    'optional': True
                }

        (valid, message) = validate(validation_dict)
        if not valid:
            logger.error(message)
            raise ValueError(message)

        # Validate taskTimeout and taskHeartbeatTimeout when callback is enabled
        MAX_TASK_TIMEOUT_SECONDS = 604800  # 1 week
        wait_for_callback = values.get('waitForCallback')
        task_timeout = values.get('taskTimeout')
        task_heartbeat = values.get('taskHeartbeatTimeout')

        if task_timeout is not None and task_timeout != '':
            try:
                timeout_val = int(task_timeout)
            except (ValueError, TypeError):
                raise ValueError("taskTimeout must be a positive integer (seconds)")
            if timeout_val <= 0:
                raise ValueError("taskTimeout must be a positive non-zero value (seconds)")
            if timeout_val > MAX_TASK_TIMEOUT_SECONDS:
                raise ValueError(
                    f"taskTimeout cannot exceed {MAX_TASK_TIMEOUT_SECONDS} seconds (1 week). "
                    f"Provided: {timeout_val}"
                )

        if task_heartbeat is not None and task_heartbeat != '':
            try:
                heartbeat_val = int(task_heartbeat)
            except (ValueError, TypeError):
                raise ValueError("taskHeartbeatTimeout must be a positive integer (seconds)")
            if heartbeat_val <= 0:
                raise ValueError("taskHeartbeatTimeout must be a positive non-zero value (seconds)")

        # Heartbeat must be smaller than timeout when both are provided
        if (task_timeout is not None and task_timeout != ''
                and task_heartbeat is not None and task_heartbeat != ''):
            timeout_val = int(task_timeout)
            heartbeat_val = int(task_heartbeat)
            if heartbeat_val >= timeout_val:
                raise ValueError(
                    f"taskHeartbeatTimeout ({heartbeat_val}s) must be smaller than "
                    f"taskTimeout ({timeout_val}s)"
                )

        return values

######################## Pipeline Response Models ##########################
class PipelineResponseModel(BaseModel, extra='ignore'):
    """Response model for a pipeline.

    Includes resource-specific fields (lambdaName, sqsQueueUrl, etc.) that are
    populated by pipelineService from the stored userProvidedResource JSON.
    The frontend (WorkflowPipelineSelector, CreatePipeline edit mode) depends
    on these fields being present in GET responses.
    """
    pipelineId: str
    databaseId: Optional[str] = None
    pipelineType: Optional[str] = None
    pipelineExecutionType: str = "Lambda"  # Default for backwards compat
    description: Optional[str] = None
    assetType: Optional[str] = None
    outputType: Optional[str] = None
    waitForCallback: Optional[str] = "Disabled"
    taskTimeout: Optional[str] = None
    taskHeartbeatTimeout: Optional[str] = None
    userProvidedResource: Optional[str] = None  # Raw JSON string from DynamoDB
    lambdaName: Optional[str] = None  # Extracted from userProvidedResource for Lambda
    sqsQueueUrl: Optional[str] = None  # Extracted from userProvidedResource for SQS
    eventBridgeBusArn: Optional[str] = None  # Extracted from userProvidedResource for EventBridge
    eventBridgeSource: Optional[str] = None  # Extracted from userProvidedResource for EventBridge
    eventBridgeDetailType: Optional[str] = None  # Extracted from userProvidedResource for EventBridge
    inputParameters: Optional[str] = None
    enabled: Optional[bool] = True
    dateCreated: Optional[str] = None
    dateUpdated: Optional[str] = None  # Matches DynamoDB field name

######################## Get Pipelines API Models ##########################
class GetPipelinesRequestModel(BaseModel, extra='ignore'):
    """Request model for listing pipelines"""
    maxItems: Optional[int] = Field(default=30000, ge=1)
    pageSize: Optional[int] = Field(default=3000, ge=1)
    startingToken: Optional[str] = None

class GetPipelinesResponseModel(BaseModel, extra='ignore'):
    """Response model for listing pipelines"""
    Items: List[PipelineResponseModel]
    NextToken: Optional[str] = None
