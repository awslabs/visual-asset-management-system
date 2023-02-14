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


class UploadAssetWorkflowRequestModel(BaseModel):
    uploadAssetBody: UploadAssetModel


class UploadAssetWorkflowResponseModel(BaseModel):
    message: str


class UploadAssetWorkflowRequest(APIGatewayProxyEventV2Model):
    body: Json[UploadAssetWorkflowRequestModel]  # type: ignore[assignment]


class UploadAssetStepFunctionRequest(BaseModel):
    body: UploadAssetModel


class UploadAssetWorkflowStepFunctionInput(BaseModel):
    uploadAssetBody: UploadAssetStepFunctionRequest


def GetUploadAssetWorkflowStepFunctionInput(
        uploadAssetWorkflowRequestModel: UploadAssetWorkflowRequestModel
) -> UploadAssetWorkflowStepFunctionInput:
    return UploadAssetWorkflowStepFunctionInput(
        uploadAssetBody=UploadAssetStepFunctionRequest(
            body=uploadAssetWorkflowRequestModel.uploadAssetBody
        )
    )
