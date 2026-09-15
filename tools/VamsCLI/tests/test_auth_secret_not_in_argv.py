"""Every credential option needs a secret path that is not the command line.

Both external connectors authenticate by shelling out to `vamscli auth login` with the Cognito
password after `-p` and the override token after `--token-override`. Every argument of a running
process is world-readable in the OS process table (`/proc/<pid>/cmdline`, `ps -ef`, Task Manager's
command-line column), so for the lifetime of the login the credential is readable by any other local
account — including one with no VAMS entitlement at all.

`auth login` therefore takes the credential from stdin (`--password-stdin`, `--token-override-stdin`)
while keeping the existing options working, per the owner constraint: "Implement but keep also
existing commands to not break existing implementations. Recommend against this though in
documentation / help." These tests pin three things: the stdin path exists and is honoured, the old
options still authenticate, and the old options' `--help` text says they are discouraged.

S6-TOOLS-013 is the rest of the surface, and `auth set-override` was the sharpest part of it: its
`--token` was `required=True` with no prompt fallback at all, so there was NO way to run that command
without publishing a bearer token to the process table. `auth change-password` and
`auth forgot-password` had an interactive prompt but no non-interactive non-argv form, and
`change-password` needs TWO secrets — one process has one stdin, so both stdin flags together read two
newline-separated values in a documented order rather than growing a second stream.
"""

from unittest.mock import Mock, patch

import pytest

from vamscli.main import cli
from vamscli.commands.auth import change_password, forgot_password, login, set_override


def _tokens():
    return {
        'access_token': 'test-token',
        'refresh_token': 'test-refresh',
        'expires_in': 3600,
    }


def _option(*names):
    """The click Option on `auth login` declaring any of ``names``, or None."""
    for param in login.params:
        if any(name in getattr(param, 'opts', []) for name in names):
            return param
    return None


def _declared_opts():
    return {opt for param in login.params for opt in getattr(param, 'opts', [])}


class TestPasswordViaStdin:
    """The documented secret path must keep the credential off the argument vector."""

    def test_login_declares_a_stdin_password_option(self):
        """Without this option a connector has no non-argv way to pass a password."""
        assert '--password' in _declared_opts(), (
            "sanity check failed: the login options could not be read, so the assertion below "
            "would be vacuous"
        )
        assert '--password-stdin' in _declared_opts()

    def test_login_authenticates_with_a_piped_password(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            authenticator = Mock()
            authenticator.authenticate.return_value = _tokens()
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com',
                    '--password-stdin',
                    '--json-output',
                ], input='piped-secret\n')

            assert result.exit_code == 0, result.output
            assert authenticator.authenticate.call_args.args[1] == 'piped-secret'

    def test_a_piped_password_keeps_a_trailing_space_but_loses_the_newline(
            self, cli_runner, generic_command_mocks):
        """A pipe or heredoc appends a newline; only CR/LF may be stripped from the secret.

        Stripping all trailing whitespace would silently authenticate with a different string than
        the one the caller stored, which presents as an unexplained 401.
        """
        with generic_command_mocks('auth') as mocks:
            authenticator = Mock()
            authenticator.authenticate.return_value = _tokens()
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com',
                    '--password-stdin',
                    '--json-output',
                ], input='pad ded \r\n')

            assert result.exit_code == 0, result.output
            assert authenticator.authenticate.call_args.args[1] == 'pad ded '

    def test_a_piped_password_is_decoded_as_utf8(self, cli_runner, generic_command_mocks):
        """The writer is usually another process, so the byte encoding cannot be left to the console.

        A text-mode read would use the console code page, which on Windows is not UTF-8; a non-ASCII
        password would reach Amazon Cognito mangled and present as a wrong password.
        """
        with generic_command_mocks('auth') as mocks:
            authenticator = Mock()
            authenticator.authenticate.return_value = _tokens()
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com',
                    '--password-stdin',
                    '--json-output',
                ], input='pä55wörd\n')

            assert result.exit_code == 0, result.output
            assert authenticator.authenticate.call_args.args[1] == 'pä55wörd'

    def test_an_empty_stdin_password_is_rejected(self, cli_runner, generic_command_mocks):
        """Otherwise an empty pipe reaches Cognito as an empty password, or prompts in a script."""
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].call_login_profile.return_value = {'success': True}

            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '-u', 'test@example.com',
                '--password-stdin',
                '--json-output',
            ], input='')

            assert result.exit_code != 0
            assert 'stdin' in result.output.lower()

    def test_the_password_option_still_authenticates(self, cli_runner, generic_command_mocks):
        """Backwards compatibility: the owner requires the existing form to keep working.

        This is also the control for the two help-text tests below — it proves the `-p` option is
        still wired to the authenticator, so a failure there is about wording, not about the option
        having been removed.
        """
        with generic_command_mocks('auth') as mocks:
            authenticator = Mock()
            authenticator.authenticate.return_value = _tokens()
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com',
                    '-p', 'password123',
                ])

            assert result.exit_code == 0, result.output
            authenticator.authenticate.assert_called_once_with(
                'test@example.com', 'password123', new_password=None, interactive=True
            )

    def test_the_token_override_option_still_authenticates(self, cli_runner,
                                                           generic_command_mocks):
        """Backwards compatibility for the external-auth form the ArcGIS connector uses."""
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '--user-id', 'test@example.com',
                '--token-override', 'vams_abc123',
                '--json-output',
            ])

            assert result.exit_code == 0, result.output
            mocks['profile_manager'].save_override_token.assert_called_once()


