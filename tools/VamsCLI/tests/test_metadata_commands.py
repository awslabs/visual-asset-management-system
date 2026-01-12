"""Test unified metadata management commands for all entity types."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError, InvalidDatabaseDataError,
    AssetLinkNotFoundError, AssetLinkValidationError, AssetLinkPermissionError,
    APIError, AuthenticationError, SetupRequiredError
)


# File-level fixtures for metadata-specific testing patterns
@pytest.fixture
def metadata_command_mocks(generic_command_mocks):
    """Provide metadata-specific command mocks."""
    return generic_command_mocks('metadata')


@pytest.fixture
def metadata_no_setup_mocks(no_setup_command_mocks):
    """Provide metadata command mocks for no-setup scenarios."""
    return no_setup_command_mocks('metadata')


#######################
# Asset Metadata Tests
#######################

class TestAssetMetadataListCommand:
    """Test metadata asset list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['metadata', 'asset', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all metadata for an asset' in result.output
        assert '--database-id' in result.output
        assert '--asset-id' in result.output
        assert '--json-output' in result.output
    
    def test_list_success(self, cli_runner, metadata_command_mocks):
        """Test successful asset metadata listing."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_metadata_v2.return_value = {
                'metadata': [
                    {
                        'databaseId': 'test-db',
                        'assetId': 'test-asset',
                        'metadataKey': 'title',
                        'metadataValue': 'Test Asset',
                        'metadataValueType': 'string'
                    },
                    {
                        'databaseId': 'test-db',
                        'assetId': 'test-asset',
                        'metadataKey': 'version',
                        'metadataValue': '1',
                        'metadataValueType': 'number'
                    }
                ],
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, ['metadata', 'asset', 'list', '-d', 'test-db', '-a', 'test-asset'])
            
            assert result.exit_code == 0
            assert 'Metadata (2 items)' in result.output
            assert 'title' in result.output
            assert 'Test Asset' in result.output
            
            # Verify API call
            mocks['api_client'].get_asset_metadata_v2.assert_called_once_with('test-db', 'test-asset', 3000, None)
    
    def test_list_json_output(self, cli_runner, metadata_command_mocks):
        """Test asset metadata listing with JSON output."""
        with metadata_command_mocks as mocks:
            api_response = {
                'metadata': [
                    {
                        'metadataKey': 'test_key',
                        'metadataValue': 'test_value',
                        'metadataValueType': 'string'
                    }
                ],
                'message': 'Success'
            }
            mocks['api_client'].get_asset_metadata_v2.return_value = api_response
            
            result = cli_runner.invoke(cli, ['metadata', 'asset', 'list', '-d', 'test-db', '-a', 'test-asset', '--json-output'])
            
            assert result.exit_code == 0
            output_json = json.loads(result.output)
            assert output_json == api_response
    
    def test_list_no_setup(self, cli_runner, metadata_no_setup_mocks):
        """Test list command without setup."""
        with metadata_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['metadata', 'asset', 'list', '-d', 'test-db', '-a', 'test-asset'])
            
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)


class TestAssetMetadataUpdateCommand:
    """Test metadata asset update command."""
    
    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(cli, ['metadata', 'asset', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Create or update metadata for an asset' in result.output
        assert '--database-id' in result.output
        assert '--asset-id' in result.output
        assert '--json-input' in result.output
        assert '--update-type' in result.output
        assert '--json-output' in result.output
    
    def test_update_success(self, cli_runner, metadata_command_mocks):
        """Test successful asset metadata update."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_metadata_v2.return_value = {
                'success': True,
                'totalItems': 2,
                'successCount': 2,
                'failureCount': 0,
                'successfulItems': ['title', 'version'],
                'failedItems': [],
                'message': 'Upserted 2 of 2 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {
                'metadata': [
                    {'metadataKey': 'title', 'metadataValue': 'Updated Asset', 'metadataValueType': 'string'},
                    {'metadataKey': 'version', 'metadataValue': '2', 'metadataValueType': 'number'}
                ]
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'asset', 'update',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--json-input', '@metadata.json'
                    ])
            
            assert result.exit_code == 0
            assert '✓ Asset metadata updated successfully!' in result.output
            assert 'Total Items: 2' in result.output
            assert 'Successful: 2' in result.output
            
            # Verify API call
            mocks['api_client'].update_asset_metadata_v2.assert_called_once()
            call_args = mocks['api_client'].update_asset_metadata_v2.call_args
            assert call_args[0][0] == 'test-db'
            assert call_args[0][1] == 'test-asset'
            assert call_args[0][3] == 'update'  # Default update type
    
    def test_update_replace_all_mode(self, cli_runner, metadata_command_mocks):
        """Test asset metadata update with replace_all mode."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_metadata_v2.return_value = {
                'success': True,
                'totalItems': 1,
                'successCount': 1,
                'failureCount': 0,
                'successfulItems': ['title'],
                'failedItems': [],
                'message': 'Replaced all metadata: deleted 2 keys, upserted 1 keys',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {
                'metadata': [
                    {'metadataKey': 'title', 'metadataValue': 'New Asset', 'metadataValueType': 'string'}
                ]
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'asset', 'update',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--json-input', '@metadata.json',
                        '--update-type', 'replace_all'
                    ])
            
            assert result.exit_code == 0
            assert '✓ Asset metadata updated successfully!' in result.output
            
            # Verify API call with replace_all mode
            call_args = mocks['api_client'].update_asset_metadata_v2.call_args
            assert call_args[0][3] == 'replace_all'


class TestAssetMetadataDeleteCommand:
    """Test metadata asset delete command."""
    
    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(cli, ['metadata', 'asset', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete metadata for an asset' in result.output
        assert '--database-id' in result.output
        assert '--asset-id' in result.output
        assert '--json-input' in result.output
    
    def test_delete_success(self, cli_runner, metadata_command_mocks):
        """Test successful asset metadata deletion."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].delete_asset_metadata_v2.return_value = {
                'success': True,
                'totalItems': 2,
                'successCount': 2,
                'failureCount': 0,
                'successfulItems': ['old_field', 'deprecated'],
                'failedItems': [],
                'message': 'Deleted 2 of 2 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {'metadataKeys': ['old_field', 'deprecated']}
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'asset', 'delete',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--json-input', '@delete-keys.json'
                    ])
            
            assert result.exit_code == 0
            assert '✓ Asset metadata deleted successfully!' in result.output
            assert 'Total Items: 2' in result.output
            assert 'Successful: 2' in result.output
            
            # Verify API call
            mocks['api_client'].delete_asset_metadata_v2.assert_called_once_with('test-db', 'test-asset', ['old_field', 'deprecated'])


