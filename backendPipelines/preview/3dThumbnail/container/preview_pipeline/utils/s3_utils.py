# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import threading

import boto3
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig
from .logging import get_logger
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = get_logger()

client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"), config=retry_config)
s3 = boto3.resource("s3", region_name=os.getenv("AWS_REGION", "us-east-1"), config=retry_config)


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
            client_for_bucket(bucket_name).download_fileobj(bucket_name, object_key, data)
    except ClientError as e:
        logger.exception(e)
        return None
    return file_path


def upload(bucket_name, object_key, file_path):
    logger.info(
        f"Uploading Object to S3 Bucket w/ auto chunking for multi-part.\n"
        f"Bucket:{bucket_name}.\nObject: {object_key}"
    )

    try:
        GB = 1024 ** 3
        MB = 1024 ** 2
        config = TransferConfig(
            multipart_threshold=1 * GB,
            max_concurrency=10,
            multipart_chunksize=100 * MB,
            use_threads=True,
        )
        client_for_bucket(bucket_name).upload_file(
            file_path,
            bucket_name,
            object_key,
            ExtraArgs={},
            Config=config,
            Callback=ProgressPercentage(file_path),
        )
    except ClientError as e:
        logger.exception(e)
        return None
    return object_key


def get_object_size(bucket_name, object_key):
    """
    Get the size of an S3 object in bytes using HEAD request.
    Returns the size in bytes, or None if the object doesn't exist or on error.
    """
    logger.info(f"Getting object size: {bucket_name}/{object_key}")
    try:
        response = client_for_bucket(bucket_name).head_object(Bucket=bucket_name, Key=object_key)
        size = response["ContentLength"]
        logger.info(f"Object size: {size} bytes ({size / (1024**3):.2f} GB)")
        return size
    except ClientError as e:
        logger.exception(f"Failed to get object size: {e}")
        return None


def list_objects_with_prefix(bucket_name, prefix):
    """
    List S3 object keys matching a prefix.
    Returns a list of object key strings, or an empty list on error.
    """
    logger.info(f"Listing objects: {bucket_name}/{prefix}")
    try:
        # Paginate: a single page caps at 1,000 keys, so a prefix holding more
        # objects than that would be silently truncated.
        keys = []
        paginator = client_for_bucket(bucket_name).get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        logger.info(f"Found {len(keys)} objects with prefix: {prefix}")
        return keys
    except ClientError as e:
        logger.exception(f"Failed to list objects: {e}")
        return []


class ProgressPercentage(object):
    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
            logger.debug(
                f"Upload progress: {self._filename} {self._seen_so_far}/{self._size} ({percentage:.2f}%)"
            )
