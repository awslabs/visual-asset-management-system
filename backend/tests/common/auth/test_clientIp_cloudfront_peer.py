# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""CloudFront's ``X-Forwarded-For`` counts only for a request whose TCP peer is CloudFront.

A CloudFront deployment's execute-api endpoint is also reachable directly, and a direct request
carries whatever headers its caller set. ``resolve_client_ip`` therefore reads ``X-Forwarded-For``
only when ``identity.sourceIp`` is one of CloudFront's origin-facing addresses (the bundled
snapshot, united with a list refreshed at run time), and otherwise evaluates the request by its
connection address. ``CloudFront-Viewer-Address`` is never read, whatever the peer.

Every header case below pairs a CloudFront peer (the header is read) with a non-CloudFront peer
(the same header is not), so an implementation that ignored the headers outright fails here just
as one that read them for everyone does. Non-CloudFront peers are documentation addresses, which
``test_cloudFrontOriginFacingRanges.py`` proves are outside the snapshot.
"""
import ipaddress
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.common.auth import clientIp
from backend.backend.common.auth import generateCloudFrontOriginFacingRanges as generator
from backend.backend.common.auth.clientIp import (
    is_cloudfront_origin_facing,
    is_ip_authorized,
    resolve_client_ip,
)
from backend.backend.common.auth import authorizerCore as core
from backend.tests.cloudFrontRangesStub import CLOUDFRONT_PEER_V4, CLOUDFRONT_PEER_V6

# The real downloader, bound before the autouse fixture can stand anything in for it.
_REAL_DOWNLOAD = clientIp._download_origin_facing

DIRECT_PEER_V4 = "203.0.113.50"
DIRECT_PEER_V6 = "2001:db8:ffff::50"
VIEWER = "198.51.100.23"
VIEWER_V6 = "2001:db8::1"
# A different address in CloudFront-Viewer-Address, so a test can tell which header answered.
OTHER_VIEWER = "203.0.113.77"
# A prefix CloudFront might add after the snapshot was taken (a documentation range here).
LATER_PREFIX = ipaddress.ip_network("192.0.2.0/24")
LATER_PEER = "192.0.2.10"


def _evt(source_ip, headers=None):
    return {
        "requestContext": {"identity": {"sourceIp": source_ip}},
        "headers": headers or {},
    }


class _Clock:
    """A settable stand-in for time.monotonic()."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def _downloads_serving(networks):
    """A _download_origin_facing stand-in that serves networks and records each attempt."""
    attempts = []

    def download():
        attempts.append(1)
        return networks

    return download, attempts


def _downloads_failing():
    attempts = []

    def download():
        attempts.append(1)
        raise ConnectionError("no route to the published document")

    return download, attempts


