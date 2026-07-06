"""Tests for Cognito forced and voluntary password change support.

Covers the CognitoAuthenticator changes (non-interactive new-password handling,
the change_password flow, and the forgot-password reset flow) and the auth login
/ auth change-password / auth forgot-password CLI commands.
"""

import json
import pytest
from unittest.mock import Mock, patch
from botocore.exceptions import ClientError

from vamscli.main import cli
from vamscli.auth.cognito import CognitoAuthenticator
from vamscli.utils.exceptions import AuthenticationError


def _make_authenticator(client_secret=None):
    """Build a CognitoAuthenticator with a mocked boto3 client."""
    authenticator = CognitoAuthenticator(
        region='us-east-1',
        user_pool_id='us-east-1_test123',
        client_id='test-client-id',
        client_secret=client_secret,
    )
    authenticator.client = Mock()
    return authenticator


def _tokens():
    """Standard AuthenticationResult payload returned by Cognito."""
    return {
        'AuthenticationResult': {
            'AccessToken': 'access-token',
            'RefreshToken': 'refresh-token',
            'IdToken': 'id-token',
            'TokenType': 'Bearer',
            'ExpiresIn': 3600,
        }
    }


def _client_error(code, message='boom', operation='ChangePassword'):
    return ClientError({'Error': {'Code': code, 'Message': message}}, operation)


class TestAuthenticatorForcedPasswordChange:
    """CognitoAuthenticator handling of the NEW_PASSWORD_REQUIRED challenge."""

    def test_new_password_required_answered_with_supplied_password(self):
        """When a new_password is supplied, the challenge is answered non-interactively."""
        authenticator = _make_authenticator()
        authenticator.client.initiate_auth.return_value = {
            'ChallengeName': 'NEW_PASSWORD_REQUIRED',
            'Session': 'session-123',
            'ChallengeParameters': {},
        }
        authenticator.client.respond_to_auth_challenge.return_value = _tokens()

        result = authenticator.authenticate(
            'user@example.com', 'TempPass1!', new_password='BrandNew1!'
        )

        assert result['access_token'] == 'access-token'
        authenticator.client.respond_to_auth_challenge.assert_called_once()
        kwargs = authenticator.client.respond_to_auth_challenge.call_args.kwargs
        assert kwargs['ChallengeName'] == 'NEW_PASSWORD_REQUIRED'
        assert kwargs['ChallengeResponses']['NEW_PASSWORD'] == 'BrandNew1!'
        # Cognito requires USERNAME in the challenge response payload.
        assert kwargs['ChallengeResponses']['USERNAME'] == 'user@example.com'
        assert kwargs['Session'] == 'session-123'

    def test_new_password_required_non_interactive_without_password_raises(self):
        """In non-interactive mode with no new_password, a clear error is raised."""
        authenticator = _make_authenticator()
        authenticator.client.initiate_auth.return_value = {
            'ChallengeName': 'NEW_PASSWORD_REQUIRED',
            'Session': 'session-123',
            'ChallengeParameters': {},
        }

        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.authenticate(
                'user@example.com', 'TempPass1!', interactive=False
            )

        assert 'new-password' in str(exc_info.value).lower()
        authenticator.client.respond_to_auth_challenge.assert_not_called()

    def test_new_password_required_interactive_prompts(self):
        """In interactive mode with no new_password, the user is prompted (existing behavior)."""
        authenticator = _make_authenticator()
        authenticator.client.initiate_auth.return_value = {
            'ChallengeName': 'NEW_PASSWORD_REQUIRED',
            'Session': 'session-123',
            'ChallengeParameters': {},
        }
        authenticator.client.respond_to_auth_challenge.return_value = _tokens()

        with patch('click.prompt', return_value='PromptedPass1!') as mock_prompt:
            result = authenticator.authenticate('user@example.com', 'TempPass1!')

        assert result['access_token'] == 'access-token'
        mock_prompt.assert_called_once()
        kwargs = authenticator.client.respond_to_auth_challenge.call_args.kwargs
        assert kwargs['ChallengeResponses']['NEW_PASSWORD'] == 'PromptedPass1!'

    def test_challenge_result_flags_password_changed(self):
        """Answering NEW_PASSWORD_REQUIRED flags that the password was already set."""
        authenticator = _make_authenticator()
        authenticator.client.initiate_auth.return_value = {
            'ChallengeName': 'NEW_PASSWORD_REQUIRED',
            'Session': 'session-123',
            'ChallengeParameters': {},
        }
        authenticator.client.respond_to_auth_challenge.return_value = _tokens()

        result = authenticator.authenticate(
            'user@example.com', 'TempPass1!', new_password='BrandNew1!'
        )

        assert result.get('password_changed_via_challenge') is True

    def test_normal_login_does_not_flag_password_changed(self):
        """A normal (no-challenge) login does not set the password-changed flag."""
        authenticator = _make_authenticator()
        authenticator.client.initiate_auth.return_value = _tokens()

        result = authenticator.authenticate('user@example.com', 'GoodPass1!')

        assert not result.get('password_changed_via_challenge')

    def test_secret_hash_included_in_challenge_when_client_secret_set(self):
        """The SECRET_HASH is included in the challenge response when a client secret is set."""
        authenticator = _make_authenticator(client_secret='shhh')
        authenticator.client.initiate_auth.return_value = {
            'ChallengeName': 'NEW_PASSWORD_REQUIRED',
            'Session': 'session-123',
            'ChallengeParameters': {},
        }
        authenticator.client.respond_to_auth_challenge.return_value = _tokens()

        authenticator.authenticate(
            'user@example.com', 'TempPass1!', new_password='BrandNew1!'
        )

        kwargs = authenticator.client.respond_to_auth_challenge.call_args.kwargs
        assert 'SECRET_HASH' in kwargs['ChallengeResponses']