class TestForcedPasswordChangeViaStdin:
    """`auth login --new-password` completes a forced change, and was argv-only.

    S6-TOOLS-013. A new account's first sign-in is exactly the case a provisioning script automates,
    and it needed BOTH the temporary and the new password. One process has one stdin, so with both
    stdin flags set it carries two newline-separated values in the documented order.
    """

    def test_login_declares_a_stdin_new_password_option(self):
        assert '--new-password' in _declared_opts(), "sanity check failed: options unreadable"
        assert '--new-password-stdin' in _declared_opts()

    def test_both_passwords_piped_as_two_lines(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            authenticator = Mock()
            authenticator.authenticate.return_value = _tokens()
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login', '-u', 'test@example.com',
                    '--password-stdin', '--new-password-stdin', '--json-output',
                ], input='TempSecret1!\nNewSecret2!\n')

            assert result.exit_code == 0, result.output
            assert authenticator.authenticate.call_args.args[1] == 'TempSecret1!'
            assert authenticator.authenticate.call_args.kwargs['new_password'] == 'NewSecret2!'

    def test_only_the_new_password_piped(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            authenticator = Mock()
            authenticator.authenticate.return_value = _tokens()
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login', '-u', 'test@example.com', '-p', 'TempSecret1!',
                    '--new-password-stdin', '--json-output',
                ], input='NewSecret2!\n')

            assert result.exit_code == 0, result.output
            assert authenticator.authenticate.call_args.kwargs['new_password'] == 'NewSecret2!'

    def test_one_line_when_two_were_promised_is_refused(self, cli_runner, generic_command_mocks):
        """Otherwise the same string is used as both the current and the new password."""
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            with patch('vamscli.commands.auth.get_authenticator') as get_auth:
                result = cli_runner.invoke(cli, [
                    'auth', 'login', '-u', 'test@example.com',
                    '--password-stdin', '--new-password-stdin', '--json-output',
                ], input='OnlyOne\n')
            assert result.exit_code != 0
            assert 'stdin' in result.output.lower()
            get_auth.assert_not_called()

    def test_the_token_override_stdin_form_cannot_share_stdin_with_a_password(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            result = cli_runner.invoke(cli, [
                'auth', 'login', '--user-id', 'test@example.com',
                '--new-password-stdin', '--token-override-stdin', '--json-output',
            ], input='a\nb\n')
            assert result.exit_code != 0
            assert '--token-override-stdin' in result.output
            mocks['profile_manager'].save_override_token.assert_not_called()


class TestTokenOverrideViaStdin:
    """The ArcGIS connector's external-auth login needs the same non-argv path."""

    def test_login_declares_a_stdin_token_option(self):
        assert '--token-override' in _declared_opts(), (
            "sanity check failed: the login options could not be read, so the assertion below "
            "would be vacuous"
        )
        assert '--token-override-stdin' in _declared_opts()

    def test_a_piped_token_is_saved(self, cli_runner, generic_command_mocks):
        """The piped token must reach `save_override_token`, not just be read and dropped."""
        with generic_command_mocks('auth') as mocks:
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '--user-id', 'test@example.com',
                '--token-override-stdin',
                '--json-output',
            ], input='vams_piped_token\n')

            assert result.exit_code == 0, result.output
            mocks['profile_manager'].save_override_token.assert_called_once_with(
                'vams_piped_token', 'test@example.com', None
            )

    def test_a_piped_token_still_requires_a_user_id(self, cli_runner, generic_command_mocks):
        """The stdin form must reach the same validation the option form does."""
        with generic_command_mocks('auth'):
            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '--token-override-stdin',
                '--json-output',
            ], input='vams_piped_token\n')

            assert result.exit_code != 0
            assert '--user-id' in result.output

    def test_an_empty_stdin_token_is_rejected(self, cli_runner, generic_command_mocks):
        """An empty pipe must not be saved as an override token that then 401s on every call."""
        with generic_command_mocks('auth') as mocks:
            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '--user-id', 'test@example.com',
                '--token-override-stdin',
                '--json-output',
            ], input='')

            assert result.exit_code != 0
            assert 'stdin' in result.output.lower()
            mocks['profile_manager'].save_override_token.assert_not_called()


