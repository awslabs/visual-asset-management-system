"""Which party answered an HTTP 403, and what the client does about it.

Amazon API Gateway, the custom authorizer, the API's resource policy, the deployment's AWS WAF web ACL
and the VAMS handlers all answer with a 403, each in its own shape:

    web ACL block (WAF_FILTERED)  {"message":"Request blocked by ...","blockedBy":"waf","requestId":"..."}
    web ACL block, or no stage    {"message":"Forbidden"}                    (byte-identical)
    authorizer deny               {"Message":"User is not authorized to access this resource ..."}
    resource-policy deny          {"Message":"User: anonymous is not authorized to perform: execute-api:Invoke ..."}
                                  (both under "message" where the API's DEFAULT_4XX response template applies)
    no deployed route             {"message":"Missing Authentication Token"}, or an Authorization-header complaint
    handler denial                {"message":"Not Authorized"}
    CloudFront web ACL block      an HTML page saying "Request blocked"

Only the authorizer's deny can be cured by a fresh token. Resending a request the web ACL blocked adds
only blocked samples, and reporting it as a sign-in problem sends the reader to sign in again, so the
response is read before anything is refreshed or resent.
"""

import ast
import inspect
import json
import sys
from unittest.mock import MagicMock, Mock, patch

import click
import pytest
import requests

import vamscli.main as main_module
from vamscli.constants import MAX_AUTH_RETRIES, WAF_ADMIN_HINT, WAF_OR_STAGE_ADMIN_HINT
from vamscli.utils.api_client import (
    FORBIDDEN_AUTHORIZER,
    FORBIDDEN_HANDLER,
    FORBIDDEN_NO_ROUTE,
    FORBIDDEN_RESOURCE_POLICY,
    FORBIDDEN_UNKNOWN,
    FORBIDDEN_WAF,
    FORBIDDEN_WAF_OR_STAGE,
    APIClient,
    _AUTHORIZER_DENIED_MESSAGE,
    _RESOURCE_POLICY_MESSAGE,
    _WAF_BLOCKED_MESSAGE,
    _WAF_OR_STAGE_MESSAGE,
    _api_error_message,
    _classify_403,
    _forbidden_request_id,
)
from vamscli.utils import global_exceptions
from vamscli.utils.exceptions import (
    APIError,
    APIUnavailableError,
    AuthenticationError,
    ConfigurationError,
    GlobalInfrastructureError,
    InvalidProfileNameError,
    OverrideTokenError,
    PermissionDeniedError,
    ProfileError,
    RateLimitExceededError,
    RequestBlockedError,
    RetryExhaustedError,
    SetupRequiredError,
    TokenExpiredError,
    VersionMismatchError,
)
from vamscli.utils.global_exceptions import handle_global_exceptions

BASE = "https://abc123.execute-api.us-west-2.amazonaws.com/api"

MARKER = {
    "message": "Request blocked by the deployment's AWS WAF web ACL",
    "blockedBy": "waf",
    "requestId": "r-1",
}
BARE = {"message": "Forbidden"}
AUTHORIZER_DENY = {"Message": "User is not authorized to access this resource with an explicit deny"}
RESOURCE_POLICY_DENY = {
    "Message": "User: anonymous is not authorized to perform: execute-api:Invoke on resource: x"}
# The same two denials through the API's DEFAULT_4XX response template, which names the key "message".
AUTHORIZER_DENY_TEMPLATED = {"message": AUTHORIZER_DENY["Message"]}
RESOURCE_POLICY_DENY_TEMPLATED = {"message": RESOURCE_POLICY_DENY["Message"]}
HANDLER_DENY = {"message": "Not Authorized"}
MISSING_TOKEN = {"message": "Missing Authentication Token"}
AUTHORIZATION_HEADER_COMPLAINT = {
    "message": "Invalid key=value pair (missing equal-sign) in Authorization header (hashed with "
               "SHA-256 and encoded with Base64): 'Bearer secret-value'."}
CLOUDFRONT_BLOCK_PAGE = (
    "<html><head><title>ERROR: The request could not be satisfied</title></head><body>"
    "<h1>403 ERROR</h1><h2>The request could not be satisfied.</h2>Request blocked. We can't connect "
    "to the server for this app or website at this time.</body></html>")

ACCESS_DENIED = {"x-amzn-ErrorType": "AccessDeniedException"}
INCOMPLETE_SIGNATURE = {"x-amzn-ErrorType": "IncompleteSignatureException"}
MISSING_TOKEN_TYPE = {"x-amzn-ErrorType": "MissingAuthenticationTokenException"}

