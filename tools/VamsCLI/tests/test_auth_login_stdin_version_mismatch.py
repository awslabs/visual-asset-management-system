"""A stdin credential must reach the login even when the CLI and API versions differ.

On a version mismatch, `auth login` asks "Continue with authentication?" before it reads the
credential. With `--password-stdin` the prompt takes the piped line as its answer, so the password
never reaches the login (issue #1705). Each test runs the real command with a mocked API client.
"""

from unittest.mock import Mock, patch

from vamscli.main import cli


def _tokens():
    return {
        'access_token': 'test-token',
        'refresh_token': 'test-refresh',
        'expires_in': 3600,
    }


def _mismatch():
    return {'match': False, 'cli_version': '2.7.0', 'api_version': '2.6.7'}


class TestStdinLoginAcrossVersionMismatch:

    def test_password_stdin_reaches_login_when_versions_differ(self, cli_runner, generic_command_mocks):
        """Interactive run, no --json-output: the version prompt must not eat the piped password."""
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].check_version.return_value = _mismatch()
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}
            authenticator = Mock()
            authenticator.authenticate.return_value = _tokens()

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com',
                    '--password-stdin',
                ], input='pw55\n')

            assert result.exit_code == 0, result.output
            assert 'No password was received on stdin' not in result.output
            assert authenticator.authenticate.call_args.args[1] == 'pw55'
