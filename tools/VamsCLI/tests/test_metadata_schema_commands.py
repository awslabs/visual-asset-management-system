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


class TestMetadataSchemaListCommand:
    """Test metadata schema list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['metadata-schema', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List metadata schemas with optional filters' in result.output
        assert '--database-id' in result.output
        assert '--entity-type' in result.output
        assert '--max-items' in result.output
        assert '--page-size' in result.output
        assert '--starting-token' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_list_all_schemas_success(self, cli_runner, metadata_schema_command_mocks):
        """Test successful listing of all metadata schemas."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-1',
                        'databaseId': 'db-1',
                        'schemaName': 'Asset Schema',
                        'metadataSchemaEntityType': 'assetMetadata',
                        'enabled': True,
                        'fields': {
                            'fields': [
                                {
                                    'metadataFieldKeyName': 'title',
                                    'metadataFieldValueType': 'string',
                                    'required': True
                                }
                            ]
                        },
                        'dateCreated': '2024-01-15T10:30:00Z'
                    },
                    {
                        'metadataSchemaId': 'schema-2',
                        'databaseId': 'db-1',
                        'schemaName': 'File Schema',
                        'metadataSchemaEntityType': 'fileMetadata',
                        'enabled': True,
                        'fields': {
                            'fields': [
                                {
                                    'metadataFieldKeyName': 'format',
                                    'metadataFieldValueType': 'string',
                                    'required': False
                                }
                            ]
                        },
                        'fileKeyTypeRestriction': '.pdf,.docx',
                        'dateCreated': '2024-01-16T11:00:00Z'
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list'])
            
            assert result.exit_code == 0
            assert 'Found 2 metadata schema(s):' in result.output
            assert 'schema-1' in result.output
            assert 'schema-2' in result.output
            assert 'Asset Schema' in result.output
            assert 'File Schema' in result.output
            assert 'assetMetadata' in result.output
            assert 'fileMetadata' in result.output
            assert 'Fields: 1' in result.output
            assert 'File Restrictions: .pdf,.docx' in result.output
            
            # Verify API call
            mocks['api_client'].list_metadata_schemas.assert_called_once_with(
                database_id=None,
                metadata_entity_type=None,
                max_items=1000,
                page_size=100,
                starting_token=None
            )
    
    def test_list_with_database_filter(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas filtered by database."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-1',
                        'databaseId': 'test-db',
                        'schemaName': 'Test Schema',
                        'metadataSchemaEntityType': 'assetMetadata',
                        'enabled': True,
                        'fields': {'fields': []}
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'list',
                '-d', 'test-db'
            ])
            
            assert result.exit_code == 0
            assert 'Found 1 metadata schema(s):' in result.output
            assert 'test-db' in result.output
            
            # Verify API call with database filter
            mocks['api_client'].list_metadata_schemas.assert_called_once_with(
                database_id='test-db',
                metadata_entity_type=None,
                max_items=1000,
                page_size=100,
                starting_token=None
            )
    
    def test_list_with_entity_type_filter(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas filtered by entity type."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-1',
                        'databaseId': 'db-1',
                        'schemaName': 'File Schema',
                        'metadataSchemaEntityType': 'fileMetadata',
                        'enabled': True,
                        'fields': {'fields': []}
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'list',
                '-e', 'fileMetadata'
            ])
            
            assert result.exit_code == 0
            assert 'fileMetadata' in result.output
            
            # Verify API call with entity type filter
            mocks['api_client'].list_metadata_schemas.assert_called_once_with(
                database_id=None,
                metadata_entity_type='fileMetadata',
                max_items=1000,
                page_size=100,
                starting_token=None
            )
    
    def test_list_with_both_filters(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with both database and entity type filters."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': []
            }
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'list',
                '-d', 'test-db',
                '-e', 'assetMetadata'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call with both filters
            mocks['api_client'].list_metadata_schemas.assert_called_once_with(
                database_id='test-db',
                metadata_entity_type='assetMetadata',
                max_items=1000,
                page_size=100,
                starting_token=None
            )
    
    def test_list_with_pagination(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with pagination parameters."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-1',
                        'databaseId': 'db-1',
                        'schemaName': 'Test Schema',
                        'metadataSchemaEntityType': 'assetMetadata',
                        'enabled': True,
                        'fields': {'fields': []}
                    }
                ],
                'NextToken': 'next-page-token'
            }
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'list',
                '--max-items', '50',
                '--page-size', '25',
                '--starting-token', 'test-token'
            ])
            
            assert result.exit_code == 0
            assert 'Next token: next-page-token' in result.output
            
            # Verify API call with pagination parameters
            mocks['api_client'].list_metadata_schemas.assert_called_once_with(
                database_id=None,
                metadata_entity_type=None,
                max_items=50,
                page_size=25,
                starting_token='test-token'
            )
    
    def test_list_json_output(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with JSON output."""
        with metadata_schema_command_mocks as mocks:
            api_response = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-1',
                        'databaseId': 'db-1',
                        'schemaName': 'Test Schema',
                        'metadataSchemaEntityType': 'assetMetadata',
                        'enabled': True,
                        'fields': {'fields': []}
                    }
                ]
            }
            mocks['api_client'].list_metadata_schemas.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'list',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert output_json == api_response
    
    def test_list_json_input_string(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with JSON input string."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': []
            }
            
            json_input = '{"databaseId": "test-db", "metadataEntityType": "assetMetadata", "maxItems": 100, "pageSize": 50}'
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'list',
                '--json-input', json_input
            ])
            
            assert result.exit_code == 0
            
            # Verify API call uses JSON input parameters
            mocks['api_client'].list_metadata_schemas.assert_called_once_with(
                database_id='test-db',
                metadata_entity_type='assetMetadata',
                max_items=100,
                page_size=50,
                starting_token=None
            )
    
    def test_list_json_input_file(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with JSON input file."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': []
            }
            
            json_data = {
                'databaseId': 'file-db',
                'metadataEntityType': 'fileMetadata',
                'maxItems': 200,
                'pageSize': 75,
                'startingToken': 'file-token'
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'metadata-schema', 'list',
                    '--json-input', 'params.json'
                ])
            
            assert result.exit_code == 0
            
            # Verify API call uses JSON file parameters
            mocks['api_client'].list_metadata_schemas.assert_called_once_with(
                database_id='file-db',
                metadata_entity_type='fileMetadata',
                max_items=200,
                page_size=75,
                starting_token='file-token'
            )
    
    def test_list_empty_result(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with empty result."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': []
            }
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list'])
            
            assert result.exit_code == 0
            assert 'No metadata schemas found.' in result.output
    
    def test_list_database_not_found(self, cli_runner, metadata_schema_command_mocks):
        """Test list command with database not found error."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.side_effect = DatabaseNotFoundError(
                "Database 'nonexistent-db' not found"
            )
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'list',
                '-d', 'nonexistent-db'
            ])
            
            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert 'vamscli database list' in result.output
    
    def test_list_authentication_error(self, cli_runner, metadata_schema_command_mocks):
        """Test list command with authentication error."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list'])
            
            assert result.exit_code == 1
            # Check that the original exception is preserved
            assert isinstance(result.exception, AuthenticationError)
    
    def test_list_api_error(self, cli_runner, metadata_schema_command_mocks):
        """Test list command with general API error."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list'])
            
            assert result.exit_code == 1
            assert '✗ API Error' in result.output
            assert 'API request failed' in result.output
    
    def test_list_no_setup(self, cli_runner, metadata_schema_no_setup_mocks):
        """Test list command without setup."""
        with metadata_schema_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['metadata-schema', 'list'])
            
            assert result.exit_code == 1
            # Check that setup required exception is preserved
            assert isinstance(result.exception, SetupRequiredError)
    
    def test_list_with_profile(self, cli_runner, metadata_schema_command_mocks):
        """Test list command with specific profile."""
        with metadata_schema_command_mocks as mocks:
            # Update profile name for this test
            mocks['profile_manager'].profile_name = 'production'
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': []
            }
            
            result = cli_runner.invoke(cli, [
                '--profile', 'production',
                'metadata-schema', 'list'
            ])
            
            assert result.exit_code == 0


