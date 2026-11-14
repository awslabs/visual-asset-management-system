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
from common.auth.viewerAddress import (
    ORIGIN_VERIFY_DIGEST_CONTEXT_KEY,
    forwarded_client_ip,
    origin_verify_digest,
    rightmost_untrusted_xff,
    strip_port,
)
from customLogging.logger import safeLogger

logger = safeLogger()


# The forwarding-header parsing is shared with the audit records' viewer address
# (common/auth/viewerAddress.py); the names below are kept for existing callers.
_strip_port = strip_port
_rightmost_untrusted_xff = rightmost_untrusted_xff


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
        # 2) Trust X-Forwarded-For only when its trailing hop is the TCP peer (the
        #    CloudFront edge that actually called the gateway), then take the right-most
        #    untrusted hop. This distinguishes a real forwarded request from a spoofed one.
        resolved = forwarded_client_ip(lower, source_ip)
        if resolved:
            return resolved

    # 3) Direct caller (no trusted forwarding header) — including ALB redirects, existing
    #    integrations that hit the execute-api URL directly, and any non-CloudFront
    #    deployment. Use the TCP peer, which a client cannot forge.
    return source_ip


def origin_verify_audit_context(event: dict, *, fronted: str = "none") -> dict:
    """The authorizer context entry that lets handlers name the viewer in audit records.

    On a CloudFront deployment, for a request that carries an accepted origin verification value,
    returns ``{ORIGIN_VERIFY_DIGEST_CONTEXT_KEY: <digest of that value>}``; otherwise ``{}``.
    ``common.auth.viewerAddress.viewer_ip_for_audit`` compares the digest with the request's own
    header before it reads the forwarding headers, so a cached authorizer answer reused for a request
    that did not come through CloudFront names the connection address. The entry is for audit records
    only; no authorization decision reads it. Never raises.
    """
    try:
        if fronted != "cloudfront":
            return {}
        headers = event.get("headers", {}) or {}
        lower = {k.lower(): v for k, v in headers.items() if isinstance(k, str)}
        value = lower.get(originVerify.ORIGIN_VERIFY_HEADER_NAME)
        if not originVerify.is_valid_origin_verify_value(value):
            return {}
        return {ORIGIN_VERIFY_DIGEST_CONTEXT_KEY: origin_verify_digest(value)}
    except Exception:
        return {}


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
