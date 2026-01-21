#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""S3 utilities for IsaacLab training pipeline."""

import os
import boto3
from urllib.parse import urlparse


class S3Client:
    def __init__(self):
        self.client = boto3.client("s3")

    def download_directory(self, s3_uri: str, local_path: str) -> None:
        """Download all objects from S3 prefix to local directory."""
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")

        os.makedirs(local_path, exist_ok=True)

        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                relative_path = key[len(prefix):].lstrip("/")
                
                # Handle single file case (relative_path is empty)
                if not relative_path:
                    relative_path = os.path.basename(key)
                
                local_file = os.path.join(local_path, relative_path)
                os.makedirs(os.path.dirname(local_file) or local_path, exist_ok=True)
                self.client.download_file(bucket, key, local_file)

    def upload_file(self, local_path: str, s3_uri: str) -> None:
        """Upload a file to S3."""
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        self.client.upload_file(local_path, bucket, key)

    def upload_directory(self, local_path: str, s3_uri: str) -> None:
        """Upload a directory to S3."""
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")

        for root, _, files in os.walk(local_path):
            for filename in files:
                local_file = os.path.join(root, filename)
                relative_path = os.path.relpath(local_file, local_path)
                s3_key = os.path.join(prefix, relative_path)
                self.client.upload_file(local_file, bucket, s3_key)
