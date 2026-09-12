# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The AWS clients the container uses, built once and handed to `run()` so tests can substitute fakes."""

from dataclasses import dataclass

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
