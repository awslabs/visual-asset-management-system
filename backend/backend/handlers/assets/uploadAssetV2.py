import os
import boto3
import json
from botocore.config import Config
from datetime import datetime
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from handlers.metadata import to_update_expr
from common.constants import STANDARD_JSON_RESPONSE
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from common.s3 import validateS3AssetExtensionsAndContentType, validateUnallowedFileExtensionAndContentType
from common.dynamodb import get_asset_object_from_id

from models.assetsV2 import UploadAssetStage1NewRequestModel, UploadAssetStage1UpdateRequestModel, UploadAssetStage1ResponseModel, UploadAssetStage2RequestModel, AssetUploadTableModel
from models.common import APIGatewayProxyResponseV2, internal_error, success, validation_error, general_error, authorization_error, VAMSGeneralErrorResponse
import uuid


    #Overall Notes
    #New DynamoDB table for assetTemporaryUpload
    #Add S3 life cycle for incomplete parts, add dynamoDB record TTL on assetTemporaryUpload. 7 days

    #dynamoDB table:
    # - uploadId (PK)
    # - httpMethodType (i.e. is this a update or create based on PUT/POST)
    # - assetId
    # - databaseId
    # - description
    # - isDistributable
    # - tags
    # - filesAssets
    # - - key
    # - - sizeInBytes
    # - - isAssetPrimaryFile
    # - - Parts (Dict Array)
    # - - - UploadUrl
    # - - - PartNumber
    # - filePreview 
    # - - key
    # - - sizeInBytes
    # - - Parts (Dict Array)
    # - - - UploadUrl
    # - - - PartNumber

    #Input Overall Type - Entirely New Asset (asset data and files w/ optional preview) - POST
    #Input Stages - New Upload Asset Request - POST
    #Inputs: 
    # - databaseId
    # - assetName
    # - description
    # - isDistributable
    # - tags (str array)
    # - assetLinks (dict array)
    # - filesAsset (Dict Array)
    # - - key
    # - - partCount (OPTIONAL - if client alreayd knows how many parts to split into)
    # - - sizeInBytes (OPTIONAL - if client only knows file size and needs parts split to be figured out, if partCount not provided)
    # - - isAssetPrimaryFile
    # - filePreview (Dict) (optional)
    # - - key
    # - - partCount
    # - - sizeInBytes (OPTIONAL - if client only knows file size and needs parts split to be figured out, if partCount not provided)

    #Outputs:
    # - assetId (newly generated)
    # - uploadId (newly generated)
    # - filesAsset (Dict Array) 
    # - - key
    # - - Parts (Dict Array)
    # - - - UploadUrl
    # - - - PartNumber
    # - - isAssetPrimaryFile
    # - filePreview (Dict) (optional)
    # - - key
    # - - Parts (Dict Array)
    # - - - UploadUrl
    # - - - PartNumber

    #Input Stages - Finalize Upload Asset Request - POST
    #Inputs: 
    # - uploadId
    # - filesAsset (Dict Array) 
    # - - key
    # - - Parts (Dict Array)
    # - - - PartNumber
    # - - - ETag
    # - filePreview (Dict) (optional)
    # - - key
    # - - Parts (Dict Array)
    # - - - PartNumber
    # - - - ETag

    #Outputs:
    # - Success

    #Input Overall Type - Update Asset (files or preview) -- Asset Data updates need to come through asset service API - POST
    #Input Stages - New Upload Asset Request - PUT
    #Inputs: 
    # - assetId
    # - databaseId
    # - filesAsset (Dict Array) (optional)
    # - - key
    # - - partCount (OPTIONAL - if client alreayd knows how many parts to split into)
    # - - sizeInBytes (OPTIONAL - if client only knows file size and needs parts split to be figured out, if partCount not provided)
    # - - isAssetPrimaryFile
    # - filePreview (Dict) (optional)
    # - - key
    # - - partCount
    # - - sizeInBytes (OPTIONAL - if client only knows file size and needs parts split to be figured out, if partCount not provided)

    #Outputs:
    # - assetId
    # - uploadRequestId (newly generated)
    # - filesAsset (Dict Array)
    # - - key
    # - - Parts (Dict Array) (optional)
    # - - - UploadUrl
    # - - - PartNumber
    # - - isAssetPrimaryFile
    # - filePreview (Dict) (optional)
    # - - key
    # - - Parts (Dict Array)
    # - - - UploadUrl
    # - - - PartNumber

    #Input Stages - Finalize Upload Asset Request - PUT
    #Inputs: 
    # - uploadRequestId
    # - filesAsset (Dict Array) (optional)
    # - - key
    # - - Parts (Dict Array)
    # - - - PartNumber
    # - - - ETag
    # - filePreview (Dict) (optional)
    # - - key
    # - - Parts (Dict Array)
    # - - - PartNumber
    # - - - ETag

    #Outputs:
    # - Success


