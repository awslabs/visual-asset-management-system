# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Miris API client shared by the Miris handlers.

Integration API calls authenticate with the Miris Integration Key, a plain-string Secrets Manager
secret named by `MIRIS_API_KEY_SECRET_ARN`, and go to the Miris API at `MIRIS_API_BASE_URL`. The
key is cached per container for `_SECRET_CACHE_TTL_SECONDS`, so a rotated key is picked up within
that window; a 401 or 403 drops the cached key and retries the call once with a freshly read one.
`get_viewer_asset_state` instead reads the viewer API with a viewer key the caller passes. In static
mode that is the deployment-wide viewer key, which `load_static_viewer_key` reads from the secret
named by `MIRIS_VIEWER_KEY_SECRET_ARN` (filled at deploy time) and caches the same way.

Every call accepts an optional `deadline` (see `request_deadline`), which is a best-effort bound:
a call is not started once less than `_MIN_CALL_SECONDS` remain before it, its connect and read
timeouts together never exceed the time left, and the Integration Key is not read from Secrets
Manager unless the bounded read and a call still fit. A slow response that keeps sending data
can still outlast it, since `requests` bounds each socket read rather than the whole response.

Redirects are never followed, so a key header never reaches another host; a 3xx response raises
`MirisIntegrationError`.

Neither the Integration Key nor any viewer key material is ever logged or placed in an exception
message. Viewer key IDs may be logged. A failed call raises `MirisIntegrationError`, whose message
carries at most the HTTP status code, never the Miris response body.
"""
import hashlib
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.cookiejar import DefaultCookiePolicy
from typing import Any, Dict, NamedTuple, Optional, Tuple

import boto3
import requests
from botocore.config import Config

from customLogging.logger import safeLogger

logger = safeLogger(service_name="MirisIntegration")

# Secrets Manager reads run inside API requests, so each attempt is short and retries are few.
_SECRET_CONNECT_TIMEOUT_SECONDS = 1
_SECRET_READ_TIMEOUT_SECONDS = 2
# Attempts in total, the first included.
_SECRET_TOTAL_MAX_ATTEMPTS = 2
_secrets_config = Config(
    connect_timeout=_SECRET_CONNECT_TIMEOUT_SECONDS,
    read_timeout=_SECRET_READ_TIMEOUT_SECONDS,
    retries={"total_max_attempts": _SECRET_TOTAL_MAX_ATTEMPTS, "mode": "standard"},
)
# Configured worst case of a Secrets Manager read: every attempt's timeouts, plus the standard mode's backoff
# of at most 2^(n-1) seconds before retry n.
_SECRET_READ_BOUND_SECONDS = (
    _SECRET_TOTAL_MAX_ATTEMPTS * (_SECRET_CONNECT_TIMEOUT_SECONDS + _SECRET_READ_TIMEOUT_SECONDS)
    + sum(2 ** (n - 1) for n in range(1, _SECRET_TOTAL_MAX_ATTEMPTS))
)

# (connect, read) seconds for every Miris API request.
_REQUEST_TIMEOUT_SECONDS = (5, 15)

# Time one API request may spend on Miris calls, inside API Gateway's integration timeout.
_REQUEST_BUDGET_SECONDS = 20
# The least integration timeout a deployment can configure (app.api.apiGatewayRest.apiGatewayTimeoutTime),
# so a deadline measured from it holds whatever the deployment sets.
_API_GATEWAY_TIMEOUT_SECONDS = 29
# Kept back from API Gateway's timeout and the Lambda's remaining time, so the handler can still answer.
_DEADLINE_MARGIN_SECONDS = 3
# A call is not started with less time than this left before the deadline.
_MIN_CALL_SECONDS = 1
# Time given to deleting a minted key that could not be recorded, even after the request
# deadline, and kept back from the Lambda's remaining time while doing so.
_CLEANUP_GRACE_SECONDS = 3
_CLEANUP_MARGIN_SECONDS = 0.5

# How long the Integration Key read from Secrets Manager is reused before it is fetched again.
_SECRET_CACHE_TTL_SECONDS = 3600

_API_KEY_HEADER = "Miris-API-Key"
_VIEWER_KEY_HEADER = "Miris-Viewer-Key"


# Longest viewer key name `viewer_key_name` produces.
MAX_VIEWER_KEY_NAME_LENGTH = 128

# Miris viewer key IDs and asset IDs are UUIDs. Checked before an ID is placed in a request path.
_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

# Lazy module-level singletons, kept across warm invocations.
_secretsmanager = None
_session: Optional[requests.Session] = None
# Secret values by ARN, each with the time it was read.
_secret_cache: Dict[str, Tuple[str, float]] = {}

# Module-level config, read once at cold start. A call made while either is unset raises
# `MirisIntegrationError`, so a Lambda that imports this module without calling it needs neither.
MIRIS_API_KEY_SECRET_ARN = os.environ.get("MIRIS_API_KEY_SECRET_ARN", "").strip()
MIRIS_API_BASE_URL = os.environ.get("MIRIS_API_BASE_URL", "").strip().rstrip("/")
# The deployment-wide viewer key's secret, set in static mode only.
MIRIS_VIEWER_KEY_SECRET_ARN = os.environ.get("MIRIS_VIEWER_KEY_SECRET_ARN", "").strip()


def integration_configured() -> bool:
    """True when both the Integration Key secret ARN and the Miris API base URL are set."""
    return bool(MIRIS_API_KEY_SECRET_ARN and MIRIS_API_BASE_URL)


class MirisIntegrationError(Exception):
    """A Miris Integration API call failed.

    `status_code` is the HTTP status Miris returned, or None when no response was received.
    """

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class MirisDeadlineExceeded(MirisIntegrationError):
    """The request deadline left too little time to start a Miris call."""


class MirisAsset(NamedTuple):
    """A Miris asset as the Integration API reports it: its processing `state` and its `tags`."""

    state: str
    tags: Tuple[str, ...]


@dataclass(frozen=True)
class MintedViewerKey:
    """A viewer key Miris created. `key` is the key material, which Miris returns only once."""

    key_id: str
    key: str = field(repr=False)
    expires_at: str


def _is_uuid(value: Any) -> bool:
    return isinstance(value, str) and bool(_UUID_PATTERN.fullmatch(value))


def is_viewer_key_id(value: Any) -> bool:
    """True when `value` is a string in the UUID form Miris uses for viewer key IDs."""
    return _is_uuid(value)


def _rfc3339_utc(value: datetime) -> str:
    """Format a datetime as an RFC 3339 UTC timestamp with second precision, e.g. 2026-10-15T19:04:28Z."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_object(response: requests.Response) -> Optional[dict]:
    """The response body as a dict, or None when it is not a JSON object."""
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def binding_tags(database_id: str, asset_id: str) -> Tuple[str, str]:
    """The Miris asset tags that bind a Miris asset to one VAMS asset. An assetId is unique only
    within its database, so both the asset tag and the database tag are required."""
    return (f"vams-asset-{asset_id}", f"vams-database-{database_id}")