# The headings and wording a connector classes as a sign-in or permission problem
# (tools/ExternalIntegrations: IsaacSim's access-denied markers, ArcGIS's re-prompt triggers).
SIGN_IN_WORDING = (
    "Authentication failed", "authentication failed", "Access forbidden.", "token has expired",
    "401", "auth login",
    "Authentication Error:", "Permission Denied:", "Token Expired:", "Override Token Error:",
)

# Every error the global handler names, with the heading its --json-output `error` starts with.
GLOBAL_ERRORS = [
    pytest.param(SetupRequiredError, "Setup Required", id="SetupRequiredError"),
    pytest.param(RequestBlockedError, "Request Blocked", id="RequestBlockedError"),
    pytest.param(AuthenticationError, "Authentication Error", id="AuthenticationError"),
    pytest.param(APIError, "API Error", id="APIError"),
    pytest.param(APIUnavailableError, "API Unavailable", id="APIUnavailableError"),
    pytest.param(ProfileError, "Profile Error", id="ProfileError"),
    pytest.param(InvalidProfileNameError, "Invalid Profile Name", id="InvalidProfileNameError"),
    pytest.param(ConfigurationError, "Configuration Error", id="ConfigurationError"),
    pytest.param(OverrideTokenError, "Override Token Error", id="OverrideTokenError"),
    pytest.param(VersionMismatchError, "Version Mismatch", id="VersionMismatchError"),
    pytest.param(RetryExhaustedError, "Rate Limit Exceeded", id="RetryExhaustedError"),
    pytest.param(RateLimitExceededError, "Rate Limit Exceeded", id="RateLimitExceededError"),
    pytest.param(TokenExpiredError, "Token Expired", id="TokenExpiredError"),
    pytest.param(PermissionDeniedError, "Permission Denied", id="PermissionDeniedError"),
]


def _response(body=None, status=403, headers=None, content_type="application/json"):
    """A requests.Response as the session returns it: a dict or list is sent as JSON, a str as is."""
    response = requests.Response()
    response.status_code = status
    response.reason = "Forbidden" if status == 403 else "OK"
    response.url = BASE + "/database"
    if body is None:
        response._content = b""
    elif isinstance(body, str):
        response._content = body.encode("utf-8")
    else:
        response._content = json.dumps(body).encode("utf-8")
    response.headers["Content-Type"] = content_type
    for name, value in (headers or {}).items():
        response.headers[name] = value
    return response


def _client(override_token=False, token_expired=False):
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = override_token
    profile_manager.is_token_expired.return_value = token_expired
    profile_manager.load_auth_profile.return_value = {"access_token": "token-before"}
    return APIClient(BASE, profile_manager=profile_manager)


class _Wire:
    """The client's session and token refresh, replaced and recorded. The last response repeats."""

    def __init__(self, monkeypatch, client, *responses, refreshes_succeed=True):
        self.sends = []
        self.refreshes = 0
        self._responses = list(responses)

        def request(method, url, **kwargs):
            self.sends.append((method, url))
            return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

        def try_refresh_token(token_before=None):
            self.refreshes += 1
            return refreshes_succeed

        monkeypatch.setattr(client.session, "request", request)
        monkeypatch.setattr(client, "_try_refresh_token", try_refresh_token)


def _no_route_message(method, path):
    """The whole no-route text, as _make_request raises it: nothing may wrap it on the way out."""
    return (f"No deployed API route matches {method} {path} (HTTP 403). The deployment may be older than "
            "this CLI, or the add-on that serves the route is not deployed.")


# --- 1. The classifier --------------------------------------------------------------------------


