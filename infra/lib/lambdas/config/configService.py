import json
import os

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

def lambda_handler(event, context):
    try:
        print("Looking up the requested resource") 
        bucket = os.getenv("ASSET_STORAGE_BUCKET", None)
        response = {
            "bucket": bucket,
        }
        print("Success")
        return {
            "statusCode": "200",
            "body": json.dumps(response),
            "headers": {
                "Content-Type": "application/json",
            },
        }
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
 