class TestStdinAndOptionFormsAreMutuallyExclusive:
    """One stdin stream, so only one credential can come from it, and never alongside its option."""

    _COMBINATIONS = (
        (['-u', 'test@example.com', '-p', 'pw', '--password-stdin'], '--password-stdin'),
        (['--user-id', 'test@example.com', '--token-override', 'vams_a',
          '--token-override-stdin'], '--token-override-stdin'),
        (['-u', 'test@example.com', '--password-stdin', '--token-override-stdin'],
         '--token-override-stdin'),
    )

    def test_conflicting_forms_are_refused(self, cli_runner, generic_command_mocks):
        for args, expected in self._COMBINATIONS:
            with generic_command_mocks('auth') as mocks:
                mocks['api_client'].call_login_profile.return_value = {'success': True}
                result = cli_runner.invoke(
                    cli, ['auth', 'login', '--json-output'] + args, input='piped\n')

                assert result.exit_code != 0, f"{args} was accepted: {result.output}"
                assert expected in result.output, f"{args}: {result.output}"
                mocks['profile_manager'].save_override_token.assert_not_called()


class TestArgvSecretOptionsAreDiscouraged:
    """"Recommend against this though in documentation / help" — the help text has to say so."""

    _DISCOURAGED = ('discourage', 'not recommended', 'deprecat', 'insecure', 'process table',
                    'process list', 'visible to other')

    def test_password_help_warns_against_the_command_line(self):
        option = _option('-p', '--password')
        assert option is not None, "the -p/--password option could not be located"
        help_text = (option.help or '').lower()
        assert 'password' in help_text, (
            f"sanity check failed: unexpected help text {option.help!r}, so the assertion below "
            "would not be about the password option"
        )
        assert any(phrase in help_text for phrase in self._DISCOURAGED), (
            f"-p/--password help does not discourage argv use: {option.help!r}"
        )

    def test_token_override_help_warns_against_the_command_line(self):
        option = _option('--token-override')
        assert option is not None, "the --token-override option could not be located"
        help_text = (option.help or '').lower()
        assert 'token' in help_text, (
            f"sanity check failed: unexpected help text {option.help!r}"
        )
        assert any(phrase in help_text for phrase in self._DISCOURAGED), (
            f"--token-override help does not discourage argv use: {option.help!r}"
        )


