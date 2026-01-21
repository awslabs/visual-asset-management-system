"""Test asset export commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError, APIError
)


# File-level fixtures for assets-export-specific testing patterns
@pytest.fixture
def assets_export_command_mocks(generic_command_mocks):
    """Provide assets-export-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for assets export command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('assetsExport')


@pytest.fixture
def assets_export_no_setup_mocks(no_setup_command_mocks):
    """Provide assets export command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('assetsExport')


class TestAssetExportCommand:
    """Test asset export command."""
    
    def test_export_help(self, cli_runner):
        """Test export command help."""
        result = cli_runner.invoke(cli, ['assets', 'export', '--help'])
        assert result.exit_code == 0
        assert 'Export comprehensive asset data' in result.output
        assert '--database-id' in result.output
        assert '--asset-id' in result.output
        assert '--auto-paginate' in result.output
        assert '--max-assets' in result.output
        assert '--starting-token' in result.output
        assert '--generate-presigned-urls' in result.output
        assert '--no-fetch-relationships' in result.output
        assert '--fetch-entire-subtrees' in result.output
        assert '--include-archived-files' in result.output
        assert '--file-extensions' in result.output
        assert '--json-output' in result.output
    
    def test_export_success_single_page(self, cli_runner, assets_export_command_mocks):
        """Test successful export (single page with auto-pagination disabled)."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {
                        'assetid': 'asset-1',
                        'databaseid': 'test-db',
                        'assetname': 'Test Asset',
                        'files': [
                            {'fileName': 'model.gltf', 'size': 1024}
                        ],
                        'metadata': {'key1': {'valueType': 'string', 'value': 'value1'}}
                    }
                ],
                'relationships': [
                    {
                        'parentAssetId': 'asset-1',
                        'childAssetId': 'asset-2',
                        'assetLinkType': 'parentChild'
                    }
                ],
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-database',
                '-a', 'test-asset',
                '--no-auto-paginate'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            assert 'Assets in this page: 1' in result.output
            assert 'Total assets in tree: 1' in result.output
            
            # Verify API call
            mocks['api_client'].export_asset.assert_called_once()
            call_args = mocks['api_client'].export_asset.call_args[0]
            assert call_args[0] == 'test-database'
            assert call_args[1] == 'test-asset'
    
    def test_export_auto_paginate_multiple_pages(self, cli_runner, assets_export_command_mocks):
        """Test export with auto-pagination (multiple pages)."""
        with assets_export_command_mocks as mocks:
            # Mock multiple pages
            mocks['api_client'].export_asset.side_effect = [
                {
                    'assets': [{'assetid': f'asset-{i}', 'databaseid': 'test-db', 'assetname': f'Asset {i}', 'files': []} for i in range(500)],
                    'relationships': [{'parentAssetId': 'root', 'childAssetId': 'asset-1'}],
                    'NextToken': 'token-page-2',
                    'totalAssetsInTree': 1234,
                    'assetsInThisPage': 500
                },
                {
                    'assets': [{'assetid': f'asset-{i}', 'databaseid': 'test-db', 'assetname': f'Asset {i}', 'files': []} for i in range(500, 1000)],
                    'NextToken': 'token-page-3',
                    'totalAssetsInTree': 1234,
                    'assetsInThisPage': 500
                },
                {
                    'assets': [{'assetid': f'asset-{i}', 'databaseid': 'test-db', 'assetname': f'Asset {i}', 'files': []} for i in range(1000, 1234)],
                    'NextToken': None,
                    'totalAssetsInTree': 1234,
                    'assetsInThisPage': 234
                }
            ]
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--auto-paginate'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            assert 'Pages retrieved: 3' in result.output
            assert 'Assets retrieved: 1,234' in result.output
            assert 'All data has been retrieved and combined' in result.output
            
            # Verify 3 API calls were made
            assert mocks['api_client'].export_asset.call_count == 3
    
    def test_export_auto_paginate_json_output(self, cli_runner, assets_export_command_mocks):
        """Test auto-pagination with pure JSON output."""
        with assets_export_command_mocks as mocks:
            # Mock 2 pages
            mocks['api_client'].export_asset.side_effect = [
                {
                    'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                    'relationships': [{'parentAssetId': 'root', 'childAssetId': 'asset-1'}],
                    'NextToken': 'token-2',
                    'totalAssetsInTree': 2,
                    'assetsInThisPage': 1
                },
                {
                    'assets': [{'assetid': 'asset-2', 'databaseid': 'test-db', 'files': []}],
                    'NextToken': None,
                    'totalAssetsInTree': 2,
                    'assetsInThisPage': 1
                }
            ]
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--auto-paginate',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Verify pure JSON output (no CLI messages)
            output_json = json.loads(result.output.strip())
            assert len(output_json['assets']) == 2
            assert output_json['assetsRetrieved'] == 2
            assert output_json['pagesRetrieved'] == 2
            assert output_json['autoPaginated'] == True
            assert len(output_json['relationships']) == 1
    
    def test_export_manual_pagination_first_page(self, cli_runner, assets_export_command_mocks):
        """Test manual pagination (first page)."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': [{'parentAssetId': 'root', 'childAssetId': 'asset-1'}],
                'NextToken': 'token-page-2',
                'totalAssetsInTree': 100,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--no-auto-paginate',
                '--max-assets', '1'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            assert 'Assets in this page: 1' in result.output
            assert 'Total assets in tree: 100' in result.output
            assert 'More data available' in result.output
            assert 'token-page-2' in result.output
            assert '--auto-paginate' in result.output
    
    def test_export_manual_pagination_with_token(self, cli_runner, assets_export_command_mocks):
        """Test manual pagination with next token."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-2', 'databaseid': 'test-db', 'files': []}],
                'NextToken': 'token-page-3',
                'totalAssetsInTree': 100,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--no-auto-paginate',
                '--starting-token', 'token-page-2'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            assert 'Assets in this page: 1' in result.output
            assert 'token-page-3' in result.output
            
            # Verify API call includes token
            # Note: API response returns 'NextToken', but API request expects 'startingToken'
            call_args = mocks['api_client'].export_asset.call_args[0]
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs.get('startingToken') == 'token-page-2'
    
    def test_export_with_filters(self, cli_runner, assets_export_command_mocks):
        """Test export with various filters."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--generate-presigned-urls',
                '--include-folder-files',
                '--include-only-primary-type-files',
                '--file-extensions', '.gltf',
                '--file-extensions', '.bin'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            
            # Verify API call includes filters
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['generatePresignedUrls'] == True
            assert call_kwargs['includeFolderFiles'] == True
            assert call_kwargs['includeOnlyPrimaryTypeFiles'] == True
            assert '.gltf' in call_kwargs['fileExtensions']
            assert '.bin' in call_kwargs['fileExtensions']
    
    def test_export_with_metadata_exclusions(self, cli_runner, assets_export_command_mocks):
        """Test export with metadata exclusions."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--no-file-metadata',
                '--no-asset-link-metadata',
                '--no-asset-metadata'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call excludes metadata
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['includeFileMetadata'] == False
            assert call_kwargs['includeAssetLinkMetadata'] == False
            assert call_kwargs['includeAssetMetadata'] == False
    
    def test_export_no_fetch_relationships(self, cli_runner, assets_export_command_mocks):
        """Test export with relationship fetching disabled (single asset mode)."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': None,
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--no-fetch-relationships'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call disables relationship fetching
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['fetchAssetRelationships'] == False
    
    def test_export_fetch_entire_subtrees(self, cli_runner, assets_export_command_mocks):
        """Test export with full tree fetching enabled."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []},
                    {'assetid': 'asset-2', 'databaseid': 'test-db', 'files': []},
                    {'assetid': 'asset-3', 'databaseid': 'test-db', 'files': []}
                ],
                'relationships': [
                    {'parentAssetId': 'asset-1', 'childAssetId': 'asset-2'},
                    {'parentAssetId': 'asset-2', 'childAssetId': 'asset-3'}
                ],
                'NextToken': None,
                'totalAssetsInTree': 3,
                'assetsInThisPage': 3
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--fetch-entire-subtrees'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call enables full tree fetching
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['fetchEntireChildrenSubtrees'] == True
    
    def test_export_include_archived_files(self, cli_runner, assets_export_command_mocks):
        """Test export with archived files included."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {
                        'assetid': 'asset-1',
                        'databaseid': 'test-db',
                        'files': [
                            {'fileName': 'model.gltf', 'isArchived': False},
                            {'fileName': 'old-model.gltf', 'isArchived': True}
                        ]
                    }
                ],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--include-archived-files'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call includes archived files
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['includeArchivedFiles'] == True
    
    def test_export_include_parent_relationships(self, cli_runner, assets_export_command_mocks):
        """Test export with parent relationships included."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []},
                    {'assetid': 'asset-2', 'databaseid': 'test-db', 'files': []}
                ],
                'relationships': [
                    {
                        'parentAssetId': 'parent-asset',
                        'childAssetId': 'asset-1',
                        'assetLinkType': 'parentChild'
                    },
                    {
                        'parentAssetId': 'asset-1',
                        'childAssetId': 'asset-2',
                        'assetLinkType': 'parentChild'
                    }
                ],
                'NextToken': None,
                'totalAssetsInTree': 2,
                'assetsInThisPage': 2
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'asset-1',
                '--include-parent-relationships'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            
            # Verify API call includes parent relationships
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['includeParentRelationships'] == True
    
    def test_export_exclude_parent_relationships_default(self, cli_runner, assets_export_command_mocks):
        """Test that parent relationships are excluded by default."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': [
                    {
                        'parentAssetId': 'asset-1',
                        'childAssetId': 'child-asset',
                        'assetLinkType': 'parentChild'
                    }
                ],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'asset-1'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call excludes parent relationships by default
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['includeParentRelationships'] == False
    
    def test_export_include_parent_relationships_json_output(self, cli_runner, assets_export_command_mocks):
        """Test parent relationships parameter with JSON output."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': [
                    {
                        'parentAssetId': 'parent-asset',
                        'childAssetId': 'asset-1',
                        'assetLinkType': 'parentChild'
                    }
                ],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'asset-1',
                '--include-parent-relationships',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Verify pure JSON output includes parent relationships
            output_json = json.loads(result.output.strip())
            assert len(output_json['relationships']) == 1
            assert output_json['relationships'][0]['parentAssetId'] == 'parent-asset'
            assert output_json['relationships'][0]['childAssetId'] == 'asset-1'
    
    def test_export_relationship_options_combined(self, cli_runner, assets_export_command_mocks):
        """Test export with combined relationship options."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--fetch-entire-subtrees',
                '--include-parent-relationships',
                '--include-archived-files',
                '--auto-paginate'
            ])
            
            assert result.exit_code == 0
            
            # Verify API call includes all options
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['fetchEntireChildrenSubtrees'] == True
            assert call_kwargs['includeParentRelationships'] == True
            assert call_kwargs['includeArchivedFiles'] == True
            assert call_kwargs['fetchAssetRelationships'] == True  # Default
    
    def test_export_json_input(self, cli_runner, assets_export_command_mocks):
        """Test export with JSON input."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'json-asset', 'databaseid': 'json-db', 'files': []}],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            json_data = {
                'databaseId': 'json-database',
                'assetId': 'json-asset',
                'generatePresignedUrls': True,
                'fileExtensions': ['.gltf', '.bin'],
                'maxAssets': 100,
                'noFetchRelationships': True,
                'fetchEntireSubtrees': True,
                'includeParentRelationships': True,
                'includeArchivedFiles': True
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'placeholder-db',
                '-a', 'placeholder-asset',
                '--json-input', json.dumps(json_data)
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            
            # Verify API call uses JSON data including new parameters
            call_args = mocks['api_client'].export_asset.call_args[0]
            assert call_args[0] == 'json-database'
            assert call_args[1] == 'json-asset'
            call_kwargs = call_args[2]
            assert call_kwargs['generatePresignedUrls'] == True
            assert call_kwargs['maxAssets'] == 100
            assert call_kwargs['fetchAssetRelationships'] == False
            assert call_kwargs['fetchEntireChildrenSubtrees'] == True
            assert call_kwargs['includeArchivedFiles'] == True
    
    def test_export_json_output_pure(self, cli_runner, assets_export_command_mocks):
        """Test that JSON output contains ONLY valid JSON."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Verify output is pure JSON (no extra text)
            try:
                parsed = json.loads(result.output.strip())
                assert isinstance(parsed, dict)
                assert 'assets' in parsed
                assert len(parsed['assets']) == 1
                assert parsed['assets'][0]['assetid'] == 'asset-1'
            except json.JSONDecodeError:
                pytest.fail(f"Output is not valid JSON: {result.output}")
    
    def test_export_auto_paginate_and_next_token_mutually_exclusive(self, cli_runner, assets_export_command_mocks):
        """Test that auto-paginate and next-token cannot be used together."""
        with assets_export_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset',
                '--starting-token', 'some-token'
            ])
            
            assert result.exit_code == 1
            assert 'cannot be used together' in result.output
    
    def test_export_default_auto_paginate(self, cli_runner, assets_export_command_mocks):
        """Test that auto-pagination is enabled by default."""
        with assets_export_command_mocks as mocks:
            # Mock single page that completes immediately
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            # Should show auto-pagination indicators
            assert 'Pages retrieved: 1' in result.output
            assert 'Assets retrieved: 1' in result.output
    
    def test_export_max_assets_validation(self, cli_runner, assets_export_command_mocks):
        """Test max-assets parameter validation."""
        with assets_export_command_mocks as mocks:
            # Test value too low
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--no-auto-paginate',
                '--max-assets', '0'
            ])
            
            assert result.exit_code == 2
            assert 'must be between 1 and 1000' in result.output
            
            # Test value too high
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--no-auto-paginate',
                '--max-assets', '1001'
            ])
            
            assert result.exit_code == 2
            assert 'must be between 1 and 1000' in result.output
    
    def test_export_max_assets_default_value(self, cli_runner, assets_export_command_mocks):
        """Test that max-assets defaults to 100 (matching backend)."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 0,
                'assetsInThisPage': 0
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            
            # Verify default maxAssets is 100
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['maxAssets'] == 100
    
    def test_export_file_extensions_normalization(self, cli_runner, assets_export_command_mocks):
        """Test file extensions are normalized correctly."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 0,
                'assetsInThisPage': 0
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--file-extensions', 'gltf',  # Without dot
                '--file-extensions', '.bin'   # With dot
            ])
            
            assert result.exit_code == 0
            
            # Verify extensions are normalized with dots
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert '.gltf' in call_kwargs['fileExtensions']
            assert '.bin' in call_kwargs['fileExtensions']
    
    def test_export_asset_not_found(self, cli_runner, assets_export_command_mocks):
        """Test export with asset not found."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.side_effect = AssetNotFoundError("Asset 'test-asset' not found")
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Not Found' in result.output
            assert 'vamscli assets get' in result.output
    
    def test_export_database_not_found(self, cli_runner, assets_export_command_mocks):
        """Test export with database not found."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.side_effect = DatabaseNotFoundError("Database 'test-db' not found")
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert 'vamscli database list' in result.output
    
    def test_export_invalid_parameters(self, cli_runner, assets_export_command_mocks):
        """Test export with invalid parameters."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.side_effect = InvalidAssetDataError("Invalid export parameters")
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert '✗ Invalid Export Parameters' in result.output
    
    def test_export_no_setup(self, cli_runner, assets_export_no_setup_mocks):
        """Test export without setup."""
        with assets_export_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            # SetupRequiredError is raised before command execution
            assert result.exception is not None
            assert 'Setup required' in str(result.exception) or result.exit_code == 1
    
    def test_export_missing_required_args(self, cli_runner):
        """Test export with missing required arguments."""
        # Test missing database ID
        result = cli_runner.invoke(cli, [
            'assets', 'export',
            '-a', 'test-asset'
        ])
        assert result.exit_code == 2
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test missing asset ID
        result = cli_runner.invoke(cli, [
            'assets', 'export',
            '-d', 'test-db'
        ])
        assert result.exit_code == 2
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_export_with_presigned_urls(self, cli_runner, assets_export_command_mocks):
        """Test export with presigned URLs generation."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {
                        'assetid': 'asset-1',
                        'databaseid': 'test-db',
                        'files': [
                            {
                                'fileName': 'model.gltf',
                                'presignedFileDownloadUrl': 'https://s3.amazonaws.com/bucket/file?signature=...',
                                'presignedFileDownloadExpiresIn': 86400
                            }
                        ]
                    }
                ],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--generate-presigned-urls',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Verify presigned URLs in output
            output_json = json.loads(result.output.strip())
            assert output_json['assets'][0]['files'][0]['presignedFileDownloadUrl'] is not None
            assert output_json['assets'][0]['files'][0]['presignedFileDownloadExpiresIn'] == 86400


class TestAssetExportUtilityFunctions:
    """Test asset export utility functions."""
    
    def test_normalize_file_extensions_with_dot(self):
        """Test normalizing extensions that already have dots."""
        from vamscli.commands.assetsExport import normalize_file_extensions
        
        extensions = ['.gltf', '.bin', '.jpg']
        result = normalize_file_extensions(extensions)
        assert result == ['.gltf', '.bin', '.jpg']
    
    def test_normalize_file_extensions_without_dot(self):
        """Test normalizing extensions without dots."""
        from vamscli.commands.assetsExport import normalize_file_extensions
        
        extensions = ['gltf', 'bin', 'jpg']
        result = normalize_file_extensions(extensions)
        assert result == ['.gltf', '.bin', '.jpg']
    
    def test_normalize_file_extensions_mixed(self):
        """Test normalizing mixed extensions."""
        from vamscli.commands.assetsExport import normalize_file_extensions
        
        extensions = ['.gltf', 'bin', '.JPG', 'PNG']
        result = normalize_file_extensions(extensions)
        assert result == ['.gltf', '.bin', '.jpg', '.png']
    
    def test_parse_json_input_valid_json(self):
        """Test parsing valid JSON string."""
        from vamscli.commands.assetsExport import parse_json_input
        
        json_str = '{"databaseId": "test", "assetId": "asset"}'
        result = parse_json_input(json_str)
        assert result == {"databaseId": "test", "assetId": "asset"}
    
    def test_parse_json_input_invalid_json(self):
        """Test parsing invalid JSON string."""
        from vamscli.commands.assetsExport import parse_json_input
        
        with pytest.raises(click.BadParameter):
            parse_json_input('invalid json string')
    
    def test_format_export_result_cli_auto_paginated(self):
        """Test CLI formatting for auto-paginated results."""
        from vamscli.commands.assetsExport import format_export_result_cli
        
        data = {
            'assets': [
                {'assetid': 'asset-1', 'assetname': 'Asset 1', 'databaseid': 'db-1', 'files': []},
                {'assetid': 'asset-2', 'assetname': 'Asset 2', 'databaseid': 'db-1', 'files': []}
            ],
            'relationships': [{'parentAssetId': 'asset-1', 'childAssetId': 'asset-2'}],
            'totalAssetsInTree': 2,
            'assetsRetrieved': 2,
            'pagesRetrieved': 1,
            'autoPaginated': True
        }
        
        result = format_export_result_cli(data)
        assert 'Total assets in tree: 2' in result
        assert 'Assets retrieved: 2' in result
        assert 'Pages retrieved: 1' in result
        assert 'Relationships: 1' in result
        assert 'All data has been retrieved and combined' in result
    
    def test_format_export_result_cli_single_page(self):
        """Test CLI formatting for single page results."""
        from vamscli.commands.assetsExport import format_export_result_cli
        
        data = {
            'assets': [{'assetid': 'asset-1', 'assetname': 'Asset 1', 'databaseid': 'db-1', 'files': []}],
            'relationships': [],
            'NextToken': 'token-123',
            'totalAssetsInTree': 100,
            'assetsInThisPage': 1
        }
        
        result = format_export_result_cli(data)
        assert 'Assets in this page: 1' in result
        assert 'Total assets in tree: 100' in result
        assert 'More data available' in result
        assert 'token-123' in result
        assert '--auto-paginate' in result


class TestAssetExportEdgeCases:
    """Test edge cases for asset export command."""
    
    def test_export_empty_result(self, cli_runner, assets_export_command_mocks):
        """Test export with no assets in result."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 0,
                'assetsInThisPage': 0
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            assert 'Assets retrieved: 0' in result.output
    
    def test_export_with_unauthorized_assets(self, cli_runner, assets_export_command_mocks):
        """Test export with some unauthorized assets in the tree."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {
                        'assetid': 'asset-1',
                        'databaseid': 'test-db',
                        'assetname': 'Authorized Asset',
                        'files': [{'fileName': 'model.gltf', 'isFolder': False}]
                    },
                    {
                        'assetId': 'asset-2',
                        'databaseId': 'test-db',
                        'unauthorizedAsset': True
                    },
                    {
                        'assetid': 'asset-3',
                        'databaseid': 'test-db',
                        'assetname': 'Another Authorized Asset',
                        'files': []
                    }
                ],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 3,
                'assetsInThisPage': 3
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            assert 'Unauthorized assets (skipped): 1' in result.output
            assert 'Assets retrieved: 3' in result.output
    
    def test_export_all_unauthorized_assets(self, cli_runner, assets_export_command_mocks):
        """Test export where all assets are unauthorized."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {
                        'assetId': 'asset-1',
                        'databaseId': 'test-db',
                        'unauthorizedAsset': True
                    },
                    {
                        'assetId': 'asset-2',
                        'databaseId': 'test-db',
                        'unauthorizedAsset': True
                    }
                ],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 2,
                'assetsInThisPage': 2
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'root-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export completed successfully!' in result.output
            assert 'Unauthorized assets (skipped): 2' in result.output
    
    def test_export_auto_paginate_single_page(self, cli_runner, assets_export_command_mocks):
        """Test auto-pagination when only one page exists."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [{'assetid': 'asset-1', 'databaseid': 'test-db', 'files': []}],
                'relationships': [],
                'NextToken': None,  # No more pages
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--auto-paginate'
            ])
            
            assert result.exit_code == 0
            assert 'Pages retrieved: 1' in result.output
            assert 'Assets retrieved: 1' in result.output
            
            # Verify only one API call was made
            assert mocks['api_client'].export_asset.call_count == 1
    
    def test_export_api_error(self, cli_runner, assets_export_command_mocks):
        """Test export with API error."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            assert '✗ API Error' in result.output
            assert 'API request failed' in result.output
    
    def test_export_json_error_format(self, cli_runner, assets_export_command_mocks):
        """Test that errors are JSON-formatted in JSON mode."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.side_effect = AssetNotFoundError("Asset not found")
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--json-output'
            ])
            
            assert result.exit_code == 1
            
            # Verify error output contains JSON (Click may add "Error:" line after)
            # Extract just the JSON part
            output_lines = result.output.strip().split('\n')
            json_lines = []
            in_json = False
            brace_count = 0
            
            for line in output_lines:
                if '{' in line and not in_json:
                    in_json = True
                if in_json:
                    json_lines.append(line)
                    brace_count += line.count('{') - line.count('}')
                    if brace_count == 0:
                        break
            
            if json_lines:
                json_str = '\n'.join(json_lines)
                try:
                    parsed = json.loads(json_str)
                    assert 'error' in parsed
                    assert 'Asset not found' in parsed['error']
                except json.JSONDecodeError:
                    pytest.fail(f"Error output does not contain valid JSON: {result.output}")
            else:
                pytest.fail(f"No JSON found in error output: {result.output}")


class TestAssetExportWithDownload:
    """Test asset export with download functionality."""
    
    @patch('vamscli.commands.assetsExport.asyncio.run')
    def test_export_download_with_unauthorized_assets(self, mock_asyncio_run, cli_runner, assets_export_command_mocks):
        """Test export with downloads when some assets are unauthorized."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {
                        'assetid': 'asset-1',
                        'databaseid': 'test-db',
                        'assetname': 'Authorized Asset',
                        'files': [
                            {
                                'fileName': 'model.gltf',
                                'relativePath': '/model.gltf',
                                'isFolder': False,
                                'size': 1024,
                                'presignedFileDownloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf'
                            }
                        ]
                    },
                    {
                        'assetId': 'asset-2',
                        'databaseId': 'test-db',
                        'unauthorizedAsset': True
                    }
                ],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 2,
                'assetsInThisPage': 2
            }
            
            # Mock download result
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
                'skipped_unauthorized_assets': 1,
                'successful_downloads': [],
                'failed_downloads': []
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--download-files',
                '--local-path', '/tmp'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export and download completed successfully!' in result.output
            assert 'Skipped (unauthorized): 1 asset(s)' in result.output
    
    @patch('vamscli.commands.assetsExport.asyncio.run')
    def test_export_with_download_success(self, mock_asyncio_run, cli_runner, assets_export_command_mocks):
        """Test export with file downloads."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {
                        'assetid': 'asset-1',
                        'databaseid': 'test-db',
                        'assetname': 'Test Asset',
                        'files': [
                            {
                                'fileName': 'model.gltf',
                                'relativePath': '/model.gltf',
                                'isFolder': False,
                                'size': 1024,
                                'presignedFileDownloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf?sig=...'
                            }
                        ]
                    }
                ],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            # Mock download result
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
                'successful_downloads': [{'relative_key': 'asset-1/model.gltf', 'local_path': '/tmp/asset-1/model.gltf', 'size': 1024}],
                'failed_downloads': []
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--generate-presigned-urls',
                '--download-files',
                '--local-path', '/tmp'
            ])
            
            assert result.exit_code == 0
            assert '✓ Export and download completed successfully!' in result.output
            assert 'Download Summary:' in result.output
            assert 'Total files: 1' in result.output
            assert 'Successfully downloaded: 1' in result.output
    
    def test_export_download_requires_local_path(self, cli_runner, assets_export_command_mocks):
        """Test that --download-files requires --local-path."""
        with assets_export_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--download-files'
            ])
            
            assert result.exit_code == 1
            assert '--download-files requires --local-path' in result.output
    
    def test_export_download_auto_enables_presigned_urls(self, cli_runner, assets_export_command_mocks):
        """Test that --download-files auto-enables presigned URLs."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 0,
                'assetsInThisPage': 0
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--download-files',
                '--local-path', '/tmp'
            ])
            
            assert result.exit_code == 0
            # Verify generatePresignedUrls was set to True
            call_kwargs = mocks['api_client'].export_asset.call_args[0][2]
            assert call_kwargs['generatePresignedUrls'] == True
    
    def test_export_organize_by_asset_requires_download(self, cli_runner, assets_export_command_mocks):
        """Test that --organize-by-asset requires --download-files."""
        with assets_export_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--organize-by-asset'
            ])
            
            assert result.exit_code == 1
            assert 'require --download-files' in result.output
    
    def test_export_flatten_and_organize_mutually_exclusive(self, cli_runner, assets_export_command_mocks):
        """Test that --flatten-downloads and --organize-by-asset are mutually exclusive."""
        with assets_export_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--download-files',
                '--local-path', '/tmp',
                '--organize-by-asset',
                '--flatten-downloads'
            ])
            
            assert result.exit_code == 1
            assert 'cannot be used together' in result.output
    
    @patch('vamscli.commands.assetsExport.asyncio.run')
    def test_export_download_with_failures(self, mock_asyncio_run, cli_runner, assets_export_command_mocks):
        """Test export with some download failures."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {
                        'assetid': 'asset-1',
                        'databaseid': 'test-db',
                        'files': [
                            {
                                'fileName': 'model.gltf',
                                'relativePath': '/model.gltf',
                                'isFolder': False,
                                'size': 1024,
                                'presignedFileDownloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf'
                            },
                            {
                                'fileName': 'texture.jpg',
                                'relativePath': '/texture.jpg',
                                'isFolder': False,
                                'size': 2048,
                                'presignedFileDownloadUrl': 'https://s3.amazonaws.com/bucket/texture.jpg'
                            }
                        ]
                    }
                ],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
            # Mock download result with one failure
            mock_asyncio_run.return_value = {
                'overall_success': False,
                'total_files': 2,
                'successful_files': 1,
                'failed_files': 1,
                'total_size': 3072,
                'total_size_formatted': '3.0 KB',
                'download_duration': 1.0,
                'average_speed': 3072,
                'average_speed_formatted': '3.0 KB/s',
                'successful_downloads': [{'relative_key': 'asset-1/model.gltf', 'local_path': '/tmp/model.gltf', 'size': 1024}],
                'failed_downloads': [{'relative_key': 'asset-1/texture.jpg', 'local_path': '/tmp/texture.jpg', 'error': 'Connection timeout'}]
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--generate-presigned-urls',
                '--download-files',
                '--local-path', '/tmp'
            ])
            
            assert result.exit_code == 0
            assert '⚠ Export completed with download errors' in result.output
            assert 'Failed downloads (1):' in result.output
            assert 'Connection timeout' in result.output
    
    @patch('vamscli.commands.assetsExport.asyncio.run')
    def test_export_download_json_output(self, mock_asyncio_run, cli_runner, assets_export_command_mocks):
        """Test export with downloads in JSON output mode."""
        with assets_export_command_mocks as mocks:
            mocks['api_client'].export_asset.return_value = {
                'assets': [
                    {
                        'assetid': 'asset-1',
                        'databaseid': 'test-db',
                        'files': [
                            {
                                'fileName': 'model.gltf',
                                'relativePath': '/model.gltf',
                                'isFolder': False,
                                'size': 1024,
                                'presignedFileDownloadUrl': 'https://s3.amazonaws.com/bucket/model.gltf'
                            }
                        ]
                    }
                ],
                'relationships': [],
                'NextToken': None,
                'totalAssetsInTree': 1,
                'assetsInThisPage': 1
            }
            
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
                'successful_downloads': [],
                'failed_downloads': []
            }
            
            result = cli_runner.invoke(cli, [
                'assets', 'export',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--download-files',
                '--local-path', '/tmp',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            
            # Verify JSON output includes download results
            output_json = json.loads(result.output.strip())
            assert 'downloadResults' in output_json
            assert output_json['downloadResults']['total_files'] == 1
            assert output_json['downloadResults']['successful_files'] == 1


if __name__ == '__main__':
    pytest.main([__file__])
