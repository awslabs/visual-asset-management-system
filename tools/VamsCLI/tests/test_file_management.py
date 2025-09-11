"""Test file management functionality."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    APIError, AuthenticationError, AssetNotFoundError, InvalidAssetDataError,
    FileNotFoundError, FileOperationError, InvalidPathError, FilePermissionError,
    FileAlreadyExistsError, FileArchivedError, InvalidVersionError
)


# File-level fixtures for file-specific testing patterns
@pytest.fixture
def file_command_mocks(generic_command_mocks):
    """Provide file-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for file command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('file')


@pytest.fixture
def file_no_setup_mocks(no_setup_command_mocks):
    """Provide file command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('file')


class TestFileUploadCommand:
    """Test file upload command."""
    
    def test_upload_help(self, cli_runner):
        """Test upload command help."""
        result = cli_runner.invoke(cli, ['file', 'upload', '--help'])
        assert result.exit_code == 0
        assert 'Upload files to an asset' in result.output
        assert '-d, --database' in result.output
        assert '-a, --asset' in result.output
        assert '--directory' in result.output
        assert '--asset-preview' in result.output
        assert '--recursive' in result.output
    
    def test_upload_success(self, cli_runner, file_command_mocks):
        """Test successful file upload."""
        with file_command_mocks as mocks:
            # Use isolated filesystem to create actual test files
            with cli_runner.isolated_filesystem():
                # Create a test file
                with open('test_file.gltf', 'w') as f:
                    f.write('test content')
                
                # Mock the upload process
                with patch('vamscli.commands.file.collect_files_from_list') as mock_collect, \
                     patch('vamscli.commands.file.validate_file_for_upload') as mock_validate, \
                     patch('vamscli.commands.file.validate_preview_files_have_base_files') as mock_validate_preview, \
                     patch('vamscli.commands.file.create_upload_sequences') as mock_sequences, \
                     patch('vamscli.commands.file.get_upload_summary') as mock_summary, \
                     patch('vamscli.commands.file.asyncio.run') as mock_asyncio:
                    
                    # Setup mocks
                    mock_file_info = Mock()
                    mock_file_info.local_path = 'test_file.gltf'
                    mock_file_info.relative_key = '/test_file.gltf'
                    mock_collect.return_value = [mock_file_info]
                    mock_validate_preview.return_value = (True, [])
                    mock_sequences.return_value = [{'files': [mock_file_info]}]
                    mock_summary.return_value = {
                        'total_files': 1,
                        'regular_files': 1,
                        'preview_files': 0,
                        'total_size_formatted': '1.0 KB',
                        'total_sequences': 1,
                        'total_parts': 1
                    }
                    mock_asyncio.return_value = {
                        'overall_success': True,
                        'successful_files': 1,
                        'failed_files': 0,
                        'total_files': 1,
                        'total_size_formatted': '1.0 KB',
                        'upload_duration': 5.0,
                        'average_speed_formatted': '200 B/s',
                        'sequence_results': []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        'test_file.gltf'
                    ])
                    
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output
                    assert 'Successful files: 1/1' in result.output
    
    def test_upload_no_setup(self, cli_runner, file_no_setup_mocks):
        """Test upload without setup."""
        with file_no_setup_mocks as mocks:
            with cli_runner.isolated_filesystem():
                # Create a test file
                with open('test_file.gltf', 'w') as f:
                    f.write('test content')
                
                result = cli_runner.invoke(cli, [
                    'file', 'upload',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    'test_file.gltf'
                ])
                
                assert result.exit_code == 1
                # With new global exception handling, SetupRequiredError is raised
                # The exception message contains the setup requirement
                assert isinstance(result.exception, Exception)
                assert 'Setup required' in str(result.exception)
    
    def test_upload_missing_required_args(self, cli_runner, file_command_mocks):
        """Test upload command with missing required arguments."""
        # Test missing database ID - Click will handle this as parameter error (exit code 2)
        result = cli_runner.invoke(cli, [
            'file', 'upload',
            '-a', 'test-asset',
            '/test/file.gltf'
        ])
        assert result.exit_code == 2  # Click parameter error
        assert 'does not exist' in result.output or 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test missing asset ID - Click will handle this as parameter error (exit code 2)
        result = cli_runner.invoke(cli, [
            'file', 'upload',
            '-d', 'test-db',
            '/test/file.gltf'
        ])
        assert result.exit_code == 2  # Click parameter error
        assert 'does not exist' in result.output or 'Missing option' in result.output or 'required' in result.output.lower()
        
        # Test missing files - this will be handled by our validation logic
        with file_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'upload',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            assert result.exit_code == 1
            assert 'Must specify files or directory to upload' in result.output
    
    def test_upload_json_input(self, cli_runner, file_command_mocks):
        """Test upload with JSON input."""
        with file_command_mocks as mocks:
            with cli_runner.isolated_filesystem():
                # Create a test file
                with open('test_file.gltf', 'w') as f:
                    f.write('test content')
                
                with patch('vamscli.commands.file.collect_files_from_list') as mock_collect, \
                     patch('vamscli.commands.file.validate_file_for_upload') as mock_validate, \
                     patch('vamscli.commands.file.validate_preview_files_have_base_files') as mock_validate_preview, \
                     patch('vamscli.commands.file.create_upload_sequences') as mock_sequences, \
                     patch('vamscli.commands.file.get_upload_summary') as mock_summary, \
                     patch('vamscli.commands.file.asyncio.run') as mock_asyncio:
                    
                    # Setup mocks
                    mock_file_info = Mock()
                    mock_file_info.local_path = 'test_file.gltf'
                    mock_file_info.relative_key = '/test_file.gltf'
                    mock_collect.return_value = [mock_file_info]
                    mock_validate_preview.return_value = (True, [])
                    mock_sequences.return_value = [{'files': [mock_file_info]}]
                    mock_summary.return_value = {
                        'total_files': 1,
                        'regular_files': 1,
                        'preview_files': 0,
                        'total_size_formatted': '1.0 KB',
                        'total_sequences': 1,
                        'total_parts': 1
                    }
                    mock_asyncio.return_value = {
                        'overall_success': True,
                        'successful_files': 1,
                        'failed_files': 0,
                        'total_files': 1,
                        'total_size_formatted': '1.0 KB',
                        'upload_duration': 5.0,
                        'average_speed_formatted': '200 B/s',
                        'sequence_results': []
                    }
                    
                    json_input = json.dumps({
                        'database_id': 'test-db',
                        'asset_id': 'test-asset',
                        'files': ['test_file.gltf']
                    })
                    
                    # JSON input still requires CLI parameters due to Click's required validation
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',  # Required by Click
                        '-a', 'test-asset',  # Required by Click
                        '--json-input', json_input
                    ])
                    
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output


class TestFileCreateFolderCommand:
    """Test file create-folder command."""
    
    def test_create_folder_help(self, cli_runner):
        """Test create-folder command help."""
        result = cli_runner.invoke(cli, ['file', 'create-folder', '--help'])
        assert result.exit_code == 0
        assert 'Create a folder in an asset' in result.output
        assert '-d, --database' in result.output
        assert '-a, --asset' in result.output
        assert '-p, --path' in result.output
    
    def test_create_folder_success(self, cli_runner, file_command_mocks):
        """Test successful folder creation."""
        with file_command_mocks as mocks:
            mocks['api_client'].create_folder.return_value = {
                'message': 'Folder created successfully',
                'relativeKey': '/models/subfolder/'
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'create-folder',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/models/subfolder/'
            ])
            
            assert result.exit_code == 0
            assert '✓ Folder created successfully!' in result.output
            assert 'Path: /models/subfolder/' in result.output
            
            # Verify API call
            mocks['api_client'].create_folder.assert_called_once_with(
                'test-db', 'test-asset', {'keyPath': '/models/subfolder/'}
            )
    
    def test_create_folder_no_setup(self, cli_runner, file_no_setup_mocks):
        """Test create-folder without setup."""
        with file_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'create-folder',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/models/subfolder/'
            ])
            
            assert result.exit_code == 1
            # With new global exception handling, SetupRequiredError is raised
            assert isinstance(result.exception, Exception)
            assert 'Setup required' in str(result.exception)
    
    def test_create_folder_json_input(self, cli_runner, file_command_mocks):
        """Test folder creation with JSON input."""
        with file_command_mocks as mocks:
            mocks['api_client'].create_folder.return_value = {
                'message': 'Folder created successfully',
                'relativeKey': '/models/'
            }
            
            json_input = json.dumps({
                'database_id': 'test-db',
                'asset_id': 'test-asset',
                'folder_path': '/models/'
            })
            
            # JSON input still requires CLI parameters due to Click's required validation
            result = cli_runner.invoke(cli, [
                'file', 'create-folder',
                '-d', 'test-db',  # Required by Click
                '-a', 'test-asset',  # Required by Click
                '-p', '/models/',  # Required by Click
                '--json-input', json_input
            ])
            
            assert result.exit_code == 0
            assert '✓ Folder created successfully!' in result.output
            
            # Verify API call with JSON data (JSON overrides CLI values)
            mocks['api_client'].create_folder.assert_called_once_with(
                'test-db', 'test-asset', {'keyPath': '/models/'}
            )
    
    def test_create_folder_asset_not_found(self, cli_runner, file_command_mocks):
        """Test create-folder with asset not found error."""
        with file_command_mocks as mocks:
            mocks['api_client'].create_folder.side_effect = AssetNotFoundError(
                "Asset 'nonexistent' not found in database 'test-db'"
            )
            
            result = cli_runner.invoke(cli, [
                'file', 'create-folder',
                '-d', 'test-db',
                '-a', 'nonexistent',
                '-p', '/models/'
            ])
            
            assert result.exit_code == 1
            assert 'Asset \'nonexistent\' not found' in result.output


class TestFileListCommand:
    """Test file list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['file', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List files in an asset' in result.output
        assert '--prefix' in result.output
        assert '--include-archived' in result.output
        assert '--max-items' in result.output
        assert '--page-size' in result.output
    
    def test_list_success(self, cli_runner, file_command_mocks):
        """Test successful file listing."""
        with file_command_mocks as mocks:
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {
                        'fileName': 'model.gltf',
                        'relativePath': '/model.gltf',
                        'isFolder': False,
                        'size': 1024,
                        'isArchived': False,
                        'primaryType': 'primary'
                    },
                    {
                        'fileName': 'textures',
                        'relativePath': '/textures/',
                        'isFolder': True,
                        'isArchived': False
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'list',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ Found 2 files' in result.output
            assert '📄 /model.gltf (1024 bytes) [primary]' in result.output
            assert '📁 /textures/' in result.output
            
            # Verify API call
            expected_params = {
                'maxItems': 1000,
                'pageSize': 100
            }
            mocks['api_client'].list_asset_files.assert_called_once_with(
                'test-db', 'test-asset', expected_params
            )
    
    def test_list_no_setup(self, cli_runner, file_no_setup_mocks):
        """Test list without setup."""
        with file_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'list',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            # With new global exception handling, SetupRequiredError is raised
            assert isinstance(result.exception, Exception)
            assert 'Setup required' in str(result.exception)
    
    def test_list_with_filters(self, cli_runner, file_command_mocks):
        """Test file listing with filters."""
        with file_command_mocks as mocks:
            mocks['api_client'].list_asset_files.return_value = {
                'items': [
                    {
                        'fileName': 'model.gltf',
                        'relativePath': '/models/model.gltf',
                        'isFolder': False,
                        'size': 1024,
                        'isArchived': True
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'list',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--prefix', 'models/',
                '--include-archived',
                '--max-items', '50'
            ])
            
            assert result.exit_code == 0
            assert '✓ Found 1 files' in result.output
            assert '(archived)' in result.output
            
            # Verify API call with filters
            expected_params = {
                'maxItems': 50,
                'pageSize': 100,
                'prefix': 'models/',
                'includeArchived': 'true'
            }
            mocks['api_client'].list_asset_files.assert_called_once_with(
                'test-db', 'test-asset', expected_params
            )
    
    def test_list_json_output(self, cli_runner, file_command_mocks):
        """Test file listing with JSON output."""
        with file_command_mocks as mocks:
            api_response = {
                'items': [
                    {
                        'fileName': 'model.gltf',
                        'relativePath': '/model.gltf',
                        'isFolder': False,
                        'size': 1024
                    }
                ]
            }
            mocks['api_client'].list_asset_files.return_value = api_response
            
            result = cli_runner.invoke(cli, [
                'file', 'list',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--json-output'
            ])
            
            assert result.exit_code == 0
            # Should output raw JSON
            output_json = json.loads(result.output.strip())
            assert output_json == api_response


class TestFileInfoCommand:
    """Test file info command."""
    
    def test_info_help(self, cli_runner):
        """Test info command help."""
        result = cli_runner.invoke(cli, ['file', 'info', '--help'])
        assert result.exit_code == 0
        assert 'Get detailed information about a specific file' in result.output
        assert '--include-versions' in result.output
        assert '-p, --path' in result.output
    
    def test_info_success(self, cli_runner, file_command_mocks):
        """Test successful file info retrieval."""
        with file_command_mocks as mocks:
            mocks['api_client'].get_file_info.return_value = {
                'fileName': 'model.gltf',
                'relativePath': '/model.gltf',
                'isFolder': False,
                'size': 1024,
                'contentType': 'model/gltf+json',
                'lastModified': '2023-01-01T00:00:00Z',
                'storageClass': 'STANDARD',
                'isArchived': False,
                'primaryType': 'primary',
                'previewFile': '/model.previewFile.png'
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'info',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/model.gltf'
            ])
            
            assert result.exit_code == 0
            assert '✓ File information retrieved' in result.output
            assert 'File: model.gltf' in result.output
            assert 'Size: 1024 bytes' in result.output
            assert 'Primary Type: primary' in result.output
            
            # Verify API call
            expected_params = {
                'filePath': '/model.gltf',
                'includeVersions': 'false'
            }
            mocks['api_client'].get_file_info.assert_called_once_with(
                'test-db', 'test-asset', expected_params
            )
    
    def test_info_no_setup(self, cli_runner, file_no_setup_mocks):
        """Test info without setup."""
        with file_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'info',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/model.gltf'
            ])
            
            assert result.exit_code == 1
            # With new global exception handling, SetupRequiredError is raised
            assert isinstance(result.exception, Exception)
            assert 'Setup required' in str(result.exception)
    
    def test_info_with_versions(self, cli_runner, file_command_mocks):
        """Test file info with version history."""
        with file_command_mocks as mocks:
            mocks['api_client'].get_file_info.return_value = {
                'fileName': 'model.gltf',
                'relativePath': '/model.gltf',
                'isFolder': False,
                'size': 1024,
                'versions': [
                    {
                        'versionId': 'version-123',
                        'isLatest': True,
                        'lastModified': '2023-01-01T00:00:00Z',
                        'size': 1024,
                        'isArchived': False
                    },
                    {
                        'versionId': 'version-122',
                        'isLatest': False,
                        'lastModified': '2022-12-31T00:00:00Z',
                        'size': 1000,
                        'isArchived': False
                    }
                ]
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'info',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/model.gltf',
                '--include-versions'
            ])
            
            assert result.exit_code == 0
            assert 'Versions (2):' in result.output
            assert 'version-123 - Current' in result.output
            assert 'version-122 - Previous' in result.output


