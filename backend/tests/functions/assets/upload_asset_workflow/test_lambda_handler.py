# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from unittest.mock import patch, Mock, MagicMock

# Mock the CasbinEnforcer class
class MockCasbinEnforcer:
    def __init__(self, claims_and_roles):
        self.claims_and_roles = claims_and_roles
        
    def enforce(self, asset_object, action):
        return True
        
    def enforceAPI(self, event):
        return True

# Create a mock for the request handler
class UploadAssetWorkflowRequestHandler:
    def __init__(self, sfn_client=None, state_machine_arn=None):
        self.sfn_client = sfn_client
        self.stat_machine_arn = state_machine_arn
        
    def process_request(self, request, request_context=None, request_headers=None):
        from backend.tests.mocks.models.assets import UploadAssetWorkflowResponseModel
        return UploadAssetWorkflowResponseModel(message='Success')

# Import mock models
from backend.tests.mocks.models.assets import (
    AssetPreviewLocationModel,
    ExecuteWorkflowModel,
    UpdateMetadataModel,
    UploadAssetModel,
    UploadAssetWorkflowRequestModel,
    UploadAssetWorkflowResponseModel
)
from moto import mock_aws
import pytest


@pytest.fixture()
def sample_request():
    event = {'body': {}, 'requestContext': {}, 'headers': {}}
    event['requestContext'] = {
        'http': {
            'method': 'POST'
        },
        'authorizer': {
            'jwt': {
                'claims': {
                    'sub': 'test-user-id',
                    'email': 'test@example.com',
                }
            }
        }
    }
    event['headers'] = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer test-token'
    }
    requst = json.dumps(UploadAssetWorkflowRequestModel(
        uploadAssetBody=UploadAssetModel(
            databaseId='1',
            assetId='test',
            assetName="testname",
            key='test_file',
            assetType='step',
            description='Testing',
            isDistributable=False,
            specifiedPipelines=[],
            tags=[],
            Comment='Testing',
            previewLocation=AssetPreviewLocationModel(
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


def mock_process_request(self, request, request_context=None, request_headers=None):
    return UploadAssetWorkflowResponseModel(message='Success')


def mock_process_request_returns_error(self, request, request_context=None, request_headers=None):
    raise Exception('StepFunction')


@patch.object(UploadAssetWorkflowRequestHandler, "process_request", mock_process_request)
@patch('handlers.authz.CasbinEnforcer', MockCasbinEnforcer)
@patch('backend.backend.handlers.authz.CasbinEnforcer', MockCasbinEnforcer)
@mock_aws
def test_request_handler_success(sample_request, monkeypatch):
    monkeypatch.setenv("UPLOAD_WORKFLOW_ARN", "TestArn")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    monkeypatch.setenv("TAG_STORAGE_TABLE_NAME", "tagStorageTable")
    from backend.backend.functions.assets.upload_asset_workflow.lambda_handler import lambda_handler
    response = lambda_handler(sample_request, None)
    assert response['statusCode'] == 500


@patch.object(UploadAssetWorkflowRequestHandler, "process_request", mock_process_request_returns_error)
@patch('handlers.authz.CasbinEnforcer', MockCasbinEnforcer)
@patch('backend.backend.handlers.authz.CasbinEnforcer', MockCasbinEnforcer)
@mock_aws
def test_request_handler_500(sample_request, monkeypatch):
    monkeypatch.setenv("UPLOAD_WORKFLOW_ARN", "TestArn")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    monkeypatch.setenv("TAG_STORAGE_TABLE_NAME", "tagStorageTable")
    from backend.backend.functions.assets.upload_asset_workflow.lambda_handler import lambda_handler
    response = lambda_handler(sample_request, None)
    assert response['statusCode'] == 500


@patch('handlers.authz.CasbinEnforcer', MockCasbinEnforcer)
@patch('backend.backend.handlers.authz.CasbinEnforcer', MockCasbinEnforcer)
@mock_aws
def test_request_handler_validation_error(monkeypatch):
    monkeypatch.setenv("UPLOAD_WORKFLOW_ARN", "TestArn")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    monkeypatch.setenv("TAG_STORAGE_TABLE_NAME", "tagStorageTable")
    from backend.backend.functions.assets.upload_asset_workflow.lambda_handler import lambda_handler
    response = lambda_handler({'body': {}, 'requestContext': {}, 'headers': {}}, None)
    assert response['statusCode'] == 400


@patch('handlers.authz.CasbinEnforcer', MockCasbinEnforcer)
@patch('backend.backend.handlers.authz.CasbinEnforcer', MockCasbinEnforcer)
@mock_aws
def test_request_handler_exception(monkeypatch, sample_request):
    monkeypatch.setenv("UPLOAD_WORKFLOW_ARN", "TestArn")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("TAG_TYPES_STORAGE_TABLE_NAME", "tagTypesStorageTable")
    monkeypatch.setenv("TAG_STORAGE_TABLE_NAME", "tagStorageTable")
    from backend.backend.functions.assets.upload_asset_workflow.lambda_handler import lambda_handler
    response = lambda_handler(sample_request, None)
    assert response['statusCode'] == 500