def is_bound_to_asset(tags: Tuple[str, ...], database_id: str, asset_id: str) -> bool:
    """True when the Miris asset tags carry both binding tags of the VAMS asset."""
    return all(tag in tags for tag in binding_tags(database_id, asset_id))


def viewer_key_name(database_id: str, asset_id: str, minted_at_ms: int) -> str:
    """The name of a per-view viewer key: `vams:{databaseId}:{assetId}:{epoch ms}`.

    When that exceeds `MAX_VIEWER_KEY_NAME_LENGTH`, the `{databaseId}:{assetId}` part is replaced
    by its SHA-256 hex digest.
    """
    identity = f"{database_id}:{asset_id}"
    name = f"vams:{identity}:{minted_at_ms}"
    if len(name) <= MAX_VIEWER_KEY_NAME_LENGTH:
        return name
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"vams:{digest}:{minted_at_ms}"


# A receipt time further than this from now is not a real API Gateway timestamp.
_RECEIPT_TIME_WINDOW_SECONDS = 3600


def _deadline(context, seconds: float, margin_seconds: float) -> float:
    remaining_ms = getattr(context, "get_remaining_time_in_millis", None)
    if callable(remaining_ms):
        seconds = min(seconds, remaining_ms() / 1000 - margin_seconds)
    return time.monotonic() + seconds


def request_deadline(context, event: Optional[dict] = None) -> float:
    """The `time.monotonic()` value by which one invocation's Miris calls should finish (best effort).

    `_REQUEST_BUDGET_SECONDS` from now, shortened to API Gateway's timeout less the time since it
    received the request (`requestContext.requestTimeEpoch` on the REST API proxy event, or the
    HTTP API's `requestContext.timeEpoch`, when the event carries one), and to the Lambda's
    remaining time when `context` reports it, each less `_DEADLINE_MARGIN_SECONDS`.
    """
    seconds = float(_REQUEST_BUDGET_SECONDS)
    request_context = (event or {}).get("requestContext") or {}
    received = _received_at(request_context.get("requestTimeEpoch"))
    if received is None:
        received = _received_at(request_context.get("timeEpoch"))
    if received is not None:
        elapsed = max(0.0, time.time() - received)
        seconds = min(seconds, _API_GATEWAY_TIMEOUT_SECONDS - elapsed - _DEADLINE_MARGIN_SECONDS)
    return _deadline(context, seconds, _DEADLINE_MARGIN_SECONDS)