class TestFileOperationCommands:
    """Test file operation commands (move, copy, archive, delete, etc.)."""
    
    def test_move_help(self, cli_runner):
        """Test move command help."""
        result = cli_runner.invoke(cli, ['file', 'move', '--help'])
        assert result.exit_code == 0
        assert 'Move a file within an asset' in result.output
        assert '--source' in result.output
        assert '--dest' in result.output
    
    def test_move_success(self, cli_runner, file_command_mocks):
        """Test successful file move."""
        with file_command_mocks as mocks:
            mocks['api_client'].move_file.return_value = {
                'success': True,
                'message': 'File moved successfully',
                'affectedFiles': ['/old/path.gltf', '/new/path.gltf']
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'move',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--source', '/old/path.gltf',
                '--dest', '/new/path.gltf'
            ])
            
            assert result.exit_code == 0
            assert '✓ File moved successfully!' in result.output
            assert 'From: /old/path.gltf' in result.output
            assert 'To: /new/path.gltf' in result.output
    
    def test_copy_help(self, cli_runner):
        """Test copy command help."""
        result = cli_runner.invoke(cli, ['file', 'copy', '--help'])
        assert result.exit_code == 0
        assert 'Copy a file within an asset or to another asset' in result.output
        assert '--dest-asset' in result.output
    
    def test_copy_cross_asset(self, cli_runner, file_command_mocks):
        """Test successful cross-asset file copy."""
        with file_command_mocks as mocks:
            mocks['api_client'].copy_file.return_value = {
                'success': True,
                'message': 'File copied successfully',
                'affectedFiles': ['/file.gltf', '/file.previewFile.png']
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'copy',
                '-d', 'test-db',
                '-a', 'source-asset',
                '--source', '/file.gltf',
                '--dest', '/file.gltf',
                '--dest-asset', 'dest-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ File copied successfully!' in result.output
            assert 'Destination Asset: dest-asset' in result.output
            assert 'Additional files copied: 1' in result.output
    
    def test_archive_help(self, cli_runner):
        """Test archive command help."""
        result = cli_runner.invoke(cli, ['file', 'archive', '--help'])
        assert result.exit_code == 0
        assert 'Archive a file or files under a prefix (soft delete)' in result.output
        assert '--prefix' in result.output
    
    def test_archive_prefix(self, cli_runner, file_command_mocks):
        """Test archiving files under a prefix."""
        with file_command_mocks as mocks:
            mocks['api_client'].archive_file.return_value = {
                'success': True,
                'message': 'Files archived successfully',
                'affectedFiles': ['/folder/file1.gltf', '/folder/file2.obj']
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'archive',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/folder/',
                '--prefix'
            ])
            
            assert result.exit_code == 0
            assert '✓ File(s) archived successfully!' in result.output
            assert 'Operation: Archive all files under prefix' in result.output
            assert 'Files archived: 2' in result.output
    
    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(cli, ['file', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Permanently delete a file or files under a prefix' in result.output
        assert '--confirm' in result.output
    
    def test_delete_requires_confirmation(self, cli_runner, file_command_mocks):
        """Test that delete requires confirmation."""
        with file_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'delete',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/file.gltf'
            ])
            
            assert result.exit_code == 1
            assert 'Permanent deletion requires confirmation' in result.output
    
    def test_delete_success(self, cli_runner, file_command_mocks):
        """Test successful file deletion."""
        with file_command_mocks as mocks:
            mocks['api_client'].delete_file.return_value = {
                'success': True,
                'message': 'File deleted successfully',
                'affectedFiles': ['/file.gltf']
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'delete',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/file.gltf',
                '--confirm'
            ])
            
            assert result.exit_code == 0
            assert '✓ File(s) deleted permanently!' in result.output
            assert 'Files deleted: 1' in result.output


