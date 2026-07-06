# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Container-side reads of the VAMS metadata + input-configuration files from S3.

Only the S3 locations travel in the pipeline definition; the container reads the files here.
S3 access uses the AWS CLI (matching this container's existing S3 I/O); no boto3 dependency.
Best-effort: an unreadable file yields an empty dict."""

import json
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_json(s3_location):
    """Read + parse a JSON object from an ``s3://`` location, or ``None`` (best-effort).

    A non-``s3://`` value is treated as inline JSON (local-test affordance)."""
    if not s3_location:
        return None
    if not s3_location.startswith("s3://"):
        try:
            return json.loads(s3_location)
        except (ValueError, TypeError):
            return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "cp", s3_location, tmp_path],
            capture_output=True, text=True
        )  # nosemgrep: dangerous-subprocess-use-audit
        if result.returncode != 0:
            logger.warning(f"Could not read S3 object {s3_location}: {result.stderr}")
            return None
        body = Path(tmp_path).read_text(encoding="utf-8")
        return json.loads(body) if body else None
    except Exception as e:  # nosec B110 - best-effort; an unreadable file yields None
        logger.warning(f"Could not read S3 object {s3_location}: {e}")
        return None


def fetch_metadata(input_metadata_s3_location):
    """Read the input-metadata file and unwrap the ``{schemaVersion, metadata}`` envelope,
    returning the inner metadata dict. ``{}`` when absent/unreadable."""
    body = _get_json(input_metadata_s3_location)
    if not isinstance(body, dict):
        return {}
    if "metadata" in body and "schemaVersion" in body:
        return body.get("metadata") or {}
    return body


def fetch_input_configuration(input_configuration_s3_location):
    """Read the per-pipeline input configuration file, returning the parsed object.
    ``{}`` when absent/unreadable."""
    body = _get_json(input_configuration_s3_location)
    return body if isinstance(body, dict) else {}
