# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Test support for the CloudFront origin-facing address check in ``common/auth/clientIp.py``.

The resolver reads ``X-Forwarded-For`` only when the TCP peer (``identity.sourceIp``) is a
CloudFront origin-facing address, so authorizer tests need three things:

* **A peer inside the list.** ``CLOUDFRONT_PEER_V4`` / ``CLOUDFRONT_PEER_V6`` are taken from the
  bundled snapshot rather than hard-coded, so regenerating the snapshot cannot leave a fixture
  outside it. Non-CloudFront peers in these tests use the documentation ranges (192.0.2.0/24,
  198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32), which the snapshot guard test proves are absent.
  A request with one of these peers as ``identity.sourceIp`` tests the guard. Traffic through the
  distribution arrives with the viewer as ``identity.sourceIp`` and the edge as the trailing
  ``X-Forwarded-For`` hop, and the tests that cover it model it that way.
* **No network.** For a request that carries ``X-Forwarded-For`` (trailing hop equal to
  ``identity.sourceIp``) from a peer the snapshot does not hold, clientIp refreshes the list from
  the published document. ``isolate_refresh`` replaces the
  ``requests`` module clientIp calls with ``OfflineRequests`` (every GET fails, as an unreachable
  host would) and resets the refresh state. It does so in EVERY loaded copy of the module: the
  suite loads clientIp under more than one name, and an authorizer module keeps the copy it was
  imported against, so the copy is found through ``resolve_client_ip.__globals__`` as well as by
  name (``backend/tests/CLAUDE.md``: patch the object the function's ``__globals__`` resolves).
* **A record of list checks.** ``record_origin_facing_checks`` wraps ``is_cloudfront_origin_facing``
  in every loaded copy and records each address checked, so a test can assert that a request did
  not consult the list at all.

Both auth test directories apply ``isolate_refresh`` through an autouse fixture in their
``conftest.py``; a test that wants a refresh to succeed patches ``_download_origin_facing`` or
``requests`` on the module it drives.
"""
import ipaddress
import sys

from backend.backend.common.auth.cloudFrontOriginFacingRanges import IPV4_PREFIXES, IPV6_PREFIXES

CLOUDFRONT_PEER_V4 = str(next(ipaddress.ip_network(IPV4_PREFIXES[0]).hosts()))
CLOUDFRONT_PEER_V6 = str(next(ipaddress.ip_network(IPV6_PREFIXES[0]).hosts()))

_CLIENT_IP_MODULES = ("common.auth.clientIp", "backend.backend.common.auth.clientIp")
_RESOLVER_HOLDERS = (
    "common.auth.authorizerCore",
    "backend.backend.common.auth.authorizerCore",
    "handlers.auth.apiGatewayAuthorizerRest",
    "backend.backend.handlers.auth.apiGatewayAuthorizerRest",
)


class OfflineRequests:
    """Stands in for ``requests`` inside clientIp: every GET fails before any byte moves."""

    def __init__(self):
        self.attempted_urls = []

    def get(self, url, **kwargs):
        self.attempted_urls.append(url)
        raise ConnectionError("unit tests do not reach the network")


def client_ip_namespaces():
    """The global namespaces of every loaded copy of clientIp."""
    found = {}
    for name in _CLIENT_IP_MODULES:
        namespace = getattr(sys.modules.get(name), "__dict__", None)
        if isinstance(namespace, dict) and "_download_origin_facing" in namespace:
            found[id(namespace)] = namespace
    for name in _RESOLVER_HOLDERS:
        module = sys.modules.get(name)
        resolver = getattr(module, "resolve_client_ip", None) if module is not None else None
        namespace = getattr(resolver, "__globals__", None)
        if isinstance(namespace, dict) and "_download_origin_facing" in namespace:
            found[id(namespace)] = namespace
    return list(found.values())


def isolate_refresh(monkeypatch):
    """Take every clientIp copy offline with fresh refresh state; returns the shared stub."""
    offline = OfflineRequests()
    for namespace in client_ip_namespaces():
        monkeypatch.setitem(namespace, "requests", offline)
        monkeypatch.setitem(namespace, "_refreshed_networks", {4: (), 6: ()})
        monkeypatch.setitem(namespace, "_next_refresh_at", 0.0)
        monkeypatch.setitem(namespace, "_consecutive_refresh_failures", 0)
        monkeypatch.setitem(namespace, "_refresh_failure_logged", False)
    return offline


def record_origin_facing_checks(monkeypatch):
    """Record every address any loaded clientIp copy checks against the origin-facing list.

    Returns the list the checked addresses are appended to. Each check still runs and answers
    as before, so a test can assert both on the resolved address and on whether the list was
    consulted.
    """
    checked = []
    for namespace in client_ip_namespaces():
        real_check = namespace["is_cloudfront_origin_facing"]

        def recording_check(address, _real_check=real_check):
            checked.append(address)
            return _real_check(address)

        monkeypatch.setitem(namespace, "is_cloudfront_origin_facing", recording_check)
    return checked
