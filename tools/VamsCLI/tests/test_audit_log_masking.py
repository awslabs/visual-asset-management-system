"""What a user types into an audit filter never reaches the CLI log file or the --verbose echo.

`@requires_setup_and_auth` logs every Click argument of a command at DEBUG ("Command arguments"),
and the rotating log file is opened at DEBUG whatever `--verbose` says. The `--contains` terms and the
`--user`, `--entity-id` and `--api-key-id` values say what or who is being investigated, so those
options are `LogMaskedOption`s: the log carries a count and a length instead. The tests that read the
real log file run the real logging (`no_mock_logging`) into the temp directory the suite redirects to.
"""

from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner

import vamscli.utils.logging as vamscli_logging
from vamscli.main import cli
from vamscli.utils.logging import (
    LogMaskedOption,
    get_log_file_path,
    log_command_start,
    mask_for_log,
    redact_to_text,
)

QID = "11111111-2222-3333-4444-555555555555"
TERM = "zqxleakterm1537"
USER = "zqxuser1537"
ENTITY = "zqxentity1537"
KEY_ID = "zqxkeyid1537"


@pytest.fixture
def real_logging():
    """Real logging for one test: a fresh logger writing to the suite's temp log directory."""
    vamscli_logging._logger = None
    try:
        yield
    finally:
        if vamscli_logging._logger is not None:
            for handler in list(vamscli_logging._logger.handlers):
                handler.flush()
                handler.close()
                vamscli_logging._logger.removeHandler(handler)
        vamscli_logging._logger = None


def _log_text() -> str:
    return get_log_file_path().read_text(encoding="utf-8")


class TestMaskHelper:
    def test_values_become_a_count_and_a_length(self):
        assert mask_for_log(("abc", "defgh")) == "[masked: 2 values, 8 characters]"
        assert mask_for_log("abcd") == "[masked: 4 characters]"

    def test_an_option_that_was_not_given_is_left_alone(self):
        assert mask_for_log(()) == () and mask_for_log(None) is None and mask_for_log("") == ""

    def test_the_marker_never_holds_the_value(self):
        assert "secretvalue" not in mask_for_log(["secretvalue"])


class TestGenericMarking:
    """Any future option can opt in: nothing here is specific to the audit commands."""

    def test_a_marked_option_of_another_command_is_masked(self):
        @click.command()
        @click.option("--needle", "needle", cls=LogMaskedOption)
        @click.option("--plain", "plain")
        def demo(needle, plain):
            ctx = click.get_current_context()
            log_command_start("demo", {"needle": needle, "plain": plain})
            return ctx

        seen = {}

        def capture(command_name, args):
            seen.update(args)

        with patch("vamscli.utils.logging.get_logger") as get_logger:
            result = CliRunner().invoke(demo, ["--needle", "hunter2needle", "--plain", "visible"])
        assert result.exit_code == 0
        logged = " ".join(str(call.args) for call in get_logger.return_value.debug.call_args_list)
        assert "hunter2needle" not in logged
        assert "masked: 13 characters" in logged and "visible" in logged


@pytest.mark.no_mock_logging
class TestRealLogFile:
    def _search(self, cli_runner, generic_command_mocks, *extra, verbose=False):
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.return_value = {"queryId": QID, "status": "Running",
                                                                  "warnings": []}
            args = (["--verbose"] if verbose else []) + [
                "audit", "search", "--contains", TERM, "--user", USER, "--entity-id", ENTITY,
                "--api-key-id", KEY_ID, "--no-wait", *extra]
            result = cli_runner.invoke(cli, args)
        return result

    def test_the_filter_values_are_absent_from_the_log_file(self, cli_runner, generic_command_mocks,
                                                            real_logging):
        result = self._search(cli_runner, generic_command_mocks)
        assert result.exit_code == 0, result.output
        text = _log_text()
        for value in (TERM, USER, ENTITY, KEY_ID):
            assert value not in text, f"{value} reached the log file"
        # Positive control: the arguments line was written, with the masks in place of the values.
        assert "Command arguments" in text
        assert "masked: 1 values, 15 characters" in text  # the term

    def test_the_filter_values_are_absent_from_the_verbose_argument_echo(self, cli_runner, generic_command_mocks,
                                                                real_logging):
        result = self._search(cli_runner, generic_command_mocks, verbose=True)
        assert result.exit_code == 0, result.output
        combined = result.output + (getattr(result, "stderr", "") or "")
        for value in (TERM, USER, ENTITY, KEY_ID):
            assert value not in combined
        assert "Arguments" in combined and "masked" in combined

    def test_the_request_body_line_holds_no_filter_value_either(self, cli_runner, generic_command_mocks,
                                                                real_logging):
        """A user-only search has no `contains`; the body still names the user."""
        with generic_command_mocks("audit") as mocks:
            mocks["api_client"].start_audit_query.return_value = {"queryId": QID, "status": "Running"}
            result = cli_runner.invoke(cli, ["audit", "search", "--user", USER, "--no-wait"])
            body = mocks["api_client"].start_audit_query.call_args.args[0]
        assert result.exit_code == 0, result.output
        assert body["users"] == [USER]
        # The mocked client does not log; the helper that logs bodies is what the real client uses.
        vamscli_logging.get_logger().debug(f"Request body: {redact_to_text(body)}")
        assert USER not in _log_text()

    def test_the_old_code_path_would_have_leaked(self, real_logging):
        """Control for the assertions above: an unmarked option IS written to the log."""

        @click.command()
        @click.option("--needle", "needle")
        def demo(needle):
            log_command_start("demo", {"needle": needle})

        CliRunner().invoke(demo, ["--needle", TERM])
        assert TERM in _log_text()
