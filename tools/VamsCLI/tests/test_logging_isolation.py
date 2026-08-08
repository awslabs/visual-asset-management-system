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
"""

import json
import sys

import pytest

from vamscli.main import cli
from vamscli.utils import logging as vamscli_logging

# `_is_verbose_mode()` returns True when the literal string '--verbose' appears anywhere in
# `sys.argv`, so pytest's own flag would otherwise switch verbose mode on for the whole session no
# matter what the globals hold — a property of the production helper rather than a leak between
# tests, and one no fixture can reach, because the helper is consulted per call. `tests/conftest.py`
# removes that argument from `sys.argv` before collection, which is why this evaluates False even
# under `pytest --verbose`.
#
# The guard below is kept rather than deleted: it is the assertion that would catch the hazard
# returning (say, if the strip were removed or the reader started matching `-v` as well), and it
# costs one comparison. `-v` never matched the literal, so it was always clean.
_ARGV_FORCES_VERBOSE = '--verbose' in sys.argv


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

    @pytest.mark.skipif(
        _ARGV_FORCES_VERBOSE,
        reason="run as `pytest --verbose`: _is_verbose_mode() reads sys.argv, so verbose stderr "
               "reaches result.output regardless of the globals this fixture restores")
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
