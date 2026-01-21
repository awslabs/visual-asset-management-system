"""Test file upload functionality."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock, MagicMock, mock_open

import pytest
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.file_processor import (
    FileInfo, calculate_file_parts, collect_files_from_directory,
    collect_files_from_list, create_upload_sequences, validate_file_for_upload,
    validate_preview_files_have_base_files, format_file_size
)
from vamscli.utils.upload_manager import UploadManager, UploadProgress, PartUploadInfo
from vamscli.utils.exceptions import (
    InvalidFileError, FileTooLargeError, PreviewFileError, UploadSequenceError,
    FileUploadError, AuthenticationError, APIError
)
from vamscli.constants import (
    DEFAULT_CHUNK_SIZE_SMALL, DEFAULT_CHUNK_SIZE_LARGE, MAX_FILE_SIZE_SMALL_CHUNKS,
    MAX_SEQUENCE_SIZE, MAX_PREVIEW_FILE_SIZE, MAX_FILES_PER_REQUEST, 
    MAX_TOTAL_PARTS_PER_REQUEST, MAX_PARTS_PER_FILE
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


class TestFileProcessor:
    """Test file processing utilities."""
    
    def test_calculate_file_parts_small_file(self):
        """Test part calculation for small files."""
        file_size = 100 * 1024 * 1024  # 100MB
        parts = calculate_file_parts(file_size)
        
        assert len(parts) == 1
        assert parts[0]["part_number"] == 1
        assert parts[0]["start_byte"] == 0
        assert parts[0]["end_byte"] == file_size - 1
        assert parts[0]["size"] == file_size
    
    def test_calculate_file_parts_large_file(self):
        """Test part calculation for large files."""
        file_size = 300 * 1024 * 1024  # 300MB
        parts = calculate_file_parts(file_size)
        
        # Should be split into 2 parts of 150MB each
        assert len(parts) == 2
        assert parts[0]["size"] == DEFAULT_CHUNK_SIZE_SMALL
        assert parts[1]["size"] == file_size - DEFAULT_CHUNK_SIZE_SMALL
    
    def test_calculate_file_parts_very_large_file(self):
        """Test part calculation for very large files."""
        file_size = 20 * 1024 * 1024 * 1024  # 20GB
        parts = calculate_file_parts(file_size)
        
        # Should use 1GB chunks
        expected_parts = 20
        assert len(parts) == expected_parts
        
        for i, part in enumerate(parts[:-1]):
            assert part["size"] == DEFAULT_CHUNK_SIZE_LARGE
        
        # Last part might be smaller
        assert parts[-1]["size"] <= DEFAULT_CHUNK_SIZE_LARGE
    
    def test_calculate_file_parts_empty_file(self):
        """Test part calculation for empty files."""
        parts = calculate_file_parts(0)
        
        # Zero-byte files should have no parts (backend expects num_parts: 0)
        assert len(parts) == 0
    
    def test_file_info_creation(self):
        """Test FileInfo object creation."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            file_info = FileInfo(tmp_path, "test/file.txt")
            
            assert file_info.local_path == Path(tmp_path)
            assert file_info.relative_key == "test/file.txt"
            assert file_info.size > 0
            assert not file_info.is_preview_file
            
            # Test preview file detection
            preview_file = FileInfo(tmp_path, "test/file.previewFile.jpg")
            assert preview_file.is_preview_file
        finally:
            Path(tmp_path).unlink()
    
    def test_validate_file_for_upload_valid_file(self):
        """Test file validation for valid files."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = Path(tmp.name)
        
        try:
            # Should not raise any exception
            validate_file_for_upload(tmp_path, "assetFile")
        finally:
            tmp_path.unlink()
    
    def test_validate_file_for_upload_missing_file(self):
        """Test file validation for missing files."""
        non_existent = Path("/non/existent/file.txt")
        
        with pytest.raises(InvalidFileError, match="File not found"):
            validate_file_for_upload(non_existent, "assetFile")
    
    def test_validate_file_for_upload_preview_too_large(self):
        """Test file validation for oversized preview files."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
            # Create a file larger than 5MB
            tmp.write(b"x" * (MAX_PREVIEW_FILE_SIZE + 1))
            tmp_path = Path(tmp.name)
        
        try:
            with pytest.raises(FileTooLargeError, match="exceeds maximum size"):
                validate_file_for_upload(tmp_path, "assetPreview")
        finally:
            tmp_path.unlink()
    
    def test_validate_file_for_upload_invalid_preview_extension(self):
        """Test file validation for invalid preview file extensions."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test")
            tmp_path = Path(tmp.name)
        
        try:
            with pytest.raises(PreviewFileError, match="unsupported extension"):
                validate_file_for_upload(tmp_path, "assetPreview", "test.previewFile.txt")
        finally:
            tmp_path.unlink()
    
    def test_collect_files_from_list(self):
        """Test collecting files from a list of paths."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create test files
            file1 = Path(tmp_dir) / "file1.txt"
            file2 = Path(tmp_dir) / "file2.jpg"
            
            file1.write_text("content1")
            file2.write_text("content2")
            
            files = collect_files_from_list([str(file1), str(file2)], "/test/")
            
            assert len(files) == 2
            assert files[0].relative_key == "/test/file1.txt"
            assert files[1].relative_key == "/test/file2.jpg"
    
    def test_collect_files_from_list_duplicate_names(self):
        """Test collecting files with duplicate names."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create files with same name in different directories
            dir1 = Path(tmp_dir) / "dir1"
            dir2 = Path(tmp_dir) / "dir2"
            dir1.mkdir()
            dir2.mkdir()
            
            file1 = dir1 / "file.txt"
            file2 = dir2 / "file.txt"
            
            file1.write_text("content1")
            file2.write_text("content2")
            
            with pytest.raises(InvalidFileError, match="Duplicate filename"):
                collect_files_from_list([str(file1), str(file2)])
    
    def test_collect_files_from_directory(self):
        """Test collecting files from a directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            
            # Create test files
            (tmp_path / "file1.txt").write_text("content1")
            (tmp_path / "file2.jpg").write_text("content2")
            
            # Create subdirectory
            sub_dir = tmp_path / "subdir"
            sub_dir.mkdir()
            (sub_dir / "file3.png").write_text("content3")
            
            # Test non-recursive
            files = collect_files_from_directory(tmp_path, recursive=False)
            assert len(files) == 2
            
            # Test recursive
            files = collect_files_from_directory(tmp_path, recursive=True)
            assert len(files) == 3
            
            # Check relative keys - should all start with /
            relative_keys = [f.relative_key for f in files]
            assert "/file1.txt" in relative_keys
            assert "/file2.jpg" in relative_keys
            assert "/subdir/file3.png" in relative_keys
    
    def test_create_upload_sequences_small_files(self):
        """Test creating upload sequences for small files."""
        files = [
            FileInfo("/tmp/file1.txt", "file1.txt", 100 * 1024 * 1024),  # 100MB
            FileInfo("/tmp/file2.txt", "file2.txt", 200 * 1024 * 1024),  # 200MB
        ]
        
        sequences = create_upload_sequences(files)
        
        # Should be grouped into one sequence
        assert len(sequences) == 1
        assert len(sequences[0].files) == 2
        assert sequences[0].total_size == 300 * 1024 * 1024
    
    def test_create_upload_sequences_large_files(self):
        """Test creating upload sequences for large files."""
        files = [
            FileInfo("/tmp/file1.txt", "file1.txt", 2 * 1024 * 1024 * 1024),  # 2GB
            FileInfo("/tmp/file2.txt", "file2.txt", 2 * 1024 * 1024 * 1024),  # 2GB
        ]
        
        sequences = create_upload_sequences(files)
        
        # Should exceed 3GB limit, so split into separate sequences
        assert len(sequences) == 2
        assert len(sequences[0].files) == 1
        assert len(sequences[1].files) == 1
    
    def test_create_upload_sequences_preview_files(self):
        """Test creating upload sequences with preview files."""
        files = [
            FileInfo("/tmp/file1.txt", "file1.txt", 100 * 1024 * 1024),
            FileInfo("/tmp/preview1.jpg", "file1.previewFile.jpg", 1 * 1024 * 1024),
            FileInfo("/tmp/file2.txt", "file2.txt", 100 * 1024 * 1024),
        ]
        
        sequences = create_upload_sequences(files)
        
        # Regular files should be in first sequence, preview files in last
        assert len(sequences) == 2
        
        # First sequence should have regular files
        regular_files = [f for f in sequences[0].files if not f.is_preview_file]
        assert len(regular_files) == 2
        
        # Last sequence should have preview files
        preview_files = [f for f in sequences[1].files if f.is_preview_file]
        assert len(preview_files) == 1
    
    def test_path_normalization_default_location(self):
        """Test path normalization with default location (/)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "file1.txt").write_text("content1")
            
            files = collect_files_from_directory(tmp_path, recursive=False, asset_location="/")
            
            assert len(files) == 1
            # Path should start with /
            assert files[0].relative_key.startswith("/")
            assert files[0].relative_key == "/file1.txt"
    
    def test_path_normalization_custom_location(self):
        """Test path normalization with custom location."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "file1.txt").write_text("content1")
            
            # Test with location without leading slash
            files = collect_files_from_directory(tmp_path, recursive=False, asset_location="test")
            assert files[0].relative_key == "/test/file1.txt"
            
            # Test with location with leading slash
            files = collect_files_from_directory(tmp_path, recursive=False, asset_location="/test")
            assert files[0].relative_key == "/test/file1.txt"
            
            # Test with location with trailing slash
            files = collect_files_from_directory(tmp_path, recursive=False, asset_location="test/")
            assert files[0].relative_key == "/test/file1.txt"
    
    def test_path_normalization_file_list(self):
        """Test path normalization for file list collection."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            file1 = Path(tmp_dir) / "file1.txt"
            file1.write_text("content1")
            
            # Test with default location
            files = collect_files_from_list([str(file1)], "/")
            assert files[0].relative_key == "/file1.txt"
            
            # Test with custom location
            files = collect_files_from_list([str(file1)], "test")
            assert files[0].relative_key == "/test/file1.txt"
    
    def test_format_file_size(self):
        """Test file size formatting."""
        assert format_file_size(0) == "0B"
        assert format_file_size(1024) == "1.0KB"
        assert format_file_size(1024 * 1024) == "1.0MB"
        assert format_file_size(1024 * 1024 * 1024) == "1.0GB"
        assert format_file_size(1536) == "1.5KB"


class TestUploadManager:
    """Test upload manager functionality."""
    
    @pytest.fixture
    def mock_api_client(self):
        """Create a mock API client."""
        client = Mock()
        client.initialize_upload.return_value = {
            "uploadId": "test-upload-id",
            "files": [
                {
                    "relativeKey": "test.txt",
                    "uploadIdS3": "s3-upload-id",
                    "numParts": 1,
                    "partUploadUrls": [
                        {
                            "PartNumber": 1,
                            "UploadUrl": "https://s3.amazonaws.com/presigned-url"
                        }
                    ]
                }
            ]
        }
        client.complete_upload.return_value = {
            "message": "Upload completed successfully",
            "overallSuccess": True
        }
        return client
    
    @pytest.fixture
    def sample_sequence(self):
        """Create a sample upload sequence."""
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        file_info = FileInfo(tmp_path, "test.txt", len(b"test content"))
        sequence = create_upload_sequences([file_info])[0]
        
        yield sequence
        
        # Cleanup
        Path(tmp_path).unlink()
    
    def test_upload_progress_initialization(self, sample_sequence):
        """Test upload progress initialization."""
        progress = UploadProgress([sample_sequence])
        
        assert progress.total_files == 1
        assert progress.total_size == len(b"test content")
        assert progress.total_parts == 1
        assert progress.completed_parts == 0
        assert progress.completed_size == 0
        assert progress.overall_progress == 0.0
    
    def test_upload_progress_update(self, sample_sequence):
        """Test upload progress updates."""
        progress = UploadProgress([sample_sequence])
        
        # Create a mock part upload info
        part_info = Mock()
        part_info.file_info.relative_key = "test.txt"
        part_info.size = len(b"test content")
        part_info.status = "completed"
        
        progress.update_part_progress(part_info)
        
        assert progress.completed_parts == 1
        assert progress.completed_size == len(b"test content")
        assert progress.overall_progress == 100.0
    
    def test_upload_manager_context(self, mock_api_client):
        """Test upload manager async context manager."""
        # Test that UploadManager can be instantiated
        manager = UploadManager(mock_api_client)
        assert manager.api_client == mock_api_client
        
        # Test basic properties
        assert hasattr(manager, 'max_parallel')
        assert hasattr(manager, 'max_retries')


class TestFileUploadCommand:
    """Test file upload CLI command."""
    
    def test_upload_help(self, cli_runner):
        """Test upload command help."""
        result = cli_runner.invoke(cli, ['file', 'upload', '--help'])
        assert result.exit_code == 0
        assert 'Upload files to an asset' in result.output
        assert '--database' in result.output
        assert '--asset' in result.output
        assert '--directory' in result.output
        assert '--asset-preview' in result.output
        assert '--json-input' in result.output
        assert '--json-output' in result.output
    
    def test_upload_missing_required_args(self, cli_runner, file_command_mocks):
        """Test upload command with missing required arguments."""
        with file_command_mocks as mocks:
            # Missing database ID
            result = cli_runner.invoke(cli, ['file', 'upload'])
            assert result.exit_code == 2  # Click parameter error
            assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_upload_missing_files(self, cli_runner, file_command_mocks):
        """Test upload command with missing files."""
        with file_command_mocks as mocks:
            result = cli_runner.invoke(cli, ['file', 'upload', '-d', 'test-db', '-a', 'test-asset'])
            assert result.exit_code == 1
            assert 'Must specify files or directory' in result.output
    
    def test_upload_success_single_file(self, cli_runner, file_command_mocks):
        """Test successful single file upload."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': ''
                }
                
                # Mock asyncio.run to return success result
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output
                    assert 'Successful files: 1/1' in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_asset_preview_multiple_files(self, cli_runner, file_command_mocks):
        """Test asset preview upload with multiple files (should fail)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            file1 = Path(tmp_dir) / "file1.jpg"
            file2 = Path(tmp_dir) / "file2.jpg"
            file1.write_text("content1")
            file2.write_text("content2")
            
            with file_command_mocks as mocks:
                result = cli_runner.invoke(cli, [
                    'file', 'upload',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    '--asset-preview',
                    str(file1), str(file2)
                ])
                
                assert result.exit_code == 1
                assert 'single file' in result.output or 'Asset preview uploads support only a single file' in result.output
    
    def test_upload_directory_success(self, cli_runner, file_command_mocks):
        """Test successful directory upload."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create test files
            (Path(tmp_dir) / "file1.txt").write_text("content1")
            (Path(tmp_dir) / "file2.jpg").write_text("content2")
            
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': ''
                }
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 2,
                        "successful_files": 2,
                        "failed_files": 0,
                        "total_size": 16,
                        "total_size_formatted": "16B",
                        "upload_duration": 2.0,
                        "average_speed": 8.0,
                        "average_speed_formatted": "8B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--directory', tmp_dir
                    ])
                    
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output
                    assert 'Successful files: 2/2' in result.output
    
    def test_upload_no_setup(self, cli_runner, file_no_setup_mocks):
        """Test upload command without setup."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_no_setup_mocks as mocks:
                result = cli_runner.invoke(cli, [
                    'file', 'upload',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    tmp_path
                ])
                
                assert result.exit_code == 1
                # With new global exception handling, SetupRequiredError is raised
                assert isinstance(result.exception, Exception)
                assert 'Setup required' in str(result.exception)
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_authentication_error(self, cli_runner, file_command_mocks):
        """Test upload command with authentication error."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.side_effect = AuthenticationError("Authentication failed")
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 1
                    # With new global exception handling, AuthenticationError is raised
                    assert isinstance(result.exception, Exception)
                    assert 'Authentication failed' in str(result.exception)
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_large_file_async_handling_message(self, cli_runner, file_command_mocks):
        """Test upload command shows large file async handling message."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': ''
                }
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    # Mock result with large file async handling
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": [
                            {
                                "sequence_id": 1,
                                "completion_result": {
                                    "message": "Upload completed successfully",
                                    "overallSuccess": True,
                                    "largeFileAsynchronousHandling": True
                                }
                            }
                        ]
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 0
                    assert 'Upload completed successfully!' in result.output
                    assert 'Asynchronous Processing:' in result.output
                    assert 'large files that will undergo separate asynchronous processing' in result.output
                    assert 'may take longer to appear in the asset' in result.output
                    assert 'vamscli file list -d test-db -a test-asset' in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_no_large_file_async_handling_message(self, cli_runner, file_command_mocks):
        """Test upload command does not show large file message when not needed."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': ''
                }
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    # Mock result without large file async handling
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": [
                            {
                                "sequence_id": 1,
                                "completion_result": {
                                    "message": "Upload completed successfully",
                                    "overallSuccess": True,
                                    "largeFileAsynchronousHandling": False
                                }
                            }
                        ]
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 0
                    assert 'Upload completed successfully!' in result.output
                    assert 'Asynchronous Processing:' not in result.output
        finally:
            Path(tmp_path).unlink()


