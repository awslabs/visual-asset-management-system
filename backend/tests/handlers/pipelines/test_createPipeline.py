#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0


from unittest.mock import Mock, call

from backend.handlers.pipelines.createPipeline import CreatePipeline

body = {
    "waitForCallback": "Disabled",
    "pipelineType": "Lambda",
    "databaseId": "default",
    "description": "demo",
    "pipelineId": "demo",
    "assetType": ".stl",
    "outputType": ".stl"
}


env = {
    "PIPELINE_STORAGE_TABLE_NAME": "pipeline_storage",
    "ENABLE_PIPELINE_FUNCTION_NAME": "enable_pipeline",
    "ENABLE_PIPELINE_FUNCTION_ARN":
        "0000000000000000000000000000000000000:function:enable_pipeline",
    'S3_BUCKET': 'pipeline-bucket',
    'ASSET_BUCKET_ARN': 'asset-bucket-arn',
    'ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE': 'role-to-attach-to-lambda-pipeline',
    'LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET':
        'lambda-pipeline-sample-function-bucket',
    'LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY':
        'lambda-pipeline-sample-function-key',
}


def test_create_pipeline():
    dynamodb = Mock()
    dynamodb.put_item = Mock()
    cloudformation = Mock()
    lambda_client = Mock()
    lambda_client.create_function = Mock()

    create_pipeline = CreatePipeline(
        dynamodb=dynamodb,
        cloudformation=cloudformation,
        lambda_client=lambda_client,
        env=env
    )

    result = create_pipeline.createLambdaPipeline(body)
    print(result)
    assert lambda_client.create_function.call_count == 1
    assert lambda_client.create_function.call_args == call(
        FunctionName='demo',
        Role='role-to-attach-to-lambda-pipeline', PackageType='Zip',
        Code={
            'S3Bucket': 'lambda-pipeline-sample-function-bucket',
            'S3Key': 'lambda-pipeline-sample-function-key',
        },
        Handler='lambda_function.lambda_handler',
        Runtime='python3.10')


def test_upload_pipeline():
    dynamodb = Mock()
    table = Mock()
    dynamodb.Table = Mock(return_value=table)
    table.put_item = Mock()
    cloudformation = Mock()
    lambda_client = Mock()
    lambda_client.create_function = Mock()

    create_pipeline = CreatePipeline(
        dynamodb=dynamodb,
        cloudformation=cloudformation,
        lambda_client=lambda_client,
        env=env
    )
    date_created = "June 14 2023 - 19:53:45"
    create_pipeline._now = Mock(return_value=date_created)
    create_pipeline.createLambdaPipeline = Mock()

    create_pipeline.upload_Pipeline(body)

    assert table.put_item.call_count == 1

    item_arg = {
        'dateCreated': '"{}"'.format(date_created),
        'userProvidedResource': '{"isProvided": false, "resourceId": ""}',
        'enabled': False
    }
    item_arg.update(body)

    assert table.put_item.call_args == call(
        Item=item_arg,
        ConditionExpression='attribute_not_exists(databaseId) and attribute_not_exists(pipelineId)'
    )
    assert create_pipeline.createLambdaPipeline.call_count == 1
    assert create_pipeline.createLambdaPipeline.call_args == call(body)