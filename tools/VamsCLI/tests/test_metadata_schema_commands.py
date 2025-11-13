"""Test metadata schema management commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    DatabaseNotFoundError, AuthenticationError, APIError, SetupRequiredError
)


# File-level fixtures for metadata-schema-specific testing patterns
@pytest.fixture
def metadata_schema_command_mocks(generic_command_mocks):
    """Provide metadata-schema-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for metadata schema command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('metadata_schema')


@pytest.fixture
def metadata_schema_no_setup_mocks(no_setup_command_mocks):
    """Provide metadata-schema command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('metadata_schema')


class TestMetadataSchemaGetCommand:
    """Test metadata schema get command."""
    
    def test_get_help(self, cli_runner):
        """Test get command help."""
        result = cli_runner.invoke(cli, ['metadata-schema', 'get', '--help'])
        assert result.exit_code == 0
        assert 'Get metadata schema for a database' in result.output
        assert '--database' in result.output
        assert '--max-items' in result.output
        assert '--page-size' in result.output
        assert '--starting-token' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_get_success(self, cli_runner, metadata_schema_command_mocks):
        """Test successful metadata schema retrieval."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema.return_value = {
                'message': {
                    'Items': [
                        {
                            'field': 'title',
                            'datatype': 'string',
                            'required': True,
                            'dependsOn': []
                        },
                        {
                            'field': 'category',
                            'datatype': 'string',
                            'required': False,
                            'dependsOn': ['title']
                        },
                        {
                            'field': 'priority',
                            'datatype': 'number',
                            'required': True,
                            'dependsOn': ['category', 'title']
                        }
                    ],
                    'NextToken': 'next-page-token'
                }
            }
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get', 
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 0
            assert 'Metadata Schema for Database (3 field(s)):' in result.output
            assert 'Field Name' in result.output
            assert 'Data Type' in result.output
            assert 'Required' in result.output
            assert 'Depends On' in result.output
            assert 'title' in result.output
            assert 'category' in result.output
            assert 'priority' in result.output
            assert 'string' in result.output
            assert 'number' in result.output
            assert 'Yes' in result.output  # Required field
            assert 'No' in result.output   # Non-required field
            assert 'More results available' in result.output
            
            # Verify API call
            mocks['api_client'].get_metadata_schema.assert_called_once_with(
                database_id='test-database',
                max_items=1000,
                page_size=100,
                starting_token=None
            )
    
    def test_get_with_pagination(self, cli_runner, metadata_schema_command_mocks):
        """Test metadata schema retrieval with pagination parameters."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema.return_value = {
                'message': {
                    'Items': [],
                    'NextToken': None
                }
            }
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database',
                '--max-items', '50',
                '--page-size', '25',
                '--starting-token', 'test-token'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call with pagination parameters
            mocks['api_client'].get_metadata_schema.assert_called_once_with(
                database_id='test-database',
                max_items=50,
                page_size=25,
                starting_token='test-token'
            )
    
    def test_get_json_output(self, cli_runner, metadata_schema_command_mocks):
        """Test metadata schema retrieval with JSON output."""
        with metadata_schema_command_mocks as mocks:
            api_response = {
                'message': {
                    'Items': [
                        {
                            'field': 'title',
                            'datatype': 'string',
                            'required': True,
                            'dependsOn': []
                        }
                    ]
                }
            }
            mocks['api_client'].get_metadata_schema.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert output_json == api_response
    
    def test_get_json_input_string(self, cli_runner, metadata_schema_command_mocks):
        """Test metadata schema retrieval with JSON input string."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema.return_value = {
                'message': {
                    'Items': [],
                    'NextToken': None
                }
            }
            
            json_input = '{"maxItems": 100, "pageSize": 50, "startingToken": "json-token"}'
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database',
                '--json-input', json_input
            ])
            
            assert result.exit_code == 0
            
            # Verify API call uses JSON input parameters
            mocks['api_client'].get_metadata_schema.assert_called_once_with(
                database_id='test-database',
                max_items=100,
                page_size=50,
                starting_token='json-token'
            )
    
    def test_get_json_input_file(self, cli_runner, metadata_schema_command_mocks):
        """Test metadata schema retrieval with JSON input file."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema.return_value = {
                'message': {
                    'Items': [],
                    'NextToken': None
                }
            }
            
            json_data = {
                'maxItems': 200,
                'pageSize': 75,
                'startingToken': 'file-token'
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'metadata-schema', 'get',
                    '-d', 'test-database',
                    '--json-input', 'pagination.json'
                ])
            
            assert result.exit_code == 0
            
            # Verify API call uses JSON file parameters
            mocks['api_client'].get_metadata_schema.assert_called_once_with(
                database_id='test-database',
                max_items=200,
                page_size=75,
                starting_token='file-token'
            )
    
    def test_get_empty_schema(self, cli_runner, metadata_schema_command_mocks):
        """Test metadata schema retrieval with empty schema."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema.return_value = {
                'message': {
                    'Items': []
                }
            }
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 0
            assert 'No metadata schema fields found for this database.' in result.output
    
    def test_get_database_not_found(self, cli_runner, metadata_schema_command_mocks):
        """Test get command with database not found error."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema.side_effect = DatabaseNotFoundError("Database 'nonexistent-db' not found")
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'nonexistent-db'
            ])
            
            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert 'vamscli database list' in result.output
    
    def test_get_authentication_error(self, cli_runner, metadata_schema_command_mocks):
        """Test get command with authentication error."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 1
            # Check that the original exception is preserved
            assert isinstance(result.exception, AuthenticationError)
    
    def test_get_api_error(self, cli_runner, metadata_schema_command_mocks):
        """Test get command with general API error."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 1
            # Check that the original exception is preserved
            assert isinstance(result.exception, APIError)
    
    def test_get_no_setup(self, cli_runner, metadata_schema_no_setup_mocks):
        """Test get command without setup."""
        with metadata_schema_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 1
            # Check that setup required exception is preserved
            assert isinstance(result.exception, SetupRequiredError)
    
    def test_get_with_profile(self, cli_runner, metadata_schema_command_mocks):
        """Test get command with specific profile."""
        with metadata_schema_command_mocks as mocks:
            # Update profile name for this test
            mocks['profile_manager'].profile_name = 'production'
            mocks['api_client'].get_metadata_schema.return_value = {
                'message': {
                    'Items': []
                }
            }
            
            result = cli_runner.invoke(cli, [
                '--profile', 'production',
                'metadata-schema', 'get',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 0


class TestMetadataSchemaCommandsIntegration:
    """Test integration scenarios for metadata schema commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_database_id(self, mock_main_profile_manager):
        """Test that metadata schema commands require database ID where appropriate."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test get without database ID
        result = runner.invoke(cli, ['metadata-schema', 'get'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_authentication_error_handling(self, cli_runner, metadata_schema_command_mocks):
        """Test authentication error handling."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'get', '-d', 'test-database'])
            
            assert result.exit_code == 1
            # Check that the original exception is preserved
            assert isinstance(result.exception, AuthenticationError)


