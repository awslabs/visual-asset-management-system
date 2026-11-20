# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import threading

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from ..logging import log
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = log.get_logger()

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


# Error codes Amazon S3 answers a read of a version that no longer exists with: NoSuchVersion or
# NoSuchKey (GET), 404 (HEAD of a deleted version), 405 / MethodNotAllowed (a delete marker).
_MISSING_VERSION_CODES = ("NoSuchVersion", "NoSuchKey", "404", "405", "MethodNotAllowed")


class InputVersionMissingError(Exception):
    """The input file version the run was launched on no longer exists."""

    def __init__(self, object_key, version_id):
        super().__init__(f"The input file {object_key} at version {version_id} no longer exists; "
                         f"it was deleted after the run started.")


def download(bucket_name: str, object_key: str, file_path: str, version_id: str = "") -> str | None:
    """Download an object from S3 to a local file path; ``version_id`` reads that version, empty the
    current one. Returns None when the download fails, and raises ``InputVersionMissingError`` when the
    requested version no longer exists."""
    logger.info(
        f"Downloading from S3. Bucket: {bucket_name}, "
        f"Key: {object_key}, Version: {version_id or 'current'}, Path: {file_path}"
    )
    kwargs = {"ExtraArgs": {"VersionId": version_id}} if version_id else {}
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as data:
            client_for_bucket(bucket_name).download_fileobj(bucket_name, object_key, data, **kwargs)
    except ClientError as e:
        if version_id and str(e.response.get("Error", {}).get("Code", "")) in _MISSING_VERSION_CODES:
            raise InputVersionMissingError(object_key, version_id) from e
        logger.exception(e)
        return None
    return file_path


def head(bucket_name: str, object_key: str, version_id: str = "") -> dict | None:
    """HeadObject for the object (at ``version_id`` when given); None when it cannot be read."""
    kwargs = {"VersionId": version_id} if version_id else {}
    try:
        return client_for_bucket(bucket_name).head_object(Bucket=bucket_name, Key=object_key, **kwargs)
    except ClientError as e:
        logger.exception(e)
        return None


def put_text(bucket_name: str, object_key: str, text: str) -> str | None:
    """Write a small UTF-8 document; None when the write fails."""
    logger.info(f"Writing to S3. Bucket: {bucket_name}, Key: {object_key}")
    try:
        client_for_bucket(bucket_name).put_object(
            Bucket=bucket_name, Key=object_key, Body=text.encode("utf-8"), ContentType="application/json")
    except ClientError as e:
        logger.exception(e)
        return None
    return object_key


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


class ProgressPercentage:
    """Upload progress, logged once each time another quarter of the file has gone (a callback arrives for every
    chunk, so testing the percentage alone logs the same line hundreds of times)."""

    def __init__(self, filename: str):
        self._filename = filename
        self._size = float(os.path.getsize(filename)) or 1.0
        self._seen_so_far = 0
        self._logged_quarter = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount: int) -> None:
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
            quarter = int(percentage // 25)
            if quarter > self._logged_quarter:
                self._logged_quarter = quarter
                logger.info(f"Upload progress: {self._filename} {min(quarter * 25, 100)}%")
