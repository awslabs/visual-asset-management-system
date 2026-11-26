#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Builds the Batch job definition for the Miris upload container."""
import json
import os

from customLogging.logger import safeLogger

logger = safeLogger(service="ConstructMirisUploadPipeline")

MIRIS_API_BASE_URL = os.environ["MIRIS_API_BASE_URL"]
MIRIS_API_KEY_SECRET_ARN = os.environ["MIRIS_API_KEY_SECRET_ARN"]
MIRIS_UPLOAD_TASK_TIMEOUT_SECONDS = int(
    os.environ.get("MIRIS_UPLOAD_TASK_TIMEOUT_SECONDS", "1800")
)
MIRIS_UPLOAD_MAX_ASSET_SIZE_BYTES = int(
    os.environ.get("MIRIS_UPLOAD_MAX_ASSET_SIZE_BYTES", "5000000000")
)
# The extensions that trigger the pipeline; the container chooses the upload root among them.
ALLOWED_INPUT_FILEEXTENSIONS = os.environ.get("ALLOWED_INPUT_FILEEXTENSIONS", ".usd,.usda,.usdc,.usdz")


def _metadata_output_location(metadata_uri):
    """Split the run's metadata output path into (bucket, prefix ending in '/').

    The container writes the Miris stream metadata for the source USD file there; without
    it a completed Miris upload would leave nothing on the file, so the run fails here,
    before the Batch job is submitted."""
    bucket, _, prefix = metadata_uri.replace("s3://", "", 1).partition("/")
    if not metadata_uri.startswith("s3://") or not bucket or not prefix:
        raise ValueError(
            "outputS3AssetMetadataPath is missing or not an s3://bucket/prefix URI "
            f"(got {metadata_uri!r}); the Miris upload pipeline writes its stream "
            "metadata for the source USD file there"
        )
    if not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def _asset_root_location(asset_root_uri):
    """Split the asset root into (bucket, prefix ending in '/'). The container lists and downloads
    the asset from there, so a run without one fails here, before the Batch job is submitted."""
    bucket, _, prefix = (asset_root_uri or "").replace("s3://", "", 1).partition("/")
    if not (asset_root_uri or "").startswith("s3://") or not bucket or not prefix.strip("/"):
        raise ValueError(
            f"assetRootS3Path is missing or not an s3://bucket/prefix URI (got {asset_root_uri!r}); "
            "the Miris upload pipeline reads the asset's files from there"
        )
    if not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def _report_location(report_uri):
    """{bucketName, objectKey} of an s3:// report URI, or {} when it names no object."""
    bucket, _, key = (report_uri or "").replace("s3://", "", 1).partition("/")
    if not (report_uri or "").startswith("s3://") or not bucket or not key:
        return {}
    return {"bucketName": bucket, "objectKey": key}


def _bucket_of(s3_uri):
    """The bucket of an s3:// URI, or '' when it names none."""
    if not (s3_uri or "").startswith("s3://"):
        return ""
    return s3_uri[len("s3://"):].partition("/")[0]


def _nested_locations_uri(event):
    """The nested-locations object openPipeline passed on, or '' when there is none."""
    uri = event.get("nestedLocationsS3Uri")
    return uri if isinstance(uri, str) else ""


def lambda_handler(event, context):
    logger.info("ConstructMirisUploadPipeline received event")

    job_name = event.get("jobName")
    input_s3_uri = event.get("inputS3AssetFilePath", "")
    input_version_id = event.get("inputVersionId", "")
    aux_uri = event.get("inputOutputS3AssetAuxiliaryFilesPath", "")
    metadata_uri = event.get("outputS3AssetMetadataPath", "")
    asset_id = event.get("assetId", "")
    database_id = event.get("databaseId", "")
    claim_fingerprint = event.get("claimFingerprint", "")
    input_configuration_s3_location = event.get("inputConfigurationS3Location", "")
    miris_asset_name = event.get("mirisAssetName", "")
    report_uri = event.get("ambiguousRootReportS3Uri", "") or ""

    input_bucket, input_key = input_s3_uri.replace("s3://", "").split("/", 1)
    metadata_bucket, metadata_key = _metadata_output_location(metadata_uri)
    asset_root_bucket, asset_root_prefix = _asset_root_location(event.get("assetRootS3Path", ""))

    _, extension = os.path.splitext(input_key)

    definition = {
        "jobName": job_name,
        "stages": [
            {
                "type": "MIRIS_UPLOAD",
                "assetId": asset_id,
                "databaseId": database_id,
                "triggerInput": {
                    "bucketName": input_bucket,
                    "objectKey": input_key,
                    "fileExtension": extension,
                    # The version the run recorded at launch ("" for none); the container reads the
                    # selected file at it, and the other layers at their current version.
                    "versionId": input_version_id,
                },
                "assetRoot": {
                    "bucketName": asset_root_bucket,
                    "objectDir": asset_root_prefix,
                },
                "rootExtensions": [
                    ext.strip().lower()
                    for ext in ALLOWED_INPUT_FILEEXTENSIONS.split(",")
                    if ext.strip()
                ],
                "outputMetadataFiles": {
                    "bucketName": metadata_bucket,
                    "objectDir": metadata_key,
                },
                # Where the container writes the dependency manifest the stream metadata names.
                "auxiliaryBucketName": _bucket_of(aux_uri),
                "mirisApiBaseUrl": MIRIS_API_BASE_URL,
                "mirisApiKeySecretArn": MIRIS_API_KEY_SECRET_ARN,
                "taskTimeoutSeconds": MIRIS_UPLOAD_TASK_TIMEOUT_SECONDS,
                "maxAssetSizeBytes": MIRIS_UPLOAD_MAX_ASSET_SIZE_BYTES,
                "inputConfigurationS3Location": input_configuration_s3_location,
                "mirisAssetName": miris_asset_name,
                # The root is the selected file on a manual run, and mirisRootFile (the asset
                # metadata miris_root_file) on an automatic run when it is set.
                "manualRun": event.get("manualRun") is True,
                "mirisRootFile": event.get("mirisRootFile", "") or "",
                # The object listing the assets stored inside the asset root, whose files the run
                # leaves out; {} when there are none.
                "nestedLocations": _report_location(_nested_locations_uri(event)),
                # Where the container lists tied roots on an ambiguous_root failure.
                "ambiguousRootReport": _report_location(report_uri),
                # Where the container lists the pre-flight warnings of a run that uploads.
                "preflightWarningsReport": _report_location(
                    event.get("preflightWarningsReportS3Uri", "")),
            }
        ],
    }

    return {
        "jobName": job_name,
        "currentStageType": "MIRIS_UPLOAD",
        "definition": [json.dumps(definition)],
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        # The failure branch reads these off the state to release the gate's claim.
        "databaseId": database_id,
        "assetId": asset_id,
        "claimFingerprint": claim_fingerprint,
        "inputOutputS3AssetAuxiliaryFilesPath": aux_uri,
        "ambiguousRootReportS3Uri": report_uri,
        "status": "STARTING",
    }
