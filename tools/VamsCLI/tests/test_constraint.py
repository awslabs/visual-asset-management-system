"""Test constraint management functionality."""

import json
import pytest
import click
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    ConstraintNotFoundError, ConstraintAlreadyExistsError, ConstraintDeletionError,
    InvalidConstraintDataError, SetupRequiredError
)


# File-level fixtures for constraint command testing patterns
@pytest.fixture
def constraint_command_mocks(generic_command_mocks):
    """Provide constraint-specific command mocks.

    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for constraint command testing.

    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('roleUserConstraints')


@pytest.fixture
def constraint_no_setup_mocks(no_setup_command_mocks):
    """Provide constraint command mocks for no-setup scenarios.

    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('roleUserConstraints')


class TestConstraintListCommand:
    """Test role constraint list command."""

    def test_list_help(self, cli_runner):
        """Test constraint list command help."""
        result = cli_runner.invoke(cli, ['role', 'constraint', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all constraints' in result.output
        assert '--page-size' in result.output
        assert '--auto-paginate' in result.output
        assert '--json-output' in result.output

    def test_list_success(self, cli_runner, constraint_command_mocks):
        """Test successful constraint listing."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].list_constraints.return_value = {
                'Items': [
                    {
                        'constraintId': 'constraint1',
                        'name': 'Test Constraint',
                        'description': 'Test description',
                        'objectType': 'asset',
                        'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': 'db1'}],
                        'groupPermissions': [{'groupId': 'admin', 'permission': 'read', 'permissionType': 'allow'}]
                    }
                ]
            }

            result = cli_runner.invoke(cli, ['role', 'constraint', 'list'])

            assert result.exit_code == 0
            assert '✓' in result.output or 'constraint1' in result.output
            assert 'Test Constraint' in result.output

            # Verify API call
            mocks['api_client'].list_constraints.assert_called_once()

    def test_list_no_setup(self, cli_runner, constraint_no_setup_mocks):
        """Test constraint list without setup."""
        with constraint_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['role', 'constraint', 'list'])

            assert result.exit_code == 1
            assert result.exception is not None
            assert isinstance(result.exception, SetupRequiredError)

    def test_list_json_output(self, cli_runner, constraint_command_mocks):
        """Test constraint list with JSON output."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].list_constraints.return_value = {
                'Items': [
                    {
                        'constraintId': 'constraint1',
                        'name': 'Test Constraint',
                        'description': 'Test description',
                        'objectType': 'asset'
                    }
                ]
            }

            result = cli_runner.invoke(cli, ['role', 'constraint', 'list', '--json-output'])

            assert result.exit_code == 0

            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert 'Items' in parsed
            assert len(parsed['Items']) == 1
            assert parsed['Items'][0]['constraintId'] == 'constraint1'

    def test_list_auto_paginate(self, cli_runner, constraint_command_mocks):
        """Test constraint list with auto-pagination."""
        with constraint_command_mocks as mocks:
            # Mock paginated responses
            mocks['api_client'].list_constraints.side_effect = [
                {
                    'Items': [{'constraintId': f'constraint{i}'} for i in range(1, 6)],
                    'NextToken': 'token1'
                },
                {
                    'Items': [{'constraintId': f'constraint{i}'} for i in range(6, 11)],
                    'NextToken': None
                }
            ]

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'list',
                '--auto-paginate',
                '--page-size', '5'
            ])

            assert result.exit_code == 0
            assert 'Auto-paginated' in result.output or 'constraint1' in result.output

            # Verify multiple API calls were made
            assert mocks['api_client'].list_constraints.call_count == 2

    def test_list_manual_pagination(self, cli_runner, constraint_command_mocks):
        """Test constraint list with manual pagination."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].list_constraints.return_value = {
                'Items': [{'constraintId': 'constraint1'}],
                'NextToken': 'next_token_value'
            }

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'list',
                '--page-size', '10',
                '--starting-token', 'token123'
            ])

            assert result.exit_code == 0

            # Verify API call with pagination params
            call_args = mocks['api_client'].list_constraints.call_args
            assert call_args[0][0]['pageSize'] == 10
            assert call_args[0][0]['startingToken'] == 'token123'

    def test_list_pagination_conflict(self, cli_runner, constraint_command_mocks):
        """Test that auto-paginate and starting-token cannot be used together."""
        with constraint_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'list',
                '--auto-paginate',
                '--starting-token', 'token123'
            ])

            assert result.exit_code == 1
            assert 'Cannot use --auto-paginate with --starting-token' in result.output


class TestConstraintGetCommand:
    """Test role constraint get command."""

    def test_get_help(self, cli_runner):
        """Test constraint get command help."""
        result = cli_runner.invoke(cli, ['role', 'constraint', 'get', '--help'])
        assert result.exit_code == 0
        assert 'Get details for a specific constraint' in result.output
        assert '--constraint-id' in result.output

    def test_get_success(self, cli_runner, constraint_command_mocks):
        """Test successful constraint retrieval."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].get_constraint.return_value = {
                'constraintId': 'test-constraint',
                'name': 'Test Constraint',
                'description': 'Test description',
                'objectType': 'asset',
                'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': 'db1'}],
                'groupPermissions': [{'groupId': 'admin', 'permission': 'read', 'permissionType': 'allow'}]
            }

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'get',
                '-c', 'test-constraint'
            ])

            assert result.exit_code == 0
            assert 'test-constraint' in result.output
            assert 'Test Constraint' in result.output

            # Verify API call
            mocks['api_client'].get_constraint.assert_called_once_with('test-constraint')

    def test_get_not_found(self, cli_runner, constraint_command_mocks):
        """Test constraint get with non-existent constraint."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].get_constraint.side_effect = ConstraintNotFoundError("Constraint 'missing' not found")

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'get',
                '-c', 'missing'
            ])

            assert result.exit_code == 1
            assert '✗ Constraint Not Found' in result.output
            assert "Constraint 'missing' not found" in result.output

    def test_get_no_setup(self, cli_runner, constraint_no_setup_mocks):
        """Test constraint get without setup."""
        with constraint_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'get',
                '-c', 'test-constraint'
            ])

            assert result.exit_code == 1
            assert result.exception is not None
            assert isinstance(result.exception, SetupRequiredError)

    def test_get_json_output(self, cli_runner, constraint_command_mocks):
        """Test constraint get with JSON output."""
        with constraint_command_mocks as mocks:
            constraint_data = {
                'constraintId': 'test-constraint',
                'name': 'Test Constraint',
                'description': 'Test description',
                'objectType': 'asset'
            }
            mocks['api_client'].get_constraint.return_value = constraint_data

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'get',
                '-c', 'test-constraint',
                '--json-output'
            ])

            assert result.exit_code == 0

            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert parsed['constraintId'] == 'test-constraint'
            assert parsed['name'] == 'Test Constraint'


class TestConstraintCreateCommand:
    """Test role constraint create command."""

    def test_create_help(self, cli_runner):
        """Test constraint create command help."""
        result = cli_runner.invoke(cli, ['role', 'constraint', 'create', '--help'])
        assert result.exit_code == 0
        assert 'Create a new constraint' in result.output
        assert '--constraint-id' in result.output
        assert '--json-input' in result.output

    def test_create_success_json_input(self, cli_runner, constraint_command_mocks):
        """Test successful constraint creation with JSON input."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].create_constraint.return_value = {
                'success': True,
                'message': 'Constraint created successfully',
                'constraintId': 'test-constraint',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            constraint_json = json.dumps({
                'name': 'Test Constraint',
                'description': 'Test description',
                'objectType': 'asset',
                'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': 'db1'}],
                'groupPermissions': [{'groupId': 'admin', 'permission': 'read', 'permissionType': 'allow'}]
            })

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'create',
                '-c', 'test-constraint',
                '--json-input', constraint_json
            ])

            assert result.exit_code == 0
            assert '✓ Constraint created successfully!' in result.output

            # Verify API call
            mocks['api_client'].create_constraint.assert_called_once()
            call_args = mocks['api_client'].create_constraint.call_args[0][0]
            assert call_args['identifier'] == 'test-constraint'
            assert call_args['name'] == 'Test Constraint'

    def test_create_success_cli_options(self, cli_runner, constraint_command_mocks):
        """Test successful constraint creation with CLI options."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].create_constraint.return_value = {
                'success': True,
                'message': 'Constraint created successfully',
                'constraintId': 'test-constraint',
                'operation': 'create'
            }

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'create',
                '-c', 'test-constraint',
                '--name', 'Test Constraint',
                '--description', 'Test description',
                '--object-type', 'asset'
            ])

            assert result.exit_code == 0
            assert '✓ Constraint created successfully!' in result.output

            # Verify API call
            mocks['api_client'].create_constraint.assert_called_once()
            call_args = mocks['api_client'].create_constraint.call_args[0][0]
            assert call_args['identifier'] == 'test-constraint'
            assert call_args['name'] == 'Test Constraint'
            assert call_args['objectType'] == 'asset'

    def test_create_missing_required_fields(self, cli_runner, constraint_command_mocks):
        """Test constraint create with missing required fields."""
        with constraint_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'create',
                '-c', 'test-constraint',
                '--name', 'Test Constraint'
                # Missing description and object-type
            ])

            assert result.exit_code == 1
            assert 'required' in result.output.lower()

    def test_create_already_exists(self, cli_runner, constraint_command_mocks):
        """Test constraint create when constraint already exists."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].create_constraint.side_effect = ConstraintAlreadyExistsError(
                "Constraint already exists"
            )

            constraint_json = json.dumps({
                'name': 'Test',
                'description': 'Test',
                'objectType': 'asset',
                'criteriaAnd': []
            })

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'create',
                '-c', 'existing-constraint',
                '--json-input', constraint_json
            ])

            assert result.exit_code == 1
            assert '✗ Constraint Already Exists' in result.output

    def test_create_invalid_data(self, cli_runner, constraint_command_mocks):
        """Test constraint create with invalid data."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].create_constraint.side_effect = InvalidConstraintDataError(
                "Invalid constraint data: objectType must be one of: asset, file"
            )

            constraint_json = json.dumps({
                'name': 'Test',
                'description': 'Test',
                'objectType': 'invalid',
                'criteriaAnd': []
            })

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'create',
                '-c', 'test-constraint',
                '--json-input', constraint_json
            ])

            assert result.exit_code == 1
            assert '✗ Invalid Constraint Data' in result.output

    def test_create_no_setup(self, cli_runner, constraint_no_setup_mocks):
        """Test constraint create without setup."""
        with constraint_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'create',
                '-c', 'test-constraint',
                '--name', 'Test',
                '--description', 'Test',
                '--object-type', 'asset'
            ])

            assert result.exit_code == 1
            assert result.exception is not None
            assert isinstance(result.exception, SetupRequiredError)

    def test_create_json_output(self, cli_runner, constraint_command_mocks):
        """Test constraint create with JSON output."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].create_constraint.return_value = {
                'success': True,
                'message': 'Constraint created',
                'constraintId': 'test-constraint',
                'operation': 'create'
            }

            constraint_json = json.dumps({
                'name': 'Test',
                'description': 'Test',
                'objectType': 'asset',
                'criteriaAnd': []
            })

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'create',
                '-c', 'test-constraint',
                '--json-input', constraint_json,
                '--json-output'
            ])

            assert result.exit_code == 0

            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert parsed['success'] == True
            assert parsed['constraintId'] == 'test-constraint'