@pytest.mark.unit
class TestHeadersCountOnlyFromCloudFrontPeers:
    def test_viewer_address_alone_from_a_cloudfront_peer_resolves_to_the_peer(self):
        evt = _evt(CLOUDFRONT_PEER_V4, {"CloudFront-Viewer-Address": f"{VIEWER}:50314"})
        assert resolve_client_ip(evt, fronted="cloudfront") == CLOUDFRONT_PEER_V4

    def test_viewer_address_from_a_direct_peer_is_not_read(self):
        evt = _evt(DIRECT_PEER_V4, {"CloudFront-Viewer-Address": f"{VIEWER}:50314"})
        assert resolve_client_ip(evt, fronted="cloudfront") == DIRECT_PEER_V4

    def test_forwarded_for_from_a_cloudfront_peer_is_read(self):
        evt = _evt(CLOUDFRONT_PEER_V4, {"X-Forwarded-For": f"{VIEWER}, {CLOUDFRONT_PEER_V4}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == VIEWER

    def test_both_headers_from_a_cloudfront_peer_resolve_from_forwarded_for(self):
        evt = _evt(
            CLOUDFRONT_PEER_V4,
            {
                "CloudFront-Viewer-Address": f"{OTHER_VIEWER}:443",
                "X-Forwarded-For": f"{VIEWER}, {CLOUDFRONT_PEER_V4}",
            },
        )
        assert resolve_client_ip(evt, fronted="cloudfront") == VIEWER

    def test_forwarded_for_from_a_direct_peer_is_not_read(self):
        # The caller sent one entry and API Gateway appended the TCP peer, so the trailing hop
        # matches sourceIp exactly as it does behind CloudFront. Only the peer tells them apart.
        evt = _evt(DIRECT_PEER_V4, {"X-Forwarded-For": f"{VIEWER}, {DIRECT_PEER_V4}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == DIRECT_PEER_V4

    def test_both_headers_from_a_direct_peer_are_not_read(self):
        evt = _evt(
            DIRECT_PEER_V4,
            {
                "CloudFront-Viewer-Address": f"{VIEWER}:443",
                "X-Forwarded-For": f"{VIEWER}, {DIRECT_PEER_V4}",
            },
        )
        assert resolve_client_ip(evt, fronted="cloudfront") == DIRECT_PEER_V4

    def test_header_names_are_matched_case_insensitively(self):
        evt = _evt(CLOUDFRONT_PEER_V4, {"x-FORWARDED-for": f"{VIEWER}, {CLOUDFRONT_PEER_V4}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == VIEWER


@pytest.mark.unit
class TestIpv6Peers:
    def test_viewer_address_alone_from_an_ipv6_cloudfront_peer_resolves_to_the_peer(self):
        evt = _evt(CLOUDFRONT_PEER_V6, {"CloudFront-Viewer-Address": f"[{VIEWER_V6}]:50314"})
        assert resolve_client_ip(evt, fronted="cloudfront") == CLOUDFRONT_PEER_V6

    def test_forwarded_for_from_an_ipv6_cloudfront_peer_is_read(self):
        evt = _evt(CLOUDFRONT_PEER_V6, {"X-Forwarded-For": f"{VIEWER_V6}, {CLOUDFRONT_PEER_V6}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == VIEWER_V6

    def test_viewer_address_from_an_ipv6_direct_peer_is_not_read(self):
        evt = _evt(DIRECT_PEER_V6, {"CloudFront-Viewer-Address": f"[{VIEWER_V6}]:50314"})
        assert resolve_client_ip(evt, fronted="cloudfront") == DIRECT_PEER_V6

    def test_forwarded_for_from_an_ipv6_direct_peer_is_not_read(self):
        evt = _evt(DIRECT_PEER_V6, {"X-Forwarded-For": f"{VIEWER_V6}, {DIRECT_PEER_V6}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == DIRECT_PEER_V6


@pytest.mark.unit
class TestMalformedHeaders:
    def test_unparseable_viewer_address_falls_back_to_the_peer(self):
        evt = _evt(CLOUDFRONT_PEER_V4, {"CloudFront-Viewer-Address": "not-an-address:443"})
        assert resolve_client_ip(evt, fronted="cloudfront") == CLOUDFRONT_PEER_V4

    def test_unparseable_viewer_address_does_not_displace_forwarded_for(self):
        evt = _evt(
            CLOUDFRONT_PEER_V4,
            {
                "CloudFront-Viewer-Address": "garbage",
                "X-Forwarded-For": f"{VIEWER}, {CLOUDFRONT_PEER_V4}",
            },
        )
        assert resolve_client_ip(evt, fronted="cloudfront") == VIEWER

    def test_unparseable_forwarded_for_entry_falls_back_to_the_peer(self):
        evt = _evt(CLOUDFRONT_PEER_V4, {"X-Forwarded-For": f"unknown, {CLOUDFRONT_PEER_V4}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == CLOUDFRONT_PEER_V4

    def test_non_string_header_values_are_ignored(self):
        evt = _evt(
            CLOUDFRONT_PEER_V4,
            {"CloudFront-Viewer-Address": ["198.51.100.23:1"], "X-Forwarded-For": 7},
        )
        assert resolve_client_ip(evt, fronted="cloudfront") == CLOUDFRONT_PEER_V4

    def test_null_headers_resolve_to_the_peer(self):
        evt = {"requestContext": {"identity": {"sourceIp": CLOUDFRONT_PEER_V4}}, "headers": None}
        assert resolve_client_ip(evt, fronted="cloudfront") == CLOUDFRONT_PEER_V4

    def test_malformed_header_from_a_direct_peer_resolves_to_the_peer(self):
        evt = _evt(DIRECT_PEER_V4, {"X-Forwarded-For": f"[::zz], {DIRECT_PEER_V4}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == DIRECT_PEER_V4


@pytest.mark.unit
class TestOtherFrontsUnaffected:
    """ALB and unfronted deployments (and therefore GovCloud and the EU sovereign templates,
    which cannot enable CloudFront) never read the headers, whatever the peer."""

    @pytest.mark.parametrize("fronted", ["alb", "none"])
    @pytest.mark.parametrize("peer", [CLOUDFRONT_PEER_V4, DIRECT_PEER_V4])
    def test_headers_are_ignored(self, fronted, peer):
        evt = _evt(
            peer,
            {
                "CloudFront-Viewer-Address": f"{VIEWER}:443",
                "X-Forwarded-For": f"{VIEWER}, {peer}",
            },
        )
        assert resolve_client_ip(evt, fronted=fronted) == peer

    @pytest.mark.parametrize("fronted", ["alb", "none"])
    def test_the_address_list_is_never_consulted(self, fronted, cloudfront_refresh_offline):
        evt = _evt(LATER_PEER, {"X-Forwarded-For": f"{VIEWER}, {LATER_PEER}"})
        assert resolve_client_ip(evt, fronted=fronted) == LATER_PEER
        assert cloudfront_refresh_offline.attempted_urls == []


@pytest.mark.unit
class TestAllowListDecision:
    """End to end through authenticate_request: the allow-list judges the resolved address."""

    def _decide(self, peer, headers):
        evt = {
            "requestContext": {"identity": {"sourceIp": peer}, "http": {"path": "/database"}},
            "path": "/database",
            "httpMethod": "GET",
            "headers": headers,
        }
        with patch.object(core, "ALLOWED_IP_RANGES", [["198.51.100.0", "198.51.100.255"]]), \
             patch.object(core, "IGNORED_PATHS", ["/database"]):
            return core.authenticate_request(evt, fronted="cloudfront")

    def test_allowed_viewer_behind_cloudfront_is_admitted(self):
        res = self._decide(CLOUDFRONT_PEER_V4, {"X-Forwarded-For": f"{VIEWER}, {CLOUDFRONT_PEER_V4}"})
        assert res["authorized"] is True

    def test_viewer_address_alone_behind_cloudfront_is_judged_by_the_peer(self):
        res = self._decide(CLOUDFRONT_PEER_V4, {"CloudFront-Viewer-Address": f"{VIEWER}:443"})
        assert res["authorized"] is False
        assert res["reason"] == "IP address not authorized"

    def test_direct_caller_naming_an_allowed_address_is_judged_by_its_connection(self):
        res = self._decide(
            DIRECT_PEER_V4,
            {
                "CloudFront-Viewer-Address": f"{VIEWER}:443",
                "X-Forwarded-For": f"{VIEWER}, {DIRECT_PEER_V4}",
            },
        )
        assert res["authorized"] is False
        assert res["reason"] == "IP address not authorized"

    def test_direct_caller_inside_the_allowed_range_is_admitted(self):
        res = self._decide("198.51.100.77", {})
        assert res["authorized"] is True


@pytest.mark.unit
class TestNoLookupWithoutHeaders:
    def test_direct_request_without_headers_never_consults_the_list(self, cloudfront_refresh_offline):
        assert resolve_client_ip(_evt(DIRECT_PEER_V4), fronted="cloudfront") == DIRECT_PEER_V4
        assert cloudfront_refresh_offline.attempted_urls == []

    def test_snapshot_peer_never_triggers_a_refresh(self, cloudfront_refresh_offline):
        evt = _evt(CLOUDFRONT_PEER_V4, {"X-Forwarded-For": f"{VIEWER}, {CLOUDFRONT_PEER_V4}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == VIEWER
        assert cloudfront_refresh_offline.attempted_urls == []

    def test_unknown_peer_with_headers_attempts_one_refresh(self, cloudfront_refresh_offline):
        evt = _evt(DIRECT_PEER_V4, {"X-Forwarded-For": f"{VIEWER}, {DIRECT_PEER_V4}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == DIRECT_PEER_V4
        assert cloudfront_refresh_offline.attempted_urls == [clientIp.ORIGIN_FACING_SOURCE_URL]

    def test_viewer_address_alone_never_consults_the_list(self, cloudfront_refresh_offline):
        evt = _evt(DIRECT_PEER_V4, {"CloudFront-Viewer-Address": f"{VIEWER}:443"})
        assert resolve_client_ip(evt, fronted="cloudfront") == DIRECT_PEER_V4
        assert cloudfront_refresh_offline.attempted_urls == []


@pytest.mark.unit
class TestOriginFacingMembership:
    def test_snapshot_addresses_of_both_families_are_origin_facing(self):
        assert is_cloudfront_origin_facing(CLOUDFRONT_PEER_V4) is True
        assert is_cloudfront_origin_facing(CLOUDFRONT_PEER_V6) is True

    @pytest.mark.parametrize("value", [None, "", "not-an-ip", "198.51.100.0/24", 12])
    def test_a_value_that_is_not_an_address_is_not_origin_facing(self, value, cloudfront_refresh_offline):
        assert is_cloudfront_origin_facing(value) is False
        assert cloudfront_refresh_offline.attempted_urls == []

    def test_snapshot_only_when_the_refresh_fails(self, monkeypatch):
        download, attempts = _downloads_failing()
        monkeypatch.setattr(clientIp, "_download_origin_facing", download)
        assert is_cloudfront_origin_facing(LATER_PEER) is False
        assert is_cloudfront_origin_facing(CLOUDFRONT_PEER_V4) is True
        assert attempts, "the refresh was never attempted"

    def test_a_failed_refresh_is_logged_once_not_per_request(self, monkeypatch):
        clock = _Clock()
        download, attempts = _downloads_failing()
        logger = MagicMock()
        monkeypatch.setattr(clientIp, "_monotonic", clock)
        monkeypatch.setattr(clientIp, "_download_origin_facing", download)
        monkeypatch.setattr(clientIp, "logger", logger)

        is_cloudfront_origin_facing(LATER_PEER)
        clock.now += clientIp.REFRESH_RETRY_AFTER_FAILURE_SECONDS + 1
        is_cloudfront_origin_facing(LATER_PEER)

        assert len(attempts) >= 2, "the second window never retried"
        assert logger.warning.call_count == 1

    def test_refreshed_prefixes_are_united_with_the_snapshot(self, monkeypatch):
        # The refreshed list holds only a later prefix: it adds that prefix and removes nothing.
        download, attempts = _downloads_serving({4: (LATER_PREFIX,), 6: ()})
        monkeypatch.setattr(clientIp, "_download_origin_facing", download)
        assert is_cloudfront_origin_facing(LATER_PEER) is True
        assert is_cloudfront_origin_facing(CLOUDFRONT_PEER_V4) is True
        assert is_cloudfront_origin_facing(CLOUDFRONT_PEER_V6) is True
        assert attempts, "the refresh was never attempted"

    def test_a_peer_only_the_refresh_knows_has_its_headers_read(self, monkeypatch):
        download, _ = _downloads_serving({4: (LATER_PREFIX,), 6: ()})
        monkeypatch.setattr(clientIp, "_download_origin_facing", download)
        evt = _evt(LATER_PEER, {"X-Forwarded-For": f"{VIEWER}, {LATER_PEER}"})
        assert resolve_client_ip(evt, fronted="cloudfront") == VIEWER

    def test_a_refresh_is_reused_for_six_hours(self, monkeypatch):
        clock = _Clock()
        download, attempts = _downloads_serving({4: (LATER_PREFIX,), 6: ()})
        monkeypatch.setattr(clientIp, "_monotonic", clock)
        monkeypatch.setattr(clientIp, "_download_origin_facing", download)

        assert is_cloudfront_origin_facing(DIRECT_PEER_V4) is False
        after_first = len(attempts)
        assert after_first >= 1, "the first lookup never refreshed"

        clock.now += clientIp.REFRESH_INTERVAL_SECONDS - 1
        assert is_cloudfront_origin_facing(DIRECT_PEER_V4) is False
        assert is_cloudfront_origin_facing(LATER_PEER) is True
        assert len(attempts) == after_first, "a refresh inside the 6 h window downloaded again"

        clock.now += 2
        is_cloudfront_origin_facing(DIRECT_PEER_V4)
        assert len(attempts) > after_first, "the list was not refreshed after 6 h"

    def test_a_failed_attempt_is_not_repeated_inside_its_retry_window(self, monkeypatch):
        clock = _Clock()
        download, attempts = _downloads_failing()
        monkeypatch.setattr(clientIp, "_monotonic", clock)
        monkeypatch.setattr(clientIp, "_download_origin_facing", download)

        is_cloudfront_origin_facing(DIRECT_PEER_V4)
        after_first = len(attempts)
        assert after_first >= 1, "the first lookup never attempted a refresh"

        clock.now += clientIp.REFRESH_RETRY_AFTER_FAILURE_SECONDS - 1
        is_cloudfront_origin_facing(DIRECT_PEER_V4)
        assert len(attempts) == after_first, "a failed attempt was repeated inside its window"

        clock.now += 2
        is_cloudfront_origin_facing(DIRECT_PEER_V4)
        assert len(attempts) > after_first, "no new attempt after the retry window"

    def test_a_refresh_with_prefixes_missing_from_the_snapshot_says_so(self, monkeypatch):
        logger = MagicMock()
        download, _ = _downloads_serving({4: (LATER_PREFIX,), 6: ()})
        monkeypatch.setattr(clientIp, "_download_origin_facing", download)
        monkeypatch.setattr(clientIp, "logger", logger)
        is_cloudfront_origin_facing(LATER_PEER)
        assert logger.warning.called
        assert "regenerate" in logger.warning.call_args[0][0]

    def test_a_refresh_the_snapshot_already_covers_is_quiet(self, monkeypatch):
        logger = MagicMock()
        snapshot = clientIp._SNAPSHOT_NETWORKS
        download, attempts = _downloads_serving({4: snapshot[4], 6: snapshot[6]})
        monkeypatch.setattr(clientIp, "_download_origin_facing", download)
        monkeypatch.setattr(clientIp, "logger", logger)
        is_cloudfront_origin_facing(LATER_PEER)
        assert attempts, "the refresh was never attempted"
        assert not logger.warning.called


class _Response:
    def __init__(self, status_code=200, body=b"", chunk_size=1024):
        self.status_code = status_code
        self._body = body
        self._chunk_size = chunk_size
        self.closed = False

    def iter_content(self, chunk_size=None):
        for start in range(0, len(self._body), self._chunk_size):
            yield self._body[start:start + self._chunk_size]

    def close(self):
        self.closed = True


class _Requests:
    """Stands in for ``requests`` inside clientIp, serving one canned response per GET."""

    def __init__(self, response):
        self.response = response
        self.gets = []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return self.response


def _document(ipv4=("192.0.2.0/24",), ipv6=("2001:db8:1::/48",), service=clientIp.ORIGIN_FACING_SERVICE):
    return {
        "syncToken": "1",
        "createDate": "2026-10-24-00-00-00",
        "prefixes": [{"ip_prefix": p, "region": "GLOBAL", "service": service} for p in ipv4]
        + [{"ip_prefix": "198.51.100.0/24", "region": "us-east-1", "service": "EC2"}],
        "ipv6_prefixes": [{"ipv6_prefix": p, "region": "GLOBAL", "service": service} for p in ipv6]
        + [{"ipv6_prefix": "2001:db8:2::/48", "region": "us-east-1", "service": "EC2"}],
    }


@pytest.mark.unit
class TestDownload:
    def _serve(self, monkeypatch, response):
        fake = _Requests(response)
        monkeypatch.setattr(clientIp, "requests", fake)
        return fake

    def test_one_short_timeout_get_with_no_redirects(self, monkeypatch):
        fake = self._serve(monkeypatch, _Response(body=json.dumps(_document()).encode()))
        _REAL_DOWNLOAD()
        assert fake.gets, "no request was made"
        url, kwargs = fake.gets[0]
        assert url == "https://ip-ranges.amazonaws.com/ip-ranges.json"
        assert kwargs["timeout"] == (2, 2)
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        assert len(fake.gets) <= 1, "an attempt issued more than one request"

    def test_returns_only_the_origin_facing_prefixes(self, monkeypatch):
        response = _Response(body=json.dumps(_document()).encode())
        self._serve(monkeypatch, response)
        networks = _REAL_DOWNLOAD()
        assert networks[4] == (ipaddress.ip_network("192.0.2.0/24"),)
        assert networks[6] == (ipaddress.ip_network("2001:db8:1::/48"),)
        assert response.closed is True

    def test_a_non_200_answer_fails_the_attempt(self, monkeypatch):
        response = _Response(status_code=503, body=b"{}")
        self._serve(monkeypatch, response)
        with pytest.raises(ValueError):
            _REAL_DOWNLOAD()
        assert response.closed is True

    def test_an_oversized_document_fails_the_attempt(self, monkeypatch):
        self._serve(monkeypatch, _Response(body=json.dumps(_document()).encode(), chunk_size=64))
        monkeypatch.setattr(clientIp, "REFRESH_MAX_DOCUMENT_BYTES", 100)
        with pytest.raises(ValueError):
            _REAL_DOWNLOAD()

    def test_a_slow_download_fails_the_attempt(self, monkeypatch):
        clock = _Clock()
        body = json.dumps(_document()).encode()

        class _SlowResponse(_Response):
            def iter_content(self, chunk_size=None):
                for chunk in super().iter_content(chunk_size):
                    clock.now += clientIp.REFRESH_DOWNLOAD_SECONDS
                    yield chunk

        self._serve(monkeypatch, _SlowResponse(body=body, chunk_size=64))
        monkeypatch.setattr(clientIp, "_monotonic", clock)
        with pytest.raises(TimeoutError):
            _REAL_DOWNLOAD()

    def test_a_document_without_origin_facing_prefixes_fails_the_attempt(self, monkeypatch):
        self._serve(monkeypatch, _Response(body=json.dumps(_document(service="EC2")).encode()))
        with pytest.raises(ValueError):
            _REAL_DOWNLOAD()

    def test_a_body_that_is_not_json_fails_the_attempt(self, monkeypatch):
        self._serve(monkeypatch, _Response(body=b"<html>not json</html>"))
        with pytest.raises(ValueError):
            _REAL_DOWNLOAD()

    def test_a_failed_download_leaves_the_snapshot_answering(self, monkeypatch):
        # The real downloader end to end: a 500 fails the attempt and the snapshot still answers.
        self._serve(monkeypatch, _Response(status_code=500, body=b""))
        assert is_cloudfront_origin_facing(LATER_PEER) is False
        assert is_cloudfront_origin_facing(CLOUDFRONT_PEER_V4) is True


@pytest.mark.unit
class TestParserAgreesWithTheGenerator:
    """The run-time parser and the snapshot generator must select the same prefixes."""

    def test_same_prefixes_from_the_same_document(self):
        document = _document(
            ipv4=("192.0.2.0/24", "198.18.0.0/15", "192.0.2.0/24"),
            ipv6=("2001:db8:1::/48", "2001:db8:3::/48"),
        )
        _, gen_ipv4, gen_ipv6 = generator.extract_origin_facing(document)
        parsed = clientIp.parse_origin_facing(document)
        assert {str(n) for n in parsed[4]} == set(gen_ipv4)
        assert {str(n) for n in parsed[6]} == set(gen_ipv6)

    def test_both_read_the_same_source_and_service(self):
        assert clientIp.ORIGIN_FACING_SOURCE_URL == generator.SOURCE_URL
        assert clientIp.ORIGIN_FACING_SERVICE == generator.SERVICE

    def test_parser_returns_none_for_a_document_without_the_service(self):
        assert clientIp.parse_origin_facing(_document(service="EC2")) is None
        assert clientIp.parse_origin_facing([]) is None


@pytest.mark.unit
class TestMatcherUnchanged:
    """Positive control: the allow-list matcher itself is not part of this change."""

    def test_matcher_still_admits_inside_and_denies_outside(self):
        ranges = [["198.51.100.0", "198.51.100.255"]]
        assert is_ip_authorized(VIEWER, ranges) is True
        assert is_ip_authorized(DIRECT_PEER_V4, ranges) is False