class TestFileUploadCommandJSONHandling:
    """Test JSON input/output handling for file upload commands."""
    
    def test_upload_json_input_string(self, cli_runner, file_command_mocks):
        """Test upload command with JSON input string."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            json_input = {
                "database_id": "test-db",
                "asset_id": "test-asset",
                "files": [tmp_path],
                "hide_progress": True
            }
            
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': ''
                }
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',  # Required even with JSON input
                        '-a', 'test-asset',  # Required even with JSON input
                        '--json-input', json.dumps(json_input),
                        '--json-output'
                    ])
                    
                    assert result.exit_code == 0
                    assert "overall_success" in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_json_input_from_file(self, cli_runner, file_command_mocks):
        """Test upload command with JSON input from file."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as content_file:
            content_file.write(b"test content")
            content_path = content_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as json_file:
            json_input = {
                "database_id": "test-db",
                "asset_id": "test-asset",
                "files": [content_path],
                "hide_progress": True
            }
            json.dump(json_input, json_file)
            json_path = json_file.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': ''
                }
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',  # Required even with JSON input
                        '-a', 'test-asset',  # Required even with JSON input
                        '--json-input', f'@{json_path}',
                        '--json-output'
                    ])
                    
                    assert result.exit_code == 0
                    assert "overall_success" in result.output
        finally:
            Path(content_path).unlink()
            Path(json_path).unlink()
    
    def test_upload_invalid_json_input(self, cli_runner, file_command_mocks):
        """Test upload command with invalid JSON input."""
        with file_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'file', 'upload',
                '-d', 'test-db',  # Required even with JSON input
                '-a', 'test-asset',  # Required even with JSON input
                '--json-input', '{"invalid": json}'
            ])
            
            assert result.exit_code == 1
            assert "Invalid JSON" in result.output
    
    def test_upload_json_output_format(self, cli_runner, file_command_mocks):
        """Test upload command with JSON output format."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': ''
                }
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    expected_result = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": []
                    }
                    mock_run.return_value = expected_result
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--json-output',
                        '--hide-progress',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 0
                    # Should output valid JSON
                    output_json = json.loads(result.output.strip())
                    assert output_json == expected_result
        finally:
            Path(tmp_path).unlink()


class TestFileUploadCommandEdgeCases:
    """Test edge cases and error handling for file upload commands."""
    
    def test_upload_partial_failure(self, cli_runner, file_command_mocks):
        """Test upload with partial failures."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": False,
                        "total_files": 2,
                        "successful_files": 1,
                        "failed_files": 1,
                        "total_size": 24,
                        "total_size_formatted": "24B",
                        "upload_duration": 2.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": [
                            {
                                "failed_files": ["failed_file.txt"]
                            }
                        ]
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    # After Rule 16 fix: Command returns result instead of sys.exit(1)
                    assert result.exit_code == 0  # Returns successfully with result
                    assert '⚠️  Upload completed with some failures' in result.output
                    assert 'Successful files: 1/2' in result.output
                    assert 'Failed files: 1' in result.output
                    assert 'failed_file.txt' in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_complete_failure(self, cli_runner, file_command_mocks):
        """Test upload with complete failure."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": False,
                        "total_files": 1,
                        "successful_files": 0,
                        "failed_files": 1,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 0.0,
                        "average_speed_formatted": "0B/s",
                        "sequence_results": [
                            {
                                "failed_files": ["test_file.txt"]
                            }
                        ]
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    # After Rule 16 fix: Command returns result instead of sys.exit(1)
                    assert result.exit_code == 0  # Returns successfully with result
                    # With new output format, failed uploads show results without success message
                    assert 'Successful files: 0/1' in result.output
                    assert 'Failed files: 1' in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_file_validation_error(self, cli_runner, file_command_mocks):
        """Test upload with file validation error."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                with patch('vamscli.commands.file.validate_file_for_upload') as mock_validate:
                    mock_validate.side_effect = InvalidFileError("Invalid file type")
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 1
                    assert 'Invalid file type' in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_api_error(self, cli_runner, file_command_mocks):
        """Test upload with API error."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.side_effect = APIError("API request failed")
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 1
                    # With new global exception handling, APIError is raised
                    assert isinstance(result.exception, Exception)
                    assert 'API request failed' in str(result.exception)
        finally:
            Path(tmp_path).unlink()


class TestBackendUploadRestrictions:
    """Test new backend upload restrictions (v2.2+)."""
    
    def test_create_sequences_with_many_files(self):
        """Test that many files automatically create multiple sequences."""
        # Create 200 small files
        files = []
        for i in range(200):
            files.append(FileInfo(f"/tmp/file{i}.txt", f"file{i}.txt", 1024))
        
        # Mock MAX_FILES_PER_REQUEST to be 50 for testing
        with patch('vamscli.utils.file_processor.MAX_FILES_PER_REQUEST', 50):
            sequences = create_upload_sequences(files)
            
            # Should create multiple sequences (at least 4 for 200 files with limit of 50)
            assert len(sequences) >= 4
            
            # Each sequence should respect the file limit
            for seq in sequences:
                assert len(seq.files) <= 50
            
            # Total files should still be 200
            total_files = sum(len(seq.files) for seq in sequences)
            assert total_files == 200
    
    def test_create_sequences_with_many_parts(self):
        """Test that files with many parts automatically create multiple sequences."""
        # Create files that would exceed parts limit in a single sequence
        files = []
        for i in range(10):  # 10 files, each with 2 parts = 20 parts total
            files.append(FileInfo(f"/tmp/file{i}.txt", f"file{i}.txt", 2 * DEFAULT_CHUNK_SIZE_SMALL))
        
        # Mock MAX_TOTAL_PARTS_PER_REQUEST to be 15 for testing
        with patch('vamscli.utils.file_processor.MAX_TOTAL_PARTS_PER_REQUEST', 15):
            sequences = create_upload_sequences(files)
            
            # Should create multiple sequences to respect parts limit
            assert len(sequences) >= 2
            
            # Each sequence should respect the parts limit
            for seq in sequences:
                assert seq.total_parts <= 15
            
            # Total files should still be 10
            total_files = sum(len(seq.files) for seq in sequences)
            assert total_files == 10
    
    def test_individual_file_too_many_parts(self):
        """Test validation with single file requiring too many parts."""
        # Create a file that would require more parts than allowed
        file_size_for_3_parts = 3 * DEFAULT_CHUNK_SIZE_SMALL
        files = [FileInfo("/tmp/large_file.txt", "large_file.txt", file_size_for_3_parts)]
        
        # Mock MAX_PARTS_PER_FILE to be smaller for testing
        with patch('vamscli.utils.file_processor.MAX_PARTS_PER_FILE', 2):
            # Should raise error during sequence creation (individual file validation)
            with pytest.raises(UploadSequenceError, match="requires .* parts"):
                create_upload_sequences(files)
    
    def test_create_sequences_valid_files(self):
        """Test sequence creation with valid files within limits."""
        # Create files within all limits
        files = []
        for i in range(10):  # Well under file limit
            files.append(FileInfo(f"/tmp/file{i}.txt", f"file{i}.txt", 1024 * 1024))  # 1MB each
        
        # Should create sequences successfully
        sequences = create_upload_sequences(files)
        
        # Should create at least one sequence
        assert len(sequences) >= 1
        
        # All files should be included
        total_files = sum(len(seq.files) for seq in sequences)
        assert total_files == 10
    
    def test_create_upload_sequences_respects_file_limit_per_sequence(self):
        """Test that upload sequences respect file count limits per sequence."""
        # Create files that would exceed file limit in a single sequence
        files = []
        for i in range(10):  # 10 files
            files.append(FileInfo(f"/tmp/file{i}.txt", f"file{i}.txt", 1024))
        
        # Mock MAX_FILES_PER_REQUEST to be 3 for testing
        with patch('vamscli.utils.file_processor.MAX_FILES_PER_REQUEST', 3):
            sequences = create_upload_sequences(files)
            
            # Should create multiple sequences
            assert len(sequences) >= 4  # At least 4 sequences for 10 files with limit of 3
            
            # Each sequence should respect the file limit
            for seq in sequences:
                assert len(seq.files) <= 3
    
    def test_create_upload_sequences_respects_parts_limit_per_sequence(self):
        """Test that upload sequences respect total parts limits per sequence."""
        # Create files that would exceed parts limit in a single sequence
        files = []
        for i in range(6):  # 6 files, each with 2 parts = 12 parts total
            files.append(FileInfo(f"/tmp/file{i}.txt", f"file{i}.txt", 2 * DEFAULT_CHUNK_SIZE_SMALL))
        
        # Mock MAX_TOTAL_PARTS_PER_REQUEST to be 5 for testing
        with patch('vamscli.utils.file_processor.MAX_TOTAL_PARTS_PER_REQUEST', 5):
            sequences = create_upload_sequences(files)
            
            # Should create multiple sequences
            assert len(sequences) >= 3  # At least 3 sequences for 12 parts with limit of 5
            
            # Each sequence should respect the parts limit
            for seq in sequences:
                assert seq.total_parts <= 5
    
    def test_zero_byte_file_handling(self):
        """Test zero-byte file handling in sequences."""
        files = [
            FileInfo("/tmp/empty.txt", "empty.txt", 0),  # Zero-byte file
            FileInfo("/tmp/normal.txt", "normal.txt", 1024),  # Normal file
        ]
        
        sequences = create_upload_sequences(files)
        
        # Should create sequences successfully
        assert len(sequences) >= 1
        
        # Check that zero-byte file is included
        all_files = []
        for seq in sequences:
            all_files.extend(seq.files)
        
        zero_byte_files = [f for f in all_files if f.size == 0]
        assert len(zero_byte_files) == 1
        assert zero_byte_files[0].relative_key == "empty.txt"


class TestFileUploadCommandNewRestrictions:
    """Test file upload command with new backend restrictions."""
    
    def test_upload_zero_byte_file_success(self, cli_runner, file_command_mocks):
        """Test successful upload of zero-byte files."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            # Create empty file
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 0,
                        "total_size_formatted": "0B",
                        "upload_duration": 1.0,
                        "average_speed": 0.0,
                        "average_speed_formatted": "0B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output
                    assert "Zero-byte files detected: 1 files" in result.output
                    assert "created during upload completion" in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_constraint_validation_message(self, cli_runner, file_command_mocks):
        """Test upload command constraint validation messages."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a small number of files for testing
            files = []
            for i in range(3):
                file_path = Path(tmp_dir) / f"file{i}.txt"
                file_path.write_text(f"content{i}")
                files.append(str(file_path))
            
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                # Mock the validation to raise an error
                with patch('vamscli.commands.file.create_upload_sequences') as mock_sequences:
                    mock_sequences.side_effect = UploadSequenceError("Too many files: 3 files provided, but maximum is 2 files per upload.")
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset'
                    ] + files)
                    
                    assert result.exit_code == 1
                    assert "Too many files" in result.output
                    # New format uses helpful_message instead of emoji tips
                    assert "You can split your upload" in result.output or "Upload Validation Error" in result.output


class TestFileUploadCommandIntegration:
    """Test integration scenarios for file upload commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_parameters(self, mock_main_profile_manager):
        """Test that upload commands require parameters where appropriate."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        runner = CliRunner()
        
        # Test upload without database ID
        result = runner.invoke(cli, ['file', 'upload'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing option' in result.output or 'required' in result.output.lower()
    
    def test_upload_with_custom_options(self, cli_runner, file_command_mocks):
        """Test upload with custom parallel uploads and retry options."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--parallel-uploads', '5',
                        '--retry-attempts', '3',
                        '--force-skip',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output
        finally:
            Path(tmp_path).unlink()


