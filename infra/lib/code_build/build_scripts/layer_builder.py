#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import zipfile
import boto3
import shutil
from botocore.exceptions import ClientError
import tempfile
s3c=boto3.client('s3')
bucketName=''
try:
    packages=os.environ['packages'].split(" ")
    bucketName=os.environ['bucketName']
except:
    packages=[]

def upload(path,key):
    try:
        response=s3c.upload_file(path,bucketName,key)
    except ClientError as e:
        print(e)
        return False
    return True

def zipdir(pkg,path):
    ziph = zipfile.ZipFile(pkg+'.zip', 'w', zipfile.ZIP_DEFLATED)
    for root, dirs, files in os.walk(path+'/'):
        for file in files:
            ziph.write(os.path.join(root, file), os.path.relpath(os.path.join(root, file), os.path.join(path, '')))

def installer(pkg):
    with tempfile.TemporaryDirectory() as tmpdir:
        path=tmpdir+'/python'
        command = 'pip install '+pkg+' -t '+path+' --upgrade'
        print(command)
        os.system(command)
        print("Downloaded")
        zipdir('lambda_layers/'+pkg,tmpdir)
        print("Zipped")
        upload('lambda_layers/'+pkg+'.zip','layers/'+pkg+'.zip')
        print("Uploaded")

def lambda_handler(event, context):
    for pkg in packages:
        installer(pkg)
    return {
        'statusCode': 200,
        'body': json.dumps('Hello from Lambda!')
    }

if __name__=="__main__":
    print("Beginning Zip")
    if len(packages)==0:
        print("No Envs")
        packages=['sagemaker stepfunctions']
    lambda_handler(None,None)
    print("Completed")