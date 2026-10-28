# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError
from vams_utils.logging import log

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline container
# runs against Amazon S3 for the length of a job, so a bare client leaves it on botocore's default mode
# with no rate limiting and a sustained burst surfaces as a throttling error instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = log.get_logger()

# The region comes from the execution environment, with no fallback: AWS_REGION is always set by
# the Batch runtime, and a literal default is a commercial region that does not exist in GovCloud
# or the EU Sovereign Cloud, so a fallback can only ever point somewhere wrong.
client = boto3.client("s3", config=retry_config)

# Multipart above 64 MiB in 64 MiB parts; the transfer manager splits, uploads and completes the parts.
_MiB = 1024 ** 2
_transfer_config = TransferConfig(multipart_threshold=64 * _MiB, multipart_chunksize=64 * _MiB,
                                  max_concurrency=10, use_threads=True)


# An asset bucket may be in another Region than this container. The pipeline definition carries a
# bucket name -> Region map (constructPipeline builds it from the manifest's bucket Regions), the
# entry point registers it, and every request for a registered bucket is signed with a client for
# that Region; the client above serves the buckets the map leaves out (the auxiliary bucket, a
# definition from an older Lambda). Per-Region clients keep the adaptive retry mode and address the
# regional us-east-1 endpoint, which an interface endpoint's private DNS covers where the global
# s3.amazonaws.com name is not.
_bucket_regions = {}
_clients_by_region = {}
_regional_retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'},
                                s3={'us_east_1_regional_endpoint': 'regional'})


def register_bucket_regions(bucket_regions):
    """Record the Region of each named bucket from a definition's bucketRegions map."""
    for bucket_name, region in (bucket_regions or {}).items():
        if bucket_name and region:
            _bucket_regions[bucket_name] = region


def client_for_bucket(bucket_name):
    """The S3 client for the Region a bucket was registered in; the default client otherwise."""
    region = _bucket_regions.get(bucket_name)
    if not region or region == client.meta.region_name:
        return client
    regional = _clients_by_region.get(region)
    if regional is None:
        regional = boto3.client("s3", region_name=region, config=_regional_retry_config)
        _clients_by_region[region] = regional
    return regional


def download(bucket_name, object_key, file_path):
    logger.info(
        "Downloading Object from S3 Bucket. Bucket: {}, Object: {}, File Path: {}".format(
            bucket_name, object_key, file_path
        )
    )
    try:
        with open(file_path, "wb") as data:
            client_for_bucket(bucket_name).download_fileobj(bucket_name, object_key, data,
                                                            Config=_transfer_config)
    except ClientError as e:
        logger.exception(e)
        return None
    return file_path


def upload(bucket_name, object_key, file_path):
    logger.info(
        f"Uploading Object to S3 Bucket.\nBucket:{bucket_name}.\n:Object: {object_key}"
    )
    try:
        client_for_bucket(bucket_name).upload_file(file_path, bucket_name, object_key,
                                                   Config=_transfer_config)
    except ClientError as e:
        logger.exception(e)
        return None
    return object_key


def exists(bucket_name, object_key):
    logger.info(
        f"Checking if object exists in S3 Bucket\nBucket:{bucket_name}.\n:Object: {object_key}"
    )
    try:
        client_for_bucket(bucket_name).head_object(Bucket=bucket_name, Key=object_key)
    except ClientError:
        return False
    return True


def delete(bucket_name, object_key):
    logger.info(
        f"Deleting Object from S3 Bucket\nBucket:{bucket_name}.\n:Object: {object_key}"
    )
    try:
        client_for_bucket(bucket_name).delete_object(Bucket=bucket_name, Key=object_key)
    except ClientError as e:
        logger.exception(e)
        return None
    return object_key


def delete_all_path_contents(bucket_name: str, pathKey: str):
    """
    Delete all objects in a bucket path
    """
    try:
        # Paginate the listing: a single page caps at 1,000 keys.
        object_keys = []
        paginator = client_for_bucket(bucket_name).get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=bucket_name, Prefix=pathKey):
            object_keys.extend(map_object_keys(page.get("Contents", [])))

        if object_keys:
            logger.info(f"Deleting {len(object_keys)} Other Objects")

            # delete objects from bucket path, in batches of the 1,000-key
            # delete_objects maximum
            for i in range(0, len(object_keys), 1000):
                client_for_bucket(bucket_name).delete_objects(
                    Bucket=bucket_name,
                    Delete={
                        'Objects': object_keys[i:i + 1000],
                    })
    except Exception as e:
        logger.exception(e)


# map object ket from object list for multi object delete request
def map_object_keys(objects) -> list:
    keys = []
    for o in objects:
        keys.append({
            'Key': o["Key"]
        })

    return keys


def get_all_files_in_path(bucket, path):
    result = {
        "Items": []
    }

    # Paginate: a single page caps at 1,000 keys, so an asset holding more files than
    # that would be only partially processed.
    paginator = client_for_bucket(bucket).get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=path):
        # map object from object list
        for o in page.get("Contents", []):
            result["Items"].append({
                'key': o['Key'],
                'relativePath': o['Key'].removeprefix(path)
            })

    # Log the length of files with a description
    logger.info("Files in the path: ")
    logger.info(len(result["Items"]))
    return result["Items"]