CLASSIFIED = [
    pytest.param(BARE, None, FORBIDDEN_WAF_OR_STAGE, id="bare-forbidden"),
    pytest.param(MARKER, None, FORBIDDEN_WAF, id="waf-marker"),
    pytest.param(AUTHORIZER_DENY, None, FORBIDDEN_AUTHORIZER, id="authorizer-explicit-deny"),
    pytest.param({"Message": "something"}, ACCESS_DENIED, FORBIDDEN_AUTHORIZER,
                 id="authorizer-error-type"),
    pytest.param(RESOURCE_POLICY_DENY, None, FORBIDDEN_RESOURCE_POLICY, id="resource-policy"),
    pytest.param(RESOURCE_POLICY_DENY, ACCESS_DENIED, FORBIDDEN_RESOURCE_POLICY,
                 id="resource-policy-with-access-denied-type"),
    pytest.param(AUTHORIZER_DENY_TEMPLATED, None, FORBIDDEN_AUTHORIZER, id="authorizer-templated"),
    pytest.param(AUTHORIZER_DENY_TEMPLATED, ACCESS_DENIED, FORBIDDEN_AUTHORIZER,
                 id="authorizer-templated-with-access-denied-type"),
    pytest.param(RESOURCE_POLICY_DENY_TEMPLATED, None, FORBIDDEN_RESOURCE_POLICY, id="resource-policy-templated"),
    pytest.param(RESOURCE_POLICY_DENY_TEMPLATED, ACCESS_DENIED, FORBIDDEN_RESOURCE_POLICY,
                 id="resource-policy-templated-with-access-denied-type"),
    pytest.param(MISSING_TOKEN, None, FORBIDDEN_NO_ROUTE, id="missing-authentication-token"),
    pytest.param({"message": "something"}, INCOMPLETE_SIGNATURE, FORBIDDEN_NO_ROUTE,
                 id="incomplete-signature-type"),
    pytest.param(None, INCOMPLETE_SIGNATURE, FORBIDDEN_NO_ROUTE, id="incomplete-signature-type-no-body"),
    pytest.param(None, MISSING_TOKEN_TYPE, FORBIDDEN_NO_ROUTE, id="missing-token-type-no-body"),
    pytest.param(AUTHORIZATION_HEADER_COMPLAINT, None, FORBIDDEN_NO_ROUTE, id="authorization-header"),
    pytest.param(HANDLER_DENY, None, FORBIDDEN_HANDLER, id="handler-not-authorized"),
    pytest.param({"message": "Forbidden: role lacks"}, None, FORBIDDEN_HANDLER, id="handler-forbidden-prefix"),
    pytest.param({"message": "Forbidden", "extra": 1}, None, FORBIDDEN_HANDLER, id="handler-forbidden-extra"),
    pytest.param(CLOUDFRONT_BLOCK_PAGE, None, FORBIDDEN_WAF, id="cloudfront-block-page"),
    pytest.param("<html><body><h1>403 Forbidden</h1></body></html>", None, FORBIDDEN_UNKNOWN,
                 id="generic-html"),
    pytest.param(None, None, FORBIDDEN_UNKNOWN, id="empty-body"),
    pytest.param([], None, FORBIDDEN_UNKNOWN, id="json-list"),
    pytest.param(["Request blocked"], None, FORBIDDEN_UNKNOWN, id="json-list-saying-request-blocked"),
    pytest.param(json.dumps("Request blocked"), None, FORBIDDEN_UNKNOWN, id="json-string-saying-request-blocked"),
    pytest.param([], ACCESS_DENIED, FORBIDDEN_AUTHORIZER, id="json-list-authorizer-type"),
    pytest.param({}, None, FORBIDDEN_UNKNOWN, id="json-empty-object"),
    pytest.param({"message": "   "}, None, FORBIDDEN_UNKNOWN, id="json-blank-message"),
    pytest.param("upstream connect error", None, FORBIDDEN_UNKNOWN, id="plain-text"),
]


class TestClassify403:
    @pytest.mark.parametrize("body,headers,kind", CLASSIFIED)
    def test_each_shape_is_attributed_to_the_party_that_sends_it(self, body, headers, kind):
        assert _classify_403(_response(body, headers=headers)) == kind

    def test_the_web_acl_marker_wins_over_every_other_signal(self):
        body = dict(MARKER, Message=AUTHORIZER_DENY["Message"])
        assert _classify_403(_response(body, headers=ACCESS_DENIED)) == FORBIDDEN_WAF

    def test_a_response_that_raises_on_every_read_is_unknown_not_an_exception(self):
        class Hostile:
            status_code = 403
            content = b'{"message": "Not Authorized"}'

            @property
            def headers(self):
                raise RuntimeError("no headers")

            def json(self):
                raise ValueError("not JSON")

            @property
            def text(self):
                raise RuntimeError("no text")

        assert _classify_403(Hostile()) == FORBIDDEN_UNKNOWN

    @pytest.mark.parametrize("json_raises", [False, True], ids=["json-returns-a-stub", "json-raises"])
    def test_a_stub_response_is_unknown_not_an_exception(self, json_raises):
        """Mock attributes are neither dicts nor strings; the classifier runs on stubs elsewhere."""
        stub = Mock()
        stub.status_code = 403
        if json_raises:
            stub.json.side_effect = ValueError("not JSON")
        assert _classify_403(stub) == FORBIDDEN_UNKNOWN


