#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore.exceptions import ClientError
from common.validators import validate
from customLogging.logger import safeLogger
from common.constants import UNALLOWED_MIME_LIST, UNALLOWED_FILE_EXTENSION_LIST

logger = safeLogger(service_name="S3Common")
s3c = boto3.client('s3')

# Per-call S3 list page sizes. These are pagination batch sizes (round-trip
# tuning), not result caps — the helpers below page to exhaustion.
S3_VERSIONS_PAGE_SIZE = 1000
S3_OBJECTS_PAGE_SIZE = 1000


def is_object_version_archived(bucket: str, key: str, version_id: str = None, client=None) -> bool:
    """Determine whether an S3 object (or a specific version) is archived.

    Uses a single head_object call rather than listing versions, which is O(1)
    regardless of how many versions the key has:
      - With version_id: heads that exact version. A delete marker returns
        405 MethodNotAllowed (archived=True); a live version returns 200
        (archived=False); a missing version returns 404 (archived=False).
      - Without version_id: heads the current version. The DeleteMarker flag (or
        a 404 NoSuchKey when only delete markers remain) indicates archived state.

    Args:
        bucket: The S3 bucket name
        key: The S3 object key
        version_id: Optional specific version ID to check
        client: Optional boto3 S3 client (defaults to the module client)

    Returns:
        True if the object/version is archived (delete marker), False otherwise.
        Best-effort: returns False on unexpected errors.
    """
    s3 = client or s3c
    try:
        if version_id:
            try:
                s3.head_object(Bucket=bucket, Key=key, VersionId=version_id)
                return False  # Version exists and is not a delete marker
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code')
                if error_code == 'MethodNotAllowed':
                    return True  # This version is a delete marker
                if error_code in ('NoSuchKey', '404', 'NotFound'):
                    return False  # Version doesn't exist
                raise
        else:
            try:
                response = s3.head_object(Bucket=bucket, Key=key)
                return response.get('DeleteMarker', False)
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code')
                if error_code in ('NoSuchKey', '404', 'NotFound'):
                    # Current object missing; archived only if a delete marker exists.
                    versions_response = s3.list_object_versions(Bucket=bucket, Prefix=key, MaxKeys=1)
                    return len(versions_response.get('DeleteMarkers', [])) > 0
                raise
    except Exception as e:
        logger.warning(f"Error checking archive status for {key}: {e}")
        return False


def list_all_object_versions(bucket: str, prefix: str, client=None, max_keys: int = None,
                             key_marker: str = None, version_id_marker: str = None) -> dict:
    """List object versions and delete markers under a prefix.

    Pages through list_object_versions via KeyMarker/VersionIdMarker until the
    listing is exhausted (or until max_keys results are collected), so callers
    never miss versions beyond a single page. A start marker can be supplied to
    begin listing partway through a key's version history.

    Args:
        bucket: The S3 bucket name
        prefix: The S3 key prefix to list versions for
        client: Optional boto3 S3 client (defaults to the module client). Pass a
            retry-configured client to inherit the caller's retry behavior.
        max_keys: Optional cap on the total number of versions + delete markers to
            collect. When set, listing stops once this many entries are gathered.
        key_marker: Optional KeyMarker to start listing after (S3 is exclusive of
            the marker).
        version_id_marker: Optional VersionIdMarker to start listing after within
            key_marker's versions (S3 is exclusive of the marker).

    Returns:
        Dict with aggregated 'Versions' and 'DeleteMarkers' lists.
    """
    s3 = client or s3c
    versions = []
    delete_markers = []
    list_kwargs = {'Bucket': bucket, 'Prefix': prefix, 'MaxKeys': S3_VERSIONS_PAGE_SIZE}
    if key_marker is not None:
        list_kwargs['KeyMarker'] = key_marker
    if version_id_marker is not None:
        list_kwargs['VersionIdMarker'] = version_id_marker

    while True:
        response = s3.list_object_versions(**list_kwargs)
        versions.extend(response.get('Versions', []))
        delete_markers.extend(response.get('DeleteMarkers', []))

        if max_keys is not None and (len(versions) + len(delete_markers)) >= max_keys:
            break

        if response.get('IsTruncated'):
            list_kwargs['KeyMarker'] = response.get('NextKeyMarker')
            list_kwargs['VersionIdMarker'] = response.get('NextVersionIdMarker')
        else:
            break

    return {'Versions': versions, 'DeleteMarkers': delete_markers}


def list_all_objects(bucket: str, prefix: str, client=None, max_objects: int = None) -> list:
    """List objects under a prefix, paging to exhaustion (or up to max_objects).

    Args:
        bucket: The S3 bucket name
        prefix: The S3 key prefix to list
        client: Optional boto3 S3 client (defaults to the module client).
        max_objects: Optional cap on the number of objects returned. When set,
            listing stops once this many objects are collected. Use for
            best-effort, non-critical reads (e.g. classification/sampling) where
            scanning an entire large asset would add unnecessary latency.

    Returns:
        List of S3 object dicts (as returned in 'Contents'), capped at max_objects
        when provided.
    """
    s3 = client or s3c
    objects = []
    pagination_config = {'PageSize': S3_OBJECTS_PAGE_SIZE}
    if max_objects is not None:
        pagination_config['MaxItems'] = max_objects
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(
        Bucket=bucket, Prefix=prefix,
        PaginationConfig=pagination_config
    ):
        objects.extend(page.get('Contents', []))
        if max_objects is not None and len(objects) >= max_objects:
            return objects[:max_objects]
    return objects

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

def validateS3AssetExtensionsAndContentType(bucket: str, prefixKey: str):
    #Get list of all objects in a particular S3 key/prefix, paging to exhaustion so every object
    #under the prefix is inspected (a single page would leave objects past the first 1,000
    #unvalidated while callers still ingest them).
    objects = list_all_objects(bucket, prefixKey)

    #Check for each returned object if it is a valid asset based on ContentType
    #Check for all malicious executable MIME types. A prefix can hold many thousands of objects, so
    #the per-object head response is not logged; validateUnallowedFileExtensionAndContentType logs
    #the offending key when a check fails.
    for obj in objects:
        respHeader = s3c.head_object(Bucket=bucket, Key=obj['Key'])
        if not validateUnallowedFileExtensionAndContentType(obj['Key'], respHeader['ContentType']):
            return False
    return True