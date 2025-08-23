"""Test metadata management commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    APIError, AuthenticationError, AssetNotFoundError, DatabaseNotFoundError,
    InvalidAssetDataError
)


# File-level fixtures for metadata-specific testing patterns
@pytest.fixture
def metadata_command_mocks(generic_command_mocks):
    """Provide metadata-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for metadata command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('metadata')


@pytest.fixture
def metadata_no_setup_mocks(no_setup_command_mocks):
    """Provide metadata command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('metadata')


class TestMetadataGetCommand:
    """Test metadata get command."""
    
    def test_get_help(self, cli_runner):
        """Test get command help."""
        result = cli_runner.invoke(cli, ['metadata', 'get', '--help'])
        assert result.exit_code == 0
        assert 'Get metadata for an asset or file' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--file-path' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_get_success(self, cli_runner, metadata_command_mocks):
        """Test successful metadata get."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.return_value = {
                "version": "1",
                "metadata": {
                    "title": "Test Asset",
                    "description": "A test asset",
                    "tags": ["test", "example"]
                }
            }
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ Metadata retrieved successfully' in result.output
            assert 'title: Test Asset' in result.output
            assert '"test"' in result.output
            assert '"example"' in result.output
            
            # Verify API call
            mocks['api_client'].get_metadata.assert_called_once_with('test-db', 'test-asset', None)
    
    def test_get_with_file_path(self, cli_runner, metadata_command_mocks):
        """Test metadata get with file path."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.return_value = {
                "version": "1",
                "metadata": {
                    "file_type": "gltf",
                    "size": 1024
                }
            }
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset',
                '--file-path', '/models/file.gltf'
            ])
            
            assert result.exit_code == 0
            assert '✓ Metadata retrieved successfully' in result.output
            assert "file '/models/file.gltf'" in result.output
            
            # Verify API call
            mocks['api_client'].get_metadata.assert_called_once_with('test-db', 'test-asset', '/models/file.gltf')
    
    def test_get_json_output(self, cli_runner, metadata_command_mocks):
        """Test metadata get with JSON output."""
        with metadata_command_mocks as mocks:
            api_response = {
                "version": "1",
                "metadata": {
                    "title": "Test Asset"
                }
            }
            mocks['api_client'].get_metadata.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Parse output as JSON
            output_data = json.loads(result.output)
            assert output_data == api_response
    
    def test_get_json_input(self, cli_runner, metadata_command_mocks):
        """Test metadata get with JSON input."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.return_value = {
                "version": "1",
                "metadata": {"title": "Test"}
            }
            
            json_input = '{"database_id": "json-db", "asset_id": "json-asset", "file_path": "/test.gltf"}'
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'override-db',  # This should be overridden by JSON
                '-a', 'override-asset',  # This should be overridden by JSON
                '--json-input', json_input
            ])
            
            assert result.exit_code == 0
            
            # Verify API call with JSON input values
            mocks['api_client'].get_metadata.assert_called_once_with('json-db', 'json-asset', '/test.gltf')
    
    def test_get_empty_metadata(self, cli_runner, metadata_command_mocks):
        """Test metadata get with empty metadata response."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.return_value = {
                "version": "1",
                "metadata": {}
            }
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ Metadata retrieved successfully' in result.output
            assert 'No metadata found.' in result.output
    
    def test_get_asset_not_found(self, cli_runner, metadata_command_mocks):
        """Test metadata get with asset not found."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.side_effect = AssetNotFoundError("Asset 'test-asset' not found")
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert "Asset 'test-asset' not found" in result.output
    
    def test_get_database_not_found(self, cli_runner, metadata_command_mocks):
        """Test metadata get with database not found."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.side_effect = DatabaseNotFoundError("Database 'test-db' not found")
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert "Database 'test-db' not found" in result.output
    
    def test_get_authentication_error(self, cli_runner, metadata_command_mocks):
        """Test metadata get with authentication error."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert "Authentication failed" in result.output
    
    def test_get_api_error(self, cli_runner, metadata_command_mocks):
        """Test metadata get with API error."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert "API request failed" in result.output
    
    def test_get_error_with_json_output(self, cli_runner, metadata_command_mocks):
        """Test metadata error with JSON output."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset',
                '--json-output'
            ])
            
            assert result.exit_code == 1
            
            # Parse output as JSON
            output_data = json.loads(result.output)
            assert output_data == {"error": "API request failed"}
    
    def test_get_no_setup(self, cli_runner, metadata_no_setup_mocks):
        """Test metadata get without setup."""
        with metadata_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'metadata', 'get', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output


