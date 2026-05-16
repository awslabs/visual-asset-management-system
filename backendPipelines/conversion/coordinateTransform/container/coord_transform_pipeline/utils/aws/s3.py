# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import threading

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from ..logging import log

logger = log.get_logger()

client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
s3 = boto3.resource("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))


def download(bucket_name: str, object_key: str, file_path: str) -> str | None:
    """Download an object from S3 to a local file path."""
    logger.info(
        f"Downloading from S3. Bucket: {bucket_name}, "
        f"Key: {object_key}, Path: {file_path}"
    )
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as data:
            client.download_fileobj(bucket_name, object_key, data)
    except ClientError as e:
        logger.exception(e)
        return None
    return file_path


def upload(bucket_name: str, object_key: str, file_path: str) -> str | None:
    """Upload a local file to S3 with automatic multipart for large files."""
    logger.info(
        f"Uploading to S3. Bucket: {bucket_name}, Key: {object_key}"
    )
    try:
        GB = 1024**3
        MB = 1024**2
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


class ProgressPercentage:
    def __init__(self, filename: str):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount: int) -> None:
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
            if int(percentage) % 25 == 0:
                logger.info(
                    f"Upload progress: {self._filename} "
                    f"{percentage:.1f}%"
                )