class TestAuthenticatorChangePassword:
    """CognitoAuthenticator.change_password (voluntary change via access token)."""

    def test_change_password_success(self):
        authenticator = _make_authenticator()
        authenticator.client.change_password.return_value = {'ResponseMetadata': {}}

        result = authenticator.change_password('access-token', 'OldPass1!', 'NewPass1!')

        assert result['success'] is True
        authenticator.client.change_password.assert_called_once_with(
            PreviousPassword='OldPass1!',
            ProposedPassword='NewPass1!',
            AccessToken='access-token',
        )

    def test_change_password_invalid_password_policy(self):
        authenticator = _make_authenticator()
        authenticator.client.change_password.side_effect = _client_error(
            'InvalidPasswordException', 'Password does not conform to policy'
        )

        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.change_password('access-token', 'OldPass1!', 'weak')

        assert 'policy' in str(exc_info.value).lower()

    def test_change_password_wrong_current_password(self):
        authenticator = _make_authenticator()
        authenticator.client.change_password.side_effect = _client_error(
            'NotAuthorizedException', 'Incorrect username or password.'
        )

        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.change_password('access-token', 'WrongOld1!', 'NewPass1!')

        assert 'current password' in str(exc_info.value).lower()

    def test_change_password_limit_exceeded(self):
        authenticator = _make_authenticator()
        authenticator.client.change_password.side_effect = _client_error(
            'LimitExceededException', 'Attempt limit exceeded'
        )

        with pytest.raises(AuthenticationError):
            authenticator.change_password('access-token', 'OldPass1!', 'NewPass1!')


class TestAuthLoginNewPassword:
    """auth login --new-password threading for forced password changes."""

    def test_login_threads_new_password_and_interactive(self, cli_runner, generic_command_mocks):
        """--new-password is forwarded to authenticate() with interactive=True in CLI mode."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = {
                'access_token': 'test-token',
                'refresh_token': 'test-refresh',
                'expires_in': 3600,
            }
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'scheurik2',
                    '-p', 'TempPass1!',
                    '--new-password', 'BrandNew1!',
                ])

            assert result.exit_code == 0
            mock_authenticator.authenticate.assert_called_once_with(
                'scheurik2', 'TempPass1!', new_password='BrandNew1!', interactive=True
            )

    def test_login_json_output_uses_non_interactive(self, cli_runner, generic_command_mocks):
        """In JSON mode, authenticate() is called with interactive=False."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = {
                'access_token': 'test-token',
                'refresh_token': 'test-refresh',
                'expires_in': 3600,
            }
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'scheurik2',
                    '-p', 'TempPass1!',
                    '--new-password', 'BrandNew1!',
                    '--json-output',
                ])

            assert result.exit_code == 0
            mock_authenticator.authenticate.assert_called_once_with(
                'scheurik2', 'TempPass1!', new_password='BrandNew1!', interactive=False
            )

    def test_login_json_output_forced_change_without_new_password_errors(self, cli_runner, generic_command_mocks):
        """JSON-mode forced change without --new-password yields a clean JSON error, not a hang."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.side_effect = AuthenticationError(
                "A password change is required before you can sign in. "
                "Re-run the command with --new-password to set a new password."
            )

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'scheurik2',
                    '-p', 'TempPass1!',
                    '--json-output',
                ])

            assert result.exit_code == 1
            parsed = json.loads(result.output)
            assert 'new-password' in parsed['error'].lower()

    def test_login_without_new_password_backwards_compatible(self, cli_runner, generic_command_mocks):
        """A normal login (no --new-password) still passes new_password=None."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = {
                'access_token': 'test-token',
                'refresh_token': 'test-refresh',
                'expires_in': 3600,
            }
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {'enabled': []}

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com',
                    '-p', 'password123',
                ])

            assert result.exit_code == 0
            mock_authenticator.authenticate.assert_called_once_with(
                'test@example.com', 'password123', new_password=None, interactive=True
            )


