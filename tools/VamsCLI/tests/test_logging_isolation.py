"""Guard that a test cannot leak verbose logging state into the tests that follow it.

`vamscli.utils.logging` holds `_verbose_mode` as a module global, and `main.py` binds
`initialize_logging` at import time, so the `mock_logging` fixture's patch of the module
attribute does not intercept the call the CLI group makes. Every `cli_runner.invoke` therefore
runs the real initializer and writes that global.

The leak is not cosmetic. In verbose mode `log_command_start` / `log_command_end` /
`log_api_request` / `log_api_response` each write to stderr, `CliRunner` merges stderr into
`result.output`, and the ~84 tests that do `json.loads(result.output)` then fail on text wrapped
around their JSON document — in tests that never touched logging. Measured before the
`isolate_logging_globals` fixture existed: `pytest --verbose` failed 113 of 1529 tests for this
reason, and four tests flipped the global mid-session in an ordinary run.

Each guard here is an ORDERED PAIR: `test_a_*` turns verbose on the way a real test does, and
`test_b_*` asserts the next test does not inherit it. Both halves are required — an assertion
made inside a single test cannot observe a missing teardown, and `test_a` doubles as the positive
control that the leak is still reproducible.

A second, separate vector used to sit alongside it: `_is_verbose_mode()` also matched the literal
`--verbose` anywhere in `sys.argv`, so pytest's own flag switched verbose mode on session-wide no
matter what the globals held, and `conftest.py` had to strip that argument before collection to keep
the suite honest. The reader now consults only the global, and
`test_verbosity_does_not_depend_on_process_argv` below pins that.
"""

import json
import sys

import pytest

from vamscli.main import cli
from vamscli.utils import logging as vamscli_logging


def test_verbosity_does_not_depend_on_process_argv(monkeypatch):
    """The reader must consult only the global Click writes, never raw argv.

    While `_is_verbose_mode()` matched the literal `--verbose` anywhere in `sys.argv`, the suite's
    result depended on how pytest was invoked: pytest's own flag turned VAMS verbose logging on for
    the whole session, every `log_*` call also wrote to stderr, `CliRunner` merged stderr into
    `result.output`, and the ~113 tests that parse it as JSON failed on text wrapped around their
    document — in tests that never touched logging. `tests/conftest.py` used to hide that by
    stripping the flag out of `sys.argv` at import, which is a process global no test owns.

    It bit production too: the literal matched as an option VALUE, so
    `vamscli search assets -q "--verbose"` turned on full request/response logging.
    """
    monkeypatch.setattr(vamscli_logging, "_verbose_mode", False)
    monkeypatch.setattr(sys, "argv", ["vamscli", "--verbose", "database", "list"])
    assert vamscli_logging._is_verbose_mode() is False

    # Positive control: the global the CLI actually sets still governs, so the assertion above is
    # about where verbosity is read FROM, not about verbosity being unreachable.
    monkeypatch.setattr(vamscli_logging, "_verbose_mode", True)
    monkeypatch.setattr(sys, "argv", ["vamscli", "database", "list"])
    assert vamscli_logging._is_verbose_mode() is True


class TestVerboseModeDoesNotLeakAcrossTests:
    """The global itself must be restored between tests."""

    def test_a_invoking_the_cli_with_verbose_sets_the_global(self, cli_runner,
                                                             generic_command_mocks):
        """POSITIVE CONTROL for `test_b`.

        If `--verbose` stops writing the global, `test_b` passes for the wrong reason and this
        file stops guarding anything. Asserted through the module attribute the CLI writes, not
        through `_is_verbose_mode()`, which also consults `sys.argv`.
        """
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_databases.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['--verbose', 'database', 'list'])
        assert result.exit_code == 0
        assert vamscli_logging._verbose_mode is True, (
            "invoking the CLI with --verbose no longer sets "
            "vamscli.utils.logging._verbose_mode, so the leak this file guards can no longer be "
            "reproduced by these tests")

    def test_b_the_next_test_starts_with_verbose_off(self):
        """The leak: a prior `--verbose` invoke must not still be in effect here."""
        assert vamscli_logging._verbose_mode is False, (
            "_verbose_mode is still True from a previous test. The isolate_logging_globals "
            "fixture in conftest.py is missing or no longer restores it, so every later test "
            "whose command logs to stderr gets that text merged into result.output.")


class TestJsonOutputSurvivesAPriorVerboseTest:
    """The consequence callers actually hit, driven through a real command.

    This is the pair that reproduces the reported symptom: a `--json-output` command emitting a
    clean JSON document, wrapped in verbose stderr text that a PREVIOUS test turned on.
    """

    def test_a_turn_verbose_on_via_the_cli(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_databases.return_value = {'Items': []}
            result = cli_runner.invoke(cli, ['--verbose', 'database', 'list'])
        assert result.exit_code == 0
        assert vamscli_logging._verbose_mode is True, "positive control failed: verbose not set"

    def test_b_json_output_is_still_a_single_parseable_document(self, cli_runner,
                                                               generic_command_mocks):
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_databases.return_value = {'Items': [{'databaseId': 'db1'}]}
            result = cli_runner.invoke(cli, ['database', 'list', '--json-output'])
        assert result.exit_code == 0
        # json.loads (not raw_decode) is deliberate: it rejects BOTH leading text ('Expecting
        # value: line 2 column 1') and trailing text ('Extra data'), which are the two observed
        # forms of this failure.
        data = json.loads(result.output)
        assert data['Items'][0]['databaseId'] == 'db1'


class TestTheLoggerSingletonIsAlsoRestored:
    """`_logger` is restored too, so a test cannot pin a stale handler for the session.

    `initialize_logging` returns the existing logger unchanged when `_logger` is already set, so a
    leaked logger makes every later call in the session a no-op regardless of the requested
    verbosity — including the `--verbose` positive controls above.
    """

    def test_a_the_logger_gets_populated_by_a_cli_invoke(self, cli_runner,
                                                         generic_command_mocks):
        with generic_command_mocks('database') as mocks:
            mocks['api_client'].list_databases.return_value = {'Items': []}
            cli_runner.invoke(cli, ['--verbose', 'database', 'list'])
        assert vamscli_logging._logger is not None, (
            "a CLI invoke no longer populates the _logger singleton; test_b below is then vacuous")

    def test_b_the_logger_is_back_to_its_pre_test_value(self):
        """Not asserted as None: the session-wide starting value is whatever ran first.

        What must hold is that `test_a`'s logger did not survive, and the only value `test_a`
        could have installed is a real `logging.Logger` named 'vamscli'.
        """
        logger = vamscli_logging._logger
        assert logger is None or getattr(logger, 'name', None) != 'vamscli', (
            "the real 'vamscli' logger installed by a previous test is still in place, so "
            "initialize_logging() will short-circuit for the rest of the session")
