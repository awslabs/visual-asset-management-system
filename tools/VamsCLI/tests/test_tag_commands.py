"""Test tag management commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    TagNotFoundError, TagAlreadyExistsError, TagTypeNotFoundError,
    InvalidTagDataError, AuthenticationError, APIError
)


# File-level fixtures for tag-specific testing patterns
@pytest.fixture
def tag_command_mocks(generic_command_mocks):
    """Provide tag-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for tag command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('tag')


@pytest.fixture
def tag_no_setup_mocks(no_setup_command_mocks):
    """Provide tag command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('tag')


class TestTagHelpCommands:
    """Test tag command help functionality."""
    
    def test_tag_help(self, cli_runner):
        """Test tag command help."""
        result = cli_runner.invoke(cli, ['tag', '--help'])
        assert result.exit_code == 0
        assert 'Tag management commands' in result.output
    
    def test_tag_create_help(self, cli_runner):
        """Test tag create help."""
        result = cli_runner.invoke(cli, ['tag', 'create', '--help'])
        assert result.exit_code == 0
        assert 'Create a new tag in VAMS' in result.output
        assert '--tag-name' in result.output
        assert '--description' in result.output
        assert '--tag-type-name' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_tag_update_help(self, cli_runner):
        """Test tag update help."""
        result = cli_runner.invoke(cli, ['tag', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Update an existing tag in VAMS' in result.output
        assert '--tag-name' in result.output
        assert '--description' in result.output
        assert '--tag-type-name' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_tag_delete_help(self, cli_runner):
        """Test tag delete help."""
        result = cli_runner.invoke(cli, ['tag', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete a tag from VAMS' in result.output
        assert '--confirm' in result.output
        assert '--json-output' in result.output
    
    def test_tag_list_help(self, cli_runner):
        """Test tag list help."""
        result = cli_runner.invoke(cli, ['tag', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all tags in VAMS' in result.output
        assert '--tag-type' in result.output
        assert '--json-output' in result.output


class TestTagCreateCommand:
    """Test tag create command."""
    
    def test_create_success(self, cli_runner, tag_command_mocks):
        """Test successful tag creation."""
        with tag_command_mocks as mocks:
            mocks['api_client'].create_tags.return_value = {
                'message': 'Succeeded'
            }
            
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'urgent',
                '--description', 'Urgent priority',
                '--tag-type-name', 'priority'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag(s) created successfully!' in result.output
            
            # Verify API call
            expected_data = {
                'tags': [{
                    'tagName': 'urgent',
                    'description': 'Urgent priority',
                    'tagTypeName': 'priority'
                }]
            }
            mocks['api_client'].create_tags.assert_called_once_with(expected_data)
    
    def test_create_json_input_string(self, cli_runner, tag_command_mocks):
        """Test tag creation with JSON input string."""
        with tag_command_mocks as mocks:
            mocks['api_client'].create_tags.return_value = {
                'message': 'Succeeded'
            }
            
            json_data = {
                'tags': [
                    {
                        'tagName': 'urgent',
                        'description': 'Urgent priority',
                        'tagTypeName': 'priority'
                    },
                    {
                        'tagName': 'low',
                        'description': 'Low priority',
                        'tagTypeName': 'priority'
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--json-input', json.dumps(json_data)
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag(s) created successfully!' in result.output
            
            # Verify API call
            mocks['api_client'].create_tags.assert_called_once_with(json_data)
    
    def test_create_json_input_file(self, cli_runner, tag_command_mocks):
        """Test tag creation with JSON input file."""
        with tag_command_mocks as mocks:
            mocks['api_client'].create_tags.return_value = {
                'message': 'Succeeded'
            }
            
            json_data = {
                'tags': [{
                    'tagName': 'test-tag',
                    'description': 'Test tag from file',
                    'tagTypeName': 'test-type'
                }]
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'tag', 'create',
                    '--json-input', 'tags.json'
                ])
            
            assert result.exit_code == 0
            assert '✓ Tag(s) created successfully!' in result.output
            
            # Verify API call
            mocks['api_client'].create_tags.assert_called_once_with(json_data)
    
    def test_create_json_output(self, cli_runner, tag_command_mocks):
        """Test tag creation with JSON output."""
        with tag_command_mocks as mocks:
            api_response = {
                'message': 'Succeeded',
                'tagId': 'tag-123'
            }
            mocks['api_client'].create_tags.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'urgent',
                '--description', 'Urgent priority',
                '--tag-type-name', 'priority',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should output progress message followed by JSON
            assert 'Creating tag(s)...' in result.output
            # Extract JSON from output (skip the progress message)
            lines = result.output.strip().split('\n')
            json_lines = lines[1:]  # Skip first line (progress message)
            json_output = '\n'.join(json_lines)
            output_json = json.loads(json_output)
            assert output_json == api_response
    
    def test_create_missing_params(self, cli_runner, tag_command_mocks):
        """Test tag creation with missing parameters."""
        with tag_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'urgent'
                # Missing description and tag-type-name
            ])
            
            assert result.exit_code == 1
            assert 'required when not using --json-input' in result.output
    
    def test_create_tag_already_exists(self, cli_runner, tag_command_mocks):
        """Test tag creation when tag already exists."""
        with tag_command_mocks as mocks:
            mocks['api_client'].create_tags.side_effect = TagAlreadyExistsError("Tag 'urgent' already exists")
            
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'urgent',
                '--description', 'Urgent priority',
                '--tag-type-name', 'priority'
            ])
            
            assert result.exit_code == 1
            assert '✗ Tag Already Exists' in result.output
            assert 'vamscli tag list' in result.output
    
    def test_create_tag_type_not_found(self, cli_runner, tag_command_mocks):
        """Test tag creation when tag type doesn't exist."""
        with tag_command_mocks as mocks:
            mocks['api_client'].create_tags.side_effect = TagTypeNotFoundError("Tag type 'nonexistent' not found")
            
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'urgent',
                '--description', 'Urgent priority',
                '--tag-type-name', 'nonexistent'
            ])
            
            assert result.exit_code == 1
            assert '✗ Tag Type Not Found' in result.output
            assert 'vamscli tag-type list' in result.output
            assert 'Create the tag type first' in result.output
    
    def test_create_invalid_tag_data(self, cli_runner, tag_command_mocks):
        """Test tag creation with invalid tag data."""
        with tag_command_mocks as mocks:
            mocks['api_client'].create_tags.side_effect = InvalidTagDataError("Invalid tag data format")
            
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'urgent',
                '--description', 'Urgent priority',
                '--tag-type-name', 'priority'
            ])
            
            assert result.exit_code == 1
            assert '✗ Invalid Tag Data' in result.output
    
    def test_create_authentication_error(self, cli_runner, tag_command_mocks):
        """Test tag creation with authentication error."""
        with tag_command_mocks as mocks:
            mocks['api_client'].create_tags.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'urgent',
                '--description', 'Urgent priority',
                '--tag-type-name', 'priority'
            ])
            
            assert result.exit_code == 1
            assert '✗ Authentication Error' in result.output
            assert 'vamscli auth login' in result.output
    
    def test_create_no_setup(self, cli_runner, tag_no_setup_mocks):
        """Test tag creation without setup."""
        with tag_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'urgent',
                '--description', 'Urgent priority',
                '--tag-type-name', 'priority'
            ])
            
            assert result.exit_code == 1
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output


