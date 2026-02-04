"""Test asset management commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    AssetNotFoundError, AssetAlreadyExistsError, DatabaseNotFoundError,
    InvalidAssetDataError, AuthenticationError, APIError, AssetAlreadyArchivedError,
    AssetDeletionError, PreviewNotFoundError, AssetNotDistributableError,
    FileDownloadError, DownloadError, AssetDownloadError, DownloadTreeError
)


# File-level fixtures for assets-specific testing patterns
@pytest.fixture
def assets_command_mocks(generic_command_mocks):
    """Provide assets-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for assets command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('assets')


@pytest.fixture
def assets_no_setup_mocks(no_setup_command_mocks):
    """Provide assets command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('assets')


class TestAssetCreateCommand:
    """Test asset create command."""
    
    def test_create_help(self, cli_runner):
        """Test create command help."""
        result = cli_runner.invoke(cli, ['assets', 'create', '--help'])
        assert result.exit_code == 0
        assert 'Create a new asset in VAMS' in result.output
        assert '--database-id' in result.output
        assert '--name' in result.output
        assert '--description' in result.output
        assert '--distributable' in result.output
        assert '--tags' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_create_success(self, cli_runner, assets_command_mocks):
        """Test successful asset creation."""
        with assets_command_mocks as mocks:
            mocks['api_client'].create_asset.return_value = {
                'assetId': 'test-asset',
                'message': 'Asset created successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'create',
                '-d', 'test-database',
                '--name', 'Test Asset',
                '--description', 'Test description',
                '--distributable'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset created successfully!' in result.output
            assert 'test-asset' in result.output
            
            # Verify API call
            mocks['api_client'].create_asset.assert_called_once()
            call_args = mocks['api_client'].create_asset.call_args[0][0]
            assert call_args['databaseId'] == 'test-database'
            assert call_args['assetName'] == 'Test Asset'
            assert call_args['description'] == 'Test description'
            assert call_args['isDistributable'] == True
    
    def test_create_with_tags(self, cli_runner, assets_command_mocks):
        """Test asset creation with multiple tags."""
        with assets_command_mocks as mocks:
            mocks['api_client'].create_asset.return_value = {
                'assetId': 'tagged-asset',
                'message': 'Asset created successfully'
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'create',
                '-d', 'test-database',
                '--name', 'Tagged Asset',
                '--description', 'Asset with tags',
                '--distributable',
                '--tags', 'tag1',
                '--tags', 'tag2',
                '--tags', 'tag3,tag4'  # Test comma-separated tags
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset created successfully!' in result.output
            
            # Verify tags were parsed correctly
            mocks['api_client'].create_asset.assert_called_once()
            call_args = mocks['api_client'].create_asset.call_args[0][0]
            assert set(call_args['tags']) == {'tag1', 'tag2', 'tag3', 'tag4'}
    
    def test_create_json_input(self, cli_runner, assets_command_mocks):
        """Test asset creation with JSON input."""
        with assets_command_mocks as mocks:
            mocks['api_client'].create_asset.return_value = {
                'assetId': 'json-asset',
                'message': 'Asset created successfully'
            }
            
            json_data = {
                'assetName': 'JSON Asset',
                'description': 'Created from JSON',
                'isDistributable': True,
                'tags': ['json', 'test']
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'create',
                '-d', 'test-database',
                '--json-input', json.dumps(json_data)
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset created successfully!' in result.output
            
            # Verify API call with JSON data
            mocks['api_client'].create_asset.assert_called_once()
            call_args = mocks['api_client'].create_asset.call_args[0][0]
            assert call_args['databaseId'] == 'test-database'
            assert call_args['assetName'] == 'JSON Asset'
            assert call_args['tags'] == ['json', 'test']
    
    def test_create_missing_required_args(self, cli_runner):
        """Test asset create with missing required arguments."""
        result = cli_runner.invoke(cli, ['assets', 'create'])
        assert result.exit_code == 2  # Click error for missing required option
        assert 'Missing option' in result.output
    
    def test_create_rejects_asset_id_in_json_input(self, cli_runner, assets_command_mocks):
        """Test that assetId in JSON input is rejected."""
        with assets_command_mocks as mocks:
            json_data = {
                'assetName': 'Test Asset',
                'description': 'Test description',
                'isDistributable': True,
                'assetId': 'custom-asset-id'  # Should be rejected
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'create',
                '-d', 'test-database',
                '--json-input', json.dumps(json_data)
            ])
            
            assert result.exit_code == 2  # Click BadParameter error
            assert 'assetId cannot be specified' in result.output
            assert 'CLI or web front-end' in result.output
            assert 'automatically generated' in result.output
            
            # Verify API was not called
            mocks['api_client'].create_asset.assert_not_called()
    
    def test_create_no_asset_id_option_exists(self, cli_runner):
        """Test that --asset-id option no longer exists."""
        result = cli_runner.invoke(cli, ['assets', 'create', '--help'])
        assert result.exit_code == 0
        assert '--asset-id' not in result.output
        # Verify other options still exist
        assert '--database-id' in result.output
        assert '--name' in result.output
        assert '--description' in result.output
    
    def test_create_asset_already_exists(self, cli_runner, assets_command_mocks):
        """Test asset creation when asset already exists."""
        with assets_command_mocks as mocks:
            mocks['api_client'].create_asset.side_effect = AssetAlreadyExistsError("Asset already exists")
            
            result = cli_runner.invoke(cli, [
                'assets', 'create',
                '-d', 'test-database',
                '--name', 'Existing Asset',
                '--description', 'This asset exists',
                '--distributable'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Already Exists' in result.output
            assert 'vamscli assets get' in result.output
    
    def test_create_database_not_found(self, cli_runner, assets_command_mocks):
        """Test asset creation with database not found."""
        with assets_command_mocks as mocks:
            mocks['api_client'].create_asset.side_effect = DatabaseNotFoundError("Database not found")
            
            result = cli_runner.invoke(cli, [
                'assets', 'create',
                '-d', 'nonexistent-database',
                '--name', 'Test Asset',
                '--description', 'Test description',
                '--distributable'
            ])
            
            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert 'vamscli database list' in result.output
    


class TestAssetUpdateCommand:
    """Test asset update command."""
    
    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(cli, ['assets', 'update', '--help'])
        assert result.exit_code == 0
        assert 'Update an existing asset in VAMS' in result.output
        assert '--database-id' in result.output
        assert '--name' in result.output
        assert '--description' in result.output
        assert '--distributable' in result.output
        assert '--tags' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_update_success(self, cli_runner, assets_command_mocks):
        """Test successful asset update."""
        with assets_command_mocks as mocks:
            mocks['api_client'].update_asset.return_value = {
                'assetId': 'test-asset',
                'message': 'Asset updated successfully',
                'operation': 'update',
                'timestamp': '2024-01-15T10:30:00Z'
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'update', 'test-asset',
                '-d', 'test-database',
                '--name', 'Updated Asset Name'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset updated successfully!' in result.output
            assert 'test-asset' in result.output
            
            # Verify API call
            mocks['api_client'].update_asset.assert_called_once_with(
                'test-database', 'test-asset', {'assetName': 'Updated Asset Name'}
            )
    
    def test_update_json_input(self, cli_runner, assets_command_mocks):
        """Test asset update with JSON input."""
        with assets_command_mocks as mocks:
            mocks['api_client'].update_asset.return_value = {
                'assetId': 'test-asset',
                'message': 'Asset updated successfully'
            }
            
            json_data = {
                'assetName': 'JSON Updated Asset',
                'description': 'Updated from JSON',
                'tags': ['updated', 'json']
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'update', 'test-asset',
                '-d', 'test-database',
                '--json-input', json.dumps(json_data)
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset updated successfully!' in result.output
            
            # Verify API call with JSON data
            mocks['api_client'].update_asset.assert_called_once_with(
                'test-database', 'test-asset', json_data
            )
    
    
    def test_update_asset_not_found(self, cli_runner, assets_command_mocks):
        """Test update command with asset not found."""
        with assets_command_mocks as mocks:
            mocks['api_client'].update_asset.side_effect = AssetNotFoundError("Asset not found")
            
            result = cli_runner.invoke(cli, [
                'assets', 'update', 'nonexistent-asset',
                '-d', 'test-database',
                '--name', 'Updated Name'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Not Found' in result.output
            assert 'vamscli assets get' in result.output


class TestAssetGetCommand:
    """Test asset get command."""
    
    def test_get_help(self, cli_runner):
        """Test get command help."""
        result = cli_runner.invoke(cli, ['assets', 'get', '--help'])
        assert result.exit_code == 0
        assert 'Get details for a specific asset' in result.output
        assert '--database-id' in result.output
        assert '--show-archived' in result.output
        assert '--json-output' in result.output
    
    def test_get_success(self, cli_runner, assets_command_mocks):
        """Test successful asset retrieval."""
        with assets_command_mocks as mocks:
            mocks['api_client'].get_asset.return_value = {
                'assetId': 'test-asset',
                'databaseId': 'test-database',
                'assetName': 'Test Asset',
                'description': 'Test description',
                'isDistributable': True,
                'tags': ['test', 'asset'],
                'status': 'active'
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'get', 'test-asset',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 0
            assert 'Asset Details:' in result.output
            assert 'ID: test-asset' in result.output
            assert 'Name: Test Asset' in result.output
            
            # Verify API call
            mocks['api_client'].get_asset.assert_called_once_with('test-database', 'test-asset', False)
    
    def test_get_json_output(self, cli_runner, assets_command_mocks):
        """Test asset get with JSON output."""
        with assets_command_mocks as mocks:
            asset_data = {
                'assetId': 'test-asset',
                'databaseId': 'test-database',
                'assetName': 'Test Asset'
            }
            mocks['api_client'].get_asset.return_value = asset_data
            
            result = cli_runner.invoke(cli, [
                'assets', 'get', 'test-asset',
                '-d', 'test-database',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should contain JSON output
            output_json = json.loads(result.output.strip())
            assert output_json['assetId'] == 'test-asset'
    
    def test_get_asset_not_found(self, cli_runner, assets_command_mocks):
        """Test asset get when asset is not found."""
        with assets_command_mocks as mocks:
            mocks['api_client'].get_asset.side_effect = AssetNotFoundError("Asset not found")
            
            result = cli_runner.invoke(cli, [
                'assets', 'get', 'nonexistent-asset',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Not Found' in result.output
            assert '--show-archived' in result.output


class TestAssetListCommand:
    """Test asset list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['assets', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List assets in a database or all assets' in result.output
        assert '--database-id' in result.output
        assert '--show-archived' in result.output
        assert '--page-size' in result.output
        assert '--max-items' in result.output
        assert '--starting-token' in result.output
        assert '--auto-paginate' in result.output
        assert '--json-output' in result.output
    
    def test_list_success(self, cli_runner, assets_command_mocks):
        """Test successful asset listing."""
        with assets_command_mocks as mocks:
            mock_response = Mock()
            mock_response.json.return_value = {
                'Items': [
                    {
                        'assetId': 'asset1',
                        'databaseId': 'test-db',
                        'assetName': 'Asset 1',
                        'description': 'First asset',
                        'isDistributable': True,
                        'tags': ['test']
                    },
                    {
                        'assetId': 'asset2',
                        'databaseId': 'test-db',
                        'assetName': 'Asset 2',
                        'description': 'Second asset',
                        'isDistributable': False,
                        'tags': []
                    }
                ]
            }
            mocks['api_client'].get.return_value = mock_response
            
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 0
            assert 'Found 2 asset(s):' in result.output
            assert 'ID: asset1' in result.output
            assert 'ID: asset2' in result.output
    
    def test_list_empty(self, cli_runner, assets_command_mocks):
        """Test asset listing with no assets."""
        with assets_command_mocks as mocks:
            mock_response = Mock()
            mock_response.json.return_value = {'Items': []}
            mocks['api_client'].get.return_value = mock_response
            
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 0
            assert 'No assets found.' in result.output
    
    def test_list_json_output(self, cli_runner, assets_command_mocks):
        """Test asset listing with JSON output."""
        with assets_command_mocks as mocks:
            api_response = {
                'Items': [
                    {
                        'assetId': 'test-asset',
                        'assetName': 'Test Asset'
                    }
                ]
            }
            mock_response = Mock()
            mock_response.json.return_value = api_response
            mocks['api_client'].get.return_value = mock_response
            
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert output_json == api_response
    
    def test_list_with_page_size(self, cli_runner, assets_command_mocks):
        """Test asset listing with page size."""
        with assets_command_mocks as mocks:
            mock_response = Mock()
            mock_response.json.return_value = {
                'Items': [{'assetId': 'asset1', 'assetName': 'Asset 1'}],
                'NextToken': 'next-token-123'
            }
            mocks['api_client'].get.return_value = mock_response
            
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database',
                '--page-size', '50'
            ])
            
            assert result.exit_code == 0
            assert 'Found 1 asset(s):' in result.output
            assert 'Next token: next-token-123' in result.output
            assert 'Use --starting-token to get the next page' in result.output
            
            # Verify API call includes pageSize
            call_args = mocks['api_client'].get.call_args
            assert call_args[1]['params']['pageSize'] == 50
    
    def test_list_with_starting_token(self, cli_runner, assets_command_mocks):
        """Test asset listing with starting token."""
        with assets_command_mocks as mocks:
            mock_response = Mock()
            mock_response.json.return_value = {
                'Items': [{'assetId': 'asset2', 'assetName': 'Asset 2'}]
            }
            mocks['api_client'].get.return_value = mock_response
            
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database',
                '--starting-token', 'token-123',
                '--page-size', '50'
            ])
            
            assert result.exit_code == 0
            assert 'Found 1 asset(s):' in result.output
            
            # Verify API call includes startingToken and pageSize
            call_args = mocks['api_client'].get.call_args
            assert call_args[1]['params']['startingToken'] == 'token-123'
            assert call_args[1]['params']['pageSize'] == 50
    
    def test_list_auto_paginate(self, cli_runner, assets_command_mocks):
        """Test asset listing with auto-pagination."""
        with assets_command_mocks as mocks:
            # Mock multiple pages
            mock_response1 = Mock()
            mock_response1.json.return_value = {
                'Items': [{'assetId': f'asset{i}', 'assetName': f'Asset {i}'} for i in range(100)],
                'NextToken': 'token1'
            }
            mock_response2 = Mock()
            mock_response2.json.return_value = {
                'Items': [{'assetId': f'asset{i}', 'assetName': f'Asset {i}'} for i in range(100, 150)],
                'NextToken': None
            }
            mocks['api_client'].get.side_effect = [mock_response1, mock_response2]
            
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database',
                '--auto-paginate'
            ])
            
            assert result.exit_code == 0
            assert 'Auto-paginated: Retrieved 150 items in 2 page(s)' in result.output
            assert 'Found 150 asset(s):' in result.output
            
            # Verify API was called twice
            assert mocks['api_client'].get.call_count == 2
    
    def test_list_auto_paginate_with_max_items(self, cli_runner, assets_command_mocks):
        """Test asset listing with auto-pagination and custom max-items."""
        with assets_command_mocks as mocks:
            # Mock two pages
            mock_response1 = Mock()
            mock_response1.json.return_value = {
                'Items': [{'assetId': f'asset{i}', 'assetName': f'Asset {i}'} for i in range(100)],
                'NextToken': 'token1'
            }
            mock_response2 = Mock()
            mock_response2.json.return_value = {
                'Items': [{'assetId': f'asset{i}', 'assetName': f'Asset {i}'} for i in range(100, 150)],
                'NextToken': 'token2'
            }
            mocks['api_client'].get.side_effect = [mock_response1, mock_response2]
            
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database',
                '--auto-paginate',
                '--max-items', '150'
            ])
            
            assert result.exit_code == 0
            assert 'Auto-paginated: Retrieved 150 items in 2 page(s)' in result.output
            
            # Verify API was called twice and maxItems was NOT passed
            assert mocks['api_client'].get.call_count == 2
            for call in mocks['api_client'].get.call_args_list:
                assert 'maxItems' not in call[1]['params']
    
    def test_list_auto_paginate_with_page_size(self, cli_runner, assets_command_mocks):
        """Test asset listing with auto-pagination and custom page size."""
        with assets_command_mocks as mocks:
            mock_response = Mock()
            mock_response.json.return_value = {
                'Items': [{'assetId': f'asset{i}', 'assetName': f'Asset {i}'} for i in range(50)],
                'NextToken': None
            }
            mocks['api_client'].get.return_value = mock_response
            
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database',
                '--auto-paginate',
                '--page-size', '50'
            ])
            
            assert result.exit_code == 0
            assert 'Auto-paginated: Retrieved 50 items in 1 page(s)' in result.output
            
            # Verify API call includes pageSize but NOT maxItems
            call_args = mocks['api_client'].get.call_args
            assert call_args[1]['params']['pageSize'] == 50
            assert 'maxItems' not in call_args[1]['params']
    
    def test_list_auto_paginate_conflict(self, cli_runner, assets_command_mocks):
        """Test that auto-paginate conflicts with starting-token."""
        with assets_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database',
                '--auto-paginate',
                '--starting-token', 'token123'
            ])
            
            assert result.exit_code == 1
            assert 'Cannot use --auto-paginate with --starting-token' in result.output
    
    def test_list_max_items_without_auto_paginate_warning(self, cli_runner, assets_command_mocks):
        """Test that max-items without auto-paginate shows warning."""
        with assets_command_mocks as mocks:
            mock_response = Mock()
            mock_response.json.return_value = {
                'Items': [{'assetId': 'asset1', 'assetName': 'Asset 1'}]
            }
            mocks['api_client'].get.return_value = mock_response
            
            result = cli_runner.invoke(cli, [
                'assets', 'list',
                '-d', 'test-database',
                '--max-items', '100'
            ])
            
            assert result.exit_code == 0
            assert 'Warning: --max-items only applies with --auto-paginate' in result.output
            
            # Verify maxItems was NOT passed to API
            call_args = mocks['api_client'].get.call_args
            assert 'maxItems' not in call_args[1]['params']


