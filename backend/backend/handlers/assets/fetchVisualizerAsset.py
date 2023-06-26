#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import base64
import sys
from botocore.config import Config
from botocore.exceptions import ClientError
from backend.common.validators import validate

s3_client = boto3.client('s3')

def lambda_handler(event, context):

    print(str(event))

    # Get the request headers from the API Gateway event
    try:
        request_headers = event['headers']
    except:
        request_headers = ""

    #Error if no headers provided (API Gateway problem or manual testing error)
    if not request_headers or request_headers == None or request_headers == "":
        message = "No Headers Provided"
        error_response = {
        'statusCode': 400,
        'body': json.dumps({"message": message}),
        'headers': {
                'Access-Control-Allow-Credentials': True,
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type, Range',
                'Access-Control-Allow-Methods': 'OPTIONS,GET'
            }
        }
        print(error_response)
        return error_response
    
    # Get the "Range" header from the request headers
    try:
        range_header = request_headers.get('range')
    except:
        range_header = ""

    # Get the "content-type" header from the request headers
    try:
        content_type_header = request_headers.get('content-type')
    except:
        content_type_header = ""
    
    # Extract the bucket name and object key from the API Gateway event
    bucket_name = os.environ["ASSET_VISUALIZER_BUCKET_NAME"]

    #Get the object key which comes after the base path of the API Call
    object_key = event['pathParameters']['proxy'] #"/".join(event['rawPath'].strip("/").split('/')[1:])

    #Error if no object key in path
    if not object_key or object_key == None or object_key == "":
        message = "No Visualizer File Object Key Provided in Path"
        error_response = {
        'statusCode': 400,
        'body': json.dumps({"message": message}),
        'headers': {
                'Access-Control-Allow-Credentials': True,
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type, Range',
                'Access-Control-Allow-Methods': 'OPTIONS,GET'
            }
        }
        print(error_response)
        return error_response
    
    # Prepare the S3 GetObject request parameters
    s3_params = {
        'Bucket': bucket_name,
        'Key': object_key
    }
    
    # Add the "Range" header to the S3 GetObject request if it exists
    if range_header and range_header != None and range_header != "":
        s3_params['Range'] = range_header


    # Add the "content-type" header to the S3 GetObject request if it exists
    if content_type_header and content_type_header != None and content_type_header != "":
        s3_params['ResponseContentType'] = content_type_header
    
    print(s3_params)

    try:
        # Fetch the file from S3
        response = s3_client.get_object(**s3_params)
        
        print(str(response))

        # Extract the file data
        file_data = response['Body'].read()
        
        # Prepare the API Gateway response
        api_gateway_response = {
            'statusCode': 200,
            'body': '',
            'headers': {
                    'Access-Control-Allow-Credentials': True,
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type, Range',
                    'Access-Control-Allow-Methods': 'OPTIONS, GET',
                    'Accept-Ranges': response['ResponseMetadata']['HTTPHeaders']['accept-ranges'],
                    'Content-Type': response['ResponseMetadata']['HTTPHeaders']['content-type'],
                    'Content-Length': response['ResponseMetadata']['HTTPHeaders']['content-length'],
                    }
        }

        # Add the "Range" header if returned
        try:
            response_header_range = response['ResponseMetadata']['HTTPHeaders']['content-range']
        except:
            response_header_range = ""
        
        if response_header_range != None and response_header_range != "":
            api_gateway_response['headers']['Content-Range'] = response_header_range
        
        # Add the "ContentEncoding" header if returned
        try:
            response_header_content_encoding = response['ResponseMetadata']['HTTPHeaders']['content-encoding']
        except:
            response_header_content_encoding = ""
        
        if response_header_content_encoding  != None and response_header_content_encoding != "":
            api_gateway_response['headers']['Content-Encoding'] = response_header_content_encoding

        print(print(str(api_gateway_response)))

        #Get the size of the file data in bytes and return error if larger than ~5.9MB (accounting for header space)
        file_size = sys.getsizeof(file_data)
        if file_size > 5.9 * 1024 * 1024:
            #NOTE: 6MB is a hard limit for Lambda functions. API Gateway has a 10MB hard limit. 
            message = "Error: Visualizer File Size Chunk is Larger than ~5.9MB"
            error_response = {
            'statusCode': 400,
            'body': json.dumps({"message": message}),
            'headers': {
                    'Access-Control-Allow-Credentials': True,
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': 'Content-Type, Range',
                    'Access-Control-Allow-Methods': 'OPTIONS,GET'
                }
            }
            print(error_response)
            return error_response

        # If returned data is binary, return the file contents as a base64 encoded string in the body
        if isinstance(file_data, bytes):
            api_gateway_response['body'] = base64.b64encode(file_data).decode('utf-8')
            api_gateway_response['isBase64Encoded'] = True
            print("Return is Binary so BaseEncode64")
        else:
            #else return as regular string
            api_gateway_response['body'] = file_data.decode('utf-8')

        return api_gateway_response
    
    except ClientError as e:
        print(e)
        message = "Error Fetching Visualizer File from Path Provided: " + str(e)
        error_response = {
        'statusCode': 400,
        'body': json.dumps({"message": message}),
        'headers': {
                'Access-Control-Allow-Credentials': True,
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type, Range',
                'Access-Control-Allow-Methods': 'OPTIONS,GET'
            }
        }
        print(error_response)
        return error_response
    except Exception as e:
        # If other error occurs, return an error response
        message = "Error: " + str(e)
        error_response = {
            'statusCode': 500,
            'body': json.dumps({"message": message}),
            'headers': {
                'Access-Control-Allow-Credentials': True,
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Headers': 'Content-Type, Range',
                'Access-Control-Allow-Methods': 'OPTIONS,GET'
            }
        }
        print(error_response)
        return error_response