class TestMetadataCreateCommand:
    """Test metadata create command."""
    
    def test_create_help(self, cli_runner):
        """Test create command help."""
        result = cli_runner.invoke(cli, ['metadata', 'create', '--help'])
        assert result.exit_code == 0
        assert 'Create metadata for an asset or file' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--file-path' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_create_json_input(self, cli_runner, metadata_command_mocks):
        """Test metadata create with JSON input."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].create_metadata.return_value = {"status": "OK"}
            
            json_input = '{"database_id": "test-db", "asset_id": "test-asset", "title": "My Asset", "tags": ["test"]}'
            
            result = cli_runner.invoke(cli, [
                'metadata', 'create', 
                '-d', 'override-db',  # This should be overridden by JSON
                '-a', 'override-asset',  # This should be overridden by JSON
                '--json-input', json_input
            ])
            
            assert result.exit_code == 0
            assert '✓ Metadata created successfully!' in result.output
            
            # Verify API call
            expected_metadata = {"title": "My Asset", "tags": ["test"]}
            mocks['api_client'].create_metadata.assert_called_once_with('test-db', 'test-asset', expected_metadata, None)
    
    def test_create_with_metadata_key(self, cli_runner, metadata_command_mocks):
        """Test metadata create with explicit metadata key in JSON."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].create_metadata.return_value = {"status": "OK"}
            
            json_input = '{"database_id": "test-db", "asset_id": "test-asset", "metadata": {"title": "My Asset", "version": 2}}'
            
            result = cli_runner.invoke(cli, [
                'metadata', 'create', 
                '-d', 'override-db',
                '-a', 'override-asset',
                '--json-input', json_input
            ])
            
            assert result.exit_code == 0
            assert '✓ Metadata created successfully!' in result.output
            
            # Verify API call
            expected_metadata = {"title": "My Asset", "version": 2}
            mocks['api_client'].create_metadata.assert_called_once_with('test-db', 'test-asset', expected_metadata, None)
    
    def test_create_json_output(self, cli_runner, metadata_command_mocks):
        """Test metadata create with JSON output."""
        with metadata_command_mocks as mocks:
            api_response = {"status": "OK", "message": "Metadata created"}
            mocks['api_client'].create_metadata.return_value = api_response
            
            json_input = '{"database_id": "test-db", "asset_id": "test-asset", "title": "Test"}'
            
            result = cli_runner.invoke(cli, [
                'metadata', 'create', 
                '-d', 'override-db',
                '-a', 'override-asset',
                '--json-input', json_input,
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Parse output as JSON
            output_data = json.loads(result.output)
            assert output_data == api_response
    
    def test_create_invalid_data(self, cli_runner, metadata_command_mocks):
        """Test metadata create with invalid data."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].create_metadata.side_effect = InvalidAssetDataError("Invalid metadata data")
            
            json_input = '{"database_id": "test-db", "asset_id": "test-asset", "title": "Test"}'
            
            result = cli_runner.invoke(cli, [
                'metadata', 'create', 
                '-d', 'override-db',
                '-a', 'override-asset',
                '--json-input', json_input
            ])
            
            assert result.exit_code == 1
            assert "Invalid metadata data" in result.output
    
    @patch('vamscli.commands.metadata.collect_metadata_interactively')
    def test_create_no_metadata_provided(self, mock_collect, cli_runner, metadata_command_mocks):
        """Test metadata create with no metadata provided."""
        with metadata_command_mocks as mocks:
            mock_collect.return_value = {}
            
            result = cli_runner.invoke(cli, [
                'metadata', 'create', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert "No metadata provided" in result.output
    
    @patch('vamscli.commands.metadata.collect_metadata_interactively')
    def test_create_interactive_success(self, mock_collect, cli_runner, metadata_command_mocks):
        """Test metadata create with interactive input."""
        with metadata_command_mocks as mocks:
            mock_collect.return_value = {"title": "Interactive Asset", "version": 1}
            mocks['api_client'].create_metadata.return_value = {"status": "OK"}
            
            result = cli_runner.invoke(cli, [
                'metadata', 'create', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ Metadata created successfully!' in result.output
            
            # Verify API call
            expected_metadata = {"title": "Interactive Asset", "version": 1}
            mocks['api_client'].create_metadata.assert_called_once_with('test-db', 'test-asset', expected_metadata, None)


class TestMetadataUpdateCommand:
    """Test metadata update command."""
    
    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(cli, ['metadata', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Update metadata for an asset or file' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--file-path' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_update_json_input(self, cli_runner, metadata_command_mocks):
        """Test metadata update with JSON input."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].update_metadata.return_value = {"status": "OK"}
            
            json_input = '{"database_id": "test-db", "asset_id": "test-asset", "file_path": "/file.gltf", "title": "Updated Asset"}'
            
            result = cli_runner.invoke(cli, [
                'metadata', 'update', 
                '-d', 'override-db',
                '-a', 'override-asset',
                '--json-input', json_input
            ])
            
            assert result.exit_code == 0
            assert '✓ Metadata updated successfully!' in result.output
            
            # Verify API call
            expected_metadata = {"title": "Updated Asset"}
            mocks['api_client'].update_metadata.assert_called_once_with('test-db', 'test-asset', expected_metadata, '/file.gltf')
    
    def test_update_success(self, cli_runner, metadata_command_mocks):
        """Test successful metadata update."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].update_metadata.return_value = {"status": "OK"}
            
            json_input = '{"database_id": "test-db", "asset_id": "test-asset", "metadata": {"title": "Updated", "version": 2}}'
            
            result = cli_runner.invoke(cli, [
                'metadata', 'update', 
                '-d', 'override-db',
                '-a', 'override-asset',
                '--json-input', json_input
            ])
            
            assert result.exit_code == 0
            assert '✓ Metadata updated successfully!' in result.output
            
            # Verify API call
            expected_metadata = {"title": "Updated", "version": 2}
            mocks['api_client'].update_metadata.assert_called_once_with('test-db', 'test-asset', expected_metadata, None)
    
    @patch('vamscli.commands.metadata.collect_metadata_interactively')
    def test_update_no_metadata_provided(self, mock_collect, cli_runner, metadata_command_mocks):
        """Test metadata update with no metadata provided."""
        with metadata_command_mocks as mocks:
            mock_collect.return_value = {}
            
            result = cli_runner.invoke(cli, [
                'metadata', 'update', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert "No metadata provided" in result.output


class TestMetadataDeleteCommand:
    """Test metadata delete command."""
    
    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(cli, ['metadata', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete metadata for an asset or file' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--file-path' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_delete_success(self, cli_runner, metadata_command_mocks):
        """Test successful metadata delete."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].delete_metadata.return_value = {"status": "OK", "message": "test-asset deleted"}
            
            result = cli_runner.invoke(cli, [
                'metadata', 'delete', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ], input='y\n')  # Confirm deletion
            
            assert result.exit_code == 0
            assert '✓ Metadata deleted successfully!' in result.output
            
            # Verify API call
            mocks['api_client'].delete_metadata.assert_called_once_with('test-db', 'test-asset', None)
    
    def test_delete_cancelled(self, cli_runner, metadata_command_mocks):
        """Test metadata delete cancelled by user."""
        with metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'metadata', 'delete', 
                '-d', 'test-db', 
                '-a', 'test-asset'
            ], input='n\n')  # Cancel deletion
            
            assert result.exit_code == 0
            assert 'Operation cancelled' in result.output
            
            # Verify API was not called
            mocks['api_client'].delete_metadata.assert_not_called()
    
    def test_delete_with_file_path(self, cli_runner, metadata_command_mocks):
        """Test metadata delete with file path."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].delete_metadata.return_value = {"status": "OK"}
            
            result = cli_runner.invoke(cli, [
                'metadata', 'delete', 
                '-d', 'test-db', 
                '-a', 'test-asset',
                '--file-path', '/models/file.gltf'
            ], input='y\n')  # Confirm deletion
            
            assert result.exit_code == 0
            assert '✓ Metadata deleted successfully!' in result.output
            assert "file '/models/file.gltf'" in result.output
            
            # Verify API call
            mocks['api_client'].delete_metadata.assert_called_once_with('test-db', 'test-asset', '/models/file.gltf')
    
    def test_delete_json_output(self, cli_runner, metadata_command_mocks):
        """Test metadata delete with JSON output."""
        with metadata_command_mocks as mocks:
            api_response = {"status": "OK", "message": "test-asset deleted"}
            mocks['api_client'].delete_metadata.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'metadata', 'delete', 
                '-d', 'test-db', 
                '-a', 'test-asset',
                '--json-output'
            ], input='y\n')  # Confirm deletion
            
            assert result.exit_code == 0
            
            # Parse output as JSON - need to extract JSON from output that includes confirmation prompt
            lines = result.output.strip().split('\n')
            # Find the JSON part (should be the last few lines)
            json_start = -1
            for i, line in enumerate(lines):
                if line.strip().startswith('{'):
                    json_start = i
                    break
            
            if json_start >= 0:
                json_output = '\n'.join(lines[json_start:])
                output_data = json.loads(json_output)
                assert output_data == api_response
            else:
                # Fallback: check if the JSON is in the output somewhere
                assert '"status": "OK"' in result.output
                assert '"message": "test-asset deleted"' in result.output


class TestMetadataCommandsIntegration:
    """Test integration scenarios for metadata commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_parameters(self, mock_main_profile_manager):
        """Test that metadata commands require parameters where appropriate."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test get without database ID
        result = runner.invoke(cli, ['metadata', 'get', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test get without asset ID
        result = runner.invoke(cli, ['metadata', 'get', '-d', 'test-db'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test create without database ID
        result = runner.invoke(cli, ['metadata', 'create', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test create without asset ID
        result = runner.invoke(cli, ['metadata', 'create', '-d', 'test-db'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_authentication_error_handling(self, cli_runner, metadata_command_mocks):
        """Test authentication error handling."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, [
                'metadata', 'get',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert 'Authentication failed' in result.output