class TestForbiddenRequestId:
    """The body's ID, else the first header carrying one: API Gateway's extended request ID (the
    regional web ACL log's), CloudFront's request ID (the CloudFront web ACL log's), then API Gateway's
    request ID (the access log's)."""

    ALL_HEADERS = {"x-amz-apigw-id": "r-2", "x-amz-cf-id": "r-3", "x-amzn-RequestId": "r-4"}

    def test_the_marker_body_names_the_request(self):
        assert _forbidden_request_id(_response(MARKER, headers=self.ALL_HEADERS)) == "r-1"

    def test_the_extended_request_id_names_it_ahead_of_the_other_headers(self):
        assert _forbidden_request_id(_response(BARE, headers=self.ALL_HEADERS)) == "r-2"

    def test_the_cloudfront_request_id_names_it_without_an_extended_request_id(self):
        headers = {"x-amz-cf-id": "r-3", "x-amzn-RequestId": "r-4"}
        assert _forbidden_request_id(_response(BARE, headers=headers)) == "r-3"

    def test_the_cloudfront_block_page_is_named_by_its_cloudfront_request_id(self):
        page = _response("<html><body>Request blocked.</body></html>", content_type="text/html",
                         headers={"x-amz-cf-id": "r-3"})
        assert _forbidden_request_id(page) == "r-3"

    def test_the_api_gateway_request_id_names_it_when_it_is_the_only_header(self):
        assert _forbidden_request_id(_response(BARE, headers={"x-amzn-RequestId": "r-4"})) == "r-4"

    def test_a_blank_header_is_skipped(self):
        headers = {"x-amz-apigw-id": "  ", "x-amz-cf-id": "", "x-amzn-RequestId": " r-4 "}
        assert _forbidden_request_id(_response(BARE, headers=headers)) == "r-4"

    def test_none_when_neither_names_it(self):
        assert _forbidden_request_id(_response(BARE)) is None
        assert _forbidden_request_id(Mock()) is None


# --- 2. and 3. A web ACL block is sent once and reported as a block ---------------------------------


class TestAWebAclBlockIsSentOnce:
    @pytest.mark.parametrize("raise_http_errors", [False, True])
    def test_the_marker_raises_request_blocked_without_a_refresh(self, monkeypatch, raise_http_errors):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(MARKER))

        with pytest.raises(RequestBlockedError) as excinfo:
            client._make_request("GET", "/database", include_auth=True, raise_http_errors=raise_http_errors)

        assert len(wire.sends) == 1
        assert wire.refreshes == 0
        assert str(excinfo.value) == _WAF_BLOCKED_MESSAGE
        assert excinfo.value.request_id == "r-1"
        assert excinfo.value.json_fields == {"blockedBy": "waf", "requestId": "r-1"}

    @pytest.mark.parametrize("raise_http_errors", [False, True])
    def test_the_bare_forbidden_raises_request_blocked_without_a_refresh(self, monkeypatch, raise_http_errors):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(BARE, headers={"x-amz-apigw-id": "r-2"}))

        with pytest.raises(RequestBlockedError) as excinfo:
            client.get("/database", include_auth=True, raise_http_errors=raise_http_errors)

        assert len(wire.sends) == 1
        assert wire.refreshes == 0
        assert str(excinfo.value) == _WAF_OR_STAGE_MESSAGE
        assert excinfo.value.json_fields == {"blockedBy": "waf-or-stage", "requestId": "r-2"}

    def test_request_blocked_is_a_global_error(self):
        assert issubclass(RequestBlockedError, GlobalInfrastructureError)
        assert RequestBlockedError("m").json_fields == {"blockedBy": "waf"}
        assert RequestBlockedError("m").request_id is None


class TestWithoutAuth:
    @pytest.mark.parametrize("raise_http_errors", [False, True])
    def test_the_marker_is_a_block_on_an_unauthenticated_call_too(self, monkeypatch, raise_http_errors):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(MARKER))

        with pytest.raises(RequestBlockedError):
            client.get("/version", include_auth=False, raise_http_errors=raise_http_errors)
        assert len(wire.sends) == 1

    def test_the_bare_forbidden_keeps_the_api_error_naming_both_causes(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(BARE))

        with pytest.raises(APIError) as excinfo:
            client.get("/version", include_auth=False)

        assert "AWS WAF web ACL" in str(excinfo.value)
        assert "stage" in str(excinfo.value)
        assert len(wire.sends) == 1

    def test_with_raise_http_errors_the_caller_gets_the_response_naming_both_causes(self, monkeypatch):
        """The caller maps the original error itself; its body still reads as both causes."""
        client = _client()
        wire = _Wire(monkeypatch, client, _response(BARE))

        with pytest.raises(requests.exceptions.HTTPError) as excinfo:
            client.get("/version", include_auth=False, raise_http_errors=True)

        message = _api_error_message(excinfo.value.response, str(excinfo.value))
        assert "AWS WAF web ACL" in message
        assert "stage" in message
        assert len(wire.sends) == 1


