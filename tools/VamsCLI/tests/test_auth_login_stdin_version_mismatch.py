"""A stdin credential must reach the login even when the CLI and API versions differ.

On a version mismatch, `auth login` asks "Continue with authentication?" before it reads the
credential. With a stdin flag the prompt takes the piped line as its answer, so the credential
never reaches the login (issue #1705). Each test runs the real command with a mocked API client.
"""

from unittest.mock import Mock, patch

import pytest

from vamscli.main import cli


def _tokens():
    return {
        'access_token': 'test-token',
        'refresh_token': 'test-refresh',
        'expires_in': 3600,
    }


def _mismatch():
    return {'match': False, 'cli_version': '2.7.0', 'api_version': '2.6.7'}


def _password_reached(mocks, authenticator):
    assert authenticator.authenticate.call_args.args[1] == 'pw55'


def _new_password_reached(mocks, authenticator):
    assert authenticator.authenticate.call_args.kwargs['new_password'] == 'pw55'


def _token_reached(mocks, authenticator):
    assert mocks['profile_manager'].save_override_token.call_args.args[0] == 'pw55'


class TestStdinLoginAcrossVersionMismatch:

    @pytest.mark.parametrize('args, reached', [
        (['-u', 'test@example.com', '--password-stdin'], _password_reached),
        (['-u', 'test@example.com', '-p', 'temp-pw', '--new-password-stdin'], _new_password_reached),
        (['--user-id', 'test@example.com', '--token-override-stdin'], _token_reached),
    ], ids=['password-stdin', 'new-password-stdin', 'token-override-stdin'])
    def test_stdin_credential_reaches_login_when_versions_differ(
            self, cli_runner, generic_command_mocks, args, reached):
        """Interactive run, no --json-output: the version prompt must not eat the piped credential."""
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].check_version.return_value = _mismatch()
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}
            authenticator = Mock()
            authenticator.authenticate.return_value = _tokens()

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, ['auth', 'login', *args], input='pw55\n')

            assert result.exit_code == 0, result.output
            assert 'Version mismatch detected' in result.output
            reached(mocks, authenticator)
