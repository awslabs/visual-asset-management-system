import os
import sys
import glob
import boto3

try:
    bucketName=os.environ['ASSET_STORAGE_BUCKET_NAME']
except:
    bucketName='dolos-assetbucket-434ytf9n31tw'
s3c=boto3.client('s3')

body = {
    "databaseId": "Unit_Test",
    "assetId": "", # Editable
    "bucket": "", # Editable
    "key": "",
    "assetType": "",
    "description": "", # Editable
    "specifiedPipelines": [], # will develop a query to list pipelines that can act as tags.
    "isDistributable": False, # Editable
    "Comment": "", # Editable
    "previewLocation": {
        "Bucket": "",
        "Key": ""
    }
}

def uploadToS3(path,bucket,key):
    s3c.upload_file(path,bucket,key)
    return None

def updateS3(path):
    _f = lambda x: os.path.splitext(x)[0]
    _f2 = lambda x: os.path.splitext(x)[1]
    fileName=_f(path)
    x=dict(body)
    x['assetId']=os.path.basename(fileName)
    x['bucket']=bucketName
    x['key']=os.path.basename(path)
    x['assetType']=_f2(path)
    x['description']=x['assetId']
    x['Comment']="Testing"
    x['previewLocation']['Bucket']=x['bucket']
    x['previewLocation']['Key']=os.path.basename(fileName)+'.png'
    uploadToS3(path,x['bucket'],x['key'])
    uploadToS3(_f(path)+'.png',x['previewLocation']['Bucket'],x['previewLocation']['Key'])
    return x