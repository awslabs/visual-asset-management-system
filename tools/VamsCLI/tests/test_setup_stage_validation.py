"""Setup proves the API base URL it is about to store actually reaches the deployed API.

Amazon API Gateway reads the FIRST path segment as the deployment stage. A stage-less execute-api base
therefore names a stage that does not exist, and every request is answered
`403 {"message":"Forbidden"}` before any authorizer or handler runs — indistinguishable from a
permission denial. `auth login` still succeeds, because it talks to Amazon Cognito directly and never
reaches this URL, which is how the condition was twice mis-diagnosed as a permissions problem. It was
not hypothetical: 2 of 8 profiles on the developer machine stored a stage-less URL.

Checked at SETUP (owner question 89, NEW-LEAD-03 option A) rather than per command, because the
misconfiguration is created here and a per-command pre-flight was declined for its latency cost.

**The probe is unauthenticated, and that is what makes it work rather than a limitation.** Setup runs
before login, so no token exists — and none is needed, because the two failure modes answer differently
on an authenticated route requested without one. Measured against a live deployment:

    <host>/api/secure-config  -> 401 {"message":"Unauthorized"}   stage resolves; authorizer ran
    <host>/secure-config      -> 403 {"message":"Forbidden"}      stage does not exist

Both directions are asserted. A probe that rejected everything would fail every setup, including the
CloudFront-fronted case that already worked — and that is the more expensive failure, since it blocks
the configuration path entirely rather than letting one bad URL through.
"""

from unittest.mock import MagicMock, patch

import pytest

from vamscli.commands.setup import (
    normalize_base_url_for_stage,
    validate_api_gateway_reachable,
)

API = "https://abc123.execute-api.us-west-2.amazonaws.com/api"


def _response(status, text):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


class TestStageProbeClassification:
    def test_a_401_means_the_stage_resolves(self):
        """The authorizer ran and refused an absent token, which is proof the route is deployed."""
        with patch("requests.get", return_value=_response(401, '{"message":"Unauthorized"}')):
            ok, detail = validate_api_gateway_reachable(API)
        assert ok is True
        assert "401" in detail

    def test_a_403_forbidden_is_rejected_and_the_message_names_the_cause(self):
        """The defect itself. The message has to name the stage, or the operator cannot act on it."""
        with patch("requests.get", return_value=_response(403, '{"message":"Forbidden"}')):
            ok, detail = validate_api_gateway_reachable(
                "https://abc123.execute-api.us-west-2.amazonaws.com")
        assert ok is False
        assert "stage" in detail.lower()
        assert "403" in detail

    def test_a_200_is_accepted(self):
        """A fronted deployment may answer the probe route without a challenge; that is still reachable."""
        with patch("requests.get", return_value=_response(200, "{}")):
            ok, _ = validate_api_gateway_reachable("https://vams.example.com/api")
        assert ok is True

    def test_a_403_that_is_not_forbidden_is_not_treated_as_a_stage_problem(self):
        """Scope control.

        A 403 carrying an authorizer denial ("User is not authorized … explicit deny") means the request
        DID reach the authorizer — the stage is fine and the caller simply lacks permission. Treating
        every 403 as a stage error would refuse setup against a correctly configured deployment whose
        probe route is permission-restricted.
        """
        with patch("requests.get",
                   return_value=_response(403, '{"Message":"User is not authorized to access this '
                                               'resource with an explicit deny"}')):
            ok, _ = validate_api_gateway_reachable(API)
        assert ok is True

    def test_a_network_failure_does_not_block_setup(self):
        """A transient outage is not a misconfiguration.

        Returning False here would make setup impossible behind a flaky proxy, so the probe degrades to
        a reported detail rather than a hard failure — the property that keeps this check safe to run
        unconditionally.
        """
        with patch("requests.get", side_effect=OSError("connection reset")):
            ok, detail = validate_api_gateway_reachable(API)
        assert ok is True
        assert "could not reach" in detail

    def test_the_probe_url_is_built_from_the_stored_base(self):
        """The probe must test the value that will be STORED, not the bootstrap URL."""
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            return _response(401, "Unauthorized")

        with patch("requests.get", side_effect=fake_get):
            validate_api_gateway_reachable(API + "/")
        # A trailing slash on the stored base must not produce a doubled separator.
        assert captured["url"] == API + "/secure-config"


class TestNormalizerAndProbeAgree:
    @pytest.mark.parametrize("supplied", [
        "https://abc123.execute-api.us-west-2.amazonaws.com",
        "https://abc123.execute-api.us-west-2.amazonaws.com/",
    ])
    def test_a_stage_less_execute_api_url_is_normalized_before_it_is_probed(self, supplied):
        """The normaliser is the first line of defence; the probe is the check that it worked."""
        normalized = normalize_base_url_for_stage(supplied)
        assert normalized.endswith("/api")

    @pytest.mark.parametrize("supplied", [
        "https://vams.example.com",
        "https://abc123.execute-api.us-west-2.amazonaws.com/api",
    ])
    def test_a_url_that_already_resolves_is_left_alone(self, supplied):
        """Paired arm: a fronted domain absorbs the stage, and must not gain a second one."""
        assert normalize_base_url_for_stage(supplied) == supplied