def _received_at(time_epoch_ms) -> Optional[float]:
    """A receipt time in epoch milliseconds as epoch seconds, or None unless it is a plausible
    recent time."""
    if not isinstance(time_epoch_ms, (int, float)) or isinstance(time_epoch_ms, bool):
        return None
    try:
        received = float(time_epoch_ms) / 1000
    except OverflowError:
        return None
    if not math.isfinite(received) or abs(time.time() - received) > _RECEIPT_TIME_WINDOW_SECONDS:
        return None
    return received


def cleanup_deadline(context) -> float:
    """A deadline for deleting a minted key that could not be recorded.

    `_CLEANUP_GRACE_SECONDS` from now regardless of the request deadline, shortened to the
    Lambda's remaining time less `_CLEANUP_MARGIN_SECONDS` when `context` reports it.
    """
    return _deadline(context, _CLEANUP_GRACE_SECONDS, _CLEANUP_MARGIN_SECONDS)


def _time_left(deadline: float, needed_seconds: float) -> float:
    """Seconds left before `deadline`; raises `MirisDeadlineExceeded` when under `needed_seconds`."""
    remaining = deadline - time.monotonic()
    if remaining < needed_seconds:
        raise MirisDeadlineExceeded("Too little time is left before the request deadline")
    return remaining


def _call_timeout(deadline: Optional[float]):
    """The (connect, read) timeout for one call. With a deadline, connect plus read never exceeds
    the time left: below their sum, both are scaled down in proportion."""
    if deadline is None:
        return _REQUEST_TIMEOUT_SECONDS
    remaining = _time_left(deadline, _MIN_CALL_SECONDS)
    connect, read = _REQUEST_TIMEOUT_SECONDS
    if remaining >= connect + read:
        return _REQUEST_TIMEOUT_SECONDS
    scale = remaining / (connect + read)
    return (connect * scale, read * scale)


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        # Requests carry only their explicit key header; no cookie is kept between them.
        _session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))
    return _session


def _send(method: str, url: str, headers: dict, deadline: Optional[float],
          json_body: Optional[dict] = None) -> requests.Response:
    """Send one request on the shared session, without following redirects. A transport failure
    or a 3xx response raises `MirisIntegrationError`."""
    timeout = _call_timeout(deadline)
    try:
        response = _get_session().request(
            method, url, headers=headers, json=json_body, timeout=timeout, allow_redirects=False
        )
    except requests.RequestException as e:
        raise MirisIntegrationError(f"Miris {method} request failed ({type(e).__name__})") from None
    if 300 <= response.status_code < 400:
        raise MirisIntegrationError(
            f"Miris {method} returned a redirect (HTTP {response.status_code})",
            status_code=response.status_code,
        )
    return response


def _get_secretsmanager_client():
    global _secretsmanager
    if _secretsmanager is None:
        _secretsmanager = boto3.client("secretsmanager", config=_secrets_config)
    return _secretsmanager


def _load_secret(secret_arn: str, name: str, deadline: Optional[float]) -> str:
    """Return a plain-string secret, read from Secrets Manager or the module cache.

    With a deadline, Secrets Manager is read only while the bounded read and one call still fit.
    """
    cached = _secret_cache.get(secret_arn)
    if cached is not None and (time.time() - cached[1]) < _SECRET_CACHE_TTL_SECONDS:
        return cached[0]
    if deadline is not None:
        _time_left(deadline, _SECRET_READ_BOUND_SECONDS + _MIN_CALL_SECONDS)
    try:
        response = _get_secretsmanager_client().get_secret_value(SecretId=secret_arn)
    except Exception as e:
        raise MirisIntegrationError(f"Could not read the {name} secret ({type(e).__name__})") from None
    value = (response.get("SecretString") or "").strip()
    if not value:
        raise MirisIntegrationError(f"The {name} secret is empty")
    _secret_cache[secret_arn] = (value, time.time())
    return value


