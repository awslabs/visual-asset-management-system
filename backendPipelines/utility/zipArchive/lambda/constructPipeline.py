# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""ConstructPipeline - zipArchive. Builds the Batch container's definition (its single command argument).

The definition stays small whatever the selection: a zip download names its request manifest, and an
zipArchive run names its run manifest, which the container reads itself, because AWS Batch bounds the size
of the job's command override.
"""

import json
import os

import boto3
from botocore.config import Config

import manifestHelper
from customLogging.logger import safeLogger

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="ConstructPipeline-ZipArchive")
s3 = boto3.client('s3', config=retry_config)
sfn = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'), config=retry_config)

# The zipArchive run configBody keys the container accepts (anything else is refused, so a typo does not
# silently fall back to a default).
ARCHIVE_CONFIG_KEYS = ("format", "compression", "outputFileName", "includePatterns", "excludePatterns",
                       "assetVersionId")
FORMATS = ("zip", "tar", "tar.gz", "tgz")


def abort_external_workflow(error, task_token):
    if not task_token:
        return
    try:
        sfn.send_task_failure(taskToken=task_token, error="ZipArchivePipelineError", cause=str(error)[:256])
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def _split_s3_uri(field_name, s3_uri):
    bucket, _, key = (s3_uri or "").replace("s3://", "").partition("/")
    if not bucket or not key:
        raise ValueError(f"{field_name} is not an s3://bucket/key location")
    return bucket, key


def archive_config(raw):
    """The validated zipArchive run configuration (configBody / inputParameters)."""
    config = dict(raw or {})
    unknown = sorted(set(config) - set(ARCHIVE_CONFIG_KEYS))
    if unknown:
        raise ValueError(f"Unknown archive configuration keys: {', '.join(unknown)}")
    if config.get("format", "zip") not in FORMATS:
        raise ValueError("format must be zip, tar or tar.gz")
    if config.get("compression", "store") not in ("store", "deflate"):
        raise ValueError("compression must be store or deflate")
    for field in ("includePatterns", "excludePatterns"):
        value = config.get(field)
        if value is not None and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
            raise ValueError(f"{field} must be a list of strings")
    if config.get("outputFileName") is not None and not isinstance(config["outputFileName"], str):
        raise ValueError("outputFileName must be a string")
    return config


def bucket_regions(event):
    regions = {}
    for path_key, region_key in (("outputS3AssetFilesPath", "outputBucketRegion"),):
        uri, region = event.get(path_key) or "", event.get(region_key) or ""
        if uri.startswith("s3://") and region:
            regions[uri[len("s3://"):].split("/", 1)[0]] = region
    return regions


def build_definition(event):
    definition = {
        "jobName": event.get("jobName"),
        "mode": event.get("mode"),
        "workflowExecutionId": event.get("workflowExecutionId", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "bucketRegions": bucket_regions(event),
    }
    if event.get("mode") == "zipDownload":
        zip_download = event.get("zipDownload") or {}
        for field in ("manifestBucket", "manifestKey", "requestId", "userId", "databaseId", "assetId"):
            if not zip_download.get(field):
                raise ValueError(f"zip download definition misses {field}")
        definition["zipDownload"] = {k: zip_download[k] for k in (
            "manifestBucket", "manifestKey", "requestId", "userId", "databaseId", "assetId")}
    elif event.get("mode") == "archiveRun":
        manifest_bucket, manifest_key = _split_s3_uri("runManifestS3Location", event.get("runManifestS3Location"))
        output_bucket, output_dir = _split_s3_uri("outputS3AssetFilesPath", event.get("outputS3AssetFilesPath"))
        config = archive_config(manifestHelper.fetch_input_configuration(
            s3, event.get("inputConfigurationS3Location", "")))
        definition["archiveRun"] = {
            "runManifest": {"bucket": manifest_bucket, "key": manifest_key},
            "assetId": event.get("assetId", ""),
            "databaseId": event.get("databaseId", ""),
            "outputBucket": output_bucket,
            "outputDir": output_dir,
            "config": config,
        }
        if event.get("inputBucketRegion"):
            # The run reads one asset; its bucket's Region comes from the manifest's first input file.
            definition["bucketRegions"][manifest_bucket] = event["inputBucketRegion"]
    else:
        raise ValueError("Unknown zipArchive mode")
    return definition


def lambda_handler(event, context):
    logger.info("Event", event=event)
    try:
        definition = build_definition(event)
        return {
            "jobName": event.get("jobName"),
            "currentStageType": "ARCHIVE",
            "definition": [json.dumps(definition)],
            "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
            "orchestrationEventPrefix": event.get("orchestrationEventPrefix", ""),
            "status": "STARTING",
        }
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get('externalSfnTaskToken', ''))
        raise