class TestConstraintUpdateCommand:
    """Test role constraint update command."""

    def test_update_help(self, cli_runner):
        """Test constraint update command help."""
        result = cli_runner.invoke(cli, ['role', 'constraint', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Update an existing constraint' in result.output
        assert '--constraint-id' in result.output
        assert '--json-input' in result.output

    def test_update_success_json_input(self, cli_runner, constraint_command_mocks):
        """Test successful constraint update with JSON input."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].update_constraint.return_value = {
                'success': True,
                'message': 'Constraint updated successfully',
                'constraintId': 'test-constraint',
                'operation': 'update',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            constraint_json = json.dumps({
                'name': 'Updated Constraint',
                'description': 'Updated description',
                'objectType': 'asset',
                'criteriaAnd': [{'field': 'databaseId', 'operator': 'equals', 'value': 'db2'}]
            })

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'update',
                '-c', 'test-constraint',
                '--json-input', constraint_json
            ])

            assert result.exit_code == 0
            assert '✓ Constraint updated successfully!' in result.output

            # Verify API call
            mocks['api_client'].update_constraint.assert_called_once()

    def test_update_success_cli_options(self, cli_runner, constraint_command_mocks):
        """Test successful constraint update with CLI options."""
        with constraint_command_mocks as mocks:
            # Mock get_constraint for retrieving existing data
            mocks['api_client'].get_constraint.return_value = {
                'constraintId': 'test-constraint',
                'name': 'Old Name',
                'description': 'Old description',
                'objectType': 'asset',
                'criteriaAnd': []
            }
            
            mocks['api_client'].update_constraint.return_value = {
                'success': True,
                'message': 'Constraint updated',
                'constraintId': 'test-constraint',
                'operation': 'update'
            }

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'update',
                '-c', 'test-constraint',
                '--name', 'New Name',
                '--description', 'New description'
            ])

            assert result.exit_code == 0
            assert '✓ Constraint updated successfully!' in result.output

            # Verify API calls
            mocks['api_client'].get_constraint.assert_called_once_with('test-constraint')
            mocks['api_client'].update_constraint.assert_called_once()

    def test_update_not_found(self, cli_runner, constraint_command_mocks):
        """Test constraint update with non-existent constraint."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].get_constraint.side_effect = ConstraintNotFoundError(
                "Constraint 'missing' not found"
            )

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'update',
                '-c', 'missing',
                '--name', 'New Name'
            ])

            assert result.exit_code == 1
            assert '✗ Constraint Not Found' in result.output

    def test_update_no_fields(self, cli_runner, constraint_command_mocks):
        """Test constraint update with no fields to update."""
        with constraint_command_mocks as mocks:
            # Mock get_constraint to return existing data
            mocks['api_client'].get_constraint.return_value = {
                'constraintId': 'test-constraint',
                'name': 'Test',
                'description': 'Test',
                'objectType': 'asset',
                'criteriaAnd': []
            }
            
            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'update',
                '-c', 'test-constraint'
                # No update fields provided
            ])

            assert result.exit_code == 1
            assert 'At least one field must be provided' in result.output

    def test_update_no_setup(self, cli_runner, constraint_no_setup_mocks):
        """Test constraint update without setup."""
        with constraint_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'update',
                '-c', 'test-constraint',
                '--name', 'New Name'
            ])

            assert result.exit_code == 1
            assert result.exception is not None
            assert isinstance(result.exception, SetupRequiredError)

    def test_update_json_output(self, cli_runner, constraint_command_mocks):
        """Test constraint update with JSON output."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].update_constraint.return_value = {
                'success': True,
                'message': 'Constraint updated',
                'constraintId': 'test-constraint',
                'operation': 'update'
            }

            constraint_json = json.dumps({
                'name': 'Updated',
                'description': 'Updated',
                'objectType': 'asset',
                'criteriaAnd': []
            })

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'update',
                '-c', 'test-constraint',
                '--json-input', constraint_json,
                '--json-output'
            ])

            assert result.exit_code == 0

            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert parsed['success'] == True
            assert parsed['constraintId'] == 'test-constraint'


class TestConstraintDeleteCommand:
    """Test role constraint delete command."""

    def test_delete_help(self, cli_runner):
        """Test constraint delete command help."""
        result = cli_runner.invoke(cli, ['role', 'constraint', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete a constraint' in result.output
        assert '--constraint-id' in result.output
        assert '--confirm' in result.output

    def test_delete_success(self, cli_runner, constraint_command_mocks):
        """Test successful constraint deletion."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].delete_constraint.return_value = {
                'success': True,
                'message': 'Constraint deleted successfully',
                'constraintId': 'test-constraint',
                'operation': 'delete',
                'timestamp': '2024-01-01T00:00:00Z'
            }

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'delete',
                '-c', 'test-constraint',
                '--confirm'
            ], input='y\n')

            assert result.exit_code == 0
            assert '✓ Constraint deleted successfully!' in result.output

            # Verify API call
            mocks['api_client'].delete_constraint.assert_called_once_with('test-constraint')

    def test_delete_no_confirm_flag(self, cli_runner, constraint_command_mocks):
        """Test constraint delete without confirm flag."""
        with constraint_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'delete',
                '-c', 'test-constraint'
            ])

            assert result.exit_code == 1
            assert 'Confirmation required' in result.output
            assert 'Use --confirm flag' in result.output

            # Verify API was not called
            mocks['api_client'].delete_constraint.assert_not_called()

    def test_delete_cancelled_prompt(self, cli_runner, constraint_command_mocks):
        """Test constraint delete cancelled at confirmation prompt."""
        with constraint_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'delete',
                '-c', 'test-constraint',
                '--confirm'
            ], input='n\n')

            assert result.exit_code == 0
            assert 'Deletion cancelled' in result.output

            # Verify API was not called
            mocks['api_client'].delete_constraint.assert_not_called()

    def test_delete_not_found(self, cli_runner, constraint_command_mocks):
        """Test constraint delete with non-existent constraint."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].delete_constraint.side_effect = ConstraintNotFoundError(
                "Constraint 'missing' not found"
            )

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'delete',
                '-c', 'missing',
                '--confirm'
            ], input='y\n')

            assert result.exit_code == 1
            assert '✗ Constraint Not Found' in result.output

    def test_delete_no_setup(self, cli_runner, constraint_no_setup_mocks):
        """Test constraint delete without setup."""
        with constraint_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'delete',
                '-c', 'test-constraint',
                '--confirm'
            ])

            assert result.exit_code == 1
            assert result.exception is not None
            assert isinstance(result.exception, SetupRequiredError)

    def test_delete_json_output(self, cli_runner, constraint_command_mocks):
        """Test constraint delete with JSON output."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].delete_constraint.return_value = {
                'success': True,
                'message': 'Constraint deleted',
                'constraintId': 'test-constraint',
                'operation': 'delete'
            }

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'delete',
                '-c', 'test-constraint',
                '--confirm',
                '--json-output'
            ])

            assert result.exit_code == 0

            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert parsed['success'] == True
            assert parsed['constraintId'] == 'test-constraint'


