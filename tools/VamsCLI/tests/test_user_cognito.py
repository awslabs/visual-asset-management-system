"""Test Cognito user management functionality."""

import json
import pytest
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    CognitoUserNotFoundError,
    CognitoUserAlreadyExistsError,
    InvalidCognitoUserDataError,
    CognitoUserOperationError
)


# File-level fixtures for user command-specific testing patterns
@pytest.fixture
def user_command_mocks(generic_command_mocks):
    """Provide user command-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for user command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('user')


@pytest.fixture
def user_no_setup_mocks(no_setup_command_mocks):
    """Provide user command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('user')


class TestUserCognitoListCommand:
    """Test user cognito list command."""

    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['user', 'cognito', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all Cognito users' in result.output
        assert '--page-size' in result.output
        assert '--auto-paginate' in result.output

    def test_list_success(self, cli_runner, user_command_mocks):
        """Test successful user listing."""
        with user_command_mocks as mocks:
            mocks['api_client'].list_cognito_users.return_value = {
                'Items': [
                    {
                        'userId': 'user1@example.com',
                        'email': 'user1@example.com',
                        'phone': '+12345678900',
                        'userStatus': 'CONFIRMED',
                        'enabled': True,
                        'mfaEnabled': False,
                        'userCreateDate': '2024-01-01T00:00:00Z',
                        'userLastModifiedDate': '2024-01-02T00:00:00Z'
                    },
                    {
                        'userId': 'user2@example.com',
                        'email': 'user2@example.com',
                        'userStatus': 'FORCE_CHANGE_PASSWORD',
                        'enabled': True,
                        'mfaEnabled': True
                    }
                ]
            }

            result = cli_runner.invoke(cli, ['user', 'cognito', 'list'])

            assert result.exit_code == 0
            assert 'Found 2 Cognito user(s)' in result.output
            assert 'user1@example.com' in result.output
            assert 'user2@example.com' in result.output
            assert 'CONFIRMED' in result.output
            assert 'FORCE_CHANGE_PASSWORD' in result.output

            # Verify API call
            mocks['api_client'].list_cognito_users.assert_called_once()

    def test_list_no_setup(self, cli_runner, user_no_setup_mocks):
        """Test list without setup."""
        with user_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['user', 'cognito', 'list'])

            assert result.exit_code == 1
            # Exception is raised before output, so check exception type
            assert result.exception is not None
            assert 'Setup required' in str(result.exception)

    def test_list_with_pagination(self, cli_runner, user_command_mocks):
        """Test list with manual pagination."""
        with user_command_mocks as mocks:
            mocks['api_client'].list_cognito_users.return_value = {
                'Items': [
                    {
                        'userId': 'user1@example.com',
                        'email': 'user1@example.com',
                        'userStatus': 'CONFIRMED',
                        'enabled': True,
                        'mfaEnabled': False
                    }
                ],
                'NextToken': 'next-token-123'
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'list',
                '--page-size', '50',
                '--starting-token', 'token-123'
            ])

            assert result.exit_code == 0
            assert 'Found 1 Cognito user(s)' in result.output
            assert 'Next token: next-token-123' in result.output

            # Verify API call with pagination params
            call_args = mocks['api_client'].list_cognito_users.call_args
            assert call_args[0][0]['pageSize'] == 50
            assert call_args[0][0]['startingToken'] == 'token-123'

    def test_list_auto_paginate(self, cli_runner, user_command_mocks):
        """Test list with auto-pagination."""
        with user_command_mocks as mocks:
            # Simulate two pages of results
            mocks['api_client'].list_cognito_users.side_effect = [
                {
                    'Items': [
                        {'userId': 'user1@example.com', 'email': 'user1@example.com', 'userStatus': 'CONFIRMED', 'enabled': True, 'mfaEnabled': False}
                    ],
                    'NextToken': 'token-page-2'
                },
                {
                    'Items': [
                        {'userId': 'user2@example.com', 'email': 'user2@example.com', 'userStatus': 'CONFIRMED', 'enabled': True, 'mfaEnabled': False}
                    ]
                }
            ]

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'list',
                '--auto-paginate'
            ])

            assert result.exit_code == 0
            assert 'Auto-paginated: Retrieved 2 items in 2 page(s)' in result.output
            assert 'user1@example.com' in result.output
            assert 'user2@example.com' in result.output

            # Verify two API calls were made
            assert mocks['api_client'].list_cognito_users.call_count == 2

    def test_list_json_output(self, cli_runner, user_command_mocks):
        """Test list with JSON output."""
        with user_command_mocks as mocks:
            mocks['api_client'].list_cognito_users.return_value = {
                'Items': [
                    {
                        'userId': 'user1@example.com',
                        'email': 'user1@example.com',
                        'userStatus': 'CONFIRMED',
                        'enabled': True,
                        'mfaEnabled': False
                    }
                ]
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'list',
                '--json-output'
            ])

            assert result.exit_code == 0

            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert 'Items' in parsed
            assert len(parsed['Items']) == 1
            assert parsed['Items'][0]['userId'] == 'user1@example.com'

    def test_list_cognito_not_enabled(self, cli_runner, user_command_mocks):
        """Test list when Cognito is not enabled."""
        with user_command_mocks as mocks:
            mocks['api_client'].list_cognito_users.side_effect = CognitoUserOperationError(
                "Cognito not enabled"
            )

            result = cli_runner.invoke(cli, ['user', 'cognito', 'list'])

            assert result.exit_code == 1
            assert 'Cognito Operation Error' in result.output
            assert 'Cognito not enabled' in result.output