class TestMetadataSchemaGetCommand:
    """Test metadata schema get command."""
    
    def test_get_help(self, cli_runner):
        """Test get command help."""
        result = cli_runner.invoke(cli, ['metadata-schema', 'get', '--help'])
        assert result.exit_code == 0
        assert 'Get a specific metadata schema by ID' in result.output
        assert '--database-id' in result.output
        assert '--schema-id' in result.output
        assert '--json-output' in result.output
    
    def test_get_success(self, cli_runner, metadata_schema_command_mocks):
        """Test successful metadata schema retrieval by ID."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema_by_id.return_value = {
                'metadataSchemaId': 'schema-123',
                'databaseId': 'test-database',
                'schemaName': 'Comprehensive Schema',
                'metadataSchemaEntityType': 'assetMetadata',
                'enabled': True,
                'fields': {
                    'fields': [
                        {
                            'metadataFieldKeyName': 'title',
                            'metadataFieldValueType': 'string',
                            'required': True,
                            'defaultMetadataFieldValue': 'Untitled'
                        },
                        {
                            'metadataFieldKeyName': 'category',
                            'metadataFieldValueType': 'string',
                            'required': False,
                            'dependsOnFieldKeyName': ['title']
                        },
                        {
                            'metadataFieldKeyName': 'priority',
                            'metadataFieldValueType': 'number',
                            'required': True,
                            'dependsOnFieldKeyName': ['category', 'title']
                        },
                        {
                            'metadataFieldKeyName': 'status',
                            'metadataFieldValueType': 'inline_controlled_list',
                            'required': True,
                            'controlledListKeys': ['draft', 'review', 'approved', 'published'],
                            'defaultMetadataFieldValue': 'draft'
                        }
                    ]
                },
                'dateCreated': '2024-01-15T10:30:00Z',
                'dateModified': '2024-01-20T14:45:00Z',
                'createdBy': 'user@example.com',
                'modifiedBy': 'admin@example.com'
            }
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database',
                '-s', 'schema-123'
            ])
            
            assert result.exit_code == 0
            assert 'Metadata Schema Details:' in result.output
            assert 'schema-123' in result.output
            assert 'test-database' in result.output
            assert 'Comprehensive Schema' in result.output
            assert 'assetMetadata' in result.output
            assert 'Fields (4):' in result.output
            assert 'title' in result.output
            assert 'category' in result.output
            assert 'priority' in result.output
            assert 'status' in result.output
            assert 'string' in result.output
            assert 'number' in result.output
            assert 'inline_controlled_list' in result.output
            assert 'Yes' in result.output  # Required field
            assert 'No' in result.output   # Non-required field
            assert 'Depends on:' in result.output
            assert 'Allowed values:' in result.output
            assert 'draft, review, approved, published' in result.output
            
            # Verify API call
            mocks['api_client'].get_metadata_schema_by_id.assert_called_once_with(
                'test-database',
                'schema-123'
            )
    
    def test_get_json_output(self, cli_runner, metadata_schema_command_mocks):
        """Test get command with JSON output."""
        with metadata_schema_command_mocks as mocks:
            api_response = {
                'metadataSchemaId': 'schema-123',
                'databaseId': 'test-database',
                'schemaName': 'Test Schema',
                'metadataSchemaEntityType': 'assetMetadata',
                'enabled': True,
                'fields': {
                    'fields': [
                        {
                            'metadataFieldKeyName': 'title',
                            'metadataFieldValueType': 'string',
                            'required': True
                        }
                    ]
                }
            }
            mocks['api_client'].get_metadata_schema_by_id.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database',
                '-s', 'schema-123',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert output_json == api_response
    
    def test_get_schema_not_found(self, cli_runner, metadata_schema_command_mocks):
        """Test get command with schema not found error."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema_by_id.side_effect = APIError(
                "Metadata schema 'nonexistent-schema' not found"
            )
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database',
                '-s', 'nonexistent-schema'
            ])
            
            assert result.exit_code == 1
            assert '✗ Metadata Schema Not Found' in result.output
            assert 'vamscli metadata-schema list' in result.output
    
    def test_get_database_not_found(self, cli_runner, metadata_schema_command_mocks):
        """Test get command with database not found error."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema_by_id.side_effect = DatabaseNotFoundError(
                "Database 'nonexistent-db' not found"
            )
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'nonexistent-db',
                '-s', 'schema-123'
            ])
            
            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert 'vamscli database list' in result.output
    
    def test_get_authentication_error(self, cli_runner, metadata_schema_command_mocks):
        """Test get command with authentication error."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].get_metadata_schema_by_id.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database',
                '-s', 'schema-123'
            ])
            
            assert result.exit_code == 1
            # Check that the original exception is preserved
            assert isinstance(result.exception, AuthenticationError)
    
    def test_get_no_setup(self, cli_runner, metadata_schema_no_setup_mocks):
        """Test get command without setup."""
        with metadata_schema_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'get',
                '-d', 'test-database',
                '-s', 'schema-123'
            ])
            
            assert result.exit_code == 1
            # Check that setup required exception is preserved
            assert isinstance(result.exception, SetupRequiredError)


