#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution
"""
import os
import boto3
import json
import datetime
import logging
from pathlib import Path
from urllib.parse import urlparse

DEST_BUCKET_NAME = os.environ["DEST_BUCKET_NAME"]
SNS_VISUALIZER_PIPELINE_PC_TOPICARN = os.environ["SNS_VISUALIZER_PIPELINE_PC_TOPICARN"]

logger = logging.getLogger()
logger.setLevel(logging.INFO)
s3_client = boto3.client('s3')
s3 = boto3.resource('s3')
sns_client = boto3.client('sns')


def execute_visualizer_pipeline(input_path, external_task_token):
    input_bucket, input_key = input_path.replace("s3://", "").split("/", 1)

    # Create the object message to be sent (partially simulating coming from an S3 event notification (a default to the pipeline))
    message = {
        "Records": [{
            "s3": [{
                "bucket": {"name": input_bucket},
                "object": {"key": input_key}
            }],
            "sfnExternalTaskToken": external_task_token
        }]
    }

    # Publish the message to the SNS topic
    response = sns_client.publish(
        TopicArn=SNS_VISUALIZER_PIPELINE_PC_TOPICARN,
        Subject="VAMS Pipeline Notification",
        Message=json.dumps(message))


def lambda_handler(event, context):
    print(event)
    if isinstance(event['body'], str):
        data = json.loads(event['body'])
    else:
        data = event['body']

    # Get external task token if passed
    if 'TaskToken' in data:
        external_task_token = data['TaskToken']
    else:
        external_task_token = ''

    # Starts excution of visualizer pipeline by writing to SNS topic with the input files
    execute_visualizer_pipeline(data['inputPath'], external_task_token)

    return {
        'statusCode': 200,
        'body': 'Success'
    }