class TestFileVersionCommands:
    """Test file version management commands."""
    
    def test_revert_help(self, cli_runner):
        """Test revert command help."""
        result = cli_runner.invoke(cli, ['file', 'revert', '--help'])
        assert result.exit_code == 0
        assert 'Revert a file to a previous version' in result.output
        assert '-v, --version' in result.output
    
    def test_revert_success(self, cli_runner, file_command_mocks):
        """Test successful file revert."""
        with file_command_mocks as mocks:
            mocks['api_client'].revert_file_version.return_value = {
                'success': True,
                'message': 'File reverted successfully',
                'filePath': '/file.gltf',
                'revertedFromVersionId': 'version-123',
                'newVersionId': 'version-456'
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'revert',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/file.gltf',
                '-v', 'version-123'
            ])
            
            assert result.exit_code == 0
            assert '✓ File reverted successfully!' in result.output
            assert 'Reverted from version: version-123' in result.output
            assert 'New version ID: version-456' in result.output
    
    def test_revert_no_setup(self, cli_runner, file_no_setup_mocks):
        """Test revert without setup."""
        with file_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'revert',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/file.gltf',
                '-v', 'version-123'
            ])
            
            assert result.exit_code == 1
            # With new global exception handling, SetupRequiredError is raised
            assert isinstance(result.exception, Exception)
            assert 'Setup required' in str(result.exception)