class TestMetadataUtilityFunctions:
    """Test metadata utility functions."""
    
    def test_parse_json_input_file_not_found(self):
        """Test parse_json_input with non-existent file."""
        from vamscli.commands.metadata import parse_json_input
        
        with pytest.raises(click.ClickException) as exc_info:
            parse_json_input('@nonexistent.json')
        
        assert "JSON input file not found" in str(exc_info.value)
    
    def test_parse_json_input_invalid_json_string(self):
        """Test parse_json_input with invalid JSON string."""
        from vamscli.commands.metadata import parse_json_input
        
        with pytest.raises(click.ClickException) as exc_info:
            parse_json_input('{"invalid": json}')
        
        assert "Invalid JSON input" in str(exc_info.value)
    
    def test_parse_json_input_valid_json_string(self):
        """Test parse_json_input with valid JSON string."""
        from vamscli.commands.metadata import parse_json_input
        
        result = parse_json_input('{"key": "value", "number": 42}')
        assert result == {"key": "value", "number": 42}
    
    def test_parse_json_input_empty_string(self):
        """Test parse_json_input with empty string."""
        from vamscli.commands.metadata import parse_json_input
        
        result = parse_json_input('')
        assert result == {}
    
    def test_parse_value_json_object(self):
        """Test parse_value with JSON object."""
        from vamscli.commands.metadata import parse_value
        
        result = parse_value('{"key": "value", "number": 42}')
        assert result == {"key": "value", "number": 42}
    
    def test_parse_value_json_array(self):
        """Test parse_value with JSON array."""
        from vamscli.commands.metadata import parse_value
        
        result = parse_value('["item1", "item2", 123]')
        assert result == ["item1", "item2", 123]
    
    def test_parse_value_json_number(self):
        """Test parse_value with JSON number."""
        from vamscli.commands.metadata import parse_value
        
        result = parse_value('42')
        assert result == 42
    
    def test_parse_value_json_boolean(self):
        """Test parse_value with JSON boolean."""
        from vamscli.commands.metadata import parse_value
        
        result = parse_value('true')
        assert result is True
    
    def test_parse_value_string(self):
        """Test parse_value with plain string."""
        from vamscli.commands.metadata import parse_value
        
        result = parse_value('plain string')
        assert result == 'plain string'
    
    def test_parse_value_empty_string(self):
        """Test parse_value with empty string."""
        from vamscli.commands.metadata import parse_value
        
        result = parse_value('')
        assert result == ''
    
    def test_format_metadata_output_empty(self):
        """Test format_metadata_output with empty metadata."""
        from vamscli.commands.metadata import format_metadata_output
        
        result = format_metadata_output({})
        assert result == "No metadata found."
    
    def test_format_metadata_output_simple(self):
        """Test format_metadata_output with simple metadata."""
        from vamscli.commands.metadata import format_metadata_output
        
        metadata = {
            "title": "Test Asset",
            "version": 1,
            "active": True
        }
        
        result = format_metadata_output(metadata)
        assert "title: Test Asset" in result
        assert "version: 1" in result
        assert "active: True" in result
    
    def test_format_metadata_output_complex(self):
        """Test format_metadata_output with complex metadata."""
        from vamscli.commands.metadata import format_metadata_output
        
        metadata = {
            "title": "Test Asset",
            "tags": ["test", "example"],
            "properties": {
                "size": 1024,
                "format": "gltf"
            }
        }
        
        result = format_metadata_output(metadata)
        assert "title: Test Asset" in result
        assert '"test"' in result  # JSON array formatting
        assert '"size": 1024' in result  # JSON object formatting
    
    @patch('click.prompt')
    def test_collect_metadata_interactively_mock(self, mock_prompt):
        """Test collect_metadata_interactively function with mocked input."""
        from vamscli.commands.metadata import collect_metadata_interactively
        
        # Simulate user entering key1=value1, key2=42, then done
        mock_prompt.side_effect = [
            'title',           # First key
            'Test Asset',      # First value
            'version',         # Second key  
            '42',              # Second value (will be parsed as number)
            'done'             # Finish
        ]
        
        result = collect_metadata_interactively()
        
        expected = {
            'title': 'Test Asset',
            'version': 42
        }
        assert result == expected
    
    @patch('click.prompt')
    def test_collect_metadata_interactively_json_values(self, mock_prompt):
        """Test collect_metadata_interactively with JSON values."""
        from vamscli.commands.metadata import collect_metadata_interactively
        
        # Mock click.prompt to simulate user input with JSON values
        mock_prompt.side_effect = [
            'tags',                           # Key
            '["tag1", "tag2"]',              # JSON array value
            'properties',                     # Key
            '{"size": 1024, "format": "gltf"}',  # JSON object value
            'done'                           # Finish
        ]
        
        result = collect_metadata_interactively()
        
        expected = {
            'tags': ['tag1', 'tag2'],
            'properties': {'size': 1024, 'format': 'gltf'}
        }
        assert result == expected


