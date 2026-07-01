# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Trusted client-IP resolution for the API Gateway authorizer.

``identity.sourceIp`` is the immediate TCP peer of the API Gateway endpoint. A single
VAMS deployment can receive API requests three ways, and IP allow-list checks must work
for all of them without breaking any:

1. **Direct to API Gateway** — existing CLIs/integrations registered against the
   execute-api URL. ``sourceIp`` is the real client. No forwarding headers are present.
2. **Behind an ALB** — the ALB rule issues an HTTP redirect to the execute-api host, so
   the client then calls API Gateway directly. This is effectively the direct case:
   ``sourceIp`` is the real client.
3. **Behind CloudFront** — CloudFront is a true reverse proxy in the request path. The
   immediate peer is a CloudFront IP, and the real client appears in the
   ``CloudFront-Viewer-Address`` header (and is appended to ``X-Forwarded-For``).

Resolution is therefore **per-request adaptive** rather than fixed at deploy time: if a
trustworthy front-injected header is present for *this* request, the real client IP is
taken from it; otherwise ``sourceIp`` is used. This means a deployment that has CloudFront
or an ALB configured still authorizes direct execute-api callers correctly (their request
simply carries no front header), so existing direct integrations are never broken.

Spoofing safety: forwarding headers are trusted **only when the deployment is actually
behind CloudFront** (``fronted == "cloudfront"``), because only then is a reverse proxy in
the request path that overwrites/appends them. On ``"alb"`` and ``"none"`` deployments the
execute-api endpoint is directly reachable and every header is client-controlled, so the
headers are ignored entirely and ``sourceIp`` (the TCP peer, which a client cannot forge)
is authoritative. This prevents a direct caller from forging ``CloudFront-Viewer-Address``
or ``X-Forwarded-For`` to impersonate an allow-listed IP. Even behind CloudFront, only the
right-most *untrusted* forwarded address (the hop adjacent to the gateway, after stripping
the proxy peer) is used, so a client-supplied left-most entry can never grant access.
"""
from typing import Optional, List


def _strip_port(addr: str) -> str:
    """Strip the port from an ``ip:port`` value. Handles IPv6 ``[ip]:port`` bracket form."""
    addr = addr.strip()
    if addr.startswith("["):
        # [IPv6]:port -> IPv6
        return addr[1:].split("]", 1)[0]
    # IPv4:port -> IPv4 (only strip when exactly one colon, else it's a bare IPv6)
    if addr.count(":") == 1:
        return addr.rsplit(":", 1)[0]
    return addr


def _rightmost_untrusted_xff(xff: str, trusted_peer: Optional[str]) -> Optional[str]:
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


def resolve_client_ip(event: dict, *, fronted: str = "none") -> Optional[str]:
    """Resolve the real client IP for an API request, adaptively per request.

    ``fronted`` is a deploy-time hint of the configured front ("cloudfront", "alb", or
    "none"). Forwarding headers are trusted **only** when ``fronted == "cloudfront"``,
    because only then is a reverse proxy in the request path that sets them. Resolution
    order:

    1. (CloudFront only) ``CloudFront-Viewer-Address`` header, if present (set by
       CloudFront; not forwardable by a client) — use the viewer IP.
    2. (CloudFront only) ``X-Forwarded-For`` whose trailing hop is the TCP peer — use the
       right-most untrusted hop.
    3. Otherwise ``identity.sourceIp`` — the TCP peer. Authoritative for direct execute-api
       callers and ALB redirects, and a client cannot forge it. On non-CloudFront
       deployments this is the only trusted source, so forged forwarding headers cannot
       impersonate an allow-listed IP.
    """
    identity = (event.get("requestContext", {}) or {}).get("identity", {}) or {}
    source_ip = identity.get("sourceIp")

    # Only a CloudFront deployment has a reverse proxy that injects trustworthy forwarding
    # headers. On "alb"/"none" the endpoint is hit directly and every header is
    # client-controlled, so they must be ignored — fall straight through to the TCP peer.
    if fronted == "cloudfront":
        headers = event.get("headers", {}) or {}
        lower = {k.lower(): v for k, v in headers.items() if isinstance(k, str)}

        # 1) CloudFront reverse-proxy path: the viewer-address header is the authoritative
        #    client IP and cannot be spoofed by a client (CloudFront overwrites it).
        viewer = lower.get("cloudfront-viewer-address")
        if viewer:
            resolved = _strip_port(viewer)
            if resolved:
                return resolved

        # 2) Trust X-Forwarded-For only when its trailing hop is the TCP peer (the
        #    CloudFront edge that actually called the gateway), then take the right-most
        #    untrusted hop. This distinguishes a real forwarded request from a spoofed one.
        xff = lower.get("x-forwarded-for")
        if xff and source_ip:
            parts = [p.strip() for p in xff.split(",") if p.strip()]
            if parts and parts[-1] == source_ip and len(parts) > 1:
                resolved = _rightmost_untrusted_xff(xff, source_ip)
                if resolved:
                    return resolved

    # 3) Direct caller (no trusted forwarding header) — including ALB redirects, existing
    #    integrations that hit the execute-api URL directly, and any non-CloudFront
    #    deployment. Use the TCP peer, which a client cannot forge.
    return source_ip


def ip_to_num(ip: str) -> int:
    return int("".join(f"{int(part):03d}" for part in ip.split(".")))


def is_ip_authorized(source_ip: Optional[str], allowed_ranges: List) -> bool:
    if not allowed_ranges:
        return True
    if not source_ip:
        return False
    try:
        source_num = ip_to_num(source_ip)
        return any(
            ip_to_num(min_ip) <= source_num <= ip_to_num(max_ip)
            for min_ip, max_ip in allowed_ranges
        )
    except (ValueError, IndexError):
        return False
