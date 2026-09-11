import pytest
from backend.backend.common.auth.clientIp import resolve_client_ip, is_ip_authorized


def _evt(source_ip=None, headers=None):
    return {
        "requestContext": {"identity": {"sourceIp": source_ip}},
        "headers": headers or {},
    }


@pytest.mark.unit
class TestResolveClientIp:
    def test_direct_uses_source_ip(self):
        evt = _evt(source_ip="203.0.113.7")
        assert resolve_client_ip(evt, fronted="none") == "203.0.113.7"

    def test_cloudfront_prefers_viewer_address(self):
        # CloudFront-Viewer-Address is "ip:port"; the port must be stripped.
        evt = _evt(source_ip="130.176.0.1",
                   headers={"CloudFront-Viewer-Address": "198.51.100.23:50314"})
        assert resolve_client_ip(evt, fronted="cloudfront") == "198.51.100.23"

    def test_cloudfront_viewer_address_ipv6(self):
        # IPv6 viewer address form is "[ipv6]:port".
        evt = _evt(source_ip="130.176.0.1",
                   headers={"CloudFront-Viewer-Address": "[2001:db8::1]:50314"})
        assert resolve_client_ip(evt, fronted="cloudfront") == "2001:db8::1"

    def test_cloudfront_falls_back_to_rightmost_untrusted_xff(self):
        # No viewer-address header; XFF trailing hop is the CloudFront peer (sourceIp),
        # so the right-most untrusted entry (the client) is used.
        evt = _evt(source_ip="70.132.0.5",
                   headers={"X-Forwarded-For": "198.51.100.23, 70.132.0.5"})
        assert resolve_client_ip(evt, fronted="cloudfront") == "198.51.100.23"

    def test_spoofed_leftmost_xff_is_not_trusted(self):
        # Behind CloudFront, an attacker prepends a fake allowed IP; the real client is the
        # last untrusted hop (the entry adjacent to the trusted CloudFront peer), never the
        # spoofed left-most.
        evt = _evt(source_ip="10.0.0.5",
                   headers={"X-Forwarded-For": "192.168.1.1, 198.51.100.23, 10.0.0.5"})
        assert resolve_client_ip(evt, fronted="cloudfront") == "198.51.100.23"

    # --- Non-CloudFront deployments: forwarding headers are NEVER trusted (anti-spoof) ---

    def test_forged_xff_ignored_when_not_cloudfront(self):
        # On an "alb"/"none" deployment the execute-api endpoint is hit directly, so a
        # client-set X-Forwarded-For (even one crafted to look proxy-forwarded) must be
        # ignored. Resolution falls through to the un-forgeable TCP peer (sourceIp).
        evt = _evt(source_ip="8.8.8.8",
                   headers={"X-Forwarded-For": "203.0.113.7, 8.8.8.8"})
        assert resolve_client_ip(evt, fronted="alb") == "8.8.8.8"
        assert resolve_client_ip(evt, fronted="none") == "8.8.8.8"

    def test_forged_viewer_address_ignored_when_not_cloudfront(self):
        # A direct caller forging CloudFront-Viewer-Address must not impersonate an allowed
        # IP on a non-CloudFront deployment; sourceIp wins.
        evt = _evt(source_ip="8.8.8.8",
                   headers={"CloudFront-Viewer-Address": "203.0.113.7:1234"})
        assert resolve_client_ip(evt, fronted="alb") == "8.8.8.8"
        assert resolve_client_ip(evt, fronted="none") == "8.8.8.8"

    # --- Direct-caller cases: must NOT fail closed even on a fronted deployment ---

    def test_direct_caller_on_cloudfront_deployment_uses_source_ip(self):
        # A client calling the execute-api URL directly carries NO CloudFront header and
        # NO X-Forwarded-For. Even though the deployment is configured fronted="cloudfront",
        # the request must resolve to its real sourceIp (existing direct integrations).
        evt = _evt(source_ip="203.0.113.7")
        assert resolve_client_ip(evt, fronted="cloudfront") == "203.0.113.7"

    def test_alb_redirect_is_direct_uses_source_ip(self):
        # ALB fronting uses a redirect, so the client then calls API Gateway directly with
        # no forwarding header; sourceIp is the real client.
        evt = _evt(source_ip="203.0.113.7")
        assert resolve_client_ip(evt, fronted="alb") == "203.0.113.7"

    def test_client_supplied_xff_without_peer_match_is_ignored(self):
        # A direct caller sets X-Forwarded-For itself, but its trailing hop is NOT the TCP
        # peer (sourceIp) — so it is untrusted and ignored; sourceIp (the real direct
        # client) is used. This prevents a direct caller from spoofing a different IP.
        evt = _evt(source_ip="203.0.113.7",
                   headers={"X-Forwarded-For": "192.168.1.1"})
        assert resolve_client_ip(evt, fronted="cloudfront") == "203.0.113.7"

    def test_no_fronted_hint_still_resolves_direct(self):
        # Default fronted="none": direct sourceIp.
        evt = _evt(source_ip="203.0.113.7")
        assert resolve_client_ip(evt) == "203.0.113.7"

    def test_unresolvable_returns_none(self):
        # No sourceIp and no usable header -> None (caller fails closed when ranges set).
        evt = _evt(source_ip=None, headers={})
        assert resolve_client_ip(evt, fronted="cloudfront") is None


@pytest.mark.unit
class TestIsIpAuthorized:
    def test_no_ranges_allows(self):
        assert is_ip_authorized("203.0.113.7", []) is True

    def test_in_range(self):
        assert is_ip_authorized("203.0.113.7", [["203.0.113.0", "203.0.113.255"]]) is True

    def test_out_of_range(self):
        assert is_ip_authorized("8.8.8.8", [["203.0.113.0", "203.0.113.255"]]) is False

    def test_none_ip_with_ranges_denies(self):
        assert is_ip_authorized(None, [["203.0.113.0", "203.0.113.255"]]) is False
