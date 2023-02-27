# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import patch
from backend.functions.assets.upload_asset_workflow.request_handler import (
    UploadAssetWorkflowRequestHandler
)
from backend.models.assets import (
    AssetPreviewLocationModel,
    ExecuteWorkflowModel,
    UpdateMetadataModel,
    UploadAssetModel,
    UploadAssetWorkflowRequestModel,
    UploadAssetWorkflowResponseModel
)
from moto import mock_stepfunctions
import pytest


@pytest.fixture()
def sample_request():
    event = {'body': {}}
    requst = json.dumps(UploadAssetWorkflowRequestModel(
        uploadAssetBody=UploadAssetModel(
            databaseId='1',
            assetId='test',
            assetName="testname",
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
        ).dict()
    )
    event['body'] = requst
    return event


def mock_process_request(self, request):
    return UploadAssetWorkflowResponseModel(message='Success')


def mock_process_request_returns_error(self, request):
    raise Exception('StepFunction')


@patch.object(UploadAssetWorkflowRequestHandler, "process_request", mock_process_request)
@mock_stepfunctions
def test_request_handler_success(sample_request, monkeypatch):
    monkeypatch.setenv("UPLOAD_WORKFLOW_ARN", "TestArn")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from backend.functions.assets.upload_asset_workflow.lambda_handler import lambda_handler
    response = lambda_handler(sample_request, None)
    assert response['statusCode'] == 200


@patch.object(UploadAssetWorkflowRequestHandler, "process_request", mock_process_request_returns_error)
@mock_stepfunctions
def test_request_handler_500(sample_request, monkeypatch):
    monkeypatch.setenv("UPLOAD_WORKFLOW_ARN", "TestArn")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from backend.functions.assets.upload_asset_workflow.lambda_handler import lambda_handler
    response = lambda_handler(sample_request, None)
    assert response['statusCode'] == 500


@mock_stepfunctions
def test_request_handler_validation_error(monkeypatch):
    monkeypatch.setenv("UPLOAD_WORKFLOW_ARN", "TestArn")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from backend.functions.assets.upload_asset_workflow.lambda_handler import lambda_handler
    response = lambda_handler({'body': {}}, None)
    assert response['statusCode'] == 422


@mock_stepfunctions
def test_request_handler_exception(monkeypatch, sample_request):
    monkeypatch.setenv("UPLOAD_WORKFLOW_ARN", "TestArn")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    from backend.functions.assets.upload_asset_workflow.lambda_handler import lambda_handler
    response = lambda_handler(sample_request, None)
    assert response['statusCode'] == 500
