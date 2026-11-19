"""The `--verbose` echo masks credentials in request and response bodies and in tracebacks.

Every console line of `utils/logging.py` goes through `_echo` (`mask_console_text`), and the request and
response bodies it prints go through `_console_view`, the log file's key-based redaction. The command's
own output and `--json-output` are not the echo and keep every value, including a new API key.
"""

import io
import json
import sys
from unittest.mock import MagicMock

import pytest
import requests
import requests.adapters

from vamscli.main import cli
from vamscli.utils import logging as vlog
from vamscli.utils.api_client import APIClient
from vamscli.utils.logging import REDACTED

# None of these values is real.
API_KEY = "vams_EXAMPLEKEYVALUE0123456789abcdef"  # pragma: allowlist secret
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJleGFtcGxlLXVzZXIifQ.c2lnbmF0dXJlLXZhbHVl"
ACCESS_TOKEN = "at-example-access-token-value"
REFRESH_TOKEN = "rt-example-refresh-token-value"
CONNECTION_URL = "https://dcv.example.com:8443/?authToken=example-session-credential#ws-1"
DESKTOP_PASSWORD = "Example-Desktop-Pass-123"  # pragma: allowlist secret
PASSWORD = "Example-Sign-In-Pass-456"  # pragma: allowlist secret
SIGNATURE = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"  # pragma: allowlist secret
PRESIGNED = ("https://bucket.s3.us-east-1.amazonaws.com/a.glb?X-Amz-Algorithm=AWS4-HMAC-SHA256"
             f"&X-Amz-SignedHeaders=host&X-Amz-Signature={SIGNATURE}")

SECRETS = (API_KEY, JWT, ACCESS_TOKEN, REFRESH_TOKEN, CONNECTION_URL, DESKTOP_PASSWORD, PASSWORD,
           SIGNATURE)

RESPONSE = {
    "apiKeyId": "key-visible-id",
    "apiKeyName": "visible-key-name",
    "apiKey": API_KEY,
    "idToken": JWT,
    "accessToken": ACCESS_TOKEN,
    "refreshToken": REFRESH_TOKEN,
    "connectionUrl": CONNECTION_URL,
    "signIn": {"userName": "visible-desktop-user", "password": DESKTOP_PASSWORD},
    "NextToken": "visible-cursor",
}


@pytest.fixture
def verbose(monkeypatch):
    file_records = []

    class FakeLogger:
        def _record(self, msg, *a, **k):
            file_records.append(str(msg))

        debug = info = warning = error = _record

    monkeypatch.setattr(vlog, "get_logger", lambda: FakeLogger())
    monkeypatch.setattr(vlog, "_verbose_mode", True)
    return file_records


def _assert_no_secret(text):
    for secret in SECRETS:
        assert secret not in text


class TestBodies:
    def test_response_credential_fields(self, verbose, capsys):
        vlog.log_api_response(200, RESPONSE, 0.2)
        err = capsys.readouterr().err
        _assert_no_secret(err)
        # Control: the names stay, and values that are not credentials are shown.
        for shown in ('"apiKey": "' + REDACTED, '"connectionUrl": "' + REDACTED, "key-visible-id",
                      "visible-key-name", "visible-desktop-user", "visible-cursor"):
            assert shown in err

    def test_request_password_field(self, verbose, capsys):
        vlog.log_api_request("POST", "https://api.example.com/x",
                             body={"userName": "visible@example.com", "password": PASSWORD})
        err = capsys.readouterr().err
        assert PASSWORD not in err
        assert "visible@example.com" in err and '"password": "' + REDACTED in err

    def test_a_truncated_response(self, verbose, capsys):
        items = [{**RESPONSE, "index": n} for n in range(6)]
        vlog.log_api_response(200, {"Items": items}, 0.2)
        err = capsys.readouterr().err
        assert "truncated for display" in err
        _assert_no_secret(err)
        assert "key-visible-id" in err

    def test_credential_shapes_in_text(self, verbose, capsys):
        vlog.log_api_response(502, f"upstream said Bearer {ACCESS_TOKEN}xx and key {API_KEY}", 0.2)
        vlog.log_debug(f"token {JWT} for url {PRESIGNED}")
        err = capsys.readouterr().err
        _assert_no_secret(err)
        assert "upstream said" in err and "X-Amz-Signature=" + REDACTED in err

    def test_the_caller_s_body_is_not_changed(self, verbose, capsys):
        body = json.loads(json.dumps(RESPONSE))
        vlog.log_api_response(200, body, 0.2)
        capsys.readouterr()
        assert body == RESPONSE

    def test_the_log_file_is_still_fully_redacted(self, verbose, capsys):
        vlog.log_api_response(200, RESPONSE, 0.2)
        capsys.readouterr()
        joined = " ".join(verbose)
        assert "key-visible-id" in joined
        _assert_no_secret(joined)


