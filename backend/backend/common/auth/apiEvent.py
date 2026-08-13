# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Normalize API Gateway REST (v1) proxy events to the canonical v2-style shape.

VAMS handlers read ``event['requestContext']['http']['path']`` / ``['method']`` and
``['sourceIp']`` (the HTTP API v2 layout). A REST API proxy event exposes these as
top-level ``path`` / ``httpMethod`` and ``requestContext.identity.sourceIp``. This
helper injects the v2-style block so handlers and the apiRoutes matcher are unchanged.
Idempotent and a no-op for events already in v2 form or for internal cross-call events.
"""

import urllib.parse

# Marker written into the event once path parameters have been percent-decoded, so a
# second normalize_event call cannot decode them again. Double-decoding corrupts a value
# whose decoded form legitimately contains a percent escape: a file named "a%20b" arrives
# as "a%2520b", decodes once to "a%20b" (correct), and would decode again to "a b".
_PATH_PARAMS_DECODED_FLAG = "vamsPathParametersDecoded"


def _decode_path_parameters(event: dict) -> None:
    """Percent-decode REST path parameter values in place, exactly once.

    API Gateway REST (v1) delivers path parameters percent-encoded, whereas HTTP API (v2)
    delivered them already decoded. Handlers use these values directly as Amazon S3 object
    keys, and an S3 key holds raw characters — a file named "my file.e57" is keyed with a
    literal space, so looking up the undecoded "my%20file.e57" raises NoSuchKey. This
    affects greedy ``{proxy+}`` file-path parameters in practice; the scalar id parameters
    are validated against character sets that contain nothing encodable, so decoding them
    is a no-op.

    Uses ``unquote`` rather than ``unquote_plus``: in a URL *path* a "+" is a literal plus,
    not a space (only query strings use "+" for space), so ``unquote_plus`` would corrupt
    any file name containing a plus.
    """
    path_params = event.get("pathParameters")
    if not isinstance(path_params, dict) or not path_params:
        return
    for key, value in path_params.items():
        if isinstance(value, str) and "%" in value:
            path_params[key] = urllib.parse.unquote(value)


def normalize_event(event: dict) -> dict:
    if not isinstance(event, dict):
        return event
    if "lambdaCrossCall" in event:
        return event

    # REST API (v1) sends `pathParameters` / `queryStringParameters` as an explicit JSON
    # `null` when there are none, whereas HTTP API (v2) omitted them (handlers relied on
    # `event.get(..., {})` returning `{}`). Coerce a present-but-null value to `{}` so the
    # many handlers that read `event['pathParameters']` / `['queryStringParameters']` without
    # an `or {}` guard do not crash with "NoneType is not subscriptable/iterable". Idempotent.
    for key in ("pathParameters", "queryStringParameters"):
        if event.get(key) is None:
            event[key] = {}

    # Percent-decode path parameters once (see _decode_path_parameters). Runs before the
    # early returns below so a v2-shaped REST event still gets its parameters decoded.
    if not event.get(_PATH_PARAMS_DECODED_FLAG):
        _decode_path_parameters(event)
        event[_PATH_PARAMS_DECODED_FLAG] = True

    rc = event.get("requestContext")
    if not isinstance(rc, dict):
        return event
    if isinstance(rc.get("http"), dict) and rc["http"].get("path"):
        return event  # already v2-shaped
    path = event.get("path")
    method = event.get("httpMethod")
    if path is None and method is None:
        return event  # not a REST proxy event we can normalize
    source_ip = (rc.get("identity", {}) or {}).get("sourceIp")
    rc["http"] = {"path": path, "method": method, "sourceIp": source_ip}
    event["requestContext"] = rc
    return event
