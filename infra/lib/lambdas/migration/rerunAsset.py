import json
import boto3
import logging
import sys
s3c = boto3.client('s3')
client = boto3.client('lambda')

def lambda_handler(event, context):
    print(event)
    eS3=event['body']
    bucketName=eS3['bucket']
    key=eS3['key']
    response=s3c.head_object(Bucket=bucketName,Key=key)
    pipelines=[]
    message="Pipelines Executed:["
    mdata=""
    if "Metadata" in response:
        mdata=response['Metadata']
        print('M True')
    if "pipelines" in mdata:
        print("p True")
        pipelines=mdata['pipelines']
        logging.info(pipelines)
        pipelines=json.loads(pipelines)
        pipelines=pipelines['functions']
    if len(pipelines)>0:
        for p in pipelines:
            l_payload={
                "assetId":p['assetid'],
                "databaseId":p['databaseid'],
                "Bucket":bucketName,
                "Key":key,
            }
            if 'input' in p:
                l_payload['input']=p['input']
            name=str(p['name'])
            l_payload=json.dumps(l_payload).encode('utf-8')
            try:
                client.invoke(FunctionName=name, InvocationType='Event', Payload=l_payload)
                message=message+name+":executed,"
            except:
                message=message+name+":failed,"
                logging.error(message)
    message=message+"]"
    logging.info(response)
    logging.info(message)