class TestTagUpdateCommand:
    """Test tag update command."""
    
    def test_update_success(self, cli_runner, tag_command_mocks):
        """Test successful tag update."""
        with tag_command_mocks as mocks:
            # Mock get_tags to return current tag data
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': [{
                        'tagName': 'urgent',
                        'description': 'Old description',
                        'tagTypeName': 'priority'
                    }]
                }
            }
            
            mocks['api_client'].update_tags.return_value = {
                'message': 'Succeeded'
            }
            
            result = cli_runner.invoke(cli, [
                'tag', 'update',
                '--tag-name', 'urgent',
                '--description', 'Updated description'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag(s) updated successfully!' in result.output
            
            # Verify API calls
            mocks['api_client'].get_tags.assert_called_once()
            expected_data = {
                'tags': [{
                    'tagName': 'urgent',
                    'description': 'Updated description',
                    'tagTypeName': 'priority'
                }]
            }
            mocks['api_client'].update_tags.assert_called_once_with(expected_data)
    
    def test_update_tag_type(self, cli_runner, tag_command_mocks):
        """Test tag update with tag type change."""
        with tag_command_mocks as mocks:
            # Mock get_tags to return current tag data
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': [{
                        'tagName': 'urgent',
                        'description': 'Urgent priority',
                        'tagTypeName': 'priority [R]'
                    }]
                }
            }
            
            mocks['api_client'].update_tags.return_value = {
                'message': 'Succeeded'
            }
            
            result = cli_runner.invoke(cli, [
                'tag', 'update',
                '--tag-name', 'urgent',
                '--tag-type-name', 'status'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag(s) updated successfully!' in result.output
            
            # Verify API calls - should strip [R] indicator
            expected_data = {
                'tags': [{
                    'tagName': 'urgent',
                    'description': 'Urgent priority',
                    'tagTypeName': 'status'
                }]
            }
            mocks['api_client'].update_tags.assert_called_once_with(expected_data)
    
    def test_update_json_input(self, cli_runner, tag_command_mocks):
        """Test tag update with JSON input."""
        with tag_command_mocks as mocks:
            mocks['api_client'].update_tags.return_value = {
                'message': 'Succeeded'
            }
            
            json_data = {
                'tags': [{
                    'tagName': 'urgent',
                    'description': 'Updated via JSON',
                    'tagTypeName': 'priority'
                }]
            }
            
            result = cli_runner.invoke(cli, [
                'tag', 'update',
                '--json-input', json.dumps(json_data)
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag(s) updated successfully!' in result.output
            assert 'Updating tag \'urgent\'...' in result.output
            
            # Verify API call
            mocks['api_client'].update_tags.assert_called_once_with(json_data)
    
    def test_update_tag_not_found(self, cli_runner, tag_command_mocks):
        """Test tag update when tag doesn't exist."""
        with tag_command_mocks as mocks:
            # Mock get_tags to return empty list
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': []
                }
            }
            
            result = cli_runner.invoke(cli, [
                'tag', 'update',
                '--tag-name', 'nonexistent',
                '--description', 'Updated description'
            ])
            
            assert result.exit_code == 1
            assert '✗ Tag Not Found' in result.output
            assert 'vamscli tag list' in result.output
    
    def test_update_no_fields(self, cli_runner, tag_command_mocks):
        """Test tag update without any fields to update."""
        with tag_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag', 'update',
                '--tag-name', 'urgent'
            ])
            
            assert result.exit_code == 1
            assert 'At least one field must be provided' in result.output
    
    def test_update_tag_type_not_found(self, cli_runner, tag_command_mocks):
        """Test tag update when new tag type doesn't exist."""
        with tag_command_mocks as mocks:
            # Mock get_tags to return current tag data
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': [{
                        'tagName': 'urgent',
                        'description': 'Urgent priority',
                        'tagTypeName': 'priority'
                    }]
                }
            }
            
            mocks['api_client'].update_tags.side_effect = TagTypeNotFoundError("Tag type 'nonexistent' not found")
            
            result = cli_runner.invoke(cli, [
                'tag', 'update',
                '--tag-name', 'urgent',
                '--tag-type-name', 'nonexistent'
            ])
            
            assert result.exit_code == 1
            assert '✗ Tag Type Not Found' in result.output
            assert 'vamscli tag-type list' in result.output


