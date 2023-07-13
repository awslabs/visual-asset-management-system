# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Dict, List, Optional
from pydantic import BaseModel, Json
from aws_lambda_powertools.utilities.parser.models import (
    APIGatewayProxyEventV2Model
)


class AssetPreviewLocationModel(BaseModel):
    Bucket: str
    Key: str


class UploadAssetModel(BaseModel):
    databaseId: str
    assetId: str
    assetName: str
    bucket: str
    key: str
    assetType: str
    description: str
    isDistributable: bool
    Comment: str
    previewLocation: AssetPreviewLocationModel
    specifiedPipelines: list[str]


class UpdateMetadataModel(BaseModel):
    version: str
    metadata: Dict[str, str]


class ExecuteWorkflowModel(BaseModel):
    workflowIds: List[str]


class UploadAssetWorkflowRequestModel(BaseModel):
    uploadAssetBody: UploadAssetModel
    copyFrom: Optional[str]
    updateMetadataBody: Optional[UpdateMetadataModel]
    executeWorkflowBody: Optional[ExecuteWorkflowModel]


class UploadAssetWorkflowResponseModel(BaseModel):
    message: str


class UploadAssetWorkflowRequest(APIGatewayProxyEventV2Model):
    body: Json[UploadAssetWorkflowRequestModel]  # type: ignore[assignment]


class UpdateAssetMetadataPathParameters(BaseModel):
    databaseId: str
    assetId: str


class UpdateAssetMetadataBody(BaseModel):
    version: str
    metadata: dict[str, str]


class UpdateAssetMetadataStepFunctionRequest(BaseModel):
    pathParameters: UpdateAssetMetadataPathParameters
    body: str


class ExecuteWorkflowPathParameters(BaseModel):
    databaseId: str
    assetId: str
    workflowId: str


class ExecuteWorkflowStepFunctionRequest(BaseModel):
    pathParameters: ExecuteWorkflowPathParameters


class UploadAssetStepFunctionRequest(BaseModel):
    body: UploadAssetModel


class CopyObjectBody(BaseModel):
    bucket: str
    key: str
    copySource: str


class UploadAssetWorkflowStepFunctionInput(BaseModel):
    uploadAssetBody: UploadAssetStepFunctionRequest
    copyObjectBody: Optional[CopyObjectBody]
    updateMetadataBody: Optional[UpdateAssetMetadataStepFunctionRequest]
    executeWorkflowBody: Optional[List[ExecuteWorkflowStepFunctionRequest]]


def GetUploadAssetWorkflowStepFunctionInput(
        uploadAssetWorkflowRequestModel: UploadAssetWorkflowRequestModel
) -> UploadAssetWorkflowStepFunctionInput:
    uploadAssetBody = UploadAssetStepFunctionRequest(
            body=uploadAssetWorkflowRequestModel.uploadAssetBody
    )

    copyObjectBody = None
    if uploadAssetWorkflowRequestModel.copyFrom is not None:
        copyObjectBody = CopyObjectBody(
            bucket=uploadAssetWorkflowRequestModel.uploadAssetBody.bucket,
            key=uploadAssetWorkflowRequestModel.uploadAssetBody.key,
            copySource=uploadAssetWorkflowRequestModel.copyFrom
        )

    updateMetadataBody = None
    if uploadAssetWorkflowRequestModel.updateMetadataBody is not None:
        metadataPathParameters = UpdateAssetMetadataPathParameters(
                    databaseId=uploadAssetWorkflowRequestModel.uploadAssetBody.databaseId,
                    assetId=uploadAssetWorkflowRequestModel.uploadAssetBody.assetId,
        )
        metadataBody = UpdateAssetMetadataBody(
                    version=uploadAssetWorkflowRequestModel.updateMetadataBody.version,
                    metadata=uploadAssetWorkflowRequestModel.updateMetadataBody.metadata
        )
        updateMetadataBody = UpdateAssetMetadataStepFunctionRequest(
            pathParameters=metadataPathParameters,
            body=json.dumps(metadataBody.dict())
        )

    executeWorkflowBody = None
    if uploadAssetWorkflowRequestModel.executeWorkflowBody is not None:
        executeWorkflowBody = [ExecuteWorkflowStepFunctionRequest(
                pathParameters=ExecuteWorkflowPathParameters(
                    databaseId=uploadAssetWorkflowRequestModel.uploadAssetBody.databaseId,
                    assetId=uploadAssetWorkflowRequestModel.uploadAssetBody.assetId,
                    workflowId=x
                )
        ) for x in uploadAssetWorkflowRequestModel.executeWorkflowBody.workflowIds]
    return UploadAssetWorkflowStepFunctionInput(
        uploadAssetBody=uploadAssetBody,
        copyObjectBody=copyObjectBody,
        updateMetadataBody=updateMetadataBody,
        executeWorkflowBody=executeWorkflowBody
    )
