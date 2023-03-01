#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Purpose
Shows how to implement an AWS Lambda function that handles input from direct
invocation.
"""

# snippet-start:[python.example_code.lambda.handler.increment]

import json
import logging
import boto3
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger()
logger.setLevel(logging.INFO)
s3_client = boto3.client('s3')
s3 = boto3.resource('s3')

def remove_prefix(text, prefix):
    return text[text.startswith(prefix) and len(prefix):]
    
def write_input_output(input_path, output_path):
    input_bucket, input_key = input_path.replace("s3://", "").split("/", 1)
    output_bucket, output_key = output_path.replace("s3://", "").split("/", 1)

    print(input_bucket)
    print(input_key)
    s3_input_bucket = s3.Bucket(input_bucket)
    if input_key.endswith("/"):
        for obj in s3_input_bucket.objects.filter(Prefix=input_key):
            copy_source = {
                'Bucket': input_bucket,
                'Key': obj.key
            }         
    
            destination_key = remove_prefix(obj.key, input_key)
            if len(destination_key) > 0:
                destination_key = output_key + destination_key
                print(f"Copying object to {output_bucket}/{destination_key}")
                s3_client.copy(copy_source, output_bucket, destination_key)
    else:
        if "/" in input_key:
            destination_key = output_key + input_key.split("/")[-1]
        else:
            destination_key = output_key + input_key

        copy_source = {
            'Bucket': input_bucket,
            'Key': input_key
        }
        print(f"Copying object to {output_bucket}/{destination_key}")
        s3_client.copy(copy_source, output_bucket, destination_key)

def lambda_handler(event, context):
    """
    Example of a NoOp pipeline
    Uploads input file to output
    """
    print(event)
    if isinstance(event['body'], str):
        data = json.loads(event['body'])
    else:
        data = event['body']

    write_input_output(data['inputPath'], data['outputPath'])
    return {
        'statusCode': 200, 
        'body': 'Success'
    }