#######################
# File Metadata Tests
#######################

class TestFileMetadataListCommand:
    """Test metadata file list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['metadata', 'file', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all metadata or attributes for a file' in result.output
        assert '--file-path' in result.output
        assert '--type' in result.output
    
    def test_list_metadata_success(self, cli_runner, metadata_command_mocks):
        """Test successful file metadata listing."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_file_metadata_v2.return_value = {
                'metadata': [
                    {
                        'metadataKey': 'format',
                        'metadataValue': 'gltf',
                        'metadataValueType': 'string'
                    }
                ],
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, [
                'metadata', 'file', 'list',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--file-path', 'models/file.gltf',
                '--type', 'metadata'
            ])
            
            assert result.exit_code == 0
            assert 'Metadata (1 items)' in result.output
            assert 'format' in result.output
            
            # Verify API call
            mocks['api_client'].get_file_metadata_v2.assert_called_once_with(
                'test-db', 'test-asset', 'models/file.gltf', 'metadata', 3000, None
            )
    
    def test_list_attribute_success(self, cli_runner, metadata_command_mocks):
        """Test successful file attribute listing."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_file_metadata_v2.return_value = {
                'metadata': [
                    {
                        'metadataKey': 'extracted_property',
                        'metadataValue': 'value',
                        'metadataValueType': 'string'
                    }
                ],
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, [
                'metadata', 'file', 'list',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--file-path', 'models/file.gltf',
                '--type', 'attribute'
            ])
            
            assert result.exit_code == 0
            assert 'Metadata (1 items)' in result.output


class TestFileMetadataUpdateCommand:
    """Test metadata file update command."""
    
    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(cli, ['metadata', 'file', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Create or update metadata/attributes for a file' in result.output
        assert '--file-path' in result.output
        assert '--type' in result.output
        assert '--update-type' in result.output
    
    def test_update_metadata_success(self, cli_runner, metadata_command_mocks):
        """Test successful file metadata update."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].update_file_metadata_v2.return_value = {
                'success': True,
                'totalItems': 1,
                'successCount': 1,
                'failureCount': 0,
                'successfulItems': ['format'],
                'failedItems': [],
                'message': 'Upserted 1 of 1 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {
                'metadata': [
                    {'metadataKey': 'format', 'metadataValue': 'gltf', 'metadataValueType': 'string'}
                ]
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'file', 'update',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--file-path', 'models/file.gltf',
                        '--type', 'metadata',
                        '--json-input', '@metadata.json'
                    ])
            
            assert result.exit_code == 0
            assert '✓ File metadata updated successfully!' in result.output