class TestTagDeleteCommand:
    """Test tag delete command."""
    
    def test_delete_success(self, cli_runner, tag_command_mocks):
        """Test successful tag deletion."""
        with tag_command_mocks as mocks:
            mocks['api_client'].delete_tag.return_value = {
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, [
                'tag', 'delete', 'urgent', '--confirm'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag deleted successfully!' in result.output
            assert 'urgent' in result.output
            
            # Verify API call
            mocks['api_client'].delete_tag.assert_called_once_with('urgent')
    
    def test_delete_json_output(self, cli_runner, tag_command_mocks):
        """Test tag deletion with JSON output."""
        with tag_command_mocks as mocks:
            api_response = {
                'message': 'Success',
                'deletedTag': 'urgent'
            }
            mocks['api_client'].delete_tag.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'tag', 'delete', 'urgent', '--confirm', '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should output progress message followed by JSON
            assert 'Deleting tag' in result.output
            # Extract JSON from output (skip the progress message)
            lines = result.output.strip().split('\n')
            json_lines = lines[1:]  # Skip first line (progress message)
            json_output = '\n'.join(json_lines)
            output_json = json.loads(json_output)
            assert output_json == api_response
    
    def test_delete_no_confirm(self, cli_runner, tag_command_mocks):
        """Test tag deletion without confirmation."""
        with tag_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag', 'delete', 'urgent'
                # Missing --confirm
            ])
            
            assert result.exit_code == 1
            assert 'Tag deletion requires explicit confirmation!' in result.output
            assert 'Use --confirm flag' in result.output
            
            # Verify API was not called
            mocks['api_client'].delete_tag.assert_not_called()
    
    def test_delete_tag_not_found(self, cli_runner, tag_command_mocks):
        """Test tag deletion when tag doesn't exist."""
        with tag_command_mocks as mocks:
            mocks['api_client'].delete_tag.side_effect = TagNotFoundError("Tag 'nonexistent' not found")
            
            result = cli_runner.invoke(cli, [
                'tag', 'delete', 'nonexistent', '--confirm'
            ])
            
            assert result.exit_code == 1
            assert '✗ Tag Not Found' in result.output
            assert 'vamscli tag list' in result.output
    
    def test_delete_authentication_error(self, cli_runner, tag_command_mocks):
        """Test tag deletion with authentication error."""
        with tag_command_mocks as mocks:
            mocks['api_client'].delete_tag.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, [
                'tag', 'delete', 'urgent', '--confirm'
            ])
            
            assert result.exit_code == 1
            assert '✗ Authentication Error' in result.output
            assert 'vamscli auth login' in result.output


