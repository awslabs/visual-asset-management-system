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

Header trust: the forwarding headers are read only on a CloudFront deployment
(``fronted == "cloudfront"``), and there only from a request that carries the deployment's
origin verification value — the header CloudFront adds to every request it forwards
(``common.auth.originVerify``). Every other request resolves to ``sourceIp``, the TCP peer:
every request on ``"alb"`` and ``"none"`` deployments, where the execute-api endpoint is
reached directly, and on a CloudFront deployment every request that reaches the execute-api
endpoint or the API custom domain without that value. Behind CloudFront, only the right-most
forwarded address that is not the proxy peer (the hop adjacent to the gateway) is used.
"""
import ipaddress
from typing import Optional, List

from common.auth import originVerify
from customLogging.logger import safeLogger

logger = safeLogger(service_name="ClientIp")


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
    "none"). Forwarding headers are read **only** when ``fronted == "cloudfront"`` and the
    request carries a valid origin verification value (``originVerify``). Resolution order:

    1. (CloudFront, verified) ``CloudFront-Viewer-Address`` header, if present (set by
       CloudFront) — use the viewer IP.
    2. (CloudFront, verified) ``X-Forwarded-For`` whose trailing hop is the TCP peer — use the
       right-most untrusted hop.
    3. Otherwise ``identity.sourceIp`` — the TCP peer. Used for direct execute-api and
       custom-domain callers, ALB redirects, every non-CloudFront deployment, and any request
       without a valid origin verification value.
    """
    identity = (event.get("requestContext", {}) or {}).get("identity", {}) or {}
    source_ip = identity.get("sourceIp")

    # Only a CloudFront deployment has a reverse proxy that injects trustworthy forwarding
    # headers. On "alb"/"none" the endpoint is hit directly and every header is
    # client-controlled, so they must be ignored — fall straight through to the TCP peer.
    if fronted == "cloudfront":
        headers = event.get("headers", {}) or {}
        lower = {k.lower(): v for k, v in headers.items() if isinstance(k, str)}

        # The forwarding headers count only on a request CloudFront forwarded, which carries the
        # deployment's origin verification value; any other request resolves to the TCP peer.
        if not originVerify.is_valid_origin_verify_value(
            lower.get(originVerify.ORIGIN_VERIFY_HEADER_NAME)
        ):
            return source_ip

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


def is_ip_authorized(source_ip: Optional[str], allowed_ranges: List) -> bool:
    """Whether a client address falls inside one of the configured ``[min, max]`` ranges.

    Ranges are inclusive and are compared numerically via ``ipaddress``, so both IPv4 and IPv6
    are expressible: ``[["203.0.113.0", "203.0.113.255"]]`` and
    ``[["2001:db8::", "2001:db8::ffff"]]`` are both valid, and a deployment may configure ranges
    of both families side by side.

    An empty range list means unrestricted. A range whose two endpoints are different families,
    or either of whose endpoints does not parse, is skipped rather than allowed — a malformed
    entry must not widen access — and a caller whose family has no configured range at all is
    logged, because that is the case where an operator has restricted by IPv4 and an IPv6 viewer
    is denied with no configuration able to admit it.
    """
    if not allowed_ranges:
        return True
    if not source_ip:
        return False

    try:
        source = ipaddress.ip_address(str(source_ip).strip())
    except (ValueError, TypeError):
        # Fail closed: an address this function cannot parse must not be admitted, and must not
        # raise into the authorizer either, where it would answer 500 instead of denying.
        logger.warning("Client address is not a valid IP address; denying")
        return False

    family_has_a_range = False
    for entry in allowed_ranges:
        try:
            min_ip, max_ip = entry
            low = ipaddress.ip_address(str(min_ip).strip())
            high = ipaddress.ip_address(str(max_ip).strip())
        except (ValueError, TypeError):
            logger.warning("Skipping malformed allowed IP range entry")
            continue

        if low.version != high.version:
            logger.warning("Skipping allowed IP range whose endpoints are different IP versions")
            continue
        if low.version != source.version:
            continue

        family_has_a_range = True
        if low <= source <= high:
            return True

    if not family_has_a_range:
        logger.warning(
            f"No allowed IP range is configured for IPv{source.version}, so every caller of that "
            f"family is denied. Add an IPv{source.version} range to "
            "authorizerOptions.allowedIpRanges."
        )
    return False
