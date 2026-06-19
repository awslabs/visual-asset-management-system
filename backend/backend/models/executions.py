# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v1 models for the workflow-execution storage data model (Stage 1).

These document the canonical record shapes for the 11 execution storage tables.
Handlers persist dicts via common.executionRecords builders; these models are
used for validation and parsing where helpful. All use the v1 idiom
(BaseModel from aws_lambda_powertools, extra='ignore').
"""

from typing import Any, Dict, List, Optional
from aws_lambda_powertools.utilities.parser import BaseModel, validator
from customLogging.logger import safeLogger

logger = safeLogger(service_name="ExecutionModels")

TRIGGER_TYPES = ("Manual", "File-Upload")


class WorkflowExecutionRecord(BaseModel, extra='ignore'):
    """Main WorkflowExecutionsStorageTableV2 row (workflow-keyed)."""
    executionId: str
    workflowId: str
    workflowDatabaseId: str
    workflow_arn: Optional[str] = ""
    workflow_execution_arn: Optional[str] = ""
    executionStartDate: Optional[str] = ""
    executionStopDate: Optional[str] = ""
    executionStatus: Optional[str] = "NEW"
    triggeredByUserId: Optional[str] = "system"
    triggerType: str = "Manual"
    executionLogGroupArn: Optional[str] = ""
    # ISO-8601 timestamp of the last Step Functions describe_execution poll (empty at
    # launch). executionService only re-polls when the stop date is unset and this is
    # older than the min sync interval, reducing direct SFN calls.
    lastSfnSyncCheckDate: Optional[str] = ""
    # executionError: the specific failure message (SFN error/cause) for a non-SUCCEEDED
    #   terminal status; broadly visible. executionLog: full CloudWatch log for the run,
    #   captured on every terminal completion (success or failure) for limited roles.
    executionError: Optional[str] = ""
    executionLog: Optional[str] = ""

    @validator("triggerType")
    def validate_trigger_type(cls, v):
        if v not in TRIGGER_TYPES:
            raise ValueError(f"triggerType must be one of {TRIGGER_TYPES}")
        return v


class PipelineExecutionRecord(BaseModel, extra='ignore'):
    """PipelineExecutionsStorageTable row (one per pipeline in the workflow)."""
    pipelineExecutionId: str
    workflowExecutionId: str
    pipelineId: str
    pipelineDatabaseId: str
    endStatePipeline: str = "false"
    S3AssetPipelineBucket: Optional[str] = ""
    S3AssetPipelineBucketInputMetadataFilePrefix: Optional[str] = ""
    S3AssetPipelineBucketInputConfigurationFilePrefix: Optional[str] = ""
    S3AssetPipelineBucketOutputFilesPrefix: Optional[str] = ""
    S3AssetPipelineBucketOutputMetadataPrefix: Optional[str] = ""
    S3AssetPipelineBucketOutputPreviewPrefix: Optional[str] = ""
    S3AssetPipelineBucketOutputResultsPrefix: Optional[str] = ""
    S3AssetAuxPipelineBucket: Optional[str] = ""
    S3AssetAuxPipelineBucketPrefixTemp: Optional[str] = ""
    S3AssetAuxPipelineBucketPrefixPreview: Optional[str] = ""
    executionStartDate: Optional[str] = ""
    executionStopDate: Optional[str] = ""
    executionStatus: Optional[str] = ""
    pipelineExecutionType: str = "Lambda"
    waitForCallback: Optional[str] = "Disabled"
    pipelineResourceArn: Optional[str] = ""
    vendedRoleArn: Optional[str] = ""
    s3ReadOnlyScopes: Optional[List[str]] = []
    s3ReadWriteScopes: Optional[List[str]] = []
    credentialVendingState: Optional[str] = "notVended"
    from_pipeline_execution_id: Optional[str] = ""
    pipeline_execution_sub_arn: Optional[str] = ""
    pipeline_execution_sub_execution_arn: Optional[str] = ""


class PipelineExecutionInputFileRecord(BaseModel, extra='ignore'):
    """PipelineExecutionInputFilesStorageTable row."""
    pipelineExecutionId: str
    workflowExecutionId: str
    assetId: str
    databaseId: str
    inputAssetFileKey: str


class PipelineExecutionInputMetadataRecord(BaseModel, extra='ignore'):
    """PipelineExecutionInputMetadataStorageTable row."""
    pipelineExecutionId: str
    assetId: str
    databaseId: str
    filePath: str
    metadata: Optional[Dict[str, Any]] = {}
    sourceInputMetadataFileS3Key: Optional[str] = ""


class PipelineExecutionInputConfigurationRecord(BaseModel, extra='ignore'):
    """PipelineExecutionInputConfigurationStorageTable row."""
    pipelineExecutionId: str
    recordType: str = "configuration"
    inputConfiguration: Optional[str] = ""
    inputConfigurationTruncated: Optional[bool] = False
    inputConfigurationFileS3Key: Optional[str] = ""
    inputPortMappings: Optional[Dict[str, Any]] = {}


class PipelineExecutionOutputFileRecord(BaseModel, extra='ignore'):
    """PipelineExecutionOutputFilesStorageTable row."""
    pipelineExecutionId: str
    fileType: str
    relativeFilePath: str
    s3Bucket: Optional[str] = ""
    s3Key: Optional[str] = ""
    fileSize: Optional[int] = 0
    contentType: Optional[str] = ""
    s3VersionId: Optional[str] = ""


class PipelineExecutionOutputMetadataRecord(BaseModel, extra='ignore'):
    """PipelineExecutionOutputMetadataStorageTable row."""
    pipelineExecutionId: str
    targetFilePath: str
    metadataKey: str
    metadataValue: Optional[str] = ""
    sourceMetadataFileRelativePath: Optional[str] = ""


class PipelineExecutionOutputResultRecord(BaseModel, extra='ignore'):
    """PipelineExecutionOutputResultsStorageTable row."""
    pipelineExecutionId: str
    relativeFilePath: str
    resultsContent: Optional[str] = ""
    resultsContentTruncated: Optional[bool] = False
    s3Key: Optional[str] = ""


class PipelineExecutionLogRecord(BaseModel, extra='ignore'):
    """PipelineExecutionLogsStorageTable row."""
    pipelineExecutionId: str
    logType: str = "summary"
    resultLog: Optional[str] = ""
    resultLogTruncated: Optional[bool] = False
    errorLog: Optional[str] = ""
    errorLogTruncated: Optional[bool] = False
    logGroupArn: Optional[str] = ""
    logStreamName: Optional[str] = ""


class WorkflowExecutionInputRecord(BaseModel, extra='ignore'):
    """WorkflowExecutionInputsStorageTable row (asset-scoped GET source)."""
    workflowExecutionId: str
    assetId: str
    databaseId: str
    inputAssetFileKey: str
    executionStartDate: Optional[str] = ""
    workflowId: Optional[str] = ""
    workflowDatabaseId: Optional[str] = ""


class WorkflowExecutionConfigurationRecord(BaseModel, extra='ignore'):
    """WorkflowExecutionConfigurationStorageTable row."""
    workflowExecutionId: str
    recordType: str = "configuration"
    workflowConfiguration: Optional[str] = ""
    workflowConfigurationTruncated: Optional[bool] = False
    inputMetadata: Optional[str] = ""
    inputMetadataTruncated: Optional[bool] = False
    specifiedPipelinesSnapshot: Optional[List[Dict[str, Any]]] = []