region = os.environ['AWS_REGION']
s3_config = Config(signature_version='s3v4', s3={'addressing_style': 'path'})
s3 = boto3.client('s3', region_name=region, config=s3_config)
lambda_client = boto3.client('lambda')
dynamodb_client = boto3.client('dynamodb')
dynamodb_resource = boto3.resource('dynamodb')
logger = safeLogger(service_name="UploadAseetV2")
main_rest_response = STANDARD_JSON_RESPONSE

storage_tempupload_prefix_key = '/upload/'
asset_storage_previewlocation_prefix_key = '/previews/'


try:
    asset_bucket_name = os.environ["S3_ASSET_STORAGE_BUCKET"]
    asset_tmp_bucket_name = os.environ["S3_ASSET_TMP_STORAGE_BUCKET"]
    asset_storage_table_name = os.environ["ASSET_STORAGE_TABLE_NAME"]
    asset_upload_table_name = os.environ["ASSET_UPLOAD_TABLE_NAME"]

except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body']['message'] = "Failed Loading Environment Variables"

asset_table = dynamodb_resource.Table(asset_storage_table_name)
asset_upload_table = dynamodb_resource.Table(asset_upload_table_name)

def calculate_num_parts(file_size, max_part_size=50 * 1024 * 1024):
    #Default: 50MB per part chunk
    return -(-file_size // max_part_size)


def generate_presigned_url(key, upload_id, part_number, bucket, expiration=86400):
    url = s3.generate_presigned_url(
        ClientMethod='upload_part',
        Params={
            'Bucket': bucket,
            'Key': key,
            'PartNumber': part_number,
            'UploadId': upload_id
        },
        ExpiresIn=expiration
    )
    return url

def save_uploaded_asset_details(assetUploadData: AssetUploadTableModel):
    #Save record to asset_upload_table DynamoDB table
    try:
        asset_upload_table.put_item(Item=assetUploadData.to_dict())
    except Exception as e:
        logger.exception("Failed saving asset upload details")
        raise VAMSGeneralErrorResponse("Failed saving asset upload details. Retry upload")
    return

def get_uploaded_asset_details(uploadId: str) -> AssetUploadTableModel:
    #fetch query results from DynamoDB table
    db_response = asset_upload_table.get_item(
    Key={
        'uploadId': uploadId
    })

    #Error if no item found
    if 'Item' not in db_response:
        raise VAMSGeneralErrorResponse("No Upload record found for UploadId")

    asset_upload_object = parse(db_response['Item'], model=AssetUploadTableModel)
    return asset_upload_object

def delete_uploaded_asset_details(uploadId: str):
    try:
        asset_upload_table.delete_item(
            Key={
                'uploadId': uploadId
            }
        )
    except Exception as e:
        logger.exception("Failed deleting asset upload details")

def get_asset_details(databaseId: str, assetId: str):
    #fetch query results from DynamoDB table
    db_response = asset_table.get_item(
    Key={
        'databaseId': databaseId,
        'assetId': assetId
    })

    #Error if no item found
    if 'Item' not in db_response:
        raise VAMSGeneralErrorResponse("No asset record found for AssetId")

    #asset_object = parse(db_response['Item'], model=AssetUploadTableModel)
    return db_response['Item']

def save_asset_details(assetData):
    #Save record to asset_table DynamoDB table
    try:
        asset_table.put_item(Item=assetData)
    except Exception as e:
        logger.exception("Failed saving asset details update")
        raise VAMSGeneralErrorResponse("Failed saving asset details update")

def move_storage_file(source_bucket: str, source_key: str, dest_bucket: str, dest_key: str):
    try:
        #Copy to new bucket
        s3.copy_object(
            CopySource={'Bucket': source_bucket, 'Key': source_key},
            Bucket=dest_bucket,
            Key=dest_key
        )

        #Delete object from original bucket
        s3.delete_object(Bucket=source_bucket, Key=source_key)

    except Exception as e:
        logger.exception("Failed moving file from temp storage")
        raise VAMSGeneralErrorResponse("Failed moving file from temp storage")

def compare_asset_upload_model_files(requestObject: UploadAssetStage2RequestModel, requestUploadObject: AssetUploadTableModel) -> bool:
    """
    Compares two AssetUploadTableModel objects to ensure all files and previews in object1
    also exist in object2 (object2 can have additional elements). Tracks findings of files
    and parts for detailed comparison.

    Args:
        object1: The first AssetUploadTableModel object.
        object2: The second AssetUploadTableModel object.

    Returns:
        True if all files and previews in object1 are present in object2 with matching details,
        False otherwise.
    """

    # Track findings (files and parts)
    all_files_found = True
    all_parts_found = True

    # Compare fileAssets
    if requestObject.filesAsset and requestUploadObject.filesAssets:
        for file1 in requestObject.filesAsset:
            found = False
            for file2 in requestUploadObject.filesAssets:
                if file1.key == file2.key:
                    # Check upload IDs match
                    if file1.uploadId != file2.uploadId:
                        all_files_found = False
                        break
                # Compare parts (if we have more parts in object 1 then 2, fail)
                if len(file1.Parts) > len(file2.Parts):
                    all_parts_found = False
                found = True
                break
            if not found:
                all_files_found = False
                break
    elif requestObject.filesAsset:  # object1 has elements but object2 doesn't
        all_files_found = False

    # Compare filePreview (similar logic with upload ID check)
    if requestObject.filePreview and requestUploadObject.filePreview:
        if requestObject.filePreview.key != requestUploadObject.filePreview.key or \
            requestObject.filePreview.uploadId != requestUploadObject.filePreview.uploadId or \
            len(requestObject.filePreview.Parts) > len(requestUploadObject.filePreview.Parts):
                all_files_found = False
                all_parts_found = False
    elif requestObject.filePreview:  # object1 has preview but object2 doesn't
        all_files_found = False

    # Overall Result (both files and parts must be found)
    return all_files_found and all_parts_found


def createupdate_asset_stage1(requestDataCreate: UploadAssetStage1NewRequestModel = None, requestDataUpdate: UploadAssetStage1UpdateRequestModel = None) -> UploadAssetStage1ResponseModel:
    logger.info("Initialing Stage 1 Processing")

    if (requestDataCreate and requestDataUpdate) or (not requestDataCreate and not requestDataUpdate):
        raise Exception("Invalid request, only one type of request allowed")
    
    #Generate new unique assetId UUID or reuse the existing assetId
    assetId = ('x'+str(uuid.uuid4())) if requestDataCreate else requestDataUpdate.assetId

    #Get databaseId from request
    databaseId = requestDataCreate.databaseId if requestDataCreate else requestDataUpdate.databaseId

    #Generate new unique uploadRequestId UUID
    uploadRequestIdNew = 'y'+str(uuid.uuid4())

    #Generate temporary location for upload
    uploadLocationAssetFiles = storage_tempupload_prefix_key+uploadRequestIdNew+'/assetFiles/'
    uploadLocationPreviewFile = storage_tempupload_prefix_key+uploadRequestIdNew+'/previewFile/'

    #Switch between the the right context
    filesAsset = requestDataCreate.filesAsset if requestDataCreate else requestDataUpdate.filesAsset
    filePreview = requestDataCreate.filePreview if requestDataCreate else requestDataUpdate.filePreview

    #Determined asset type
    assetType = None

    #Process initial asset files
    filesAssetDict = None
    if filesAsset:
        filesAssetDict = filesAsset.to_dict()
        for file in filesAssetDict:
            #Validate file extension to start (final check again on this for full MIME types on stage2)
            if not validateUnallowedFileExtensionAndContentType(file.get("key"), ""):
                raise ValidationError("Asset file provided has an unsupported file extension")
            
            #Calculate parts needed
            if file.get("sizeInBytes") and not file.get("partCount"):
                file["partCount"] = calculate_num_parts(file.get("sizeInBytes"))

            #Determine asset type based on primary asset file extension
            if file.get("isAssetPrimaryFile", "False") == "True":
                assetType = file.get("key").split(".")[-1]

            resp = s3.create_multipart_upload(
                Bucket=asset_bucket_name,
                Key=file.get("key"),
                ContentType='application/octet-stream',
                Metadata={
                    "databaseid": databaseId,
                    "assetid": assetId
                }
            )
            upload_id = resp['UploadId']

            #Set the upload ID for the file
            file["uploadId"] = upload_id

            #Set storage key location
            file["StorageKey"] = uploadLocationAssetFiles+file.get("key")

            #Loop through the quantity of parts based on partCount and add to "Parts"
            file["Parts"] = []
            for partNumber in range(1, file.get("partCount", 1)+1):
                part = {
                    "UploadUrl": generate_presigned_url(uploadLocationAssetFiles+file.get("key"), upload_id, partNumber, asset_tmp_bucket_name),
                    "PartNumber": partNumber,
                    "StorageKey": uploadLocationAssetFiles+file.get("key")
                }
                file["Parts"].append(part)
        
    #Process Preview File checks
    previewFileDict = None
    if filePreview :
        previewFileDict = filePreview.to_dict()
        #Validate file extension to start
        if not validateUnallowedFileExtensionAndContentType(previewFileDict.get("key"), ""):
            raise ValidationError("Asset preview file provided has an unsupported file extension")
        
        if previewFileDict.get("sizeInBytes") and not previewFileDict.get("partCount"):
            previewFileDict["partCount"] = calculate_num_parts(previewFileDict.get("sizeInBytes"))

        resp = s3.create_multipart_upload(
            Bucket=asset_bucket_name,
            Key=previewFileDict.get("key"),
            ContentType='application/octet-stream',
            Metadata={
                "databaseid": databaseId,
                "assetid": assetId
            }
        )
        upload_id = resp['UploadId']

        #Set the upload ID for the file
        previewFileDict["uploadId"] = upload_id

        #Set storage key location
        previewFileDict["StorageKey"] = uploadLocationPreviewFile+previewFileDict.get("key")

        #Loop through the quantity of parts based on partCount and add to "Parts"
        previewFileDict["Parts"] = []
        for partNumber in range(1, previewFileDict.get("partCount", 1)+1):
            part = {
                "UploadUrl": generate_presigned_url(uploadLocationPreviewFile+previewFileDict.get("key"), upload_id, partNumber, asset_tmp_bucket_name),
                "PartNumber": partNumber,
            }
            file["Parts"].append(part)

    #Create payload to store into upload temp asset table
    upload_storage_object_payload = {
        "uploadRequestId": uploadRequestIdNew,
        "httpMethodType": "POST" if requestDataCreate else "PUT",
        "assetId": assetId,
        "databaseId": databaseId,
        "vamsDataDetails": { #Only fields with non-None will be updated / inserted into the VAMS record at stage 2
            "assetName": requestDataCreate.assetDataDetails.assetName if requestDataCreate else None,
            "description": requestDataCreate.assetDataDetails.description if requestDataCreate else None,
            "isDistributable": requestDataCreate.assetDataDetails.isDistributable if requestDataCreate else None,
            "assetType": assetType,
            "tags": requestDataCreate.assetDataDetails.tags if requestDataCreate else None,
            "assetLinks": requestDataCreate.assetDataDetails.assetLinks if requestDataCreate else None,
        },
        "filesAssets": filesAssetDict,
        "filePreview": previewFileDict
    }

    #Parse to validate parameter fields for the upload table and save
    upload_storage_object = parse(upload_storage_object_payload,model=AssetUploadTableModel)
    save_uploaded_asset_details(upload_storage_object)

    #Create payload to store into upload temp asset table
    stage1_response_payload = {
        "uploadRequestId": uploadRequestIdNew,
        "assetId": assetId,
        "databaseId": databaseId,
        "filesAssets": filesAssetDict,
        "filePreview": previewFileDict
    }

    #Parse to validate parameters fields and return response
    response = parse(stage1_response_payload, model=UploadAssetStage1ResponseModel)
    return response

def complete_asset_stage2(requestData: UploadAssetStage2RequestModel, asset_uploaded_object: AssetUploadTableModel):
    logger.info("Initialing Stage 2 Processing")

    #Check to make sure for each file that each Part (and corresponding Part information) provided on RequestData matches the data stored asset_uploaded_object record. 
    #Note: Fewer parts are allowed though as users can choose how they want to upload with the parts provided. 
    if not compare_asset_upload_model_files(requestData, asset_uploaded_object):
        raise VAMSGeneralErrorResponse("The provided request data file files and parts do not match the stage 1 provided response.")

    filesAsset = requestData.filesAsset
    filePreview = requestData.filePreview

    finalUploadedFilesToProcess = []

    #Process initial asset files
    filesAssetDict = None
    if filesAsset:
        filesAssetDict = filesAsset.to_dict()
        for file in filesAssetDict:

            #Get the equivilent asset_uploaded file record for this file key to pull additional data from
            assetUploadedFile = None
            for aUploadedFile in asset_uploaded_object.filesAssets:
                if aUploadedFile.key == file.get("key"):
                    assetUploadedFile = aUploadedFile
                    break

            #Complete the upload for each file in the upload storage
            resp = s3.complete_multipart_upload(
                Bucket=asset_tmp_bucket_name,
                Key=file.get("key"),
                UploadId=file.get("uploadId"),
                MultipartUpload={'Parts': file.get("Parts", [])}
            )
            logger.info(resp)

            #Did we successfully combine the parts?
            if resp['ResponseMetadata']['HTTPStatusCode'] == "200":
                logger.info("Multipart upload completed successfully for file.")

                #Do Storage MIME check on files
                if(not validateS3AssetExtensionsAndContentType(asset_tmp_bucket_name, assetUploadedFile.StorageKey)):
                    raise ValidationError("Asset file uploaded has an unsupported file MIME type and/or extension")

                finalUploadedFilesToProcess.append({'type': 'asset', 'isPrimary': assetUploadedFile.isAssetPrimaryFile, 'storageKey': assetUploadedFile.StorageKey})
            else:
                #TODO: How to handle individual file errors?
                logger.error("Multipart upload failed for a file.")
                raise VAMSGeneralErrorResponse("Multipart upload failed for a file.")

    #Process Preview File checks
    previewFileDict = None
    if filePreview :
        previewFileDict = filePreview.to_dict()

        #Complete the upload for each file in the upload storage
        resp = s3.complete_multipart_upload(
            Bucket=asset_tmp_bucket_name,
            Key=previewFileDict.get("key"),
            UploadId=previewFileDict.get("uploadId"),
            MultipartUpload={'Parts': previewFileDict.get("Parts", [])}
        )
        logger.info(resp)

        #Did we successfully combine the parts?
        if resp['ResponseMetadata']['HTTPStatusCode'] == "200":
            logger.info("Multipart upload completed successfully for file.")

            #Do Storage MIME check on files
            if(not validateS3AssetExtensionsAndContentType(asset_tmp_bucket_name, assetUploadedFile.StorageKey)):
                raise ValidationError("Asset file uploaded has an unsupported file MIME type and/or extension")

            finalUploadedFilesToProcess.append({'type': 'preview', 'isPrimary': None, 'storageKey': asset_uploaded_object.filePreview.StorageKey})
        else:
            #TODO: How to handle individual file errors?
            logger.error("Multipart upload failed for a file.")
            raise VAMSGeneralErrorResponse("Multipart upload failed for a file.")


    #Move/copy/version file from temporary location to primary asset storage location
    for file in finalUploadedFilesToProcess:
        if file.get("type") == "asset":
            #Move file from temporary location to primary asset storage location
            move_storage_file(asset_tmp_bucket_name, file.get("storageKey"), asset_bucket_name, asset_uploaded_object.assetId + file.get("key"))

        elif file.get("type") == "preview":
            #Move file from temporary location to primary asset storage location
            move_storage_file(asset_tmp_bucket_name, file.get("storageKey"), asset_bucket_name, asset_storage_previewlocation_prefix_key + asset_uploaded_object.assetId + file.get("key"))

    #Do any final asset table updates if changes happened in any of the fields (assetDataDetails, primaryAssetFileKey and locations, preview locations)
    #This is where we also roll the version of assets on a PUT
    #TODO: Figure out new versioning scheme?
    #TODO: Figure out assetLinks saving
    #TODO: Move all asset data updates to another lambda function?
    #TODO: Create also a new upload wrapper lambda that can take care of calling other lambda functions (like this one) for updating VAMS data. That keeps this function clean?
    assetObject = {}
    saveAssetDetails = False

    if asset_uploaded_object.httpMethodType == "POST":

        #Create new asset record
        assetObject = {
            #...
        }

        saveAssetDetails = True

    elif asset_uploaded_object.httpMethodType == "PUT":

        #Get existing asset record
        assetObject = get_asset_details(asset_uploaded_object.databaseId,asset_uploaded_object.assetId)

        #Update preview file or primary asset file changes
        if assetObject:
            if assetObject.primaryAssetFileKey != asset_uploaded_object.filesAssets[0].StorageKey:
                assetObject.primaryAssetFileKey = asset_uploaded_object.filesAssets[0].StorageKey
                saveAssetDetails = True

            if assetObject.previewFileKey != asset_uploaded_object.filePreview.StorageKey:
                assetObject.previewFileKey = asset_uploaded_object.filePreview.StorageKey
                saveAssetDetails = True

        #Update asset versioning information


    if saveAssetDetails:
        save_asset_details(assetObject)

    #Remove record from uploads table
    delete_uploaded_asset_details(asset_uploaded_object.uploadRequestId)

    return #Success!

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:

    response = STANDARD_JSON_RESPONSE
    try: 
        global claims_and_roles
        claims_and_roles = request_to_claims(event)
        http_method = event['requestContext']['http']['method']
        operation_allowed_on_asset = False

        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])

        if 'uploadId' not in event['body']:
            if http_method == "POST":
                logger.info("Stage 1 API Call - POST - Create New Asset")
                requestData = parse(event['body'], model=UploadAssetStage1NewRequestModel)
                logger.info(requestData)

                #ABAC Checks (skip AssetType check until stage2 where we know what it is)
                asset = {
                    "object__type": "asset",
                    "databaseId": requestData.databaseId,
                    "assetName": requestData.assetDataDetails.assetName,
                    "tags": requestData.assetDataDetails.tags,
                }
                for user_name in claims_and_roles["tokens"]:
                    casbin_enforcer = CasbinEnforcer(user_name)
                    if casbin_enforcer.enforce(f"user::{user_name}", asset, http_method) and casbin_enforcer.enforceAPI(event):
                        operation_allowed_on_asset = True
                        break

                if operation_allowed_on_asset:
                    returnResponse = createupdate_asset_stage1(requestData, None)
                    return success(body={'message': returnResponse})

            elif http_method == "PUT":
                logger.info("Stage 1 API Call - PUT - Update Existing Asset")
                requestData = parse(event['body'], model=UploadAssetStage1UpdateRequestModel)
                logger.info(requestData)

                #Lookup existing asset
                asset_object = get_asset_object_from_id(requestData.assetId)

                #Raise error if no asset found
                if not asset_object:
                    raise VAMSGeneralErrorResponse("Asset Record not found for AssetId provided")

                #ABAC Checks
                for user_name in claims_and_roles["tokens"]:
                    casbin_enforcer = CasbinEnforcer(user_name)
                    if casbin_enforcer.enforce(f"user::{user_name}", asset_object, http_method) and casbin_enforcer.enforceAPI(event):
                        operation_allowed_on_asset = True
                        break

                if operation_allowed_on_asset:
                    returnResponse = createupdate_asset_stage1(None, requestData)
                    return success(body={'message': returnResponse})

        else:
            logger.info("Stage 2 API Call - Upload ID Provided (same across PUT/POST)")

            requestData = parse(event['body'], model=UploadAssetStage2RequestModel)
            logger.info(requestData)

            #Lookup existing uploaded asset details (also does check if asset upload exists)
            asset_upload_object = get_uploaded_asset_details(requestData.uploadId)

            #Lookup existing asset
            asset_object = get_asset_object_from_id(asset_upload_object.assetId)

            #Asset ID should exist but let's check anyway
            if not asset_object:
                raise VAMSGeneralErrorResponse("Asset Record not found for AssetId provided at stage 1 upload.")

            #ABAC Checks (Use uploaded data or existing asset data depending on upload type)
            asset_object = {
                "object__type": "asset",
                "assetName": asset_upload_object.assetDataDetails.assetName if asset_upload_object.assetDataDetails.assetName else asset_object.get("assetName"),
                "databaseId": asset_upload_object.databaseId,
                "assetType": asset_upload_object.assetDataDetails.assetType if asset_upload_object.assetDataDetails.assetName else asset_object.get("assetType"), 
                "tags": asset_upload_object.assetDataDetails.tags if asset_upload_object.assetDataDetails.tags else asset_object.get("tags"),
            }
            for user_name in claims_and_roles["tokens"]:
                casbin_enforcer = CasbinEnforcer(user_name)
                if casbin_enforcer.enforce(f"user::{user_name}", asset_object, http_method) and casbin_enforcer.enforceAPI(event):
                    operation_allowed_on_asset = True
                    break

            if operation_allowed_on_asset:
                returnResponse = complete_asset_stage2(requestData, asset_upload_object)
                return success(body={'message': returnResponse})

        #If we made it to this point, check auth flag
        if operation_allowed_on_asset == False:
            return authorization_error()
        
        #Return a internal error if we made it to this point
        return internal_error()


    except ValidationError as v:
        logger.error(v)
        return validation_error(body={
            'message': str(v)
        })
    except VAMSGeneralErrorResponse as ve:
        logger.error(ve)
        return general_error(body={
            'message': str(ve)
        })
    except Exception as e:
        logger.exception(e)
        return internal_error()
