# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Normalize API Gateway REST (v1) proxy events to the canonical v2-style shape.

VAMS handlers read ``event['requestContext']['http']['path']`` / ``['method']`` and
``['sourceIp']`` (the HTTP API v2 layout). A REST API proxy event exposes these as
top-level ``path`` / ``httpMethod`` and ``requestContext.identity.sourceIp``. This
helper injects the v2-style block so handlers and the apiRoutes matcher are unchanged.
Idempotent and a no-op for events already in v2 form or for internal cross-call events.
"""

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
