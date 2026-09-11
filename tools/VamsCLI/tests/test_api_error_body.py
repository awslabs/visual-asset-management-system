"""The server's error text reaches the user, whatever key the body carries it under.

Every body shape here was MEASURED against a live VAMS deployment rather than assumed:

    GET /api/database   with a bogus bearer token  -> 403 {"Message": "User is not authorized ..."}
    GET /api/database   with no Authorization      -> 401 {"message": "Unauthorized"}
    GET /database       (stage omitted)            -> 403 {"message": "Forbidden"}
    GET /notastage/...  (wrong stage)              -> 403 {"message": "Forbidden"}

Two of those are why this module exists. Amazon API Gateway answers an authorizer denial with a
CAPITAL-M ``Message``, so reading only ``message`` discarded the explanation and showed the requests
library's generic ``403 Client Error: Forbidden for url: ...`` -- which reads as a malformed request
rather than a permissions problem. And a base URL whose deployment stage is missing or misspelled
produces a bare ``Forbidden`` that is indistinguishable from a real denial without a hint.
"""

import json

import pytest
import requests

from vamscli.utils.api_client import _api_error_message


FALLBACK = "403 Client Error: Forbidden for url: https://x.execute-api.us-west-2.amazonaws.com/api/database"


def _response(body, status=403, content_type="application/json"):
    """A requests.Response carrying `body`, built the way the error paths receive one."""
    response = requests.Response()
    response.status_code = status
    response.reason = "Forbidden" if status == 403 else "Unauthorized"
    response.url = "https://x.execute-api.us-west-2.amazonaws.com/api/database"
    if body is None:
        response._content = b""
    else:
        response._content = body.encode("utf-8") if isinstance(body, str) else body
    response.headers["Content-Type"] = content_type
    return response


class TestTheServerMessageIsSurfaced:
    def test_api_gateway_capital_message_is_read(self):
        """The measured 403 body from an authorizer denial. Reading only 'message' loses this."""
        body = json.dumps(
            {"Message": "User is not authorized to access this resource with an explicit "
                        "deny in an identity-based policy"}
        )

        assert _api_error_message(_response(body), FALLBACK) == (
            "User is not authorized to access this resource with an explicit deny in an "
            "identity-based policy"
        )

    def test_lowercase_message_is_read(self):
        """The shape VAMS's own handlers return, which must keep working."""
        body = json.dumps({"message": "Not Authorized"})

        assert _api_error_message(_response(body), FALLBACK) == "Not Authorized"

    @pytest.mark.parametrize("key", ["message", "Message", "errorMessage", "error"])
    def test_every_known_key_is_read(self, key):
        assert _api_error_message(_response(json.dumps({key: "specific detail"})), FALLBACK) == (
            "specific detail"
        )

    def test_the_lowercase_key_wins_when_both_are_present(self):
        """Preference order is asserted, not left to dict iteration order."""
        body = json.dumps({"Message": "gateway wording", "message": "handler wording"})

        assert _api_error_message(_response(body), FALLBACK) == "handler wording"


class TestTheBareForbiddenCarriesTheStageHint:
    def test_a_bare_forbidden_names_the_stage_as_the_likely_cause(self):
        """Measured: a base URL with the stage omitted or misspelled returns exactly this body.

        Without the hint it is indistinguishable from a real authorization denial, and the
        configuration mistake is invisible -- the reader has no reason to suspect their URL.
        """
        message = _api_error_message(_response(json.dumps({"message": "Forbidden"})), FALLBACK)

        assert message.startswith("Forbidden")
        assert "stage" in message
        assert "vamscli setup" in message

    def test_a_real_denial_does_not_get_the_stage_hint(self):
        """The paired arm. A hint on every 403 would be noise and would mislead."""
        body = json.dumps({"Message": "User is not authorized to access this resource"})

        assert "stage" not in _api_error_message(_response(body), FALLBACK)

    def test_a_message_merely_containing_forbidden_does_not_get_the_hint(self):
        """The match is on the exact gateway body, not a substring."""
        body = json.dumps({"message": "Forbidden: your role lacks assets:read on this database"})

        assert "stage" not in _api_error_message(_response(body), FALLBACK)


class TestItNeverRaisesAndFallsBackSensibly:
    """These run inside an `except` block, so a second exception would mask the first.

    138 of the 142 replaced call sites called `.json()` unguarded, which is exactly that failure:
    a non-JSON error body raised while handling the HTTP error, and the original status was lost.
    """

    def test_an_empty_body_falls_back(self):
        assert _api_error_message(_response(None), FALLBACK) == FALLBACK

    def test_a_non_json_body_raises_nothing_and_falls_back_to_its_text(self):
        assert _api_error_message(_response("upstream connect error"), FALLBACK) == (
            "upstream connect error"
        )

    def test_an_html_error_page_is_not_shown_as_a_message(self):
        """An HTML page is noise; the fallback is more useful than a wall of markup."""
        page = "<html><head><title>502</title></head><body>Bad Gateway</body></html>"

        assert _api_error_message(_response(page, content_type="text/html"), FALLBACK) == FALLBACK

    def test_a_very_long_non_json_body_is_not_shown(self):
        assert _api_error_message(_response("x" * 5000), FALLBACK) == FALLBACK

    def test_a_json_body_that_is_not_an_object_falls_back(self):
        assert _api_error_message(_response(json.dumps(["forbidden"])), FALLBACK) == FALLBACK

    def test_a_blank_message_value_falls_back_rather_than_showing_nothing(self):
        """An empty string would otherwise render as `API request failed (403): `."""
        assert _api_error_message(_response(json.dumps({"message": "   "})), FALLBACK) == FALLBACK

    def test_a_response_object_without_content_or_text_does_not_raise(self):
        """A stub or a streamed response may lack either attribute; neither may propagate."""

        class Bare:
            pass

        assert _api_error_message(Bare(), FALLBACK) == FALLBACK


def test_every_error_path_in_the_client_uses_the_helper():
    """A ratchet: no call site may go back to reading only the lowercase key.

    The replacement covered 142 sites in one pass. A partial reversion is the dangerous shape -- some
    paths surfacing the server's message and others not is harder to notice than none doing so.
    """
    import inspect

    from vamscli.utils import api_client

    source = inspect.getsource(api_client)

    assert "error_data.get('message', str(e))" not in source, (
        "an error path reads only the lowercase 'message' key again; Amazon API Gateway's own "
        "responses use 'Message' and that text would be discarded"
    )
    # Positive control: the helper is genuinely in use, so the assertion above cannot pass merely
    # because the error paths were deleted or renamed.
    assert source.count("_api_error_message(e.response, str(e))") > 100