class TestAuthChangePassword:
    """auth change-password command (voluntary change via username + old password)."""

    def _auth_result(self):
        return {
            'access_token': 'access-token',
            'refresh_token': 'refresh-token',
            'expires_in': 3600,
        }

    def test_change_password_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = self._auth_result()
            mock_authenticator.change_password.return_value = {'success': True}

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password',
                    '-u', 'scheurik2',
                    '--old-password', 'OldPass1!',
                    '--new-password', 'NewPass1!',
                ])

            assert result.exit_code == 0
            assert 'changed' in result.output.lower()
            mock_authenticator.authenticate.assert_called_once_with(
                'scheurik2', 'OldPass1!', new_password='NewPass1!', interactive=False
            )
            mock_authenticator.change_password.assert_called_once_with(
                'access-token', 'OldPass1!', 'NewPass1!'
            )

    def test_change_password_json_output_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = self._auth_result()
            mock_authenticator.change_password.return_value = {'success': True}

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password',
                    '-u', 'scheurik2',
                    '--old-password', 'OldPass1!',
                    '--new-password', 'NewPass1!',
                    '--json-output',
                ])

            assert result.exit_code == 0
            parsed = json.loads(result.output)
            assert parsed['success'] is True
            assert parsed['user_id'] == 'scheurik2'

    def test_change_password_forced_change_only(self, cli_runner, generic_command_mocks):
        """If the account is in forced-change state, the challenge sets the new password
        and ChangePassword is not called again."""
        with generic_command_mocks('auth') as mocks:
            forced_result = self._auth_result()
            forced_result['password_changed_via_challenge'] = True
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = forced_result

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password',
                    '-u', 'scheurik2',
                    '--old-password', 'TempPass1!',
                    '--new-password', 'NewPass1!',
                ])

            assert result.exit_code == 0
            assert 'changed' in result.output.lower()
            mock_authenticator.change_password.assert_not_called()

    def test_change_password_wrong_old_password(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.side_effect = AuthenticationError(
                "Invalid username or password"
            )

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password',
                    '-u', 'scheurik2',
                    '--old-password', 'WrongOld1!',
                    '--new-password', 'NewPass1!',
                ])

            assert result.exit_code == 1
            assert 'Invalid username or password' in result.output

    def test_change_password_invalid_new_password_policy(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = self._auth_result()
            mock_authenticator.change_password.side_effect = AuthenticationError(
                "New password does not meet the password policy: too short"
            )

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password',
                    '-u', 'scheurik2',
                    '--old-password', 'OldPass1!',
                    '--new-password', 'weak',
                ])

            assert result.exit_code == 1
            assert 'policy' in result.output.lower()

    def test_change_password_json_output_missing_params(self, cli_runner, generic_command_mocks):
        """In JSON mode, both passwords are required (no prompting)."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password',
                    '-u', 'scheurik2',
                    '--json-output',
                ])

            assert result.exit_code == 1
            parsed = json.loads(result.output)
            assert 'error' in parsed
            mock_authenticator.authenticate.assert_not_called()

    def test_change_password_rejected_for_external_auth(self, cli_runner, generic_command_mocks):
        """change-password is Cognito-only and is rejected for external deployments."""
        with generic_command_mocks('auth') as mocks:
            mocks['profile_manager'].load_config.return_value = {
                'api_gateway_url': 'https://api.example.com',
                'amplify_config': {
                    'region': 'us-east-1',
                    'cognitoUserPoolId': '',
                },
            }
            result = cli_runner.invoke(cli, [
                'auth', 'change-password',
                '-u', 'scheurik2',
                '--old-password', 'OldPass1!',
                '--new-password', 'NewPass1!',
            ])

            assert result.exit_code == 1
            assert 'cognito' in result.output.lower()

    def test_change_password_prompts_in_cli_mode(self, cli_runner, generic_command_mocks):
        """In CLI mode, missing passwords are prompted (new password confirmed)."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = self._auth_result()
            mock_authenticator.change_password.return_value = {'success': True}

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator), \
                 patch('click.prompt', side_effect=['OldPass1!', 'NewPass1!']):
                result = cli_runner.invoke(cli, [
                    'auth', 'change-password',
                    '-u', 'scheurik2',
                ])

            assert result.exit_code == 0
            mock_authenticator.change_password.assert_called_once_with(
                'access-token', 'OldPass1!', 'NewPass1!'
            )

    def test_change_password_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('auth') as mocks:
            result = cli_runner.invoke(cli, [
                'auth', 'change-password',
                '-u', 'scheurik2',
                '--old-password', 'OldPass1!',
                '--new-password', 'NewPass1!',
            ])
            assert result.exit_code == 1


