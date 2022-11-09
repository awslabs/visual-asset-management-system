#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import time

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

cw_log=boto3.client('logs')
_namespace="errors"
try:
    _logGroupName=os.environ('CloudWatchLogGroupName')
except:
    print("Failed Loading Environment Variables")
    response['body']['message'] = "Failed Loading Environment Variables"


def lambda_handler(event, context):
    print(event)
    body={}
    queryParams = event.get('queryStringParameters', {})
    pathParams = event.get('pathParameters', {})
    
    body.update(queryParams)
    body.update(pathParams)
    
    try:
        metric={}
        if body['apiType']=='list' and 'databaseId' in body:
            print('Listing Errors')
            _st=0
            _ft=1000000000000
            _limit=1000
            if 'startTime' in body:
                _st=int(body['startTime'])
            if 'endTime' in body:
                _ft=int(body['endTime'])
            if 'limit' in body:
                _limit=int(body['limit'])
            resp=cw_log.get_log_events(
                logGroupName=_logGroupName,
                logStreamName=str(body['databaseId']),
                startTime=_st,
                endTime=_ft,
                limit=_limit,
                startFromHead=False
            )
            response['body']=json.dumps(resp)
            return response
        if 'databaseId' not in body:
            raise ValueError("No databaseId in body")
        else:
            metric['name']=context.function_name
            metric['error']=json.dumps(body)
            resp=cw_log.put_log_events(
                logGroupName=_logGroupName,
                logStreamName=str(body['databaseId']),
                logEvents=[{
                    'timestamp':int(round(time.time()*1000)),
                    'message':json.dumps(metric)
                }]
            )
    except Exception as e:
        response['statusCode']=500
        response['body']=str(e)
        return response