"""Test tag type management commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    TagTypeNotFoundError, TagTypeAlreadyExistsError, TagTypeInUseError,
    InvalidTagTypeDataError, AuthenticationError, APIError, SetupRequiredError
)


# File-level fixtures for tag-type-specific testing patterns
@pytest.fixture
def tag_type_command_mocks(generic_command_mocks):
    """Provide tag-type-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for tag-type command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('tag_type')


@pytest.fixture
def tag_type_no_setup_mocks(no_setup_command_mocks):
    """Provide tag-type command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('tag_type')


class TestTagTypeCreateCommand:
    """Test tag-type create command."""
    
    def test_create_help(self, cli_runner):
        """Test create command help."""
        result = cli_runner.invoke(cli, ['tag-type', 'create', '--help'])
        assert result.exit_code == 0
        assert 'Create a new tag type in VAMS' in result.output
        assert '--tag-type-name' in result.output
        assert '--description' in result.output
        assert '--required' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_create_success(self, cli_runner, tag_type_command_mocks):
        """Test successful tag type creation."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].create_tag_types.return_value = {
                'message': 'Succeeded'
            }
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'priority',
                '--description', 'Priority levels'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) created successfully!' in result.output
            
            # Verify API call
            expected_data = {
                'tagTypes': [{
                    'tagTypeName': 'priority',
                    'description': 'Priority levels',
                    'required': 'False'
                }]
            }
            mocks['api_client'].create_tag_types.assert_called_once_with(expected_data)
    
    def test_create_with_required_flag(self, cli_runner, tag_type_command_mocks):
        """Test tag type creation with required flag."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].create_tag_types.return_value = {
                'message': 'Succeeded'
            }
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'status',
                '--description', 'Processing status',
                '--required'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) created successfully!' in result.output
            
            # Verify API call
            expected_data = {
                'tagTypes': [{
                    'tagTypeName': 'status',
                    'description': 'Processing status',
                    'required': 'True'
                }]
            }
            mocks['api_client'].create_tag_types.assert_called_once_with(expected_data)
    
    def test_create_json_input_string(self, cli_runner, tag_type_command_mocks):
        """Test tag type creation with JSON input string."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].create_tag_types.return_value = {
                'message': 'Succeeded'
            }
            
            json_data = {
                'tagTypes': [
                    {
                        'tagTypeName': 'priority',
                        'description': 'Priority levels',
                        'required': 'True'
                    },
                    {
                        'tagTypeName': 'category',
                        'description': 'Asset categories',
                        'required': 'False'
                    }
                ]
            }
            
            # Need to provide required parameters even with JSON input due to Click validation
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'dummy',  # Required by Click, overridden by JSON
                '--description', 'dummy',    # Required by Click, overridden by JSON
                '--json-input', json.dumps(json_data)
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) created successfully!' in result.output
            
            # Verify API call
            mocks['api_client'].create_tag_types.assert_called_once_with(json_data)
    
    def test_create_json_input_file(self, cli_runner, tag_type_command_mocks):
        """Test tag type creation with JSON input file."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].create_tag_types.return_value = {
                'message': 'Succeeded'
            }
            
            json_data = {
                'tagTypes': [{
                    'tagTypeName': 'priority',
                    'description': 'Priority levels',
                    'required': 'True'
                }]
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                # Need to provide required parameters even with JSON input due to Click validation
                result = cli_runner.invoke(cli, [
                    'tag-type', 'create',
                    '--tag-type-name', 'dummy',  # Required by Click, overridden by JSON
                    '--description', 'dummy',    # Required by Click, overridden by JSON
                    '--json-input', 'tag-types.json'
                ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) created successfully!' in result.output
            
            # Verify API call
            mocks['api_client'].create_tag_types.assert_called_once_with(json_data)
    
    def test_create_json_output(self, cli_runner, tag_type_command_mocks):
        """Test tag type creation with JSON output."""
        with tag_type_command_mocks as mocks:
            api_response = {
                'message': 'Succeeded',
                'tagTypes': ['priority']
            }
            mocks['api_client'].create_tag_types.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'priority',
                '--description', 'Priority levels',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Find the JSON part in the output (after progress message)
            output_lines = result.output.strip().split('\n')
            json_start = -1
            for i, line in enumerate(output_lines):
                if line.strip().startswith('{'):
                    json_start = i
                    break
            
            if json_start >= 0:
                json_output = '\n'.join(output_lines[json_start:])
                output_json = json.loads(json_output)
                assert output_json == api_response
            else:
                # Fallback: check if JSON content is present
                assert '"message": "Succeeded"' in result.output
                assert '"tagTypes": ["priority"]' in result.output
    
    def test_create_missing_parameters(self, cli_runner, tag_type_command_mocks):
        """Test tag type creation with missing required parameters."""
        with tag_type_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'priority'
                # Missing description
            ])
            
            assert result.exit_code == 2  # Click parameter error for missing required option
            assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_create_tag_type_already_exists(self, cli_runner, tag_type_command_mocks):
        """Test tag type creation when tag type already exists."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].create_tag_types.side_effect = TagTypeAlreadyExistsError("Tag type 'priority' already exists")
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'priority',
                '--description', 'Priority levels'
            ])
            
            assert result.exit_code == 1
            assert '✗ Tag Type Already Exists' in result.output
            assert 'vamscli tag-type list' in result.output
    
    def test_create_invalid_tag_type_data(self, cli_runner, tag_type_command_mocks):
        """Test tag type creation with invalid data."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].create_tag_types.side_effect = InvalidTagTypeDataError("Invalid tag type data format")
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'priority',
                '--description', 'Priority levels'
            ])
            
            assert result.exit_code == 1
            assert '✗ Invalid Tag Type Data' in result.output
    
    def test_create_no_setup(self, cli_runner, tag_type_no_setup_mocks):
        """Test create command without setup."""
        with tag_type_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'priority',
                '--description', 'Priority levels'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)
    
    def test_create_authentication_error(self, cli_runner, tag_type_command_mocks):
        """Test tag type creation with authentication error."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].create_tag_types.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'priority',
                '--description', 'Priority levels'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, AuthenticationError)