class TestFilePrimaryTypeCommands:
    """Test file primary type management commands."""
    
    def test_set_primary_help(self, cli_runner):
        """Test set-primary command help."""
        result = cli_runner.invoke(cli, ['file', 'set-primary', '--help'])
        assert result.exit_code == 0
        assert 'Set or remove primary type metadata for a file' in result.output
        assert '--type' in result.output
        assert '--type-other' in result.output
    
    def test_set_primary_success(self, cli_runner, file_command_mocks):
        """Test successful primary type setting."""
        with file_command_mocks as mocks:
            mocks['api_client'].set_primary_file.return_value = {
                'success': True,
                'message': 'Primary type set successfully',
                'filePath': '/file.gltf',
                'primaryType': 'primary'
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'set-primary',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/file.gltf',
                '--type', 'primary'
            ])
            
            assert result.exit_code == 0
            assert '✓ Primary type set successfully!' in result.output
            assert 'Type: primary' in result.output
    
    def test_set_primary_other_type(self, cli_runner, file_command_mocks):
        """Test setting custom primary type."""
        with file_command_mocks as mocks:
            mocks['api_client'].set_primary_file.return_value = {
                'success': True,
                'message': 'Primary type set successfully',
                'filePath': '/file.gltf',
                'primaryType': 'custom-type'
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'set-primary',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/file.gltf',
                '--type', 'other',
                '--type-other', 'custom-type'
            ])
            
            assert result.exit_code == 0
            assert '✓ Primary type set successfully!' in result.output
            assert 'Type: custom-type' in result.output
    
    def test_set_primary_other_requires_type_other(self, cli_runner, file_command_mocks):
        """Test that 'other' type requires --type-other."""
        with file_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'set-primary',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/file.gltf',
                '--type', 'other'
            ])
            
            assert result.exit_code == 1
            assert 'Custom type is required when using --type other' in result.output
    
    def test_set_primary_no_setup(self, cli_runner, file_no_setup_mocks):
        """Test set-primary without setup."""
        with file_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'set-primary',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/file.gltf',
                '--type', 'primary'
            ])
            
            assert result.exit_code == 1
            # With new global exception handling, SetupRequiredError is raised
            assert isinstance(result.exception, Exception)
            assert 'Setup required' in str(result.exception)


