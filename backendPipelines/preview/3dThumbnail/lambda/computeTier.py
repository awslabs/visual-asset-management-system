#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The compute tier a 3D thumbnail run is submitted on.

The pipeline's state machine carries two AWS Batch states on one Fargate job definition, a standard
tier and a large tier, each with fixed vCPU and memory overrides, behind a Choice on the run's
`computeTier`. The input file's size picks the tier: at or above the large-tier threshold the run is
large, otherwise standard. The run's input configuration (the template's `computeTier` field) forces
either tier; `auto` leaves the choice to the size.

An asset bucket may be in another Region than this function: the HeadObject on the input and the read
of the input configuration each use the client for the Region of the bucket they address, from the
event's `inputBucketRegion` / `outputBucketRegion`.

Selection never fails a run. An input whose size cannot be read runs on the standard tier."""

import os

import manifestHelper
from customLogging.logger import safeLogger

logger = safeLogger(service="ComputeTier3dThumbnail")

TIER_STANDARD = "standard"
TIER_LARGE = "large"
TIER_AUTO = "auto"
TIERS = (TIER_STANDARD, TIER_LARGE)

# The input-configuration key that forces a tier.
COMPUTE_TIER_PARAMETER = "computeTier"

# The size at which an input runs on the large tier when the deployment sets none.
DEFAULT_LARGE_TIER_THRESHOLD_BYTES = 2 * 1024 ** 3


def large_tier_threshold_bytes(value=None):
    """The large-tier threshold in bytes, from `value` or the LARGE_TIER_THRESHOLD_BYTES environment
    variable; the default when neither is a positive integer."""
    raw = value if value is not None else os.environ.get("LARGE_TIER_THRESHOLD_BYTES", "")
    try:
        threshold = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LARGE_TIER_THRESHOLD_BYTES
    return threshold if threshold > 0 else DEFAULT_LARGE_TIER_THRESHOLD_BYTES


def requested_tier(parameters):
    """The tier `parameters` (a parsed input configuration) forces, or "" when it names none,
    names `auto`, or names a value that is not a tier."""
    if not isinstance(parameters, dict) or parameters.get(COMPUTE_TIER_PARAMETER) is None:
        return ""
    value = parameters.get(COMPUTE_TIER_PARAMETER)
    tier = value.strip().lower() if isinstance(value, str) else ""
    if tier in TIERS:
        return tier
    if tier != TIER_AUTO:
        logger.warning(f"{COMPUTE_TIER_PARAMETER} {str(value)[:32]!r} is not one of "
                       f"{TIER_AUTO}, {TIER_STANDARD}, {TIER_LARGE}; choosing by input size")
    return ""


def bucket_regions(event):
    """Bucket name -> Region for the input and output asset buckets `event` names, from the
    `inputBucketRegion` / `outputBucketRegion` the vamsExecute Lambda resolved from the manifest (the
    same map constructPipeline hands the container). A bucket in the deployment Region (an empty
    Region, or an event from an older Lambda) is left out, as is a value that is not a string."""
    event = event or {}
    regions = {}
    for path_key, region_key in (("inputS3AssetFilePath", "inputBucketRegion"),
                                 ("outputS3AssetFilesPath", "outputBucketRegion")):
        uri, region = event.get(path_key), event.get(region_key)
        if not isinstance(uri, str) or not isinstance(region, str) or not region:
            continue
        bucket, _ = manifestHelper.parse_s3_uri(uri)
        if bucket:
            regions[bucket] = region
    return regions


def s3_client_for(s3_client, regions, s3_uri):
    """The client for the object `s3_uri` names: manifestHelper's client for its bucket's Region when
    `regions` maps the bucket, else `s3_client` (the deployment Region). A bucket in another Region
    answers only requests signed for that Region."""
    bucket, _ = manifestHelper.parse_s3_uri(s3_uri)
    region = regions.get(bucket, "") if bucket else ""
    return manifestHelper.s3_client_for_region(region) if region else s3_client


def input_size_bytes(s3_client, input_s3_uri):
    """The input object's size from HeadObject. Raises when the URI names no object or the call fails."""
    bucket, key = manifestHelper.parse_s3_uri(input_s3_uri)
    if not bucket or not key:
        raise ValueError("the input path names no S3 object")
    return int(s3_client.head_object(Bucket=bucket, Key=key)["ContentLength"])


def select_compute_tier(s3_client, event, threshold_bytes):
    """The tier for the run `event` describes: the tier its input configuration forces, else the one
    the input's size falls in. Each object is read with the client for its bucket's Region
    (`bucket_regions`); `s3_client` serves the buckets in the deployment Region."""
    event = event or {}
    regions = bucket_regions(event)
    configuration_location = event.get("inputConfigurationS3Location", "")
    try:
        configuration = manifestHelper.fetch_input_configuration(
            s3_client_for(s3_client, regions, configuration_location), configuration_location)
    except Exception as e:
        logger.warning(f"Input configuration unreadable for compute tier selection; "
                       f"choosing by input size: {e}")
        configuration = {}

    tier = requested_tier(configuration)
    if tier:
        logger.info(f"Compute tier {tier}: set by the input configuration")
        return tier

    input_path = event.get("inputS3AssetFilePath", "")
    try:
        size = input_size_bytes(s3_client_for(s3_client, regions, input_path), input_path)
    except Exception as e:
        logger.warning(f"Could not read the input size; using the {TIER_STANDARD} compute tier: {e}")
        return TIER_STANDARD

    tier = TIER_LARGE if size >= threshold_bytes else TIER_STANDARD
    logger.info(f"Compute tier {tier}: input is {size} bytes, large-tier threshold {threshold_bytes}")
    return tier


def submitted_tier(value):
    """The tier the state machine routes on for a `computeTier` value carried in its input: the value
    itself when it is a tier, else the standard tier."""
    tier = value.strip().lower() if isinstance(value, str) else ""
    if tier in TIERS:
        return tier
    logger.warning(f"computeTier {str(value)[:32]!r} is not a tier; using the {TIER_STANDARD} tier")
    return TIER_STANDARD
