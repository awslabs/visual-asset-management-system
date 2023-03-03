# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

from backend.functions.assets.upload_asset_workflow.request_handler import (
    UploadAssetWorkflowRequestHandler
)
from backend.models.assets import (
    AssetPreviewLocationModel,
    ExecuteWorkflowModel,
    UpdateMetadataModel,
    UploadAssetModel,
    UploadAssetWorkflowRequestModel
)
import boto3
from moto import mock_stepfunctions
import pytest
from moto.core import DEFAULT_ACCOUNT_ID as ACCOUNT_ID

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
    )


@mock_stepfunctions
def test_lambda_handler_happy(sample_request):
    client = boto3.client("stepfunctions", region_name='us-east-1')
    sm = client.create_state_machine(
        name="name", definition=str(simple_definition), roleArn=_get_default_role()
    )
    request_handler = UploadAssetWorkflowRequestHandler(
        sfn_client=client,
        state_machine_arn=sm['stateMachineArn']
    )
    response = request_handler.process_request(sample_request)

    executions = client.list_executions(stateMachineArn=sm['stateMachineArn'])
    print(executions)
    assert len(executions['executions']) == 1
    assert response.message == 'Success'