def _load_api_key(deadline: Optional[float] = None) -> str:
    """Return the Miris Integration Key."""
    if not MIRIS_API_KEY_SECRET_ARN:
        raise MirisIntegrationError("MIRIS_API_KEY_SECRET_ARN is not configured")
    return _load_secret(MIRIS_API_KEY_SECRET_ARN, "Miris Integration Key", deadline)


def load_static_viewer_key(deadline: Optional[float] = None) -> str:
    """Return the deployment-wide viewer key of static mode."""
    if not MIRIS_VIEWER_KEY_SECRET_ARN:
        raise MirisIntegrationError("MIRIS_VIEWER_KEY_SECRET_ARN is not configured")
    return _load_secret(MIRIS_VIEWER_KEY_SECRET_ARN, "Miris viewer key", deadline)


def _invalidate_api_key_cache() -> None:
    """Drop the cached Integration Key so the next call re-reads Secrets Manager."""
    _secret_cache.pop(MIRIS_API_KEY_SECRET_ARN, None)


def _clear_secret_cache() -> None:
    """Drop every cached secret."""
    _secret_cache.clear()


def _request(method: str, path: str, json_body: Optional[dict] = None,
             deadline: Optional[float] = None) -> requests.Response:
    """Send one request to the Miris Integration API and return the response.

    `path` starts with "/" and is appended to `MIRIS_API_BASE_URL`. A 401 or 403 is retried once
    with a freshly read Integration Key. Any response is returned to the caller, which decides
    which status codes count as success. A transport failure raises `MirisIntegrationError`.
    """
    if not MIRIS_API_BASE_URL:
        raise MirisIntegrationError("MIRIS_API_BASE_URL is not configured")
    url = f"{MIRIS_API_BASE_URL}{path}"
    response = None
    for attempt in range(2):
        headers = {_API_KEY_HEADER: _load_api_key(deadline), "Accept": "application/json"}
        response = _send(method, url, headers, deadline, json_body=json_body)
        if response.status_code in (401, 403) and attempt == 0:
            logger.warning(f"Miris {method} returned HTTP {response.status_code}; re-reading the Integration Key")
            _invalidate_api_key_cache()
            continue
        break
    if response.status_code in (401, 403):
        _report_auth_failure(response.status_code)
    return response


def _report_auth_failure(status_code: int) -> None:
    """Log that Miris refused the Integration Key after a re-read, with the `MirisAuthFailures`
    CloudWatch metric (embedded metric format) in `MIRIS_METRICS_NAMESPACE` when it is set."""
    extra: dict = {"statusCode": status_code, "MirisAuthFailures": 1}
    namespace = os.environ.get("MIRIS_METRICS_NAMESPACE", "").strip()
    if namespace:
        extra["_aws"] = {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": namespace,
                "Dimensions": [[]],
                "Metrics": [{"Name": "MirisAuthFailures", "Unit": "Count"}],
            }],
        }
    logger.error("miris_auth_failed", extra=extra)


def delete_viewer_key(key_id: str, deadline: Optional[float] = None) -> None:
    """Delete one viewer key from the Miris workspace.

    A key Miris no longer has (404) counts as deleted. Raises `MirisIntegrationError` for an ID
    that is not a UUID (without calling Miris), for any other non-2xx status, and for a transport
    failure.
    """
    if not is_viewer_key_id(key_id):
        raise MirisIntegrationError("Viewer key ID is not a UUID")
    response = _request("DELETE", f"/v1/asset-viewer-key/{key_id}", deadline=deadline)
    if response.status_code in (200, 204):
        return
    if response.status_code == 404:
        logger.info(f"Miris viewer key {key_id} was already deleted")
        return
    raise MirisIntegrationError(
        f"Miris viewer key delete returned HTTP {response.status_code}",
        status_code=response.status_code,
    )


def _delete_unusable_key(key_id: str, context) -> None:
    """Delete a key Miris created but returned without usable key material (best effort), within
    its own `cleanup_deadline` grace period."""
    try:
        delete_viewer_key(key_id, deadline=cleanup_deadline(context))
        logger.warning(f"Deleted Miris viewer key {key_id}, returned without usable key material")
    except Exception as e:
        logger.error(
            f"Could not delete Miris viewer key {key_id}, returned without usable key material"
            f" ({type(e).__name__})"
        )