class TestMetadataSchemaJSONHandling:
    """Test JSON input/output handling for metadata schema commands."""
    
    def test_invalid_json_input_string(self, cli_runner, metadata_schema_command_mocks):
        """Test handling of invalid JSON input string."""
        with metadata_schema_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database',
                '--json-input', 'invalid json string'
            ])
            
            assert result.exit_code == 1  # Click exception error
            assert 'Invalid JSON input' in result.output
    
    def test_invalid_json_input_file(self, cli_runner, metadata_schema_command_mocks):
        """Test handling of invalid JSON input file."""
        with metadata_schema_command_mocks as mocks:
            with patch('builtins.open', mock_open(read_data='invalid json')):
                result = cli_runner.invoke(cli, [
                    'metadata-schema', 'get',
                    '-d', 'test-database',
                    '--json-input', 'invalid.json'
                ])
            
            assert result.exit_code == 1  # Click exception error
            assert 'Invalid JSON' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, metadata_schema_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with metadata_schema_command_mocks as mocks:
            with patch('builtins.open', side_effect=FileNotFoundError()):
                result = cli_runner.invoke(cli, [
                    'metadata-schema', 'get',
                    '-d', 'test-database',
                    '--json-input', 'nonexistent.json'
                ])
            
            assert result.exit_code == 1  # Click exception error
            assert 'Invalid JSON input' in result.output


