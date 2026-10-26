"""Second confirmation of destructive commands in and out of an interactive terminal (#809).

Each destructive command below refuses to run without ``--confirm`` and, in text mode, then asks
"Are you sure you want to proceed?". The question is asked only when stdin is an interactive
terminal; with no terminal (a script, a pipe, CI) ``--confirm`` is the confirmation. ``CliRunner``'s
stdin is not a terminal, so a plain invocation exercises the non-interactive path and the
``interactive_stdin`` fixture (``tests/conftest.py``) the interactive one.
"""

import io
import json
from pathlib import Path

import pytest

from vamscli.main import cli
from vamscli.utils import confirmation
from vamscli.utils.confirmation import confirm_destructive_action, stdin_is_interactive

PROMPT = "Are you sure you want to proceed?"

# (command id, command module patched by generic_command_mocks, argv, APIClient delete method)
DESTRUCTIVE_COMMANDS = [
    ("assets delete", "assets",
     ["assets", "delete", "s27-asset", "-d", "s27-db"], "delete_asset_permanent"),
    ("database delete", "database",
     ["database", "delete", "-d", "s27-db"], "delete_database"),
    ("role delete", "roleUserConstraints",
     ["role", "delete", "-r", "s27-role"], "delete_role"),
    ("role constraint delete", "roleUserConstraints",
     ["role", "constraint", "delete", "-c", "s27-constraint"], "delete_constraint"),
    ("role user delete", "roleUserConstraints",
     ["role", "user", "delete", "-u", "user@example.com"], "delete_user_roles"),
    ("user cognito delete", "user",
     ["user", "cognito", "delete", "-u", "user@example.com"], "delete_cognito_user"),
]

IDS = [c[0] for c in DESTRUCTIVE_COMMANDS]


class _Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        if isinstance(self._tty, Exception):
            raise self._tty
        return self._tty


class TestStdinIsInteractive:
    def test_terminal(self, monkeypatch):
        monkeypatch.setattr(confirmation.sys, "stdin", _Stream(True))
        assert stdin_is_interactive() is True

    def test_pipe_or_file(self, monkeypatch):
        monkeypatch.setattr(confirmation.sys, "stdin", _Stream(False))
        assert stdin_is_interactive() is False

    def test_closed_stream(self, monkeypatch):
        monkeypatch.setattr(confirmation.sys, "stdin", _Stream(ValueError("I/O operation on closed file")))
        assert stdin_is_interactive() is False

    def test_no_stdin(self, monkeypatch):
        monkeypatch.setattr(confirmation.sys, "stdin", None)
        assert stdin_is_interactive() is False


class TestConfirmDestructiveAction:
    def test_non_interactive_does_not_prompt(self, monkeypatch):
        monkeypatch.setattr(confirmation, "stdin_is_interactive", lambda: False)

        def fail(*_a, **_k):
            raise AssertionError("prompted without a terminal")

        monkeypatch.setattr(confirmation.click, "confirm", fail)
        assert confirm_destructive_action() is True

    @pytest.mark.parametrize("answer", [True, False])
    def test_interactive_returns_the_answer(self, monkeypatch, answer):
        monkeypatch.setattr(confirmation, "stdin_is_interactive", lambda: True)
        asked = []
        monkeypatch.setattr(confirmation.click, "confirm",
                            lambda question: asked.append(question) or answer)
        assert confirm_destructive_action() is answer
        assert asked == [PROMPT]


@pytest.mark.parametrize("_name, module, argv, method", DESTRUCTIVE_COMMANDS, ids=IDS)
class TestDestructiveCommands:
    def test_confirm_without_terminal_proceeds(self, cli_runner, generic_command_mocks,
                                               _name, module, argv, method):
        """--confirm with stdin at end of input (no terminal) deletes, without prompting."""
        with generic_command_mocks(module) as mocks:
            getattr(mocks['api_client'], method).return_value = {'success': True, 'message': 'deleted'}

            result = cli_runner.invoke(cli, argv + ['--confirm'], input='')

            assert result.exit_code == 0, result.output
            assert PROMPT not in result.output
            assert 'cancelled' not in result.output.lower()
            getattr(mocks['api_client'], method).assert_called_once()

    def test_without_confirm_still_refuses(self, cli_runner, generic_command_mocks,
                                           _name, module, argv, method):
        with generic_command_mocks(module) as mocks:
            result = cli_runner.invoke(cli, argv, input='')

            assert result.exit_code != 0
            assert '--confirm' in result.output
            getattr(mocks['api_client'], method).assert_not_called()

    def test_terminal_prompt_declined(self, cli_runner, generic_command_mocks, interactive_stdin,
                                      _name, module, argv, method):
        with generic_command_mocks(module) as mocks:
            result = cli_runner.invoke(cli, argv + ['--confirm'], input='n\n')

            assert result.exit_code == 0, result.output
            assert PROMPT in result.output
            assert 'Deletion cancelled.' in result.output
            getattr(mocks['api_client'], method).assert_not_called()

    def test_terminal_prompt_accepted(self, cli_runner, generic_command_mocks, interactive_stdin,
                                      _name, module, argv, method):
        with generic_command_mocks(module) as mocks:
            getattr(mocks['api_client'], method).return_value = {'success': True, 'message': 'deleted'}

            result = cli_runner.invoke(cli, argv + ['--confirm'], input='y\n')

            assert result.exit_code == 0, result.output
            assert PROMPT in result.output
            getattr(mocks['api_client'], method).assert_called_once()

    def test_json_output_never_prompts(self, cli_runner, generic_command_mocks, interactive_stdin,
                                       _name, module, argv, method):
        """--json-output keeps skipping the question, even at a terminal."""
        with generic_command_mocks(module) as mocks:
            getattr(mocks['api_client'], method).return_value = {'success': True, 'message': 'deleted'}

            result = cli_runner.invoke(cli, argv + ['--confirm', '--json-output'], input='')

            assert result.exit_code == 0, result.output
            assert PROMPT not in result.output
            json.loads(result.output)
            getattr(mocks['api_client'], method).assert_called_once()