class TestAuthenticatorForgotPassword:
    """CognitoAuthenticator forgot_password / confirm_forgot_password."""

    def test_forgot_password_success_returns_delivery(self):
        authenticator = _make_authenticator()
        authenticator.client.forgot_password.return_value = {
            'CodeDeliveryDetails': {
                'Destination': 'j***@example.com',
                'DeliveryMedium': 'EMAIL',
                'AttributeName': 'email',
            }
        }

        result = authenticator.forgot_password('jane@example.com')

        assert result['code_delivery']['Destination'] == 'j***@example.com'
        authenticator.client.forgot_password.assert_called_once()
        kwargs = authenticator.client.forgot_password.call_args.kwargs
        assert kwargs['Username'] == 'jane@example.com'
        assert kwargs['ClientId'] == 'test-client-id'

    def test_forgot_password_includes_secret_hash_when_secret_set(self):
        authenticator = _make_authenticator(client_secret='shhh')
        authenticator.client.forgot_password.return_value = {'CodeDeliveryDetails': {}}

        authenticator.forgot_password('jane@example.com')

        kwargs = authenticator.client.forgot_password.call_args.kwargs
        assert 'SecretHash' in kwargs

    def test_forgot_password_limit_exceeded(self):
        authenticator = _make_authenticator()
        authenticator.client.forgot_password.side_effect = _client_error(
            'LimitExceededException', 'Attempt limit exceeded', operation='ForgotPassword'
        )

        with pytest.raises(AuthenticationError):
            authenticator.forgot_password('jane@example.com')

    def test_confirm_forgot_password_success(self):
        authenticator = _make_authenticator()
        authenticator.client.confirm_forgot_password.return_value = {'ResponseMetadata': {}}

        result = authenticator.confirm_forgot_password('jane@example.com', '123456', 'NewPass1!')

        assert result['success'] is True
        authenticator.client.confirm_forgot_password.assert_called_once()
        kwargs = authenticator.client.confirm_forgot_password.call_args.kwargs
        assert kwargs['Username'] == 'jane@example.com'
        assert kwargs['ConfirmationCode'] == '123456'
        assert kwargs['Password'] == 'NewPass1!'

    def test_confirm_forgot_password_code_mismatch(self):
        authenticator = _make_authenticator()
        authenticator.client.confirm_forgot_password.side_effect = _client_error(
            'CodeMismatchException', 'Invalid verification code provided',
            operation='ConfirmForgotPassword'
        )

        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.confirm_forgot_password('jane@example.com', 'bad', 'NewPass1!')

        assert 'code' in str(exc_info.value).lower()

    def test_confirm_forgot_password_expired_code(self):
        authenticator = _make_authenticator()
        authenticator.client.confirm_forgot_password.side_effect = _client_error(
            'ExpiredCodeException', 'Invalid code provided, please request a code again',
            operation='ConfirmForgotPassword'
        )

        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.confirm_forgot_password('jane@example.com', '123456', 'NewPass1!')

        assert 'expired' in str(exc_info.value).lower()

    def test_confirm_forgot_password_invalid_password_policy(self):
        authenticator = _make_authenticator()
        authenticator.client.confirm_forgot_password.side_effect = _client_error(
            'InvalidPasswordException', 'Password does not conform to policy',
            operation='ConfirmForgotPassword'
        )

        with pytest.raises(AuthenticationError) as exc_info:
            authenticator.confirm_forgot_password('jane@example.com', '123456', 'weak')

        assert 'policy' in str(exc_info.value).lower()

    def test_confirm_forgot_password_includes_secret_hash_when_secret_set(self):
        authenticator = _make_authenticator(client_secret='shhh')
        authenticator.client.confirm_forgot_password.return_value = {}

        authenticator.confirm_forgot_password('jane@example.com', '123456', 'NewPass1!')

        kwargs = authenticator.client.confirm_forgot_password.call_args.kwargs
        assert 'SecretHash' in kwargs