class TestMetadataSchemaFormatting:
    """Test metadata schema output formatting."""
    
    def test_format_metadata_schema_output_cli(self):
        """Test CLI formatting of metadata schema output."""
        from vamscli.commands.metadata_schema import format_metadata_schema_output
        
        schema_data = {
            'message': {
                'Items': [
                    {
                        'field': 'title',
                        'datatype': 'string',
                        'required': True,
                        'dependsOn': []
                    },
                    {
                        'field': 'category',
                        'datatype': 'string',
                        'required': False,
                        'dependsOn': ['title']
                    },
                    {
                        'field': 'tags',
                        'datatype': 'array',
                        'required': False,
                        'dependsOn': ['title', 'category', 'priority', 'status']  # Long dependency list
                    }
                ],
                'NextToken': 'next-token'
            }
        }
        
        result = format_metadata_schema_output(schema_data)
        
        assert 'Metadata Schema for Database (3 field(s)):' in result
        assert 'Field Name' in result
        assert 'Data Type' in result
        assert 'Required' in result
        assert 'Depends On' in result
        assert 'title' in result
        assert 'category' in result
        assert 'tags' in result
        assert 'string' in result
        assert 'array' in result
        assert 'Yes' in result  # Required field
        assert 'No' in result   # Non-required field
        assert 'None' in result  # Empty dependencies
        assert 'title' in result  # Single dependency
        assert '...' in result  # Truncated long dependencies
        assert 'More results available' in result
        assert 'next-token' in result
    
    def test_format_metadata_schema_output_json(self):
        """Test JSON formatting of metadata schema output - now handled by output_result."""
        from vamscli.commands.metadata_schema import format_metadata_schema_output
        
        schema_data = {
            'message': {
                'Items': [
                    {
                        'field': 'title',
                        'datatype': 'string',
                        'required': True,
                        'dependsOn': []
                    }
                ]
            }
        }
        
        # The format function now only handles CLI output
        # JSON output is handled by output_result utility
        result = format_metadata_schema_output(schema_data)
        
        # Should return CLI-formatted string
        assert 'Metadata Schema for Database' in result
        assert 'title' in result
    
    def test_format_metadata_schema_output_empty(self):
        """Test formatting of empty metadata schema."""
        from vamscli.commands.metadata_schema import format_metadata_schema_output
        
        schema_data = {
            'message': {
                'Items': []
            }
        }
        
        result = format_metadata_schema_output(schema_data)
        
        assert result == "No metadata schema fields found for this database."
    
    def test_format_metadata_schema_output_malformed(self):
        """Test formatting of malformed metadata schema response."""
        from vamscli.commands.metadata_schema import format_metadata_schema_output
        
        # Test with missing message field
        schema_data = {}
        result = format_metadata_schema_output(schema_data)
        assert result == "No metadata schema fields found for this database."
        
        # Test with non-dict message field
        schema_data = {'message': 'not a dict'}
        result = format_metadata_schema_output(schema_data)
        assert result == "No metadata schema fields found for this database."


class TestMetadataSchemaUtilityFunctions:
    """Test utility functions for metadata schema commands."""
    
    def test_parse_json_input_function(self):
        """Test the parse_json_input utility function."""
        from vamscli.commands.metadata_schema import parse_json_input
        
        # Test valid JSON string
        json_string = '{"maxItems": 100, "pageSize": 50}'
        result = parse_json_input(json_string)
        assert result == {"maxItems": 100, "pageSize": 50}
        
        # Test valid JSON file
        json_data = {"maxItems": 200, "pageSize": 25}
        with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
            result = parse_json_input('test.json')
            assert result == json_data
        
        # Test invalid JSON string that's not a file
        with pytest.raises(click.BadParameter) as exc_info:
            parse_json_input('invalid json')
        assert 'Invalid JSON input' in str(exc_info.value)
        
        # Test nonexistent file
        with patch('builtins.open', side_effect=FileNotFoundError()):
            with pytest.raises(click.BadParameter) as exc_info:
                parse_json_input('nonexistent.json')
            assert 'Invalid JSON input' in str(exc_info.value)


if __name__ == '__main__':
    pytest.main([__file__])
