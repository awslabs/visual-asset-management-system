#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0


from backend.handlers.auth import get_database_set, request_to_claims
import json
import boto3
import os
from datetime import datetime

"""
given a assetId, databaseId determine if a user has access to mutate s3
objects for that asset

POST /auth/scopeds3access
{
    "assetId": "...",
    "databaseId": "...",
}
"""

ROLE_ARN = os.environ['ROLE_ARN']


def lambda_handler(event, context):
    print(event)
    response = {
        'statusCode': 200,
        'body': '',
        'headers': {
            'Content-Type': 'application/json',
                'Access-Control-Allow-Credentials': True,
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type',
                'Access-Control-Allow-Methods': 'OPTIONS,POST,GET'
        }
    }

    try:

        if "body" not in event:
            response['body'] = json.dumps({
                "message": "No Body",
            })
            response['statusCode'] = 400
            return response

        body = json.loads(event["body"])
        assetId = body.get("assetId", None)
        databaseId = body.get("databaseId", None)

        if assetId is None:
            response['body'] = json.dumps({
                "message": "No Asset Id",
            })
            response['statusCode'] = 400
            return response

        if databaseId is None:
            response['body'] = json.dumps({
                "message": "No Database Id",
            })
            response['statusCode'] = 400
            return response

        claims_and_roles = request_to_claims(event)
        is_super_admin = "super-admin" in claims_and_roles.get("roles", [])
        tokens = claims_and_roles['tokens']

        databases = get_database_set(tokens)
        if databaseId not in databases and not is_super_admin:
            response['body'] = json.dumps({
                "message": "Not Authorized",
            })
            response['statusCode'] = 403
            return response

        timeout = 900

        # generate a policy scoped to the assetId as the s3 key prefix
        # to be passed to assume_role
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Sid": "Stmt1",
                "Effect": "Allow",
                # set of actions needed to do get object and multipart upload
                "Action": [
                    "s3:PutObject",
                    "s3:GetObject*",
                    "s3:GetBucket*",
                    "s3:List*",
                    "s3:CreateMultipartUpload",
                    "s3:AbortMultipartUpload",
                    "s3:ListMultipartUploadParts",
                    "s3:DeleteObject",
                ],
                "Resource": [
                    "arn:aws:s3:::" +
                    os.environ['S3_BUCKET'] + "/" + assetId + "/*",
                    "arn:aws:s3:::" +
                    os.environ['S3_BUCKET'] + "/previews/" + assetId + "/*"
                ]
            }, {
                "Sid": "Stmt2",
                "Effect": "Allow",
                # set of actions needed to do get object and multipart upload
                "Action": [
                    "s3:ListBucket",
                ],
                "Resource": [
                    "arn:aws:s3:::" + os.environ['S3_BUCKET']
                ]
            }]
        }

        # use sts to create a session for timeout seconds
        sts_client = boto3.client('sts')
        assumed_role_object = sts_client.assume_role(
            RoleArn=ROLE_ARN,
            RoleSessionName="presign",
            DurationSeconds=timeout,
            Policy=json.dumps(policy),
        )

        print("assumed role object", assumed_role_object)

        def datetime_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError("Type not serializable")

        assumed_role_object['bucket'] = os.environ['S3_BUCKET']
        assumed_role_object['region'] = os.environ['AWS_REGION']

        # return the credentials
        response['body'] = json.dumps(assumed_role_object,
                                      default=datetime_serializer)

        return response

    except Exception as e:
        response['statusCode'] = 500
        print("Error!", e.__class__, "occurred.")
        try:
            print(e)
            response['body'] = json.dumps({"message": str(e)})
        except Exception:
            print("Can't Read Error")
            response['body'] = json.dumps({
                "message": "An unexpected error occurred "
                           "while executing the request"
            })
        return response
