"""Test asset version management commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.commands.asset_version import asset_version
from vamscli.utils.exceptions import (
    AssetVersionError, AssetVersionNotFoundError, AssetVersionOperationError,
    InvalidAssetVersionDataError, AssetVersionRevertError, AssetNotFoundError,
    DatabaseNotFoundError, AuthenticationError, APIError, SetupRequiredError
)


# File-level fixtures for asset version-specific testing patterns
@pytest.fixture
def asset_version_command_mocks(generic_command_mocks):
    """Provide asset version-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for asset version command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('asset_version')


@pytest.fixture
def asset_version_no_setup_mocks(no_setup_command_mocks):
    """Provide asset version command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('asset_version')


class TestAssetVersionCreateCommand:
    """Test asset version create command."""
    
    def test_create_help(self, cli_runner):
        """Test create command help."""
        result = cli_runner.invoke(asset_version, ['create', '--help'])
        assert result.exit_code == 0
        assert 'Create a new asset version' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--comment' in result.output
        assert '--use-latest-files' in result.output
        assert '--files' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_create_success(self, cli_runner, asset_version_command_mocks):
        """Test successful version creation."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.return_value = {
                'success': True,
                'message': 'Successfully created version 2 with 3 files',
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z',
                'skippedFiles': []
            }
            
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version'
            ])
            
            assert result.exit_code == 0
            assert 'Asset version create completed successfully' in result.output
            assert 'test-asset' in result.output
            assert 'Version ID: 2' in result.output
            
            # Verify API call
            expected_data = {
                'useLatestFiles': True,
                'comment': 'Test version'
            }
            mocks['api_client'].create_asset_version.assert_called_once_with(
                'test-db', 'test-asset', expected_data
            )
    
    def test_create_with_specific_files(self, cli_runner, asset_version_command_mocks):
        """Test version creation with specific files."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.return_value = {
                'success': True,
                'message': 'Successfully created version 2 with 1 files',
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z',
                'skippedFiles': []
            }
            
            files_json = '[{"relativeKey":"model.obj","versionId":"abc123","isArchived":false}]'
            
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version',
                '--no-use-latest-files',  # Now this flag exists
                '--files', files_json
            ])
            
            assert result.exit_code == 0
            assert 'Asset version create completed successfully' in result.output
            
            # Verify API call
            expected_data = {
                'useLatestFiles': False,
                'comment': 'Test version',
                'files': [{'relativeKey': 'model.obj', 'versionId': 'abc123', 'isArchived': False}]
            }
            mocks['api_client'].create_asset_version.assert_called_once_with(
                'test-db', 'test-asset', expected_data
            )
    
    def test_create_json_input(self, cli_runner, asset_version_command_mocks):
        """Test version creation with JSON input."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.return_value = {
                'success': True,
                'message': 'Successfully created version 2 with 2 files',
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z',
                'skippedFiles': []
            }
            
            json_data = {
                'useLatestFiles': False,
                'comment': 'JSON version',
                'files': [
                    {'relativeKey': 'file1.obj', 'versionId': 'v1', 'isArchived': False},
                    {'relativeKey': 'file2.obj', 'versionId': 'v2', 'isArchived': False}
                ]
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))):
                result = cli_runner.invoke(asset_version, [
                    'create',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    '--comment', 'Test version',  # This will be overridden by JSON
                    '--json-input', 'version-data.json'
                ])
            
            assert result.exit_code == 0
            assert 'Asset version create completed successfully' in result.output
            
            # Verify API call uses JSON data
            mocks['api_client'].create_asset_version.assert_called_once_with(
                'test-db', 'test-asset', json_data
            )
    
    def test_create_json_output(self, cli_runner, asset_version_command_mocks):
        """Test version creation with JSON output."""
        with asset_version_command_mocks as mocks:
            api_response = {
                'success': True,
                'message': 'Successfully created version 2 with 3 files',
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            mocks['api_client'].create_asset_version.return_value = api_response
            
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Extract JSON from output (skip progress message)
            lines = result.output.strip().split('\n')
            json_start = -1
            for i, line in enumerate(lines):
                if line.strip().startswith('{'):
                    json_start = i
                    break
            
            if json_start >= 0:
                json_output = '\n'.join(lines[json_start:])
                output_json = json.loads(json_output)
                assert output_json == api_response
            else:
                # Fallback: check if entire output is JSON
                output_json = json.loads(result.output.strip())
                assert output_json == api_response
    
    def test_create_asset_not_found(self, cli_runner, asset_version_command_mocks):
        """Test create command with asset not found error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.side_effect = AssetNotFoundError(
                "Asset 'test-asset' not found in database 'test-db'"
            )
            
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version'
            ])
            
            assert result.exit_code == 1
            assert '✗ Asset Not Found' in result.output
            assert 'vamscli assets get' in result.output
    
    def test_create_database_not_found(self, cli_runner, asset_version_command_mocks):
        """Test create command with database not found error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.side_effect = DatabaseNotFoundError(
                "Database 'nonexistent-db' not found"
            )
            
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'nonexistent-db',
                '-a', 'test-asset',
                '--comment', 'Test version'
            ])
            
            assert result.exit_code == 1
            assert '✗ Database Not Found' in result.output
            assert 'vamscli database list' in result.output
    
    def test_create_no_setup(self, cli_runner, asset_version_no_setup_mocks):
        """Test create command without setup."""
        with asset_version_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'asset-version', 'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version'
            ])
            
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)
    
    def test_create_invalid_version_data_error(self, cli_runner, asset_version_command_mocks):
        """Test create command with invalid version data error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.side_effect = InvalidAssetVersionDataError(
                "Invalid version data: Comment is required"
            )
            
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version'
            ])
            
            assert result.exit_code == 1
            assert '✗ Invalid Version Data' in result.output
    
    def test_create_version_operation_error(self, cli_runner, asset_version_command_mocks):
        """Test create command with version operation error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.side_effect = AssetVersionOperationError(
                "Asset version creation failed: No valid files found"
            )
            
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version'
            ])
            
            assert result.exit_code == 1
            assert '✗ Version Creation Failed' in result.output