class TestTagTypeUpdateCommand:
    """Test tag-type update command."""
    
    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(cli, ['tag-type', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Update an existing tag type in VAMS' in result.output
        assert '--tag-type-name' in result.output
        assert '--description' in result.output
        assert '--required' in result.output and '--not-required' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_update_success(self, cli_runner, tag_type_command_mocks):
        """Test successful tag type update."""
        with tag_type_command_mocks as mocks:
            # Mock get_tag_types to return current tag type data
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': [{
                        'tagTypeName': 'priority',
                        'description': 'Old description',
                        'required': 'False'
                    }]
                }
            }
            
            mocks['api_client'].update_tag_types.return_value = {
                'message': 'Succeeded'
            }
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'update',
                '--tag-type-name', 'priority',
                '--description', 'Updated description'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) updated successfully!' in result.output
            
            # Verify API calls
            mocks['api_client'].get_tag_types.assert_called_once()
            expected_data = {
                'tagTypes': [{
                    'tagTypeName': 'priority',
                    'description': 'Updated description',
                    'required': 'False'
                }]
            }
            mocks['api_client'].update_tag_types.assert_called_once_with(expected_data)
    
    def test_update_required_flag(self, cli_runner, tag_type_command_mocks):
        """Test tag type update with required flag."""
        with tag_type_command_mocks as mocks:
            # Mock get_tag_types to return current tag type data
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': [{
                        'tagTypeName': 'priority',
                        'description': 'Priority levels',
                        'required': 'False'
                    }]
                }
            }
            
            mocks['api_client'].update_tag_types.return_value = {
                'message': 'Succeeded'
            }
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'update',
                '--tag-type-name', 'priority',
                '--required'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) updated successfully!' in result.output
            
            # Verify API calls
            expected_data = {
                'tagTypes': [{
                    'tagTypeName': 'priority',
                    'description': 'Priority levels',
                    'required': 'True'
                }]
            }
            mocks['api_client'].update_tag_types.assert_called_once_with(expected_data)
    
    def test_update_not_required_flag(self, cli_runner, tag_type_command_mocks):
        """Test tag type update with not-required flag."""
        with tag_type_command_mocks as mocks:
            # Mock get_tag_types to return current tag type data
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': [{
                        'tagTypeName': 'priority',
                        'description': 'Priority levels',
                        'required': 'True'
                    }]
                }
            }
            
            mocks['api_client'].update_tag_types.return_value = {
                'message': 'Succeeded'
            }
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'update',
                '--tag-type-name', 'priority',
                '--not-required'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) updated successfully!' in result.output
            
            # Verify API calls
            expected_data = {
                'tagTypes': [{
                    'tagTypeName': 'priority',
                    'description': 'Priority levels',
                    'required': 'False'
                }]
            }
            mocks['api_client'].update_tag_types.assert_called_once_with(expected_data)
    
    def test_update_json_input(self, cli_runner, tag_type_command_mocks):
        """Test tag type update with JSON input."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].update_tag_types.return_value = {
                'message': 'Succeeded'
            }
            
            json_data = {
                'tagTypes': [{
                    'tagTypeName': 'priority',
                    'description': 'Updated via JSON',
                    'required': 'True'
                }]
            }
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'update',
                '--tag-type-name', 'priority',
                '--json-input', json.dumps(json_data)
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) updated successfully!' in result.output
            
            # Verify API call
            mocks['api_client'].update_tag_types.assert_called_once_with(json_data)
    
    def test_update_tag_type_not_found(self, cli_runner, tag_type_command_mocks):
        """Test tag type update when tag type doesn't exist."""
        with tag_type_command_mocks as mocks:
            # Mock get_tag_types to return empty list
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': []
                }
            }
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'update',
                '--tag-type-name', 'nonexistent',
                '--description', 'Updated description'
            ])
            
            assert result.exit_code == 1
            assert '✗ Tag Type Not Found' in result.output
            assert 'vamscli tag-type list' in result.output
    
    def test_update_no_fields_provided(self, cli_runner, tag_type_command_mocks):
        """Test tag type update with no fields to update."""
        with tag_type_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag-type', 'update',
                '--tag-type-name', 'priority'
                # No fields to update
            ])
            
            assert result.exit_code == 1  # Custom validation error
            assert 'At least one field must be provided for update' in result.output
    
    def test_update_invalid_tag_type_data(self, cli_runner, tag_type_command_mocks):
        """Test tag type update with invalid data."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': [{
                        'tagTypeName': 'priority',
                        'description': 'Priority levels',
                        'required': 'False'
                    }]
                }
            }
            
            mocks['api_client'].update_tag_types.side_effect = InvalidTagTypeDataError("Invalid tag type data format")
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'update',
                '--tag-type-name', 'priority',
                '--description', 'Updated description'
            ])
            
            assert result.exit_code == 1
            assert '✗ Invalid Tag Type Data' in result.output


class TestTagTypeDeleteCommand:
    """Test tag-type delete command."""
    
    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(cli, ['tag-type', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete a tag type from VAMS' in result.output
        assert 'permanently deletes a tag type' in result.output
        assert '--confirm' in result.output
        assert '--json-output' in result.output
    
    def test_delete_success(self, cli_runner, tag_type_command_mocks):
        """Test successful tag type deletion."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].delete_tag_type.return_value = {
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'delete', 'priority', '--confirm'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type deleted successfully!' in result.output
            assert 'priority' in result.output
            
            # Verify API call
            mocks['api_client'].delete_tag_type.assert_called_once_with('priority')
    
    def test_delete_json_output(self, cli_runner, tag_type_command_mocks):
        """Test tag type deletion with JSON output."""
        with tag_type_command_mocks as mocks:
            api_response = {
                'message': 'Success',
                'deletedTagType': 'priority'
            }
            mocks['api_client'].delete_tag_type.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'delete', 'priority', '--confirm', '--json-output'
            ])
            
            assert result.exit_code == 0
            # Find the JSON part in the output (after progress message)
            output_lines = result.output.strip().split('\n')
            json_start = -1
            for i, line in enumerate(output_lines):
                if line.strip().startswith('{'):
                    json_start = i
                    break
            
            if json_start >= 0:
                json_output = '\n'.join(output_lines[json_start:])
                output_json = json.loads(json_output)
                assert output_json == api_response
            else:
                # Fallback: check if JSON content is present
                assert '"message": "Success"' in result.output
                assert '"deletedTagType": "priority"' in result.output
    
    def test_delete_no_confirm_flag(self, cli_runner, tag_type_command_mocks):
        """Test tag type deletion without confirmation flag."""
        with tag_type_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag-type', 'delete', 'priority'
                # Missing --confirm
            ])
            
            assert result.exit_code == 1
            assert 'Tag type deletion requires explicit confirmation!' in result.output
            assert 'Use --confirm flag' in result.output
            
            # Verify API was not called
            mocks['api_client'].delete_tag_type.assert_not_called()
    
    def test_delete_tag_type_not_found(self, cli_runner, tag_type_command_mocks):
        """Test tag type deletion when tag type doesn't exist."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].delete_tag_type.side_effect = TagTypeNotFoundError("Tag type 'nonexistent' not found")
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'delete', 'nonexistent', '--confirm'
            ])
            
            assert result.exit_code == 1
            assert '✗ Tag Type Not Found' in result.output
            assert 'vamscli tag-type list' in result.output
    
    def test_delete_tag_type_in_use(self, cli_runner, tag_type_command_mocks):
        """Test tag type deletion when tag type is in use."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].delete_tag_type.side_effect = TagTypeInUseError("Tag type 'priority' is in use by existing tags")
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'delete', 'priority', '--confirm'
            ])
            
            assert result.exit_code == 1
            assert '✗ Tag Type In Use' in result.output
            assert 'Delete all tags using this tag type' in result.output
            assert 'vamscli tag list --tag-type' in result.output


