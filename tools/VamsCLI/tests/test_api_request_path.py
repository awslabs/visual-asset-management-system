"""`_make_request` refuses a request path with an empty, '.' or '..' segment before anything is sent.

`_make_request` joins the endpoint onto the base URL. The join resolves a '.' or '..' segment and drops
an empty one, and requests decodes a percent-encoded dot before sending, so a path value of any method
carrying such a segment would address another route (an API key's among them). Such a path raises
`InvalidRequestPathError` before any request, whichever method built it.
"""

import re
from unittest.mock import Mock

import pytest

from vamscli import constants
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import APIError, GlobalInfrastructureError, InvalidRequestPathError

# Every route constant constants.py exposes (the re-exported add-on paths included), with each
# placeholder filled by a well-formed value.
CONSTANT_PATHS = sorted(
    (name, re.sub(r"\{[^}]*\}", lambda match: "v-1:c-1" if ":" in match.group(0) else "x-1", value))
    for name, value in vars(constants).items()
    if name.startswith("API_") and isinstance(value, str) and value.startswith("/")
)

# A path that addresses an API key's route once a dot segment in front of it is resolved.
API_KEY = "auth/api-keys/k-1"


def _client() -> APIClient:
    # A real ProfileManager reads the developer's config directory; a stub keeps every assertion on
    # the request that was built and the exception that was raised.
    return APIClient("https://api.example.com", profile_manager=Mock())


class _SessionAnswer:
    """An empty 200 from the session."""

    status_code = 200
    content = b"{}"
    text = "{}"
    headers = {}

    def json(self):
        return {}

    def raise_for_status(self):
        return None


def _record_requests(monkeypatch, client):
    """Record every request the client's session is asked to send, answering an empty 200. The path
    guard sits in `_make_request`, so this recorder replaces the session below it, not the verbs."""
    sent = []
    monkeypatch.setattr(client, "_validate_token_before_request", lambda include_auth: None)
    monkeypatch.setattr(client, "_get_headers", lambda include_auth: {})
    monkeypatch.setattr(
        client.session, "request", lambda method, url, **kwargs: sent.append((method, url)) or _SessionAnswer())
    return sent


class TestRequestPathHasNoDotOrEmptySegment:

    def test_the_constant_parse_is_not_vacuous(self):
        # Control: the parametrization below reads the route constants rather than an empty set.
        assert len(CONSTANT_PATHS) > 100
        assert ("API_AUTH_API_KEY", "/auth/api-keys/x-1") in CONSTANT_PATHS

    @pytest.mark.parametrize("name,path", CONSTANT_PATHS)
    def test_every_route_constant_is_sent_unchanged(self, monkeypatch, name, path):
        """Control: each route constant, filled with well-formed values, is sent as it is."""
        client = _client()
        sent = _record_requests(monkeypatch, client)
        client._make_request("GET", path)
        assert sent == [("GET", "https://api.example.com" + path)]

    @pytest.mark.parametrize(
        "segment", [".hidden", "a..b", "...", "jane.doe@example.com", "on_upload%23t-1", "a%2Fb"])
    def test_a_dot_or_an_encoded_slash_inside_a_segment_is_sent(self, monkeypatch, segment):
        """Control: only a whole '.' or '..' segment is refused, not a dot within one."""
        client = _client()
        sent = _record_requests(monkeypatch, client)
        client._make_request("GET", f"/auth/loginProfile/{segment}")
        assert sent == [("GET", f"https://api.example.com/auth/loginProfile/{segment}")]

    @pytest.mark.parametrize("verb", ["GET", "POST", "PUT", "DELETE"])
    @pytest.mark.parametrize(
        "endpoint",
        [
            f"/database/../{API_KEY}",
            f"/database/x/./../../{API_KEY}",
            f"/database/../{API_KEY}?x=/assets",
            f"/database/%2e%2e/{API_KEY}",
            f"/database/%2E%2E/{API_KEY}",
            f"/database/.%2e/{API_KEY}",
            f"/database/%252e%252e/{API_KEY}",
            "/database/..%2F" + API_KEY.replace("/", "%2F"),
            f"/{API_KEY}/x/..;",
            "/database/./x",
            "/database/%2e/x",
            "/database//assets",
            "/database/",
        ],
    )
    def test_a_path_with_a_dot_or_empty_segment_is_refused_unsent(self, monkeypatch, verb, endpoint):
        client = _client()
        sent = _record_requests(monkeypatch, client)
        with pytest.raises(InvalidRequestPathError):
            client._make_request(verb, endpoint)
        assert sent == []

    @pytest.mark.parametrize(
        "method,args,path",
        [
            ("get_database", ("db-1",), "/database/db-1"),
            ("get_asset", ("db-1", "a-1"), "/database/db-1/assets/a-1"),
        ],
    )
    def test_a_well_formed_value_is_sent_to_its_own_route(self, monkeypatch, method, args, path):
        """Control: the same recorder sees the method's request for a well-formed value."""
        client = _client()
        sent = _record_requests(monkeypatch, client)
        getattr(client, method)(*args)
        assert sent == [("GET", "https://api.example.com" + path)]

    @pytest.mark.parametrize(
        "method,args",
        [
            ("get_database", (f"../{API_KEY}",)),
            ("get_asset", (f"../{API_KEY}?x=", "a1")),
            ("get_database", ("%2E%2E/" + API_KEY,)),
        ],
    )
    def test_a_value_carrying_a_dot_segment_is_refused_unsent(self, monkeypatch, method, args):
        client = _client()
        sent = _record_requests(monkeypatch, client)
        with pytest.raises(APIError) as excinfo:
            getattr(client, method)(*args)
        assert sent == []
        # A method may wrap the refusal in its own APIError; the refusal stays the cause.
        error = excinfo.value
        while not isinstance(error, InvalidRequestPathError) and error.__context__ is not None:
            error = error.__context__
        assert isinstance(error, InvalidRequestPathError)

    def test_the_refusal_is_an_api_error_the_command_reports(self):
        # A business error: commands and the global handler report it as an API error, and it never
        # reads as an infrastructure failure.
        assert issubclass(InvalidRequestPathError, APIError)
        assert not issubclass(InvalidRequestPathError, GlobalInfrastructureError)
