"""Test asset links metadata functionality."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    AssetLinkNotFoundError, AssetLinkValidationError, AssetLinkPermissionError,
    APIUnavailableError, AuthenticationError, APIError, SetupRequiredError
)


# File-level fixtures for asset-links-metadata-specific testing patterns
@pytest.fixture
def asset_links_metadata_command_mocks(generic_command_mocks):
    """Provide asset-links-metadata-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for asset-links-metadata command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('asset_links_metadata')


@pytest.fixture
def asset_links_metadata_no_setup_mocks(no_setup_command_mocks):
    """Provide asset-links-metadata command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('asset_links_metadata')


class TestAssetLinksMetadataListCommand:
    """Test asset-links-metadata list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['asset-links-metadata', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all metadata for an asset link' in result.output
        assert 'ASSET_LINK_ID' in result.output
        assert '--json-output' in result.output
    
    def test_list_success(self, cli_runner, asset_links_metadata_command_mocks):
        """Test successful metadata listing."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_link_metadata.return_value = {
                'metadata': [
                    {
                        'assetLinkId': 'abc123-def456-ghi789-012345',
                        'metadataKey': 'description',
                        'metadataValue': 'Test description',
                        'metadataValueType': 'string'
                    },
                    {
                        'assetLinkId': 'abc123-def456-ghi789-012345',
                        'metadataKey': 'distance',
                        'metadataValue': '15.5',
                        'metadataValueType': 'number'
                    }
                ],
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, ['asset-links-metadata', 'list', 'abc123-def456-ghi789-012345'])
            
            assert result.exit_code == 0
            assert 'Asset Link Metadata (2 items)' in result.output
            assert 'description' in result.output
            assert 'Test description' in result.output
            assert 'distance' in result.output
            assert '15.5' in result.output
            
            # Verify API call
            mocks['api_client'].get_asset_link_metadata.assert_called_once_with('abc123-def456-ghi789-012345')
    
    def test_list_json_output(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata listing with JSON output."""
        with asset_links_metadata_command_mocks as mocks:
            api_response = {
                'metadata': [
                    {
                        'assetLinkId': 'abc123-def456-ghi789-012345',
                        'metadataKey': 'test_key',
                        'metadataValue': 'test_value',
                        'metadataValueType': 'string'
                    }
                ],
                'message': 'Success'
            }
            mocks['api_client'].get_asset_link_metadata.return_value = api_response
            
            result = cli_runner.invoke(cli, ['asset-links-metadata', 'list', 'abc123-def456-ghi789-012345', '--json-output'])
            
            assert result.exit_code == 0
            output_json = json.loads(result.output)
            assert output_json == api_response
    
    def test_list_empty_metadata(self, cli_runner, asset_links_metadata_command_mocks):
        """Test listing when no metadata exists."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_link_metadata.return_value = {
                'metadata': [],
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, ['asset-links-metadata', 'list', 'abc123-def456-ghi789-012345'])
            
            assert result.exit_code == 0
            assert 'No metadata found for this asset link' in result.output
    
    def test_list_no_setup(self, cli_runner, asset_links_metadata_no_setup_mocks):
        """Test list command without setup."""
        with asset_links_metadata_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['asset-links-metadata', 'list', 'abc123-def456-ghi789-012345'])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)
    
    def test_list_asset_link_not_found(self, cli_runner, asset_links_metadata_command_mocks):
        """Test list command with non-existent asset link."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_link_metadata.side_effect = AssetLinkNotFoundError(
                "Asset link 'abc123-def456-ghi789-012345' not found"
            )
            
            result = cli_runner.invoke(cli, ['asset-links-metadata', 'list', 'abc123-def456-ghi789-012345'])
            
            assert result.exit_code == 1
            assert '✗ Asset Link Error' in result.output
            assert 'not found' in result.output


