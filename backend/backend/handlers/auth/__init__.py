#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import json
import os
import boto3

dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
db_database = None

def request_to_claims(request):
    if 'requestContext' not in request:
        # #Lambda cross-calling input. Only checked when requestContext is not present through API Gateway call. 
        # if 'lambdaCrossCall' in request:
        #     return {
        #         "tokens": [request["lambdaCrossCall"]["userName"]],
        #         "roles": [],
        #         "externalAttributes": []
        #     }
        # else:
        return {
            "tokens": [],
            "roles": [],
            "externalAttributes": []
        }

    #Normal authorizer processing
    return {
        "tokens": json.loads(request['requestContext']['authorizer']['jwt']['claims']['vams:tokens']),
        "roles": json.loads(request['requestContext']['authorizer']['jwt']['claims']['vams:roles']),
        "externalAttributes": json.loads(request['requestContext']['authorizer']['jwt']['claims']['vams:externalAttributes']),
    }