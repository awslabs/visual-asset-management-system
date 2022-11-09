#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
import datetime
path=os.environ['CODEBUILD_SRC_DIR_StackOutput']+'/cft_output.json'

bucketName=os.environ['s3bucket']

f=open(path)
data=json.load(f)

command='aws apigateway create-deployment --rest-api-id '+data['APIGatewayId'] +' --stage-name Prod'

os.system(command)

config_file={
    "APP_TITLE": "Amazon Dolos",
    "CUSTOMER_NAME": "Amazon Web Services",
    "s3": {
        "REGION": data['region'],
        "BUCKET": data['AssetStorageBucket']
    },
    "apiGateway": {
        "REGION": data['region'],
        "URL": data['APIGURL']
    },
    "cognito": {
        "REGION": data['region'],
        "USER_POOL_ID": data['UserPoolID'],
        "APP_CLIENT_ID": data['AppClientID'],
        "IDENTITY_POOL_ID": data['IdentityPoolID'],
        "Domain":data['Domain']
    }
}

fileName=datetime.datetime.utcnow().strftime('%B%d%Y-%H_%M_%S')+'-config.json'
s3=boto3.resource('s3')
s3Object=s3.Object(bucketName,fileName)
s3Object.put(Body=bytes(json.dumps(config_file).encode('UTF-8')))

f.close()