class TestAssetArchiveCommand:
    """Test asset archive command."""
    
    def test_archive_help(self, cli_runner):
        """Test archive command help."""
        result = cli_runner.invoke(cli, ['assets', 'archive', '--help'])
        assert result.exit_code == 0
        assert 'Archive an asset (soft delete)' in result.output
        assert '--database' in result.output
        assert '--reason' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_archive_success(self, cli_runner, assets_command_mocks):
        """Test successful asset archiving."""
        with assets_command_mocks as mocks:
            mocks['api_client'].archive_asset.return_value = {
                'message': 'Asset archived successfully',
                'operation': 'archive',
                'timestamp': '2024-01-15T10:30:00Z'
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'archive', 'test-asset',
                '-d', 'test-database',
                '--reason', 'No longer needed'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset archived successfully!' in result.output
            assert 'test-asset' in result.output
            
            # Verify API call
            mocks['api_client'].archive_asset.assert_called_once_with(
                'test-database', 'test-asset', 'No longer needed'
            )
    
    def test_archive_without_reason(self, cli_runner, assets_command_mocks):
        """Test asset archiving without reason."""
        with assets_command_mocks as mocks:
            mocks['api_client'].archive_asset.return_value = {
                'success': True,
                'message': 'Asset archived successfully',
                'assetId': 'test-asset',
                'operation': 'archive',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'archive', 'test-asset',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset archived successfully!' in result.output
            assert 'test-asset' in result.output
            assert 'test-database' in result.output
            assert 'archived state' in result.output
            
            # Verify API call without reason
            mocks['api_client'].archive_asset.assert_called_once_with('test-database', 'test-asset', None)
    
    def test_archive_json_input_file(self, cli_runner, assets_command_mocks):
        """Test asset archive with JSON input from file."""
        with assets_command_mocks as mocks:
            mocks['api_client'].archive_asset.return_value = {
                'success': True,
                'message': 'Asset archived successfully',
                'assetId': 'json-asset',
                'operation': 'archive',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            
            json_data = {
                'databaseId': 'json-database',
                'assetId': 'json-asset',
                'reason': 'JSON reason'
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'assets', 'archive', 'test-asset',
                    '-d', 'test-database',
                    '--json-input', 'test.json'
                ])
            
            assert result.exit_code == 0
            assert '✓ Asset archived successfully!' in result.output
            
            # Verify API call uses JSON data
            mocks['api_client'].archive_asset.assert_called_once_with('json-database', 'json-asset', 'JSON reason')
    
    def test_archive_json_output(self, cli_runner, assets_command_mocks):
        """Test asset archive with JSON output."""
        with assets_command_mocks as mocks:
            api_response = {
                'success': True,
                'message': 'Asset archived successfully',
                'assetId': 'test-asset',
                'operation': 'archive',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            mocks['api_client'].archive_asset.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'assets', 'archive', 'test-asset',
                '-d', 'test-database',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should contain JSON in output (may have CLI messages too)
            assert 'Asset archived successfully' in result.output
            # Extract JSON from output
            lines = result.output.strip().split('\n')
            json_lines = []
            in_json = False
            for line in lines:
                if line.strip().startswith('{'):
                    in_json = True
                if in_json:
                    json_lines.append(line)
                if line.strip().endswith('}'):
                    break
            if json_lines:
                json_output = '\n'.join(json_lines)
                output_json = json.loads(json_output)
                assert output_json['assetId'] == 'test-asset'
    
    def test_archive_asset_not_found(self, cli_runner, assets_command_mocks):
        """Test archive command with asset not found."""
        with assets_command_mocks as mocks:
            mocks['api_client'].archive_asset.side_effect = AssetNotFoundError("Asset not found")
            
            result = cli_runner.invoke(cli, [
                'assets', 'archive', 'nonexistent-asset',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Not Found' in result.output
            assert 'vamscli assets get' in result.output
    
    def test_archive_already_archived(self, cli_runner, assets_command_mocks):
        """Test archive command with already archived asset."""
        with assets_command_mocks as mocks:
            mocks['api_client'].archive_asset.side_effect = AssetAlreadyArchivedError("Asset already archived")
            
            result = cli_runner.invoke(cli, [
                'assets', 'archive', 'test-asset',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Already Archived' in result.output
            assert '--show-archived' in result.output


class TestAssetDeleteCommand:
    """Test asset delete command."""
    
    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(cli, ['assets', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Permanently delete an asset' in result.output
        assert 'WARNING: This action cannot be undone!' in result.output
        assert '--database' in result.output
        assert '--reason' in result.output
        assert '--confirm' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    @patch('click.confirm')
    def test_delete_success(self, mock_confirm, cli_runner, assets_command_mocks):
        """Test successful asset deletion."""
        with assets_command_mocks as mocks:
            mocks['api_client'].delete_asset_permanent.return_value = {
                'message': 'Asset deleted successfully',
                'operation': 'delete',
                'timestamp': '2024-01-15T10:30:00Z'
            }
            
            mock_confirm.return_value = True  # User confirms deletion
            
            result = cli_runner.invoke(cli, [
                'assets', 'delete', 'test-asset',
                '-d', 'test-database',
                '--confirm',
                '--reason', 'Project cancelled'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset permanently deleted!' in result.output
            assert 'test-asset' in result.output
            
            # Verify API call
            mocks['api_client'].delete_asset_permanent.assert_called_once_with(
                'test-database', 'test-asset', 'Project cancelled', True
            )
    
    def test_delete_no_confirm_flag(self, cli_runner, assets_command_mocks):
        """Test delete command without confirm flag."""
        with assets_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'delete', 'test-asset',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 1
            assert 'Permanent deletion requires explicit confirmation!' in result.output
            assert 'Use --confirm flag' in result.output
            assert 'cannot be undone' in result.output
            
            # Verify API was not called
            mocks['api_client'].delete_asset_permanent.assert_not_called()
    
    @patch('click.confirm')
    def test_delete_user_cancels(self, mock_confirm, cli_runner, assets_command_mocks):
        """Test delete command when user cancels confirmation."""
        with assets_command_mocks as mocks:
            mock_confirm.return_value = False  # User cancels deletion
            
            result = cli_runner.invoke(cli, [
                'assets', 'delete', 'test-asset',
                '-d', 'test-database',
                '--confirm'
            ])
            
            assert result.exit_code == 0
            assert 'Deletion cancelled' in result.output
            
            # Verify API was not called
            mocks['api_client'].delete_asset_permanent.assert_not_called()
    
    @patch('click.confirm')
    def test_delete_asset_not_found(self, mock_confirm, cli_runner, assets_command_mocks):
        """Test delete command with asset not found."""
        with assets_command_mocks as mocks:
            mocks['api_client'].delete_asset_permanent.side_effect = AssetNotFoundError("Asset not found")
            mock_confirm.return_value = True  # User confirms deletion
            
            result = cli_runner.invoke(cli, [
                'assets', 'delete', 'nonexistent-asset',
                '-d', 'test-database',
                '--confirm'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Not Found' in result.output
            assert '--show-archived' in result.output
    
    @patch('click.confirm')
    def test_delete_json_input_file(self, mock_confirm, cli_runner, assets_command_mocks):
        """Test delete command with JSON input from file."""
        with assets_command_mocks as mocks:
            mocks['api_client'].delete_asset_permanent.return_value = {
                'success': True,
                'message': 'Asset permanently deleted',
                'assetId': 'json-asset',
                'operation': 'delete',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            
            mock_confirm.return_value = True  # User confirms deletion
            
            json_data = {
                'databaseId': 'json-database',
                'assetId': 'json-asset',
                'reason': 'JSON reason',
                'confirmPermanentDelete': True
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'assets', 'delete', 'test-asset',
                    '-d', 'test-database',
                    '--confirm',  # Add --confirm flag to skip interactive prompt
                    '--json-input', 'test.json'
                ])
            
            assert result.exit_code == 0
            assert '✓ Asset permanently deleted!' in result.output
            
            # Verify API call uses JSON data
            mocks['api_client'].delete_asset_permanent.assert_called_once_with('json-database', 'json-asset', 'JSON reason', True)
    
    @patch('click.confirm')
    def test_delete_json_output(self, mock_confirm, cli_runner, assets_command_mocks):
        """Test delete command with JSON output."""
        with assets_command_mocks as mocks:
            api_response = {
                'success': True,
                'message': 'Asset permanently deleted',
                'assetId': 'test-asset',
                'operation': 'delete',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            mocks['api_client'].delete_asset_permanent.return_value = api_response
            
            mock_confirm.return_value = True  # User confirms deletion
            
            result = cli_runner.invoke(cli, [
                'assets', 'delete', 'test-asset',
                '-d', 'test-database',
                '--confirm',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should contain JSON in output (may have CLI messages too)
            assert 'Asset permanently deleted' in result.output
            # Extract JSON from output
            lines = result.output.strip().split('\n')
            json_lines = []
            in_json = False
            for line in lines:
                if line.strip().startswith('{'):
                    in_json = True
                if in_json:
                    json_lines.append(line)
                if line.strip().endswith('}'):
                    break
            if json_lines:
                json_output = '\n'.join(json_lines)
                output_json = json.loads(json_output)
                assert output_json['assetId'] == 'test-asset'
    
    @patch('click.confirm')
    def test_delete_deletion_error(self, mock_confirm, cli_runner, assets_command_mocks):
        """Test delete command with deletion error (business logic exception)."""
        with assets_command_mocks as mocks:
            mocks['api_client'].delete_asset_permanent.side_effect = AssetDeletionError("Deletion confirmation required")
            
            mock_confirm.return_value = True  # User confirms deletion
            
            # Use --confirm flag and JSON input to avoid interactive confirmation prompt
            json_data = {
                'databaseId': 'test-database',
                'assetId': 'test-asset',
                'reason': 'Test deletion',
                'confirmPermanentDelete': True
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'assets', 'delete', 'test-asset',
                    '-d', 'test-database',
                    '--confirm',  # Add --confirm flag to skip interactive prompt
                    '--json-input', 'test.json'
                ])
            
            assert result.exit_code == 1
            assert '✗ Deletion Error' in result.output
            assert 'Deletion confirmation required' in result.output


class TestAssetDownloadCommand:
    """Test asset download command."""
    
    def test_download_help(self, cli_runner):
        """Test download command help."""
        result = cli_runner.invoke(cli, ['assets', 'download', '--help'])
        assert result.exit_code == 0
        assert 'Download files from an asset' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--file-key' in result.output
        assert '--recursive' in result.output
        assert '--shareable-links-only' in result.output
        assert '--json-output' in result.output
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_whole_asset_success(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test downloading all files from an asset."""
        with assets_command_mocks as mocks:
            # Mock file listing
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {'relativePath': '/model.gltf', 'isFolder': False, 'size': 1024},
                    {'relativePath': '/texture.jpg', 'isFolder': False, 'size': 2048}
                ]
            }
            
            # Mock download URLs
            mocks['api_client'].download_asset_file.side_effect = [
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf', 'expiresIn': 86400},
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/texture.jpg', 'expiresIn': 86400}
            ]
            
            # Mock async download result
            mock_asyncio_run.return_value = {
                'overall_success': True,
                'total_files': 2,
                'successful_files': 2,
                'failed_files': 0,
                'total_size': 3072,
                'total_size_formatted': '3.0 KB',
                'download_duration': 1.5,
                'average_speed': 2048,
                'average_speed_formatted': '2.0 KB/s',
                'successful_downloads': [
                    {'relative_key': '/model.gltf', 'local_path': '/tmp/model.gltf', 'size': 1024},
                    {'relative_key': '/texture.jpg', 'local_path': '/tmp/texture.jpg', 'size': 2048}
                ],
                'failed_downloads': []
            }
            
            # Mock file existence verification
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('pathlib.Path.stat') as mock_stat:
                mock_stat.return_value.st_size = 1024
                
                result = cli_runner.invoke(cli, [
                    'assets', 'download', '/tmp',
                    '-d', 'test-database',
                    '-a', 'test-asset'
                ])
            
            assert result.exit_code == 0
            assert '✓ Download completed successfully!' in result.output
            assert 'Total files: 2' in result.output
            assert 'Successful: 2' in result.output
            assert 'Failed: 0' in result.output
            
            # Verify API calls
            mocks['api_client'].list_asset_files.assert_called_once()
            assert mocks['api_client'].download_asset_file.call_count == 2
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_single_file_success(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test downloading a single file."""
        with assets_command_mocks as mocks:
            # Mock download URL
            mocks['api_client'].download_asset_file.return_value = {
                'downloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf',
                'expiresIn': 86400
            }
            
            # Mock async download result
            mock_asyncio_run.return_value = {
                'overall_success': True,
                'total_files': 1,
                'successful_files': 1,
                'failed_files': 0,
                'total_size': 1024,
                'total_size_formatted': '1.0 KB',
                'download_duration': 0.5,
                'average_speed': 2048,
                'average_speed_formatted': '2.0 KB/s',
                'successful_downloads': [
                    {'relative_key': '/model.gltf', 'local_path': '/tmp/model.gltf', 'size': 1024}
                ],
                'failed_downloads': []
            }
            
            # Mock file existence verification
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('pathlib.Path.stat') as mock_stat:
                mock_stat.return_value.st_size = 1024
                
                result = cli_runner.invoke(cli, [
                    'assets', 'download', '/tmp',
                    '-d', 'test-database',
                    '-a', 'test-asset',
                    '--file-key', '/model.gltf'
                ])
            
            assert result.exit_code == 0
            assert '✓ Download completed successfully!' in result.output
            assert 'Total files: 1' in result.output
            
            # Verify API call
            mocks['api_client'].download_asset_file.assert_called_once_with('test-database', 'test-asset', '/model.gltf')
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_root_folder_filters_folders(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test downloading from root folder filters out folder objects."""
        with assets_command_mocks as mocks:
            # Mock file listing with root folder and files
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {'relativePath': '/', 'isFolder': True},  # Root folder - should be filtered
                    {'relativePath': '/model.gltf', 'isFolder': False, 'size': 1024},
                    {'relativePath': '/texture.jpg', 'isFolder': False, 'size': 2048},
                    {'relativePath': '/subfolder/', 'isFolder': True}  # Subfolder - should be filtered
                ]
            }
            
            # Mock download URLs (only for files, not folders)
            mocks['api_client'].download_asset_file.side_effect = [
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf', 'expiresIn': 86400},
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/texture.jpg', 'expiresIn': 86400}
            ]
            
            # Mock async download result
            mock_asyncio_run.return_value = {
                'overall_success': True,
                'total_files': 2,  # Only 2 files, not 4
                'successful_files': 2,
                'failed_files': 0,
                'total_size': 3072,
                'total_size_formatted': '3.0 KB',
                'download_duration': 1.5,
                'average_speed': 2048,
                'average_speed_formatted': '2.0 KB/s',
                'successful_downloads': [
                    {'relative_key': '/model.gltf', 'local_path': '/tmp/model.gltf', 'size': 1024},
                    {'relative_key': '/texture.jpg', 'local_path': '/tmp/texture.jpg', 'size': 2048}
                ],
                'failed_downloads': []
            }
            
            # Mock file existence verification
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('pathlib.Path.stat') as mock_stat:
                # Create a proper mock stat result
                stat_result = Mock()
                stat_result.st_size = 1024
                stat_result.st_mode = 0o100644  # Regular file mode
                mock_stat.return_value = stat_result
                
                result = cli_runner.invoke(cli, [
                    'assets', 'download', '/tmp',
                    '-d', 'test-database',
                    '-a', 'test-asset',
                    '--file-key', '/'
                ])
            
            assert result.exit_code == 0
            assert '✓ Download completed successfully!' in result.output
            assert 'Total files: 2' in result.output
            
            # Verify only files were requested for download, not folders
            assert mocks['api_client'].download_asset_file.call_count == 2
            # Verify no attempt to download "/" or "/subfolder/"
            for call in mocks['api_client'].download_asset_file.call_args_list:
                file_key = call[0][2]  # Third argument is file_key
                # Ensure we're not trying to download folder paths
                assert file_key in ['/model.gltf', '/texture.jpg']
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_recursive_folder(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test downloading a folder recursively."""
        with assets_command_mocks as mocks:
            # Mock file listing with folder objects that should be filtered
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {'relativePath': '/models/', 'isFolder': True},  # Folder - should be filtered
                    {'relativePath': '/models/model1.gltf', 'isFolder': False, 'size': 1024},
                    {'relativePath': '/models/model2.gltf', 'isFolder': False, 'size': 2048},
                    {'relativePath': '/models/subfolder/', 'isFolder': True},  # Folder - should be filtered
                    {'relativePath': '/models/subfolder/model3.gltf', 'isFolder': False, 'size': 512}
                ]
            }
            
            # Mock download URLs
            mocks['api_client'].download_asset_file.side_effect = [
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/model1.gltf', 'expiresIn': 86400},
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/model2.gltf', 'expiresIn': 86400},
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/model3.gltf', 'expiresIn': 86400}
            ]
            
            # Mock async download result
            mock_asyncio_run.return_value = {
                'overall_success': True,
                'total_files': 3,
                'successful_files': 3,
                'failed_files': 0,
                'total_size': 3584,
                'total_size_formatted': '3.5 KB',
                'download_duration': 2.0,
                'average_speed': 1792,
                'average_speed_formatted': '1.8 KB/s',
                'successful_downloads': [],
                'failed_downloads': []
            }
            
            # Mock file existence verification
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('pathlib.Path.stat') as mock_stat:
                mock_stat.return_value.st_size = 1024
                
                result = cli_runner.invoke(cli, [
                    'assets', 'download', '/tmp',
                    '-d', 'test-database',
                    '-a', 'test-asset',
                    '--file-key', '/models/',
                    '--recursive'
                ])
            
            assert result.exit_code == 0
            assert '✓ Download completed successfully!' in result.output
            assert 'Total files: 3' in result.output
            assert 'Successful: 3' in result.output
    
    @patch('vamscli.commands.assets.FileTreeBuilder.get_files_under_prefix')
    @patch('vamscli.commands.assets.FileTreeBuilder.flatten_file_list')
    def test_download_flattened_conflict_abort(self, mock_flatten, mock_get_files, cli_runner, assets_command_mocks):
        """Test flattened download with filename conflicts in JSON mode (should fail)."""
        with assets_command_mocks as mocks:
            # Mock file listing with conflicting names
            all_files = [
                {'relativePath': '/models/folder1/model.gltf', 'isFolder': False, 'size': 1024},
                {'relativePath': '/models/folder2/model.gltf', 'isFolder': False, 'size': 2048}
            ]
            
            mocks['api_client'].list_asset_files.return_value = {
                'items': all_files
            }
            
            # Mock get_files_under_prefix to return files under /models/
            mock_get_files.return_value = all_files
            
            # Mock flatten to raise FileDownloadError due to conflicts
            mock_flatten.side_effect = FileDownloadError("Filename conflicts detected in flattened download: model.gltf")
            
            result = cli_runner.invoke(cli, [
                'assets', 'download', '/tmp',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--file-key', '/models/',
                '--recursive',
                '--flatten-download-tree',
                '--json-output'  # JSON mode should fail on conflicts
            ])
            
            assert result.exit_code == 1
            assert 'Filename conflicts detected' in result.output or 'Download Error' in result.output
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_asset_preview(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test downloading asset preview."""
        with assets_command_mocks as mocks:
            # Mock preview download
            mocks['api_client'].download_asset_preview.return_value = {
                'downloadUrl': 'https://s3.amazonaws.com/bucket/preview.jpg',
                'key': 'preview.jpg',
                'size': 512,
                'expiresIn': 86400
            }
            
            # Mock async download result
            mock_asyncio_run.return_value = {
                'overall_success': True,
                'total_files': 1,
                'successful_files': 1,
                'failed_files': 0,
                'total_size': 512,
                'total_size_formatted': '512 B',
                'download_duration': 0.3,
                'average_speed': 1706,
                'average_speed_formatted': '1.7 KB/s',
                'successful_downloads': [],
                'failed_downloads': []
            }
            
            # Mock file existence verification
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('pathlib.Path.stat') as mock_stat:
                mock_stat.return_value.st_size = 512
                
                result = cli_runner.invoke(cli, [
                    'assets', 'download', '/tmp',
                    '-d', 'test-database',
                    '-a', 'test-asset',
                    '--asset-preview'
                ])
            
            assert result.exit_code == 0
            assert '✓ Download completed successfully!' in result.output
            
            # Verify API call
            mocks['api_client'].download_asset_preview.assert_called_once_with('test-database', 'test-asset')
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_with_file_previews(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test downloading file with its preview."""
        with assets_command_mocks as mocks:
            # Mock file listing
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {'relativePath': '/model.gltf', 'isFolder': False, 'size': 1024}
                ]
            }
            
            # Mock download URLs (main file + preview)
            mocks['api_client'].download_asset_file.side_effect = [
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf', 'expiresIn': 86400},
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf_preview', 'expiresIn': 86400}
            ]
            
            # Mock async download result
            mock_asyncio_run.return_value = {
                'overall_success': True,
                'total_files': 2,
                'successful_files': 2,
                'failed_files': 0,
                'total_size': 1024,
                'total_size_formatted': '1.0 KB',
                'download_duration': 1.0,
                'average_speed': 1024,
                'average_speed_formatted': '1.0 KB/s',
                'successful_downloads': [],
                'failed_downloads': []
            }
            
            # Mock file existence verification
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('pathlib.Path.stat') as mock_stat:
                mock_stat.return_value.st_size = 1024
                
                result = cli_runner.invoke(cli, [
                    'assets', 'download', '/tmp',
                    '-d', 'test-database',
                    '-a', 'test-asset',
                    '--file-key', '/model.gltf',
                    '--file-previews'
                ])
            
            assert result.exit_code == 0
            assert '✓ Download completed successfully!' in result.output
            
            # Verify both main file and preview were requested
            assert mocks['api_client'].download_asset_file.call_count == 2
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_with_failures(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test download with some file failures."""
        with assets_command_mocks as mocks:
            # Mock file listing
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {'relativePath': '/model.gltf', 'isFolder': False, 'size': 1024},
                    {'relativePath': '/texture.jpg', 'isFolder': False, 'size': 2048}
                ]
            }
            
            # Mock download URLs
            mocks['api_client'].download_asset_file.side_effect = [
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf', 'expiresIn': 86400},
                {'downloadUrl': 'https://s3.amazonaws.com/bucket/texture.jpg', 'expiresIn': 86400}
            ]
            
            # Mock async download result with one failure
            mock_asyncio_run.return_value = {
                'overall_success': False,
                'total_files': 2,
                'successful_files': 1,
                'failed_files': 1,
                'total_size': 3072,
                'total_size_formatted': '3.0 KB',
                'download_duration': 2.0,
                'average_speed': 512,
                'average_speed_formatted': '512 B/s',
                'successful_downloads': [
                    {'relative_key': '/model.gltf', 'local_path': '/tmp/model.gltf', 'size': 1024}
                ],
                'failed_downloads': [
                    {'relative_key': '/texture.jpg', 'local_path': '/tmp/texture.jpg', 'error': 'Connection timeout'}
                ]
            }
            
            # Mock file existence verification
            with patch('pathlib.Path.exists', side_effect=[True, False]), \
                 patch('pathlib.Path.stat') as mock_stat:
                mock_stat.return_value.st_size = 1024
                
                result = cli_runner.invoke(cli, [
                    'assets', 'download', '/tmp',
                    '-d', 'test-database',
                    '-a', 'test-asset'
                ])
            
            assert result.exit_code == 0
            assert '⚠ Download completed with errors' in result.output
            assert 'Total files: 2' in result.output
            assert 'Successful: 1' in result.output
            assert 'Failed: 1' in result.output
            assert 'Failed downloads:' in result.output
            assert '/texture.jpg' in result.output
            assert 'Connection timeout' in result.output
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_verification_failure(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test download with file verification failure."""
        with assets_command_mocks as mocks:
            # Mock file listing
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {'relativePath': '/model.gltf', 'isFolder': False, 'size': 1024}
                ]
            }
            
            # Mock download URL
            mocks['api_client'].download_asset_file.return_value = {
                'downloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf',
                'expiresIn': 86400
            }
            
            # Mock async download result
            mock_asyncio_run.return_value = {
                'overall_success': True,
                'total_files': 1,
                'successful_files': 1,
                'failed_files': 0,
                'total_size': 1024,
                'total_size_formatted': '1.0 KB',
                'download_duration': 1.0,
                'average_speed': 1024,
                'average_speed_formatted': '1.0 KB/s',
                'successful_downloads': [
                    {'relative_key': '/model.gltf', 'local_path': '/tmp/model.gltf', 'size': 1024}
                ],
                'failed_downloads': []
            }
            
            # Mock file existence but wrong size
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('pathlib.Path.stat') as mock_stat:
                mock_stat.return_value.st_size = 512  # Wrong size
                
                result = cli_runner.invoke(cli, [
                    'assets', 'download', '/tmp',
                    '-d', 'test-database',
                    '-a', 'test-asset'
                ])
            
            assert result.exit_code == 0
            assert 'Verified: 0' in result.output
            assert 'Verification failures: 1' in result.output
            assert 'size_mismatch' in result.output
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_json_output(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test download with JSON output."""
        with assets_command_mocks as mocks:
            # Mock file listing
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {'relativePath': '/model.gltf', 'isFolder': False, 'size': 1024}
                ]
            }
            
            # Mock download URL
            mocks['api_client'].download_asset_file.return_value = {
                'downloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf',
                'expiresIn': 86400
            }
            
            # Mock async download result
            mock_asyncio_run.return_value = {
                'overall_success': True,
                'total_files': 1,
                'successful_files': 1,
                'failed_files': 0,
                'total_size': 1024,
                'total_size_formatted': '1.0 KB',
                'download_duration': 1.0,
                'average_speed': 1024,
                'average_speed_formatted': '1.0 KB/s',
                'successful_downloads': [],
                'failed_downloads': []
            }
            
            # Mock file existence verification
            with patch('pathlib.Path.exists', return_value=True), \
                 patch('pathlib.Path.stat') as mock_stat:
                mock_stat.return_value.st_size = 1024
                
                result = cli_runner.invoke(cli, [
                    'assets', 'download', '/tmp',
                    '-d', 'test-database',
                    '-a', 'test-asset',
                    '--json-output'
                ])
            
            assert result.exit_code == 0
            
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert output_json['overall_success'] == True
            assert output_json['total_files'] == 1
            assert output_json['successful_files'] == 1
            assert output_json['verified_files'] == 1
    
    def test_download_shareable_links_only(self, cli_runner, assets_command_mocks):
        """Test download command with shareable links only."""
        with assets_command_mocks as mocks:
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {
                        'relativePath': '/model.gltf',
                        'isFolder': False
                    },
                    {
                        'relativePath': '/textures/texture1.jpg',
                        'isFolder': False
                    }
                ]
            }
            mocks['api_client'].download_asset_file.return_value = {
                'downloadUrl': 'https://example.com/download/url',
                'expiresIn': 86400
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'download',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--shareable-links-only'
            ])
            
            assert result.exit_code == 0
            assert '✓ Shareable links generated successfully!' in result.output
            assert 'Files (2):' in result.output
    
    def test_download_asset_preview_only(self, cli_runner, assets_command_mocks):
        """Test download command for asset preview only."""
        with assets_command_mocks as mocks:
            mocks['api_client'].download_asset_preview.return_value = {
                'downloadUrl': 'https://example.com/preview/url',
                'expiresIn': 86400
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'download',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--asset-preview',
                '--shareable-links-only'
            ])
            
            assert result.exit_code == 0
            assert '✓ Shareable links generated successfully!' in result.output
            assert 'asset_preview' in result.output
    
    def test_download_missing_local_path(self, cli_runner, assets_command_mocks):
        """Test download command without local path for actual download."""
        with assets_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'download',
                '-d', 'test-database',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert 'Local path is required for downloads' in result.output
    
    def test_download_conflicting_options(self, cli_runner, assets_command_mocks):
        """Test download command with conflicting options."""
        with assets_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'download', '/local/path',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--asset-preview',
                '--file-key', '/model.gltf'
            ])
            
            assert result.exit_code == 1
            assert 'Cannot specify both --asset-preview and --file-key' in result.output
    
    def test_download_shareable_links_single_file(self, cli_runner, assets_command_mocks):
        """Test download command with shareable links for single file."""
        with assets_command_mocks as mocks:
            mocks['api_client'].download_asset_file.return_value = {
                'downloadUrl': 'https://s3.amazonaws.com/bucket/file.gltf?signature=...',
                'expiresIn': 86400,
                'downloadType': 'assetFile'
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'download',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--file-key', '/model.gltf',
                '--shareable-links-only'
            ])
            
            assert result.exit_code == 0
            assert '✓ Shareable links generated successfully!' in result.output
            assert '/model.gltf' in result.output
            assert 'https://s3.amazonaws.com' in result.output
            assert 'Expires: in 24 hours' in result.output
            
            # Verify API call
            mocks['api_client'].download_asset_file.assert_called_once_with('test-database', 'test-asset', '/model.gltf')
    
    def test_download_shareable_links_whole_asset(self, cli_runner, assets_command_mocks):
        """Test download command with shareable links for whole asset."""
        with assets_command_mocks as mocks:
            # Mock file listing
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {'relativePath': '/model.gltf', 'isFolder': False, 'size': 1024},
                    {'relativePath': '/texture.jpg', 'isFolder': False, 'size': 2048}
                ]
            }
            
            # Mock download URLs
            mocks['api_client'].download_asset_file.side_effect = [
                {
                    'downloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf?signature=...',
                    'expiresIn': 86400,
                    'downloadType': 'assetFile'
                },
                {
                    'downloadUrl': 'https://s3.amazonaws.com/bucket/texture.jpg?signature=...',
                    'expiresIn': 86400,
                    'downloadType': 'assetFile'
                }
            ]
            
            result = cli_runner.invoke(cli, [
                'assets', 'download',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--shareable-links-only'
            ])
            
            assert result.exit_code == 0
            assert '✓ Shareable links generated successfully!' in result.output
            assert '/model.gltf' in result.output
            assert '/texture.jpg' in result.output
            assert 'Total: 2 file(s)' in result.output
            
            # Verify API calls
            mocks['api_client'].list_asset_files.assert_called_once()
            assert mocks['api_client'].download_asset_file.call_count == 2
    
    @patch('vamscli.commands.assets.asyncio.run')
    def test_download_whole_asset_no_files(self, mock_asyncio_run, cli_runner, assets_command_mocks):
        """Test download command when asset has no files."""
        with assets_command_mocks as mocks:
            # Mock empty file listing
            mocks['api_client'].list_asset_files.return_value = {
                'items': []
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'download', '/local/path',
                '-d', 'test-database',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert '✗ Download Error' in result.output
            assert 'currently has no files to download' in result.output
    
    def test_download_validation_errors(self, cli_runner, assets_command_mocks):
        """Test download command validation errors."""
        with assets_command_mocks as mocks:
            # Test file-previews without file-key
            result = cli_runner.invoke(cli, [
                'assets', 'download', '/local/path',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--file-previews'
            ])
            assert result.exit_code == 1
            assert '--file-previews requires --file-key to be specified' in result.output
    
    def test_download_no_files_in_asset(self, cli_runner, assets_command_mocks):
        """Test download command with no files in asset."""
        with assets_command_mocks as mocks:
            # Mock empty file listing
            mocks['api_client'].list_asset_files.return_value = {
                'items': []
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'download',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--shareable-links-only'
            ])
            
            assert result.exit_code == 1
            assert 'currently has no files to download' in result.output
    
    def test_download_json_input_file(self, cli_runner, assets_command_mocks):
        """Test download command with JSON input from file."""
        with assets_command_mocks as mocks:
            mocks['api_client'].download_asset_file.return_value = {
                'downloadUrl': 'https://s3.amazonaws.com/bucket/file.gltf?signature=...',
                'expiresIn': 86400,
                'downloadType': 'assetFile'
            }
            
            json_data = {
                'database': 'json-database',
                'asset': 'json-asset',
                'file_key': '/json-model.gltf',
                'shareable_links_only': True
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(cli, [
                    'assets', 'download',
                    '-d', 'placeholder-db',  # Will be overridden by JSON
                    '-a', 'placeholder-asset',  # Will be overridden by JSON
                    '--json-input', 'test.json'
                ])
            
            assert result.exit_code == 0
            assert '✓ Shareable links generated successfully!' in result.output
            
            # Verify API call uses JSON data
            mocks['api_client'].download_asset_file.assert_called_once_with('json-database', 'json-asset', '/json-model.gltf')
    
    def test_download_json_output(self, cli_runner, assets_command_mocks):
        """Test download command with JSON output."""
        with assets_command_mocks as mocks:
            mocks['api_client'].download_asset_file.return_value = {
                'downloadUrl': 'https://s3.amazonaws.com/bucket/file.gltf?signature=...',
                'expiresIn': 86400,
                'downloadType': 'assetFile'
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'download',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--file-key', '/model.gltf',
                '--shareable-links-only',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert 'shareableLinks' in output_json
            assert len(output_json['shareableLinks']) == 1
            assert output_json['shareableLinks'][0]['filePath'] == '/model.gltf'
            assert 'downloadUrl' in output_json['shareableLinks'][0]
    
    def test_download_missing_required_args(self, cli_runner):
        """Test download command with missing required arguments."""
        runner = CliRunner()
        
        # Test missing database ID
        result = runner.invoke(cli, [
            'assets', 'download', '/local/path',
            '-a', 'test-asset'
        ])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test missing asset ID
        result = runner.invoke(cli, [
            'assets', 'download', '/local/path',
            '-d', 'test-database'
        ])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_download_asset_not_found(self, cli_runner, assets_command_mocks):
        """Test download command with asset not found."""
        with assets_command_mocks as mocks:
            mocks['api_client'].download_asset_file.side_effect = AssetNotFoundError("Asset 'test-asset' not found")
            
            result = cli_runner.invoke(cli, [
                'assets', 'download',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--file-key', '/model.gltf',
                '--shareable-links-only'
            ])
            
            assert result.exit_code == 1
            assert '✗ Download Error' in result.output
            assert 'Failed to generate shareable links' in result.output
            assert 'not found' in result.output.lower()


class TestAssetCommandsIntegration:
    """Test integration scenarios for asset commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_asset_id(self, mock_main_profile_manager):
        """Test that asset commands require asset ID where appropriate."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test update without asset ID
        result = runner.invoke(cli, ['assets', 'update'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'required' in result.output.lower()
        
        # Test get without asset ID
        result = runner.invoke(cli, ['assets', 'get'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'required' in result.output.lower()
        
        # Test archive without asset ID
        result = runner.invoke(cli, ['assets', 'archive'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'required' in result.output.lower()
        
        # Test delete without asset ID
        result = runner.invoke(cli, ['assets', 'delete'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'required' in result.output.lower()
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_database_id(self, mock_main_profile_manager):
        """Test that asset commands require database ID."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test archive without database ID
        result = runner.invoke(cli, ['assets', 'archive', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test delete without database ID
        result = runner.invoke(cli, ['assets', 'delete', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    
    def test_database_not_found_error(self, cli_runner, assets_command_mocks):
        """Test database not found error handling (business logic exception)."""
        with assets_command_mocks as mocks:
            mocks['api_client'].archive_asset.side_effect = DatabaseNotFoundError("Database 'test-database' not found")
            
            result = cli_runner.invoke(cli, [
                'assets', 'archive', 'test-asset',
                '-d', 'test-database'
            ])
            
            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert 'vamscli database list' in result.output


class TestAssetUtilityFunctions:
    """Test asset utility functions."""
    
    def test_parse_tags_input_single_tags(self):
        """Test parsing individual tags."""
        from vamscli.commands.assets import parse_tags_input
        
        tags = ['tag1', 'tag2', 'tag3']
        result = parse_tags_input(tags)
        assert result == ['tag1', 'tag2', 'tag3']
    
    def test_parse_tags_input_comma_separated(self):
        """Test parsing comma-separated tags."""
        from vamscli.commands.assets import parse_tags_input
        
        tags = ['tag1,tag2', 'tag3', 'tag4,tag5,tag6']
        result = parse_tags_input(tags)
        assert result == ['tag1', 'tag2', 'tag3', 'tag4', 'tag5', 'tag6']
    
    def test_parse_tags_input_duplicates(self):
        """Test parsing tags with duplicates."""
        from vamscli.commands.assets import parse_tags_input
        
        tags = ['tag1', 'tag2', 'tag1', 'tag3', 'tag2']
        result = parse_tags_input(tags)
        assert result == ['tag1', 'tag2', 'tag3']  # Duplicates removed, order preserved
    
    def test_parse_tags_input_empty(self):
        """Test parsing empty tags."""
        from vamscli.commands.assets import parse_tags_input
        
        result = parse_tags_input([])
        assert result == []
        
        result = parse_tags_input([''])
        assert result == []
    
    def test_parse_json_input_valid_json(self):
        """Test parsing valid JSON string."""
        from vamscli.commands.assets import parse_json_input
        
        json_str = '{"assetName": "test", "description": "desc"}'
        result = parse_json_input(json_str)
        assert result == {"assetName": "test", "description": "desc"}
    
    def test_parse_json_input_invalid_json(self):
        """Test parsing invalid JSON string."""
        from vamscli.commands.assets import parse_json_input
        
        with pytest.raises(click.BadParameter):
            parse_json_input('invalid json string')
    
    def test_format_asset_output_cli_format(self):
        """Test CLI formatting of asset output."""
        from vamscli.commands.assets import format_asset_output
        
        asset_data = {
            'assetId': 'test-asset',
            'databaseId': 'test-db',
            'assetName': 'Test Asset',
            'description': 'Test description',
            'isDistributable': True,
            'tags': ['tag1', 'tag2'],
            'status': 'active'
        }
        
        result = format_asset_output(asset_data, json_output=False)
        assert 'Asset Details:' in result
        assert 'ID: test-asset' in result
        assert 'Name: Test Asset' in result
        assert 'Distributable: Yes' in result
        assert 'Tags: tag1, tag2' in result
    
    def test_format_asset_output_json_format(self):
        """Test JSON formatting of asset output."""
        from vamscli.commands.assets import format_asset_output
        
        asset_data = {
            'assetId': 'test-asset',
            'databaseId': 'test-db'
        }
        
        result = format_asset_output(asset_data, json_output=True)
        parsed = json.loads(result)
        assert parsed['assetId'] == 'test-asset'
        assert parsed['databaseId'] == 'test-db'


class TestAssetCommandsJSONHandling:
    """Test JSON input/output handling for asset commands."""
    
    def test_invalid_json_input_string(self, cli_runner, assets_command_mocks):
        """Test handling of invalid JSON input string."""
        with assets_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'create',
                '-d', 'test-database',
                '--json-input', 'invalid json'
            ])
            
            assert result.exit_code == 2  # Click parameter validation happens before our decorator
            assert 'BadParameter' in str(result.exception) or 'Invalid JSON input' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, assets_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with assets_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'create',
                '-d', 'test-database',
                '--json-input', 'nonexistent.json'
            ])
            
            assert result.exit_code == 2  # Click parameter validation happens before our decorator
            assert 'BadParameter' in str(result.exception) or 'Invalid JSON input' in result.output
    
    def test_invalid_json_input_file(self, cli_runner, assets_command_mocks):
        """Test handling of invalid JSON content in files."""
        with assets_command_mocks as mocks:
            with patch('builtins.open', mock_open(read_data='invalid json')):
                result = cli_runner.invoke(cli, [
                    'assets', 'archive', 'test-asset',
                    '-d', 'test-database',
                    '--json-input', 'invalid.json'
                ])
            
            assert result.exit_code == 2  # Click parameter validation happens before our decorator
            assert 'BadParameter' in str(result.exception) or 'Invalid JSON in input file' in result.output
    
    def test_nonexistent_json_input_file_archive(self, cli_runner, assets_command_mocks):
        """Test handling of nonexistent JSON input file for archive."""
        with assets_command_mocks as mocks:
            with patch('builtins.open', side_effect=FileNotFoundError()):
                result = cli_runner.invoke(cli, [
                    'assets', 'archive', 'test-asset',
                    '-d', 'test-database',
                    '--json-input', 'nonexistent.json'
                ])
            
            assert result.exit_code == 2  # Click parameter error
            assert 'None' in result.output  # FileNotFoundError results in None value


class TestAssetCommandsEdgeCases:
    """Test edge cases for asset commands."""
    
    def test_create_invalid_asset_data_error(self, cli_runner, assets_command_mocks):
        """Test create command with invalid asset data error."""
        with assets_command_mocks as mocks:
            mocks['api_client'].create_asset.side_effect = InvalidAssetDataError("Invalid asset data")
            
            result = cli_runner.invoke(cli, [
                'assets', 'create',
                '-d', 'test-database',
                '--name', 'Test Asset',
                '--description', 'Test description',
                '--distributable'
            ])
            
            assert result.exit_code == 1
            assert '✗ Invalid Asset Data' in result.output
            assert 'Invalid asset data' in result.output
    
    def test_update_invalid_asset_data_error(self, cli_runner, assets_command_mocks):
        """Test update command with invalid asset data error."""
        with assets_command_mocks as mocks:
            mocks['api_client'].update_asset.side_effect = InvalidAssetDataError("Invalid update data")
            
            result = cli_runner.invoke(cli, [
                'assets', 'update', 'test-asset',
                '-d', 'test-database',
                '--name', 'Updated Name'
            ])
            
            assert result.exit_code == 1
            assert '✗ Invalid Update Data' in result.output
            assert 'Invalid update data' in result.output
    
    def test_download_preview_not_found_error(self, cli_runner, assets_command_mocks):
        """Test download command with preview not found error."""
        with assets_command_mocks as mocks:
            mocks['api_client'].download_asset_preview.side_effect = Exception("Preview not available")
            
            result = cli_runner.invoke(cli, [
                'assets', 'download',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--asset-preview',
                '--shareable-links-only'
            ])
            
            assert result.exit_code == 1
            assert '✗ Download Error' in result.output
            assert 'Asset preview not available' in result.output
    

if __name__ == '__main__':
    pytest.main([__file__])