# --- 4. and 5. Only the authorizer's deny is refreshed ------------------------------------------------


class TestAuthorizerDeny:
    @pytest.mark.parametrize("body", [AUTHORIZER_DENY, None], ids=["authorizer", "unknown"])
    def test_a_deny_then_success_returns_the_response_after_one_refresh(self, monkeypatch, body):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(body), _response({"ok": True}, status=200))

        response = client.get("/database", include_auth=True)

        assert response.status_code == 200
        assert len(wire.sends) == 2
        assert wire.refreshes == 1

    @pytest.mark.parametrize("raise_http_errors", [False, True])
    def test_a_persistent_deny_names_the_authorizer_after_every_retry(self, monkeypatch, raise_http_errors):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(AUTHORIZER_DENY))

        with pytest.raises(PermissionDeniedError) as excinfo:
            client.get("/database", include_auth=True, raise_http_errors=raise_http_errors)

        assert "authorizer refused the request even with a refreshed token" in str(excinfo.value)
        assert str(excinfo.value) == _AUTHORIZER_DENIED_MESSAGE
        assert len(wire.sends) == MAX_AUTH_RETRIES + 1 == 4
        assert wire.refreshes == MAX_AUTH_RETRIES == 3

    def test_a_failed_refresh_of_an_expired_token_is_token_expired(self, monkeypatch):
        client = _client(token_expired=True)
        wire = _Wire(monkeypatch, client, _response(AUTHORIZER_DENY), refreshes_succeed=False)

        with pytest.raises(TokenExpiredError):
            client.get("/database", include_auth=True)
        assert (len(wire.sends), wire.refreshes) == (1, 1)

    def test_a_failed_refresh_of_a_live_token_is_permission_denied(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(AUTHORIZER_DENY), refreshes_succeed=False)

        with pytest.raises(PermissionDeniedError) as excinfo:
            client.get("/database", include_auth=True)
        assert "You do not have permission to perform this action" in str(excinfo.value)
        assert (len(wire.sends), wire.refreshes) == (1, 1)

    def test_an_override_token_is_never_refreshed(self, monkeypatch):
        client = _client(override_token=True)
        wire = _Wire(monkeypatch, client, _response(AUTHORIZER_DENY))

        with pytest.raises(PermissionDeniedError) as excinfo:
            client.get("/database", include_auth=True)
        assert "You do not have permission to perform this action" in str(excinfo.value)
        assert (len(wire.sends), wire.refreshes) == (1, 0)

    def test_an_override_token_that_expired_in_flight_is_token_expired(self, monkeypatch):
        client = _client(override_token=True)
        # Unexpired at the pre-flight check, expired by the time the 403 is read.
        client.profile_manager.is_token_expired.side_effect = [False, True]
        wire = _Wire(monkeypatch, client, _response(AUTHORIZER_DENY))

        with pytest.raises(TokenExpiredError):
            client.get("/database", include_auth=True)
        assert (len(wire.sends), wire.refreshes) == (1, 0)


# --- 6. to 8. The other parties answer once ------------------------------------------------------------


