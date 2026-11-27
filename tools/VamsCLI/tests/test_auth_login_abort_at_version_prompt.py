"""Ctrl+C or end of input at the version-mismatch prompt cancels the login.

`click.confirm` raises `click.Abort` for both, and `click.Abort` is a `RuntimeError`. The version
check's generic handler used to catch it, print "Could not check API version:" with an empty message,
and sign the user in although they chose to stop (issue #1911). Each test runs the real command with a
mocked API client.
"""

from unittest.mock import Mock, patch

import click
import pytest

from vamscli.main import cli


def _mismatch():
    return {'match': False, 'cli_version': '2.7.0', 'api_version': '2.6.7'}


def _login(cli_runner, mocks, authenticator, **invoke_kwargs):
    mocks['api_client'].check_version.return_value = _mismatch()
    mocks['api_client'].call_login_profile.return_value = {'success': True}
    mocks['api_client'].get_secure_config.return_value = {'enabled': []}
    with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
        return cli_runner.invoke(
            cli, ['auth', 'login', '-u', 'test@example.com', '-p', 'pw55'], **invoke_kwargs)


class TestAbortAtVersionMismatchPrompt:

    def test_end_of_input_cancels_the_login(self, cli_runner, generic_command_mocks):
        """A closed stdin makes the real click.confirm raise click.Abort."""
        with generic_command_mocks('auth') as mocks:
            authenticator = Mock()
            result = _login(cli_runner, mocks, authenticator, input='')

            assert result.exit_code == 0, result.output
            assert 'Version mismatch detected' in result.output
            assert 'Authentication cancelled.' in result.output
            assert 'Could not check API version' not in result.output
            authenticator.authenticate.assert_not_called()
            mocks['profile_manager'].save_auth_profile.assert_not_called()

    def test_ctrl_c_cancels_the_login(self, cli_runner, generic_command_mocks):
        """click.confirm turns a KeyboardInterrupt at the prompt into click.Abort."""
        with generic_command_mocks('auth') as mocks:
            authenticator = Mock()
            with patch('click.confirm', side_effect=click.Abort()):
                result = _login(cli_runner, mocks, authenticator)

            assert result.exit_code == 0, result.output
            assert 'Authentication cancelled.' in result.output
            assert 'Could not check API version' not in result.output
            authenticator.authenticate.assert_not_called()
            mocks['profile_manager'].save_auth_profile.assert_not_called()

    def test_failed_version_check_still_does_not_block_the_login(self, cli_runner, generic_command_mocks):
        """Control: an error from the version check itself warns and the login goes on."""
        with generic_command_mocks('auth') as mocks:
            authenticator = Mock()
            authenticator.authenticate.return_value = {
                'access_token': 'test-token', 'refresh_token': 'test-refresh', 'expires_in': 3600,
            }
            mocks['api_client'].check_version.side_effect = RuntimeError('version endpoint down')
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}
            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, ['auth', 'login', '-u', 'test@example.com', '-p', 'pw55'])

            assert result.exit_code == 0, result.output
            assert 'Could not check API version: version endpoint down' in result.output
            authenticator.authenticate.assert_called_once()
