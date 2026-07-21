"""
FMM Compliance Output Utility

Provides a standard interface for VAMS pipelines to write compliance
measurement output that the FMM pipeline callback handler can consume.

Usage in a pipeline Lambda or container:

    from common.compliance_output import write_compliance_output

    measurements = {
        "residual_error_mm": 0.42,
        "scale_deviation_ppm": 0.8,
    }
    write_compliance_output(
        s3_client=s3_client,
        bucket=output_bucket,
        output_metadata_path=data["outputS3AssetMetadataPath"],
        evaluation_id=fmm_context.get("evaluationId"),
        database_id=data["databaseId"],
        asset_id=data["assetId"],
        measurements=measurements,
        status="success",
    )

The output file (compliance-output.json) follows this schema:

    {
        "complianceOutput": true,
        "status": "success" | "error",
        "measurements": {
            "<outputField>": <numeric_value>,
            ...
        },
        "errors": [],
        "pipelineMetadata": {}
    }

The "measurements" keys must match the "outputField" values defined in the
compliance schema's pipeline rule checks. The FMM callback handler compares
each measurement against the tolerance defined in the schema.
"""

import json
import os
from typing import Any, Dict, List, Optional


def write_compliance_output(
    s3_client,
    bucket: str,
    output_metadata_path: str,
    evaluation_id: str,
    database_id: str,
    asset_id: str,
    measurements: Dict[str, Any],
    status: str = "success",
    errors: Optional[List[str]] = None,
    pipeline_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Write a compliance-output.json file to S3.

    Writes to TWO locations for redundancy:
    1. The standard metadata output path (picked up by process-output step)
    2. A well-known compliance path (direct lookup by FMM callback)

    Args:
        s3_client: boto3 S3 client
        bucket: S3 bucket name (from event's bucketAsset)
        output_metadata_path: S3 URI or key prefix for metadata outputs
        evaluation_id: FMM evaluation ID from fmmContext
        database_id: VAMS database ID
        asset_id: VAMS asset ID
        measurements: Dict of output field names to numeric values
        status: "success" or "error"
        errors: List of error messages (when status is "error")
        pipeline_metadata: Optional additional metadata about the pipeline run

    Returns:
        S3 key where the primary compliance output was written
    """
    output = {
        "complianceOutput": True,
        "status": status,
        "measurements": measurements,
        "errors": errors or [],
        "pipelineMetadata": pipeline_metadata or {},
    }
    body = json.dumps(output, indent=2)

    metadata_bucket, metadata_key_prefix = _parse_s3_path(
        output_metadata_path, bucket
    )
    primary_key = f"{metadata_key_prefix}compliance-output.json"
    s3_client.put_object(
        Bucket=metadata_bucket,
        Key=primary_key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )

    if evaluation_id:
        fallback_key = (
            f"compliance/{database_id}/{asset_id}/"
            f"{evaluation_id}/compliance-output.json"
        )
        s3_client.put_object(
            Bucket=bucket,
            Key=fallback_key,
            Body=body.encode("utf-8"),
            ContentType="application/json",
        )

    return primary_key


def parse_fmm_context(event_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract FMM context from pipeline event data if present.

    The FMM context is embedded in the inputMetadata JSON field by the
    evaluation engine. Falls back to checking a top-level fmmContext field.

    Returns None if this is not an FMM-triggered execution.
    """
    input_metadata_raw = event_data.get("inputMetadata", "")
    if input_metadata_raw:
        if isinstance(input_metadata_raw, str):
            try:
                input_metadata = json.loads(input_metadata_raw)
            except (json.JSONDecodeError, TypeError):
                input_metadata = {}
        elif isinstance(input_metadata_raw, dict):
            input_metadata = input_metadata_raw
        else:
            input_metadata = {}

        fmm_context = input_metadata.get("fmmContext")
        if fmm_context and isinstance(fmm_context, dict):
            return fmm_context

    fmm_context_raw = event_data.get("fmmContext")
    if not fmm_context_raw:
        return None
    if isinstance(fmm_context_raw, str):
        try:
            return json.loads(fmm_context_raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if isinstance(fmm_context_raw, dict):
        return fmm_context_raw
    return None


def _parse_s3_path(path: str, default_bucket: str) -> tuple:
    """Parse an S3 URI or key prefix into (bucket, key_prefix)."""
    if path.startswith("s3://"):
        without_scheme = path[5:]
        parts = without_scheme.split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
    else:
        bucket = default_bucket
        key = path

    if not key.endswith("/"):
        key += "/"
    return bucket, key