# ---------------------------------------------------------------------------
# S6-TOOLS-013: the rest of the credential surface
# ---------------------------------------------------------------------------


def _opts_of(command):
    return {opt for param in command.params for opt in getattr(param, 'opts', [])}


def _option_of(command, *names):
    for param in command.params:
        if any(name in getattr(param, 'opts', []) for name in names):
            return param
    return None


class TestSetOverrideTokenViaStdin:
    """`auth set-override --token` was `required=True` with no prompt and no stdin alternative, so
    the command had no form at all that kept the bearer token off the process table."""

    def test_the_stdin_option_exists(self):
        declared = _opts_of(set_override)
        assert '--token' in declared, "sanity check failed: set-override options could not be read"
        assert '--token-stdin' in declared

    def test_the_token_option_is_no_longer_click_required(self):
        """It has to stop being `required=True` for the stdin form to be usable at all; the
        exactly-one rule moves into the command body."""
        option = _option_of(set_override, '--token')
        assert option is not None
        assert option.required is False

    def test_a_piped_token_is_saved(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['profile_manager'].has_config.return_value = True
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            result = cli_runner.invoke(cli, [
                'auth', 'set-override', '-u', 'test@example.com', '--token-stdin', '--json-output',
            ], input='vams_piped_token\n')

            assert result.exit_code == 0, result.output
            mocks['profile_manager'].save_override_token.assert_called_once_with(
                'vams_piped_token', 'test@example.com', None)

    def test_the_token_option_still_works(self, cli_runner, generic_command_mocks):
        """Backwards compatibility control: the option form is the one existing scripts use."""
        with generic_command_mocks('auth') as mocks:
            mocks['profile_manager'].has_config.return_value = True
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            result = cli_runner.invoke(cli, [
                'auth', 'set-override', '-u', 'test@example.com', '--token', 'vams_argv_token',
                '--json-output',
            ])

            assert result.exit_code == 0, result.output
            mocks['profile_manager'].save_override_token.assert_called_once_with(
                'vams_argv_token', 'test@example.com', None)

    def test_neither_form_is_refused(self, cli_runner, generic_command_mocks):
        """Dropping `required=True` must not make the token optional."""
        with generic_command_mocks('auth') as mocks:
            mocks['profile_manager'].has_config.return_value = True
            result = cli_runner.invoke(cli, [
                'auth', 'set-override', '-u', 'test@example.com', '--json-output'])
            assert result.exit_code != 0
            assert '--token-stdin' in result.output
            mocks['profile_manager'].save_override_token.assert_not_called()

    def test_both_forms_together_are_refused(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['profile_manager'].has_config.return_value = True
            result = cli_runner.invoke(cli, [
                'auth', 'set-override', '-u', 'test@example.com', '--token', 'a',
                '--token-stdin', '--json-output'], input='b\n')
            assert result.exit_code != 0
            mocks['profile_manager'].save_override_token.assert_not_called()

    def test_an_empty_stdin_token_is_refused(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['profile_manager'].has_config.return_value = True
            result = cli_runner.invoke(cli, [
                'auth', 'set-override', '-u', 'test@example.com', '--token-stdin',
                '--json-output'], input='')
            assert result.exit_code != 0
            assert 'stdin' in result.output.lower()
            mocks['profile_manager'].save_override_token.assert_not_called()

    def test_the_token_option_help_discourages_argv(self):
        option = _option_of(set_override, '--token')
        help_text = (option.help or '').lower()
        assert 'token' in help_text
        assert any(phrase in help_text
                   for phrase in TestArgvSecretOptionsAreDiscouraged._DISCOURAGED), option.help


class TestChangePasswordViaStdin:
    """Two secrets, one stdin: both flags together read two newline-separated values, old then new."""

    @staticmethod
    def _configured(mocks):
        mocks['profile_manager'].has_config.return_value = True
        mocks['profile_manager'].load_config.return_value = {
            'api_gateway_url': 'https://api.example.com/api',
            'amplify_config': {'region': 'us-east-1', 'cognitoUserPoolId': 'pool',
                               'cognitoAppClientId': 'client'},
        }
        return mocks

    def test_the_stdin_options_exist(self):
        declared = _opts_of(change_password)
        assert '--old-password' in declared and '--new-password' in declared
        assert '--old-password-stdin' in declared
        assert '--new-password-stdin' in declared

    def test_both_passwords_piped_as_two_lines(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            self._configured(mocks)
            authenticator = Mock()
            authenticator.authenticate.return_value = {
                **_tokens(), 'password_changed_via_challenge': False}

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password', '-u', 'me@example.com',
                    '--old-password-stdin', '--new-password-stdin', '--json-output',
                ], input='OldSecret1!\nNewSecret2!\n')

            assert result.exit_code == 0, result.output
            # Order matters and is documented: current password first, then the new one.
            assert authenticator.authenticate.call_args.args[1] == 'OldSecret1!'
            assert authenticator.authenticate.call_args.kwargs['new_password'] == 'NewSecret2!'
            authenticator.change_password.assert_called_once_with(
                'test-token', 'OldSecret1!', 'NewSecret2!')

    def test_only_the_new_password_piped(self, cli_runner, generic_command_mocks):
        """One flag reads one line, so the old password can still come from an option or a prompt."""
        with generic_command_mocks('auth') as mocks:
            self._configured(mocks)
            authenticator = Mock()
            authenticator.authenticate.return_value = {
                **_tokens(), 'password_changed_via_challenge': True}

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password', '-u', 'me@example.com',
                    '--old-password', 'OldSecret1!', '--new-password-stdin', '--json-output',
                ], input='NewSecret2!\n')

            assert result.exit_code == 0, result.output
            assert authenticator.authenticate.call_args.kwargs['new_password'] == 'NewSecret2!'

    def test_too_few_lines_on_stdin_is_refused(self, cli_runner, generic_command_mocks):
        """One line when two were promised must not silently reuse it as both passwords."""
        with generic_command_mocks('auth') as mocks:
            self._configured(mocks)
            with patch('vamscli.commands.auth.get_authenticator') as get_auth:
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password', '-u', 'me@example.com',
                    '--old-password-stdin', '--new-password-stdin', '--json-output',
                ], input='OnlyOne\n')

            assert result.exit_code != 0
            assert 'stdin' in result.output.lower()
            get_auth.assert_not_called()

    @pytest.mark.parametrize("argv", [
        ['--old-password', 'x', '--old-password-stdin'],
        ['--new-password', 'x', '--new-password-stdin'],
    ])
    def test_an_option_with_its_own_stdin_flag_is_refused(self, cli_runner, generic_command_mocks,
                                                          argv):
        with generic_command_mocks('auth') as mocks:
            self._configured(mocks)
            with patch('vamscli.commands.auth.get_authenticator') as get_auth:
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password', '-u', 'me@example.com', '--json-output'] + argv,
                    input='piped\n')
            assert result.exit_code != 0
            get_auth.assert_not_called()

    @pytest.mark.parametrize("option_names", [('--old-password',), ('--new-password',)])
    def test_the_password_options_help_discourages_argv(self, option_names):
        option = _option_of(change_password, *option_names)
        assert option is not None, option_names
        help_text = (option.help or '').lower()
        assert any(phrase in help_text
                   for phrase in TestArgvSecretOptionsAreDiscouraged._DISCOURAGED), option.help