class TestConstraintUtilityFunctions:
    """Test constraint utility functions."""

    def test_format_constraint_output(self):
        """Test constraint output formatting."""
        from vamscli.commands.roleUserConstraints import format_constraint_output

        constraint_data = {
            'constraintId': 'test-constraint',
            'name': 'Test Constraint',
            'description': 'Test description',
            'objectType': 'asset',
            'criteriaAnd': [
                {'field': 'databaseId', 'operator': 'equals', 'value': 'db1'}
            ],
            'groupPermissions': [
                {'groupId': 'admin', 'permission': 'read', 'permissionType': 'allow'}
            ]
        }

        result = format_constraint_output(constraint_data, json_output=False)
        
        assert 'Constraint Details:' in result
        assert 'test-constraint' in result
        assert 'Test Constraint' in result
        assert 'Criteria AND' in result
        assert 'Group Permissions' in result

    def test_format_constraint_output_json(self):
        """Test constraint output formatting with JSON."""
        from vamscli.commands.roleUserConstraints import format_constraint_output

        constraint_data = {
            'constraintId': 'test-constraint',
            'name': 'Test Constraint'
        }

        result = format_constraint_output(constraint_data, json_output=True)
        
        # Should be valid JSON
        parsed = json.loads(result)
        assert parsed['constraintId'] == 'test-constraint'


