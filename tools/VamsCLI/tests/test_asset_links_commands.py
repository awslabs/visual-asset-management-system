"""Test asset links management commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.commands.asset_links import asset_links
from vamscli.main import cli
from vamscli.utils.exceptions import (
    AssetLinkNotFoundError, AssetLinkAlreadyExistsError, CycleDetectionError,
    AssetLinkPermissionError, AssetLinkValidationError, InvalidRelationshipTypeError,
    AssetLinkOperationError, AssetNotFoundError, DatabaseNotFoundError, 
    AuthenticationError, APIError, SetupRequiredError
)


# File-level fixtures for asset-links-specific testing patterns
@pytest.fixture
def asset_links_command_mocks(generic_command_mocks):
    """Provide asset-links-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for asset-links command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('asset_links')


@pytest.fixture
def asset_links_no_setup_mocks(no_setup_command_mocks):
    """Provide asset-links command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('asset_links')


class TestAssetLinksCreateCommand:
    """Test asset links create command."""
    
    def test_create_help(self, cli_runner):
        """Test create command help."""
        result = cli_runner.invoke(asset_links, ['create', '--help'])
        assert result.exit_code == 0
        assert 'Create a new asset link between two assets' in result.output
        assert '--from-asset-id' in result.output
        assert '--from-database-id' in result.output
        assert '--to-asset-id' in result.output
        assert '--to-database-id' in result.output
        assert '--relationship-type' in result.output
        assert '--tags' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_create_success_related(self, cli_runner, asset_links_command_mocks):
        """Test successful asset link creation with related relationship."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].create_asset_link.return_value = {
                'assetLinkId': '12345678-1234-1234-1234-123456789012',
                'message': 'Asset link created successfully'
            }
            
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link created successfully!' in result.output
            assert '12345678-1234-1234-1234-123456789012' in result.output
            assert 'related' in result.output
            assert 'asset1 (db1) → To: asset2 (db2)' in result.output
            
            # Verify API call
            expected_data = {
                'fromAssetId': 'asset1',
                'fromAssetDatabaseId': 'db1',
                'toAssetId': 'asset2',
                'toAssetDatabaseId': 'db2',
                'relationshipType': 'related',
                'tags': []
            }
            mocks['api_client'].create_asset_link.assert_called_once_with(expected_data)
    
    def test_create_success_parent_child_with_tags(self, cli_runner, asset_links_command_mocks):
        """Test successful asset link creation with parentChild relationship and tags."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].create_asset_link.return_value = {
                'assetLinkId': '12345678-1234-1234-1234-123456789012',
                'message': 'Asset link created successfully'
            }
            
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'parent-asset',
                '--from-database-id', 'db1',
                '--to-asset-id', 'child-asset',
                '--to-database-id', 'db1',
                '--relationship-type', 'parentChild',
                '--tags', 'tag1',
                '--tags', 'tag2,tag3'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link created successfully!' in result.output
            assert 'parentChild' in result.output
            assert 'Tags: tag1, tag2, tag3' in result.output
            
            # Verify API call with parsed tags
            expected_data = {
                'fromAssetId': 'parent-asset',
                'fromAssetDatabaseId': 'db1',
                'toAssetId': 'child-asset',
                'toAssetDatabaseId': 'db1',
                'relationshipType': 'parentChild',
                'tags': ['tag1', 'tag2', 'tag3']
            }
            mocks['api_client'].create_asset_link.assert_called_once_with(expected_data)
    
    def test_create_json_input(self, cli_runner, asset_links_command_mocks):
        """Test create command with JSON input."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].create_asset_link.return_value = {
                'assetLinkId': '12345678-1234-1234-1234-123456789012',
                'message': 'Asset link created successfully'
            }
            
            json_data = {
                'fromAssetId': 'json-asset1',
                'fromAssetDatabaseId': 'json-db1',
                'toAssetId': 'json-asset2',
                'toAssetDatabaseId': 'json-db2',
                'relationshipType': 'related',
                'tags': ['json-tag1', 'json-tag2']
            }
            
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'ignored',
                '--from-database-id', 'ignored',
                '--to-asset-id', 'ignored',
                '--to-database-id', 'ignored',
                '--relationship-type', 'related',
                '--json-input', json.dumps(json_data)
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link created successfully!' in result.output
            
            # Verify API call uses JSON data
            mocks['api_client'].create_asset_link.assert_called_once_with(json_data)
    
    def test_create_json_output(self, cli_runner, asset_links_command_mocks):
        """Test create command with JSON output."""
        with asset_links_command_mocks as mocks:
            api_response = {
                'assetLinkId': '12345678-1234-1234-1234-123456789012',
                'message': 'Asset link created successfully'
            }
            mocks['api_client'].create_asset_link.return_value = api_response
            
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert output_json == api_response
    
    def test_create_already_exists_error(self, cli_runner, asset_links_command_mocks):
        """Test create command with asset link already exists error."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].create_asset_link.side_effect = AssetLinkAlreadyExistsError("Asset link already exists")
            
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Link Already Exists' in result.output
            assert 'relationship already exists' in result.output
    
    def test_create_cycle_detection_error(self, cli_runner, asset_links_command_mocks):
        """Test create command with cycle detection error."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].create_asset_link.side_effect = CycleDetectionError("Creating link would create cycle")
            
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'parent',
                '--from-database-id', 'db1',
                '--to-asset-id', 'child',
                '--to-database-id', 'db1',
                '--relationship-type', 'parentChild'
            ])
            
            assert result.exit_code == 1
            assert '✗ Cycle Detection Error' in result.output
            assert 'create a cycle' in result.output
    
    def test_create_no_setup(self, cli_runner, asset_links_no_setup_mocks):
        """Test create command without setup."""
        with asset_links_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-links', 'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)