class TestAnsweredOnce:
    @pytest.mark.parametrize("raise_http_errors", [False, True])
    def test_a_handler_denial_is_permission_denied_with_the_handler_text(self, monkeypatch, raise_http_errors):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(HANDLER_DENY))

        with pytest.raises(PermissionDeniedError) as excinfo:
            client.get("/database", include_auth=True, raise_http_errors=raise_http_errors)

        assert str(excinfo.value) == "Access forbidden. Not Authorized"
        assert (len(wire.sends), wire.refreshes) == (1, 0)

    @pytest.mark.parametrize("body", [RESOURCE_POLICY_DENY, RESOURCE_POLICY_DENY_TEMPLATED],
                             ids=["gateway-default", "templated"])
    @pytest.mark.parametrize("raise_http_errors", [False, True])
    def test_a_resource_policy_deny_names_the_resource_policy(self, monkeypatch, raise_http_errors, body):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(body, headers=ACCESS_DENIED))

        with pytest.raises(PermissionDeniedError) as excinfo:
            client.get("/database", include_auth=True, raise_http_errors=raise_http_errors)

        assert str(excinfo.value).startswith("Access forbidden. The API's resource policy")
        assert str(excinfo.value) == _RESOURCE_POLICY_MESSAGE
        assert (len(wire.sends), wire.refreshes) == (1, 0)

    @pytest.mark.parametrize("include_auth", [True, False])
    @pytest.mark.parametrize("raise_http_errors", [False, True])
    def test_no_route_names_the_method_and_path_and_never_the_body(self, monkeypatch, include_auth,
                                                                   raise_http_errors):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(AUTHORIZATION_HEADER_COMPLAINT))

        with pytest.raises(APIError) as excinfo:
            client.get("/database/x/assets?startingToken=query-value", include_auth=include_auth,
                       raise_http_errors=raise_http_errors)

        text = str(excinfo.value)
        assert text == _no_route_message("GET", "/api/database/x/assets")
        assert "secret-value" not in text
        assert "Authorization header" not in text
        assert "query-value" not in text
        assert (len(wire.sends), wire.refreshes) == (1, 0)

    def test_missing_authentication_token_is_no_route(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(MISSING_TOKEN))

        with pytest.raises(APIError) as excinfo:
            client.post("/database", data={"databaseId": "n"}, include_auth=True)

        assert str(excinfo.value) == _no_route_message("POST", "/api/database")
        assert (len(wire.sends), wire.refreshes) == (1, 0)


class TestARedirectTargetAnswered:
    """A 403 from the host a followed redirect reached (a presigned Amazon S3 URL) is not the API's."""

    @staticmethod
    def _redirected(body, url):
        redirect = _response(None, status=303)
        response = _response(body, content_type="application/xml")
        response.url = url
        response.history = [redirect]
        return response

    @pytest.mark.parametrize("raise_http_errors", [False, True])
    def test_it_is_reported_once_naming_the_host_without_a_refresh(self, monkeypatch, raise_http_errors):
        client = _client()
        s3_denied = "<?xml version=\"1.0\"?><Error><Code>AccessDenied</Code></Error>"
        url = "https://bucket.s3.us-west-2.amazonaws.com/export.json?X-Amz-Signature=secret-value"
        wire = _Wire(monkeypatch, client, self._redirected(s3_denied, url))

        with pytest.raises(APIError) as excinfo:
            client.post("/database/d/assets/a/export", data={}, include_auth=True,
                        raise_http_errors=raise_http_errors)

        text = str(excinfo.value)
        assert "bucket.s3.us-west-2.amazonaws.com" in text
        assert "HTTP 403" in text
        assert "secret-value" not in text
        assert "X-Amz" not in text
        assert not isinstance(excinfo.value, (PermissionDeniedError, RequestBlockedError))
        assert (len(wire.sends), wire.refreshes) == (1, 0)

    def test_a_redirect_that_stays_on_the_api_host_is_still_classified(self, monkeypatch):
        """Control: the redirect alone does not decide; the answering host does."""
        client = _client()
        wire = _Wire(monkeypatch, client, self._redirected(MARKER, BASE + "/database"))

        with pytest.raises(RequestBlockedError):
            client.get("/database", include_auth=True)

        assert (len(wire.sends), wire.refreshes) == (1, 0)


# --- 10. and 11. The global handler and its wording ------------------------------------------------


def _handled(monkeypatch, error, json_mode):
    """Run `error` through the global handler; return the ClickException message it ends with."""
    argv = ["vamscli", "database", "list"] + (["--json-output"] if json_mode else [])
    monkeypatch.setattr(sys, "argv", argv)

    @handle_global_exceptions()
    def command():
        raise error

    with pytest.raises(click.ClickException) as excinfo:
        command()
    return excinfo.value.message