class _Deployment(requests.adapters.BaseAdapter):
    """A transport answering the self-service API key route."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def send(self, request, stream=False, timeout=None, verify=True, cert=None, proxies=None):
        self.sent.append(request)
        response = requests.Response()
        if request.method == "POST" and request.path_url.endswith("/auth/user/api-keys"):
            response.status_code = 200
            body = {"apiKeyId": "key-visible-id", "apiKeyName": "visible-key-name",
                    "userId": "visible-user", "expiresAt": "2027-01-01T00:00:00Z", "apiKey": API_KEY}
        else:
            response.status_code = 404
            body = {"message": "Not Found"}
        response.headers["Content-Type"] = "application/json"
        response.raw = io.BytesIO(json.dumps(body).encode("utf-8"))
        response.url = request.url
        response.request = request
        return response

    def close(self):
        pass


def _real_client():
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.is_token_expired.return_value = False
    profile_manager.load_auth_profile.return_value = {"access_token": "t"}
    client = APIClient("https://api.example.com", profile_manager=profile_manager)
    client.session.mount("https://", _Deployment())
    return client


class TestApiKeyCreateUnderVerbose:
    """`--verbose api-key user create` on a real APIClient: the key reaches the command's output only."""

    @pytest.mark.parametrize("json_flag", [[], ["--json-output"]])
    def test_the_key_is_shown_once_and_never_in_the_echo(self, cli_runner, generic_command_mocks,
                                                         json_flag):
        with generic_command_mocks("apiKey") as mocks:
            mocks["patches"]["cmd_api"].side_effect = lambda *a, **k: _real_client()
            result = cli_runner.invoke(cli, ["--verbose", "api-key", "user", "create", "--name", "n",
                                             "--description", "d", "--expires-at",
                                             "2027-01-01T00:00:00Z", *json_flag])

        assert result.exit_code == 0, result.output
        # Control: the response went through the echo, masked.
        assert '"apiKey": "' + REDACTED in result.stderr and "key-visible-id" in result.stderr
        assert API_KEY not in result.stderr
        # The command's own output keeps the one-time key.
        assert result.stdout.count(API_KEY) == 1
        if json_flag:
            assert json.loads(result.stdout)["apiKey"] == API_KEY


class TestTracebacks:
    """The verbose and `--debug` tracebacks of an unexpected error pass through the console mask."""

    @staticmethod
    def _failing():
        from vamscli.utils.global_exceptions import handle_global_exceptions

        @handle_global_exceptions()
        def command():
            raise RuntimeError(f"transfer failed for {PRESIGNED} with {API_KEY}")

        return command

    def test_verbose_traceback(self, verbose, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["vamscli", "--verbose", "x"])
        with pytest.raises(SystemExit):
            self._failing()()
        err = capsys.readouterr().err
        assert "VERBOSE ERROR DETAILS" in err and "X-Amz-Signature=" + REDACTED in err
        traceback_text = err.split("VERBOSE ERROR DETAILS:")[1].split("=" * 60)[1]
        # The traceback names the error; only the credentials are masked.
        assert "RuntimeError: transfer failed for" in traceback_text
        assert "X-Amz-Signature=" + REDACTED in traceback_text
        assert SIGNATURE not in traceback_text and API_KEY not in traceback_text

    def test_debug_traceback_in_text(self, capsys, monkeypatch):
        monkeypatch.setattr(vlog, "_verbose_mode", False)
        monkeypatch.setattr(sys, "argv", ["vamscli", "x", "--debug"])
        with pytest.raises(SystemExit):
            self._failing()()
        err = capsys.readouterr().err
        traceback_text = err.split("Traceback (most recent call last)")[1]
        assert "X-Amz-Signature=" + REDACTED in traceback_text
        assert SIGNATURE not in traceback_text and API_KEY not in traceback_text

    def test_debug_traceback_in_json(self, capsys, monkeypatch):
        monkeypatch.setattr(vlog, "_verbose_mode", False)
        monkeypatch.setattr(sys, "argv", ["vamscli", "x", "--json-output", "--debug"])
        with pytest.raises(SystemExit):
            self._failing()()
        data = json.loads(capsys.readouterr().out)
        assert "X-Amz-Signature=" + REDACTED in data["traceback"]
        assert SIGNATURE not in data["traceback"] and API_KEY not in data["traceback"]
