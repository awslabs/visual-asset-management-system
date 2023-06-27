#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime


sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"]
)

SOURCE_BUCKET_NAME = os.environ["SOURCE_BUCKET_NAME"]
DEST_BUCKET_NAME = os.environ["DEST_BUCKET_NAME"]
STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]


def lambda_handler(event, context):
    """
    OpenPipeline
    Starts StepFunctions State Machine for processing point cloud data
    SFN input parsed from input SNS Topic event data
    """

    print(f"Event: {event}")
    print(f"Context: {context}")

    # if no records in message return no files response
    if not event['Records']:
        print(f"Error: Unable to retrieve SNS Records. No files to process.")
        return {
            'statusCode': 500,
            'body': {
                'error': "Unable to retrieve SNS Records. No files to process."
            }
        }

    responses = []

    # Loop through S3 Uploads Records in SNS Message Input
    records = event['Records']
    for sns_record in records:
        print(f"SNS Record: {sns_record}")

        try:
            # Parse SNS Message to retrieve S3 Records
            s3_records = json.loads(sns_record["Sns"]["Message"])['Records']
            print(f"S3 Records: {s3_records}")
        except:
            print(f"Error: Unable to parse SNS Message. No S3 Records to process.")
            response.append({
                'statusCode': 500,
                'body': {
                    'error': "Error: unable to parse SNS Message. No S3 Records to process."
                }
            })

        for record in s3_records:
            print(f"S3 Record: {record}")
            # Extract the S3 bucket and key from the event data
            s3_source_bucket = record['s3']['bucket']['name']
            s3_source_key = record['s3']['object']['key']

            # Extract the root name and extension from the input key
            file_root, extension = os.path.splitext(s3_source_key)

            # Generate new job name
            job_name = f"VisualizerPipelineJob_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

            # StateMachine Execution Input
            sfn_input = {
                "jobName": job_name,
                "sourceBucketName": s3_source_bucket,
                "sourceObjectKey": s3_source_key,
                "sourceFileExtension": extension,
                "destinationBucketName": DEST_BUCKET_NAME,
                "destinationObjectFolderKey": f"{s3_source_key}/",
            }

            try:
                # Start the Step Functions state machine with the bucket key and name
                print(f"Starting SFN State Machine: {STATE_MACHINE_ARN}")
                print(f"SFN Input: {json.dumps(sfn_input)}")

                sfn_response = sfn.start_execution(
                    stateMachineArn=STATE_MACHINE_ARN,
                    name=job_name,
                    input=json.dumps(sfn_input)
                )

                print(f"SFN Response: {sfn_response}")

                # response datetime not JSON serializable
                sfn_response["startDate"] = sfn_response["startDate"].strftime('%m-%d-%Y %H:%M:%S')

                responses.append({
                    'statusCode': 200,
                    'body': {
                        "message": "Starting Asset Processing State Machine",
                        "execution": sfn_response
                    }
                })
            except Exception as e:
                print(f"Error: {str(e)}")
                responses.append({
                    'statusCode': 200,
                    'body': {
                        "error": f"Error: {str(e)}",
                    }
                })

    print(f"Responses: {responses}")

    # Loop through responses and see if any have errors; If so return 500 error response
    for response in responses:
        if "error" in response['body']:
            return response

    # Return success 200 response
    return {
        'statusCode': 200,
        'body': {
            "message": "Starting Asset Processing State Machine",
            "execution": sfn_response
        }
    }
