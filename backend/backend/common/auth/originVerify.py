# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Origin verification value of a CloudFront deployment.

On a CloudFront deployment, CloudFront adds ``ORIGIN_VERIFY_HEADER_NAME`` to every request it
forwards to the REST API, carrying the deployment's origin verification value. The value is an
AWS Secrets Manager secret (``ORIGIN_VERIFY_SECRET_ARN``) whose SecretString is JSON:
``{"current": <value CloudFront sends>, "previous": <value before the last rotation>}``.
``previous`` is absent until the first rotation.

``is_valid_origin_verify_value`` accepts ``current`` and ``previous`` of the ``AWSCURRENT``
version, and ``current`` of an ``AWSPENDING`` version while a rotation is in progress, so a request
is recognised whichever value its CloudFront edge sends during a rotation. Comparison is
constant-time. The accepted values are cached per execution environment for
``CACHE_TTL_SECONDS``; a value that matches none of them re-reads the secret at most once per
``MISMATCH_REFRESH_INTERVAL_SECONDS``, so a rotation is picked up without a cold start. When the
secret cannot be read, no value is accepted; the client's short timeouts bound a read that cannot
reach Secrets Manager to a few seconds. Nothing here logs a value or raises.

The header name is mirrored in ``infra/config/config.ts`` (``API_ORIGIN_VERIFY_HEADER_NAME``) and
in ``customLogging/logger.py`` ``SENSITIVE_KEYS``.
"""
import hmac
import json
import os
import time
from typing import Iterable, Optional, Tuple

import boto3
from botocore.config import Config as BotoConfig

from customLogging.logger import safeLogger

logger = safeLogger(service_name="OriginVerify")

ORIGIN_VERIFY_HEADER_NAME = "x-vams-origin-verify"
CURRENT_KEY = "current"
PREVIOUS_KEY = "previous"
SECRET_ARN_ENV = "ORIGIN_VERIFY_SECRET_ARN"

CACHE_TTL_SECONDS = 300
MISMATCH_REFRESH_INTERVAL_SECONDS = 5

# Short timeouts and few retries, so that when Secrets Manager cannot be reached (for example from a
# VPC with no Secrets Manager endpoint or NAT route) a read fails within seconds and the caller falls
# back to the TCP peer, well inside the REST authorizer's own timeout. botocore counts max_attempts as
# retries after the first request: at most three attempts of 2 + 2 seconds each.
retry_config = BotoConfig(
    connect_timeout=2,
    read_timeout=2,
    retries={"max_attempts": 2, "mode": "standard"},
)
secrets_client = boto3.client("secretsmanager", config=retry_config)

_cache = {"values": (), "fetched_at": None}


def _secret_arn() -> str:
    return os.environ.get(SECRET_ARN_ENV, "") or ""


def _now() -> float:
    return time.monotonic()


def values_from_secret_string(secret_string: Optional[str], keys: Iterable[str]) -> Tuple[str, ...]:
    """The non-empty string values of ``keys`` in a JSON SecretString, in key order."""
    try:
        data = json.loads(secret_string or "")
    except (TypeError, ValueError):
        return ()
    if not isinstance(data, dict):
        return ()
    return tuple(v for v in (data.get(k) for k in keys) if isinstance(v, str) and v)


def _fetch_accepted_values(secret_arn: str) -> Tuple[str, ...]:
    """Read the accepted values from Secrets Manager. Raises on a failed AWSCURRENT read."""
    current = secrets_client.get_secret_value(SecretId=secret_arn)
    values = list(values_from_secret_string(current.get("SecretString"), (CURRENT_KEY, PREVIOUS_KEY)))
    try:
        pending = secrets_client.get_secret_value(SecretId=secret_arn, VersionStage="AWSPENDING")
        values.extend(values_from_secret_string(pending.get("SecretString"), (CURRENT_KEY,)))
    except Exception:
        # No rotation in progress (ResourceNotFoundException), or the pending read failed: the
        # AWSCURRENT values stand on their own.
        pass
    return tuple(dict.fromkeys(values))


def _refresh(secret_arn: str) -> None:
    try:
        _cache["values"] = _fetch_accepted_values(secret_arn)
    except Exception as e:
        # Keep the last values read, if any, and retry no sooner than the next refresh window.
        logger.warning(f"Could not read the origin verification secret: {type(e).__name__}")
    _cache["fetched_at"] = _now()


def _matches(value: str, accepted: Tuple[str, ...]) -> bool:
    try:
        candidate = value.encode("utf-8")
        matched = False
        for accepted_value in accepted:
            # Every accepted value is compared, so the time taken does not depend on which matched.
            if hmac.compare_digest(candidate, accepted_value.encode("utf-8")):
                matched = True
        return matched
    except Exception:
        return False


def is_valid_origin_verify_value(value: Optional[str]) -> bool:
    """Whether ``value`` is one of the deployment's accepted origin verification values."""
    secret_arn = _secret_arn()
    if not secret_arn or not isinstance(value, str) or not value:
        return False

    now = _now()
    fetched_at = _cache["fetched_at"]
    if fetched_at is None or now - fetched_at >= CACHE_TTL_SECONDS:
        _refresh(secret_arn)
    if _matches(value, _cache["values"]):
        return True

    # A value from a rotation this environment has not seen yet: re-read once, bounded so that
    # requests carrying a wrong value cannot drive a Secrets Manager call each.
    fetched_at = _cache["fetched_at"]
    if fetched_at is None or _now() - fetched_at >= MISMATCH_REFRESH_INTERVAL_SECONDS:
        _refresh(secret_arn)
        return _matches(value, _cache["values"])
    return False