class TestTagTypeListCommand:
    """Test tag-type list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['tag-type', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all tag types in VAMS' in result.output
        assert '--show-tags' in result.output
        assert '--json-output' in result.output
    
    def test_list_success(self, cli_runner, tag_type_command_mocks):
        """Test successful tag type listing."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': [
                        {
                            'tagTypeName': 'priority',
                            'description': 'Priority levels',
                            'required': 'True',
                            'tags': ['urgent', 'low']
                        },
                        {
                            'tagTypeName': 'category',
                            'description': 'Asset categories',
                            'required': 'False',
                            'tags': ['model', 'texture']
                        }
                    ]
                }
            }
            
            result = cli_runner.invoke(cli, ['tag-type', 'list'])
            
            assert result.exit_code == 0
            assert 'Found 2 tag type(s):' in result.output
            assert 'priority' in result.output
            assert 'category' in result.output
            assert 'Priority levels' in result.output
            assert 'Asset categories' in result.output
            
            # Verify API call
            mocks['api_client'].get_tag_types.assert_called_once()
    
    def test_list_show_tags(self, cli_runner, tag_type_command_mocks):
        """Test tag type listing with show-tags option."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': [
                        {
                            'tagTypeName': 'priority',
                            'description': 'Priority levels',
                            'required': 'True',
                            'tags': ['urgent', 'low']
                        }
                    ]
                }
            }
            
            result = cli_runner.invoke(cli, ['tag-type', 'list', '--show-tags'])
            
            assert result.exit_code == 0
            assert 'Tag Type Details:' in result.output
            assert 'Associated Tags (2): urgent, low' in result.output
    
    def test_list_json_output(self, cli_runner, tag_type_command_mocks):
        """Test tag type listing with JSON output."""
        with tag_type_command_mocks as mocks:
            expected_response = {
                'message': {
                    'Items': [
                        {
                            'tagTypeName': 'priority',
                            'description': 'Priority levels',
                            'required': 'True',
                            'tags': ['urgent', 'low']
                        }
                    ]
                }
            }
            
            mocks['api_client'].get_tag_types.return_value = expected_response
            
            result = cli_runner.invoke(cli, ['tag-type', 'list', '--json-output'])
            
            assert result.exit_code == 0
            
            # Find the JSON part in the output (after progress message)
            output_lines = result.output.strip().split('\n')
            json_start = -1
            for i, line in enumerate(output_lines):
                if line.strip().startswith('{'):
                    json_start = i
                    break
            
            if json_start >= 0:
                json_output = '\n'.join(output_lines[json_start:])
                output_json = json.loads(json_output)
                assert output_json == expected_response
            else:
                # Fallback: check if JSON content is present
                assert '"tagTypeName": "priority"' in result.output
                assert '"description": "Priority levels"' in result.output
    
    def test_list_empty(self, cli_runner, tag_type_command_mocks):
        """Test tag type listing when no tag types exist."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': []
                }
            }
            
            result = cli_runner.invoke(cli, ['tag-type', 'list'])
            
            assert result.exit_code == 0
            assert 'No tag types found.' in result.output
    
    def test_list_with_pagination(self, cli_runner, tag_type_command_mocks):
        """Test tag type listing with pagination info."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': [
                        {
                            'tagTypeName': 'priority',
                            'description': 'Priority levels',
                            'required': 'True',
                            'tags': []
                        }
                    ],
                    'NextToken': 'next-page-token'
                }
            }
            
            result = cli_runner.invoke(cli, ['tag-type', 'list'])
            
            assert result.exit_code == 0
            assert 'More results available' in result.output


class TestTagTypeCommandsIntegration:
    """Test integration scenarios for tag type commands."""
    
    def test_tag_type_create_update_delete_flow(self, cli_runner, tag_type_command_mocks):
        """Test complete tag type lifecycle."""
        with tag_type_command_mocks as mocks:
            # Test create
            mocks['api_client'].create_tag_types.return_value = {'message': 'Succeeded'}
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'test-type',
                '--description', 'Test tag type'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) created successfully!' in result.output
            
            # Test update
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': [{
                        'tagTypeName': 'test-type',
                        'description': 'Test tag type',
                        'required': 'False'
                    }]
                }
            }
            mocks['api_client'].update_tag_types.return_value = {'message': 'Succeeded'}
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'update',
                '--tag-type-name', 'test-type',
                '--description', 'Updated test tag type'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type(s) updated successfully!' in result.output
            
            # Test delete
            mocks['api_client'].delete_tag_type.return_value = {'message': 'Success'}
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'delete', 'test-type', '--confirm'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag type deleted successfully!' in result.output
    
    def test_authentication_error_handling(self, cli_runner, tag_type_command_mocks):
        """Test authentication error handling across commands."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].get_tag_types.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, ['tag-type', 'list'])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, AuthenticationError)