class TestFileMetadataDeleteCommand:
    """Test metadata file delete command."""
    
    def test_delete_success(self, cli_runner, metadata_command_mocks):
        """Test successful file metadata deletion."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].delete_file_metadata_v2.return_value = {
                'success': True,
                'totalItems': 1,
                'successCount': 1,
                'failureCount': 0,
                'successfulItems': ['old_field'],
                'failedItems': [],
                'message': 'Deleted 1 of 1 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {'metadataKeys': ['old_field']}
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'file', 'delete',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--file-path', 'models/file.gltf',
                        '--type', 'metadata',
                        '--json-input', '@delete-keys.json'
                    ])
            
            assert result.exit_code == 0
            assert '✓ File metadata deleted successfully!' in result.output


#######################
# Asset Link Metadata Tests
#######################

class TestAssetLinkMetadataListCommand:
    """Test metadata asset-link list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['metadata', 'asset-link', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all metadata for an asset link' in result.output
        assert '--asset-link-id' in result.output
    
    def test_list_success(self, cli_runner, metadata_command_mocks):
        """Test successful asset link metadata listing."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_link_metadata_v2.return_value = {
                'metadata': [
                    {
                        'assetLinkId': 'abc123-def456',
                        'metadataKey': 'relationship',
                        'metadataValue': 'parent-child',
                        'metadataValueType': 'string'
                    }
                ],
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, ['metadata', 'asset-link', 'list', '--asset-link-id', 'abc123-def456'])
            
            assert result.exit_code == 0
            assert 'Metadata (1 items)' in result.output
            assert 'relationship' in result.output
            
            # Verify API call
            mocks['api_client'].get_asset_link_metadata_v2.assert_called_once_with('abc123-def456', 3000, None)


class TestAssetLinkMetadataUpdateCommand:
    """Test metadata asset-link update command."""
    
    def test_update_success(self, cli_runner, metadata_command_mocks):
        """Test successful asset link metadata update."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_link_metadata_v2.return_value = {
                'success': True,
                'totalItems': 1,
                'successCount': 1,
                'failureCount': 0,
                'successfulItems': ['Matrix'],
                'failedItems': [],
                'message': 'Upserted 1 of 1 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {
                'metadata': [
                    {'metadataKey': 'Matrix', 'metadataValue': '[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]', 'metadataValueType': 'matrix4x4'}
                ]
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'asset-link', 'update',
                        '--asset-link-id', 'abc123-def456',
                        '--json-input', '@metadata.json'
                    ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata updated successfully!' in result.output


class TestAssetLinkMetadataDeleteCommand:
    """Test metadata asset-link delete command."""
    
    def test_delete_success(self, cli_runner, metadata_command_mocks):
        """Test successful asset link metadata deletion."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].delete_asset_link_metadata_v2.return_value = {
                'success': True,
                'totalItems': 1,
                'successCount': 1,
                'failureCount': 0,
                'successfulItems': ['old_field'],
                'failedItems': [],
                'message': 'Deleted 1 of 1 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {'metadataKeys': ['old_field']}
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'asset-link', 'delete',
                        '--asset-link-id', 'abc123-def456',
                        '--json-input', '@delete-keys.json'
                    ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata deleted successfully!' in result.output


#######################
# Database Metadata Tests
#######################

class TestDatabaseMetadataListCommand:
    """Test metadata database list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['metadata', 'database', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all metadata for a database' in result.output
        assert '--database-id' in result.output
    
    def test_list_success(self, cli_runner, metadata_command_mocks):
        """Test successful database metadata listing."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_database_metadata_v2.return_value = {
                'metadata': [
                    {
                        'databaseId': 'test-db',
                        'metadataKey': 'owner',
                        'metadataValue': 'admin',
                        'metadataValueType': 'string'
                    }
                ],
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, ['metadata', 'database', 'list', '-d', 'test-db'])
            
            assert result.exit_code == 0
            assert 'Metadata (1 items)' in result.output
            assert 'owner' in result.output
            
            # Verify API call
            mocks['api_client'].get_database_metadata_v2.assert_called_once_with('test-db', 3000, None)


class TestDatabaseMetadataUpdateCommand:
    """Test metadata database update command."""
    
    def test_update_success(self, cli_runner, metadata_command_mocks):
        """Test successful database metadata update."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].update_database_metadata_v2.return_value = {
                'success': True,
                'totalItems': 1,
                'successCount': 1,
                'failureCount': 0,
                'successfulItems': ['owner'],
                'failedItems': [],
                'message': 'Upserted 1 of 1 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {
                'metadata': [
                    {'metadataKey': 'owner', 'metadataValue': 'new_admin', 'metadataValueType': 'string'}
                ]
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'database', 'update',
                        '-d', 'test-db',
                        '--json-input', '@metadata.json'
                    ])
            
            assert result.exit_code == 0
            assert '✓ Database metadata updated successfully!' in result.output