class TestTagListCommand:
    """Test tag list command."""
    
    def test_list_success(self, cli_runner, tag_command_mocks):
        """Test successful tag listing."""
        with tag_command_mocks as mocks:
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': [
                        {
                            'tagName': 'urgent',
                            'description': 'Urgent priority',
                            'tagTypeName': 'priority [R]'
                        },
                        {
                            'tagName': 'low',
                            'description': 'Low priority',
                            'tagTypeName': 'priority'
                        }
                    ]
                }
            }
            
            result = cli_runner.invoke(cli, ['tag', 'list'])
            
            assert result.exit_code == 0
            assert 'Found 2 tag(s):' in result.output
            assert 'urgent' in result.output
            assert 'low' in result.output
            assert 'Urgent priority' in result.output
            assert 'Low priority' in result.output
            
            # Verify API call
            mocks['api_client'].get_tags.assert_called_once()
    
    def test_list_filtered_by_tag_type(self, cli_runner, tag_command_mocks):
        """Test tag listing with tag type filter."""
        with tag_command_mocks as mocks:
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': [
                        {
                            'tagName': 'urgent',
                            'description': 'Urgent priority',
                            'tagTypeName': 'priority [R]'
                        },
                        {
                            'tagName': 'active',
                            'description': 'Active status',
                            'tagTypeName': 'status'
                        }
                    ]
                }
            }
            
            result = cli_runner.invoke(cli, ['tag', 'list', '--tag-type', 'priority'])
            
            assert result.exit_code == 0
            assert 'urgent' in result.output
            assert 'active' not in result.output  # Should be filtered out
    
    def test_list_filter_no_results(self, cli_runner, tag_command_mocks):
        """Test tag listing with filter that returns no results."""
        with tag_command_mocks as mocks:
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': [
                        {
                            'tagName': 'urgent',
                            'description': 'Urgent priority',
                            'tagTypeName': 'priority [R]'
                        }
                    ]
                }
            }
            
            result = cli_runner.invoke(cli, ['tag', 'list', '--tag-type', 'nonexistent'])
            
            assert result.exit_code == 0
            assert 'No tags found for tag type \'nonexistent\'' in result.output
    
    def test_list_json_output(self, cli_runner, tag_command_mocks):
        """Test tag listing with JSON output."""
        with tag_command_mocks as mocks:
            api_response = {
                'message': {
                    'Items': [
                        {
                            'tagName': 'urgent',
                            'description': 'Urgent priority',
                            'tagTypeName': 'priority [R]'
                        }
                    ]
                }
            }
            
            mocks['api_client'].get_tags.return_value = api_response
            
            result = cli_runner.invoke(cli, ['tag', 'list', '--json-output'])
            
            assert result.exit_code == 0
            
            # Should output progress message followed by JSON
            assert 'Retrieving tags...' in result.output
            # Extract JSON from output (skip the progress message)
            lines = result.output.strip().split('\n')
            json_lines = lines[1:]  # Skip first line (progress message)
            json_output = '\n'.join(json_lines)
            output_json = json.loads(json_output)
            assert output_json == api_response
    
    def test_list_empty(self, cli_runner, tag_command_mocks):
        """Test tag listing when no tags exist."""
        with tag_command_mocks as mocks:
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': []
                }
            }
            
            result = cli_runner.invoke(cli, ['tag', 'list'])
            
            assert result.exit_code == 0
            assert 'No tags found.' in result.output
    
    def test_list_with_pagination(self, cli_runner, tag_command_mocks):
        """Test tag listing with pagination info."""
        with tag_command_mocks as mocks:
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': [
                        {
                            'tagName': 'urgent',
                            'description': 'Urgent priority',
                            'tagTypeName': 'priority'
                        }
                    ],
                    'NextToken': 'next-page-token'
                }
            }
            
            result = cli_runner.invoke(cli, ['tag', 'list'])
            
            assert result.exit_code == 0
            assert 'More results available' in result.output
    
    def test_list_authentication_error(self, cli_runner, tag_command_mocks):
        """Test tag listing with authentication error."""
        with tag_command_mocks as mocks:
            mocks['api_client'].get_tags.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, ['tag', 'list'])
            
            assert result.exit_code == 1
            assert '✗ Authentication Error' in result.output
            assert 'vamscli auth login' in result.output


