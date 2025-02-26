import os
import boto3
import json
from botocore.config import Config
from datetime import datetime
from handlers.metadata import to_update_expr
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType

region = os.environ['AWS_REGION']
timeout = int(os.environ['CRED_TOKEN_TIMEOUT_SECONDS'])
s3_config = Config(signature_version='s3v4', s3={'addressing_style': 'path'})
s3 = boto3.client('s3', region_name=region, config=s3_config)

lambda_client = boto3.client('lambda')
dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')
logger = safeLogger(service_name="IngestAsset")

main_rest_response = STANDARD_JSON_RESPONSE


try:
    bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]
    db_table_name = os.environ["DATABASE_STORAGE_TABLE_NAME"]
    asset_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    upload_asset_lambda = os.environ["UPLOAD_LAMBDA_FUNCTION_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body']['message'] = "Failed Loading Environment Variables"


def calculate_num_parts(file_size, max_part_size=12 * 1024 * 1024):
    return -(-file_size // max_part_size)


def generate_presigned_url(key, upload_id, part_number, expiration=timeout):
    url = s3.generate_presigned_url(
        ClientMethod='upload_part',
        Params={
            'Bucket': bucket_name,
            'Key': key,
            'PartNumber': part_number,
            'UploadId': upload_id
        },
        ExpiresIn=expiration
    )
    return url


def generate_upload_asset_payload(event):
    database_id = event['body']['databaseId']
    assetName = event['body']['assetName']
    event_asset_id = event["body"].get("assetId")
    key = event['body']['key']
    description = event['body']['description']
    tags = event['body'].get('tags', [] )

    payload = {
        "body": {
            "databaseId": database_id,
            "assetId": event_asset_id,
            "assetName": assetName,
            "pipelineId": None,
            "executionId": None,
            "tags": tags,
            "key": key,
            "assetType": f".{key.split('.')[-1]}",
            "description": description,
            "specifiedPipelines": [],
            "isDistributable": True,
            "Comment": "",
            "assetLocation": {
                "Key": key
            },
            "previewLocation": None
        },
        "returnAsset": True
    }
    return payload


#Function to check a provided databaseId against the dynamoDB of databases to see whether or not it exists
def verifyDatabaseExists(databaseId):
    table = dynamodb.Table(db_table_name)
    try:
        response = table.get_item(Key={'databaseId': databaseId})
        asset = response.get('Item', {})
        if asset:
            return (True, "")
        else:
            return (False, f"DatabaseId {databaseId} does not exist")
    except Exception as e:
        return (False, f"Error verifying databaseId: {e}")


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    # logger.info("API_KEY", event.get('requestContext', {}).get('api-key'))
    #TODO: uncomment the below lines once we add apiKey to API Gateway
    """
    api_key = event.get('headers', {}).get('x-api-key')
    if not api_key or api_key != event.get('requestContext', {}).get('api-key'):
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Forbidden"})
        return response
    """

    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    if isinstance(event['body'], str):
        event['body'] = json.loads(event['body'])

    if 'databaseId' not in event['body']:
        message = "No databaseId in API Call"
        logger.error(message)
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": message})
        return response
    
    if 'assetId' not in event['body']:
        message = "No assetId in API Call"
        logger.error(message)
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": message})
        return response

    if 'assetName' not in event['body']:
        message = "No assetName in API Call"
        logger.error(message)
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": message})
        return response
    
    if 'key' not in event['body']:
        message = "No key (file name path) in API Call"
        logger.error(message)
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": message})
        return response
    logger.info("Validating parameters")
    (valid, message) = validate({
        'assetPathKey': {
            'value': event['body']['key'],
            'validator': 'ASSET_PATH'
        },
        'databaseId': {
            'value': event['body']['databaseId'],
            'validator': 'ID'
        },
        'assetId': {
            'value': event['body']['assetId'],
            'validator': 'ID'
        },
        'assetName': {
            'value': event['body']['assetName'],
            'validator': 'OBJECT_NAME'
        },
        'description': {
            'value': event['body']['description'],
            'validator': 'STRING_256'
        },
        'tags': {
            'value': event['body'].get('tags', []),
            'validator': 'OBJECT_NAME_ARRAY',
            'optional': True

        }
    })
    if not valid:
        logger.error(message)
        response['body'] = json.dumps({"message": message})
        response['statusCode'] = 400
        return response
    
    #Do check / lookup on what databases exist already and compare to databaseId set in body
    #Will throw error if it does not exist
    (db_verified, db_err_message) = verifyDatabaseExists(event['body']['databaseId'])
    if not db_verified:
        logger.error(db_err_message)
        response['body'] = json.dumps({"message": db_err_message})
        response['statusCode'] = 404
        return response
    
    #Split object key by path and return the first value (the asset ID)
    asset_idFromPath = event['body']['key'].split("/")[0]

    #Check if the asset ID is the same as the asset ID from the path
    if asset_idFromPath != event['body']['assetId']:
        response['body'] = json.dumps({"message": "Asset ID from path does not match the asset ID"})
        response['statusCode'] = 400
        return response

    #ABAC Checks
    http_method = event['requestContext']['http']['method']
    operation_allowed_on_asset = False

    asset = {
        "object__type": "asset",
        "databaseId": event['body']['databaseId'],
        "assetId": event['body']['assetId'],
        "assetName": event['body']['assetName'],
        "tags": event['body'].get('tags', [])
    }

    logger.info(asset)

    for user_name in claims_and_roles["tokens"]:
        casbin_enforcer = CasbinEnforcer(user_name)
        if casbin_enforcer.enforce(f"user::{user_name}", asset, "PUT") and casbin_enforcer.enforceAPI(event):
            operation_allowed_on_asset = True
            break

    if operation_allowed_on_asset:
        try:
            if http_method == 'POST':
                if "parts" in event['body']:
                    logger.info("Stage 2 - complete upload")

                    """ Sample request format
                    {
                    "parts": [
                            {"PartNumber": 1, "ETag": "exampleETag1"},
                            {"PartNumber": 2, "ETag": "exampleETag2"}
                        ],
                    "upload_id": "upload_id",
                    "key": "assetId/file_name",
                    ..... Other Asset upload related attribute like databaseId, assetName, description, tags
                    }
                    """
                    resp = s3.complete_multipart_upload(
                        Bucket=bucket_name,
                        Key=event['body']['key'],
                        UploadId=event['body'].get("upload_id"),
                        MultipartUpload={'Parts': event['body'].get("parts", [])}
                    )
                    logger.info(resp)

                    if resp['ResponseMetadata']['HTTPStatusCode'] // 100 == 2:
                        logger.info("S3 Multipart upload completed successfully.")

                        try:
                            if 'databaseId' in event['body'] and 'description' in event['body']:
                                logger.info(event)

                                #Do MIME check on whatever is uploaded to S3 at this point for this asset, before we do DynamoDB insertion, to validate it's not malicious
                                if(not validateS3AssetExtensionsAndContentType(bucket_name, event['body']['assetId'])):
                                    #TODO: Delete asset and all versions of it from bucket
                                    #TODO: Change workflow so files get uplaoded first and then this function/workflow should run, error if no asset files are uploaded yet when running this
                                    response['statusCode'] = 403
                                    response['body'] = json.dumps({"message": "An uploaded asset contains a potentially malicious executable type object. Unable to process asset upload."})
                                    return response

                                payload = generate_upload_asset_payload(event)
                                payload.update({
                                    "requestContext": event['requestContext']
                                })
                                logger.info("Payload:")
                                logger.info(payload)

                                logger.info("Invoking Asset Lambda .........")
                                lambda_response = lambda_client.invoke(FunctionName=upload_asset_lambda,
                                                                    InvocationType='RequestResponse',
                                                                    Payload=json.dumps(payload).encode('utf-8'))
                                logger.info("lambda response")
                                logger.info(lambda_response)

                                stream = lambda_response['Payload']
                                response_payload = json.loads(stream.read().decode("utf-8"))
                                logger.info("lambda payload")
                                logger.info(response_payload)

                                response_payload_body = json.loads(response_payload.get("body", ""))
                                logger.info("lambda response payload body")
                                logger.info(response_payload_body)

                                #if lambda response is anything but status code 200, throw exception with error message coming back
                                if response_payload['statusCode'] != 200 and response_payload['statusCode'] != 500:
                                    logger.exception("Error inserting asset record: "+response_payload_body.get('message', "Unknown"))
                                    response['statusCode'] = response_payload['statusCode']
                                    response['body'] = json.dumps({"message": "Error inserting asset record: "+response_payload_body.get('message', "Unknown")})
                                    return response
                                elif response_payload['statusCode'] == 500:
                                    logger.exception("Error invoking upload Asset Lambda function:")
                                    response['statusCode'] = 500
                                    response['body'] = json.dumps({"message": "Internal Server Error"})
                                    return

                                logger.info("Invoke Asset Lambda Successfully.")

                                table = dynamodb.Table(os.environ['METADATA_STORAGE_TABLE_NAME'])

                                metadata = {'_metadata_last_updated': datetime.now().isoformat()}
                                keys_map, values_map, expr = to_update_expr(metadata)
                                table.update_item(
                                    Key={
                                        "databaseId": event['body']['databaseId'],
                                        "assetId": event['body']['assetId'],
                                    },
                                    ExpressionAttributeNames=keys_map,
                                    ExpressionAttributeValues=values_map,
                                    UpdateExpression=expr,
                                )
                                logger.info("Created metadata successfully")

                                response['statusCode'] = 200
                                response['body'] = json.dumps({"message": "Multipart upload and asset ingestion completed successfully."})
                            else:
                                response['statusCode'] = 400
                                response['body'] = json.dumps({"message": "DatabaseId, Description are required."})
                        except Exception as e:
                            logger.exception("Error invoking upload Asset Lambda function:")
                            response['statusCode'] = 500
                            response['body'] = json.dumps({"message": "Error invoking upload Asset Lambda function. "})
                    else:
                        response['statusCode'] = 400
                        response['body'] = json.dumps({"message": "Multipart upload and asset ingestion completion failed."})
                else:
                    logger.info("Stage 1 - initiatlize upload")
                    file_size = int(event['body']['file_size'])
                    num_parts = calculate_num_parts(file_size)
                    key = event['body']['key']

                    resp = s3.create_multipart_upload(
                        Bucket=bucket_name,
                        Key=key,
                        ContentType='application/octet-stream',
                        Metadata={
                            "databaseid": event['body']['databaseId'],
                            "assetid": event['body']['assetId']
                        }
                    )
                    upload_id = resp['UploadId']

                    # Generate pre-signed URLs for part uploads
                    part_urls = [generate_presigned_url(key, upload_id, part_number) for part_number in range(1, num_parts + 1)]

                    response['body'] = json.dumps({"message": {
                        'uploadId': upload_id,
                        'numParts': num_parts,
                        'partUploadUrls': part_urls
                    }})
                    response['statusCode'] = 200
            else:
                response['statusCode'] = 403
                response['body'] = json.dumps({"message": "Only POST Supported"})

            return response

        except Exception as e:
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
            return response
        
    else:
        response['statusCode'] = 403
        response['body'] = json.dumps({"message": "Not Authorized"})
        return response