class TestMetadataSchemaCommandsIntegration:
    """Test integration scenarios for metadata schema commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_parameters(self, mock_main_profile_manager):
        """Test that metadata schema commands require appropriate parameters."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test get without database ID
        result = runner.invoke(cli, ['metadata-schema', 'get', '-s', 'schema-123'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test get without schema ID
        result = runner.invoke(cli, ['metadata-schema', 'get', '-d', 'test-db'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_authentication_error_handling(self, cli_runner, metadata_schema_command_mocks):
        """Test authentication error handling."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list'])
            
            assert result.exit_code == 1
            # Check that the original exception is preserved
            assert isinstance(result.exception, AuthenticationError)


class TestMetadataSchemaJSONHandling:
    """Test JSON input/output handling for metadata schema commands."""
    
    def test_invalid_json_input_string(self, cli_runner, metadata_schema_command_mocks):
        """Test handling of invalid JSON input string."""
        with metadata_schema_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'list',
                '--json-input', 'invalid json string'
            ])
            
            assert result.exit_code == 1
            assert 'Invalid JSON input' in result.output
    
    def test_invalid_json_input_file(self, cli_runner, metadata_schema_command_mocks):
        """Test handling of invalid JSON input file."""
        with metadata_schema_command_mocks as mocks:
            with patch('builtins.open', mock_open(read_data='invalid json')):
                result = cli_runner.invoke(cli, [
                    'metadata-schema', 'list',
                    '--json-input', 'invalid.json'
                ])
            
            assert result.exit_code == 1
            assert 'Invalid JSON' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, metadata_schema_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with metadata_schema_command_mocks as mocks:
            with patch('builtins.open', side_effect=FileNotFoundError()):
                result = cli_runner.invoke(cli, [
                    'metadata-schema', 'list',
                    '--json-input', 'nonexistent.json'
                ])
            
            assert result.exit_code == 1
            assert 'Invalid JSON input' in result.output


class TestMetadataSchemaFormatting:
    """Test metadata schema output formatting."""
    
    def test_format_list_output_cli(self):
        """Test CLI formatting of metadata schemas list."""
        from vamscli.commands.metadata_schema import format_metadata_schema_list_output
        
        schemas_data = {
            'Items': [
                {
                    'metadataSchemaId': 'schema-1',
                    'databaseId': 'db-1',
                    'schemaName': 'Asset Schema',
                    'metadataSchemaEntityType': 'assetMetadata',
                    'enabled': True,
                    'fields': {
                        'fields': [
                            {'metadataFieldKeyName': 'title', 'metadataFieldValueType': 'string', 'required': True},
                            {'metadataFieldKeyName': 'category', 'metadataFieldValueType': 'string', 'required': False}
                        ]
                    },
                    'dateCreated': '2024-01-15T10:30:00Z'
                },
                {
                    'metadataSchemaId': 'schema-2',
                    'databaseId': 'db-2',
                    'schemaName': 'File Schema',
                    'metadataSchemaEntityType': 'fileMetadata',
                    'enabled': False,
                    'fields': {
                        'fields': [
                            {'metadataFieldKeyName': 'format', 'metadataFieldValueType': 'string', 'required': True}
                        ]
                    },
                    'fileKeyTypeRestriction': '.pdf,.docx',
                    'dateCreated': '2024-01-16T11:00:00Z',
                    'dateModified': '2024-01-17T12:00:00Z'
                }
            ],
            'NextToken': 'next-token'
        }
        
        result = format_metadata_schema_list_output(schemas_data)
        
        assert 'Found 2 metadata schema(s):' in result
        assert 'schema-1' in result
        assert 'schema-2' in result
        assert 'Asset Schema' in result
        assert 'File Schema' in result
        assert 'assetMetadata' in result
        assert 'fileMetadata' in result
        assert 'Fields: 2' in result
        assert 'Fields: 1' in result
        assert 'File Restrictions: .pdf,.docx' in result
        assert 'Next token: next-token' in result
    
    def test_format_detail_output_cli(self):
        """Test CLI formatting of single metadata schema details."""
        from vamscli.commands.metadata_schema import format_metadata_schema_detail_output
        
        schema_data = {
            'metadataSchemaId': 'schema-123',
            'databaseId': 'test-db',
            'schemaName': 'Detailed Schema',
            'metadataSchemaEntityType': 'assetMetadata',
            'enabled': True,
            'fields': {
                'fields': [
                    {
                        'metadataFieldKeyName': 'title',
                        'metadataFieldValueType': 'string',
                        'required': True,
                        'defaultMetadataFieldValue': 'Default Title'
                    },
                    {
                        'metadataFieldKeyName': 'tags',
                        'metadataFieldValueType': 'array',
                        'required': False,
                        'dependsOnFieldKeyName': ['title', 'category']
                    },
                    {
                        'metadataFieldKeyName': 'status',
                        'metadataFieldValueType': 'inline_controlled_list',
                        'required': True,
                        'controlledListKeys': ['draft', 'review', 'approved'],
                        'defaultMetadataFieldValue': 'draft'
                    }
                ]
            },
            'fileKeyTypeRestriction': '.glb,.gltf',
            'dateCreated': '2024-01-15T10:30:00Z',
            'dateModified': '2024-01-20T14:45:00Z',
            'createdBy': 'user@example.com',
            'modifiedBy': 'admin@example.com'
        }
        
        result = format_metadata_schema_detail_output(schema_data)
        
        assert 'Metadata Schema Details:' in result
        assert 'schema-123' in result
        assert 'test-db' in result
        assert 'Detailed Schema' in result
        assert 'assetMetadata' in result
        assert 'Fields (3):' in result
        assert 'title' in result
        assert 'tags' in result
        assert 'status' in result
        assert 'string' in result
        assert 'array' in result
        assert 'inline_controlled_list' in result
        assert 'Depends on:' in result
        assert 'Allowed values:' in result
        assert 'File Restrictions: .glb,.gltf' in result
    
    def test_format_list_output_empty(self):
        """Test formatting of empty metadata schemas list."""
        from vamscli.commands.metadata_schema import format_metadata_schema_list_output
        
        schemas_data = {'Items': []}
        result = format_metadata_schema_list_output(schemas_data)
        assert result == "No metadata schemas found."
    
    def test_format_detail_output_no_fields(self):
        """Test formatting of schema with no fields."""
        from vamscli.commands.metadata_schema import format_metadata_schema_detail_output
        
        schema_data = {
            'metadataSchemaId': 'schema-123',
            'databaseId': 'test-db',
            'schemaName': 'Empty Schema',
            'metadataSchemaEntityType': 'assetMetadata',
            'enabled': True,
            'fields': {'fields': []}
        }
        
        result = format_metadata_schema_detail_output(schema_data)
        assert 'No fields defined.' in result


class TestMetadataSchemaUtilityFunctions:
    """Test utility functions for metadata schema commands."""
    
    def test_parse_json_input_function(self):
        """Test the parse_json_input utility function."""
        from vamscli.commands.metadata_schema import parse_json_input
        
        # Test valid JSON string
        json_string = '{"databaseId": "test-db", "maxItems": 100}'
        result = parse_json_input(json_string)
        assert result == {"databaseId": "test-db", "maxItems": 100}
        
        # Test valid JSON file
        json_data = {"databaseId": "file-db", "pageSize": 50}
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
    
    def test_parse_json_input_empty(self):
        """Test parse_json_input with empty or None input."""
        from vamscli.commands.metadata_schema import parse_json_input
        
        # Test None
        result = parse_json_input(None)
        assert result == {}
        
        # Test empty string
        result = parse_json_input('')
        assert result == {}


class TestMetadataSchemaEntityTypes:
    """Test entity type filtering for metadata schemas."""
    
    def test_list_databaseMetadata_entity_type(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with databaseMetadata entity type."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-databaseMetadata',
                        'databaseId': 'test-db',
                        'schemaName': 'databaseMetadata Schema',
                        'metadataSchemaEntityType': 'databaseMetadata',
                        'enabled': True,
                        'fields': {'fields': []}
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list', '-e', 'databaseMetadata'])
            
            assert result.exit_code == 0
            assert 'databaseMetadata' in result.output
    
    def test_list_assetMetadata_entity_type(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with assetMetadata entity type."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-assetMetadata',
                        'databaseId': 'test-db',
                        'schemaName': 'assetMetadata Schema',
                        'metadataSchemaEntityType': 'assetMetadata',
                        'enabled': True,
                        'fields': {'fields': []}
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list', '-e', 'assetMetadata'])
            
            assert result.exit_code == 0
            assert 'assetMetadata' in result.output
    
    def test_list_fileMetadata_entity_type(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with fileMetadata entity type."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-fileMetadata',
                        'databaseId': 'test-db',
                        'schemaName': 'fileMetadata Schema',
                        'metadataSchemaEntityType': 'fileMetadata',
                        'enabled': True,
                        'fields': {'fields': []}
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list', '-e', 'fileMetadata'])
            
            assert result.exit_code == 0
            assert 'fileMetadata' in result.output
    
    def test_list_fileAttribute_entity_type(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with fileAttribute entity type."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-fileAttribute',
                        'databaseId': 'test-db',
                        'schemaName': 'fileAttribute Schema',
                        'metadataSchemaEntityType': 'fileAttribute',
                        'enabled': True,
                        'fields': {'fields': []}
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list', '-e', 'fileAttribute'])
            
            assert result.exit_code == 0
            assert 'fileAttribute' in result.output
    
    def test_list_assetLinkMetadata_entity_type(self, cli_runner, metadata_schema_command_mocks):
        """Test listing schemas with assetLinkMetadata entity type."""
        with metadata_schema_command_mocks as mocks:
            mocks['api_client'].list_metadata_schemas.return_value = {
                'Items': [
                    {
                        'metadataSchemaId': 'schema-assetLinkMetadata',
                        'databaseId': 'test-db',
                        'schemaName': 'assetLinkMetadata Schema',
                        'metadataSchemaEntityType': 'assetLinkMetadata',
                        'enabled': True,
                        'fields': {'fields': []}
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, ['metadata-schema', 'list', '-e', 'assetLinkMetadata'])
            
            assert result.exit_code == 0
            assert 'assetLinkMetadata' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
