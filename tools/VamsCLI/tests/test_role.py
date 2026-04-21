"""Test role management functionality."""

import json
import pytest
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    RoleNotFoundError, RoleAlreadyExistsError, RoleDeletionError, InvalidRoleDataError,
    SetupRequiredError
)


# File-level fixtures for role-specific testing patterns
@pytest.fixture
def role_command_mocks(generic_command_mocks):
    """Provide role-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for role command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('roleUserConstraints')


@pytest.fixture
def role_no_setup_mocks(no_setup_command_mocks):
    """Provide role command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('roleUserConstraints')


class TestRoleHelp:
    """Test role command help text."""
    
    def test_role_help(self, cli_runner):
        """Test role command group help."""
        result = cli_runner.invoke(cli, ['role', '--help'])
        assert result.exit_code == 0
        assert 'Role management commands' in result.output
        assert 'list' in result.output
        assert 'create' in result.output
        assert 'update' in result.output
        assert 'delete' in result.output
    
    def test_role_list_help(self, cli_runner):
        """Test role list command help."""
        result = cli_runner.invoke(cli, ['role', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all roles' in result.output
        assert '--page-size' in result.output
        assert '--auto-paginate' in result.output
        assert '--json-output' in result.output
    
    def test_role_create_help(self, cli_runner):
        """Test role create command help."""
        result = cli_runner.invoke(cli, ['role', 'create', '--help'])
        assert result.exit_code == 0
        assert 'Create a new role' in result.output
        assert '--role-name' in result.output
        assert '--description' in result.output
        assert '--mfa-required' in result.output
        assert '--json-input' in result.output
    
    def test_role_update_help(self, cli_runner):
        """Test role update command help."""
        result = cli_runner.invoke(cli, ['role', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Update an existing role' in result.output
        assert '--role-name' in result.output
        assert '--description' in result.output
        assert '--mfa-required' in result.output
        assert '--no-mfa-required' in result.output
    
    def test_role_delete_help(self, cli_runner):
        """Test role delete command help."""
        result = cli_runner.invoke(cli, ['role', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete a role' in result.output
        assert '--role-name' in result.output
        assert '--confirm' in result.output


class TestRoleListCommand:
    """Test role list command."""
    
    def test_list_success(self, cli_runner, role_command_mocks):
        """Test successful role listing."""
        with role_command_mocks as mocks:
            mocks['api_client'].list_roles.return_value = {
                'message': {
                    'Items': [
                        {
                            'roleName': 'admin',
                            'description': 'Administrator role',
                            'id': 'role-uuid-1',
                            'createdOn': '2024-01-01T00:00:00',
                            'mfaRequired': True
                        },
                        {
                            'roleName': 'viewer',
                            'description': 'Read-only role',
                            'id': 'role-uuid-2',
                            'createdOn': '2024-01-02T00:00:00',
                            'mfaRequired': False
                        }
                    ]
                }
            }
            
            result = cli_runner.invoke(cli, ['role', 'list'])
            
            assert result.exit_code == 0
            assert 'Found 2 role(s)' in result.output
            assert 'admin' in result.output
            assert 'viewer' in result.output
            assert 'Administrator role' in result.output
            
            # Verify API call
            mocks['api_client'].list_roles.assert_called_once()
    
    def test_list_empty(self, cli_runner, role_command_mocks):
        """Test listing when no roles exist."""
        with role_command_mocks as mocks:
            mocks['api_client'].list_roles.return_value = {
                'message': {'Items': []}
            }
            
            result = cli_runner.invoke(cli, ['role', 'list'])
            
            assert result.exit_code == 0
            assert 'No roles found' in result.output
    
    def test_list_auto_paginate(self, cli_runner, role_command_mocks):
        """Test role listing with auto-pagination."""
        with role_command_mocks as mocks:
            # Simulate two pages of results
            mocks['api_client'].list_roles.side_effect = [
                {
                    'message': {
                        'Items': [{'roleName': f'role{i}', 'description': f'Role {i}', 'mfaRequired': False} for i in range(100)],
                        'NextToken': 'token123'
                    }
                },
                {
                    'message': {
                        'Items': [{'roleName': f'role{i}', 'description': f'Role {i}', 'mfaRequired': False} for i in range(100, 150)]
                    }
                }
            ]
            
            result = cli_runner.invoke(cli, ['role', 'list', '--auto-paginate'])
            
            assert result.exit_code == 0
            assert 'Auto-paginated: Retrieved 150 items in 2 page(s)' in result.output
            assert 'Found 150 role(s)' in result.output
            
            # Verify two API calls were made
            assert mocks['api_client'].list_roles.call_count == 2
    
    def test_list_manual_paginate(self, cli_runner, role_command_mocks):
        """Test role listing with manual pagination."""
        with role_command_mocks as mocks:
            mocks['api_client'].list_roles.return_value = {
                'message': {
                    'Items': [{'roleName': 'admin', 'description': 'Admin', 'mfaRequired': False}],
                    'NextToken': 'token123'
                }
            }
            
            result = cli_runner.invoke(cli, ['role', 'list', '--page-size', '10'])
            
            assert result.exit_code == 0
            assert 'Next token: token123' in result.output
            assert 'Use --starting-token to get the next page' in result.output
    
    def test_list_json_output(self, cli_runner, role_command_mocks):
        """Test role listing with JSON output."""
        with role_command_mocks as mocks:
            mocks['api_client'].list_roles.return_value = {
                'message': {
                    'Items': [
                        {'roleName': 'admin', 'description': 'Admin role', 'mfaRequired': True}
                    ]
                }
            }
            
            result = cli_runner.invoke(cli, ['role', 'list', '--json-output'])
            
            assert result.exit_code == 0
            
            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert 'Items' in parsed
            assert len(parsed['Items']) == 1
            assert parsed['Items'][0]['roleName'] == 'admin'
    
    def test_list_no_setup(self, cli_runner, role_no_setup_mocks):
        """Test role list without setup."""
        with role_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['role', 'list'])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)
    
    def test_list_conflicting_pagination_options(self, cli_runner, role_command_mocks):
        """Test that conflicting pagination options are rejected."""
        with role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'list',
                '--auto-paginate',
                '--starting-token', 'token123'
            ])
            
            assert result.exit_code == 1
            assert 'Cannot use --auto-paginate with --starting-token' in result.output


class TestRoleCreateCommand:
    """Test role create command."""
    
    def test_create_success(self, cli_runner, role_command_mocks):
        """Test successful role creation."""
        with role_command_mocks as mocks:
            mocks['api_client'].create_role.return_value = {
                'success': True,
                'message': 'Role admin created successfully',
                'roleName': 'admin',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00'
            }
            
            result = cli_runner.invoke(cli, [
                'role', 'create',
                '-r', 'admin',
                '--description', 'Administrator role'
            ])
            
            assert result.exit_code == 0
            assert '✓ Role created successfully!' in result.output
            assert 'admin' in result.output
            
            # Verify API call
            mocks['api_client'].create_role.assert_called_once()
            call_args = mocks['api_client'].create_role.call_args[0][0]
            assert call_args['roleName'] == 'admin'
            assert call_args['description'] == 'Administrator role'
    
    def test_create_with_mfa(self, cli_runner, role_command_mocks):
        """Test role creation with MFA requirement."""
        with role_command_mocks as mocks:
            mocks['api_client'].create_role.return_value = {
                'success': True,
                'message': 'Role created',
                'roleName': 'secure-admin',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00'
            }
            
            result = cli_runner.invoke(cli, [
                'role', 'create',
                '-r', 'secure-admin',
                '--description', 'Secure admin',
                '--mfa-required'
            ])
            
            assert result.exit_code == 0
            
            # Verify MFA flag was set
            call_args = mocks['api_client'].create_role.call_args[0][0]
            assert call_args['mfaRequired'] is True
    
    def test_create_with_source(self, cli_runner, role_command_mocks):
        """Test role creation with source information."""
        with role_command_mocks as mocks:
            mocks['api_client'].create_role.return_value = {
                'success': True,
                'message': 'Role created',
                'roleName': 'ldap-admin',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00'
            }
            
            result = cli_runner.invoke(cli, [
                'role', 'create',
                '-r', 'ldap-admin',
                '--description', 'LDAP admin',
                '--source', 'LDAP',
                '--source-identifier', 'cn=admin,dc=example,dc=com'
            ])
            
            assert result.exit_code == 0
            
            # Verify source fields were set
            call_args = mocks['api_client'].create_role.call_args[0][0]
            assert call_args['source'] == 'LDAP'
            assert call_args['sourceIdentifier'] == 'cn=admin,dc=example,dc=com'
    
    def test_create_with_json_input(self, cli_runner, role_command_mocks):
        """Test role creation with JSON input."""
        with role_command_mocks as mocks:
            mocks['api_client'].create_role.return_value = {
                'success': True,
                'message': 'Role created',
                'roleName': 'json-role',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00'
            }
            
            json_data = json.dumps({
                'roleName': 'json-role',
                'description': 'Role from JSON',
                'mfaRequired': True
            })
            
            result = cli_runner.invoke(cli, [
                'role', 'create',
                '-r', 'json-role',
                '--json-input', json_data
            ])
            
            assert result.exit_code == 0
            
            # Verify JSON data was used
            call_args = mocks['api_client'].create_role.call_args[0][0]
            assert call_args['description'] == 'Role from JSON'
            assert call_args['mfaRequired'] is True
    
    def test_create_missing_description(self, cli_runner, role_command_mocks):
        """Test role creation without required description."""
        with role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'create',
                '-r', 'admin'
            ])
            
            assert result.exit_code == 1
            assert '--description is required' in result.output
    
    def test_create_already_exists(self, cli_runner, role_command_mocks):
        """Test creating a role that already exists."""
        with role_command_mocks as mocks:
            mocks['api_client'].create_role.side_effect = RoleAlreadyExistsError(
                "Role already exists: Role 'admin' already exists"
            )
            
            result = cli_runner.invoke(cli, [
                'role', 'create',
                '-r', 'admin',
                '--description', 'Admin role'
            ])
            
            assert result.exit_code == 1
            assert '✗ Role Already Exists' in result.output
            assert 'already exists' in result.output
    
    def test_create_invalid_data(self, cli_runner, role_command_mocks):
        """Test role creation with invalid data."""
        with role_command_mocks as mocks:
            mocks['api_client'].create_role.side_effect = InvalidRoleDataError(
                "Invalid role data: roleName contains invalid characters"
            )
            
            result = cli_runner.invoke(cli, [
                'role', 'create',
                '-r', 'invalid@role',
                '--description', 'Invalid role'
            ])
            
            assert result.exit_code == 1
            assert '✗ Invalid Role Data' in result.output
    
    def test_create_no_setup(self, cli_runner, role_no_setup_mocks):
        """Test role create without setup."""
        from vamscli.utils.exceptions import SetupRequiredError
        
        with role_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'create',
                '-r', 'admin',
                '--description', 'Admin role'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)
    
    def test_create_json_output(self, cli_runner, role_command_mocks):
        """Test role creation with JSON output."""
        with role_command_mocks as mocks:
            mocks['api_client'].create_role.return_value = {
                'success': True,
                'message': 'Role created',
                'roleName': 'admin',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00'
            }
            
            result = cli_runner.invoke(cli, [
                'role', 'create',
                '-r', 'admin',
                '--description', 'Admin role',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert parsed['success'] is True
            assert parsed['roleName'] == 'admin'


class TestRoleUpdateCommand:
    """Test role update command."""
    
    def test_update_success(self, cli_runner, role_command_mocks):
        """Test successful role update."""
        with role_command_mocks as mocks:
            mocks['api_client'].update_role.return_value = {
                'success': True,
                'message': 'Role admin updated successfully',
                'roleName': 'admin',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00'
            }
            
            result = cli_runner.invoke(cli, [
                'role', 'update',
                '-r', 'admin',
                '--description', 'Updated description'
            ])
            
            assert result.exit_code == 0
            assert '✓ Role updated successfully!' in result.output
            
            # Verify API call
            mocks['api_client'].update_role.assert_called_once()
            call_args = mocks['api_client'].update_role.call_args[0][0]
            assert call_args['roleName'] == 'admin'
            assert call_args['description'] == 'Updated description'
    
    def test_update_mfa_required(self, cli_runner, role_command_mocks):
        """Test enabling MFA requirement."""
        with role_command_mocks as mocks:
            mocks['api_client'].update_role.return_value = {
                'success': True,
                'message': 'Role updated',
                'roleName': 'admin',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00'
            }
            
            result = cli_runner.invoke(cli, [
                'role', 'update',
                '-r', 'admin',
                '--mfa-required'
            ])
            
            assert result.exit_code == 0
            
            # Verify MFA flag was set
            call_args = mocks['api_client'].update_role.call_args[0][0]
            assert call_args['mfaRequired'] is True
    
    def test_update_no_mfa_required(self, cli_runner, role_command_mocks):
        """Test disabling MFA requirement."""
        with role_command_mocks as mocks:
            mocks['api_client'].update_role.return_value = {
                'success': True,
                'message': 'Role updated',
                'roleName': 'admin',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00'
            }
            
            result = cli_runner.invoke(cli, [
                'role', 'update',
                '-r', 'admin',
                '--no-mfa-required'
            ])
            
            assert result.exit_code == 0
            
            # Verify MFA flag was set to False
            call_args = mocks['api_client'].update_role.call_args[0][0]
            assert call_args['mfaRequired'] is False
    
    def test_update_conflicting_mfa_flags(self, cli_runner, role_command_mocks):
        """Test that conflicting MFA flags are rejected."""
        with role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'update',
                '-r', 'admin',
                '--mfa-required',
                '--no-mfa-required'
            ])
            
            assert result.exit_code == 1
            assert 'Cannot use both --mfa-required and --no-mfa-required' in result.output
    
    def test_update_no_fields(self, cli_runner, role_command_mocks):
        """Test update without any fields to update."""
        with role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'update',
                '-r', 'admin'
            ])
            
            assert result.exit_code == 1
            assert 'At least one field must be provided for update' in result.output
    
    def test_update_not_found(self, cli_runner, role_command_mocks):
        """Test updating a non-existent role."""
        with role_command_mocks as mocks:
            mocks['api_client'].update_role.side_effect = RoleNotFoundError(
                "Role not found: Role 'nonexistent' does not exist"
            )
            
            result = cli_runner.invoke(cli, [
                'role', 'update',
                '-r', 'nonexistent',
                '--description', 'New description'
            ])
            
            assert result.exit_code == 1
            assert '✗ Role Not Found' in result.output
    
    def test_update_with_json_input(self, cli_runner, role_command_mocks):
        """Test role update with JSON input."""
        with role_command_mocks as mocks:
            mocks['api_client'].update_role.return_value = {
                'success': True,
                'message': 'Role updated',
                'roleName': 'admin',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00'
            }
            
            json_data = json.dumps({
                'roleName': 'admin',
                'description': 'Updated from JSON',
                'source': 'LDAP'
            })
            
            result = cli_runner.invoke(cli, [
                'role', 'update',
                '-r', 'admin',
                '--json-input', json_data
            ])
            
            assert result.exit_code == 0
            
            # Verify JSON data was used
            call_args = mocks['api_client'].update_role.call_args[0][0]
            assert call_args['description'] == 'Updated from JSON'
            assert call_args['source'] == 'LDAP'
    
    def test_update_no_setup(self, cli_runner, role_no_setup_mocks):
        """Test role update without setup."""
        from vamscli.utils.exceptions import SetupRequiredError
        
        with role_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'update',
                '-r', 'admin',
                '--description', 'Updated'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)


