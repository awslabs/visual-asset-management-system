# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import threading

import boto3
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig
from .logging import get_logger

logger = get_logger()

client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
s3 = boto3.resource("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))


def download(bucket_name, object_key, file_path):
    logger.info(
        "Downloading Object from S3 Bucket. Bucket: {}, Object: {}, File Path: {}".format(
            bucket_name, object_key, file_path
        )
    )
    try:
        with open(file_path, "wb") as data:
            client.download_fileobj(bucket_name, object_key, data)
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
        s3.meta.client.upload_file(
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
        response = client.head_object(Bucket=bucket_name, Key=object_key)
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
        response = client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        keys = [obj["Key"] for obj in response.get("Contents", [])]
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
