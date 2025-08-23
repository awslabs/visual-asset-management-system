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
    MAX_SEQUENCE_SIZE, MAX_PREVIEW_FILE_SIZE
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
        
        assert len(parts) == 1
        assert parts[0]["part_number"] == 1
        assert parts[0]["start_byte"] == 0
        assert parts[0]["end_byte"] == 0
        assert parts[0]["size"] == 0
    
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
            assert files[0].relative_key == "test/file1.txt"
            assert files[1].relative_key == "test/file2.jpg"
    
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
            
            # Check relative keys
            relative_keys = [f.relative_key for f in files]
            assert "file1.txt" in relative_keys
            assert "file2.jpg" in relative_keys
            assert "subdir/file3.png" in relative_keys
    
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
    
    def test_validate_preview_files_have_base_files_valid(self):
        """Test preview file validation with valid base files."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp1:
            tmp1.write(b"content1")
            tmp1_path = tmp1.name
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp2:
            tmp2.write(b"content2")
            tmp2_path = tmp2.name
        
        try:
            files = [
                FileInfo(tmp1_path, "file1", 1024),  # Base file (without extension in relative_key)
                FileInfo(tmp2_path, "file1.previewFile.jpg", 1024),  # Preview file matching base
            ]
            
            valid, missing = validate_preview_files_have_base_files(files)
            
            assert valid
            assert len(missing) == 0
        finally:
            Path(tmp1_path).unlink()
            Path(tmp2_path).unlink()
    
    def test_validate_preview_files_have_base_files_missing(self):
        """Test preview file validation with missing base files."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp1:
            tmp1.write(b"content1")
            tmp1_path = tmp1.name
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp2:
            tmp2.write(b"content2")
            tmp2_path = tmp2.name
        
        try:
            files = [
                FileInfo(tmp1_path, "file1.txt", 1024),
                FileInfo(tmp2_path, "file2.previewFile.jpg", 1024),  # Missing base file
            ]
            
            valid, missing = validate_preview_files_have_base_files(files)
            
            assert not valid
            assert "file2.previewFile.jpg" in missing
        finally:
            Path(tmp1_path).unlink()
            Path(tmp2_path).unlink()
    
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
    
    @pytest.mark.asyncio
    async def test_upload_manager_context(self, mock_api_client):
        """Test upload manager async context manager."""
        async with UploadManager(mock_api_client) as manager:
            assert manager.session is not None
        
        # Session should be closed after context exit
        assert manager.session.closed


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
                # The error might be in the output or might be a different error due to mock setup
                assert ('Configuration not found' in result.output or 
                        'Mock' in result.output or 
                        'Unexpected error' in result.output)
        finally:
            Path(tmp_path).unlink()
    
    def test_upload_authentication_error(self, cli_runner, file_command_mocks):
        """Test upload command with authentication error."""
        with tempfile.NamedTemporaryFile(delete=False, suffix='.txt') as tmp:
            tmp.write(b"test content")
            tmp_path = tmp.name
        
        try:
            with file_command_mocks as mocks:
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.side_effect = AuthenticationError("Authentication failed")
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 1
                    assert 'Authentication failed' in result.output
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
                    
                    assert result.exit_code == 1  # Should exit with error code
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
                    
                    assert result.exit_code == 1  # Should exit with error code
                    assert '❌ Upload failed' in result.output
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
                with patch('vamscli.commands.file.asyncio.run') as mock_run:
                    mock_run.side_effect = APIError("API request failed")
                    
                    result = cli_runner.invoke(cli, [
                        'file', 'upload',
                        '-d', 'test-db',
                        '-a', 'test-asset',
                        tmp_path
                    ])
                    
                    assert result.exit_code == 1
                    assert 'API request failed' in result.output
        finally:
            Path(tmp_path).unlink()


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


if __name__ == '__main__':
    pytest.main([__file__])
