from backend.models.assets import (
    AssetPreviewLocationModel,
    GetUploadAssetWorkflowStepFunctionInput,
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
        )
    )


def test_step_function_input_from_request(sample_request):
    result = GetUploadAssetWorkflowStepFunctionInput(sample_request)
    assert result is not None