def create_viewer_key(miris_asset_uuid: str, expires_at: datetime, name: str,
                      deadline: Optional[float] = None, context=None) -> MintedViewerKey:
    """Create a viewer key that can stream only `miris_asset_uuid`, until `expires_at`.

    The key is created with no tags and the one asset ID; Miris combines tags and asset IDs with
    AND, so an empty tag list grants exactly the listed asset. The returned `expires_at` is the
    requested expiry as sent. Raises `MirisIntegrationError` for an asset ID that is not a UUID
    (without calling Miris), for a non-2xx status, for a transport failure, and for a response
    without a UUID `id` and a non-empty `key`. A response with a UUID `id` but no usable `key` has
    still created a key, so that key is deleted before the error is raised, within the
    `cleanup_deadline` of `context` even when `deadline` has passed; a failed delete is logged and
    does not change the error.
    """
    if not _is_uuid(miris_asset_uuid):
        raise MirisIntegrationError("Miris asset ID is not a UUID")
    requested_expiry = _rfc3339_utc(expires_at)
    response = _request(
        "POST",
        "/v1/asset-viewer-key",
        json_body={
            "name": name,
            "tags": [],
            "asset_ids": [miris_asset_uuid],
            "expires_at": requested_expiry,
        },
        deadline=deadline,
    )
    if response.status_code not in (200, 201):
        raise MirisIntegrationError(
            f"Miris viewer key create returned HTTP {response.status_code}",
            status_code=response.status_code,
        )

    body = _json_object(response) or {}
    key_id = body.get("id")
    key = body.get("key")
    if not is_viewer_key_id(key_id) or not isinstance(key, str) or not key.strip():
        if is_viewer_key_id(key_id):
            _delete_unusable_key(key_id, context)
        raise MirisIntegrationError(
            "Miris viewer key create returned no usable key", status_code=response.status_code
        )
    logger.info(f"Created Miris viewer key {key_id}")
    return MintedViewerKey(key_id=key_id, key=key, expires_at=requested_expiry)


def _response_state(response: requests.Response, what: str) -> str:
    state = (_json_object(response) or {}).get("state")
    if not isinstance(state, str):
        raise MirisIntegrationError(f"{what} returned no state", status_code=response.status_code)
    return state


def get_asset(miris_asset_uuid: str, deadline: Optional[float] = None) -> Optional[MirisAsset]:
    """Read one asset with the Integration Key, or return None when Miris has no such asset (404).

    Absent or null `tags` are read as no tags, and tags that are not strings are dropped. Raises
    `MirisIntegrationError` for an ID that is not a UUID (without calling Miris), for any other
    non-2xx status, for a transport failure, and for a response without a string `state` or with
    `tags` that are not a list.
    """
    if not _is_uuid(miris_asset_uuid):
        raise MirisIntegrationError("Miris asset ID is not a UUID")
    response = _request("GET", f"/v1/asset/{miris_asset_uuid}", deadline=deadline)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise MirisIntegrationError(
            f"Miris asset read returned HTTP {response.status_code}",
            status_code=response.status_code,
        )
    state = _response_state(response, "Miris asset read")
    tags = (_json_object(response) or {}).get("tags")
    if tags is None:
        tags = []
    if not isinstance(tags, list):
        raise MirisIntegrationError("Miris asset read returned no tag list", status_code=200)
    return MirisAsset(state=state, tags=tuple(tag for tag in tags if isinstance(tag, str)))


def get_viewer_asset_state(miris_asset_uuid: str, viewer_key: Optional[str],
                           deadline: Optional[float] = None) -> Optional[str]:
    """Read one asset's `state` from the viewer API with `viewer_key`, or None on a 404.

    The viewer API reports no tags. The key is sent in a header, never the query string. Raises
    `MirisIntegrationError` for an ID that is not a UUID or a missing viewer key or base URL
    (without calling Miris), for any other non-2xx status (a 400 included, which the status route
    reports as processing), for a transport failure, and for a response without a string `state`.
    """
    if not _is_uuid(miris_asset_uuid):
        raise MirisIntegrationError("Miris asset ID is not a UUID")
    if not viewer_key:
        raise MirisIntegrationError("No Miris viewer key is configured")
    if not MIRIS_API_BASE_URL:
        raise MirisIntegrationError("MIRIS_API_BASE_URL is not configured")
    response = _send(
        "GET",
        f"{MIRIS_API_BASE_URL}/viewer/v1/asset/{miris_asset_uuid}",
        {_VIEWER_KEY_HEADER: viewer_key, "Accept": "application/json"},
        deadline,
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise MirisIntegrationError(
            f"Miris viewer asset read returned HTTP {response.status_code}",
            status_code=response.status_code,
        )
    return _response_state(response, "Miris viewer asset read")
