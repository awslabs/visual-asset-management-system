"""The IP allow-list matcher must be family-aware: an IPv6 range is expressible and matches.

`_strip_port` has always had a dedicated branch for the bracketed CloudFront viewer-address form
(`[2001:db8::1]:50314`) and `test_clientIp.py` asserts the resolver extracts the address from it, so
the module reads as IPv6-aware. The matcher was not: it split on `.` and `int()`ed each part, so any
IPv6 literal raised `ValueError`, was caught, and became a denial — with no configuration able to
admit an IPv6 caller, because the CDK validator accepted only IPv4 dotted quads.

Every case below pairs a match with a non-match of the same family, so a matcher that simply started
allowing everything fails here just as a family-blind one does. The cross-family cases are the ones
that pin the actual rule: a configured IPv4 range must not admit an IPv6 caller and vice versa,
because the numeric comparison would otherwise be meaningless across families.
"""

import pytest

from backend.backend.common.auth.clientIp import resolve_client_ip, is_ip_authorized


V4_RANGE = ["203.0.113.0", "203.0.113.255"]
V6_RANGE = ["2001:db8::", "2001:db8::ffff"]


@pytest.mark.unit
class TestIpv6RangeMatching:
    def test_ipv6_inside_an_ipv6_range_is_authorized(self):
        assert is_ip_authorized("2001:db8::1", [V6_RANGE]) is True

    def test_ipv6_outside_an_ipv6_range_is_denied(self):
        assert is_ip_authorized("2001:db9::1", [V6_RANGE]) is False

    def test_ipv6_range_boundaries_are_inclusive(self):
        assert is_ip_authorized("2001:db8::", [V6_RANGE]) is True
        assert is_ip_authorized("2001:db8::ffff", [V6_RANGE]) is True
        assert is_ip_authorized("2001:db8::1:0", [V6_RANGE]) is False

    def test_compressed_and_expanded_spellings_are_the_same_address(self):
        expanded = "2001:0db8:0000:0000:0000:0000:0000:0001"
        assert is_ip_authorized(expanded, [V6_RANGE]) is True
        assert is_ip_authorized("2001:db8::1", [["2001:0db8::0", "2001:0db8::ffff"]]) is True

    def test_mixed_family_range_list_admits_both_families(self):
        ranges = [V4_RANGE, V6_RANGE]
        assert is_ip_authorized("203.0.113.7", ranges) is True
        assert is_ip_authorized("2001:db8::1", ranges) is True
        assert is_ip_authorized("8.8.8.8", ranges) is False
        assert is_ip_authorized("2001:db9::1", ranges) is False


@pytest.mark.unit
class TestCrossFamilyIsolation:
    def test_ipv4_range_does_not_admit_an_ipv6_caller(self):
        assert is_ip_authorized("2001:db8::1", [V4_RANGE]) is False

    def test_ipv6_range_does_not_admit_an_ipv4_caller(self):
        assert is_ip_authorized("203.0.113.7", [V6_RANGE]) is False

    def test_ipv4_still_matches_when_only_an_ipv4_range_is_configured(self):
        """Positive control for the two assertions above: the IPv4 path is unaffected."""
        assert is_ip_authorized("203.0.113.7", [V4_RANGE]) is True


@pytest.mark.unit
class TestMalformedRangesFailClosed:
    def test_unparseable_endpoint_is_skipped_not_allowed(self):
        assert is_ip_authorized("203.0.113.7", [["not-an-ip", "203.0.113.255"]]) is False

    def test_endpoints_of_different_families_are_skipped(self):
        assert is_ip_authorized("203.0.113.7", [["203.0.113.0", "2001:db8::ffff"]]) is False

    def test_a_malformed_entry_does_not_disable_a_valid_sibling(self):
        ranges = [["not-an-ip", "also-not-an-ip"], V4_RANGE]
        assert is_ip_authorized("203.0.113.7", ranges) is True
        assert is_ip_authorized("8.8.8.8", ranges) is False

    def test_a_non_pair_entry_is_skipped(self):
        assert is_ip_authorized("203.0.113.7", [["203.0.113.0"]]) is False
        assert is_ip_authorized("203.0.113.7", [None]) is False

    def test_unparseable_client_address_is_denied(self):
        assert is_ip_authorized("not-an-ip", [V4_RANGE]) is False

    def test_no_configured_range_is_still_unrestricted(self):
        """The empty list keeps meaning 'no restriction' for both families."""
        assert is_ip_authorized("203.0.113.7", []) is True
        assert is_ip_authorized("2001:db8::1", []) is True


@pytest.mark.unit
class TestEndToEndCloudFrontIpv6:
    """The resolver already extracts an IPv6 viewer address; the matcher must now accept it."""

    def _evt(self, source_ip, viewer):
        return {
            "requestContext": {"identity": {"sourceIp": source_ip}},
            "headers": {"CloudFront-Viewer-Address": viewer},
        }

    def test_bracketed_viewer_address_resolves_and_authorizes(self):
        evt = self._evt("130.176.0.1", "[2001:db8::1]:50314")
        client_ip = resolve_client_ip(evt, fronted="cloudfront")
        assert client_ip == "2001:db8::1"
        assert is_ip_authorized(client_ip, [V6_RANGE]) is True

    def test_bracketed_viewer_address_outside_the_range_is_denied(self):
        evt = self._evt("130.176.0.1", "[2001:db9::1]:50314")
        client_ip = resolve_client_ip(evt, fronted="cloudfront")
        assert client_ip == "2001:db9::1"
        assert is_ip_authorized(client_ip, [V6_RANGE]) is False