class TestDatabaseMetadataDeleteCommand:
    """Test metadata database delete command."""
    
    def test_delete_success(self, cli_runner, metadata_command_mocks):
        """Test successful database metadata deletion."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].delete_database_metadata_v2.return_value = {
                'success': True,
                'totalItems': 1,
                'successCount': 1,
                'failureCount': 0,
                'successfulItems': ['old_field'],
                'failedItems': [],
                'message': 'Deleted 1 of 1 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {'metadataKeys': ['old_field']}
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'database', 'delete',
                        '-d', 'test-db',
                        '--json-input', '@delete-keys.json'
                    ])
            
            assert result.exit_code == 0
            assert '✓ Database metadata deleted successfully!' in result.output


#######################
# Helper Function Tests
#######################

class TestMetadataHelperFunctions:
    """Test metadata helper functions."""
    
    def test_load_json_input_file(self):
        """Test load_json_input with file."""
        from vamscli.commands.metadata import load_json_input
        
        json_data = {'metadata': [{'metadataKey': 'test', 'metadataValue': 'value', 'metadataValueType': 'string'}]}
        
        with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
            with patch('pathlib.Path.exists', return_value=True):
                result = load_json_input('@test.json')
        
        assert result == json_data
    
    def test_load_json_input_string(self):
        """Test load_json_input with JSON string."""
        from vamscli.commands.metadata import load_json_input
        
        json_string = '{"metadata": [{"metadataKey": "test", "metadataValue": "value", "metadataValueType": "string"}]}'
        result = load_json_input(json_string)
        
        assert result == json.loads(json_string)
    
    def test_load_json_input_invalid_json(self):
        """Test load_json_input with invalid JSON."""
        from vamscli.commands.metadata import load_json_input
        
        with pytest.raises(click.ClickException) as exc_info:
            load_json_input('invalid json')
        
        assert 'Invalid JSON input' in str(exc_info.value)
    
    def test_load_json_input_file_not_found(self):
        """Test load_json_input with non-existent file."""
        from vamscli.commands.metadata import load_json_input
        
        with patch('pathlib.Path.exists', return_value=False):
            with pytest.raises(click.ClickException) as exc_info:
                load_json_input('@nonexistent.json')
        
        assert 'JSON input file not found' in str(exc_info.value)
    
    def test_format_metadata_list_empty(self):
        """Test format_metadata_list with empty list."""
        from vamscli.commands.metadata import format_metadata_list
        
        result = format_metadata_list([], 'asset')
        assert 'No metadata found for this asset' in result
    
    def test_format_metadata_list_with_items(self):
        """Test format_metadata_list with items."""
        from vamscli.commands.metadata import format_metadata_list
        
        metadata_list = [
            {
                'metadataKey': 'title',
                'metadataValue': 'Test Asset',
                'metadataValueType': 'string'
            },
            {
                'metadataKey': 'version',
                'metadataValue': '1',
                'metadataValueType': 'number'
            }
        ]
        
        result = format_metadata_list(metadata_list, 'asset')
        assert 'Metadata (2 items)' in result
        assert 'Key: title' in result
        assert 'Value: Test Asset' in result
        assert 'Type: string' in result
    
    def test_format_bulk_operation_result(self):
        """Test format_bulk_operation_result."""
        from vamscli.commands.metadata import format_bulk_operation_result
        
        result_data = {
            'totalItems': 3,
            'successCount': 2,
            'failureCount': 1,
            'successfulItems': ['key1', 'key2'],
            'failedItems': [{'key': 'key3', 'error': 'Validation failed'}]
        }
        
        result = format_bulk_operation_result(result_data, 'updated')
        assert 'Total Items: 3' in result
        assert 'Successful: 2' in result
        assert 'Failed: 1' in result
        assert 'Successfully updated:' in result
        assert '• key1' in result
        assert '• key2' in result
        assert 'Failed items:' in result
        assert '• key3: Validation failed' in result


#######################
# Integration Tests
#######################

class TestMetadataCommandsIntegration:
    """Test integration scenarios for metadata commands."""
    
    def test_metadata_group_help(self, cli_runner):
        """Test metadata group help."""
        result = cli_runner.invoke(cli, ['metadata', '--help'])
        assert result.exit_code == 0
        assert 'Metadata management commands for all entity types' in result.output
        assert 'asset' in result.output
        assert 'file' in result.output
        assert 'asset-link' in result.output
        assert 'database' in result.output
    
    def test_all_entity_types_have_three_operations(self, cli_runner):
        """Test that all entity types have list, update, and delete operations."""
        # Test asset
        result = cli_runner.invoke(cli, ['metadata', 'asset', '--help'])
        assert result.exit_code == 0
        assert 'list' in result.output
        assert 'update' in result.output
        assert 'delete' in result.output
        
        # Test file
        result = cli_runner.invoke(cli, ['metadata', 'file', '--help'])
        assert result.exit_code == 0
        assert 'list' in result.output
        assert 'update' in result.output
        assert 'delete' in result.output
        
        # Test asset-link
        result = cli_runner.invoke(cli, ['metadata', 'asset-link', '--help'])
        assert result.exit_code == 0
        assert 'list' in result.output
        assert 'update' in result.output
        assert 'delete' in result.output
        
        # Test database
        result = cli_runner.invoke(cli, ['metadata', 'database', '--help'])
        assert result.exit_code == 0
        assert 'list' in result.output
        assert 'update' in result.output
        assert 'delete' in result.output
    
    def test_inline_json_input(self, cli_runner, metadata_command_mocks):
        """Test commands with inline JSON input."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_metadata_v2.return_value = {
                'success': True,
                'totalItems': 1,
                'successCount': 1,
                'failureCount': 0,
                'successfulItems': ['title'],
                'failedItems': [],
                'message': 'Upserted 1 of 1 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_string = '{"metadata":[{"metadataKey":"title","metadataValue":"Test","metadataValueType":"string"}]}'
            
            result = cli_runner.invoke(cli, [
                'metadata', 'asset', 'update',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--json-input', json_string
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset metadata updated successfully!' in result.output
    
    def test_bulk_operation_partial_failure(self, cli_runner, metadata_command_mocks):
        """Test bulk operation with partial failures."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_metadata_v2.return_value = {
                'success': True,
                'totalItems': 3,
                'successCount': 2,
                'failureCount': 1,
                'successfulItems': ['key1', 'key2'],
                'failedItems': [{'key': 'key3', 'error': 'Schema validation failed'}],
                'message': 'Upserted 2 of 3 metadata items',
                'timestamp': '2024-12-30T14:30:00.000Z'
            }
            
            json_data = {
                'metadata': [
                    {'metadataKey': 'key1', 'metadataValue': 'value1', 'metadataValueType': 'string'},
                    {'metadataKey': 'key2', 'metadataValue': 'value2', 'metadataValueType': 'string'},
                    {'metadataKey': 'key3', 'metadataValue': 'invalid', 'metadataValueType': 'string'}
                ]
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'asset', 'update',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--json-input', '@metadata.json'
                    ])
            
            assert result.exit_code == 0
            assert 'Total Items: 3' in result.output
            assert 'Successful: 2' in result.output
            assert 'Failed: 1' in result.output
            assert 'key3: Schema validation failed' in result.output


#######################
# Error Handling Tests
#######################

class TestMetadataErrorHandling:
    """Test error handling for metadata commands."""
    
    def test_update_missing_metadata_array(self, cli_runner, metadata_command_mocks):
        """Test update command with missing metadata array in JSON."""
        with metadata_command_mocks as mocks:
            json_data = {'wrong_key': 'value'}
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'asset', 'update',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--json-input', '@metadata.json'
                    ])
            
            assert result.exit_code == 1
            assert "JSON input must contain 'metadata' array" in result.output
    
    def test_delete_missing_keys_array(self, cli_runner, metadata_command_mocks):
        """Test delete command with missing metadataKeys array in JSON."""
        with metadata_command_mocks as mocks:
            json_data = {'wrong_key': 'value'}
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'asset', 'delete',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--json-input', '@delete-keys.json'
                    ])
            
            assert result.exit_code == 1
            assert "JSON input must contain 'metadataKeys' array" in result.output
    
    def test_update_empty_metadata_array(self, cli_runner, metadata_command_mocks):
        """Test update command with empty metadata array."""
        with metadata_command_mocks as mocks:
            json_data = {'metadata': []}
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                with patch('pathlib.Path.exists', return_value=True):
                    result = cli_runner.invoke(cli, [
                        'metadata', 'asset', 'update',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--json-input', '@metadata.json'
                    ])
            
            assert result.exit_code == 1
            assert "'metadata' must be a non-empty array" in result.output
    
    def test_asset_not_found_error(self, cli_runner, metadata_command_mocks):
        """Test handling of asset not found error."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_metadata_v2.side_effect = AssetNotFoundError(
                "Asset 'test-asset' not found in database 'test-db'"
            )
            
            result = cli_runner.invoke(cli, ['metadata', 'asset', 'list', '-d', 'test-db', '-a', 'test-asset'])
            
            assert result.exit_code == 1
            assert '✗ AssetNotFound' in result.output  # Simplified error type
            assert 'not found' in result.output
    
    def test_database_not_found_error(self, cli_runner, metadata_command_mocks):
        """Test handling of database not found error."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_database_metadata_v2.side_effect = DatabaseNotFoundError(
                "Database 'test-db' not found"
            )
            
            result = cli_runner.invoke(cli, ['metadata', 'database', 'list', '-d', 'test-db'])
            
            assert result.exit_code == 1
            assert '✗ Database Error' in result.output
            assert 'not found' in result.output
    
    def test_asset_link_permission_error(self, cli_runner, metadata_command_mocks):
        """Test handling of asset link permission error."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_link_metadata_v2.side_effect = AssetLinkPermissionError(
                "Not authorized to view metadata for this asset link"
            )
            
            result = cli_runner.invoke(cli, ['metadata', 'asset-link', 'list', '--asset-link-id', 'abc123'])
            
            assert result.exit_code == 1
            assert '✗ Asset Link Metadata Error' in result.output  # Simplified error type
            assert 'Not authorized' in result.output


