# Copyright 2025 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import threading

import boto3
from boto3.exceptions import S3TransferFailedError
from botocore.exceptions import (
    ClientError,
    IncompleteReadError,
    ReadTimeoutError,
    ResponseStreamingError,
)
from boto3.s3.transfer import TransferConfig
from urllib3.exceptions import ProtocolError
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

MB = 1024 ** 2

# Bytes requested per read of a streamed object, and how often a dropped stream is reopened at the
# offset it reached before the read fails.
STREAM_CHUNK_BYTES = 16 * MB
STREAM_MAX_RESUMES = 5


def download_transfer_config(cpus: int = None) -> TransferConfig:
    """Parallel ranged GETs for the input download: 64 MiB parts, up to four per CPU (I/O bound),
    at most 32 at a time. ``cpus`` defaults to the task's CPUs (its cgroup quota)."""
    if not cpus:
        from ..resources import cpu_count
        cpus = cpu_count()
    cpus = max(1, int(cpus))
    return TransferConfig(
        multipart_threshold=64 * MB,
        multipart_chunksize=64 * MB,
        max_concurrency=min(32, 4 * cpus),
        use_threads=True,
    )


def download(bucket_name, object_key, file_path):
    logger.info(
        "Downloading Object from S3 Bucket. Bucket: {}, Object: {}, File Path: {}".format(
            bucket_name, object_key, file_path
        )
    )
    try:
        client.download_file(bucket_name, object_key, file_path, Config=download_transfer_config())
    except (ClientError, S3TransferFailedError) as e:
        logger.exception(e)
        return None
    return file_path


def iter_object_bytes(bucket_name, object_key, chunk_size: int = STREAM_CHUNK_BYTES,
                      max_resumes: int = STREAM_MAX_RESUMES):
    """Yield an object's bytes in chunks from one streaming GET, without a local copy.

    A stream that drops mid-way (a reset connection or a read timeout on a multi-GB object) is
    reopened with a ranged GET at the offset already delivered, up to ``max_resumes`` times, so the
    consumer sees each byte exactly once.
    """
    offset = 0
    resumes = 0
    while True:
        kwargs = {"Bucket": bucket_name, "Key": object_key}
        if offset:
            kwargs["Range"] = f"bytes={offset}-"
        body = client.get_object(**kwargs)["Body"]
        try:
            for chunk in body.iter_chunks(chunk_size):
                if chunk:
                    offset += len(chunk)
                    yield chunk
            return
        except (ReadTimeoutError, ProtocolError, ResponseStreamingError, IncompleteReadError,
                ConnectionError) as e:
            resumes += 1
            if resumes > max_resumes:
                raise
            logger.warning(f"Stream of {bucket_name}/{object_key} dropped at byte {offset} ({e}); "
                           f"resuming ({resumes}/{max_resumes})")
        finally:
            body.close()


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


def head_object_size(bucket_name, object_key):
    """The size in bytes of an object, or None when it does not exist or cannot be read.

    For optional objects (files an input references): a missing one is expected, so it is logged as
    a warning without a traceback.
    """
    try:
        return int(client.head_object(Bucket=bucket_name, Key=object_key)["ContentLength"])
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.warning(f"HeadObject {bucket_name}/{object_key} failed: {code or e}")
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
        paginator = client.get_paginator("list_objects_v2")
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