class TestAssetLinksMetadataCreateCommand:
    """Test asset-links-metadata create command."""
    
    def test_create_help(self, cli_runner):
        """Test create command help."""
        result = cli_runner.invoke(cli, ['asset-links-metadata', 'create', '--help'])
        assert result.exit_code == 0
        assert 'Create metadata for an asset link' in result.output
        assert '--key' in result.output
        assert '--value' in result.output
        assert '--type' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_create_success(self, cli_runner, asset_links_metadata_command_mocks):
        """Test successful metadata creation."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key',
                '--value', 'test_value'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata created successfully!' in result.output
            assert 'Key: test_key' in result.output
            assert 'Value: test_value' in result.output
            assert 'Type: string' in result.output
            
            expected_data = {
                'metadataKey': 'test_key',
                'metadataValue': 'test_value',
                'metadataValueType': 'string'
            }
            mocks['api_client'].create_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', expected_data
            )
    
    def test_create_with_number_type(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata creation with number type."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'distance',
                '--value', '15.5',
                '--type', 'number'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata created successfully!' in result.output
            assert 'Key: distance' in result.output
            assert 'Value: 15.5' in result.output
            assert 'Type: number' in result.output
            
            expected_data = {
                'metadataKey': 'distance',
                'metadataValue': '15.5',
                'metadataValueType': 'number'
            }
            mocks['api_client'].create_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', expected_data
            )
    
    def test_create_with_xyz_type(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata creation with XYZ coordinate type."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            xyz_value = '{"x": 1.5, "y": 2.0, "z": 0.5}'
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'offset',
                '--value', xyz_value,
                '--type', 'xyz'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata created successfully!' in result.output
            assert 'Key: offset' in result.output
            assert 'Type: xyz' in result.output
            
            expected_data = {
                'metadataKey': 'offset',
                'metadataValue': xyz_value,
                'metadataValueType': 'xyz'
            }
            mocks['api_client'].create_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', expected_data
            )
    
    def test_create_with_wxyz_type(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata creation with WXYZ quaternion type."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            wxyz_value = '{"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}'
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'rotation',
                '--value', wxyz_value,
                '--type', 'wxyz'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata created successfully!' in result.output
            assert 'Key: rotation' in result.output
            assert 'Type: wxyz' in result.output
            
            expected_data = {
                'metadataKey': 'rotation',
                'metadataValue': wxyz_value,
                'metadataValueType': 'wxyz'
            }
            mocks['api_client'].create_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', expected_data
            )
    
    def test_create_with_matrix4x4_type(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata creation with MATRIX4X4 type."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            matrix_value = '[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]'
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'transform',
                '--value', matrix_value,
                '--type', 'matrix4x4'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata created successfully!' in result.output
            assert 'Key: transform' in result.output
            assert 'Type: matrix4x4' in result.output
            
            expected_data = {
                'metadataKey': 'transform',
                'metadataValue': matrix_value,
                'metadataValueType': 'matrix4x4'
            }
            mocks['api_client'].create_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', expected_data
            )
    
    def test_create_with_geopoint_type(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata creation with GEOPOINT type."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            geopoint_value = '{"type": "Point", "coordinates": [-74.0060, 40.7128]}'
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'location',
                '--value', geopoint_value,
                '--type', 'geopoint'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata created successfully!' in result.output
            assert 'Key: location' in result.output
            assert 'Type: geopoint' in result.output
            
            expected_data = {
                'metadataKey': 'location',
                'metadataValue': geopoint_value,
                'metadataValueType': 'geopoint'
            }
            mocks['api_client'].create_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', expected_data
            )
    
    def test_create_with_lla_type(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata creation with LLA coordinate type."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            lla_value = '{"lat": 40.7128, "long": -74.0060, "alt": 10.5}'
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'position',
                '--value', lla_value,
                '--type', 'lla'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata created successfully!' in result.output
            assert 'Key: position' in result.output
            assert 'Type: lla' in result.output
            
            expected_data = {
                'metadataKey': 'position',
                'metadataValue': lla_value,
                'metadataValueType': 'lla'
            }
            mocks['api_client'].create_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', expected_data
            )
    
    def test_create_with_json_type(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata creation with JSON type."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            json_value = '{"enabled": true, "count": 5, "tags": ["test", "metadata"]}'
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'config',
                '--value', json_value,
                '--type', 'json'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata created successfully!' in result.output
            assert 'Key: config' in result.output
            assert 'Type: json' in result.output
            
            expected_data = {
                'metadataKey': 'config',
                'metadataValue': json_value,
                'metadataValueType': 'json'
            }
            mocks['api_client'].create_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', expected_data
            )
    
    def test_create_json_input(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata creation with JSON input."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            json_data = {
                'metadataKey': 'description',
                'metadataValue': 'JSON input description',
                'metadataValueType': 'string'
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                    '--json-input', 'metadata.json'
                ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata created successfully!' in result.output
            
            expected_data = {
                'metadataKey': 'description',
                'metadataValue': 'JSON input description',
                'metadataValueType': 'string'
            }
            mocks['api_client'].create_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', expected_data
            )
    
    def test_create_missing_key(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command without required key."""
        with asset_links_metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--value', 'test_value'
            ])
            
            assert result.exit_code == 1
            assert 'Metadata key is required' in result.output
    
    def test_create_missing_value(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command without required value."""
        with asset_links_metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key'
            ])
            
            assert result.exit_code == 1
            assert 'Metadata value is required' in result.output
    
    def test_create_conflicting_input_methods(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with conflicting input methods."""
        with asset_links_metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key',
                '--value', 'test_value',
                '--json-input', 'metadata.json'
            ])
            
            assert result.exit_code == 1
            assert 'Cannot use --key/--value options with --json-input' in result.output
    
    def test_create_permission_error(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with permission error."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.side_effect = AssetLinkPermissionError(
                "Not authorized to create metadata for this asset link"
            )
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key',
                '--value', 'test_value'
            ])
            
            assert result.exit_code == 1
            assert '✗ Permission Error' in result.output
            assert 'Not authorized' in result.output
    
    def test_create_validation_error(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with validation error."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.side_effect = AssetLinkValidationError(
                "Metadata key already exists"
            )
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key',
                '--value', 'test_value'
            ])
            
            assert result.exit_code == 1
            assert '✗ Validation Error' in result.output
            assert 'already exists' in result.output


