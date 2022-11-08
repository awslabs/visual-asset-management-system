import os
import sys
import boto3
import json
from boto3.dynamodb.conditions import Key

try:
    jobDatabase=os.environ['JOB_STORAGE_TABLE_NAME']
    asset_Database=os.environ['ASSET_STORAGE_TABLE_NAME']
    updateFunction=os.environ['UPLOAD_LAMBDA_FUNCTION_NAME']
    ddb=boto3.resource('dynamodb')
    table=ddb.Table(jobDatabase)
    table2=ddb.Table(asset_Database)    
    lambda_client=boto3.client('lambda')
    _lambda = lambda name,payload: lambda_client.invoke(FunctionName=name,InvocationType='Event', Payload=json.dumps(payload).encode('utf-8'))
except Exception as e:
    print(str(e))
    print("Failed Loading Error Functions")   
def reading_database(jobId):
    print("Reading Database")
    response = table.query(
        KeyConditionExpression=Key('jobId').eq(jobId),
        ScanIndexForward=False
    )
    return response['Items'][0]
def updateAsset(pName, body, status, message="running"):
    print("7")
    print(pName+":"+json.dumps(body)+":"+message)
    try:
        resp = table2.query(
            KeyConditionExpression=Key('databaseId').eq(
                body['databaseId']) & Key('assetId').eq(body['assetId']),
            ScanIndexForward=False,
        )
        if len(resp['Items']) == 0:
            raise ValueError('No Items of this AssetId found in Database')
        else:
            item = resp['Items'][0]
            version = body['VersionId']
            pipelines = []
            if version == item['currentVersion']['S3Version']:
                pipelines = item['currentVersion']['specifiedPipelines']
                test = True
                for i, p in enumerate(pipelines):
                    if p['name'] == pName:
                        pipelines[i]['status'] = status
                        test = False
                        break
                if test:
                    new = {
                        'name': pName,
                        'status': status
                    }
                    pipelines.append(new)
                item['currentVersion']['specifiedPipelines'] = pipelines
                item['specifiedPipelines']=pipelines
            else:
                item0=item
                for i, f in item0['versions']:
                    if f['S3Version'] == version:
                        pipelines = f['specifiedPipelines']
                        test = True
                        for j, p in enumerate(pipelines):
                            if p['name'] == pName:
                                test = False
                                pipelines[j]['status'] = status
                                break
                        if test:
                            new = {
                                'name': pName,
                                'status': status
                            }
                            pipelines.append(new)
                        item['versions'][i]['specifiedPipelines']=pipelines
                        break
    except Exception as e:
        print(e)
        raise(e)
        return json.dumps({"message": str(e)})

def update_database(job):
    table.put_item(Item=job)    

def updating_job(event):
    print("Updating Job")
    detail=event['detail']
    jobId=detail['ProcessingJobName']
    status=detail['ProcessingJobStatus']
    outputKey=detail['']

def start_job(event):
    print("Starting Job")
    payload=event

    if 'databaseId' not in payload or 'jobId' not in payload or 'assetId' not in payload:
        raise ValueError("DatabaseId or JobId not in event.")
    else:
        update_database(payload)
def lambda_handler(event,context):
    print(event)
    try:
        if 'jobId' in event:
            start_job(event)
        else:
            updating_job(event)
    except Exception as e:
        print(str(e))
        raise(e)