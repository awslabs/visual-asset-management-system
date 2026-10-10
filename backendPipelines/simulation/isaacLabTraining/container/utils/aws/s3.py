#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""S3 utilities for IsaacLab training pipeline."""

import os
import boto3
from urllib.parse import urlparse
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})


# Per-Region clients address the regional us-east-1 endpoint, which an interface endpoint's private
# DNS covers where the global s3.amazonaws.com name is not.
_regional_retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'},
                                s3={'us_east_1_regional_endpoint': 'regional'})


class S3Client:
    """S3 access for the job. An asset bucket may be in another Region than this container: the job
    config carries a bucket name -> Region map (openPipeline builds it from the manifest's bucket
    Regions), `register_bucket_regions` records it, and every request for a registered bucket is
    signed with a client for that Region. `client` serves the buckets the map leaves out (a config
    from an older Lambda) in the job's own Region."""

    def __init__(self):
        self.client = boto3.client("s3", config=_regional_retry_config)
        self._bucket_regions = {}
        self._clients_by_region = {}

    def register_bucket_regions(self, bucket_regions) -> None:
        """Record the Region of each named bucket from the job config's bucketRegions map."""
        for bucket_name, region in (bucket_regions or {}).items():
            if bucket_name and region:
                self._bucket_regions[bucket_name] = region

    def client_for_bucket(self, bucket_name: str):
        """The S3 client for the Region a bucket was registered in; `client` otherwise."""
        region = self._bucket_regions.get(bucket_name)
        if not region or region == self.client.meta.region_name:
            return self.client
        regional = self._clients_by_region.get(region)
        if regional is None:
            regional = boto3.client("s3", region_name=region, config=_regional_retry_config)
            self._clients_by_region[region] = regional
        return regional

    def download_directory(self, s3_uri: str, local_path: str) -> None:
        """Download all objects from S3 prefix to local directory."""
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")

        os.makedirs(local_path, exist_ok=True)

        paginator = self.client_for_bucket(bucket).get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                relative_path = key[len(prefix):].lstrip("/")
                
                # Handle single file case (relative_path is empty)
                if not relative_path:
                    relative_path = os.path.basename(key)
                
                local_file = os.path.join(local_path, relative_path)
                os.makedirs(os.path.dirname(local_file) or local_path, exist_ok=True)
                self.client_for_bucket(bucket).download_file(bucket, key, local_file)

    def upload_file(self, local_path: str, s3_uri: str) -> None:
        """Upload a file to S3."""
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        self.client_for_bucket(bucket).upload_file(local_path, bucket, key)

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
                self.client_for_bucket(bucket).upload_file(local_file, bucket, s3_key)