class TestTagTypeCommandsJSONHandling:
    """Test JSON input/output handling for tag type commands."""
    
    def test_invalid_json_input_string(self, cli_runner, tag_type_command_mocks):
        """Test handling of invalid JSON input string."""
        with tag_type_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'dummy',  # Required by Click
                '--description', 'dummy',    # Required by Click
                '--json-input', 'invalid json string'
            ])
            
            assert result.exit_code == 1  # Custom validation error
            assert 'Invalid JSON input' in result.output
    
    def test_invalid_json_input_file(self, cli_runner, tag_type_command_mocks):
        """Test handling of invalid JSON input file."""
        with tag_type_command_mocks as mocks:
            with patch('builtins.open', mock_open(read_data='invalid json')):
                result = cli_runner.invoke(cli, [
                    'tag-type', 'create',
                    '--tag-type-name', 'dummy',  # Required by Click
                    '--description', 'dummy',    # Required by Click
                    '--json-input', 'invalid.json'
                ])
            
            # Custom validation error for invalid JSON in file
            assert result.exit_code == 1
            assert 'Invalid' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, tag_type_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with tag_type_command_mocks as mocks:
            with patch('builtins.open', side_effect=FileNotFoundError()):
                result = cli_runner.invoke(cli, [
                    'tag-type', 'create',
                    '--tag-type-name', 'dummy',  # Required by Click
                    '--description', 'dummy',    # Required by Click
                    '--json-input', 'nonexistent.json'
                ])
            
            assert result.exit_code == 1  # Custom validation error
            assert 'Invalid JSON input' in result.output