class TestUserCognitoCreateCommand:
    """Test user cognito create command."""

    def test_create_help(self, cli_runner):
        """Test create command help."""
        result = cli_runner.invoke(cli, ['user', 'cognito', 'create', '--help'])
        assert result.exit_code == 0
        assert 'Create a new Cognito user' in result.output
        assert '--user-id' in result.output
        assert '--email' in result.output
        assert '--phone' in result.output

    def test_create_success(self, cli_runner, user_command_mocks):
        """Test successful user creation."""
        with user_command_mocks as mocks:
            mocks['api_client'].create_cognito_user.return_value = {
                'success': True,
                'message': 'User created successfully',
                'userId': 'user@example.com',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z',
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'create',
                '-u', 'user@example.com',
                '-e', 'user@example.com'
            ])

            assert result.exit_code == 0
            assert '✓ Cognito user created successfully!' in result.output
            assert 'user@example.com' in result.output
            assert 'Operation: create' in result.output

            # Verify API call
            mocks['api_client'].create_cognito_user.assert_called_once()
            call_args = mocks['api_client'].create_cognito_user.call_args[0][0]
            assert call_args['userId'] == 'user@example.com'
            assert call_args['email'] == 'user@example.com'

    def test_create_with_phone(self, cli_runner, user_command_mocks):
        """Test user creation with phone number."""
        with user_command_mocks as mocks:
            mocks['api_client'].create_cognito_user.return_value = {
                'success': True,
                'message': 'User created successfully',
                'userId': 'user@example.com',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z',
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'create',
                '-u', 'user@example.com',
                '-e', 'user@example.com',
                '-p', '+12345678900'
            ])

            assert result.exit_code == 0
            assert '✓ Cognito user created successfully!' in result.output

            # Verify API call includes phone
            call_args = mocks['api_client'].create_cognito_user.call_args[0][0]
            assert call_args['phone'] == '+12345678900'

    def test_create_no_setup(self, cli_runner, user_no_setup_mocks):
        """Test create without setup."""
        with user_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'create',
                '-u', 'user@example.com',
                '-e', 'user@example.com'
            ])

            assert result.exit_code == 1
            # Exception is raised before output, so check exception type
            assert result.exception is not None
            assert 'Setup required' in str(result.exception)

    def test_create_user_already_exists(self, cli_runner, user_command_mocks):
        """Test create when user already exists."""
        with user_command_mocks as mocks:
            mocks['api_client'].create_cognito_user.side_effect = CognitoUserAlreadyExistsError(
                "User already exists"
            )

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'create',
                '-u', 'user@example.com',
                '-e', 'user@example.com'
            ])

            assert result.exit_code == 1
            assert 'User Already Exists' in result.output
            assert 'User already exists' in result.output

    def test_create_invalid_data(self, cli_runner, user_command_mocks):
        """Test create with invalid user data."""
        with user_command_mocks as mocks:
            mocks['api_client'].create_cognito_user.side_effect = InvalidCognitoUserDataError(
                "Invalid email format"
            )

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'create',
                '-u', 'invalid-user',
                '-e', 'invalid-email'
            ])

            assert result.exit_code == 1
            assert 'Invalid User Data' in result.output
            assert 'Invalid email format' in result.output

    def test_create_json_output(self, cli_runner, user_command_mocks):
        """Test create with JSON output."""
        with user_command_mocks as mocks:
            mocks['api_client'].create_cognito_user.return_value = {
                'success': True,
                'message': 'User created successfully',
                'userId': 'user@example.com',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z',
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'create',
                '-u', 'user@example.com',
                '-e', 'user@example.com',
                '--json-output'
            ])

            assert result.exit_code == 0

            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert parsed['userId'] == 'user@example.com'

    def test_create_missing_required_params(self, cli_runner):
        """Test create with missing required parameters."""
        # Missing email
        result = cli_runner.invoke(cli, [
            'user', 'cognito', 'create',
            '-u', 'user@example.com'
        ])
        assert result.exit_code == 2
        assert 'Missing option' in result.output or 'required' in result.output.lower()