class TestAssetLinksMetadataUpdateCommand:
    """Test asset-links-metadata update command."""
    
    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(cli, ['asset-links-metadata', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Update metadata for an asset link' in result.output
        assert 'METADATA_KEY' in result.output
        assert '--value' in result.output
        assert '--type' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_update_success(self, cli_runner, asset_links_metadata_command_mocks):
        """Test successful metadata update."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_link_metadata.return_value = {
                'message': 'Asset link metadata updated successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'update', 'abc123-def456-ghi789-012345', 'test_key',
                '--value', 'updated_value'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata updated successfully!' in result.output
            assert 'Key: test_key' in result.output
            assert 'New Value: updated_value' in result.output
            assert 'Type: string' in result.output
            
            expected_data = {
                'metadataValue': 'updated_value',
                'metadataValueType': 'string'
            }
            mocks['api_client'].update_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', 'test_key', expected_data
            )
    
    def test_update_with_number_type(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata update with number type."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_link_metadata.return_value = {
                'message': 'Asset link metadata updated successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'update', 'abc123-def456-ghi789-012345', 'distance',
                '--value', '20.0',
                '--type', 'number'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata updated successfully!' in result.output
            assert 'Key: distance' in result.output
            assert 'New Value: 20.0' in result.output
            assert 'Type: number' in result.output
            
            expected_data = {
                'metadataValue': '20.0',
                'metadataValueType': 'number'
            }
            mocks['api_client'].update_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', 'distance', expected_data
            )
    
    def test_update_json_input(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata update with JSON input."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_link_metadata.return_value = {
                'message': 'Asset link metadata updated successfully'
            }
            
            json_data = {
                'metadataValue': 'JSON updated value',
                'metadataValueType': 'string'
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'asset-links-metadata', 'update', 'abc123-def456-ghi789-012345', 'test_key',
                    '--json-input', 'metadata.json'
                ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata updated successfully!' in result.output
            
            expected_data = {
                'metadataValue': 'JSON updated value',
                'metadataValueType': 'string'
            }
            mocks['api_client'].update_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', 'test_key', expected_data
            )
    
    def test_update_missing_value(self, cli_runner, asset_links_metadata_command_mocks):
        """Test update command without required value."""
        with asset_links_metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'update', 'abc123-def456-ghi789-012345', 'test_key'
            ])
            
            assert result.exit_code == 1
            assert 'Metadata value is required' in result.output
    
    def test_update_conflicting_input_methods(self, cli_runner, asset_links_metadata_command_mocks):
        """Test update command with conflicting input methods."""
        with asset_links_metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'update', 'abc123-def456-ghi789-012345', 'test_key',
                '--value', 'test_value',
                '--json-input', 'metadata.json'
            ])
            
            assert result.exit_code == 1
            assert 'Cannot use --value option with --json-input' in result.output
    
    def test_update_validation_error_key_not_found(self, cli_runner, asset_links_metadata_command_mocks):
        """Test update command with metadata key not found."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_link_metadata.side_effect = AssetLinkValidationError(
                "Metadata key 'test_key' not found for this asset link"
            )
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'update', 'abc123-def456-ghi789-012345', 'test_key',
                '--value', 'new_value'
            ])
            
            assert result.exit_code == 1
            assert '✗ Validation Error' in result.output
            assert 'not found' in result.output