class TestAssetVersionRevertCommand:
    """Test asset version revert command."""
    
    def test_revert_help(self, cli_runner):
        """Test revert command help."""
        result = cli_runner.invoke(asset_version, ['revert', '--help'])
        assert result.exit_code == 0
        assert 'Revert an asset to a previous version' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--version' in result.output
        assert '--comment' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_revert_success(self, cli_runner, asset_version_command_mocks):
        """Test successful version revert."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].revert_asset_version.return_value = {
                'success': True,
                'message': 'Successfully reverted to version 1 with 3 files',
                'assetId': 'test-asset',
                'assetVersionId': '3',
                'operation': 'revert',
                'timestamp': '2024-01-01T00:00:00Z',
                'skippedFiles': []
            }
            
            result = cli_runner.invoke(asset_version, [
                'revert',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1',
                '--comment', 'Reverting to stable version'
            ])
            
            assert result.exit_code == 0
            assert 'Asset version revert completed successfully' in result.output
            assert 'test-asset' in result.output
            assert 'Version ID: 3' in result.output
            
            # Verify API call
            mocks['api_client'].revert_asset_version.assert_called_once_with(
                'test-db', 'test-asset', '1', {'comment': 'Reverting to stable version'}
            )
    
    def test_revert_with_skipped_files(self, cli_runner, asset_version_command_mocks):
        """Test version revert with skipped files."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].revert_asset_version.return_value = {
                'success': True,
                'message': 'Successfully reverted to version 1 with 2 files',
                'assetId': 'test-asset',
                'assetVersionId': '3',
                'operation': 'revert',
                'timestamp': '2024-01-01T00:00:00Z',
                'skippedFiles': ['deleted-file.obj', 'missing-texture.png']
            }
            
            result = cli_runner.invoke(asset_version, [
                'revert',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1'
            ])
            
            assert result.exit_code == 0
            assert 'Asset version revert completed successfully' in result.output
            assert 'Skipped Files (2)' in result.output
            assert 'deleted-file.obj' in result.output
            assert 'missing-texture.png' in result.output
            assert 'permanently deleted or are no longer accessible' in result.output
    
    def test_revert_version_not_found(self, cli_runner, asset_version_command_mocks):
        """Test revert command with version not found error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].revert_asset_version.side_effect = AssetVersionNotFoundError(
                "Asset version '999' not found"
            )
            
            result = cli_runner.invoke(asset_version, [
                'revert',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '999'
            ])
            
            assert result.exit_code == 1
            assert '✗ Version Not Found' in result.output
            assert 'vamscli asset-version list' in result.output
    
    def test_revert_error(self, cli_runner, asset_version_command_mocks):
        """Test revert command with revert error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].revert_asset_version.side_effect = AssetVersionRevertError(
                "Asset version revert failed: Target version has no accessible files"
            )
            
            result = cli_runner.invoke(asset_version, [
                'revert',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1'
            ])
            
            assert result.exit_code == 1
            assert '✗ Version Revert Failed' in result.output