class TestForgotPasswordViaStdin:
    @staticmethod
    def _configured(mocks):
        mocks['profile_manager'].has_config.return_value = True
        mocks['profile_manager'].load_config.return_value = {
            'api_gateway_url': 'https://api.example.com/api',
            'amplify_config': {'region': 'us-east-1', 'cognitoUserPoolId': 'pool',
                               'cognitoAppClientId': 'client'},
        }
        return mocks

    def test_the_stdin_option_exists(self):
        declared = _opts_of(forgot_password)
        assert '--new-password' in declared
        assert '--new-password-stdin' in declared

    def test_a_piped_new_password_confirms_the_reset(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            self._configured(mocks)
            authenticator = Mock()

            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'forgot-password', '-u', 'me@example.com', '--code', '123456',
                    '--new-password-stdin', '--json-output',
                ], input='NewSecret2!\n')

            assert result.exit_code == 0, result.output
            authenticator.confirm_forgot_password.assert_called_once_with(
                'me@example.com', '123456', 'NewSecret2!')
            # The piped password must not be mistaken for the request phase.
            authenticator.forgot_password.assert_not_called()

    def test_a_piped_new_password_without_a_code_is_refused(self, cli_runner,
                                                            generic_command_mocks):
        """The confirm phase needs both halves; a piped password alone must not request a code and
        silently discard it."""
        with generic_command_mocks('auth') as mocks:
            self._configured(mocks)
            authenticator = Mock()
            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'forgot-password', '-u', 'me@example.com', '--new-password-stdin',
                    '--json-output'], input='NewSecret2!\n')

            assert result.exit_code != 0
            assert '--code' in result.output
            authenticator.confirm_forgot_password.assert_not_called()

    def test_the_option_form_still_works(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            self._configured(mocks)
            authenticator = Mock()
            with patch('vamscli.commands.auth.get_authenticator', return_value=authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'forgot-password', '-u', 'me@example.com', '--code', '123456',
                    '--new-password', 'NewSecret2!', '--json-output'])

            assert result.exit_code == 0, result.output
            authenticator.confirm_forgot_password.assert_called_once_with(
                'me@example.com', '123456', 'NewSecret2!')

    def test_the_new_password_option_help_discourages_argv(self):
        option = _option_of(forgot_password, '--new-password')
        help_text = (option.help or '').lower()
        assert any(phrase in help_text
                   for phrase in TestArgvSecretOptionsAreDiscouraged._DISCOURAGED), option.help


