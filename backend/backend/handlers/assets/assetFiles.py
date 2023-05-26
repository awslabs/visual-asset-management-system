import boto3
import json
import os

import logging

# Create a logger object to log the events
logger = logging.getLogger()
logger.setLevel(logging.INFO)


dynamodb_client = boto3.client('dynamodb')
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')
paginator = s3_client.get_paginator('list_objects_v2')
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

asset_database = os.environ["ASSET_STORAGE_TABLE_NAME"]
asset_table = dynamodb.Table(asset_database)

# A method that takes in s3 path and returns all the files in that path using paginator and s3_client


def get_all_files_in_path(bucket, path):
    files = []
    for page in paginator.paginate(Bucket=bucket, Prefix=path):
        for obj in page['Contents']:
            files.append({
                'key': obj['Key'],
                'relativePath': obj['Key'].removeprefix(path)
            })

    # Log the length of files with a description
    logger.info("Files in the path: ")
    logger.info(len(files))
    return files

# Check if the assetId is present in the database using asset_table resource
# If it exists return the assetLocation bucket and key
# If it does not exist return None


def get_asset(database_id, asset_id):
    asset_item = asset_table.get_item(
        Key={
            'databaseId': database_id,
            'assetId': asset_id
        })
    if 'Item' in asset_item:
        return asset_item['Item']
    else:
        return None


def lambda_handler(event, context):
    # get assetId, databaseId from event
    asset_id = event['pathParameters']['assetId']
    database_id = event['pathParameters']['databaseId']

    # log the assetId and databaseId
    logger.info("AssetId: " + asset_id + " DatabaseId: " + database_id)

    # check if assetId exists in database
    asset = get_asset(database_id, asset_id)
    asset_location = asset['assetLocation']

    # log the asset_location
    logger.info("AssetLocation: " + str(asset_location))

    # if assetId exists in database
    if asset_location:
        # Get Bucket and Key from assetLocation dictionary
        bucket = asset_location['Bucket']
        key = asset_location['Key']

        # get all files in assetLocation
        files = get_all_files_in_path(bucket, key)
        response['body'] = json.dumps(files)
        return response
    else:
        # log the assetId and databaseId on a single line and include they don't exist
        logger.info("AssetId: " + asset_id + " DatabaseId: " + database_id + " Asset does not exist")

        response['statusCode'] = 404
        response['body'] = json.dumps("Asset not found")
        return response

