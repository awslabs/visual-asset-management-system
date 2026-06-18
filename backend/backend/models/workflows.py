# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from typing import List, Optional
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate, id_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="WorkflowModels")

######################## Workflow Pipeline Models ##########################
class WorkflowPipelineFunction(BaseModel, extra='ignore'):
    """Model for a pipeline function within a workflow"""
    name: str
    databaseId: str
    pipelineType: str
    pipelineExecutionType: str = "Lambda"  # Default for backwards compat
    outputType: str
    waitForCallback: str = "Disabled"
    taskTimeout: Optional[str] = None
    taskHeartbeatTimeout: Optional[str] = None
    userProvidedResource: Optional[str] = None
    inputParameters: Optional[str] = None

class SpecifiedPipelines(BaseModel, extra='ignore'):
    """Model for the list of pipelines in a workflow"""
    functions: List[WorkflowPipelineFunction]

######################## Create Workflow API Models ##########################
class CreateWorkflowRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a new workflow"""
    workflowId: str = Field(..., min_length=4, max_length=64, pattern=id_pattern)
    databaseId: str
    description: str = Field(..., min_length=4, max_length=256)
    specifiedPipelines: SpecifiedPipelines
    autoTriggerOnFileExtensionsUpload: Optional[str] = ""

    @root_validator
    def validate_fields(cls, values):
        """Validate workflow request fields"""
        logger.info("Validating workflow request parameters")

        # Validate at least one pipeline function is provided
        specified_pipelines = values.get('specifiedPipelines')
        if specified_pipelines and hasattr(specified_pipelines, 'functions'):
            if len(specified_pipelines.functions) < 1:
                raise ValueError("At least one pipeline function is required in specifiedPipelines")
        else:
            raise ValueError("specifiedPipelines with at least one pipeline function is required")

        # Extract pipeline names for ID format validation
        pipeline_names = [f.name for f in specified_pipelines.functions]

        # Validate fields using the common validator framework
        validation_dict = {
            'databaseId': {
                'value': values.get('databaseId'),
                'validator': 'ID',
                'allowGlobalKeyword': True
            },
            'workflowId': {
                'value': values.get('workflowId'),
                'validator': 'ID'
            },
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            },
            'pipelineId': {
                'value': pipeline_names,
                'validator': 'ID_ARRAY'
            }
        }

        (valid, message) = validate(validation_dict)
        if not valid:
            logger.error(message)
            raise ValueError(message)

        # Validate autoTriggerOnFileExtensionsUpload format if provided
        auto_trigger = values.get('autoTriggerOnFileExtensionsUpload', '')
        if auto_trigger and auto_trigger.strip():
            trigger_value = auto_trigger.strip().lower()
            if trigger_value not in ['.all', 'all']:
                # Parse comma-delimited extensions and validate format
                for ext in auto_trigger.split(','):
                    ext = ext.strip()
                    if not ext:
                        continue
                    ext_clean = ext.lstrip('.').lower()
                    if not ext_clean:
                        continue
                    if not all(c.isalnum() or c in ['-', '_'] for c in ext_clean):
                        raise ValueError(
                            "Invalid autoTriggerOnFileExtensionsUpload format. "
                            "Must be comma-delimited extensions (e.g., 'jpg,png,pdf') or 'all'."
                        )

        return values

######################## Workflow Response Models ##########################
class WorkflowResponseModel(BaseModel, extra='ignore'):
    """Response model for a workflow"""
    workflowId: str
    databaseId: Optional[str] = None
    description: Optional[str] = None
    specifiedPipelines: Optional[SpecifiedPipelines] = None
    workflow_arn: Optional[str] = None
    autoTriggerOnFileExtensionsUpload: Optional[str] = ""
    dateCreated: Optional[str] = None
    dateModified: Optional[str] = None

######################## Get Workflows API Models ##########################
class GetWorkflowsRequestModel(BaseModel, extra='ignore'):
    """Request model for listing workflows"""
    maxItems: Optional[int] = Field(default=30000, ge=1)
    pageSize: Optional[int] = Field(default=3000, ge=1)
    startingToken: Optional[str] = None

class GetWorkflowsResponseModel(BaseModel, extra='ignore'):
    """Response model for listing workflows"""
    Items: List[WorkflowResponseModel]
    NextToken: Optional[str] = None

######################## Execute Workflow API Models ##########################
class ExecuteWorkflowRequestModel(BaseModel, extra='ignore'):
    """Request body model for executing a workflow.

    The execute endpoint also takes databaseId / assetId / workflowId as path
    parameters (validated separately in the handler). This body model carries the
    target workflow's database and an optional specific file key to run against.
    `triggerSource` is an internal marker set by the auto-trigger (SQS) caller; it is
    accepted but not a client-facing field.
    """
    # Declared Optional so a missing/empty value flows to the validate() dispatcher
    # below, which emits the exact "workflowDatabaseId is a required field." message
    # the prior handler returned (rather than Pydantic's "field required").
    workflowDatabaseId: Optional[str] = None
    fileKey: Optional[str] = None
    triggerSource: Optional[str] = None

    @root_validator
    def validate_fields(cls, values):
        # Mirror the original handler validation exactly so error messages are
        # unchanged: workflowDatabaseId is a required ID (GLOBAL allowed); fileKey,
        # when provided, is an optional ASSET_PATH (file, not folder).
        (valid, message) = validate({
            'workflowDatabaseId': {
                'value': values.get('workflowDatabaseId', '') or '',
                'validator': 'ID',
                'allowGlobalKeyword': True
            },
            'assetKey': {
                'value': values.get('fileKey', '') or '',
                'validator': 'ASSET_PATH',
                'isFolder': False,
                'optional': True
            },
        })
        if not valid:
            raise ValueError(message)
        return values


class ExecuteWorkflowResponseModel(BaseModel, extra='ignore'):
    """Response model for a launched workflow execution.

    The execute endpoint returns the new execution id in the `message` field; this
    model documents that body shape (`{"message": "<executionId>"}`)."""
    message: str


######################## List Executions API Models ##########################
class ListExecutionsRequestModel(BaseModel, extra='ignore'):
    """Request body model for listing an asset's workflow executions.

    databaseId / assetId / (optional) workflowId arrive as path parameters and are
    validated in the handler. The body optionally carries the workflow's database to
    filter by a specific workflow.
    """
    workflowDatabaseId: Optional[str] = None

    @root_validator
    def validate_fields(cls, values):
        (valid, message) = validate({
            'workflowDatabaseId': {
                'value': values.get('workflowDatabaseId', '') or '',
                'validator': 'ID',
                'allowGlobalKeyword': True,
                'optional': True
            }
        })
        if not valid:
            raise ValueError(message)
        return values


class WorkflowExecutionResponseModel(BaseModel, extra='ignore'):
    """Response model for a single workflow execution item in the executions list.

    Mirrors the exact wire fields the frontend (`WorkflowTab.tsx`,
    `WorkflowExecutionListDefinition.tsx`) and CLI (`format_execution_output`) read.
    """
    workflowDatabaseId: Optional[str] = None
    workflowId: Optional[str] = None
    executionId: Optional[str] = None
    executionStatus: Optional[str] = None
    startDate: Optional[str] = None
    stopDate: Optional[str] = None
    inputAssetFileKey: Optional[str] = None
    databaseId: Optional[str] = None
    assetId: Optional[str] = None
    executionError: Optional[str] = None
    executionLog: Optional[str] = None
