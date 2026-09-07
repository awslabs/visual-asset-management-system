"""The connector must not pass a password or an override token on the command line.

Every argument of a running process is world-readable in the OS process table
(``/proc/<pid>/cmdline``, ``ps -ef``, Task Manager's command-line column), so a credential after
``-p`` or ``--token-override`` is readable by any other local account for the lifetime of the login —
including an account with no VAMS entitlement. Isaac Sim runs on shared workstations and render
nodes, so "another local account" is not hypothetical.

``login`` and ``login_with_token`` therefore route the secret through the child process's stdin
(``subprocess.run(..., input=...)``) against ``vamscli auth login --password-stdin`` /
``--token-override-stdin``, while the CLI keeps accepting the option forms. These tests assert at the
process boundary, so they see the argv the OS would see: the shared ``cli_service`` fixture patches
``_execute_command`` away and cannot observe it.

Guards FIX-056 (S6C-CONNECTORS-005): a password or override token passed on the vamscli command
line is visible in the OS process table. The ArcGIS Pro connector carries the same change in
``Services/VamsCliService.cs``, which has no test project in this repository to assert it.
"""

import json
import logging

import pytest

from vams.connector.isaacsim import vams_cli_service as module

PASSWORD = "n0t-in-argv-please"
OVERRIDE_TOKEN = "vams_n0t_in_argv_either"
USERNAME = "user@example.invalid"


class _CompletedProcess:
    def __init__(self, stdout: str):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


class _Recorder:
    """Records argv and kwargs of every ``subprocess.run`` the connector makes."""

    def __init__(self):
        self.argvs = []
        self.kwargs = []
        self.responses = []

    def run(self, cmd, **kwargs):
        self.argvs.append(list(cmd))
        self.kwargs.append(kwargs)
        return _CompletedProcess(self.responses.pop(0) if self.responses else "{}")

    @property
    def last(self):
        return self.argvs[-1]

    @property
    def last_kwargs(self):
        return self.kwargs[-1]


@pytest.fixture
def recorder(monkeypatch):
    """A ``subprocess.run`` recorder plus a service that uses the real ``_execute_command``."""
    monkeypatch.setattr(module.VamsCliService, "_verify_cli_installed", lambda self: None)
    rec = _Recorder()
    monkeypatch.setattr(module.subprocess, "run", rec.run)
    rec.service = module.VamsCliService()
    return rec


def _login_payload():
    """A success payload that also carries the web URL, so ``login`` issues no follow-up call."""
    return json.dumps({"success": True, "web_deployed_url": "https://vams.example.invalid"})


class TestSecretsStayOffTheArgumentVector:
    """Neither credential may appear in any token the connector hands to the OS."""

    def test_the_recorder_observes_the_login_argv(self, recorder):
        """Control: the login command really reaches ``subprocess.run``.

        Without this, "the password is not in argv" is satisfied just as well by a recorder that was
        never wired up or a login that never ran.
        """
        recorder.responses = [_login_payload()]
        recorder.service.login(USERNAME, PASSWORD)
        argv = recorder.last
        assert argv[:1] == ["vamscli"]
        assert "auth" in argv and "login" in argv
        assert USERNAME in argv, f"the username should still be an ordinary argument: {argv}"

    def test_the_cognito_password_is_not_an_argument(self, recorder):
        recorder.responses = [_login_payload()]
        recorder.service.login(USERNAME, PASSWORD)
        argv = recorder.last
        assert USERNAME in argv, (
            "sanity check failed: this is not the login argv, so the assertion below is vacuous"
        )
        assert not any(PASSWORD in token for token in argv), (
            f"the password appears in argv: {argv}"
        )

    def test_the_cognito_password_is_piped_to_stdin(self, recorder):
        """A fix that only drops ``-p`` without supplying the secret would break login silently."""
        recorder.responses = [_login_payload()]
        recorder.service.login(USERNAME, PASSWORD)
        piped = recorder.last_kwargs.get("input")
        assert piped is not None, "no stdin payload was supplied to the CLI"
        assert PASSWORD in piped

    def test_the_override_token_is_not_an_argument(self, recorder):
        recorder.service.login_with_token(USERNAME, OVERRIDE_TOKEN)
        argv = recorder.last
        assert USERNAME in argv, (
            "sanity check failed: this is not the login argv, so the assertion below is vacuous"
        )
        assert not any(OVERRIDE_TOKEN in token for token in argv), (
            f"the override token appears in argv: {argv}"
        )

    def test_the_override_token_is_piped_to_stdin(self, recorder):
        """Dropping ``--token-override`` without supplying the token would save an empty override."""
        recorder.service.login_with_token(USERNAME, OVERRIDE_TOKEN)
        piped = recorder.last_kwargs.get("input")
        assert piped is not None, "no stdin payload was supplied to the CLI"
        assert OVERRIDE_TOKEN in piped

    def test_the_recorder_observes_the_token_login_argv(self, recorder):
        """Control for the override-token case: that command reaches ``subprocess.run`` too."""
        recorder.service.login_with_token(USERNAME, OVERRIDE_TOKEN)
        argv = recorder.last
        assert argv[:1] == ["vamscli"]
        assert "auth" in argv and "login" in argv

    def test_the_stdin_payload_is_utf8_encoded(self, recorder):
        """The CLI decodes a piped credential as UTF-8; the locale code page is not always it.

        Left to the locale, a non-ASCII password would reach Cognito mangled and look like a wrong
        password, with no diagnostic anywhere.
        """
        recorder.responses = [_login_payload()]
        recorder.service.login(USERNAME, "pä55wörd")
        assert recorder.last_kwargs.get("encoding") == "utf-8"
        assert recorder.last_kwargs.get("input") == "pä55wörd"