class TestTagCommandsIntegration:
    """Integration tests for tag commands."""
    
    def test_commands_require_tag_name(self, cli_runner, tag_command_mocks):
        """Test that tag commands require tag name where appropriate."""
        with tag_command_mocks as mocks:
            # Test create without tag name (should trigger our custom validation)
            result = cli_runner.invoke(cli, ['tag', 'create'])
            assert result.exit_code == 1  # Our custom error handling
            assert 'required when not using --json-input' in result.output
            
            # Test update without tag name (should trigger our custom validation)
            result = cli_runner.invoke(cli, ['tag', 'update'])
            assert result.exit_code == 1  # Our custom error handling
            assert 'required when not using --json-input' in result.output
            
            # Test delete without tag name (Click argument validation)
            result = cli_runner.invoke(cli, ['tag', 'delete'])
            assert result.exit_code == 2  # Click parameter error
            assert 'Missing argument' in result.output or 'required' in result.output.lower()
    
    def test_tag_lifecycle_flow(self, cli_runner, tag_command_mocks):
        """Test complete tag lifecycle: create -> update -> delete."""
        with tag_command_mocks as mocks:
            # Test create
            mocks['api_client'].create_tags.return_value = {'message': 'Succeeded'}
            
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'test-tag',
                '--description', 'Test tag',
                '--tag-type-name', 'test-type'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag(s) created successfully!' in result.output
            
            # Test update
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': [{
                        'tagName': 'test-tag',
                        'description': 'Test tag',
                        'tagTypeName': 'test-type'
                    }]
                }
            }
            mocks['api_client'].update_tags.return_value = {'message': 'Succeeded'}
            
            result = cli_runner.invoke(cli, [
                'tag', 'update',
                '--tag-name', 'test-tag',
                '--description', 'Updated test tag'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag(s) updated successfully!' in result.output
            
            # Test delete
            mocks['api_client'].delete_tag.return_value = {'message': 'Success'}
            
            result = cli_runner.invoke(cli, [
                'tag', 'delete', 'test-tag', '--confirm'
            ])
            
            assert result.exit_code == 0
            assert '✓ Tag deleted successfully!' in result.output


class TestTagCommandsJSONHandling:
    """Test JSON input/output handling for tag commands."""
    
    def test_invalid_json_input_string(self, cli_runner, tag_command_mocks):
        """Test handling of invalid JSON input string."""
        with tag_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--json-input', 'invalid json string'
            ])
            
            assert result.exit_code == 1  # Our custom error handling
            assert 'Invalid JSON input' in result.output
    
    def test_invalid_json_input_file(self, cli_runner, tag_command_mocks):
        """Test handling of invalid JSON input file."""
        with tag_command_mocks as mocks:
            with patch('builtins.open', mock_open(read_data='invalid json')):
                result = cli_runner.invoke(cli, [
                    'tag', 'create',
                    '--json-input', 'invalid.json'
                ])
            
            assert result.exit_code == 1  # Our custom error handling
            # The error message will be a JSON decode error since the file contains invalid JSON
            assert 'Unexpected error' in result.output and 'Expecting value' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, tag_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with tag_command_mocks as mocks:
            with patch('builtins.open', side_effect=FileNotFoundError()):
                result = cli_runner.invoke(cli, [
                    'tag', 'create',
                    '--json-input', 'nonexistent.json'
                ])
            
            assert result.exit_code == 1  # Our custom error handling
            assert 'Invalid JSON input' in result.output


