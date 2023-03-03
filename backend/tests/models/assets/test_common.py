# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from backend.models.assets import (
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
            bucket='test_bucket',
            key='test_file',
            assetType='step',
            description='Testing',
            isDistributable=False,
            specifiedPipelines=[],
            Comment='Testing',
            previewLocation=AssetPreviewLocationModel(
                Bucket='test_bucket',
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
            bucket='test_bucket',
            key='test_file',
            assetType='step',
            description='Testing',
            isDistributable=False,
            specifiedPipelines=[],
            Comment='Testing',
            previewLocation=AssetPreviewLocationModel(
                Bucket='test_bucket',
                Key='test_preview_key'
            )
        )
    )


def test_step_function_input_from_request(sample_request):
    result = GetUploadAssetWorkflowStepFunctionInput(sample_request)
    assert result.copyObjectBody is not None
    assert result.updateMetadataBody is not None
    assert result.executeWorkflowBody is not None
    assert result.uploadAssetBody is not None


def test_step_function_input_required(only_required):
    result = GetUploadAssetWorkflowStepFunctionInput(only_required)
    assert result.updateMetadataBody is None
    assert result.executeWorkflowBody is None
    assert result.uploadAssetBody is not None
