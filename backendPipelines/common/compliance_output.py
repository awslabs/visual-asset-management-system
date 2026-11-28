#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance output helper for VAMS pipelines.

A pipeline that backs a compliance pipeline rule reports its measurements by writing ONE file,
``compliance-output.json``, under the execution's standard RESULTS output prefix (the
``outputs.results`` prefix of the workflow manifest, alongside the ``files`` / ``previews`` /
``metadata`` prefixes). The workflow end-state lambda records every file under that prefix as a
PipelineExecutionOutputResults row with its content, and the compliance workflow callback reads
the document from that row — so the pipeline never needs the evaluation id, the asset, or any
compliance table.

This file is the canonical copy, and a pipeline vendors it the way it vendors ``manifestHelper.py``:
copy it into the pipeline's own ``lambda/`` or ``container/`` directory and import it from there.
A pipeline's Lambda code asset and container build context hold only that directory, so this copy
is not importable from a deployed pipeline. Every copy stays byte-identical to this one
(``backendPipelines/tests/test_compliance_output_helper.py``). The module needs only the standard
library and the S3 client the caller passes in.

Usage in a pipeline Lambda or container, with the inputs resolved by
``manifestHelper.resolve_pipeline_inputs`` and the client for the output bucket's Region::

    import compliance_output
    import manifestHelper

    compliance_output.write_compliance_output(
        s3_client=manifestHelper.s3_client_for_region(resolved["outputBucketRegion"]),
        measurements={"residual_error_mm": 0.42, "scale_deviation_ppm": 0.8},
        results_s3_path=resolved["outputS3AssetResultsPath"],
    )

``manifest=`` (the parsed workflow manifest) can be passed instead of ``results_s3_path``.

Document schema::

    {
        "complianceOutput": true,
        "status": "success" | "error",
        "measurements": {"<outputField>": <numeric value>, ...},
        "errors": [],
        "pipelineMetadata": {}
    }

The ``measurements`` keys are matched against the ``outputField`` of each check in the compliance
schema's pipeline rule; a ``status`` of ``error`` fails every check of the rule.
"""

import json
from typing import Any, Dict, List, Optional

COMPLIANCE_OUTPUT_FILE_NAME = "compliance-output.json"


def build_compliance_output(
    measurements: Dict[str, Any],
    status: str = "success",
    errors: Optional[List[str]] = None,
    pipeline_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The compliance output document for a set of measurements."""
    return {
        "complianceOutput": True,
        "status": status,
        "measurements": measurements,
        "errors": errors or [],
        "pipelineMetadata": pipeline_metadata or {},
    }


def results_output_location(manifest: Dict[str, Any]) -> str:
    """``s3://bucket/prefix/`` of the execution's results output, from the workflow manifest's
    ``outputs`` block (``bucket`` + bucket-relative ``results`` prefix)."""
    outputs = (manifest or {}).get("outputs") or {}
    bucket = outputs.get("bucket", "")
    prefix = (outputs.get("results", "") or "").strip("/")
    if not bucket or not prefix:
        raise ValueError("The workflow manifest carries no results output location")
    return f"s3://{bucket}/{prefix}/"


def write_compliance_output(
    s3_client,
    measurements: Dict[str, Any],
    manifest: Optional[Dict[str, Any]] = None,
    results_s3_path: Optional[str] = None,
    status: str = "success",
    errors: Optional[List[str]] = None,
    pipeline_metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Write ``compliance-output.json`` under the execution's results output prefix.

    Args:
        s3_client: boto3 S3 client
        measurements: Output field names to numeric values
        manifest: The parsed workflow manifest; the location is its ``outputs.bucket`` +
            ``outputs.results``. Alternatively pass ``results_s3_path``.
        results_s3_path: The results output location as ``s3://bucket/prefix/`` (or
            ``bucket/prefix/``) when the manifest is not at hand
        status: ``"success"`` or ``"error"``
        errors: Error messages when ``status`` is ``"error"``
        pipeline_metadata: Optional free-form metadata about the pipeline run

    Returns:
        The ``s3://bucket/key`` location written.
    """
    location = results_s3_path or results_output_location(manifest or {})
    bucket, key_prefix = parse_s3_path(location)
    key = f"{key_prefix}{COMPLIANCE_OUTPUT_FILE_NAME}"
    body = json.dumps(build_compliance_output(measurements, status, errors, pipeline_metadata), indent=2)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/json",
    )
    return f"s3://{bucket}/{key}"


def parse_s3_path(path: str) -> tuple:
    """``(bucket, key_prefix)`` for ``s3://bucket/prefix`` or ``bucket/prefix``; the prefix ends
    with exactly one ``/`` (an empty prefix stays empty)."""
    without_scheme = path[5:] if path.startswith("s3://") else path
    parts = without_scheme.split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""
    key = key.strip("/")
    return bucket, f"{key}/" if key else ""