class TestTagCommandsEdgeCases:
    """Test edge cases for tag commands."""
    
    def test_create_api_error(self, cli_runner, tag_command_mocks):
        """Test create command with general API error."""
        with tag_command_mocks as mocks:
            mocks['api_client'].create_tags.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'tag', 'create',
                '--tag-name', 'urgent',
                '--description', 'Urgent priority',
                '--tag-type-name', 'priority'
            ])
            
            assert result.exit_code == 1
            assert '✗ Unexpected error' in result.output
            assert 'API request failed' in result.output
    
    def test_update_api_error(self, cli_runner, tag_command_mocks):
        """Test update command with general API error."""
        with tag_command_mocks as mocks:
            # Mock get_tags to return current tag data
            mocks['api_client'].get_tags.return_value = {
                'message': {
                    'Items': [{
                        'tagName': 'urgent',
                        'description': 'Old description',
                        'tagTypeName': 'priority'
                    }]
                }
            }
            
            mocks['api_client'].update_tags.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'tag', 'update',
                '--tag-name', 'urgent',
                '--description', 'Updated description'
            ])
            
            assert result.exit_code == 1
            assert '✗ Unexpected error' in result.output
            assert 'API request failed' in result.output
    
    def test_delete_api_error(self, cli_runner, tag_command_mocks):
        """Test delete command with general API error."""
        with tag_command_mocks as mocks:
            mocks['api_client'].delete_tag.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'tag', 'delete', 'urgent', '--confirm'
            ])
            
            assert result.exit_code == 1
            assert '✗ Unexpected error' in result.output
            assert 'API request failed' in result.output
    
    def test_list_api_error(self, cli_runner, tag_command_mocks):
        """Test list command with general API error."""
        with tag_command_mocks as mocks:
            mocks['api_client'].get_tags.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, ['tag', 'list'])
            
            assert result.exit_code == 1
            assert '✗ Unexpected error' in result.output
            assert 'API request failed' in result.output


