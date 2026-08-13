# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Container-side reads of the VAMS metadata + input-configuration files from S3.

Only the S3 locations travel in the pipeline definition; the container reads the files here.
S3 access uses the AWS CLI, matching the rest of this container. Best-effort: an unreadable
file yields an empty dict."""

import json
import logging
import subprocess

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
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "cp", s3_location, "-"],
            capture_output=True, text=True
        )  # nosemgrep: dangerous-subprocess-use-audit
        if result.returncode != 0:
            logger.warning(f"Could not read S3 object {s3_location}: {result.stderr}")
            return None
        body = result.stdout
        return json.loads(body) if body else None
    except Exception as e:  # nosec B110 - best-effort; an unreadable file yields None
        logger.warning(f"Could not read S3 object {s3_location}: {e}")
        return None


def _read_text(s3_location):
    """The raw text at an ``s3://`` location, or ``None`` when it cannot be fetched.

    Separate from ``_get_json`` so a caller can tell a transport failure from a parse failure; the
    inline (non-``s3://``) form is the same local-test affordance ``_get_json`` offers."""
    if not s3_location:
        return None
    if not s3_location.startswith("s3://"):
        return s3_location
    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["aws", "s3", "cp", s3_location, "-"],
            capture_output=True, text=True
        )  # nosemgrep: dangerous-subprocess-use-audit
        if result.returncode != 0:
            logger.warning(f"Could not read S3 object {s3_location}: {result.stderr}")
            return None
        return result.stdout
    except Exception as e:
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


class InputConfigurationError(RuntimeError):
    """Raised when an input-configuration file exists but cannot be parsed as a JSON object."""


def fetch_input_configuration(input_configuration_s3_location):
    """Read the per-pipeline input configuration file, returning the parsed object.

    ``{}`` when no configuration was supplied. Raises ``InputConfigurationError`` when a location WAS
    supplied but its body is not a JSON object.

    The distinction is the point. Returning ``{}`` for a malformed body makes it indistinguishable from
    "no configuration", so the pipeline falls back to its hardcoded defaults, the run reports SUCCESS,
    and every parameter the caller set is silently gone. A job that cannot read its own configuration
    has not done what was asked, so it fails instead."""
    if not input_configuration_s3_location:
        return {}
    body = _read_text(input_configuration_s3_location)
    if body is None:
        raise InputConfigurationError(
            f"Could not read the input configuration at {input_configuration_s3_location}")
    if not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except ValueError as e:
        raise InputConfigurationError(
            f"The input configuration at {input_configuration_s3_location} is not valid JSON: {e}")
    if not isinstance(parsed, dict):
        raise InputConfigurationError(
            f"The input configuration at {input_configuration_s3_location} is not a JSON object")
    return parsed