def _remote_item(relative_path):
    return {
        'fileName': Path(relative_path).name, 'relativePath': relative_path, 'isFolder': False,
        'size': 100, 'dateCreatedCurrentVersion': '2026-01-01T00:00:00+00:00',
        'versionId': 'v1', 'etag': 'etag1', 'isArchived': False,
    }


def _sync_api_defaults(api_client, remote_items=None):
    api_client.get_asset.return_value = {
        'assetId': 'asset1', 'databaseId': 'db1', 'assetName': 'Asset 1', 'isDistributable': True
    }
    api_client.get_database.return_value = {'databaseId': 'db1', 'restrictFileUploadsToExtensions': '.all'}
    api_client.list_asset_files.return_value = {'items': remote_items or [], 'NextToken': None}
    api_client.archive_file.return_value = {'success': True, 'affectedFiles': []}
    api_client.delete_file.return_value = {'success': True, 'affectedFiles': []}
    api_client.create_asset_version.return_value = {
        'success': True, 'assetVersionId': '2', 'operation': 'create'
    }


class TestSyncPushPermanentDelete:
    ARGV = ['--allow-delete', '--permanent-delete', '--confirm']

    def _argv(self, tmp_path):
        return ['sync', 'file', 'push', str(tmp_path), '-d', 'db1', '-a', 'asset1'] + self.ARGV

    def test_confirm_without_terminal_deletes(self, cli_runner, generic_command_mocks, tmp_path):
        with generic_command_mocks('sync') as mocks:
            _sync_api_defaults(mocks['api_client'], [_remote_item('/gone.txt')])

            result = cli_runner.invoke(cli, self._argv(tmp_path), input='')

            assert result.exit_code == 0, result.output
            assert PROMPT not in result.output
            mocks['api_client'].delete_file.assert_called_once_with('db1', 'asset1', {
                'filePath': '/gone.txt', 'isPrefix': False, 'confirmPermanentDelete': True
            })
            mocks['api_client'].archive_file.assert_not_called()

    def test_terminal_prompt_declined(self, cli_runner, generic_command_mocks, tmp_path,
                                      interactive_stdin):
        with generic_command_mocks('sync') as mocks:
            _sync_api_defaults(mocks['api_client'], [_remote_item('/gone.txt')])

            result = cli_runner.invoke(cli, self._argv(tmp_path), input='n\n')

            assert result.exit_code != 0
            assert PROMPT in result.output
            assert 'Sync push aborted' in result.output
            mocks['api_client'].delete_file.assert_not_called()

    def test_terminal_prompt_accepted(self, cli_runner, generic_command_mocks, tmp_path,
                                      interactive_stdin):
        with generic_command_mocks('sync') as mocks:
            _sync_api_defaults(mocks['api_client'], [_remote_item('/gone.txt')])

            result = cli_runner.invoke(cli, self._argv(tmp_path), input='y\n')

            assert result.exit_code == 0, result.output
            assert PROMPT in result.output
            mocks['api_client'].delete_file.assert_called_once()


class TestSyncPullDelete:
    def _argv(self, tmp_path):
        return ['sync', 'file', 'pull', str(tmp_path), '-d', 'db1', '-a', 'asset1',
                '--allow-delete', '--confirm']

    def test_confirm_without_terminal_deletes(self, cli_runner, generic_command_mocks, tmp_path):
        target = tmp_path / 'local-only.txt'
        target.write_text('x')
        with generic_command_mocks('sync') as mocks:
            _sync_api_defaults(mocks['api_client'])

            result = cli_runner.invoke(cli, self._argv(tmp_path), input='')

            assert result.exit_code == 0, result.output
            assert PROMPT not in result.output
            assert not target.exists()

    def test_terminal_prompt_accepted(self, cli_runner, generic_command_mocks, tmp_path,
                                      interactive_stdin):
        target = tmp_path / 'local-only.txt'
        target.write_text('x')
        with generic_command_mocks('sync') as mocks:
            _sync_api_defaults(mocks['api_client'])

            result = cli_runner.invoke(cli, self._argv(tmp_path), input='y\n')

            assert result.exit_code == 0, result.output
            assert PROMPT in result.output
            assert not target.exists()