class TestTheStdinFlagsMatchTheCliContract:
    """The connector spawns the CLI, so a renamed flag surfaces only as a non-zero exit at runtime.

    The paired assertion that `auth login` still declares these flags lives in
    ``tools/VamsCLI/tests/test_auth_secret_not_in_argv.py``; together they catch a rename on either
    side. Nothing else does — this connector does not import the CLI.
    """

    def test_the_password_login_asks_for_the_stdin_form(self, recorder):
        recorder.responses = [_login_payload()]
        recorder.service.login(USERNAME, PASSWORD)
        argv = recorder.last
        assert "--password-stdin" in argv, argv
        assert "-p" not in argv, f"the argv password option is still used: {argv}"

    def test_the_token_login_asks_for_the_stdin_form(self, recorder):
        recorder.service.login_with_token(USERNAME, OVERRIDE_TOKEN)
        argv = recorder.last
        assert "--token-override-stdin" in argv, argv
        assert "--token-override" not in argv, f"the argv token option is still used: {argv}"


class TestSecretsStayOutOfTheConnectorLog:
    """``_execute_command`` debug-logs the whole argv, so an argv secret lands in the Kit log too."""

    def test_the_password_is_not_debug_logged(self, recorder, caplog):
        recorder.responses = [_login_payload()]
        with caplog.at_level(logging.DEBUG, logger=module.logger.name):
            recorder.service.login(USERNAME, PASSWORD)
        emitted = "\n".join(record.getMessage() for record in caplog.records)
        assert "Executing" in emitted, (
            "sanity check failed: the command trace was not captured, so the assertion below is "
            "vacuous"
        )
        assert PASSWORD not in emitted, "the password was written to the connector log"

    def test_the_override_token_is_not_debug_logged(self, recorder, caplog):
        """The token path traces the same argv and also echoes stdout, which a Kit log routinely
        carries into a support ticket."""
        with caplog.at_level(logging.DEBUG, logger=module.logger.name):
            recorder.service.login_with_token(USERNAME, OVERRIDE_TOKEN)
        emitted = "\n".join(record.getMessage() for record in caplog.records)
        assert "Executing" in emitted, (
            "sanity check failed: the command trace was not captured, so the assertion below is "
            "vacuous"
        )
        assert OVERRIDE_TOKEN not in emitted, "the override token was written to the connector log"

    def test_the_login_response_echo_carries_no_credential(self, recorder, caplog):
        """`_execute_command` debug-logs the first 500 characters of stdout. `auth login` returns
        only success/user_id/expiry notes, so nothing sensitive rides back out — asserted here
        because a future CLI change that added a token to that response would leak it silently."""
        recorder.responses = [_login_payload()]
        with caplog.at_level(logging.DEBUG, logger=module.logger.name):
            recorder.service.login(USERNAME, PASSWORD)
        emitted = "\n".join(record.getMessage() for record in caplog.records)
        assert "stdout:" in emitted, (
            "sanity check failed: the stdout echo was not captured, so the assertion below is "
            "vacuous"
        )
        assert PASSWORD not in emitted
