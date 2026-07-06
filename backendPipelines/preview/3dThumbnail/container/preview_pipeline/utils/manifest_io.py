# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Container-side reads of the VAMS metadata + input-configuration files from S3.

Only the S3 locations travel in the pipeline definition; the container reads the files here,
mirroring the lambda-side ``manifestHelper``. Best-effort: an unreadable file yields an empty dict."""

import json

from .s3_utils import client
from .logging import get_logger

logger = get_logger()


def _parse_s3_uri(uri):
    """Split ``s3://bucket/key`` into ``(bucket, key)``; ``("", "")`` for an empty/non-s3 value."""
    if not uri or not uri.startswith("s3://"):
        return "", ""
    without_scheme = uri[len("s3://"):]
    if "/" in without_scheme:
        return tuple(without_scheme.split("/", 1))
    return without_scheme, ""


def _get_json(s3_location):
    """Read + parse a JSON object from an ``s3://`` location, or ``None`` (best-effort).

    A non-``s3://`` value is treated as inline JSON (localTest affordance)."""
    if not s3_location:
        return None
    if not s3_location.startswith("s3://"):
        try:
            return json.loads(s3_location)
        except (ValueError, TypeError):
            return None
    bucket, key = _parse_s3_uri(s3_location)
    if not bucket or not key:
        return None
    try:
        resp = client.get_object(Bucket=bucket, Key=key)
        body = resp["Body"].read().decode("utf-8")
        return json.loads(body) if body else None
    except Exception as e:  # nosec B110 - best-effort; an unreadable file yields None
        logger.warning(f"Could not read S3 object {s3_location}: {e}")
        return None


def fetch_metadata(input_metadata_s3_location):
    """Read the input-metadata file and unwrap the ``{schemaVersion, metadata}`` envelope, returning
    the inner metadata dict. An un-enveloped file is returned as-is. ``{}`` when absent/unreadable."""
    body = _get_json(input_metadata_s3_location)
    if not isinstance(body, dict):
        return {}
    if "metadata" in body and "schemaVersion" in body:
        return body.get("metadata") or {}
    return body


def fetch_input_configuration(input_configuration_s3_location):
    """Read the per-pipeline input configuration file (the user-defined ``inputParameters``),
    returning the parsed object. ``{}`` when absent/unreadable."""
    body = _get_json(input_configuration_s3_location)
    return body if isinstance(body, dict) else {}
