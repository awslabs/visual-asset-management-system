from typing import Dict, List
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
    updateMetadataModel: UpdateMetadataModel
    executeWorkflowModel: ExecuteWorkflowModel


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
    body: UpdateAssetMetadataBody


class ExecuteWorkflowPathParameters(BaseModel):
    databaseId: str
    assetId: str
    workflowId: str


class ExecuteWorkflowStepFunctionRequest(BaseModel):
    pathParameters: ExecuteWorkflowPathParameters


class UploadAssetStepFunctionRequest(BaseModel):
    body: UploadAssetModel


class UploadAssetWorkflowStepFunctionInput(BaseModel):
    uploadAssetBody: UploadAssetStepFunctionRequest
    updateAssetMetadataBody: UpdateAssetMetadataStepFunctionRequest
    executeWorkflowBody: List[ExecuteWorkflowStepFunctionRequest]


def GetUploadAssetWorkflowStepFunctionInput(
        uploadAssetWorkflowRequestModel: UploadAssetWorkflowRequestModel
) -> UploadAssetWorkflowStepFunctionInput:
    uploadAssetBody = UploadAssetStepFunctionRequest(
            body=uploadAssetWorkflowRequestModel.uploadAssetBody
    )
    metadataPathParameters = UpdateAssetMetadataPathParameters(
                databaseId=uploadAssetWorkflowRequestModel.uploadAssetBody.databaseId,
                assetId=uploadAssetWorkflowRequestModel.uploadAssetBody.assetId,
    )
    metadataBody = UpdateAssetMetadataBody(
                version=uploadAssetWorkflowRequestModel.updateMetadataModel.version,
                metadata=uploadAssetWorkflowRequestModel.updateMetadataModel.metadata
    )
    executeWorkflowBody = [ExecuteWorkflowStepFunctionRequest(
            pathParameters=ExecuteWorkflowPathParameters(
                databaseId=uploadAssetWorkflowRequestModel.uploadAssetBody.databaseId,
                assetId=uploadAssetWorkflowRequestModel.uploadAssetBody.assetId,
                workflowId=x
            )
    ) for x in uploadAssetWorkflowRequestModel.executeWorkflowModel.workflowIds]
    return UploadAssetWorkflowStepFunctionInput(
        uploadAssetBody=uploadAssetBody,
        updateAssetMetadataBody=UpdateAssetMetadataStepFunctionRequest(
            pathParameters=metadataPathParameters,
            body=metadataBody
        ),
        executeWorkflowBody=executeWorkflowBody
    )