class TestRoleDeleteCommand:
    """Test role delete command."""
    
    @patch('click.confirm')
    def test_delete_success(self, mock_confirm, cli_runner, role_command_mocks):
        """Test successful role deletion."""
        mock_confirm.return_value = True
        
        with role_command_mocks as mocks:
            mocks['api_client'].delete_role.return_value = {
                'message': 'success'
            }
            
            result = cli_runner.invoke(cli, [
                'role', 'delete',
                '-r', 'old-role',
                '--confirm'
            ])
            
            assert result.exit_code == 0
            assert '✓ Role deleted successfully!' in result.output
            
            # Verify API call
            mocks['api_client'].delete_role.assert_called_once_with('old-role')
    
    def test_delete_no_confirm_flag(self, cli_runner, role_command_mocks):
        """Test deletion without confirm flag."""
        with role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'delete',
                '-r', 'admin'
            ])
            
            assert result.exit_code == 1
            assert 'Role deletion requires explicit confirmation' in result.output
            assert 'Use --confirm flag' in result.output
    
    @patch('click.confirm')
    def test_delete_cancelled_at_prompt(self, mock_confirm, cli_runner, role_command_mocks):
        """Test deletion cancelled at confirmation prompt."""
        mock_confirm.return_value = False
        
        with role_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'delete',
                '-r', 'admin',
                '--confirm'
            ])
            
            assert result.exit_code == 0
            assert 'Deletion cancelled' in result.output
            
            # Verify API was not called
            mocks['api_client'].delete_role.assert_not_called()
    
    @patch('click.confirm')
    def test_delete_not_found(self, mock_confirm, cli_runner, role_command_mocks):
        """Test deleting a non-existent role."""
        mock_confirm.return_value = True
        
        with role_command_mocks as mocks:
            mocks['api_client'].delete_role.side_effect = RoleNotFoundError(
                "Role 'nonexistent' not found"
            )
            
            result = cli_runner.invoke(cli, [
                'role', 'delete',
                '-r', 'nonexistent',
                '--confirm'
            ])
            
            assert result.exit_code == 1
            assert '✗ Role Not Found' in result.output
    
    @patch('click.confirm')
    def test_delete_with_dependencies(self, mock_confirm, cli_runner, role_command_mocks):
        """Test deleting a role with dependencies."""
        mock_confirm.return_value = True
        
        with role_command_mocks as mocks:
            mocks['api_client'].delete_role.side_effect = RoleDeletionError(
                "Role deletion failed: Role is assigned to active users"
            )
            
            result = cli_runner.invoke(cli, [
                'role', 'delete',
                '-r', 'admin',
                '--confirm'
            ])
            
            assert result.exit_code == 1
            assert '✗ Role Deletion Error' in result.output
    
    def test_delete_no_setup(self, cli_runner, role_no_setup_mocks):
        """Test role delete without setup."""
        from vamscli.utils.exceptions import SetupRequiredError
        
        with role_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'delete',
                '-r', 'admin',
                '--confirm'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)
    
    @patch('click.confirm')
    def test_delete_json_output(self, mock_confirm, cli_runner, role_command_mocks):
        """Test role deletion with JSON output."""
        mock_confirm.return_value = True
        
        with role_command_mocks as mocks:
            api_response = {
                'message': 'success',
                'roleName': 'old-role'
            }
            mocks['api_client'].delete_role.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'role', 'delete',
                '-r', 'old-role',
                '--confirm',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Should output ONLY pure JSON (no status messages in JSON mode)
            # Parse the entire output as JSON (may be multi-line with indentation)
            parsed = json.loads(result.output)
            assert parsed['message'] == 'success'
            assert parsed['roleName'] == 'old-role'