class TestConstraintCommandIntegration:
    """Test constraint command integration scenarios."""

    def test_constraint_commands_require_parameters(self, cli_runner):
        """Test that constraint commands require appropriate parameters."""
        # Test create without constraint-id
        result = cli_runner.invoke(cli, ['role', 'constraint', 'create'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()

        # Test get without constraint-id
        result = cli_runner.invoke(cli, ['role', 'constraint', 'get'])
        assert result.exit_code == 2
        assert 'Missing option' in result.output or 'required' in result.output.lower()

        # Test update without constraint-id
        result = cli_runner.invoke(cli, ['role', 'constraint', 'update'])
        assert result.exit_code == 2
        assert 'Missing option' in result.output or 'required' in result.output.lower()

        # Test delete without constraint-id
        result = cli_runner.invoke(cli, ['role', 'constraint', 'delete'])
        assert result.exit_code == 2
        assert 'Missing option' in result.output or 'required' in result.output.lower()

    def test_constraint_list_empty_result(self, cli_runner, constraint_command_mocks):
        """Test constraint list with no constraints."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].list_constraints.return_value = {
                'Items': []
            }

            result = cli_runner.invoke(cli, ['role', 'constraint', 'list'])

            assert result.exit_code == 0
            assert 'No constraints found' in result.output

    def test_constraint_complex_json_structure(self, cli_runner, constraint_command_mocks):
        """Test constraint with complex JSON structure."""
        with constraint_command_mocks as mocks:
            mocks['api_client'].create_constraint.return_value = {
                'success': True,
                'message': 'Constraint created',
                'constraintId': 'complex-constraint',
                'operation': 'create'
            }

            complex_constraint = {
                'name': 'Complex Constraint',
                'description': 'Complex test',
                'objectType': 'asset',
                'criteriaAnd': [
                    {'field': 'databaseId', 'operator': 'equals', 'value': 'db1'},
                    {'field': 'assetType', 'operator': 'contains', 'value': 'model'}
                ],
                'criteriaOr': [
                    {'field': 'tags', 'operator': 'in', 'value': ['tag1', 'tag2']}
                ],
                'groupPermissions': [
                    {'groupId': 'admin', 'permission': 'read', 'permissionType': 'allow'},
                    {'groupId': 'viewer', 'permission': 'read', 'permissionType': 'allow'}
                ],
                'userPermissions': [
                    {'userId': 'user1@example.com', 'permission': 'write', 'permissionType': 'allow'}
                ]
            }

            result = cli_runner.invoke(cli, [
                'role', 'constraint', 'create',
                '-c', 'complex-constraint',
                '--json-input', json.dumps(complex_constraint)
            ])

            assert result.exit_code == 0
            assert '✓ Constraint created successfully!' in result.output

            # Verify API call with complex structure
            mocks['api_client'].create_constraint.assert_called_once()
            call_args = mocks['api_client'].create_constraint.call_args[0][0]
            assert len(call_args['criteriaAnd']) == 2
            assert len(call_args['criteriaOr']) == 1
            assert len(call_args['groupPermissions']) == 2
            assert len(call_args['userPermissions']) == 1


if __name__ == '__main__':
    pytest.main([__file__])
