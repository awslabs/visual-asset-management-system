# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import threading
from botocore.exceptions import ClientError
from ..logging import log
from boto3.s3.transfer import TransferConfig
from ..pipeline.extensions import split_large_file

logger = log.get_logger()

client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
s3 = boto3.resource('s3', region_name=os.getenv("AWS_REGION", "us-east-1"))


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
        logger.error(e)
        return None
    return file_path


def uploadV2(bucket_name, object_key, file_path):
    logger.info(
        f"Uploading Object to S3 Bucket w/ auto chunking for multi-part.\nBucket:{bucket_name}.\n:Object: {object_key}"
    )

   # Multipart upload
    try:
        GB = 1024 ** 3
        MB = 1024 ** 2
        config = TransferConfig(multipart_threshold=1*GB, max_concurrency=10,
                                multipart_chunksize=100*MB, use_threads=True
                                )
        s3.meta.client.upload_file(file_path, bucket_name, object_key,
                                   ExtraArgs={},
                                   Config=config,
                                   Callback=ProgressPercentage(file_path)
                                   )
    except ClientError as e:
        logger.error(e)
        return None
    return object_key


def upload(bucket_name, object_key, file_path):
    logger.info(
        f"Uploading Object to S3 Bucket.\nBucket:{bucket_name}.\n:Object: {object_key}"
    )

    # Check if our file is larger than 1GB; If so, split it up and upload each part otherwise upload as is
    if os.path.getsize(file_path) > 1000000000:
        logger.info(
            f"Splitting Large File to S3 Multi-part upload: {file_path}")
        split_large_file(file_path)

        try:
            # Create Multipart Upload
            multipart_upload = client.create_multipart_upload(
                Bucket=bucket_name,
                Key=object_key,
            )

            # Upload each part of the file
            uploadedS3Parts = []
            part_number = 1
            for part in split_large_file(file_path):
                logger.info(f"Uploading Multi-part Split File: {part}")

                uploadPart = client.MultipartUploadPart(
                    bucket_name, object_key, multipart_upload['UploadId'], part_number
                )

                uploadPartResponse = uploadPart.upload(
                    Body=part,
                )

                uploadedS3Parts.append({
                    'PartNumber': part_number,
                    'ETag': uploadPartResponse['ETag']
                })

                part_number = part_number + 1

            # Complete Multipart Upload
            completeResult = client.complete_multipart_upload(
                Bucket=bucket_name,
                Key=object_key,
                MultipartUpload={
                    'Parts': uploadedS3Parts
                },
                UploadId=multipart_upload['UploadId'],
            )
        except ClientError as e:
            logger.error(e)
            return None
        return object_key

    else:
        try:
            with open(file_path, "rb") as data:
                client.upload_fileobj(data, bucket_name, object_key)
        except ClientError as e:
            logger.error(e)
            return None
        return object_key


def exists(bucket_name, object_key):
    logger.info(
        f"Checking if object exists in S3 Bucket\nBucket:{bucket_name}.\n:Object: {object_key}"
    )
    try:
        client.head_object(Bucket=bucket_name, Key=object_key)
    except ClientError:
        return False
    return True


def delete(bucket_name, object_key):
    logger.info(
        f"Deleting Object from S3 Bucket\nBucket:{bucket_name}.\n:Object: {object_key}"
    )
    try:
        client.delete_object(Bucket=bucket_name, Key=object_key)
    except ClientError as e:
        logger.error(e)
        return None
    return object_key


# Class for multipart upload
class ProgressPercentage(object):
    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        # To simplify we'll assume this is hooked up
        # to a single filename.
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
            # sys.stdout.write(
            #     "\r%s  %s / %s  (%.2f%%)" % (
            #         self._filename, self._seen_so_far, self._size,
            #         percentage))
            # sys.stdout.flush()