class TestRoleUtilityFunctions:
    """Test role utility functions."""
    
    def test_parse_json_input_string(self):
        """Test parsing JSON from string."""
        from vamscli.commands.roleUserConstraints import parse_json_input
        
        json_str = '{"roleName": "admin", "description": "Admin role"}'
        result = parse_json_input(json_str)
        
        assert result['roleName'] == 'admin'
        assert result['description'] == 'Admin role'
    
    def test_parse_json_input_empty(self):
        """Test parsing empty JSON input."""
        from vamscli.commands.roleUserConstraints import parse_json_input
        
        result = parse_json_input('')
        assert result == {}
        
        result = parse_json_input(None)
        assert result == {}
    
    def test_format_role_output(self):
        """Test formatting role output for CLI."""
        from vamscli.commands.roleUserConstraints import format_role_output
        
        role_data = {
            'roleName': 'admin',
            'description': 'Administrator role',
            'id': 'role-uuid',
            'createdOn': '2024-01-01T00:00:00',
            'source': 'LDAP',
            'sourceIdentifier': 'cn=admin',
            'mfaRequired': True
        }
        
        result = format_role_output(role_data, json_output=False)
        
        assert 'Role Details:' in result
        assert 'Role Name: admin' in result
        assert 'Description: Administrator role' in result
        assert 'ID: role-uuid' in result
        assert 'MFA Required: True' in result
    
    def test_format_role_output_json(self):
        """Test formatting role output as JSON."""
        from vamscli.commands.roleUserConstraints import format_role_output
        
        role_data = {
            'roleName': 'admin',
            'description': 'Admin role',
            'mfaRequired': False
        }
        
        result = format_role_output(role_data, json_output=True)
        parsed = json.loads(result)
        
        assert parsed['roleName'] == 'admin'
        assert parsed['description'] == 'Admin role'
        assert parsed['mfaRequired'] is False


class TestRoleCommandParameterValidation:
    """Test role command parameter validation."""
    
    @patch('vamscli.main.ProfileManager')
    def test_create_requires_role_name(self, mock_main_profile_manager, cli_runner):
        """Test that create command requires role name."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['role', 'create', '--description', 'Test'])
        
        assert result.exit_code == 2
        assert 'Missing option' in result.output or '--role-name' in result.output
    
    @patch('vamscli.main.ProfileManager')
    def test_update_requires_role_name(self, mock_main_profile_manager, cli_runner):
        """Test that update command requires role name."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['role', 'update', '--description', 'Test'])
        
        assert result.exit_code == 2
        assert 'Missing option' in result.output or '--role-name' in result.output
    
    @patch('vamscli.main.ProfileManager')
    def test_delete_requires_role_name(self, mock_main_profile_manager, cli_runner):
        """Test that delete command requires role name."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['role', 'delete', '--confirm'])
        
        assert result.exit_code == 2
        assert 'Missing option' in result.output or '--role-name' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