class TestFilePreviewCommands:
    """Test file preview management commands."""
    
    def test_delete_preview_help(self, cli_runner):
        """Test delete-preview command help."""
        result = cli_runner.invoke(cli, ['file', 'delete-preview', '--help'])
        assert result.exit_code == 0
        assert 'Delete the asset preview file' in result.output
        assert '-d, --database' in result.output
        assert '-a, --asset' in result.output
    
    def test_delete_preview_success(self, cli_runner, file_command_mocks):
        """Test successful asset preview deletion."""
        with file_command_mocks as mocks:
            mocks['api_client'].delete_asset_preview.return_value = {
                'success': True,
                'message': 'Asset preview deleted successfully',
                'assetId': 'test-asset'
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'delete-preview',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ Asset preview deleted successfully!' in result.output
            assert 'Asset: test-asset' in result.output
    
    def test_delete_preview_no_setup(self, cli_runner, file_no_setup_mocks):
        """Test delete-preview without setup."""
        with file_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'delete-preview',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            # With new global exception handling, SetupRequiredError is raised
            assert isinstance(result.exception, Exception)
            assert 'Setup required' in str(result.exception)
    
    def test_delete_auxiliary_help(self, cli_runner):
        """Test delete-auxiliary command help."""
        result = cli_runner.invoke(cli, ['file', 'delete-auxiliary', '--help'])
        assert result.exit_code == 0
        assert 'Delete auxiliary preview asset files' in result.output
        assert '-p, --path' in result.output
    
    def test_delete_auxiliary_success(self, cli_runner, file_command_mocks):
        """Test successful auxiliary files deletion."""
        with file_command_mocks as mocks:
            mocks['api_client'].delete_auxiliary_preview_files.return_value = {
                'success': True,
                'message': 'Auxiliary files deleted successfully',
                'filePath': '/file.gltf',
                'deletedCount': 3
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'delete-auxiliary',
                '-d', 'test-db',
                '-a', 'test-asset',
                '-p', '/file.gltf'
            ])
            
            assert result.exit_code == 0
            assert '✓ Auxiliary preview files deleted successfully!' in result.output
            assert 'Files deleted: 3' in result.output