class TestRateLimitingCompatibility:
    """Test compatibility with 429 rate limiting."""
    
    def test_upload_handles_rate_limiting(self, cli_runner, file_command_mocks):
        """Test that upload command handles 429 rate limiting properly."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                # Mock the upload manager to simulate rate limiting during upload
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    # Simulate rate limiting being handled by the API client
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 5.0,  # Longer duration due to rate limiting
                        "average_speed": 2.4,
                        "average_speed_formatted": "2.4B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_rate_limit_exhausted(self, cli_runner, file_command_mocks):
        """Test upload command when rate limit retries are exhausted."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                from vamscli.utils.exceptions import RetryExhaustedError
                
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.side_effect = RetryExhaustedError(
                        "Rate limit exceeded. All 5 retry attempts exhausted."
                    )
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 1
                    # With new global exception handling, RetryExhaustedError is raised
                    assert isinstance(result.exception, Exception)
                    assert "Rate limit exceeded" in str(result.exception)
        finally:
            Path(tmp_path).unlink()


class TestParallelPipelineArchitecture:
    """Test parallel pipeline upload architecture."""
    
    def test_parallel_initialization_multiple_sequences(self):
        """Test that multiple sequences are initialized in parallel."""
        # Create 4 sequences worth of files
        files = []
        for i in range(200):
            files.append(FileInfo(f"/tmp/file{i}.txt", f"file{i}.txt", 1024))
        
        # Mock MAX_FILES_PER_REQUEST to create 4 sequences
        with patch('vamscli.utils.file_processor.MAX_FILES_PER_REQUEST', 50):
            sequences = create_upload_sequences(files)
            assert len(sequences) >= 4
            
            # Verify sequences were created correctly
            total_files = sum(len(seq.files) for seq in sequences)
            assert total_files == 200
    
    def test_global_part_upload_pool(self):
        """Test that parts from all sequences are uploaded in a shared pool."""
        # Create multiple sequences with parts
        files = []
        for i in range(10):
            # Each file has 2 parts
            files.append(FileInfo(f"/tmp/file{i}.txt", f"file{i}.txt", 2 * DEFAULT_CHUNK_SIZE_SMALL))
        
        # Mock to create 2 sequences
        with patch('vamscli.utils.file_processor.MAX_FILES_PER_REQUEST', 5):
            sequences = create_upload_sequences(files)
            assert len(sequences) >= 2
            
            # Calculate total parts across all sequences
            total_parts = sum(seq.total_parts for seq in sequences)
            assert total_parts == 20  # 10 files × 2 parts each
    
    def test_sequence_completion_monitoring(self):
        """Test that sequences can complete independently."""
        # Create files for multiple sequences
        files = [
            FileInfo("/tmp/file1.txt", "file1.txt", 1024),
            FileInfo("/tmp/file2.txt", "file2.txt", 1024),
            FileInfo("/tmp/file3.txt", "file3.txt", 1024),
        ]
        
        sequences = create_upload_sequences(files)
        
        # Each sequence should be independently completable
        for seq in sequences:
            assert seq.sequence_id > 0
            assert len(seq.files) > 0
            seq.calculate_parts()
            assert seq.total_parts >= 0