class TestTagTypeCommandsEdgeCases:
    """Test edge cases for tag type commands."""
    
    def test_create_api_error(self, cli_runner, tag_type_command_mocks):
        """Test create command with general API error."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].create_tag_types.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'create',
                '--tag-type-name', 'priority',
                '--description', 'Priority levels'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, APIError)
    
    def test_update_api_error(self, cli_runner, tag_type_command_mocks):
        """Test update command with general API error."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].get_tag_types.return_value = {
                'message': {
                    'Items': [{
                        'tagTypeName': 'priority',
                        'description': 'Priority levels',
                        'required': 'False'
                    }]
                }
            }
            
            mocks['api_client'].update_tag_types.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'update',
                '--tag-type-name', 'priority',
                '--description', 'Updated description'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, APIError)
    
    def test_delete_api_error(self, cli_runner, tag_type_command_mocks):
        """Test delete command with general API error."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].delete_tag_type.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'tag-type', 'delete', 'priority', '--confirm'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, APIError)
    
    def test_list_api_error(self, cli_runner, tag_type_command_mocks):
        """Test list command with general API error."""
        with tag_type_command_mocks as mocks:
            mocks['api_client'].get_tag_types.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, ['tag-type', 'list'])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, APIError)


class TestTagTypeCommandsParameterValidation:
    """Test parameter validation for tag type commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_tag_type_name(self, mock_main_profile_manager):
        """Test that commands require tag-type-name where appropriate."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test create without tag-type-name
        result = runner.invoke(cli, ['tag-type', 'create'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test update without tag-type-name
        result = runner.invoke(cli, ['tag-type', 'update'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    @patch('vamscli.main.ProfileManager')
    def test_delete_requires_tag_type_name_argument(self, mock_main_profile_manager):
        """Test that delete command requires tag type name as argument."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test delete without tag type name argument
        result = runner.invoke(cli, ['tag-type', 'delete'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'required' in result.output.lower()


class TestTagTypeUtilityFunctions:
    """Test tag type utility functions."""
    
    def test_parse_json_input_string(self):
        """Test parsing JSON input from string."""
        from vamscli.commands.tag_type import parse_json_input
        
        json_string = '{"tagTypes": [{"tagTypeName": "test", "description": "Test"}]}'
        result = parse_json_input(json_string)
        
        expected = {"tagTypes": [{"tagTypeName": "test", "description": "Test"}]}
        assert result == expected
    
    def test_parse_json_input_file(self):
        """Test parsing JSON input from file."""
        from vamscli.commands.tag_type import parse_json_input
        
        json_data = {"tagTypes": [{"tagTypeName": "test", "description": "Test"}]}
        
        with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
            result = parse_json_input('test.json')
        
        assert result == json_data
    
    def test_parse_json_input_invalid_string(self):
        """Test parsing invalid JSON string."""
        from vamscli.commands.tag_type import parse_json_input
        
        with pytest.raises(click.BadParameter) as exc_info:
            parse_json_input('invalid json')
        
        assert 'Invalid JSON input' in str(exc_info.value)
    
    def test_parse_json_input_nonexistent_file(self):
        """Test parsing nonexistent JSON file."""
        from vamscli.commands.tag_type import parse_json_input
        
        with patch('builtins.open', side_effect=FileNotFoundError()):
            with pytest.raises(click.BadParameter) as exc_info:
                parse_json_input('nonexistent.json')
        
        assert 'Invalid JSON input' in str(exc_info.value)
    
    def test_format_tag_type_output_cli(self):
        """Test formatting tag type output for CLI."""
        from vamscli.commands.tag_type import format_tag_type_output
        
        tag_type_data = {
            'tagTypeName': 'priority',
            'description': 'Priority levels',
            'required': 'True',
            'tags': ['urgent', 'low']
        }
        
        result = format_tag_type_output(tag_type_data, json_output=False)
        
        assert 'Tag Type Details:' in result
        assert 'Name: priority' in result
        assert 'Description: Priority levels' in result
        assert 'Required: Yes' in result
        assert 'Associated Tags (2): urgent, low' in result
    
    def test_format_tag_type_output_json(self):
        """Test formatting tag type output as JSON."""
        from vamscli.commands.tag_type import format_tag_type_output
        
        tag_type_data = {
            'tagTypeName': 'priority',
            'description': 'Priority levels',
            'required': 'True',
            'tags': ['urgent', 'low']
        }
        
        result = format_tag_type_output(tag_type_data, json_output=True)
        
        # Should be valid JSON
        parsed = json.loads(result)
        assert parsed == tag_type_data
    
    def test_format_tag_types_list_output_cli(self):
        """Test formatting tag types list for CLI."""
        from vamscli.commands.tag_type import format_tag_types_list_output
        
        tag_types_data = [
            {
                'tagTypeName': 'priority',
                'description': 'Priority levels',
                'required': 'True',
                'tags': ['urgent', 'low']
            },
            {
                'tagTypeName': 'category',
                'description': 'Asset categories',
                'required': 'False',
                'tags': []
            }
        ]
        
        result = format_tag_types_list_output(tag_types_data, json_output=False)
        
        assert 'Found 2 tag type(s):' in result
        assert 'priority' in result
        assert 'category' in result
        assert 'Priority levels' in result
        assert 'Asset categories' in result
    
    def test_format_tag_types_list_output_empty(self):
        """Test formatting empty tag types list."""
        from vamscli.commands.tag_type import format_tag_types_list_output
        
        result = format_tag_types_list_output([], json_output=False)
        
        assert result == "No tag types found."
    
    def test_format_tag_types_list_output_json(self):
        """Test formatting tag types list as JSON."""
        from vamscli.commands.tag_type import format_tag_types_list_output
        
        tag_types_data = [
            {
                'tagTypeName': 'priority',
                'description': 'Priority levels',
                'required': 'True',
                'tags': ['urgent', 'low']
            }
        ]
        
        result = format_tag_types_list_output(tag_types_data, json_output=True)
        
        # Should be valid JSON
        parsed = json.loads(result)
        assert parsed == tag_types_data


if __name__ == '__main__':
    pytest.main([__file__])