#######################
# Edge Cases Tests
#######################

class TestMetadataEdgeCases:
    """Test edge cases for metadata commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_parameters(self, mock_main_profile_manager):
        """Test that commands require appropriate parameters."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test asset list without database ID
        result = runner.invoke(cli, ['metadata', 'asset', 'list', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test file list without file-path
        result = runner.invoke(cli, ['metadata', 'file', 'list', '-d', 'test-db', '-a', 'test-asset', '--type', 'metadata'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test asset-link list without asset-link-id
        result = runner.invoke(cli, ['metadata', 'asset-link', 'list'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_pagination_parameters(self, cli_runner, metadata_command_mocks):
        """Test pagination parameters."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_metadata_v2.return_value = {
                'metadata': [],
                'NextToken': 'next-page-token',
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, [
                'metadata', 'asset', 'list',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--page-size', '100',
                '--starting-token', 'previous-token'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call with pagination params
            mocks['api_client'].get_asset_metadata_v2.assert_called_once_with(
                'test-db', 'test-asset', 100, 'previous-token'
            )
    
    def test_schema_enrichment_display(self, cli_runner, metadata_command_mocks):
        """Test display of schema enrichment fields."""
        with metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_metadata_v2.return_value = {
                'metadata': [
                    {
                        'metadataKey': 'title',
                        'metadataValue': 'Test',
                        'metadataValueType': 'string',
                        'metadataSchemaField': True,
                        'metadataSchemaRequired': True
                    }
                ],
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, ['metadata', 'asset', 'list', '-d', 'test-db', '-a', 'test-asset'])
            
            assert result.exit_code == 0
            assert 'Schema Field: Yes' in result.output
            assert 'Required: Yes' in result.output


if __name__ == '__main__':
    pytest.main([__file__])