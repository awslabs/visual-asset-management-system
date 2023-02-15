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
        updateMetadataModel=UpdateMetadataModel(
            version="1",
            metadata={
                'test': 'test'
            }
        ),
        executeWorkflowModel=ExecuteWorkflowModel(
            workflowIds=[
                'test1',
                'test2',
                'test3'
            ]
        )
    )


def test_step_function_input_from_request(sample_request):
    print("Testing")
    result = GetUploadAssetWorkflowStepFunctionInput(sample_request)
    assert result is not None