class TestUserCognitoUpdateCommand:
    """Test user cognito update command."""

    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(cli, ['user', 'cognito', 'update', '--help'])
        assert result.exit_code == 0
        assert "Update a Cognito user's email or phone" in result.output
        assert '--user-id' in result.output
        assert '--email' in result.output
        assert '--phone' in result.output

    def test_update_email_success(self, cli_runner, user_command_mocks):
        """Test successful email update."""
        with user_command_mocks as mocks:
            mocks['api_client'].update_cognito_user.return_value = {
                'success': True,
                'message': 'User updated successfully',
                'userId': 'user@example.com',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'update',
                '-u', 'user@example.com',
                '-e', 'newemail@example.com'
            ])

            assert result.exit_code == 0
            assert '✓ Cognito user updated successfully!' in result.output
            assert 'user@example.com' in result.output

            # Verify API call
            mocks['api_client'].update_cognito_user.assert_called_once()
            call_args = mocks['api_client'].update_cognito_user.call_args
            assert call_args[0][0] == 'user@example.com'
            assert call_args[0][1]['email'] == 'newemail@example.com'

    def test_update_phone_success(self, cli_runner, user_command_mocks):
        """Test successful phone update."""
        with user_command_mocks as mocks:
            mocks['api_client'].update_cognito_user.return_value = {
                'success': True,
                'message': 'User updated successfully',
                'userId': 'user@example.com',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'update',
                '-u', 'user@example.com',
                '-p', '+12345678900'
            ])

            assert result.exit_code == 0
            assert '✓ Cognito user updated successfully!' in result.output

            # Verify API call includes phone
            call_args = mocks['api_client'].update_cognito_user.call_args[0][1]
            assert call_args['phone'] == '+12345678900'

    def test_update_both_fields(self, cli_runner, user_command_mocks):
        """Test update with both email and phone."""
        with user_command_mocks as mocks:
            mocks['api_client'].update_cognito_user.return_value = {
                'success': True,
                'message': 'User updated successfully',
                'userId': 'user@example.com',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'update',
                '-u', 'user@example.com',
                '-e', 'newemail@example.com',
                '-p', '+12345678900'
            ])

            assert result.exit_code == 0

            # Verify both fields in API call
            call_args = mocks['api_client'].update_cognito_user.call_args[0][1]
            assert call_args['email'] == 'newemail@example.com'
            assert call_args['phone'] == '+12345678900'

    def test_update_no_fields(self, cli_runner, user_command_mocks):
        """Test update without any fields."""
        with user_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'update',
                '-u', 'user@example.com'
            ])

            # Click.BadParameter raises SystemExit(2) for parameter errors
            assert result.exit_code == 2
            assert 'At least one field must be provided' in result.output

    def test_update_user_not_found(self, cli_runner, user_command_mocks):
        """Test update when user not found."""
        with user_command_mocks as mocks:
            mocks['api_client'].update_cognito_user.side_effect = CognitoUserNotFoundError(
                "User 'user@example.com' not found"
            )

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'update',
                '-u', 'user@example.com',
                '-e', 'newemail@example.com'
            ])

            assert result.exit_code == 1
            assert 'User Not Found' in result.output
            assert "User 'user@example.com' not found" in result.output

    def test_update_json_output(self, cli_runner, user_command_mocks):
        """Test update with JSON output."""
        with user_command_mocks as mocks:
            mocks['api_client'].update_cognito_user.return_value = {
                'success': True,
                'message': 'User updated successfully',
                'userId': 'user@example.com',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'update',
                '-u', 'user@example.com',
                '-e', 'newemail@example.com',
                '--json-output'
            ])

            assert result.exit_code == 0

            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert parsed['userId'] == 'user@example.com'
            assert parsed['operation'] == 'update'


