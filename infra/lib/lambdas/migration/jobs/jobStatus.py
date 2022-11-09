#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import boto3

sm_client = boto3.client('sagemaker')


def lambda_handler(event, context):
    """
    :param event:
    :param context:
    :return:
    """
    job_name = event['JobName']
    print(f'Job Name: {job_name}')
    response = sm_client.describe_processing_job(
        ProcessingJobName=job_name
    )
    job_status = response["ProcessingJobStatus"]
    print(f'Current Job status: {job_status}')
    return {
        'ProcessingJobStatus': job_status,
        'JobName': job_name,
        'FailureReason': response.get('FailureReason', None),
        'ExitMessage': response.get('ExitMessage', None),
    }