class TestZeroByteFileSupport:
    """Test zero-byte file support in upload functionality."""
    
    def test_calculate_parts_zero_byte_file(self):
        """Test part calculation for zero-byte files."""
        parts = calculate_file_parts(0)
        
        # Zero-byte files should have no parts (backend expects num_parts: 0)
        assert len(parts) == 0
    
    def test_upload_sequences_with_zero_byte_files(self):
        """Test upload sequence creation with zero-byte files."""
        files = [
            FileInfo("/tmp/empty1.txt", "empty1.txt", 0),
            FileInfo("/tmp/empty2.txt", "empty2.txt", 0),
            FileInfo("/tmp/normal.txt", "normal.txt", 1024),
        ]
        
        sequences = create_upload_sequences(files)
        
        # Should create sequences successfully
        assert len(sequences) >= 1
        
        # Verify all files are included
        all_files = []
        for seq in sequences:
            all_files.extend(seq.files)
        
        assert len(all_files) == 3
        
        # Check zero-byte files
        zero_byte_files = [f for f in all_files if f.size == 0]
        assert len(zero_byte_files) == 2
    
    def test_upload_zero_byte_file_display(self, cli_runner, file_command_mocks):
        """Test that zero-byte files are properly displayed in upload summary."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            # Create empty file
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                # Mock file collection to return zero-byte file
                with patch('vamscli.commands.file.collect_files_from_list') as mock_collect:
                    mock_collect.return_value = [FileInfo(tmp_path, "empty.txt", 0)]
                    
                    with patch('vamscli.commands.file.asyncio.run') as mock_run:
                        mock_run.return_value = {
                            "overall_success": True,
                            "total_files": 1,
                            "successful_files": 1,
                            "failed_files": 0,
                            "total_size": 0,
                            "total_size_formatted": "0B",
                            "upload_duration": 1.0,
                            "average_speed": 0.0,
                            "average_speed_formatted": "0B/s",
                            "sequence_results": []
                        }
                        
                        result = cli_runner.invoke(cli, [
                            'file', 'upload',
                            '-d', 'test-db',
                            '-a', 'test-asset',
                            tmp_path
                        ])
                        
                        assert result.exit_code == 0
                        assert "📄 Zero-byte files detected: 1 files" in result.output
                        assert "created during upload completion" in result.output
        finally:
            Path(tmp_path).unlink()
    


class TestFileExtensionValidation:
    """Test file extension validation for database restrictions."""
    
    def test_validate_extensions_no_restrictions(self):
        """Test validation with no restrictions."""
        files = [
            FileInfo("/tmp/file.glb", "file.glb", 1024),
            FileInfo("/tmp/file.txt", "file.txt", 1024),
        ]
        
        # Should not raise with empty restrictions
        from vamscli.utils.file_processor import validate_file_extensions
        validate_file_extensions(files, "")
        validate_file_extensions(files, None)
    
    def test_validate_extensions_all_wildcard(self):
        """Test validation with .all wildcard."""
        files = [
            FileInfo("/tmp/file.glb", "file.glb", 1024),
            FileInfo("/tmp/file.txt", "file.txt", 1024),
        ]
        
        from vamscli.utils.file_processor import validate_file_extensions
        
        # Should not raise with .all wildcard
        validate_file_extensions(files, ".all")
        validate_file_extensions(files, ".glb,.all,.txt")
    
    def test_validate_extensions_allowed(self):
        """Test validation with allowed extensions."""
        files = [
            FileInfo("/tmp/file.glb", "file.glb", 1024),
            FileInfo("/tmp/file.gltf", "file.gltf", 1024),
        ]
        
        from vamscli.utils.file_processor import validate_file_extensions
        
        # Should not raise for allowed extensions
        validate_file_extensions(files, ".glb,.gltf")
    
    def test_validate_extensions_disallowed(self):
        """Test validation with disallowed extensions."""
        files = [
            FileInfo("/tmp/file.txt", "file.txt", 1024),
        ]
        
        from vamscli.utils.file_processor import validate_file_extensions
        
        # Should raise for disallowed extension
        with pytest.raises(InvalidFileError, match="do not meet the restriction"):
            validate_file_extensions(files, ".glb,.gltf")
    
    def test_validate_extensions_multiple_violations(self):
        """Test validation with multiple disallowed files."""
        files = [
            FileInfo("/tmp/file.txt", "file.txt", 1024),
            FileInfo("/tmp/file.pdf", "file.pdf", 1024),
            FileInfo("/tmp/file.glb", "file.glb", 1024),  # This one is allowed
        ]
        
        from vamscli.utils.file_processor import validate_file_extensions
        
        # Should raise with detailed error message
        with pytest.raises(InvalidFileError) as exc_info:
            validate_file_extensions(files, ".glb,.gltf")
        
        error_message = str(exc_info.value)
        assert "Database has file extension restrictions: .glb,.gltf" in error_message
        assert "The following files do not meet the restriction:" in error_message
        assert "file.txt (extension: .txt)" in error_message
        assert "file.pdf (extension: .pdf)" in error_message
        # file.glb should NOT be in the error message
        assert "file.glb" not in error_message
    
    def test_validate_extensions_skip_preview_files(self):
        """Test that preview files are skipped."""
        files = [
            FileInfo("/tmp/preview.txt", "file.previewFile.txt", 1024),
            FileInfo("/tmp/file.glb", "file.glb", 1024),
        ]
        
        from vamscli.utils.file_processor import validate_file_extensions
        
        # Should not raise - preview file is skipped
        validate_file_extensions(files, ".glb")
    
    def test_validate_extensions_case_insensitive(self):
        """Test case-insensitive extension matching."""
        files = [
            FileInfo("/tmp/file.GLB", "file.GLB", 1024),
        ]
        
        from vamscli.utils.file_processor import validate_file_extensions
        
        # Should not raise - case insensitive
        validate_file_extensions(files, ".glb")
    
    def test_validate_extensions_with_spaces(self):
        """Test validation with spaces in extension list."""
        files = [
            FileInfo("/tmp/file.glb", "file.glb", 1024),
            FileInfo("/tmp/file.gltf", "file.gltf", 1024),
        ]
        
        from vamscli.utils.file_processor import validate_file_extensions
        
        # Should handle spaces in extension list
        validate_file_extensions(files, ".glb, .gltf, .obj")
    
    def test_upload_with_extension_restrictions(self, cli_runner, file_command_mocks):
        """Test upload command with database extension restrictions."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.glb') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config (no restrictions)
                mocks['api_client'].get_database.return_value = {'databaseId': 'test-db', 'restrictFileUploadsToExtensions': ''}
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    # Mock database config with restrictions
                    mocks['api_client'].get_database.return_value = {
                        'databaseId': 'test-db',
                        'restrictFileUploadsToExtensions': '.glb'
                    }
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output
                    
                    # Verify get_database was called
                    mocks['api_client'].get_database.assert_called_once_with('test-db')
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_with_extension_violation(self, cli_runner, file_command_mocks):
        """Test upload command with extension violation."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config with restrictions
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': '.glb'
                }
                
                result = cli_runner.invoke(cli, [
                    'file', 'upload',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    tmp_path
                ])
                
                assert result.exit_code == 1
                assert 'Database has file extension restrictions: .glb' in result.output
                assert 'not meet the restriction' in result.output
                assert '.txt' in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_with_multiple_extension_violations(self, cli_runner, file_command_mocks):
        """Test upload command with multiple extension violations."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create files with different extensions
            file1 = Path(tmp_dir) / "file1.txt"
            file2 = Path(tmp_dir) / "file2.pdf"
            file3 = Path(tmp_dir) / "file3.glb"  # This one is allowed
            
            file1.write_text("content1")
            file2.write_text("content2")
            file3.write_text("content3")
            
            with file_command_mocks as mocks:
                # Mock database config with restrictions
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': '.glb,.gltf'
                }
                
                result = cli_runner.invoke(cli, [
                    'file', 'upload',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    str(file1), str(file2), str(file3)
                ])
                
                assert result.exit_code == 1
                assert 'Database has file extension restrictions: .glb,.gltf' in result.output
                assert 'file1.txt (extension: .txt)' in result.output
                assert 'file2.pdf (extension: .pdf)' in result.output
                # file3.glb should NOT be in error message
                assert 'file3.glb' not in result.output or 'file3.glb (extension:' not in result.output
    
    def test_upload_with_no_restrictions(self, cli_runner, file_command_mocks):
        """Test upload command with no database restrictions."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config without restrictions
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': ''
                }
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_preview_skips_extension_validation(self, cli_runner, file_command_mocks):
        """Test that asset preview uploads skip extension validation."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config with restrictions that don't include .jpg
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': '.glb'
                }
                
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.return_value = {
                        "overall_success": True,
                        "total_files": 1,
                        "successful_files": 1,
                        "failed_files": 0,
                        "total_size": 12,
                        "total_size_formatted": "12B",
                        "upload_duration": 1.0,
                        "average_speed": 12.0,
                        "average_speed_formatted": "12B/s",
                        "sequence_results": []
                    }
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        '--asset-preview',
                        tmp_path
                    ])
                    
                    # Should succeed - preview files skip extension validation
                    assert result.exit_code == 0
                    assert '✅ Upload completed successfully!' in result.output
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_extension_validation_json_output(self, cli_runner, file_command_mocks):
        """Test extension validation error with JSON output."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                # Mock database config with restrictions
                mocks['api_client'].get_database.return_value = {
                    'databaseId': 'test-db',
                    'restrictFileUploadsToExtensions': '.glb'
                }
                
                result = cli_runner.invoke(cli, [
                    'file', 'upload',
                    '-d', 'test-db',
                    '-a', 'test-asset',
                    '--json-output',
                    tmp_path
                ])
                
                assert result.exit_code == 1
                # Should output JSON error format
                try:
                    output_json = json.loads(result.output.strip())
                    assert 'error' in output_json
                    assert 'Database has file extension restrictions' in output_json['error']
                except json.JSONDecodeError:
                    # If not JSON, at least check error message is present
                    assert 'Database has file extension restrictions' in result.output
        finally:
            Path(tmp_path).unlink()


if __name__ == '__main__':
    pytest.main([__file__])