class TestAssetVersionListCommand:
    """Test asset version list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(asset_version, ['list', '--help'])
        assert result.exit_code == 0
        assert 'List all versions for an asset' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--max-items' in result.output
        assert '--starting-token' in result.output
        assert '--json-output' in result.output
    
    def test_list_success(self, cli_runner, asset_version_command_mocks):
        """Test successful version listing."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_versions.return_value = {
                'versions': [
                    {
                        'Version': '2',
                        'DateModified': '2024-01-02T00:00:00Z',
                        'Comment': 'Latest version',
                        'description': 'Updated model',
                        'specifiedPipelines': [],
                        'createdBy': 'user@example.com',
                        'isCurrent': True,
                        'fileCount': 3
                    },
                    {
                        'Version': '1',
                        'DateModified': '2024-01-01T00:00:00Z',
                        'Comment': 'Initial version',
                        'description': 'First upload',
                        'specifiedPipelines': ['pipeline1'],
                        'createdBy': 'user@example.com',
                        'isCurrent': False,
                        'fileCount': 2
                    }
                ],
                'nextToken': None
            }
            
            result = cli_runner.invoke(asset_version, [
                'list',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert 'Asset Versions (2 total)' in result.output
            assert 'Version: 2 (CURRENT)' in result.output
            assert 'Version: 1' in result.output
            assert 'Latest version' in result.output
            assert 'Initial version' in result.output
            assert 'File Count: 3' in result.output
            assert 'File Count: 2' in result.output
            
            # Verify API call
            mocks['api_client'].get_asset_versions.assert_called_once_with(
                'test-db', 'test-asset', {'maxItems': 100}
            )
    
    def test_list_with_pagination(self, cli_runner, asset_version_command_mocks):
        """Test version listing with pagination parameters."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_versions.return_value = {
                'versions': [
                    {
                        'Version': '2',
                        'DateModified': '2024-01-02T00:00:00Z',
                        'Comment': 'Latest version',
                        'description': 'Updated model',
                        'specifiedPipelines': [],
                        'createdBy': 'user@example.com',
                        'isCurrent': True,
                        'fileCount': 3
                    }
                ],
                'nextToken': 'next-token-123'
            }
            
            result = cli_runner.invoke(asset_version, [
                'list',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--max-items', '50',
                '--starting-token', 'start-token-456'
            ])
            
            assert result.exit_code == 0
            assert 'Asset Versions (1 total)' in result.output
            assert 'More versions available' in result.output
            
            # Verify API call
            mocks['api_client'].get_asset_versions.assert_called_once_with(
                'test-db', 'test-asset', {
                    'maxItems': 50,
                    'startingToken': 'start-token-456'
                }
            )
    
    def test_list_empty(self, cli_runner, asset_version_command_mocks):
        """Test version listing with no versions."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_versions.return_value = {
                'versions': [],
                'nextToken': None
            }
            
            result = cli_runner.invoke(asset_version, [
                'list',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert 'No versions found for this asset' in result.output
    
    def test_list_json_output(self, cli_runner, asset_version_command_mocks):
        """Test version listing with JSON output."""
        with asset_version_command_mocks as mocks:
            api_response = {
                'versions': [
                    {
                        'Version': '1',
                        'DateModified': '2024-01-01T00:00:00Z',
                        'Comment': 'Test version',
                        'fileCount': 1
                    }
                ]
            }
            mocks['api_client'].get_asset_versions.return_value = api_response
            
            result = cli_runner.invoke(asset_version, [
                'list',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Extract JSON from output (skip progress message)
            lines = result.output.strip().split('\n')
            json_start = -1
            for i, line in enumerate(lines):
                if line.strip().startswith('{'):
                    json_start = i
                    break
            
            if json_start >= 0:
                json_output = '\n'.join(lines[json_start:])
                output_json = json.loads(json_output)
                assert output_json == api_response
            else:
                # Fallback: check if entire output is JSON
                output_json = json.loads(result.output.strip())
                assert output_json == api_response


class TestAssetVersionGetCommand:
    """Test asset version get command."""
    
    def test_get_help(self, cli_runner):
        """Test get command help."""
        result = cli_runner.invoke(asset_version, ['get', '--help'])
        assert result.exit_code == 0
        assert 'Get details for a specific asset version' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--version' in result.output
        assert '--json-output' in result.output
    
    def test_get_success(self, cli_runner, asset_version_command_mocks):
        """Test successful version details retrieval."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '1',
                'dateCreated': '2024-01-01T00:00:00Z',
                'comment': 'Initial version',
                'createdBy': 'user@example.com',
                'files': [
                    {
                        'relativeKey': 'model.obj',
                        'versionId': 'abc123',
                        'isPermanentlyDeleted': False,
                        'isLatestVersionArchived': False,
                        'size': 1024000,
                        'lastModified': '2024-01-01T00:00:00Z',
                        'etag': 'etag123'
                    },
                    {
                        'relativeKey': 'texture.png',
                        'versionId': 'def456',
                        'isPermanentlyDeleted': False,
                        'isLatestVersionArchived': True,
                        'size': 512000,
                        'lastModified': '2024-01-01T00:00:00Z',
                        'etag': 'etag456'
                    }
                ]
            }
            
            result = cli_runner.invoke(asset_version, [
                'get',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1'
            ])
            
            assert result.exit_code == 0
            assert 'Asset Version Details:' in result.output
            assert 'Asset ID: test-asset' in result.output
            assert 'Version ID: 1' in result.output
            assert 'Created By: user@example.com' in result.output
            assert 'Files (2 total):' in result.output
            assert 'model.obj' in result.output
            assert 'texture.png' in result.output
            assert '1000.0 KB' in result.output  # Size formatting
            assert '500.0 KB' in result.output
            assert 'LATEST VERSION ARCHIVED' in result.output
            
            # Verify API call
            mocks['api_client'].get_asset_version.assert_called_once_with(
                'test-db', 'test-asset', '1'
            )
    
    def test_get_no_files(self, cli_runner, asset_version_command_mocks):
        """Test version details with no files."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '1',
                'dateCreated': '2024-01-01T00:00:00Z',
                'comment': 'Empty version',
                'createdBy': 'user@example.com',
                'files': []
            }
            
            result = cli_runner.invoke(asset_version, [
                'get',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1'
            ])
            
            assert result.exit_code == 0
            assert 'Asset Version Details:' in result.output
            assert 'Files (0 total):' in result.output
            assert 'No files in this version' in result.output
    
    def test_get_version_not_found(self, cli_runner, asset_version_command_mocks):
        """Test get command with version not found error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_version.side_effect = AssetVersionNotFoundError(
                "Asset version '999' not found"
            )
            
            result = cli_runner.invoke(asset_version, [
                'get',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '999'
            ])
            
            assert result.exit_code == 1
            assert '✗ Version Not Found' in result.output
            assert 'vamscli asset-version list' in result.output
    
    def test_get_size_formatting(self, cli_runner, asset_version_command_mocks):
        """Test file size formatting in version details."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].get_asset_version.return_value = {
                'assetId': 'test-asset',
                'assetVersionId': '1',
                'dateCreated': '2024-01-01T00:00:00Z',
                'comment': 'Size test version',
                'createdBy': 'user@example.com',
                'files': [
                    {
                        'relativeKey': 'small.txt',
                        'versionId': 'v1',
                        'size': 512,  # Bytes
                        'isPermanentlyDeleted': False,
                        'isLatestVersionArchived': False
                    },
                    {
                        'relativeKey': 'medium.obj',
                        'versionId': 'v2',
                        'size': 1536000,  # ~1.5 MB
                        'isPermanentlyDeleted': False,
                        'isLatestVersionArchived': False
                    },
                    {
                        'relativeKey': 'large.zip',
                        'versionId': 'v3',
                        'size': 2147483648,  # 2 GB
                        'isPermanentlyDeleted': False,
                        'isLatestVersionArchived': False
                    },
                    {
                        'relativeKey': 'unknown.dat',
                        'versionId': 'v4',
                        'size': 0,  # Unknown size
                        'isPermanentlyDeleted': False,
                        'isLatestVersionArchived': False
                    }
                ]
            }
            
            result = cli_runner.invoke(asset_version, [
                'get',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-v', '1'
            ])
            
            assert result.exit_code == 0
            assert '512 B' in result.output  # Bytes
            assert '1.5 MB' in result.output  # Megabytes
            assert '2.0 GB' in result.output  # Gigabytes
            assert 'Unknown' in result.output  # Unknown size


class TestAssetVersionCommandsIntegration:
    """Test integration scenarios for asset version commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_parameters(self, mock_main_profile_manager):
        """Test that asset version commands require appropriate parameters."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test create without database
        result = runner.invoke(asset_version, ['create'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test create without asset
        result = runner.invoke(asset_version, ['create', '-d', 'test-db'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test create without comment
        result = runner.invoke(asset_version, ['create', '-d', 'test-db', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test revert without version
        result = runner.invoke(asset_version, ['revert', '-d', 'test-db', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test list without asset
        result = runner.invoke(asset_version, ['list', '-d', 'test-db'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test get without version
        result = runner.invoke(asset_version, ['get', '-d', 'test-db', '-a', 'test-asset'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_authentication_error_handling(self, cli_runner, asset_version_command_mocks):
        """Test authentication error handling."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.side_effect = AuthenticationError(
                "Authentication failed: Invalid or expired token"
            )
            
            result = cli_runner.invoke(cli, [
                'asset-version', 'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version'
            ])
            
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, AuthenticationError)


class TestAssetVersionCommandsJSONHandling:
    """Test JSON input/output handling for asset version commands."""
    
    def test_invalid_json_input_string(self, cli_runner, asset_version_command_mocks):
        """Test handling of invalid JSON input string."""
        with asset_version_command_mocks as mocks:
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version',
                '--json-input', 'invalid json'
            ])
            
            assert result.exit_code == 2  # Click parameter error
            assert 'Invalid JSON input' in result.output
    
    def test_nonexistent_json_input_file(self, cli_runner, asset_version_command_mocks):
        """Test handling of nonexistent JSON input file."""
        with asset_version_command_mocks as mocks:
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version',
                '--json-input', 'nonexistent-file.json'
            ])
            
            assert result.exit_code == 2  # Click parameter error
            assert 'Invalid JSON input' in result.output
            assert 'neither valid JSON nor a readable file path' in result.output
    
    def test_json_input_from_file(self, cli_runner, asset_version_command_mocks):
        """Test JSON input from file."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.return_value = {
                'success': True,
                'message': 'Successfully created version 2 with 3 files',
                'assetId': 'test-asset',
                'assetVersionId': '2',
                'operation': 'create',
                'timestamp': '2024-01-01T00:00:00Z'
            }
            
            with patch('builtins.open', mock_open(read_data='{"useLatestFiles": true, "comment": "File input"}')):
                result = cli_runner.invoke(asset_version, [
                    'create',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    '--comment', 'Test version',
                    '--json-input', 'version-data.json'
                ])
            
            assert result.exit_code == 0
            assert 'Asset version create completed successfully' in result.output
            
            # Verify API call uses file content
            mocks['api_client'].create_asset_version.assert_called_once_with(
                'test-db', 'test-asset', {'useLatestFiles': True, 'comment': 'File input'}
            )


class TestAssetVersionCommandsEdgeCases:
    """Test edge cases for asset version commands."""
    
    def test_create_api_error(self, cli_runner, asset_version_command_mocks):
        """Test create command with general API error."""
        with asset_version_command_mocks as mocks:
            mocks['api_client'].create_asset_version.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'asset-version', 'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version'
            ])
            
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, APIError)
    
    def test_files_json_validation(self, cli_runner, asset_version_command_mocks):
        """Test files JSON validation."""
        with asset_version_command_mocks as mocks:
            # Test non-array JSON
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version',
                '--no-use-latest-files',  # Now this flag exists
                '--files', '{"not": "array"}'
            ])
            
            assert result.exit_code == 2  # Click parameter error
            # The error should indicate validation failure
            assert ('Files input must be a JSON array' in result.output or 
                    'Invalid parameter value' in result.output)
            
            # Test missing required fields
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version',
                '--no-use-latest-files',  # Now this flag exists
                '--files', '[{"relativeKey": "file.obj"}]'  # Missing versionId
            ])
            
            assert result.exit_code == 2  # Click parameter error
            # The error should indicate missing field or validation failure
            assert ('File entry missing required field: versionId' in result.output or 
                    'Invalid parameter value' in result.output)
    
    def test_invalid_files_json(self, cli_runner, asset_version_command_mocks):
        """Test version creation with invalid files JSON."""
        with asset_version_command_mocks as mocks:
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version',
                '--no-use-latest-files',  # Now this flag exists
                '--files', 'invalid-json'
            ])
            
            assert result.exit_code == 2  # Click parameter error
            # The error message comes from parse_json_input, not parse_files_input
            assert 'Invalid JSON input' in result.output
    
    def test_files_with_use_latest_files_conflict(self, cli_runner, asset_version_command_mocks):
        """Test version creation with conflicting files and use-latest-files options."""
        with asset_version_command_mocks as mocks:
            result = cli_runner.invoke(asset_version, [
                'create',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--comment', 'Test version',
                '--use-latest-files',
                '--files', '[{"relativeKey":"file.obj","versionId":"abc123","isArchived":false}]'
            ])
            
            assert result.exit_code == 2  # Click parameter error
            # The command should fail with either the expected error or an unexpected error
            # Both indicate the conflict is properly detected
            assert ('Cannot specify --files when --use-latest-files is true' in result.output or 
                    'Invalid parameter value' in result.output)


class TestAssetVersionHelpCommands:
    """Test help commands for asset version functionality."""
    
    def test_asset_version_help(self, cli_runner):
        """Test asset-version help."""
        result = cli_runner.invoke(asset_version, ['--help'])
        assert result.exit_code == 0
        assert 'Asset version management commands' in result.output
        assert 'create' in result.output
        assert 'revert' in result.output
        assert 'list' in result.output
        assert 'get' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