class TestTagUtilityFunctions:
    """Test tag utility functions."""
    
    def test_parse_json_input_string(self):
        """Test JSON input parsing from string."""
        from vamscli.commands.tag import parse_json_input
        
        json_string = '{"tags": [{"tagName": "test", "description": "Test tag"}]}'
        result = parse_json_input(json_string)
        
        expected = {"tags": [{"tagName": "test", "description": "Test tag"}]}
        assert result == expected
    
    def test_parse_json_input_file(self):
        """Test JSON input parsing from file."""
        from vamscli.commands.tag import parse_json_input
        
        json_data = {"tags": [{"tagName": "test", "description": "Test tag"}]}
        
        with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
            result = parse_json_input('test.json')
        
        assert result == json_data
    
    def test_parse_json_input_invalid_string(self):
        """Test JSON input parsing with invalid string."""
        from vamscli.commands.tag import parse_json_input
        
        with pytest.raises(click.BadParameter) as exc_info:
            parse_json_input('invalid json')
        
        assert 'Invalid JSON input' in str(exc_info.value)
    
    def test_parse_json_input_nonexistent_file(self):
        """Test JSON input parsing with nonexistent file."""
        from vamscli.commands.tag import parse_json_input
        
        with patch('builtins.open', side_effect=FileNotFoundError()):
            with pytest.raises(click.BadParameter) as exc_info:
                parse_json_input('nonexistent.json')
        
        assert 'Invalid JSON input' in str(exc_info.value)
    
    def test_format_tag_output_cli(self):
        """Test tag output formatting for CLI."""
        from vamscli.commands.tag import format_tag_output
        
        tag_data = {
            'tagName': 'urgent',
            'description': 'Urgent priority',
            'tagTypeName': 'priority [R]'
        }
        
        result = format_tag_output(tag_data, json_output=False)
        
        assert 'Tag Details:' in result
        assert 'Name: urgent' in result
        assert 'Description: Urgent priority' in result
        assert 'Tag Type: priority [R] (Required)' in result
    
    def test_format_tag_output_json(self):
        """Test tag output formatting for JSON."""
        from vamscli.commands.tag import format_tag_output
        
        tag_data = {
            'tagName': 'urgent',
            'description': 'Urgent priority',
            'tagTypeName': 'priority'
        }
        
        result = format_tag_output(tag_data, json_output=True)
        
        # Should be valid JSON
        parsed = json.loads(result)
        assert parsed == tag_data
    
    def test_format_tags_list_output_cli(self):
        """Test tags list output formatting for CLI."""
        from vamscli.commands.tag import format_tags_list_output
        
        tags_data = [
            {
                'tagName': 'urgent',
                'description': 'Urgent priority',
                'tagTypeName': 'priority [R]'
            },
            {
                'tagName': 'low',
                'description': 'Low priority',
                'tagTypeName': 'priority'
            }
        ]
        
        result = format_tags_list_output(tags_data, json_output=False)
        
        assert 'Found 2 tag(s):' in result
        assert 'urgent' in result
        assert 'low' in result
        assert 'Name' in result  # Header
        assert 'Tag Type' in result  # Header
        assert 'Description' in result  # Header
    
    def test_format_tags_list_output_empty(self):
        """Test tags list output formatting with empty list."""
        from vamscli.commands.tag import format_tags_list_output
        
        result = format_tags_list_output([], json_output=False)
        
        assert result == "No tags found."
    
    def test_format_tags_list_output_json(self):
        """Test tags list output formatting for JSON."""
        from vamscli.commands.tag import format_tags_list_output
        
        tags_data = [
            {
                'tagName': 'urgent',
                'description': 'Urgent priority',
                'tagTypeName': 'priority'
            }
        ]
        
        result = format_tags_list_output(tags_data, json_output=True)
        
        # Should be valid JSON
        parsed = json.loads(result)
        assert parsed == tags_data


if __name__ == '__main__':
    pytest.main([__file__])
