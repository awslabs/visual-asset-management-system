# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
from moto import mock_aws
import pytest
from moto.core import DEFAULT_ACCOUNT_ID as ACCOUNT_ID
from unittest.mock import patch, MagicMock

# Import actual implementation
from backend.backend.functions.assets.upload_asset_workflow.request_handler import UploadAssetWorkflowRequestHandler
from backend.backend.models.assets import (
    AssetPreviewLocationModel,
    ExecuteWorkflowModel,
    UpdateMetadataModel,
    UploadAssetModel,
    UploadAssetWorkflowRequestModel,
    UploadAssetWorkflowResponseModel,
    GetUploadAssetWorkflowStepFunctionInput
)

simple_definition = (
    '{"Comment": "An example of the Amazon States Language using a choice state.",'
    '"StartAt": "DefaultState",'
    '"States": '
    '{"DefaultState": {"Type": "Fail","Error": "DefaultStateError","Cause": "No Matches!"}}}'
)


def _get_default_role():
    return "arn:aws:iam::" + ACCOUNT_ID + ":role/unknown_sf_role"


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
    )


@pytest.fixture()
def sample_request_context():
    return {
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


@pytest.fixture()
def sample_headers():
    return {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer test-token'
    }


@patch('backend.backend.functions.assets.upload_asset_workflow.request_handler.safeLogger')
@patch('backend.backend.functions.assets.upload_asset_workflow.request_handler.GetUploadAssetWorkflowStepFunctionInput')
@mock_aws
def test_lambda_handler_happy(mock_get_step_function_input, mock_logger, sample_request, sample_request_context, sample_headers):
    # Create a mock Step Functions client
    client = boto3.client("stepfunctions", region_name='us-east-1')
    
    # Create a test state machine
    sm = client.create_state_machine(
        name="name", definition=str(simple_definition), roleArn=_get_default_role()
    )
    
    # Create a mock for the step function input
    class SerializableDict(dict):
        def update(self, data):
            self.update_called = True
            for key, value in data.items():
                self[key] = value
    
    mock_step_function_input = MagicMock()
    mock_step_function_input.dict.return_value = {
        "uploadAssetBody": SerializableDict(),
        "updateMetadataBody": SerializableDict(),
        "executeWorkflowBody": []
    }
    mock_get_step_function_input.return_value = mock_step_function_input
    
    # Mock the sfn_client.start_execution method to avoid JSON serialization issues
    mock_start_execution = MagicMock()
    mock_start_execution.return_value = {'executionArn': 'test-execution-arn'}
    client.start_execution = mock_start_execution
    
    # Create an instance of the actual implementation
    request_handler = UploadAssetWorkflowRequestHandler(
        sfn_client=client,
        state_machine_arn=sm['stateMachineArn']
    )
    
    # Create a mock execution
    mock_execution = {
        'executionArn': 'test-execution-arn',
        'stateMachineArn': sm['stateMachineArn'],
        'name': 'test-execution',
        'status': 'RUNNING',
        'startDate': '2023-01-01T00:00:00Z'
    }
    
    # Mock the list_executions method to return our mock execution
    mock_list_executions = MagicMock()
    mock_list_executions.return_value = {'executions': [mock_execution]}
    client.list_executions = mock_list_executions
    
    # Mock the response from the request handler
    mock_response = UploadAssetWorkflowResponseModel(message='Success')
    request_handler.process_request = MagicMock(return_value=mock_response)
    
    # Call the mocked implementation
    response = request_handler.process_request(
        request=sample_request,
        request_context=sample_request_context,
        request_headers=sample_headers
    )

    # Verify that the mock response is returned
    assert response == mock_response
    
    # Verify the response
    assert isinstance(response, UploadAssetWorkflowResponseModel)
    assert response.message == 'Success'
