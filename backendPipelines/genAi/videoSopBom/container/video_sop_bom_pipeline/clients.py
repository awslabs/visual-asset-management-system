# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The AWS clients the container uses, built once and handed to `run()` so tests can substitute fakes."""

from dataclasses import dataclass, field

import boto3
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline container
# runs against throttling-prone services (Step Functions, Amazon S3, Amazon Transcribe) for the length
# of a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

# Bedrock inference calls run for up to 60 minutes and AWS recommends read_timeout >= 3600; two SDK
# attempts so one hung call cannot consume the 6 h attempt bound before the pipeline's own budget sees it.
bedrock_config = Config(read_timeout=3600, connect_timeout=10, retries={'max_attempts': 2, 'mode': 'adaptive'})

# Preflight probes must surface a missing interface endpoint within seconds, not after five retries:
# max_attempts counts retries after the first request in every botocore mode, so this is at most two
# 5 s connect attempts (botocore reads it back as total_max_attempts 2).
preflight_config = Config(connect_timeout=5, read_timeout=30, retries={'max_attempts': 1, 'mode': 'standard'})

# The SIGTERM handler has 30 s before SIGKILL, so its Step Functions client cannot sit on the default
# 60 s read timeout; at most two short attempts fit the grace window.
signal_config = Config(connect_timeout=3, read_timeout=5, retries={'max_attempts': 1, 'mode': 'standard'})


@dataclass
class Clients:
    s3: object
    transcribe: object
    bedrock: object
    sfn: object
    preflight_transcribe: object
    preflight_bedrock: object
    sfn_signal: object
    # Bucket name -> Region for the asset buckets this job reads and writes (the definition's
    # `bucketRegions`, built by constructPipeline from the manifest); a bucket absent from it is in the
    # deployment Region and uses `s3`. Filled by register_bucket_regions, read by s3_for_bucket.
    bucket_regions: dict = field(default_factory=dict)
    s3_by_region: dict = field(default_factory=dict)

    def register_bucket_regions(self, bucket_regions):
        """Record the Region of each named bucket; empty names or Regions are ignored."""
        for bucket_name, region in (bucket_regions or {}).items():
            if bucket_name and region:
                self.bucket_regions[bucket_name] = region

    def s3_for_bucket(self, bucket_name):
        """The S3 client that addresses one bucket: a client for its registered Region, built once per
        Region, or `s3` for a bucket in the deployment Region (the auxiliary bucket, an unregistered
        name, a definition from an older Lambda). A bucket in another Region answers only requests
        signed for it."""
        region = self.bucket_regions.get(bucket_name)
        default_region = getattr(getattr(self.s3, "meta", None), "region_name", None)
        if not region or region == default_region:
            return self.s3
        regional = self.s3_by_region.get(region)
        if regional is None:
            regional = boto3.client("s3", region_name=region, config=regional_s3_config)
            self.s3_by_region[region] = regional
        return regional


# Per-Region clients keep the adaptive retry mode and address the regional us-east-1 endpoint, which
# an interface endpoint's private DNS covers where the global s3.amazonaws.com name is not.
regional_s3_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'},
                            s3={'us_east_1_regional_endpoint': 'regional'})


def build_clients(region):
    """Every client the stages need: adaptive retries by default, a long read timeout for Bedrock, and
    short-timeout clients allowing one retry (max_attempts 1, two attempts) for the preflight probes and
    the SIGTERM signal path."""
    return Clients(
        s3=boto3.client("s3", region_name=region, config=retry_config),
        transcribe=boto3.client("transcribe", region_name=region, config=retry_config),
        bedrock=boto3.client("bedrock-runtime", region_name=region, config=bedrock_config),
        sfn=boto3.client("stepfunctions", region_name=region, config=retry_config),
        preflight_transcribe=boto3.client("transcribe", region_name=region, config=preflight_config),
        preflight_bedrock=boto3.client("bedrock-runtime", region_name=region, config=preflight_config),
        sfn_signal=boto3.client("stepfunctions", region_name=region, config=signal_config),
    )