class TestAssetLinksGetCommand:
    """Test asset links get command."""
    
    def test_get_help(self, cli_runner):
        """Test get command help."""
        result = cli_runner.invoke(asset_links, ['get', '--help'])
        assert result.exit_code == 0
        assert 'Get details for a specific asset link' in result.output
        assert '--asset-link-id' in result.output
        assert '--json-output' in result.output
    
    def test_get_success(self, cli_runner, asset_links_command_mocks):
        """Test successful asset link retrieval."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].get_single_asset_link.return_value = {
                'assetLink': {
                    'assetLinkId': '12345678-1234-1234-1234-123456789012',
                    'fromAssetId': 'asset1',
                    'fromAssetDatabaseId': 'db1',
                    'toAssetId': 'asset2',
                    'toAssetDatabaseId': 'db2',
                    'relationshipType': 'related',
                    'tags': ['tag1', 'tag2']
                },
                'message': 'Success'
            }
            
            result = cli_runner.invoke(asset_links, [
                'get',
                '--asset-link-id', '12345678-1234-1234-1234-123456789012'
            ])
            
            assert result.exit_code == 0
            assert 'Asset Link Details:' in result.output
            assert '12345678-1234-1234-1234-123456789012' in result.output
            assert 'related' in result.output
            assert 'asset1' in result.output
            assert 'asset2' in result.output
            assert 'tag1, tag2' in result.output
            
            # Verify API call
            mocks['api_client'].get_single_asset_link.assert_called_once_with('12345678-1234-1234-1234-123456789012')
    
    def test_get_json_output(self, cli_runner, asset_links_command_mocks):
        """Test get command with JSON output."""
        with asset_links_command_mocks as mocks:
            api_response = {
                'assetLink': {
                    'assetLinkId': '12345678-1234-1234-1234-123456789012',
                    'fromAssetId': 'asset1',
                    'fromAssetDatabaseId': 'db1',
                    'toAssetId': 'asset2',
                    'toAssetDatabaseId': 'db2',
                    'relationshipType': 'related',
                    'tags': []
                },
                'message': 'Success'
            }
            mocks['api_client'].get_single_asset_link.return_value = api_response
            
            result = cli_runner.invoke(asset_links, [
                'get',
                '--asset-link-id', '12345678-1234-1234-1234-123456789012',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert output_json == api_response
    
    def test_get_not_found(self, cli_runner, asset_links_command_mocks):
        """Test get command with asset link not found."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].get_single_asset_link.side_effect = AssetLinkNotFoundError("Asset link not found")
            
            result = cli_runner.invoke(asset_links, [
                'get',
                '--asset-link-id', 'nonexistent-link'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Link Not Found' in result.output
            assert 'vamscli asset-links list' in result.output


class TestAssetLinksUpdateCommand:
    """Test asset links update command."""
    
    def test_update_help(self, cli_runner):
        """Test update command help."""
        result = cli_runner.invoke(asset_links, ['update', '--help'])
        assert result.exit_code == 0
        assert 'Update an existing asset link' in result.output
        assert '--asset-link-id' in result.output
        assert '--tags' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_update_success(self, cli_runner, asset_links_command_mocks):
        """Test successful asset link update."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].update_asset_link.return_value = {
                'message': 'Asset link updated successfully'
            }
            
            result = cli_runner.invoke(asset_links, [
                'update',
                '--asset-link-id', '12345678-1234-1234-1234-123456789012',
                '--tags', 'new-tag1',
                '--tags', 'new-tag2'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link updated successfully!' in result.output
            assert '12345678-1234-1234-1234-123456789012' in result.output
            assert 'New Tags: new-tag1, new-tag2' in result.output
            
            # Verify API call
            expected_data = {
                'tags': ['new-tag1', 'new-tag2']
            }
            mocks['api_client'].update_asset_link.assert_called_once_with('12345678-1234-1234-1234-123456789012', expected_data)
    
    def test_update_no_fields(self, cli_runner, asset_links_command_mocks):
        """Test update command with no fields provided."""
        with asset_links_command_mocks as mocks:
            result = cli_runner.invoke(asset_links, [
                'update',
                '--asset-link-id', '12345678-1234-1234-1234-123456789012'
            ])
            
            assert result.exit_code == 2  # Click parameter error
            assert 'At least one field must be provided' in result.output


class TestAssetLinksDeleteCommand:
    """Test asset links delete command."""
    
    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(asset_links, ['delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete an asset link' in result.output
        assert '--asset-link-id' in result.output
        assert '--json-output' in result.output
    
    @patch('click.confirm')
    def test_delete_success(self, mock_confirm, cli_runner, asset_links_command_mocks):
        """Test successful asset link deletion."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].delete_asset_link.return_value = {
                'message': 'Asset link deleted successfully'
            }
            
            mock_confirm.return_value = True  # User confirms deletion
            
            result = cli_runner.invoke(asset_links, [
                'delete',
                '--asset-link-id', '12345678-1234-1234-1234-123456789012'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset link deleted successfully!' in result.output
            assert '12345678-1234-1234-1234-123456789012' in result.output
            
            # Verify API call
            mocks['api_client'].delete_asset_link.assert_called_once_with('12345678-1234-1234-1234-123456789012')
    
    @patch('click.confirm')
    def test_delete_user_cancels(self, mock_confirm, cli_runner, asset_links_command_mocks):
        """Test delete command when user cancels confirmation."""
        with asset_links_command_mocks as mocks:
            mock_confirm.return_value = False  # User cancels deletion
            
            result = cli_runner.invoke(asset_links, [
                'delete',
                '--asset-link-id', '12345678-1234-1234-1234-123456789012'
            ])
            
            assert result.exit_code == 0
            assert 'Deletion cancelled' in result.output
            
            # Verify API was not called
            mocks['api_client'].delete_asset_link.assert_not_called()


class TestAssetLinksListCommand:
    """Test asset links list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(asset_links, ['list', '--help'])
        assert result.exit_code == 0
        assert 'List all asset links for a specific asset' in result.output
        assert '--database-id' in result.output
        assert '--asset-id' in result.output
        assert '--tree-view' in result.output
        assert '--json-output' in result.output
    
    def test_list_success(self, cli_runner, asset_links_command_mocks):
        """Test successful asset links listing."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].get_asset_links_for_asset.return_value = {
                'related': [
                    {
                        'assetId': 'related-asset',
                        'assetName': 'Related Asset',
                        'databaseId': 'db1',
                        'assetLinkId': '11111111-1111-1111-1111-111111111111'
                    }
                ],
                'parents': [
                    {
                        'assetId': 'parent-asset',
                        'assetName': 'Parent Asset',
                        'databaseId': 'db1',
                        'assetLinkId': '22222222-2222-2222-2222-222222222222'
                    }
                ],
                'children': [
                    {
                        'assetId': 'child-asset',
                        'assetName': 'Child Asset',
                        'databaseId': 'db1',
                        'assetLinkId': '33333333-3333-3333-3333-333333333333'
                    }
                ],
                'unauthorizedCounts': {
                    'related': 1,
                    'parents': 0,
                    'children': 2
                },
                'message': 'Success'
            }
            
            result = cli_runner.invoke(asset_links, [
                'list',
                '-d', 'test-database',
                '--asset-id', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert 'Asset Links for test-asset in database test-database:' in result.output
            assert 'Related Assets (1):' in result.output
            assert 'Related Asset (db1)' in result.output
            assert 'Parent Assets (1):' in result.output
            assert 'Parent Asset (db1)' in result.output
            assert 'Child Assets (1):' in result.output
            assert 'Child Asset (db1)' in result.output
            assert 'Unauthorized Assets:' in result.output
            assert 'Related: 1' in result.output
            assert 'Children: 2' in result.output
            
            # Verify API call
            mocks['api_client'].get_asset_links_for_asset.assert_called_once_with('test-database', 'test-asset', False)
    
    def test_list_tree_view(self, cli_runner, asset_links_command_mocks):
        """Test list command with tree view."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].get_asset_links_for_asset.return_value = {
                'related': [],
                'parents': [],
                'children': [
                    {
                        'assetId': 'child1',
                        'assetName': 'Child 1',
                        'databaseId': 'db1',
                        'assetLinkId': '11111111-1111-1111-1111-111111111111',
                        'children': [
                            {
                                'assetId': 'grandchild1',
                                'assetName': 'Grandchild 1',
                                'databaseId': 'db1',
                                'assetLinkId': '22222222-2222-2222-2222-222222222222',
                                'children': []
                            }
                        ]
                    }
                ],
                'unauthorizedCounts': {
                    'related': 0,
                    'parents': 0,
                    'children': 0
                },
                'message': 'Success'
            }
            
            result = cli_runner.invoke(asset_links, [
                'list',
                '-d', 'test-database',
                '--asset-id', 'test-asset',
                '--tree-view'
            ])
            
            assert result.exit_code == 0
            assert 'Asset Links for test-asset in database test-database:' in result.output
            assert 'Child Assets (1):' in result.output
            assert 'Child 1 (db1)' in result.output
            assert 'Grandchild 1 (db1)' in result.output
            
            # Verify API call with tree view flag
            mocks['api_client'].get_asset_links_for_asset.assert_called_once_with('test-database', 'test-asset', True)
    
    def test_list_asset_not_found(self, cli_runner, asset_links_command_mocks):
        """Test list command with asset not found."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].get_asset_links_for_asset.side_effect = AssetNotFoundError("Asset not found")
            
            result = cli_runner.invoke(asset_links, [
                'list',
                '-d', 'test-database',
                '--asset-id', 'nonexistent-asset'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Not Found' in result.output
            assert 'vamscli assets get' in result.output


class TestAssetLinksCommandsIntegration:
    """Test integration scenarios for asset links commands."""
    
    @patch('vamscli.commands.asset_links.get_profile_manager_from_context')
    def test_commands_require_parameters(self, mock_get_profile_manager):
        """Test that asset links commands require necessary parameters."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_get_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test create without required parameters
        result = runner.invoke(asset_links, ['create'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test get without asset link ID
        result = runner.invoke(asset_links, ['get'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test update without asset link ID
        result = runner.invoke(asset_links, ['update'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test delete without asset link ID
        result = runner.invoke(asset_links, ['delete'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test list without required parameters
        result = runner.invoke(asset_links, ['list'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_authentication_error_handling(self, cli_runner, asset_links_command_mocks):
        """Test authentication error handling."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].create_asset_link.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, [
                'asset-links', 'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, AuthenticationError)
    
    def test_permission_error_handling(self, cli_runner, asset_links_command_mocks):
        """Test permission error handling."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].create_asset_link.side_effect = AssetLinkPermissionError("Not authorized to create asset link")
            
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related'
            ])
            
            assert result.exit_code == 1
            assert '✗ Permission Error' in result.output
            assert 'permissions on both assets' in result.output


class TestAssetLinksCommandsJSONHandling:
    """Test JSON input/output handling for asset links commands."""
    
    def test_invalid_json_input_string(self, cli_runner, asset_links_command_mocks):
        """Test handling of invalid JSON input string."""
        with asset_links_command_mocks as mocks:
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related',
                '--json-input', 'invalid json'
            ])
            
            assert result.exit_code == 2  # Click parameter error
            assert 'Invalid JSON input' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, asset_links_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with asset_links_command_mocks as mocks:
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related',
                '--json-input', 'nonexistent.json'
            ])
            
            assert result.exit_code == 2  # Click parameter error
            assert 'Invalid JSON input' in result.output


class TestAssetLinksCommandsValidation:
    """Test validation for asset links commands."""
    
    def test_invalid_relationship_type(self, cli_runner):
        """Test create command with invalid relationship type."""
        result = cli_runner.invoke(asset_links, [
            'create',
            '--from-asset-id', 'asset1',
            '--from-database-id', 'db1',
            '--to-asset-id', 'asset2',
            '--to-database-id', 'db2',
            '--relationship-type', 'invalid'
        ])
        
        assert result.exit_code == 2  # Click parameter error
        assert 'Invalid value for' in result.output or 'invalid choice' in result.output.lower()
    
    def test_tags_parsing(self, cli_runner, asset_links_command_mocks):
        """Test tags parsing with various input formats."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].create_asset_link.return_value = {
                'assetLinkId': '12345678-1234-1234-1234-123456789012',
                'message': 'Asset link created successfully'
            }
            
            result = cli_runner.invoke(asset_links, [
                'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related',
                '--tags', 'tag1,tag2',
                '--tags', 'tag3',
                '--tags', 'tag1'  # Duplicate should be removed
            ])
            
            assert result.exit_code == 0
            
            # Verify API call with properly parsed and deduplicated tags
            expected_data = {
                'fromAssetId': 'asset1',
                'fromAssetDatabaseId': 'db1',
                'toAssetId': 'asset2',
                'toAssetDatabaseId': 'db2',
                'relationshipType': 'related',
                'tags': ['tag1', 'tag2', 'tag3']  # Deduplicated and ordered
            }
            mocks['api_client'].create_asset_link.assert_called_once_with(expected_data)


class TestAssetLinksCommandsEdgeCases:
    """Test edge cases for asset links commands."""
    
    def test_create_api_error(self, cli_runner, asset_links_command_mocks):
        """Test create command with general API error."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].create_asset_link.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'asset-links', 'create',
                '--from-asset-id', 'asset1',
                '--from-database-id', 'db1',
                '--to-asset-id', 'asset2',
                '--to-database-id', 'db2',
                '--relationship-type', 'related'
            ])
            
            # Global exception handling - no output, just exception propagation
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, APIError)
    
    def test_list_empty_results(self, cli_runner, asset_links_command_mocks):
        """Test list command with no asset links."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].get_asset_links_for_asset.return_value = {
                'related': [],
                'parents': [],
                'children': [],
                'unauthorizedCounts': {
                    'related': 0,
                    'parents': 0,
                    'children': 0
                },
                'message': 'Success'
            }
            
            result = cli_runner.invoke(asset_links, [
                'list',
                '-d', 'test-database',
                '--asset-id', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert 'Related Assets (0):' in result.output
            assert 'Parent Assets (0):' in result.output
            assert 'Child Assets (0):' in result.output
            assert 'None' in result.output
    
    def test_database_not_found_error(self, cli_runner, asset_links_command_mocks):
        """Test database not found error handling."""
        with asset_links_command_mocks as mocks:
            mocks['api_client'].get_asset_links_for_asset.side_effect = DatabaseNotFoundError("Database not found")
            
            result = cli_runner.invoke(asset_links, [
                'list',
                '-d', 'nonexistent-database',
                '--asset-id', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert 'vamscli database list' in result.output


class TestAssetLinksUtilityFunctions:
    """Test utility functions for asset links commands."""
    
    def test_parse_tags_input_empty(self):
        """Test parse_tags_input with empty input."""
        from vamscli.commands.asset_links import parse_tags_input
        
        result = parse_tags_input([])
        assert result == []
        
        result = parse_tags_input([''])
        assert result == []
    
    def test_parse_tags_input_comma_separated(self):
        """Test parse_tags_input with comma-separated tags."""
        from vamscli.commands.asset_links import parse_tags_input
        
        result = parse_tags_input(['tag1,tag2,tag3'])
        assert result == ['tag1', 'tag2', 'tag3']
        
        result = parse_tags_input(['tag1, tag2 , tag3'])
        assert result == ['tag1', 'tag2', 'tag3']
    
    def test_parse_tags_input_mixed(self):
        """Test parse_tags_input with mixed individual and comma-separated tags."""
        from vamscli.commands.asset_links import parse_tags_input
        
        result = parse_tags_input(['tag1', 'tag2,tag3', 'tag4'])
        assert result == ['tag1', 'tag2', 'tag3', 'tag4']
    
    def test_parse_tags_input_duplicates(self):
        """Test parse_tags_input removes duplicates while preserving order."""
        from vamscli.commands.asset_links import parse_tags_input
        
        result = parse_tags_input(['tag1', 'tag2', 'tag1', 'tag3', 'tag2'])
        assert result == ['tag1', 'tag2', 'tag3']
    
    def test_validate_relationship_type_valid(self):
        """Test validate_relationship_type with valid types."""
        from vamscli.commands.asset_links import validate_relationship_type
        
        assert validate_relationship_type('related') == 'related'
        assert validate_relationship_type('parentChild') == 'parentChild'
    
    def test_validate_relationship_type_invalid(self):
        """Test validate_relationship_type with invalid type."""
        from vamscli.commands.asset_links import validate_relationship_type
        
        with pytest.raises(InvalidRelationshipTypeError) as exc_info:
            validate_relationship_type('invalid')
        
        assert 'Invalid relationship type' in str(exc_info.value)
        assert 'related' in str(exc_info.value)
        assert 'parentChild' in str(exc_info.value)
    
    def test_parse_json_input_string(self):
        """Test parse_json_input with JSON string."""
        from vamscli.commands.asset_links import parse_json_input
        
        json_string = '{"key": "value", "number": 123}'
        result = parse_json_input(json_string)
        assert result == {"key": "value", "number": 123}
    
    def test_parse_json_input_invalid_string(self):
        """Test parse_json_input with invalid JSON string and no file."""
        from vamscli.commands.asset_links import parse_json_input
        
        with patch('builtins.open', side_effect=FileNotFoundError()):
            with pytest.raises(click.BadParameter) as exc_info:
                parse_json_input('invalid json')
            
            assert 'Invalid JSON input' in str(exc_info.value)
    
    def test_format_asset_link_output_cli(self):
        """Test format_asset_link_output with CLI formatting."""
        from vamscli.commands.asset_links import format_asset_link_output
        
        link_data = {
            'assetLink': {
                'assetLinkId': '12345678-1234-1234-1234-123456789012',
                'fromAssetId': 'asset1',
                'fromAssetDatabaseId': 'db1',
                'toAssetId': 'asset2',
                'toAssetDatabaseId': 'db2',
                'relationshipType': 'related',
                'tags': ['tag1', 'tag2']
            }
        }
        
        result = format_asset_link_output(link_data, json_output=False)
        
        assert 'Asset Link Details:' in result
        assert '12345678-1234-1234-1234-123456789012' in result
        assert 'related' in result
        assert 'asset1' in result
        assert 'asset2' in result
        assert 'tag1, tag2' in result
    
    def test_format_asset_link_output_json(self):
        """Test format_asset_link_output with JSON formatting."""
        from vamscli.commands.asset_links import format_asset_link_output
        
        link_data = {
            'assetLink': {
                'assetLinkId': '12345678-1234-1234-1234-123456789012',
                'relationshipType': 'related'
            }
        }
        
        result = format_asset_link_output(link_data, json_output=True)
        
        # Should be valid JSON
        parsed = json.loads(result)
        assert parsed == link_data


if __name__ == '__main__':
    pytest.main([__file__])