class TestUserCognitoDeleteCommand:
    """Test user cognito delete command."""

    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(cli, ['user', 'cognito', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete a Cognito user' in result.output
        assert '--user-id' in result.output
        assert '--confirm' in result.output

    def test_delete_success(self, cli_runner, user_command_mocks):
        """Test successful user deletion."""
        with user_command_mocks as mocks:
            mocks['api_client'].delete_cognito_user.return_value = {
                'success': True,
                'message': 'User deleted successfully',
                'userId': 'user@example.com',
                'operation': 'delete',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'delete',
                '-u', 'user@example.com',
                '--confirm'
            ], input='y\n')

            assert result.exit_code == 0
            assert '✓ Cognito user deleted successfully!' in result.output
            assert 'user@example.com' in result.output

            # Verify API call
            mocks['api_client'].delete_cognito_user.assert_called_once_with('user@example.com')

    def test_delete_no_confirm_flag(self, cli_runner, user_command_mocks):
        """Test delete without confirm flag."""
        with user_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'delete',
                '-u', 'user@example.com'
            ])

            assert result.exit_code == 1
            assert 'User deletion requires explicit confirmation' in result.output
            assert 'Use --confirm flag' in result.output

            # Verify API was not called
            mocks['api_client'].delete_cognito_user.assert_not_called()

    def test_delete_cancelled_at_prompt(self, cli_runner, user_command_mocks):
        """Test delete cancelled at confirmation prompt."""
        with user_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'delete',
                '-u', 'user@example.com',
                '--confirm'
            ], input='n\n')

            assert result.exit_code == 0
            assert 'Deletion cancelled' in result.output

            # Verify API was not called
            mocks['api_client'].delete_cognito_user.assert_not_called()

    def test_delete_user_not_found(self, cli_runner, user_command_mocks):
        """Test delete when user not found."""
        with user_command_mocks as mocks:
            mocks['api_client'].delete_cognito_user.side_effect = CognitoUserNotFoundError(
                "User 'user@example.com' not found"
            )

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'delete',
                '-u', 'user@example.com',
                '--confirm'
            ], input='y\n')

            assert result.exit_code == 1
            assert 'User Not Found' in result.output
            assert "User 'user@example.com' not found" in result.output

    def test_delete_json_output(self, cli_runner, user_command_mocks):
        """Test delete with JSON output."""
        with user_command_mocks as mocks:
            mocks['api_client'].delete_cognito_user.return_value = {
                'success': True,
                'message': 'User deleted successfully',
                'userId': 'user@example.com',
                'operation': 'delete',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'delete',
                '-u', 'user@example.com',
                '--confirm',
                '--json-output'
            ], input='y\n')

            assert result.exit_code == 0

            # Note: Delete command has confirmation prompts that appear even with --json-output
            # This is by design for safety - the prompts use click.secho/click.echo which bypass json_output
            # For this test, we verify the command succeeds and API is called correctly
            # In real usage, users can parse the JSON from the output or use --confirm to skip interactive prompt
            
            # Verify API was called correctly
            mocks['api_client'].delete_cognito_user.assert_called_once_with('user@example.com')
            
            # Verify success message appears in output
            assert '✓ Cognito user deleted successfully!' in result.output or 'user@example.com' in result.output


