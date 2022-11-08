import json
from re import L
import boto3
from boto3.dynamodb.conditions import Key, Attr
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
import os

try:
    client = boto3.client('lambda')
    error_Function = os.environ['ERROR_FUNCTION']
    sagemaker_execution=os.environ['SAGEMAKER_FUNCTION']
    _err = lambda payload: client.invoke(FunctionName=error_Function,InvocationType='Event', Payload=json.dumps(payload).encode('utf-8'))
    _lambda = lambda name,payload: client.invoke(FunctionName=name,InvocationType='Event', Payload=json.dumps(payload).encode('utf-8'))
except Exception as e:
    print(str(e))
    print("Failed Loading Error Functions")    
s3c = boto3.client('s3')
dynamodb = boto3.resource('dynamodb')
try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    pipeline_Database=os.environ["PIPELINE_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
def updateDatabase(pName, body, status, message="running"):
    print(pName+":"+json.dumps(body)+":"+message)
    table = dynamodb.Table(asset_Database)
    try:
        resp = table.query(
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

def get_Pipelines(databaseId):
    table = dynamodb.Table(pipeline_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId),
        ScanIndexForward=False,
    )
    return response['Items']

def launchPipelines(data, bucketName, key, version):
    pipelines = []
    mData = ""
    print(data)
    if "Metadata" in data:
        mData = data['Metadata']
        print(mData)
        print('M True')
    else:
        raise ValueError("No Metadata in launchPipelines")
    message = "Pipelines Executed:["
    if 'pipelines' in mData:
        print("p True")
        pipelines = mData['pipelines']
        # TODO remove this, I'm not sure why this is necessary. Is a string passed somewhere else in the code?
        try: 
            pipelines = json.loads(pipelines)
        except Exception as e:
            print("Json loads failed")
        pipelines = pipelines['functions']
        print(pipelines)
    if len(pipelines) > 0:
        db_pipelines=get_Pipelines(mData['databaseId'])
        for p in pipelines:
            print("Executing")
            print(p)
            l_payload = {
                "assetId": mData['assetId'],
                "databaseId": mData['databaseId'],
                "Bucket": bucketName,
                "Key": key,
                "VersionId": version
            }
            # TODO Input does nothing currently. I'm not sure what it's supposed to do
            if 'input' in p:
                l_payload['input'] = p['input']
            name = ''
            pipelineType=''
            for pdict in db_pipelines:
                if pdict['pipelineId']==str(p['name']):
                    name=pdict['pipelineId']
                    pipelineType=pdict['pipelineType']
                    if 'outputType' in pdict:
                        l_payload['outputType'] = pdict['outputType']
                    break
            print('function name')
            print(name)
            print('payload')
            print(l_payload)
            try:
                if pipelineType=='SageMaker':
                    l_payload['pipelineId']=name
                    _lambda(sagemaker_execution,l_payload)
                else:
                    _lambda(name,l_payload)
                message = message+name+":executed,"
                updateDatabase(name, l_payload, 'InProgress', 'running')
            except Exception as e:
                updateDatabase(name, l_payload, 'failed', str(e))
                message = message+name+":failed check logs,"
    message = message+"]"
    print(message)

def lambda_handler(event, context):
    print(event)
    try:
        if 'Records' not in event:
            if isinstance(event['body'], str):
                event['body'] = json.loads(event['body'])    
            data=event['body']
            mData={
                'Metadata':{
                    'assetId':data['assetId'],
                    'databaseId':data['databaseId'],
                    'Bucket':data['bucketName'],
                    'Key':data['key'],
                    'pipelines':data['specifiedPipelines']
                }
            }
            if 'versionId' in data:
                launchPipelines(mData, data['bucketName'], data['key'], data['versionId'])
            else:
                response = s3c.head_object(Bucket=data['bucketName'], Key=data['key'])
                version=response['VersionId']
                launchPipelines(mData, data['bucketName'], data['key'],version)
            print('Relaunching Pipleines')

        else:
            print("S3 Upload")
            eS3 = event['Records'][0]['s3']
            bucketName = eS3['bucket']['name']
            print(bucketName)
            key = eS3['object']['key']
            version = eS3['object']['versionId']
            response = s3c.head_object(Bucket=bucketName, Key=key, VersionId=version)
            print(response)
            launchPipelines(response, bucketName, key, version)
            print("Success")
    except Exception as e:
        raise(e)
        e=str(e)
        print(e)
        _err(e)