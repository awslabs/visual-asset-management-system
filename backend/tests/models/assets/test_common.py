# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Import actual implementation models instead of mocks
from backend.backend.models.assets import (
    AssetPreviewLocationModel,
    ExecuteWorkflowModel,
    GetUploadAssetWorkflowStepFunctionInput,
    UpdateMetadataModel,
    UploadAssetModel,
    UploadAssetWorkflowRequestModel
)
import pytest


@pytest.fixture()
def sample_request():
    return UploadAssetWorkflowRequestModel(
        uploadAssetBody=UploadAssetModel(
            databaseId='1',
            assetId='test',
            assetName='test',
            key='test_file',
            assetType='step',
            description='Testing',
            isDistributable=False,
            specifiedPipelines=[],
            tags=[],  # Added tags field which is required in the actual model
            Comment='Testing',
            previewLocation=AssetPreviewLocationModel(
                Key='test_preview_key'
            )
        ),
        copyFrom="test/src",
        updateMetadataBody=UpdateMetadataModel(
            version="1",
            metadata={
                'test': 'test'
            }
        ),
        executeWorkflowBody=ExecuteWorkflowModel(
            workflowIds=[
                'test1',
                'test2',
                'test3'
            ]
        )
    )


@pytest.fixture()
def only_required():
    return UploadAssetWorkflowRequestModel(
        uploadAssetBody=UploadAssetModel(
            databaseId='1',
            assetId='test',
            assetName='test',
            key='test_file',
            assetType='step',
            description='Testing',
            isDistributable=False,
            specifiedPipelines=[],
            tags=[],  # Added tags field which is required in the actual model
            Comment='Testing',
            previewLocation=AssetPreviewLocationModel(
                Key='test_preview_key'
            )
        )
    )


@pytest.fixture()
def without_preview():
    return UploadAssetWorkflowRequestModel(
        uploadAssetBody=UploadAssetModel(
            databaseId='1',
            assetId='test',
            assetName='test',
            key='test_file',
            assetType='step',
            description='Testing',
            isDistributable=False,
            specifiedPipelines=[],
            tags=[],  # Added tags field which is required in the actual model
            Comment='Testing',
        )
    )


def test_step_function_input_from_request(sample_request):
    # Test the actual implementation of GetUploadAssetWorkflowStepFunctionInput
    result = GetUploadAssetWorkflowStepFunctionInput(sample_request)
    
    # Verify the result contains the expected data
    assert result.copyObjectBody is not None
    assert result.copyObjectBody.copySource == "test/src"
    
    assert result.updateMetadataBody is not None
    assert result.updateMetadataBody.pathParameters.databaseId == '1'
    assert result.updateMetadataBody.pathParameters.assetId == 'test'
    
    assert result.executeWorkflowBody is not None
    assert len(result.executeWorkflowBody) == 3
    assert result.executeWorkflowBody[0].pathParameters.workflowId == 'test1'
    
    assert result.uploadAssetBody is not None
    assert result.uploadAssetBody.body.databaseId == '1'
    assert result.uploadAssetBody.body.assetId == 'test'


def test_step_function_input_required(only_required):
    # Test with only required fields
    result = GetUploadAssetWorkflowStepFunctionInput(only_required)
    
    # Verify optional fields are None
    assert result.copyObjectBody is None
    assert result.updateMetadataBody is None
    assert result.executeWorkflowBody is None
    
    # Verify required fields are present
    assert result.uploadAssetBody is not None
    assert result.uploadAssetBody.body.databaseId == '1'
    assert result.uploadAssetBody.body.assetId == 'test'


def test_without_preview(without_preview):
    # Test without preview location
    result = GetUploadAssetWorkflowStepFunctionInput(without_preview)
    
    # Verify optional fields are None
    assert result.updateMetadataBody is None
    assert result.executeWorkflowBody is None
    
    # Verify required fields are present
    assert result.uploadAssetBody is not None
    assert result.uploadAssetBody.body.databaseId == '1'
    assert result.uploadAssetBody.body.assetId == 'test'
    
    # Verify preview location is None
    assert result.uploadAssetBody.body.previewLocation is None