class TestGlobalHandler:
    def test_cli_mode_reports_a_block_and_what_the_administrator_does(self, monkeypatch, capsys):
        _handled(monkeypatch, RequestBlockedError(_WAF_BLOCKED_MESSAGE, request_id="r-1"), json_mode=False)

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == f"✗ Request Blocked: {_WAF_BLOCKED_MESSAGE}"
        assert lines[1].startswith("Ask the VAMS administrator")
        assert lines[1].endswith("Request ID: r-1.")
        assert lines[1] == f"{WAF_ADMIN_HINT} Request ID: r-1."

    def test_cli_mode_without_a_request_id_names_none(self, monkeypatch, capsys):
        _handled(monkeypatch, RequestBlockedError(_WAF_BLOCKED_MESSAGE), json_mode=False)

        assert capsys.readouterr().out.splitlines()[1] == WAF_ADMIN_HINT

    def test_json_mode_carries_the_type_the_block_fields_and_the_hint(self, monkeypatch):
        document = json.loads(
            _handled(monkeypatch, RequestBlockedError(_WAF_BLOCKED_MESSAGE, request_id="r-1"), json_mode=True))

        assert document["error"] == f"Request Blocked: {_WAF_BLOCKED_MESSAGE}"
        assert document["error_type"] == "RequestBlockedError"
        assert document["blockedBy"] == "waf"
        assert document["requestId"] == "r-1"
        assert document["additional_info"] == [f"{WAF_ADMIN_HINT} Request ID: r-1."]

    def test_json_mode_of_an_unconfirmed_block(self, monkeypatch):
        document = json.loads(_handled(
            monkeypatch, RequestBlockedError(_WAF_OR_STAGE_MESSAGE, confirmed=False), json_mode=True))

        assert document["blockedBy"] == "waf-or-stage"
        assert "requestId" not in document
        assert document["additional_info"] == [WAF_OR_STAGE_ADMIN_HINT]

    def test_cli_mode_of_an_unconfirmed_block_names_both_causes(self, monkeypatch, capsys):
        """A deployment without AWS WAF answers a URL that names no deployed stage the same way."""
        _handled(monkeypatch, RequestBlockedError(_WAF_OR_STAGE_MESSAGE, request_id="r-2", confirmed=False),
                 json_mode=False)

        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == f"✗ Request Blocked: {_WAF_OR_STAGE_MESSAGE}"
        assert lines[1] == f"{WAF_OR_STAGE_ADMIN_HINT} Request ID: r-2."
        assert "vamscli profile info" in WAF_OR_STAGE_ADMIN_HINT
        assert "If the deployment has an AWS WAF web ACL" in WAF_OR_STAGE_ADMIN_HINT

    @pytest.mark.parametrize("error_class,heading", GLOBAL_ERRORS)
    def test_every_global_error_gains_its_type_and_keeps_its_heading(self, monkeypatch, error_class, heading):
        document = json.loads(_handled(monkeypatch, error_class("the message"), json_mode=True))

        assert document["error"] == f"{heading}: the message"
        assert document["error_type"] == error_class.__name__

    def test_the_cases_name_every_error_the_handler_names(self):
        """The handler's exception import lists the errors it has a branch for."""
        tree = ast.parse(inspect.getsource(global_exceptions))
        named = {alias.name for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module == "exceptions" and node.level == 1
                 for alias in node.names}

        assert named == {param.values[0].__name__ for param in GLOBAL_ERRORS}

    def test_an_authentication_error_keeps_its_hint(self, monkeypatch):
        document = json.loads(_handled(monkeypatch, AuthenticationError("bad token"), json_mode=True))

        assert document["additional_info"] == ["Run 'vamscli auth login' to re-authenticate."]

    def test_json_fields_never_replace_the_handlers_own_keys(self, monkeypatch):
        error = PermissionDeniedError("Access forbidden. Not Authorized")
        error.json_fields = {"error": "replaced", "error_type": "Replaced", "extra": 1}

        document = json.loads(_handled(monkeypatch, error, json_mode=True))

        assert document["error"] == "Permission Denied: Access forbidden. Not Authorized"
        assert document["error_type"] == "PermissionDeniedError"
        assert document["extra"] == 1

    def test_json_fields_that_are_not_a_mapping_are_left_out(self, monkeypatch):
        """The handler must still report the error; a json_fields that is not a mapping is not read."""
        error = PermissionDeniedError("Access forbidden. Not Authorized")
        error.json_fields = ["blockedBy"]

        document = json.loads(_handled(monkeypatch, error, json_mode=True))

        assert set(document) == {"error", "error_type", "additional_info"}
        assert document["error_type"] == "PermissionDeniedError"


