"""Test user role management functionality."""

import json
import pytest
import click
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    UserRoleError, UserRoleNotFoundError, UserRoleAlreadyExistsError,
    UserRoleDeletionError, InvalidUserRoleDataError,
    AuthenticationError, APIError, SetupRequiredError
)


# File-level fixtures for user role command testing patterns
@pytest.fixture
def user_role_command_mocks(generic_command_mocks):
    """Provide user role command mocks.

    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for user role command testing.

    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('roleUserConstraints')


@pytest.fixture
def user_role_no_setup_mocks(no_setup_command_mocks):
    """Provide user role command mocks for no-setup scenarios.

    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('roleUserConstraints')


class TestUserRoleListCommand:
    """Test user role list command."""

    def test_list_help(self, cli_runner):
        """Test user role list command help."""
        result = cli_runner.invoke(cli, ['role', 'user', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all user role assignments' in result.output
        assert '--page-size' in result.output
        assert '--auto-paginate' in result.output
        assert '--json-output' in result.output

    def test_list_success(self, cli_runner, user_role_command_mocks):
        """Test successful user role listing."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].list_user_roles.return_value = {
                'Items': [
                    {
                        'userId': 'user1@example.com',
                        'roleName': ['admin', 'viewer'],
                        'createdOn': '2024-01-01T00:00:00Z'
                    },
                    {
                        'userId': 'user2@example.com',
                        'roleName': ['viewer'],
                        'createdOn': '2024-01-02T00:00:00Z'
                    }
                ]
            }

            result = cli_runner.invoke(cli, ['role', 'user', 'list'])

            assert result.exit_code == 0
            assert 'user1@example.com' in result.output
            assert 'user2@example.com' in result.output
            assert 'admin' in result.output
            assert 'viewer' in result.output

            # Verify API call
            mocks['api_client'].list_user_roles.assert_called_once()

    def test_list_no_setup(self, cli_runner, user_role_no_setup_mocks):
        """Test user role list without setup."""
        with user_role_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['role', 'user', 'list'])

            assert result.exit_code == 1
            assert result.exception is not None
            assert isinstance(result.exception, SetupRequiredError)

    def test_list_empty(self, cli_runner, user_role_command_mocks):
        """Test user role list with no results."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].list_user_roles.return_value = {
                'Items': []
            }

            result = cli_runner.invoke(cli, ['role', 'user', 'list'])

            assert result.exit_code == 0
            assert 'No user role assignments found' in result.output

    def test_list_json_output(self, cli_runner, user_role_command_mocks):
        """Test user role list with JSON output."""
        with user_role_command_mocks as mocks:
            test_data = {
                'Items': [
                    {
                        'userId': 'user1@example.com',
                        'roleName': ['admin'],
                        'createdOn': '2024-01-01T00:00:00Z'
                    }
                ]
            }
            mocks['api_client'].list_user_roles.return_value = test_data

            result = cli_runner.invoke(cli, ['role', 'user', 'list', '--json-output'])

            assert result.exit_code == 0
            
            # Verify output is valid JSON
            output_data = json.loads(result.output)
            assert 'Items' in output_data
            assert len(output_data['Items']) == 1
            assert output_data['Items'][0]['userId'] == 'user1@example.com'

    def test_list_with_pagination(self, cli_runner, user_role_command_mocks):
        """Test user role list with manual pagination."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].list_user_roles.return_value = {
                'Items': [
                    {
                        'userId': 'user1@example.com',
                        'roleName': ['admin'],
                        'createdOn': '2024-01-01T00:00:00Z'
                    }
                ],
                'NextToken': 'next-token-123'
            }

            result = cli_runner.invoke(cli, [
                'role', 'user', 'list',
                '--page-size', '10',
                '--starting-token', 'token-123'
            ])

            assert result.exit_code == 0
            assert 'user1@example.com' in result.output
            assert 'Next token: next-token-123' in result.output

            # Verify API call with pagination params
            call_args = mocks['api_client'].list_user_roles.call_args
            assert call_args[0][0]['pageSize'] == 10
            assert call_args[0][0]['startingToken'] == 'token-123'

    def test_list_auto_paginate(self, cli_runner, user_role_command_mocks):
        """Test user role list with auto-pagination."""
        with user_role_command_mocks as mocks:
            # Simulate two pages of results
            mocks['api_client'].list_user_roles.side_effect = [
                {
                    'Items': [
                        {'userId': 'user1@example.com', 'roleName': ['admin'], 'createdOn': '2024-01-01T00:00:00Z'}
                    ],
                    'NextToken': 'token-page-2'
                },
                {
                    'Items': [
                        {'userId': 'user2@example.com', 'roleName': ['viewer'], 'createdOn': '2024-01-02T00:00:00Z'}
                    ]
                }
            ]

            result = cli_runner.invoke(cli, ['role', 'user', 'list', '--auto-paginate'])

            assert result.exit_code == 0
            assert 'user1@example.com' in result.output
            assert 'user2@example.com' in result.output
            assert 'Auto-paginated' in result.output
            assert '2 items' in result.output

            # Verify two API calls were made
            assert mocks['api_client'].list_user_roles.call_count == 2

    def test_list_auto_paginate_with_max_items(self, cli_runner, user_role_command_mocks):
        """Test user role list with auto-pagination and max items limit."""
        with user_role_command_mocks as mocks:
            # Simulate hitting max items limit
            mocks['api_client'].list_user_roles.return_value = {
                'Items': [
                    {'userId': f'user{i}@example.com', 'roleName': ['viewer'], 'createdOn': f'2024-01-{i:02d}T00:00:00Z'}
                    for i in range(1, 6)
                ],
                'NextToken': 'more-items-available'
            }

            result = cli_runner.invoke(cli, [
                'role', 'user', 'list',
                '--auto-paginate',
                '--max-items', '5'
            ])

            assert result.exit_code == 0
            assert 'Reached maximum of 5 items' in result.output

    def test_list_conflicting_pagination_options(self, cli_runner, user_role_command_mocks):
        """Test user role list with conflicting pagination options."""
        with user_role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'user', 'list',
                '--auto-paginate',
                '--starting-token', 'token-123'
            ])

            assert result.exit_code == 1
            assert 'Cannot use --auto-paginate with --starting-token' in result.output


class TestUserRoleCreateCommand:
    """Test user role create command."""

    def test_create_help(self, cli_runner):
        """Test user role create command help."""
        result = cli_runner.invoke(cli, ['role', 'user', 'create', '--help'])
        assert result.exit_code == 0
        assert 'Assign roles to a user' in result.output
        assert '--user-id' in result.output
        assert '--role-name' in result.output
        assert '--json-input' in result.output

    def test_create_success_single_role(self, cli_runner, user_role_command_mocks):
        """Test successful user role creation with single role."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].create_user_roles.return_value = {
                'success': True,
                'message': 'User roles created successfully',
                'userId': 'user@example.com',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'role', 'user', 'create',
                '-u', 'user@example.com',
                '--role-name', 'admin'
            ])

            assert result.exit_code == 0
            assert '✓ User roles assigned successfully!' in result.output
            assert 'user@example.com' in result.output

            # Verify API call
            call_args = mocks['api_client'].create_user_roles.call_args
            assert call_args[0][0]['userId'] == 'user@example.com'
            assert call_args[0][0]['roleName'] == ['admin']

    def test_create_success_multiple_roles(self, cli_runner, user_role_command_mocks):
        """Test successful user role creation with multiple roles."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].create_user_roles.return_value = {
                'success': True,
                'message': 'User roles created successfully',
                'userId': 'user@example.com',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'role', 'user', 'create',
                '-u', 'user@example.com',
                '--role-name', 'admin',
                '--role-name', 'viewer',
                '--role-name', 'editor'
            ])

            assert result.exit_code == 0
            assert '✓ User roles assigned successfully!' in result.output

            # Verify API call with multiple roles
            call_args = mocks['api_client'].create_user_roles.call_args
            assert call_args[0][0]['userId'] == 'user@example.com'
            assert set(call_args[0][0]['roleName']) == {'admin', 'viewer', 'editor'}

    def test_create_no_setup(self, cli_runner, user_role_no_setup_mocks):
        """Test user role create without setup."""
        with user_role_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'user', 'create',
                '-u', 'user@example.com',
                '--role-name', 'admin'
            ])

            assert result.exit_code == 1
            assert result.exception is not None
            assert isinstance(result.exception, SetupRequiredError)

    def test_create_missing_role_name(self, cli_runner, user_role_command_mocks):
        """Test user role create without role name."""
        with user_role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'user', 'create',
                '-u', 'user@example.com'
            ])

            assert result.exit_code == 1
            assert 'At least one --role-name is required' in result.output

    def test_create_already_exists(self, cli_runner, user_role_command_mocks):
        """Test user role create when role already exists."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].create_user_roles.side_effect = UserRoleAlreadyExistsError(
                "One or more roles already exist for this user"
            )

            result = cli_runner.invoke(cli, [
                'role', 'user', 'create',
                '-u', 'user@example.com',
                '--role-name', 'admin'
            ])

            assert result.exit_code == 1
            assert '✗ User Role Already Exists' in result.output
            assert 'already exist' in result.output

    def test_create_invalid_data(self, cli_runner, user_role_command_mocks):
        """Test user role create with invalid data."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].create_user_roles.side_effect = InvalidUserRoleDataError(
                "Invalid user role data: Role 'invalid-role' does not exist in the system"
            )

            result = cli_runner.invoke(cli, [
                'role', 'user', 'create',
                '-u', 'user@example.com',
                '--role-name', 'invalid-role'
            ])

            assert result.exit_code == 1
            assert '✗ Invalid User Role Data' in result.output
            assert 'does not exist' in result.output

    def test_create_with_json_input_string(self, cli_runner, user_role_command_mocks):
        """Test user role create with JSON input string."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].create_user_roles.return_value = {
                'success': True,
                'message': 'User roles created successfully',
                'userId': 'user@example.com',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            json_input = json.dumps({
                'roleName': ['admin', 'viewer']
            })

            result = cli_runner.invoke(cli, [
                'role', 'user', 'create',
                '-u', 'user@example.com',
                '--json-input', json_input
            ])

            assert result.exit_code == 0
            assert '✓ User roles assigned successfully!' in result.output

            # Verify API call
            call_args = mocks['api_client'].create_user_roles.call_args
            assert call_args[0][0]['userId'] == 'user@example.com'
            assert set(call_args[0][0]['roleName']) == {'admin', 'viewer'}

    def test_create_json_output(self, cli_runner, user_role_command_mocks):
        """Test user role create with JSON output."""
        with user_role_command_mocks as mocks:
            test_result = {
                'success': True,
                'message': 'User roles created successfully',
                'userId': 'user@example.com',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            mocks['api_client'].create_user_roles.return_value = test_result

            result = cli_runner.invoke(cli, [
                'role', 'user', 'create',
                '-u', 'user@example.com',
                '--role-name', 'admin',
                '--json-output'
            ])

            assert result.exit_code == 0
            
            # Verify output is valid JSON
            output_data = json.loads(result.output)
            assert output_data['success'] == True
            assert output_data['userId'] == 'user@example.com'


class TestUserRoleUpdateCommand:
    """Test user role update command."""

    def test_update_help(self, cli_runner):
        """Test user role update command help."""
        result = cli_runner.invoke(cli, ['role', 'user', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Update roles for a user' in result.output
        assert 'differential update' in result.output
        assert '--user-id' in result.output
        assert '--role-name' in result.output

    def test_update_success(self, cli_runner, user_role_command_mocks):
        """Test successful user role update."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].update_user_roles.return_value = {
                'success': True,
                'message': 'User roles updated successfully',
                'userId': 'user@example.com',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'role', 'user', 'update',
                '-u', 'user@example.com',
                '--role-name', 'admin',
                '--role-name', 'editor'
            ])

            assert result.exit_code == 0
            assert '✓ User roles updated successfully!' in result.output
            assert 'user@example.com' in result.output

            # Verify API call
            call_args = mocks['api_client'].update_user_roles.call_args
            assert call_args[0][0]['userId'] == 'user@example.com'
            assert set(call_args[0][0]['roleName']) == {'admin', 'editor'}

    def test_update_no_setup(self, cli_runner, user_role_no_setup_mocks):
        """Test user role update without setup."""
        with user_role_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'user', 'update',
                '-u', 'user@example.com',
                '--role-name', 'admin'
            ])

            assert result.exit_code == 1
            assert result.exception is not None
            assert isinstance(result.exception, SetupRequiredError)

    def test_update_missing_role_name(self, cli_runner, user_role_command_mocks):
        """Test user role update without role name."""
        with user_role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'user', 'update',
                '-u', 'user@example.com'
            ])

            assert result.exit_code == 1
            assert 'At least one --role-name is required' in result.output

    def test_update_not_found(self, cli_runner, user_role_command_mocks):
        """Test user role update when user role not found."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].update_user_roles.side_effect = UserRoleNotFoundError(
                "User role not found"
            )

            result = cli_runner.invoke(cli, [
                'role', 'user', 'update',
                '-u', 'nonexistent@example.com',
                '--role-name', 'admin'
            ])

            assert result.exit_code == 1
            assert '✗ User Role Not Found' in result.output

    def test_update_with_json_input(self, cli_runner, user_role_command_mocks):
        """Test user role update with JSON input."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].update_user_roles.return_value = {
                'success': True,
                'message': 'User roles updated successfully',
                'userId': 'user@example.com',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            json_input = json.dumps({
                'roleName': ['admin', 'viewer', 'editor']
            })

            result = cli_runner.invoke(cli, [
                'role', 'user', 'update',
                '-u', 'user@example.com',
                '--json-input', json_input
            ])

            assert result.exit_code == 0
            assert '✓ User roles updated successfully!' in result.output

            # Verify API call
            call_args = mocks['api_client'].update_user_roles.call_args
            assert call_args[0][0]['userId'] == 'user@example.com'
            assert set(call_args[0][0]['roleName']) == {'admin', 'viewer', 'editor'}

    def test_update_json_output(self, cli_runner, user_role_command_mocks):
        """Test user role update with JSON output."""
        with user_role_command_mocks as mocks:
            test_result = {
                'success': True,
                'message': 'User roles updated successfully',
                'userId': 'user@example.com',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            mocks['api_client'].update_user_roles.return_value = test_result

            result = cli_runner.invoke(cli, [
                'role', 'user', 'update',
                '-u', 'user@example.com',
                '--role-name', 'admin',
                '--json-output'
            ])

            assert result.exit_code == 0
            
            # Verify output is valid JSON
            output_data = json.loads(result.output)
            assert output_data['success'] == True
            assert output_data['userId'] == 'user@example.com'


class TestUserRoleDeleteCommand:
    """Test user role delete command."""

    def test_delete_help(self, cli_runner):
        """Test user role delete command help."""
        result = cli_runner.invoke(cli, ['role', 'user', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete all roles for a user' in result.output
        assert 'WARNING' in result.output
        assert '--user-id' in result.output
        assert '--confirm' in result.output

    def test_delete_success(self, cli_runner, user_role_command_mocks):
        """Test successful user role deletion."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].delete_user_roles.return_value = {
                'success': True,
                'message': 'User roles deleted successfully',
                'userId': 'user@example.com',
                'operation': 'delete',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'role', 'user', 'delete',
                '-u', 'user@example.com',
                '--confirm'
            ], input='y\n')

            assert result.exit_code == 0
            assert '✓ User roles deleted successfully!' in result.output
            assert 'user@example.com' in result.output

            # Verify API call
            mocks['api_client'].delete_user_roles.assert_called_once_with('user@example.com')

    def test_delete_no_setup(self, cli_runner, user_role_no_setup_mocks):
        """Test user role delete without setup."""
        with user_role_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'user', 'delete',
                '-u', 'user@example.com',
                '--confirm'
            ])

            assert result.exit_code == 1
            assert result.exception is not None
            assert isinstance(result.exception, SetupRequiredError)

    def test_delete_without_confirm(self, cli_runner, user_role_command_mocks):
        """Test user role delete without confirmation flag."""
        with user_role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'user', 'delete',
                '-u', 'user@example.com'
            ])

            assert result.exit_code == 1
            assert 'Confirmation required' in result.output
            assert 'Use --confirm flag' in result.output

    def test_delete_cancelled_at_prompt(self, cli_runner, user_role_command_mocks):
        """Test user role delete cancelled at confirmation prompt."""
        with user_role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'user', 'delete',
                '-u', 'user@example.com',
                '--confirm'
            ], input='n\n')

            assert result.exit_code == 0
            assert 'Deletion cancelled' in result.output

            # Verify API was not called
            mocks['api_client'].delete_user_roles.assert_not_called()

    def test_delete_not_found(self, cli_runner, user_role_command_mocks):
        """Test user role delete when user role not found."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].delete_user_roles.side_effect = UserRoleNotFoundError(
                "User roles for 'nonexistent@example.com' not found"
            )

            result = cli_runner.invoke(cli, [
                'role', 'user', 'delete',
                '-u', 'nonexistent@example.com',
                '--confirm'
            ], input='y\n')

            assert result.exit_code == 1
            assert '✗ User Role Not Found' in result.output

    def test_delete_json_output(self, cli_runner, user_role_command_mocks):
        """Test user role delete with JSON output."""
        with user_role_command_mocks as mocks:
            test_result = {
                'success': True,
                'message': 'User roles deleted successfully',
                'userId': 'user@example.com',
                'operation': 'delete',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            mocks['api_client'].delete_user_roles.return_value = test_result

            result = cli_runner.invoke(cli, [
                'role', 'user', 'delete',
                '-u', 'user@example.com',
                '--confirm',
                '--json-output'
            ])

            assert result.exit_code == 0
            
            # Verify output is valid JSON
            output_data = json.loads(result.output)
            assert output_data['success'] == True
            assert output_data['userId'] == 'user@example.com'

    def test_delete_error_handling(self, cli_runner, user_role_command_mocks):
        """Test user role delete error handling."""
        with user_role_command_mocks as mocks:
            mocks['api_client'].delete_user_roles.side_effect = UserRoleDeletionError(
                "User role deletion failed: Database error"
            )

            result = cli_runner.invoke(cli, [
                'role', 'user', 'delete',
                '-u', 'user@example.com',
                '--confirm'
            ], input='y\n')

            assert result.exit_code == 1
            assert '✗ User Role Deletion Error' in result.output


class TestUserRoleUtilityFunctions:
    """Test user role utility functions."""

    def test_format_user_role_output(self):
        """Test format_user_role_output function."""
        from vamscli.commands.roleUserConstraints import format_user_role_output

        user_role_data = {
            'userId': 'user@example.com',
            'roleName': ['admin', 'viewer'],
            'createdOn': '2024-01-01T00:00:00Z'
        }

        result = format_user_role_output(user_role_data)
        
        assert 'User Role Details:' in result
        assert 'user@example.com' in result
        assert 'admin' in result
        assert 'viewer' in result
        assert '2024-01-01T00:00:00Z' in result

    def test_format_user_role_output_json(self):
        """Test format_user_role_output with JSON mode."""
        from vamscli.commands.roleUserConstraints import format_user_role_output

        user_role_data = {
            'userId': 'user@example.com',
            'roleName': ['admin'],
            'createdOn': '2024-01-01T00:00:00Z'
        }

        result = format_user_role_output(user_role_data, json_output=True)
        
        # Verify it's valid JSON
        parsed = json.loads(result)
        assert parsed['userId'] == 'user@example.com'
        assert parsed['roleName'] == ['admin']

    def test_format_user_role_output_empty_roles(self):
        """Test format_user_role_output with no roles."""
        from vamscli.commands.roleUserConstraints import format_user_role_output

        user_role_data = {
            'userId': 'user@example.com',
            'roleName': [],
            'createdOn': '2024-01-01T00:00:00Z'
        }

        result = format_user_role_output(user_role_data)
        
        assert 'user@example.com' in result
        assert 'Roles: (none)' in result


class TestUserRoleIntegration:
    """Test user role command integration scenarios."""

    def test_create_and_list_workflow(self, cli_runner, user_role_command_mocks):
        """Test creating user roles and then listing them."""
        with user_role_command_mocks as mocks:
            # Setup create response
            mocks['api_client'].create_user_roles.return_value = {
                'success': True,
                'message': 'User roles created successfully',
                'userId': 'user@example.com',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            # Create user roles
            create_result = cli_runner.invoke(cli, [
                'role', 'user', 'create',
                '-u', 'user@example.com',
                '--role-name', 'admin'
            ])

            assert create_result.exit_code == 0

            # Setup list response
            mocks['api_client'].list_user_roles.return_value = {
                'Items': [
                    {
                        'userId': 'user@example.com',
                        'roleName': ['admin'],
                        'createdOn': '2024-01-01T00:00:00Z'
                    }
                ]
            }

            # List user roles
            list_result = cli_runner.invoke(cli, ['role', 'user', 'list'])

            assert list_result.exit_code == 0
            assert 'user@example.com' in list_result.output
            assert 'admin' in list_result.output


if __name__ == '__main__':
    pytest.main([__file__])