class TestAuthForgotPasswordCommand:
    """auth forgot-password command (self-service reset via emailed code)."""

    def test_request_phase_sends_code(self, cli_runner, generic_command_mocks):
        """With no --code, the command requests a verification code."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.forgot_password.return_value = {
                'code_delivery': {'Destination': 'j***@example.com', 'DeliveryMedium': 'EMAIL'}
            }

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'forgot-password',
                    '-u', 'jane@example.com',
                    '--json-output',
                ])

            assert result.exit_code == 0
            mock_authenticator.forgot_password.assert_called_once_with('jane@example.com')
            mock_authenticator.confirm_forgot_password.assert_not_called()
            parsed = json.loads(result.output)
            assert parsed['code_sent'] is True

    def test_confirm_phase_resets_password(self, cli_runner, generic_command_mocks):
        """With --code and --new-password, the command confirms the reset."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.confirm_forgot_password.return_value = {'success': True}

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'forgot-password',
                    '-u', 'jane@example.com',
                    '--code', '123456',
                    '--new-password', 'NewPass1!',
                    '--json-output',
                ])

            assert result.exit_code == 0
            mock_authenticator.confirm_forgot_password.assert_called_once_with(
                'jane@example.com', '123456', 'NewPass1!'
            )
            mock_authenticator.forgot_password.assert_not_called()
            parsed = json.loads(result.output)
            assert parsed['success'] is True

    def test_interactive_prompts_through_both_phases(self, cli_runner, generic_command_mocks):
        """In interactive mode, request the code then prompt for code + new password."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.forgot_password.return_value = {
                'code_delivery': {'Destination': 'j***@example.com'}
            }
            mock_authenticator.confirm_forgot_password.return_value = {'success': True}

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator), \
                 patch('click.prompt', side_effect=['123456', 'NewPass1!']):
                result = cli_runner.invoke(cli, [
                    'auth', 'forgot-password',
                    '-u', 'jane@example.com',
                ])

            assert result.exit_code == 0
            mock_authenticator.forgot_password.assert_called_once_with('jane@example.com')
            mock_authenticator.confirm_forgot_password.assert_called_once_with(
                'jane@example.com', '123456', 'NewPass1!'
            )

    def test_json_output_code_without_new_password_errors(self, cli_runner, generic_command_mocks):
        """In JSON mode, supplying --code without --new-password is an error."""
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'forgot-password',
                    '-u', 'jane@example.com',
                    '--code', '123456',
                    '--json-output',
                ])

            assert result.exit_code == 1
            parsed = json.loads(result.output)
            assert 'error' in parsed
            mock_authenticator.forgot_password.assert_not_called()
            mock_authenticator.confirm_forgot_password.assert_not_called()

    def test_confirm_phase_code_mismatch(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mock_authenticator = Mock()
            mock_authenticator.confirm_forgot_password.side_effect = AuthenticationError(
                "The verification code is incorrect. Request a new code and try again."
            )

            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'forgot-password',
                    '-u', 'jane@example.com',
                    '--code', 'bad',
                    '--new-password', 'NewPass1!',
                ])

            assert result.exit_code == 1
            assert 'verification code' in result.output.lower()

    def test_rejected_for_external_auth(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('auth') as mocks:
            mocks['profile_manager'].load_config.return_value = {
                'api_gateway_url': 'https://api.example.com',
                'amplify_config': {
                    'region': 'us-east-1',
                    'cognitoUserPoolId': '',
                },
            }
            result = cli_runner.invoke(cli, [
                'auth', 'forgot-password',
                '-u', 'jane@example.com',
            ])

            assert result.exit_code == 1
            assert 'cognito' in result.output.lower()

    def test_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('auth') as mocks:
            result = cli_runner.invoke(cli, [
                'auth', 'forgot-password',
                '-u', 'jane@example.com',
            ])
            assert result.exit_code == 1
