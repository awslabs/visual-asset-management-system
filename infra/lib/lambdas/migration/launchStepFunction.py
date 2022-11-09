#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json

sfn=boto3.client('stepfunctions')

def lambda_handler(event,context):
    body=event['body']
    if isinstance(body, str):
        body = json.loads(body)
    smArn=''
    try:
        stateMachines=sfn.list_state_machines()['stateMachines']
        for sm in stateMachines:
            if sm['name']==body['pipeline']:
                smArn=sm['stateMachineArn']
                break
        if smArn=='':
            raise ValueError('No pipeline in request')
        response = sfn.start_execution(
            stateMachineArn=smArn,
            input=json.dumps(body),
        )    
    except Exception as e:
        print(str(e))