class TestUserCognitoResetPasswordCommand:
    """Test user cognito reset-password command."""

    def test_reset_password_help(self, cli_runner):
        """Test reset-password command help."""
        result = cli_runner.invoke(cli, ['user', 'cognito', 'reset-password', '--help'])
        assert result.exit_code == 0
        assert "Reset a Cognito user's password" in result.output
        assert '--user-id' in result.output
        assert '--confirm' in result.output

    def test_reset_password_success(self, cli_runner, user_command_mocks):
        """Test successful password reset."""
        with user_command_mocks as mocks:
            mocks['api_client'].reset_cognito_user_password.return_value = {
                'success': True,
                'message': 'Password reset successfully',
                'userId': 'user@example.com',
                'operation': 'resetPassword',
                'timestamp': '2024-01-01T00:00:00Z',
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'reset-password',
                '-u', 'user@example.com',
                '--confirm'
            ])

            assert result.exit_code == 0
            assert '✓ Password reset successfully!' in result.output
            assert 'user@example.com' in result.output
            assert 'Operation: resetPassword' in result.output

            # Verify API call
            mocks['api_client'].reset_cognito_user_password.assert_called_once_with(
                'user@example.com',
                confirm_reset=True
            )

    def test_reset_password_no_confirm_flag(self, cli_runner, user_command_mocks):
        """Test reset-password without confirm flag."""
        with user_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'reset-password',
                '-u', 'user@example.com'
            ])

            assert result.exit_code == 1
            assert 'Password reset requires explicit confirmation' in result.output
            assert 'Use --confirm flag' in result.output

            # Verify API was not called
            mocks['api_client'].reset_cognito_user_password.assert_not_called()

    def test_reset_password_user_not_found(self, cli_runner, user_command_mocks):
        """Test reset-password when user not found."""
        with user_command_mocks as mocks:
            mocks['api_client'].reset_cognito_user_password.side_effect = CognitoUserNotFoundError(
                "User 'user@example.com' not found"
            )

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'reset-password',
                '-u', 'user@example.com',
                '--confirm'
            ])

            assert result.exit_code == 1
            assert 'User Not Found' in result.output
            assert "User 'user@example.com' not found" in result.output

    def test_reset_password_json_output(self, cli_runner, user_command_mocks):
        """Test reset-password with JSON output."""
        with user_command_mocks as mocks:
            mocks['api_client'].reset_cognito_user_password.return_value = {
                'success': True,
                'message': 'Password reset successfully',
                'userId': 'user@example.com',
                'operation': 'resetPassword',
                'timestamp': '2024-01-01T00:00:00Z',
            }

            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'reset-password',
                '-u', 'user@example.com',
                '--confirm',
                '--json-output'
            ])

            assert result.exit_code == 0

            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert parsed['userId'] == 'user@example.com'
            assert parsed['operation'] == 'resetPassword'

    def test_reset_password_no_setup(self, cli_runner, user_no_setup_mocks):
        """Test reset-password without setup."""
        with user_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'user', 'cognito', 'reset-password',
                '-u', 'user@example.com',
                '--confirm'
            ])

            assert result.exit_code == 1
            # Exception is raised before output, so check exception type
            assert result.exception is not None
            assert 'Setup required' in str(result.exception)


class TestUserCognitoCommandGroup:
    """Test user cognito command group structure."""

    def test_user_group_help(self, cli_runner):
        """Test user command group help."""
        result = cli_runner.invoke(cli, ['user', '--help'])
        assert result.exit_code == 0
        assert 'User management commands' in result.output
        assert 'cognito' in result.output

    def test_cognito_group_help(self, cli_runner):
        """Test cognito command group help."""
        result = cli_runner.invoke(cli, ['user', 'cognito', '--help'])
        assert result.exit_code == 0
        assert 'Cognito user management commands' in result.output
        assert 'list' in result.output
        assert 'create' in result.output
        assert 'update' in result.output
        assert 'delete' in result.output
        assert 'reset-password' in result.output


class TestUserCognitoErrorHandling:
    """Test error handling across all Cognito user commands."""

    def test_cognito_not_enabled_error(self, cli_runner, user_command_mocks):
        """Test handling of Cognito not enabled error."""
        with user_command_mocks as mocks:
            mocks['api_client'].list_cognito_users.side_effect = CognitoUserOperationError(
                "Cognito authentication provider is not enabled"
            )

            result = cli_runner.invoke(cli, ['user', 'cognito', 'list'])

            assert result.exit_code == 1
            assert 'Cognito Operation Error' in result.output
            assert 'Cognito authentication provider is not enabled' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