class TestAssetLinksMetadataDeleteCommand:
    """Test asset-links-metadata delete command."""
    
    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(cli, ['asset-links-metadata', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete metadata for an asset link' in result.output
        assert 'METADATA_KEY' in result.output
        assert '--json-output' in result.output
    
    def test_delete_success(self, cli_runner, asset_links_metadata_command_mocks):
        """Test successful metadata deletion."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].delete_asset_link_metadata.return_value = {
                'message': 'Asset link metadata deleted successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'delete', 'abc123-def456-ghi789-012345', 'test_key'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link metadata deleted successfully!' in result.output
            assert 'Deleted key: test_key' in result.output
            
            mocks['api_client'].delete_asset_link_metadata.assert_called_once_with(
                'abc123-def456-ghi789-012345', 'test_key'
            )
    
    def test_delete_json_output(self, cli_runner, asset_links_metadata_command_mocks):
        """Test metadata deletion with JSON output."""
        with asset_links_metadata_command_mocks as mocks:
            api_response = {
                'message': 'Asset link metadata deleted successfully'
            }
            mocks['api_client'].delete_asset_link_metadata.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'delete', 'abc123-def456-ghi789-012345', 'test_key',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            output_json = json.loads(result.output)
            assert output_json == api_response
    
    def test_delete_asset_link_not_found(self, cli_runner, asset_links_metadata_command_mocks):
        """Test delete command with non-existent asset link."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].delete_asset_link_metadata.side_effect = AssetLinkNotFoundError(
                "Asset link 'abc123-def456-ghi789-012345' not found"
            )
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'delete', 'abc123-def456-ghi789-012345', 'test_key'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Link Error' in result.output
            assert 'not found' in result.output
    
    def test_delete_validation_error(self, cli_runner, asset_links_metadata_command_mocks):
        """Test delete command with validation error."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].delete_asset_link_metadata.side_effect = AssetLinkValidationError(
                "Metadata key 'test_key' not found for this asset link"
            )
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'delete', 'abc123-def456-ghi789-012345', 'test_key'
            ])
            
            assert result.exit_code == 1
            assert '✗ Validation Error' in result.output
            assert 'not found' in result.output


class TestAssetLinksMetadataIntegration:
    """Test integration scenarios for asset-links-metadata commands."""
    
    def test_asset_links_metadata_group_help(self, cli_runner):
        """Test asset-links-metadata group help."""
        result = cli_runner.invoke(cli, ['asset-links-metadata', '--help'])
        assert result.exit_code == 0
        assert 'Manage metadata for asset links' in result.output
        assert 'list' in result.output
        assert 'create' in result.output
        assert 'update' in result.output
        assert 'delete' in result.output
    
    def test_command_registration(self, cli_runner):
        """Test that asset-links-metadata commands are properly registered."""
        result = cli_runner.invoke(cli, ['--help'])
        assert result.exit_code == 0
        assert 'asset-links-metadata' in result.output
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_asset_link_id(self, mock_main_profile_manager, cli_runner):
        """Test that commands require asset link ID where appropriate."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        # Test list without asset link ID
        result = cli_runner.invoke(cli, ['asset-links-metadata', 'list'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'required' in result.output.lower()
        
        # Test create without asset link ID
        result = cli_runner.invoke(cli, ['asset-links-metadata', 'create'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'required' in result.output.lower()
        
        # Test update without asset link ID
        result = cli_runner.invoke(cli, ['asset-links-metadata', 'update'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'required' in result.output.lower()
        
        # Test delete without asset link ID
        result = cli_runner.invoke(cli, ['asset-links-metadata', 'delete'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'required' in result.output.lower()
    
    def test_end_to_end_workflow(self, cli_runner, asset_links_metadata_command_mocks):
        """Test end-to-end workflow: create, list, update, delete."""
        with asset_links_metadata_command_mocks as mocks:
            asset_link_id = 'abc123-def456-ghi789-012345'
            
            # 1. Create metadata
            mocks['api_client'].create_asset_link_metadata.return_value = {
                'message': 'Asset link metadata created successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', asset_link_id,
                '--key', 'description',
                '--value', 'Test connection'
            ])
            assert result.exit_code == 0
            assert 'created successfully' in result.output
            
            # 2. List metadata
            mocks['api_client'].get_asset_link_metadata.return_value = {
                'metadata': [
                    {
                        'assetLinkId': asset_link_id,
                        'metadataKey': 'description',
                        'metadataValue': 'Test connection',
                        'metadataValueType': 'string'
                    }
                ],
                'message': 'Success'
            }
            
            result = cli_runner.invoke(cli, ['asset-links-metadata', 'list', asset_link_id])
            assert result.exit_code == 0
            assert 'description' in result.output
            assert 'Test connection' in result.output
            
            # 3. Update metadata
            mocks['api_client'].update_asset_link_metadata.return_value = {
                'message': 'Asset link metadata updated successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'update', asset_link_id, 'description',
                '--value', 'Updated connection'
            ])
            assert result.exit_code == 0
            assert 'updated successfully' in result.output
            
            # 4. Delete metadata
            mocks['api_client'].delete_asset_link_metadata.return_value = {
                'message': 'Asset link metadata deleted successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'delete', asset_link_id, 'description'
            ])
            assert result.exit_code == 0
            assert 'deleted successfully' in result.output
    
    def test_authentication_error_handling(self, cli_runner, asset_links_metadata_command_mocks):
        """Test authentication error handling."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_link_metadata.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, ['asset-links-metadata', 'list', 'abc123-def456-ghi789-012345'])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, AuthenticationError)


class TestAssetLinksMetadataJSONHandling:
    """Test JSON input/output handling for asset-links-metadata commands."""
    
    def test_create_json_input_missing_key(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with JSON input missing required key."""
        with asset_links_metadata_command_mocks as mocks:
            json_data = {
                'metadataValue': 'test_value'
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                    '--json-input', 'metadata.json'
                ])
            
            assert result.exit_code == 1
            assert 'JSON input must contain \'metadataKey\' field' in result.output
    
    def test_create_json_input_missing_value(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with JSON input missing required value."""
        with asset_links_metadata_command_mocks as mocks:
            json_data = {
                'metadataKey': 'test_key'
            }
            
            mock_file = mock_open(read_data=json.dumps(json_data))
            with patch('builtins.open', mock_file):
                result = cli_runner.invoke(cli, [
                    'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                    '--json-input', 'metadata.json'
                ])
            
            assert result.exit_code == 1
            assert 'JSON input must contain \'metadataValue\' field' in result.output
    
    def test_update_json_input_missing_value(self, cli_runner, asset_links_metadata_command_mocks):
        """Test update command with JSON input missing required value."""
        with asset_links_metadata_command_mocks as mocks:
            json_data = {
                'metadataValueType': 'string'
            }
            
            mock_file = mock_open(read_data=json.dumps(json_data))
            with patch('builtins.open', mock_file):
                result = cli_runner.invoke(cli, [
                    'asset-links-metadata', 'update', 'abc123-def456-ghi789-012345', 'test_key',
                    '--json-input', 'metadata.json'
                ])
            
            assert result.exit_code == 1
            assert 'JSON input must contain \'metadataValue\' field' in result.output
    
    def test_invalid_json_input_file(self, cli_runner, asset_links_metadata_command_mocks):
        """Test handling of invalid JSON input file."""
        with asset_links_metadata_command_mocks as mocks:
            mock_file = mock_open(read_data='invalid json')
            with patch('builtins.open', mock_file):
                result = cli_runner.invoke(cli, [
                    'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                    '--json-input', 'invalid.json'
                ])
            
            assert result.exit_code == 1
            assert 'Invalid JSON in input file' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, asset_links_metadata_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with asset_links_metadata_command_mocks as mocks:
            # Now that we removed exists=True, our command handles the file not found error
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--json-input', 'nonexistent.json'
            ])
            
            assert result.exit_code == 1  # Our custom error handling
            assert 'JSON input file \'nonexistent.json\' not found' in result.output
    
    def test_all_commands_json_output(self, cli_runner, asset_links_metadata_command_mocks):
        """Test all commands with JSON output option."""
        with asset_links_metadata_command_mocks as mocks:
            asset_link_id = 'abc123-def456-ghi789-012345'
            
            # Test list with JSON output
            list_response = {'metadata': [], 'message': 'Success'}
            mocks['api_client'].get_asset_link_metadata.return_value = list_response
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'list', asset_link_id, '--json-output'
            ])
            
            assert result.exit_code == 0
            output_json = json.loads(result.output)
            assert output_json == list_response
            
            # Test create with JSON output
            create_response = {'message': 'Created successfully'}
            mocks['api_client'].create_asset_link_metadata.return_value = create_response
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', asset_link_id,
                '--key', 'test_key',
                '--value', 'test_value',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            output_json = json.loads(result.output)
            assert output_json == create_response
            
            # Test update with JSON output
            update_response = {'message': 'Updated successfully'}
            mocks['api_client'].update_asset_link_metadata.return_value = update_response
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'update', asset_link_id, 'test_key',
                '--value', 'new_value',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            output_json = json.loads(result.output)
            assert output_json == update_response
            
            # Test delete with JSON output
            delete_response = {'message': 'Deleted successfully'}
            mocks['api_client'].delete_asset_link_metadata.return_value = delete_response
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'delete', asset_link_id, 'test_key',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            output_json = json.loads(result.output)
            assert output_json == delete_response


class TestAssetLinksMetadataEdgeCases:
    """Test edge cases for asset-links-metadata commands."""
    
    def test_create_api_error(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with general API error."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].create_asset_link_metadata.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key',
                '--value', 'test_value'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, APIError)
    
    def test_list_api_unavailable_error(self, cli_runner, asset_links_metadata_command_mocks):
        """Test list command with API unavailable error."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].get_asset_link_metadata.side_effect = APIUnavailableError("API service unavailable")
            
            result = cli_runner.invoke(cli, ['asset-links-metadata', 'list', 'abc123-def456-ghi789-012345'])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, APIUnavailableError)
    
    def test_update_permission_error(self, cli_runner, asset_links_metadata_command_mocks):
        """Test update command with permission error."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].update_asset_link_metadata.side_effect = AssetLinkPermissionError(
                "Not authorized to update metadata for this asset link"
            )
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'update', 'abc123-def456-ghi789-012345', 'test_key',
                '--value', 'new_value'
            ])
            
            assert result.exit_code == 1
            assert '✗ Permission Error' in result.output
            assert 'Not authorized' in result.output
    
    def test_delete_permission_error(self, cli_runner, asset_links_metadata_command_mocks):
        """Test delete command with permission error."""
        with asset_links_metadata_command_mocks as mocks:
            mocks['api_client'].delete_asset_link_metadata.side_effect = AssetLinkPermissionError(
                "Not authorized to delete metadata for this asset link"
            )
            
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'delete', 'abc123-def456-ghi789-012345', 'test_key'
            ])
            
            assert result.exit_code == 1
            assert '✗ Permission Error' in result.output
            assert 'Not authorized' in result.output


class TestAssetLinksMetadataValidationFunctions:
    """Test metadata validation functionality."""
    
    def test_validate_metadata_types(self):
        """Test metadata type validation function."""
        from vamscli.commands.asset_links_metadata import validate_metadata_type
        
        # Valid types - all 11 supported types
        assert validate_metadata_type('string') == 'string'
        assert validate_metadata_type('STRING') == 'string'  # Case insensitive
        assert validate_metadata_type('number') == 'number'
        assert validate_metadata_type('boolean') == 'boolean'
        assert validate_metadata_type('date') == 'date'
        assert validate_metadata_type('json') == 'json'
        assert validate_metadata_type('xyz') == 'xyz'
        assert validate_metadata_type('wxyz') == 'wxyz'
        assert validate_metadata_type('matrix4x4') == 'matrix4x4'
        assert validate_metadata_type('geopoint') == 'geopoint'
        assert validate_metadata_type('geojson') == 'geojson'
        assert validate_metadata_type('lla') == 'lla'
        
        # Invalid type
        with pytest.raises(click.BadParameter):
            validate_metadata_type('invalid')
    
    def test_validate_metadata_values(self):
        """Test metadata value validation function."""
        from vamscli.commands.asset_links_metadata import validate_metadata_value
        
        # Valid string (no validation needed)
        assert validate_metadata_value('any string', 'string') == 'any string'
        
        # Valid number
        assert validate_metadata_value('15.5', 'number') == '15.5'
        assert validate_metadata_value('42', 'number') == '42'
        
        # Invalid number
        with pytest.raises(click.BadParameter):
            validate_metadata_value('not_a_number', 'number')
        
        # Valid boolean
        assert validate_metadata_value('true', 'boolean') == 'true'
        assert validate_metadata_value('false', 'boolean') == 'false'
        assert validate_metadata_value('TRUE', 'boolean') == 'TRUE'  # Case insensitive check
        
        # Invalid boolean
        with pytest.raises(click.BadParameter):
            validate_metadata_value('maybe', 'boolean')
        
        # Valid date
        assert validate_metadata_value('2023-12-01T10:30:00Z', 'date') == '2023-12-01T10:30:00Z'
        
        # Invalid date
        with pytest.raises(click.BadParameter):
            validate_metadata_value('not-a-date', 'date')
        
        # Valid JSON
        json_value = '{"key": "value", "number": 42}'
        assert validate_metadata_value(json_value, 'json') == json_value
        
        # Invalid JSON
        with pytest.raises(click.BadParameter):
            validate_metadata_value('invalid json', 'json')
        
        # Valid XYZ
        xyz_value = '{"x": 1.5, "y": 2.0, "z": 0.5}'
        assert validate_metadata_value(xyz_value, 'xyz') == xyz_value
        
        # Invalid XYZ - not JSON
        with pytest.raises(click.BadParameter):
            validate_metadata_value('not json', 'xyz')
        
        # Invalid XYZ - missing coordinates
        with pytest.raises(click.BadParameter):
            validate_metadata_value('{"x": 1.0}', 'xyz')
        
        # Invalid XYZ - non-numeric coordinates
        with pytest.raises(click.BadParameter):
            validate_metadata_value('{"x": "not_number", "y": 2.0, "z": 0.5}', 'xyz')
        
        # Valid WXYZ
        wxyz_value = '{"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}'
        assert validate_metadata_value(wxyz_value, 'wxyz') == wxyz_value
        
        # Invalid WXYZ - missing w coordinate
        with pytest.raises(click.BadParameter):
            validate_metadata_value('{"x": 0.0, "y": 0.0, "z": 0.0}', 'wxyz')
        
        # Valid MATRIX4X4
        matrix_value = '[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]'
        assert validate_metadata_value(matrix_value, 'matrix4x4') == matrix_value
        
        # Invalid MATRIX4X4 - wrong dimensions
        with pytest.raises(click.BadParameter):
            validate_metadata_value('[[1,0,0],[0,1,0],[0,0,1]]', 'matrix4x4')
        
        # Valid GEOPOINT
        geopoint_value = '{"type": "Point", "coordinates": [-74.0060, 40.7128]}'
        assert validate_metadata_value(geopoint_value, 'geopoint') == geopoint_value
        
        # Invalid GEOPOINT - wrong type
        with pytest.raises(click.BadParameter):
            validate_metadata_value('{"type": "Polygon", "coordinates": []}', 'geopoint')
        
        # Note: Coordinate range validation is handled by geojson library, not our validation
        # The geojson library may or may not reject out-of-range coordinates depending on implementation
        
        # Valid GEOJSON
        geojson_value = '{"type": "Polygon", "coordinates": [[[-74.1, 40.7], [-74.0, 40.7], [-74.0, 40.8], [-74.1, 40.8], [-74.1, 40.7]]]}'
        assert validate_metadata_value(geojson_value, 'geojson') == geojson_value
        
        # Note: GeoJSON validation is handled entirely by the geojson library
        # The library determines what constitutes valid/invalid GeoJSON
        
        # Valid LLA
        lla_value = '{"lat": 40.7128, "long": -74.0060, "alt": 10.5}'
        assert validate_metadata_value(lla_value, 'lla') == lla_value
        
        # Invalid LLA - latitude out of range
        with pytest.raises(click.BadParameter):
            validate_metadata_value('{"lat": 100, "long": -74.0060, "alt": 10.5}', 'lla')
        
        # Invalid LLA - missing altitude
        with pytest.raises(click.BadParameter):
            validate_metadata_value('{"lat": 40.7128, "long": -74.0060}', 'lla')
    
    def test_validate_number_type_edge_cases(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with invalid number value."""
        with asset_links_metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key',
                '--value', 'not_a_number',
                '--type', 'number'
            ])
            
            assert result.exit_code == 2  # Click parameter validation error
            assert 'not a valid number' in result.output
    
    def test_validate_boolean_type_edge_cases(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with invalid boolean value."""
        with asset_links_metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key',
                '--value', 'maybe',
                '--type', 'boolean'
            ])
            
            assert result.exit_code == 2  # Click parameter validation error
            assert 'not a valid boolean' in result.output
    
    def test_validate_xyz_type_edge_cases(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with invalid XYZ value."""
        with asset_links_metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key',
                '--value', '{"x": 1.0}',  # Missing y and z
                '--type', 'xyz'
            ])
            
            assert result.exit_code == 2  # Click parameter validation error
            assert 'XYZ data must contain' in result.output
    
    def test_validate_invalid_metadata_type(self, cli_runner, asset_links_metadata_command_mocks):
        """Test create command with invalid metadata type."""
        with asset_links_metadata_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links-metadata', 'create', 'abc123-def456-ghi789-012345',
                '--key', 'test_key',
                '--value', 'test_value',
                '--type', 'invalid_type'
            ])
            
            assert result.exit_code == 2  # Click parameter validation error
            assert 'Invalid metadata type' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