class TestMetadataJSONHandling:
    """Test JSON input/output handling for metadata commands."""
    
    def test_parse_json_input_from_file(self, cli_runner, metadata_command_mocks):
        """Test parsing JSON input from file."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_metadata.return_value = {
                "version": "1",
                "metadata": {"title": "File Test"}
            }
            
            json_data = {"database_id": "file-db", "asset_id": "file-asset"}
            
            # Mock both the file existence check and file reading
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'metadata', 'get',
                    '-d', 'override-db',
                    '-a', 'override-asset',
                    '--json-input', '@test.json'
                ])
            
            assert result.exit_code == 0
            # Verify API call uses file data
            mocks['api_client'].get_metadata.assert_called_once_with('file-db', 'file-asset', None)
    
    def test_invalid_json_input_file(self, cli_runner, metadata_command_mocks):
        """Test handling of invalid JSON input file."""
        with metadata_command_mocks as mocks:
            # Mock file exists but contains invalid JSON
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('builtins.open', mock_open(read_data='invalid json')):
                result = cli_runner.invoke(cli, [
                    'metadata', 'get',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    '--json-input', '@invalid.json'
                ])
            
            assert result.exit_code == 1
            # The error message includes the specific JSON error details
            assert 'Invalid JSON' in result.output or 'Expecting value' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, metadata_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with metadata_command_mocks as mocks:
            with patch('pathlib.Path.exists', return_value=False):
                result = cli_runner.invoke(cli, [
                    'metadata', 'get',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    '--json-input', '@nonexistent.json'
                ])
            
            assert result.exit_code == 1
            assert 'JSON input file not found' in result.output


class TestMetadataEdgeCases:
    """Test edge cases for metadata commands."""
    
    def test_metadata_help(self, cli_runner):
        """Test metadata group help."""
        result = cli_runner.invoke(cli, ['metadata', '--help'])
        assert result.exit_code == 0
        assert 'Metadata management commands' in result.output
        assert 'get' in result.output
        assert 'create' in result.output
        assert 'update' in result.output
        assert 'delete' in result.output
    
    def test_get_missing_database_parameter(self, cli_runner):
        """Test get command with missing database parameter."""
        result = cli_runner.invoke(cli, ['metadata', 'get', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_get_missing_asset_parameter(self, cli_runner):
        """Test get command with missing asset parameter."""
        result = cli_runner.invoke(cli, ['metadata', 'get', '-d', 'test-db'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_create_missing_database_parameter(self, cli_runner):
        """Test create command with missing database parameter."""
        result = cli_runner.invoke(cli, ['metadata', 'create', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_create_missing_asset_parameter(self, cli_runner):
        """Test create command with missing asset parameter."""
        result = cli_runner.invoke(cli, ['metadata', 'create', '-d', 'test-db'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_update_missing_database_parameter(self, cli_runner):
        """Test update command with missing database parameter."""
        result = cli_runner.invoke(cli, ['metadata', 'update', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_update_missing_asset_parameter(self, cli_runner):
        """Test update command with missing asset parameter."""
        result = cli_runner.invoke(cli, ['metadata', 'update', '-d', 'test-db'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_delete_missing_database_parameter(self, cli_runner):
        """Test delete command with missing database parameter."""
        result = cli_runner.invoke(cli, ['metadata', 'delete', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_delete_missing_asset_parameter(self, cli_runner):
        """Test delete command with missing asset parameter."""
        result = cli_runner.invoke(cli, ['metadata', 'delete', '-d', 'test-db'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()


if __name__ == '__main__':
    pytest.main([__file__])
