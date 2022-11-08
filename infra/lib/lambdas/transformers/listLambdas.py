import os
import boto3
import json
from boto3.dynamodb.conditions import Key, Attr

lambdaClient=boto3.client('lambda')
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
pipeline_Database = None
unitTest = {
    "body": {
        "databaseId": "Unit_Test"
    }
}
unitTest['body']=json.dumps(unitTest['body'])

try:
    pipeline_Database = os.environ["PIPELINE_STORAGE_TABLE_NAME"]
except:
    print("Failed Loading Environment Variables")
    response['body'] =json.dumps({"message":"Failed Loading Environment Variables"}) 

#List All Lambdas in the Account
def get_Lambdas():
    allData=lambdaClient.list_functions()
    items=[]
    funcs=allData['Functions']
    for f in funcs:
        tags=lambdaClient.list_tags(Resource=f['FunctionArn'])
        tags=tags['Tags']
        if 'pipeline' in tags:
            item={
                "name":f['FunctionName'],
                "description":f['Description']
            }
            items.append(item)
    return items


def lambda_handler(event, context):
    print(event)
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
    try:
        print("Listing Lambdas")
        response['body'] = json.dumps({"message":get_Lambdas()})
        print(response)
        return response
    except Exception as e:
        response['statusCode'] = 500
        print("Error!", e.__class__, "occurred.")
        try:
            response['body'] = json.dumps({"message":str(e)})
        except:
            print("Can't Read Error")
            response['body'] = json.dumps({"message":"Error in Lambda"})
        print(response)
        return response


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    print(test_response)
