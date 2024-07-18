#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

from handlers.auth import request_to_claims
import json
import boto3
import os
from datetime import datetime
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from common.dynamodb import get_asset_object_from_id
from common.validators import validate
from customLogging.logger import safeLogger

claims_and_roles = {}
logger = safeLogger(service="ScopedS3Access")


"""
given a assetId, databaseId determine if a user has access to mutate s3
objects for that asset

POST /auth/scopeds3access
{
    "assetId": "...",
    "databaseId": "...",
}
"""

#Set boto environment variable to use regional STS endpoint (https://stackoverflow.com/questions/71255594/request-times-out-when-try-to-assume-a-role-with-aws-sts-from-a-private-subnet-u)
#AWS_STS_REGIONAL_ENDPOINTS='regional'
os.environ["AWS_STS_REGIONAL_ENDPOINTS"] = 'regional'

ROLE_ARN = os.environ['ROLE_ARN']
AWS_PARTITION = os.environ['AWS_PARTITION']
KMS_KEY_ARN = os.environ['KMS_KEY_ARN']

cognito_auth = os.environ["COGNITO_AUTH"]
identity_pool_id = os.environ["IDENTITY_POOL_ID"]
cred_timeout = int(os.environ['CRED_TOKEN_TIMEOUT_SECONDS'])

def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    logger.info(event)

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
        
        logger.info("Validating parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': databaseId,
                'validator': 'ID'
            },
            'assetId': {
                'value': assetId,
                'validator': 'ID'
            },
        })
        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        claims_and_roles = request_to_claims(event)

        asset = get_asset_object_from_id(assetId)
        if asset:
            allowed = False
            # Add Casbin Enforcer to check if the current user has permissions to POST or PUT the asset:
            # If this is the first time the asset is being accessed, then it would not be present in the database
            # So, replacing it with the requested content
            if not asset.get("assetId"):
                asset = {
                    "object__type": "asset",
                    "assetId": assetId,
                    "databaseId": databaseId
                }
            else:
                asset.update({
                    "object__type": "asset"
                })
            for user_name in claims_and_roles["tokens"]:
                casbin_enforcer = CasbinEnforcer(user_name)
                if casbin_enforcer.enforce(f"user::{user_name}", asset, "POST") or casbin_enforcer.enforce(f"user::{user_name}", asset, "PUT"):
                    allowed = True
                    break

            if allowed:
                timeout = cred_timeout

                # generate a policy scoped to the assetId as the s3 key prefix
                # to be passed to assume_role
                policy = {
                    "Version": "2012-10-17",
                    "Statement": [{
                        "Sid": "Stmt1",
                        "Effect": "Allow",
                        # set of actions needed to do put object and multipart upload
                        "Action": [
                            "s3:PutObject",
                            "s3:List*",
                            "s3:CreateMultipartUpload",
                            "s3:AbortMultipartUpload",
                            "s3:ListMultipartUploadParts",
                        ],
                        "Resource": [
                            "arn:"+AWS_PARTITION+":s3:::" +
                            os.environ['S3_BUCKET'] + "/" + assetId + "/*",
                            "arn:"+AWS_PARTITION+":s3:::" +
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
                            "arn:"+AWS_PARTITION+":s3:::" + os.environ['S3_BUCKET']
                        ]
                    }]
                }

                #If we have a KMS ARN, add statement policy to allow KMS actions
                if KMS_KEY_ARN != "":
                    policy["Statement"].append({
                        "Sid": "Stmt3",
                        "Effect": "Allow",
                        "Action": [
                            "kms:Decrypt",
                            "kms:DescribeKey",
                            "kms:Encrypt",
                            "kms:GenerateDataKey*",
                            "kms:ReEncrypt*",
                            "kms:ListKeys",
                            "kms:CreateGrant",
                            "kms:ListAliases",
                        ],
                        "Resource": [KMS_KEY_ARN]
                    })

                # use sts to create a session for timeout seconds
                sts_client = boto3.client('sts')
                client = boto3.client('cognito-identity')

                cognito_token=event["headers"]["authorization"].split(" ")[1]
                print("cognito_token", cognito_token, "cognito_auth", cognito_auth, "identity_pool_id", identity_pool_id)
                login={cognito_auth:cognito_token}
                print("login", login)
                
                account_id = sts_client.get_caller_identity()['Account']
                print("account_id", account_id)

                cognitoId = client.get_id(
                    AccountId=account_id,
                    IdentityPoolId=identity_pool_id,
                    Logins=login,
                )
                print("cognitoId", cognitoId["IdentityId"])

                cognito_open_id = client.get_open_id_token(
                    IdentityId=cognitoId["IdentityId"],
                    Logins=login
                )

                print("cognito_open_id", cognito_open_id)
                print(f"Role: {ROLE_ARN}")
                assumed_role_object = sts_client.assume_role_with_web_identity(
                    RoleArn=ROLE_ARN,
                    RoleSessionName="presign",
                    WebIdentityToken=cognito_open_id["Token"],
                    DurationSeconds=timeout,
                    Policy=json.dumps(policy),

                )

                """
                assumed_role_object = sts_client.assume_role(
                    RoleArn=ROLE_ARN,
                    RoleSessionName="presign",
                    DurationSeconds=timeout,
                    Policy=json.dumps(policy),
                )
                """
                logger.info("assumed role object")
                logger.info(assumed_role_object)

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
            else:
                response['body'] = json.dumps({
                    "message": "Not Authorized",
                })
                response['statusCode'] = 403
                return response
        else:
            response['body'] = json.dumps({
                "message": "Asset not found",
            })
            response['statusCode'] = 404
            return response

    except Exception as e:
        response['statusCode'] = 500
        logger.exception(e)
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
