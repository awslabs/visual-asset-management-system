#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Miris stream metadata for the source USD file.

The process-output step applies a ``<relativeFilePath>.metadata.json`` object found under the run's
metadata output path to that asset file, so the object key is the USD's asset-relative path plus the
``.metadata.json`` suffix. Kept free of boto3/pxr imports so it can be tested in isolation.
"""
import datetime

MIRIS_ASSET_UUID_KEY = "miris_asset_uuid"
MIRIS_SOURCE_VERSION_ID_KEY = "miris_source_version_id"
MIRIS_UPLOADED_AT_KEY = "miris_uploaded_at"
MIRIS_DISPLAY_NAME_KEY = "miris_display_name"
MIRIS_DEPENDENCY_MANIFEST_KEY = "miris_dependency_manifest"

METADATA_FILE_SUFFIX = ".metadata.json"


def metadata_output_location(stage: dict) -> tuple[str, str]:
    """Return (bucket, prefix) of the run's metadata output path; the prefix ends with '/'.

    Raises ValueError when the stage carries no usable metadata output path."""
    location = stage.get("outputMetadataFiles") or {}
    bucket = location.get("bucketName") or ""
    prefix = location.get("objectDir") or ""
    if not bucket or not prefix:
        raise ValueError(
            "Pipeline definition has no metadata output path (outputMetadataFiles); "
            "the Miris stream metadata for the source USD file cannot be written"
        )
    if not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def metadata_object_key(metadata_prefix: str, relative_path: str) -> str:
    """Key of the '.metadata.json' object that targets the asset file at `relative_path`."""
    return f"{metadata_prefix}{relative_path.lstrip('/')}{METADATA_FILE_SUFFIX}"


def format_uploaded_at(uploaded_at: datetime.datetime) -> str:
    """ISO 8601 UTC timestamp with second precision, e.g. '2026-10-14T09:30:00Z'."""
    utc = uploaded_at.astimezone(datetime.timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def build_stream_metadata_body(
    asset_uuid: str,
    source_version_id: str,
    display_name: str,
    uploaded_at: datetime.datetime,
    dependency_manifest: str = "",
) -> dict:
    """File-metadata body recording the Miris asset streamed from the USD file and the id of the
    dependency manifest of the files it was built from (dependency_manifest)."""
    return {
        "type": "metadata",
        "updateType": "update",
        "metadata": [
            {
                "metadataKey": MIRIS_ASSET_UUID_KEY,
                "metadataValue": asset_uuid,
                "metadataValueType": "string",
            },
            {
                "metadataKey": MIRIS_SOURCE_VERSION_ID_KEY,
                "metadataValue": source_version_id or "",
                "metadataValueType": "string",
            },
            {
                "metadataKey": MIRIS_UPLOADED_AT_KEY,
                "metadataValue": format_uploaded_at(uploaded_at),
                "metadataValueType": "date",
            },
            {
                "metadataKey": MIRIS_DISPLAY_NAME_KEY,
                "metadataValue": display_name,
                "metadataValueType": "string",
            },
            {
                "metadataKey": MIRIS_DEPENDENCY_MANIFEST_KEY,
                "metadataValue": dependency_manifest or "",
                "metadataValueType": "string",
            },
        ],
    }