class TestBlockWording:
    """A connector reads these words as a sign-in or permission problem, which a block is not."""

    @pytest.mark.parametrize("text", [_WAF_BLOCKED_MESSAGE, _WAF_OR_STAGE_MESSAGE, WAF_ADMIN_HINT,
                                      WAF_OR_STAGE_ADMIN_HINT],
                             ids=["blocked", "waf-or-stage", "admin-hint", "waf-or-stage-admin-hint"])
    def test_the_block_texts_carry_no_sign_in_wording(self, text):
        for fragment in SIGN_IN_WORDING:
            assert fragment not in text, fragment

    @pytest.mark.parametrize("json_mode", [False, True])
    def test_the_rendered_block_carries_no_sign_in_wording(self, monkeypatch, capsys, json_mode):
        for message, confirmed in ((_WAF_BLOCKED_MESSAGE, True), (_WAF_OR_STAGE_MESSAGE, False)):
            _handled(monkeypatch, RequestBlockedError(message, request_id="r-1", confirmed=confirmed),
                     json_mode=json_mode)
            rendered = capsys.readouterr().out
            for fragment in SIGN_IN_WORDING:
                assert fragment not in rendered, fragment

    @pytest.mark.parametrize("text", [_RESOURCE_POLICY_MESSAGE, _AUTHORIZER_DENIED_MESSAGE])
    def test_a_denial_reads_as_one(self, text):
        assert text.startswith("Access forbidden.")


# --- 12. to 14. The unauthenticated and sign-in paths --------------------------------------------------


class TestSignInAndSetupPaths:
    def test_a_block_during_the_login_profile_call_clears_no_credentials(self, monkeypatch):
        client = _client()
        _Wire(monkeypatch, client, _response(MARKER))

        with pytest.raises(RequestBlockedError):
            client.call_login_profile("user@example.com")
        client.profile_manager.delete_auth_profile.assert_not_called()

    def test_the_availability_check_reports_the_marker_as_a_block(self, monkeypatch):
        client = _client()
        wire = _Wire(monkeypatch, client, _response(MARKER))

        with pytest.raises(RequestBlockedError) as excinfo:
            client.check_api_availability()
        assert excinfo.value.request_id == "r-1"
        assert len(wire.sends) == 1

    def test_the_availability_check_names_both_causes_of_a_bare_forbidden(self, monkeypatch):
        client = _client()
        _Wire(monkeypatch, client, _response(BARE))

        with pytest.raises(APIUnavailableError) as excinfo:
            client.check_api_availability()
        assert str(excinfo.value) == (
            "VAMS API answered the version check with a bare 403 \"Forbidden\": the deployment's AWS WAF "
            "web ACL blocked the request, or the stored API Gateway URL names no deployed stage (see "
            "'vamscli profile info')."
        )
        assert "AWS WAF web ACL" in str(excinfo.value)
        assert "names no deployed stage" in str(excinfo.value)

    def test_the_availability_check_keeps_its_404_message(self, monkeypatch):
        client = _client()
        _Wire(monkeypatch, client, _response({"message": "Not Found"}, status=404))

        with pytest.raises(APIUnavailableError) as excinfo:
            client.check_api_availability()
        assert "VAMS API version endpoint not found" in str(excinfo.value)

    @pytest.mark.parametrize("method", ["check_version", "get_amplify_config"])
    def test_the_setup_calls_let_a_block_through_unwrapped(self, monkeypatch, method):
        client = _client()
        _Wire(monkeypatch, client, _response(MARKER))

        with pytest.raises(RequestBlockedError):
            getattr(client, method)()

    @pytest.mark.parametrize("body,error,error_type", [
        (MARKER, f"Request Blocked: {_WAF_BLOCKED_MESSAGE}", "RequestBlockedError"),
        (BARE, "API Unavailable: VAMS API answered the version check with a bare 403", "APIUnavailableError"),
    ], ids=["web-acl-marker", "bare-forbidden"])
    def test_auth_login_stops_when_its_availability_check_is_blocked(self, monkeypatch, generic_command_mocks,
                                                                    body, error, error_type):
        """`auth login` runs the legacy availability check first; a block there ends the command.

        Signing in past it would report success with no feature switches saved, and every gated command
        would then send the reader to sign in again.
        """
        monkeypatch.setattr(sys, "argv", ["vamscli", "auth", "login", "-u", "user@example.com", "-p", "pw",
                                          "--json-output"])
        authenticator = Mock()
        with generic_command_mocks("auth") as mocks, \
                patch("vamscli.commands.auth.get_authenticator", return_value=authenticator), \
                patch("requests.Session.request", return_value=_response(body)) as send:
            mocks["patches"]["dec_api"].side_effect = APIClient
            with pytest.raises(click.ClickException) as excinfo:
                main_module.main()

        document = json.loads(excinfo.value.message)
        assert excinfo.value.exit_code == 1
        assert document["error"].startswith(error)
        assert document["error_type"] == error_type
        assert send.call_count == 1
        authenticator.authenticate.assert_not_called()
