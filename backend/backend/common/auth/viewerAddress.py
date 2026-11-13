# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The viewer address of a request CloudFront forwarded, for the authorizer and for handlers.

``forwarded_client_ip`` reads the forwarding headers CloudFront sets (``CloudFront-Viewer-Address``,
then the right-most ``X-Forwarded-For`` hop that is not the proxy peer). It decides nothing about
trust: ``common.auth.clientIp.resolve_client_ip`` calls it only for a request that carries the
deployment's origin verification value, and so does ``viewer_ip_for_audit`` below.

A handler Lambda does not hold the origin verification secret, so it cannot check the value itself.
On a CloudFront deployment the authorizer, which has checked it, adds the SHA-256 digest of the
value it accepted to the authorizer context (``ORIGIN_VERIFY_DIGEST_CONTEXT_KEY``).
``viewer_ip_for_audit`` reads the forwarding headers only when the request's own origin verification
header has that digest. API Gateway sets the authorizer context from the authorizer's answer; a client
cannot set it, and the authorizer removes a verified token claim of the same name before it adds its
own entry, so only the authorizer's check sets the key. A request without the header, or with any other value, resolves to no viewer address,
so the caller keeps the connection address. The digest names the request only in audit records; it
is never an input to an authorization decision.

This module imports nothing that creates an AWS client, so a handler pays nothing to import it.
"""
import hashlib
import hmac
from typing import Any, Dict, Optional

# The origin verification header (mirrors common.auth.originVerify.ORIGIN_VERIFY_HEADER_NAME, which
# this module does not import because that module creates a Secrets Manager client at import).
ORIGIN_VERIFY_HEADER_NAME = "x-vams-origin-verify"

# The authorizer context key that carries the digest of the origin verification value the authorizer
# accepted for this request.
ORIGIN_VERIFY_DIGEST_CONTEXT_KEY = "vams:originVerifyDigest"

_DIGEST_LABEL = b"vams-origin-verify-digest:"


def origin_verify_digest(value: str) -> str:
    """The hex SHA-256 digest of an origin verification value, as the authorizer context carries it."""
    return hashlib.sha256(_DIGEST_LABEL + value.encode("utf-8")).hexdigest()


def strip_port(addr: str) -> str:
    """Strip the port from an ``ip:port`` value. Handles IPv6 ``[ip]:port`` bracket form."""
    addr = addr.strip()
    if addr.startswith("["):
        # [IPv6]:port -> IPv6
        return addr[1:].split("]", 1)[0]
    # IPv4:port -> IPv4 (only strip when exactly one colon, else it's a bare IPv6)
    if addr.count(":") == 1:
        return addr.rsplit(":", 1)[0]
    return addr


def rightmost_untrusted_xff(xff: str, trusted_peer: Optional[str]) -> Optional[str]:
    """Return the right-most X-Forwarded-For entry that we did not add (the real client).

    The entry adjacent to the gateway is the trusted proxy hop (e.g. CloudFront); the real
    client is the entry to its left. Dropping a trailing entry equal to the trusted peer
    prevents a client from spoofing access by prepending a fake left-most address.
    """
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    if not parts:
        return None
    # Drop a trailing entry equal to the trusted peer (the proxy that called us).
    if trusted_peer and parts[-1] == trusted_peer:
        parts = parts[:-1]
    if not parts:
        return None
    return parts[-1]


def lower_headers(event: Any) -> Dict[str, Any]:
    """The event's headers keyed by their lower-case name."""
    headers = event.get("headers") if isinstance(event, dict) else None
    if not isinstance(headers, dict):
        return {}
    return {k.lower(): v for k, v in headers.items() if isinstance(k, str)}


def forwarded_client_ip(lower: Dict[str, Any], source_ip: Optional[str]) -> Optional[str]:
    """The viewer address the CloudFront forwarding headers name, or None.

    1. ``CloudFront-Viewer-Address`` (CloudFront overwrites it), port stripped.
    2. ``X-Forwarded-For`` whose trailing hop is the TCP peer: the right-most hop before it.

    Call it only for a request whose origin verification value was accepted.
    """
    viewer = lower.get("cloudfront-viewer-address")
    if isinstance(viewer, str) and viewer:
        resolved = strip_port(viewer)
        if resolved:
            return resolved

    xff = lower.get("x-forwarded-for")
    if isinstance(xff, str) and xff and source_ip:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts and parts[-1] == source_ip and len(parts) > 1:
            resolved = rightmost_untrusted_xff(xff, source_ip)
            if resolved:
                return resolved
    return None


def _authorizer_context(event: Dict[str, Any]) -> Dict[str, Any]:
    request_context = event.get("requestContext")
    authorizer = request_context.get("authorizer") if isinstance(request_context, dict) else None
    if not isinstance(authorizer, dict):
        return {}
    if isinstance(authorizer.get("lambda"), dict):
        return authorizer["lambda"]
    return authorizer


def viewer_ip_for_audit(event: Any) -> Optional[str]:
    """The viewer address of an API request that came through the deployment's CloudFront, or None.

    For audit records only. The forwarding headers are read when the request's origin verification
    header has the digest the authorizer put in its context for the value it accepted; otherwise
    None, and the caller records the connection address. Never raises.
    """
    try:
        if not isinstance(event, dict):
            return None
        expected = _authorizer_context(event).get(ORIGIN_VERIFY_DIGEST_CONTEXT_KEY)
        if not isinstance(expected, str) or not expected:
            return None
        lower = lower_headers(event)
        presented = lower.get(ORIGIN_VERIFY_HEADER_NAME)
        if not isinstance(presented, str) or not presented:
            return None
        if not hmac.compare_digest(origin_verify_digest(presented), expected):
            return None
        request_context = event.get("requestContext") if isinstance(event.get("requestContext"), dict) else {}
        identity = request_context.get("identity") if isinstance(request_context.get("identity"), dict) else {}
        http = request_context.get("http") if isinstance(request_context.get("http"), dict) else {}
        source_ip = identity.get("sourceIp") or http.get("sourceIp")
        return forwarded_client_ip(lower, source_ip)
    except Exception:
        return None
