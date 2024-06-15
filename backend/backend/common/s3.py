#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from common.validators import validate
from customLogging.logger import safeLogger
from common.constants import UNALLOWED_MIME_LIST, UNALLOWED_FILE_EXTENSION_LIST

logger = safeLogger(service_name="S3Common")
s3c = boto3.client('s3')

def validateUnallowedFileExtensionAndContentType(keyPath: str, contentType: str):
    #Check if the content type is in the list of unallowed MIME types
    if contentType in UNALLOWED_MIME_LIST:
        logger.error(f"Unallowed file content type detected in asset: {keyPath}")
        return False
    
    #check if the file extension of the keyPath is in the list of unallowed file extensions
    if os.path.splitext(keyPath)[1] and os.path.splitext(keyPath)[1] in UNALLOWED_FILE_EXTENSION_LIST:
        logger.error(f"Unallowed file extension detected in asset: {keyPath}")
        return False
    return True

def validateS3AssetExtensionsAndContentType(bucket: str, assetIdPrefixKey: str):
    #Get list of all objects in a particular S3 key/prefix
    resp = s3c.list_objects_v2(Bucket=bucket, Prefix=assetIdPrefixKey)
    logger.info(resp)

    #Check for each returned object if it is a valid asset based on ContentType
    #Check for all malicious executable MIME types
    if "Contents" in resp:
        for obj in resp['Contents']:
            respHeader = s3c.head_object(Bucket=bucket, Key=obj['Key'])
            logger.info(respHeader)
            if not validateUnallowedFileExtensionAndContentType(obj['Key'], respHeader['ContentType']):
                return False
    return True