class TestFileCommandsIntegration:
    """Test integration scenarios for file commands."""
    
    def test_authentication_error_handling(self, cli_runner, file_command_mocks):
        """Test authentication error handling."""
        with file_command_mocks as mocks:
            mocks['api_client'].list_asset_files.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, [
                'file', 'list',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            # With new global exception handling, AuthenticationError is raised
            assert isinstance(result.exception, Exception)
            assert 'Authentication failed' in str(result.exception)
    
    def test_api_error_handling(self, cli_runner, file_command_mocks):
        """Test general API error handling."""
        with file_command_mocks as mocks:
            mocks['api_client'].list_asset_files.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'file', 'list',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 1
            # With new global exception handling, APIError is raised
            assert isinstance(result.exception, Exception)
            assert 'API request failed' in str(result.exception)
    
    def test_asset_not_found_error_handling(self, cli_runner, file_command_mocks):
        """Test asset not found error handling."""
        with file_command_mocks as mocks:
            mocks['api_client'].list_asset_files.side_effect = AssetNotFoundError(
                "Asset 'nonexistent' not found in database 'test-db'"
            )
            
            result = cli_runner.invoke(cli, [
                'file', 'list',
                '-d', 'test-db',
                '-a', 'nonexistent'
            ])
            
            assert result.exit_code == 1
            assert 'Asset \'nonexistent\' not found' in result.output


class TestFileJSONInputHandling:
    """Test JSON input handling functionality."""
    
    def test_parse_json_string_success(self, cli_runner, file_command_mocks):
        """Test parsing JSON string input."""
        with file_command_mocks as mocks:
            mocks['api_client'].create_folder.return_value = {
                'message': 'Folder created successfully',
                'relativeKey': '/models/'
            }
            
            json_input = json.dumps({
                'database_id': 'test-db',
                'asset_id': 'test-asset',
                'folder_path': '/models/'
            })
            
            # JSON input still requires CLI parameters due to Click's required validation
            result = cli_runner.invoke(cli, [
                'file', 'create-folder',
                '-d', 'test-db',  # Required by Click
                '-a', 'test-asset',  # Required by Click
                '-p', '/models/',  # Required by Click
                '--json-input', json_input
            ])
            
            assert result.exit_code == 0
            assert '✓ Folder created successfully!' in result.output
    
    def test_parse_json_file_input(self, cli_runner, file_command_mocks):
        """Test parsing JSON from file."""
        with file_command_mocks as mocks:
            mocks['api_client'].create_folder.return_value = {
                'message': 'Folder created successfully',
                'relativeKey': '/models/'
            }
            
            json_data = {
                'database_id': 'test-db',
                'asset_id': 'test-asset',
                'folder_path': '/models/'
            }
            
            with patch('builtins.open', mock_open(read_data=json.dumps(json_data))), \
                 patch('pathlib.Path.exists', return_value=True):
                
                # JSON input still requires CLI parameters due to Click's required validation
                result = cli_runner.invoke(cli, [
                    'file', 'create-folder',
                    '-d', 'test-db',  # Required by Click
                    '-a', 'test-asset',  # Required by Click
                    '-p', '/models/',  # Required by Click
                    '--json-input', '@test.json'
                ])
                
                assert result.exit_code == 0
                assert '✓ Folder created successfully!' in result.output
    
    def test_invalid_json_input(self, cli_runner, file_command_mocks):
        """Test error with invalid JSON input."""
        with file_command_mocks as mocks:
            # JSON input still requires CLI parameters due to Click's required validation
            result = cli_runner.invoke(cli, [
                'file', 'create-folder',
                '-d', 'test-db',  # Required by Click
                '-a', 'test-asset',  # Required by Click
                '-p', '/models/',  # Required by Click
                '--json-input', '{"invalid": json}'
            ])
            
            assert result.exit_code == 1
            # JSON parsing errors cause SystemExit via ClickException
            assert isinstance(result.exception, SystemExit)
            # Check the output for the error message
            assert 'Invalid JSON input' in result.output
    
    def test_json_file_not_found(self, cli_runner, file_command_mocks):
        """Test error when JSON input file doesn't exist."""
        with file_command_mocks as mocks:
            with patch('pathlib.Path.exists', return_value=False):
                # JSON input still requires CLI parameters due to Click's required validation
                result = cli_runner.invoke(cli, [
                    'file', 'create-folder',
                    '-d', 'test-db',  # Required by Click
                    '-a', 'test-asset',  # Required by Click
                    '-p', '/models/',  # Required by Click
                    '--json-input', '@nonexistent.json'
                ])
                
                assert result.exit_code == 1
                # File not found errors cause SystemExit via ClickException
                assert isinstance(result.exception, SystemExit)
                # Check the output for the error message
                assert 'JSON input file not found' in result.output


class TestFileCommandsEdgeCases:
    """Test edge cases for file commands."""
    
    def test_empty_file_list(self, cli_runner, file_command_mocks):
        """Test file listing with no files."""
        with file_command_mocks as mocks:
            mocks['api_client'].list_asset_files.return_value = {
                'items': []
            }
            
            result = cli_runner.invoke(cli, [
                'file', 'list',
                '-d', 'test-db',
                '-a', 'test-asset'
            ])
            
            assert result.exit_code == 0
            assert '✓ Found 0 files' in result.output
    
    def test_file_operation_with_invalid_path(self, cli_runner, file_command_mocks):
        """Test file operation with invalid path."""
        with file_command_mocks as mocks:
            mocks['api_client'].move_file.side_effect = InvalidPathError("Invalid file path")
            
            result = cli_runner.invoke(cli, [
                'file', 'move',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--source', '/invalid/path',
                '--dest', '/new/path'
            ])
            
            assert result.exit_code == 1
            # InvalidPathError is a business logic exception, handled by command
            assert 'Invalid file path' in result.output
    
    def test_file_already_exists_error(self, cli_runner, file_command_mocks):
        """Test file operation when file already exists."""
        with file_command_mocks as mocks:
            mocks['api_client'].copy_file.side_effect = FileAlreadyExistsError("File already exists")
            
            result = cli_runner.invoke(cli, [
                'file', 'copy',
                '-d', 'test-db',
                '-a', 'test-asset',
                '--source', '/file.gltf',
                '--dest', '/existing.gltf'
            ])
            
            assert result.exit_code == 1
            # FileAlreadyExistsError is a business logic exception, handled by command
            assert 'File already exists' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
