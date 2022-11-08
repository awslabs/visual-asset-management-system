import json
import boto3
from boto3.dynamodb.conditions import Key
import os
import uuid
import time
from validators import validate

try:
    client = boto3.client('lambda')
    s3c = boto3.client('s3')
    sfn_client = boto3.client('stepfunctions')
    dynamodb = boto3.resource('dynamodb')
except Exception as e:
    print(str(e))
    print("Failed Loading Error Functions")    

try:
    asset_Database = os.environ["ASSET_STORAGE_TABLE_NAME"]
    pipeline_Database=os.environ["PIPELINE_STORAGE_TABLE_NAME"]
    workflow_database = os.environ["WORKFLOW_STORAGE_TABLE_NAME"]
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")


def get_pipelines(databaseId, pipelineId):
    table = dynamodb.Table(pipeline_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('pipelineId').eq(pipelineId),
        ScanIndexForward=False,
    )
    return response['Items']

def launchWorkflow(bucketName, key, workflow_arn, asset_id, workflow_id, database_id):
    print("Launching workflow with arn: ", workflow_arn)
    response = sfn_client.start_execution(
        stateMachineArn=workflow_arn,
        input=json.dumps({ 'bucket': bucketName, 'key': key, 'databaseId': database_id,
         'assetId': asset_id, 'workflowId': workflow_id })
    )
    print("Response: ", response)
    executionId = response['executionArn'].split(":")[-1];
    table = dynamodb.Table(workflow_execution_database)
    table.put_item(
        Item = {
            'pk': f'{asset_id}-{workflow_id}', 
            'sk': executionId,
            'database_id': database_id,
            'asset_id': asset_id, 
            'workflow_id': workflow_id, 
            'workflow_arn': workflow_arn,
            'execution_arn': response['executionArn'], 
            'execution_id': executionId,
            'assets': []
        }
    )   
    return executionId

def get_asset(databaseId, assetId):
    table = dynamodb.Table(asset_Database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId)
    )
    return response['Items']


def get_workflow(databaseId, workflowId): 
    table = dynamodb.Table(workflow_database)
    response = table.query(
        KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('workflowId').eq(workflowId)
    )
    return response['Items']

def validate_pipelines(databaseId, workflow):
    for pipeline in workflow['specifiedPipelines']['functions']:
        pipeline_state = get_pipelines(databaseId, pipeline["name"])[0]
        if not pipeline_state['enabled']:
            return (False, pipeline["name"])
    return (True, '')

def lambda_handler(event, context):
    print(event)
    response = {}
    try:
        pathParams = event.get('pathParameters', {})
        print(pathParams)
        if 'databaseId' not in pathParams:
            message = "No database iD in API Call"
            response['body']=json.dumps({"message":message})
            print(response)
            return response
        
        if 'assetId' not in pathParams:
            message = "No assetId iD in API Call"
            response['body']=json.dumps({"message":message})
            print(response)
            return response

        if 'workflowId' not in pathParams:
            message = "No workflow iD in API Call"
            response['body']=json.dumps({"message":message})
            print(response)
            return response

        asset = get_asset(pathParams['databaseId'], pathParams['assetId'])[0]
        workflow = get_workflow(pathParams['databaseId'], pathParams['workflowId'])[0]
        print(asset)
        print(workflow)
        (status, pipelineName) = validate_pipelines(pathParams['databaseId'], workflow)
        if not status:
            print("Not all pipelines are enabled")
            response['statusCode'] = 500
            response['body'] = json.dumps({'message': f'{pipelineName} is not enabled'})
        else:
            print("All pipelines are enabled. Continuing to run run workflow")

        data = {}
        data['assetId'] = pathParams['assetId']
        data['workflow_arn']=workflow['workflow_arn']
        data['workflowId']=workflow['workflowId']
        data['bucketName'] = asset['assetLocation']['Bucket']
        data['key'] = asset['assetLocation']['Key']

        print("Validating Parameters")
        (valid, message) = validate({
            'databaseId': {
                'value': pathParams['databaseId'], 
                'validator': 'ID'
            },
            'workflowId': {
                'value': data['workflowId'], 
                'validator': 'ID'
            }, 
            'assetId': {
                'value': data['assetId'], 
                'validator': 'ID'
            },
        })

        if not valid:
            print(message)
            response['body']=json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        executionId = launchWorkflow(data['bucketName'], data['key'], data['workflow_arn'], data['assetId'], data['workflowId'], pathParams['databaseId'])
        response["statusCode"] = 200
        response['body'] = json.dumps({'message': executionId})
        return response

    except Exception as e:
        response['statusCode'] = 500
        print("Error!", e.__class__, "occurred.")
        try:
            print(e)
            response['body'] = json.dumps({"message": str(e)})
        except:
            print("Can't Read Error")
            response['body'] = json.dumps({"message": "An unexpected error occurred while executing the request"})
        return response