class TestEveryCredentialOptionHasANonArgvAlternative:
    """The rule, swept rather than spot-checked (`tools/VamsCLI/CLAUDE.md` Rule 11).

    A command that accepts a password, token, or API key as an option value only has no safe
    non-interactive form, and both connectors are non-interactive by construction. This walks the auth
    group and asserts each credential option has a `<name>-stdin` sibling, so the next credential
    option added without one fails here instead of at a security review.
    """

    # Names that carry a secret VALUE. `--token-override` is included; `--code` is not — a
    # verification code is one-time, short-lived, and useless without the account.
    CREDENTIAL_OPTS = ('--password', '--old-password', '--new-password', '--token',
                       '--token-override')

    def test_each_credential_option_has_a_stdin_sibling(self):
        from vamscli.commands.auth import auth as auth_group

        checked = 0
        problems = []
        for name, command in auth_group.commands.items():
            declared = _opts_of(command)
            for opt in self.CREDENTIAL_OPTS:
                if opt not in declared:
                    continue
                checked += 1
                if f"{opt}-stdin" not in declared:
                    problems.append(f"auth {name} declares {opt} with no {opt}-stdin")
        # Control: a walk that resolved no commands, or matched no options, would report clean.
        assert checked >= 6, f"only inspected {checked} credential options across the auth group"
        assert not problems, problems
