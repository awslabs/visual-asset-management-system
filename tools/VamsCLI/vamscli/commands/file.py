"""File management commands for VamsCLI."""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.json_output import output_status, output_result, output_error
from ..utils.logging import log_debug
from ..utils.exceptions import (
    VamsCLIError, InvalidFileError, FileTooLargeError, PreviewFileError,
    UploadSequenceError, FileUploadError, FileNotFoundError, FileOperationError,
    InvalidPathError, FilePermissionError, FileAlreadyExistsError, 
    FileArchivedError, InvalidVersionError, AssetNotFoundError, InvalidAssetDataError,
    DatabaseNotFoundError
)
from ..utils.file_processor import (
    FileInfo, collect_files_from_directory, collect_files_from_list,
    create_upload_sequences, validate_file_for_upload, 
    validate_preview_files_have_base_files, get_upload_summary, format_file_size
)
from ..utils.upload_manager import UploadManager, UploadProgress, format_duration
from ..utils.api_client import APIClient
from ..utils.profile import ProfileManager
from ..constants import DEFAULT_PARALLEL_UPLOADS, DEFAULT_RETRY_ATTEMPTS


class ProgressDisplay:
    """Display upload progress in the terminal."""
    
    def __init__(self, hide_progress: bool = False, json_output: bool = False, total_sequences: int = 1):
        self.hide_progress = hide_progress or json_output  # Suppress progress in JSON mode
        self.json_output = json_output
        self.total_sequences = total_sequences
        self.last_update = 0
        self.update_interval = 0.5  # Update every 500ms
        
    def update(self, progress: UploadProgress):
        """Update the progress display."""
        if self.hide_progress:
            return
            
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
            
        self.last_update = current_time
        
        # Clear previous lines
        click.echo('\033[2K\033[1A' * 12, nl=False)  # Clear up to 12 lines
        
        # Sequence status (if multiple sequences)
        if self.total_sequences > 1:
            initialized = progress.initialized_sequences
            uploaded = progress.uploaded_sequences
            completed = progress.completed_sequences
            click.echo(f"\nSequences: {initialized} initialized, {uploaded} uploaded, {completed} completed, {self.total_sequences} total")
        
        # Overall progress
        overall_pct = progress.overall_progress
        completed_size_str = format_file_size(progress.completed_size)
        total_size_str = format_file_size(progress.total_size)
        
        # Progress bar
        bar_width = 40
        filled = int(bar_width * overall_pct / 100)
        bar = '█' * filled + '░' * (bar_width - filled)
        
        click.echo(f"\nOverall Progress: [{bar}] {overall_pct:.1f}% ({completed_size_str}/{total_size_str})")
        
        # Speed and ETA
        speed_str = format_file_size(int(progress.upload_speed)) + "/s"
        eta = progress.estimated_time_remaining
        eta_str = format_duration(eta) if eta else "calculating..."
        
        # Show throttling message if detected
        throttle_msg = ""
        if hasattr(progress, 'is_throttled') and progress.is_throttled:
            retry_count = getattr(progress, 'throttle_retry_count', 0)
            throttle_msg = f" | ⚠️ Processing times undergoing expected throttling (retry {retry_count}, 60s backoff)"
        
        click.echo(f"Speed: {speed_str} | Active: {progress.active_uploads} | ETA: {eta_str}{throttle_msg}")
        
        # File progress - show actively uploading files first, then pending
        # Sort by status priority: uploading > pending > completed > failed
        status_priority = {"uploading": 0, "pending": 1, "completed": 2, "failed": 3}
        sorted_files = sorted(
            progress.file_progress.items(),
            key=lambda x: (status_priority.get(x[1]["status"], 4), x[0])
        )
        
        files_shown = 0
        for file_key, file_progress in sorted_files:
            if files_shown >= 5:
                remaining = len(progress.file_progress) - files_shown
                if remaining > 0:
                    click.echo(f"... and {remaining} more files")
                break
                
            file_pct = (file_progress["completed_size"] / file_progress["total_size"]) * 100 if file_progress["total_size"] > 0 else 0
            status_icon = {
                "pending": "⏳",
                "uploading": "⬆️",
                "completed": "✅",
                "failed": "❌"
            }.get(file_progress["status"], "❓")
            
            # Truncate long filenames
            display_name = file_key
            if len(display_name) > 50:
                display_name = "..." + display_name[-47:]
                
            click.echo(f"  {status_icon} {display_name}: {file_pct:.1f}%")
            files_shown += 1


def parse_json_input(json_input: str) -> dict:
    """Parse JSON input from string or file."""
    # Handle None, empty string, or Click Sentinel objects
    if not json_input or (hasattr(json_input, '__class__') and 'Sentinel' in json_input.__class__.__name__):
        return {}
        
    # Check if it's a file path
    if json_input.startswith('@'):
        file_path = Path(json_input[1:])
        if not file_path.exists():
            raise click.ClickException(f"JSON input file not found: {file_path}")
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON in file {file_path}: {e}")
    else:
        # Direct JSON string
        try:
            return json.loads(json_input)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON input: {e}")


def validate_upload_args(database_id: str, asset_id: str, files_or_directory: tuple,
                        directory: str, asset_preview: bool, recursive: bool) -> tuple:
    """Validate and normalize upload arguments."""
    # Validate required arguments
    if not database_id:
        raise click.ClickException("Database ID is required (-d/--database)")
    if not asset_id:
        raise click.ClickException("Asset ID is required (-a/--asset)")
    
    # Determine file source
    if directory:
        if files_or_directory:
            raise click.ClickException("Cannot specify both directory and file arguments")
        file_source = ("directory", directory)
    elif files_or_directory:
        if len(files_or_directory) == 1 and Path(files_or_directory[0]).is_dir():
            file_source = ("directory", files_or_directory[0])
        else:
            file_source = ("files", list(files_or_directory))
    else:
        raise click.ClickException("Must specify files or directory to upload")
    
    # Validate asset preview constraints
    if asset_preview:
        if file_source[0] == "directory":
            raise click.ClickException("Asset preview uploads do not support directory uploads")
        if len(file_source[1]) > 1:
            raise click.ClickException("Asset preview uploads support only a single file")
        if recursive:
            raise click.ClickException("Recursive option not applicable for asset preview uploads")
    
    return file_source


@click.group()
def file():
    """File management commands."""
    pass


@file.command()
@click.argument('files_or_directory', nargs=-1, type=click.Path(exists=True))
@click.option('-d', '--database', 'database_id', required=True, 
              help='Database ID (required)')
@click.option('-a', '--asset', 'asset_id', required=True,
              help='Asset ID (required)')
@click.option('--directory', type=click.Path(exists=True, file_okay=False, dir_okay=True),
              help='Directory to upload')
@click.option('--asset-preview', is_flag=True,
              help='Upload as asset preview (single file only)')
@click.option('--asset-location', default='/',
              help='Base asset location (default: "/")')
@click.option('--recursive', is_flag=True,
              help='Include subdirectories when uploading directory')
@click.option('--parallel-uploads', type=int, default=DEFAULT_PARALLEL_UPLOADS,
              help=f'Max parallel uploads (default: {DEFAULT_PARALLEL_UPLOADS})')
@click.option('--retry-attempts', type=int, default=DEFAULT_RETRY_ATTEMPTS,
              help=f'Retry attempts per part (default: {DEFAULT_RETRY_ATTEMPTS})')
@click.option('--force-skip', is_flag=True,
              help='Auto-skip failed parts after retries')
@click.option('--json-input', 
              help='JSON input with all parameters (file path with @ prefix or JSON string)')
@click.option('--json-output', is_flag=True,
              help='Output API response as JSON')
@click.option('--hide-progress', is_flag=True,
              help='Hide upload progress display')
@click.pass_context
@requires_setup_and_auth
def upload(ctx: click.Context, files_or_directory, database_id, asset_id, directory, asset_preview,
           asset_location, recursive, parallel_uploads, retry_attempts, force_skip,
           json_input, json_output, hide_progress):
    """Upload files to an asset."""
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        if json_data:
            log_debug(f"Parsed JSON input with keys: {list(json_data.keys())}")
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        asset_preview = json_data.get('asset_preview', asset_preview)
        asset_location = json_data.get('asset_location', asset_location)
        recursive = json_data.get('recursive', recursive)
        parallel_uploads = json_data.get('parallel_uploads', parallel_uploads)
        retry_attempts = json_data.get('retry_attempts', retry_attempts)
        force_skip = json_data.get('force_skip', force_skip)
        hide_progress = json_data.get('hide_progress', hide_progress)
        
        # Handle files from JSON
        if 'files' in json_data:
            if files_or_directory or directory:
                raise click.ClickException("Cannot specify both JSON files and command line file arguments")
            files_or_directory = tuple(json_data['files'])
            log_debug(f"Using {len(files_or_directory)} files from JSON input")
        elif 'directory' in json_data:
            if files_or_directory or directory:
                raise click.ClickException("Cannot specify both JSON directory and command line arguments")
            directory = json_data['directory']
            log_debug(f"Using directory from JSON input: {directory}")
        
        # Auto-enable hide_progress when json_output is enabled
        if json_output:
            hide_progress = True
        
        # Validate arguments
        log_debug(f"Validating upload arguments: database_id={database_id}, asset_id={asset_id}, asset_preview={asset_preview}")
        file_source = validate_upload_args(
            database_id, asset_id, files_or_directory, directory, 
            asset_preview, recursive
        )
        log_debug(f"File source validated: type={file_source[0]}, source={file_source[1] if file_source[0] == 'directory' else f'{len(file_source[1])} files'}")
        
        # Collect files
        output_status("Collecting files...", json_output or hide_progress)
        log_debug(f"Collecting files from {file_source[0]}: {file_source[1] if file_source[0] == 'directory' else 'file list'}")
            
        if file_source[0] == "directory":
            files = collect_files_from_directory(
                Path(file_source[1]), recursive, asset_location
            )
            log_debug(f"Collected {len(files)} files from directory (recursive={recursive})")
        else:
            files = collect_files_from_list(file_source[1], asset_location)
            log_debug(f"Collected {len(files)} files from file list")
        
        # Validate files
        upload_type = "assetPreview" if asset_preview else "assetFile"
        log_debug(f"Validating {len(files)} files for upload type: {upload_type}")
        
        for file_info in files:
            validate_file_for_upload(file_info.local_path, upload_type, file_info.relative_key)
        
        log_debug(f"All {len(files)} files validated successfully")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_gateway_url = config['api_gateway_url']
        log_debug(f"Initializing API client with gateway: {api_gateway_url}")
        api_client = APIClient(api_gateway_url, profile_manager)
        
        # NEW: Fetch database configuration for extension restrictions
        output_status("Checking database upload restrictions...", json_output or hide_progress)
        log_debug(f"Fetching database configuration for: {database_id}")
        
        try:
            database_config = api_client.get_database(database_id)
            log_debug(f"Database config retrieved: {database_config.get('databaseId')}")
            
            # Check for file extension restrictions
            restricted_extensions = database_config.get('restrictFileUploadsToExtensions', '')
            
            if restricted_extensions and restricted_extensions.strip():
                log_debug(f"Database has file extension restrictions: {restricted_extensions}")
                output_status(f"Validating file extensions against database restrictions...", json_output or hide_progress)
                
                # Validate file extensions
                from ..utils.file_processor import validate_file_extensions
                validate_file_extensions(files, restricted_extensions, upload_type)
                
                log_debug(f"All {len(files)} files passed extension validation")
            else:
                log_debug("No file extension restrictions configured for this database")
                
        except DatabaseNotFoundError as e:
            output_error(e, json_output, error_type="Database Not Found")
            raise click.ClickException(str(e))
        except InvalidFileError as e:
            # Extension validation failed
            output_error(
                e,
                json_output,
                error_type="File Extension Validation Error",
                helpful_message=f"The database '{database_id}' restricts uploads to specific file types. "
                               f"Please check the allowed extensions and try again."
            )
            raise click.ClickException(str(e))
        
        # Create upload sequences with enhanced validation
        log_debug(f"Creating upload sequences from {len(files)} files")
        try:
            sequences = create_upload_sequences(files)
            summary = get_upload_summary(sequences)
            log_debug(f"Created {len(sequences)} upload sequences with {summary['total_parts']} total parts, {summary['total_size_formatted']} total size")
        except UploadSequenceError as e:
            # Provide helpful guidance for constraint violations
            error_msg = str(e)
            helpful_message = None
            
            # Only individual file part limit errors should occur now
            # (per-sequence limits are handled automatically by creating multiple sequences)
            if "requires" in error_msg and "parts" in error_msg:
                from ..constants import MAX_PARTS_PER_FILE
                helpful_message = f"Individual files cannot exceed {MAX_PARTS_PER_FILE} parts. Consider compressing very large files before upload."
            
            output_error(
                e,
                json_output,
                error_type="Upload Validation Error",
                helpful_message=helpful_message
            )
            raise click.ClickException(str(e))
        
        if not json_output and not hide_progress:
            click.echo(f"\nUpload Summary:")
            click.echo(f"  Files: {summary['total_files']} ({summary['regular_files']} regular, {summary['preview_files']} preview)")
            click.echo(f"  Total Size: {summary['total_size_formatted']}")
            click.echo(f"  Sequences: {summary['total_sequences']}")
            click.echo(f"  Parts: {summary['total_parts']}")
            click.echo(f"  Upload Type: {upload_type}")
            
            # Show helpful info for multi-sequence uploads
            if summary['total_sequences'] > 1:
                click.echo(f"\n📋 Multi-sequence upload: Your files will be uploaded in {summary['total_sequences']} separate requests")
                click.echo(f"   This is handled automatically.")
            
            # Show zero-byte file info if any
            zero_byte_files = [f for f in files if f.size == 0]
            if zero_byte_files:
                click.echo(f"\n📄 Zero-byte files detected: {len(zero_byte_files)} files")
                click.echo(f"   These will be created during upload completion.")
            
            click.echo()
        
        # Run upload
        log_debug(f"Starting upload manager with {parallel_uploads} parallel uploads, {retry_attempts} retry attempts, force_skip={force_skip}")
        async def run_upload():
            progress_display = ProgressDisplay(hide_progress, json_output, total_sequences=len(sequences))
            
            async with UploadManager(
                api_client=api_client,
                max_parallel=parallel_uploads,
                max_retries=retry_attempts,
                force_skip=force_skip,
                progress_callback=progress_display.update
            ) as upload_manager:
                
                return await upload_manager.upload_all_sequences(
                    sequences, database_id, asset_id, upload_type
                )
        
        # Execute upload
        output_status("Starting upload...", json_output or hide_progress)
        log_debug("Executing async upload operation")
            
        result = asyncio.run(run_upload())
        
        log_debug(f"Upload completed: {result['successful_files']}/{result['total_files']} files successful, duration={result.get('upload_duration', 0):.2f}s")
        
        # Create a clean, JSON-serializable result when json_output is enabled
        if json_output:
            # Extract only serializable data
            clean_result = {
                'overall_success': result.get('overall_success', False),
                'total_files': result.get('total_files', 0),
                'successful_files': result.get('successful_files', 0),
                'failed_files': result.get('failed_files', 0),
                'total_size': result.get('total_size', 0),
                'total_size_formatted': result.get('total_size_formatted', '0 B'),
                'upload_duration': result.get('upload_duration', 0),
                'average_speed': result.get('average_speed', 0),
                'average_speed_formatted': result.get('average_speed_formatted', '0 B/s')
            }
            
            # Add sequence results without progress objects
            if 'sequence_results' in result:
                clean_sequence_results = []
                for seq_result in result['sequence_results']:
                    clean_seq = {
                        'sequence_number': seq_result.get('sequence_number'),
                        'successful_files': seq_result.get('successful_files', 0),
                        'failed_files': seq_result.get('failed_files', [])
                    }
                    if 'completion_result' in seq_result:
                        clean_seq['completion_result'] = seq_result['completion_result']
                    clean_sequence_results.append(clean_seq)
                clean_result['sequence_results'] = clean_sequence_results
            
            result = clean_result
        
        # Clear progress display (only in CLI mode)
        if not json_output and not hide_progress:
            click.echo('\033[2K\033[1A' * 10, nl=False)  # Clear progress lines
        
        # Check for asynchronous processing across all completion results
        has_async_processing = False
        async_processing_note = None
        for seq_result in result.get("sequence_results", []):
            completion_result = seq_result.get("completion_result")
            if completion_result:
                # Check for large file async handling (backend-initiated)
                if completion_result.get("largeFileAsynchronousHandling"):
                    has_async_processing = True
                    log_debug("Large file asynchronous handling detected in upload result")
                    break
                # Check for 503-triggered async processing (throttling-initiated)
                elif completion_result.get("asynchronousProcessing"):
                    has_async_processing = True
                    async_processing_note = completion_result.get("note")
                    log_debug("503 asynchronous processing detected in upload result")
                    break
        
        # Format upload result
        def format_upload_result(data):
            """Format upload result for CLI display."""
            lines = []
            
            # Show asynchronous processing message if detected
            if has_async_processing:
                lines.append("\n📋 Asynchronous Processing:")
                if async_processing_note:
                    # 503-triggered async processing with custom note
                    lines.append(f"   {async_processing_note}")
                else:
                    # Large file async handling
                    lines.append("   Your upload contains large files that will undergo separate asynchronous processing.")
                    lines.append("   This may take some time, so files may take longer to appear in the asset.")
                lines.append(f"   You can check the asset files later using: vamscli file list -d {database_id} -a {asset_id}")
                lines.append("")
            
            lines.append("Results:")
            lines.append(f"  Successful files: {data['successful_files']}/{data['total_files']}")
            if data["failed_files"] > 0:
                lines.append(f"  Failed files: {data['failed_files']}")
            lines.append(f"  Total size: {data['total_size_formatted']}")
            lines.append(f"  Duration: {format_duration(data['upload_duration'])}")
            lines.append(f"  Average speed: {data['average_speed_formatted']}")
            
            # Show failed files if any
            failed_files = []
            for seq_result in data["sequence_results"]:
                failed_files.extend(seq_result.get("failed_files", []))
            
            if failed_files:
                lines.append("\nFailed files:")
                for failed_file in failed_files:
                    lines.append(f"  - {failed_file}")
            
            return '\n'.join(lines)
        
        # Determine success message
        if result["overall_success"]:
            success_msg = "✅ Upload completed successfully!"
        elif result["successful_files"] > 0:
            success_msg = "⚠️  Upload completed with some failures"
        else:
            success_msg = None  # Will show as error
        
        output_result(
            result,
            json_output,
            success_message=success_msg,
            cli_formatter=format_upload_result
        )
        
        # Return result for programmatic use (Rule 16)
        log_debug("Upload command completed successfully")
        return result
            
    except (InvalidFileError, FileTooLargeError, PreviewFileError, 
            UploadSequenceError, FileUploadError) as e:
        # Only handle file-specific business logic errors
        log_debug(f"File upload error caught: {type(e).__name__}: {str(e)}")
        output_error(e, json_output, error_type="File Upload Error")
        raise click.ClickException(str(e))


@file.command('create-folder')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('-p', '--path', 'folder_path', required=True, help='Folder path to create (must end with /)')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def create_folder(ctx: click.Context, database_id: str, asset_id: str, folder_path: str, json_input: str, json_output: bool):
    """Create a folder in an asset."""
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        folder_path = json_data.get('folder_path', folder_path)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not folder_path:
            raise click.ClickException("Folder path is required (-p/--path)")
        
        # Ensure folder path ends with /
        if not folder_path.endswith('/'):
            folder_path += '/'
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        output_status(f"Creating folder '{folder_path}'...", json_output)
        
        # Call API
        result = api_client.create_folder(database_id, asset_id, {"relativeKey": folder_path})
        
        def format_folder_result(data):
            """Format folder creation result for CLI display."""
            return f"  Path: {folder_path}"
        
        output_result(
            result,
            json_output,
            success_message="✓ Folder created successfully!",
            cli_formatter=format_folder_result
        )
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError, InvalidPathError, FileAlreadyExistsError) as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="Folder Creation Error")
        raise click.ClickException(str(e))


@file.command('list')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--prefix', help='Filter files by prefix')
@click.option('--include-archived', is_flag=True, help='Include archived files')
@click.option('--basic', is_flag=True, help='Skip expensive lookups for faster listing')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def list_files(ctx: click.Context, database_id: str, asset_id: str, prefix: str, include_archived: bool,
               basic: bool, page_size: int, max_items: int, starting_token: str, auto_paginate: bool,
               json_input: str, json_output: bool):
    """List files in an asset.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli file list -d my-db -a my-asset
        
        # Fast listing with basic mode
        vamscli file list -d my-db -a my-asset --basic
        
        # Auto-pagination to fetch all items (default: up to 10,000)
        vamscli file list -d my-db -a my-asset --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli file list -d my-db -a my-asset --auto-paginate --max-items 5000
        
        # Auto-pagination with custom page size
        vamscli file list -d my-db -a my-asset --auto-paginate --page-size 500
        
        # Manual pagination with page size
        vamscli file list -d my-db -a my-asset --page-size 200
        vamscli file list -d my-db -a my-asset --starting-token "token123" --page-size 200
        
        # With filters
        vamscli file list -d my-db -a my-asset --prefix "models/" --include-archived
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        prefix = json_data.get('prefix', prefix)
        include_archived = json_data.get('include_archived', include_archived)
        basic = json_data.get('basic', basic)
        page_size = json_data.get('page_size', page_size)
        max_items = json_data.get('max_items', max_items)
        starting_token = json_data.get('starting_token', starting_token)
        auto_paginate = json_data.get('auto_paginate', auto_paginate)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        
        # Validate pagination options
        if auto_paginate and starting_token:
            raise click.ClickException(
                "Cannot use --auto-paginate with --starting-token. "
                "Use --auto-paginate for automatic pagination, or --starting-token for manual pagination."
            )
        
        # Warn if max-items used without auto-paginate
        if max_items and not auto_paginate:
            output_status("Warning: --max-items only applies with --auto-paginate. Ignoring --max-items.", json_output)
            max_items = None
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        if auto_paginate:
            # Auto-pagination mode: fetch all items up to max_items (default 10,000)
            max_total_items = max_items or 10000
            output_status(f"Retrieving files (auto-paginating up to {max_total_items} items)...", json_output)
            
            all_items = []
            next_token = None
            total_fetched = 0
            page_count = 0
            
            while True:
                page_count += 1
                
                # Prepare query parameters for this page
                params = {}
                if prefix:
                    params['prefix'] = prefix
                if include_archived:
                    params['includeArchived'] = 'true'
                if basic:
                    params['basic'] = 'true'
                if page_size:
                    params['pageSize'] = page_size  # Pass pageSize to API
                if next_token:
                    params['startingToken'] = next_token
                
                # Note: maxItems is NOT passed to API - it's CLI-side limit only
                
                # Make API call
                result = api_client.list_asset_files(database_id, asset_id, params)
                
                # Aggregate items
                items = result.get('items', [])
                all_items.extend(items)
                total_fetched += len(items)
                
                # Show progress in CLI mode
                if not json_output:
                    output_status(f"Fetched {total_fetched} files (page {page_count})...", False)
                
                # Check if we should continue
                next_token = result.get('NextToken')
                if not next_token or total_fetched >= max_total_items:
                    break
            
            # Create final result
            final_result = {
                'items': all_items,
                'totalItems': len(all_items),
                'autoPaginated': True,
                'pageCount': page_count
            }
            
            if total_fetched >= max_total_items and next_token:
                final_result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
            
        else:
            # Manual pagination mode: single API call
            output_status("Retrieving files...", json_output)
            
            # Prepare query parameters
            params = {}
            if prefix:
                params['prefix'] = prefix
            if include_archived:
                params['includeArchived'] = 'true'
            if basic:
                params['basic'] = 'true'
            if page_size:
                params['pageSize'] = page_size  # Pass pageSize to API
            if starting_token:
                params['startingToken'] = starting_token
            
            # Note: maxItems is NOT passed to API in manual mode
            
            # Call API
            final_result = api_client.list_asset_files(database_id, asset_id, params)
        
        # Format output
        def format_files_list(data):
            """Format files list for CLI display."""
            items = data.get('items', [])
            if not items:
                return "No files found."
            
            lines = []
            
            # Show auto-pagination info if present
            if data.get('autoPaginated'):
                lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
                if data.get('note'):
                    lines.append(f"⚠️  {data['note']}")
                lines.append("")
            
            lines.append(f"Found {len(items)} file(s):")
            lines.append("")
            
            for item in items:
                file_type = "📁" if item.get('isFolder') else "📄"
                archived = " (archived)" if item.get('isArchived') else ""
                size_info = f" ({item.get('size', 0)} bytes)" if not item.get('isFolder') else ""
                primary_type = f" [{item.get('primaryType')}]" if item.get('primaryType') else ""
                
                lines.append(f"  {file_type} {item.get('relativePath', '')}{size_info}{primary_type}{archived}")
            
            # Show nextToken for manual pagination
            if not data.get('autoPaginated') and data.get('NextToken'):
                lines.append(f"\nNext token: {data['NextToken']}")
                lines.append("Use --starting-token to get the next page")
            
            return '\n'.join(lines)
        
        output_result(final_result, json_output, cli_formatter=format_files_list)
        
        return final_result
        
    except AssetNotFoundError as e:
        # Only handle command-specific business logic errors
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message="Use 'vamscli assets list' to see available assets."
        )
        raise click.ClickException(str(e))


@file.command('revert')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('-p', '--path', 'file_path', required=True, help='File path to revert')
@click.option('-v', '--version', 'version_id', required=True, help='Version ID to revert to')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def revert_file(ctx: click.Context, database_id: str, asset_id: str, file_path: str, version_id: str, json_input: str, json_output: bool):
    """Revert a file to a previous version.
    
    Examples:
        # Revert a file to a specific version
        vamscli file revert -d my-db -a my-asset -p "/file.gltf" -v "version-id-123"
        
        # Revert with JSON input
        vamscli file revert --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf", "version_id": "version-123"}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        file_path = json_data.get('file_path', file_path)
        version_id = json_data.get('version_id', version_id)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not file_path:
            raise click.ClickException("File path is required (-p/--path)")
        if not version_id:
            raise click.ClickException("Version ID is required (-v/--version)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        output_status(f"Reverting file '{file_path}'...", json_output)
        
        # Call API
        result = api_client.revert_file_version(database_id, asset_id, version_id, {
            "filePath": file_path
        })
        
        def format_revert_result(data):
            """Format revert result for CLI display."""
            lines = []
            lines.append(f"  File: {file_path}")
            lines.append(f"  Reverted from version: {data.get('revertedFromVersionId', version_id)}")
            if data.get('newVersionId'):
                lines.append(f"  New version ID: {data.get('newVersionId')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ File reverted successfully!",
            cli_formatter=format_revert_result
        )
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError, InvalidVersionError) as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="File Revert Error")
        raise click.ClickException(str(e))


@file.command('set-primary')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('-p', '--path', 'file_path', required=True, help='File path to set primary type for')
@click.option('--type', 'primary_type', required=True, 
              type=click.Choice(['', 'primary', 'lod1', 'lod2', 'lod3', 'lod4', 'lod5', 'other']),
              help='Primary type (empty string to remove)')
@click.option('--type-other', help='Custom primary type when type is "other"')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def set_primary_file(ctx: click.Context, database_id: str, asset_id: str, file_path: str, primary_type: str, type_other: str, json_input: str, json_output: bool):
    """Set or remove primary type metadata for a file.
    
    Examples:
        # Set primary type
        vamscli file set-primary -d my-db -a my-asset -p "/file.gltf" --type "primary"
        
        # Set custom primary type
        vamscli file set-primary -d my-db -a my-asset -p "/file.gltf" --type "other" --type-other "custom-type"
        
        # Remove primary type
        vamscli file set-primary -d my-db -a my-asset -p "/file.gltf" --type ""
        
        # Set with JSON input
        vamscli file set-primary --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf", "primary_type": "primary"}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        file_path = json_data.get('file_path', file_path)
        primary_type = json_data.get('primary_type', primary_type)
        type_other = json_data.get('type_other', type_other)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not file_path:
            raise click.ClickException("File path is required (-p/--path)")
        if primary_type is None:
            raise click.ClickException("Primary type is required (--type)")
        
        # Validate type-other requirement
        if primary_type == 'other' and not type_other:
            raise click.ClickException("Custom type is required when using --type other (--type-other)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        # Prepare request data
        primary_data = {
            "filePath": file_path,
            "primaryType": primary_type
        }
        if primary_type == 'other' and type_other:
            primary_data["primaryTypeOther"] = type_other
        
        output_status(f"Setting primary type for '{file_path}'...", json_output)
        
        # Call API
        result = api_client.set_primary_file(database_id, asset_id, primary_data)
        
        def format_primary_result(data):
            """Format primary type result for CLI display."""
            lines = []
            if primary_type == '':
                lines.append(f"  File: {file_path}")
                lines.append("  Primary type removed")
            else:
                final_type = type_other if primary_type == 'other' else primary_type
                lines.append(f"  File: {file_path}")
                lines.append(f"  Type: {final_type}")
            return '\n'.join(lines)
        
        success_msg = "✓ Primary type removed successfully!" if primary_type == '' else "✓ Primary type set successfully!"
        
        output_result(
            result,
            json_output,
            success_message=success_msg,
            cli_formatter=format_primary_result
        )
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="Primary Type Error")
        raise click.ClickException(str(e))


@file.command('delete-preview')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def delete_asset_preview(ctx: click.Context, database_id: str, asset_id: str, json_input: str, json_output: bool):
    """Delete the asset preview file.
    
    Examples:
        # Delete asset preview
        vamscli file delete-preview -d my-db -a my-asset
        
        # Delete with JSON input
        vamscli file delete-preview --json-input '{"database_id": "my-db", "asset_id": "my-asset"}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        output_status(f"Deleting asset preview for '{asset_id}'...", json_output)
        
        # Call API
        result = api_client.delete_asset_preview(database_id, asset_id)
        
        def format_preview_result(data):
            """Format preview deletion result for CLI display."""
            return f"  Asset: {asset_id}"
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset preview deleted successfully!",
            cli_formatter=format_preview_result
        )
        
        return result
        
    except AssetNotFoundError as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="Asset Preview Deletion Error")
        raise click.ClickException(str(e))


@file.command('delete-auxiliary')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('-p', '--path', 'file_path', required=True, help='File path prefix for auxiliary files to delete')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def delete_auxiliary_files(ctx: click.Context, database_id: str, asset_id: str, file_path: str, json_input: str, json_output: bool):
    """Delete auxiliary preview asset files.
    
    Examples:
        # Delete auxiliary files for a specific path
        vamscli file delete-auxiliary -d my-db -a my-asset -p "/file.gltf"
        
        # Delete with JSON input
        vamscli file delete-auxiliary --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf"}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        file_path = json_data.get('file_path', file_path)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not file_path:
            raise click.ClickException("File path is required (-p/--path)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        output_status(f"Deleting auxiliary files for '{file_path}'...", json_output)
        
        # Call API
        result = api_client.delete_auxiliary_preview_files(database_id, asset_id, {
            "filePath": file_path
        })
        
        def format_auxiliary_result(data):
            """Format auxiliary deletion result for CLI display."""
            lines = []
            lines.append(f"  Path prefix: {file_path}")
            if data.get('deletedCount'):
                lines.append(f"  Files deleted: {data.get('deletedCount')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Auxiliary preview files deleted successfully!",
            cli_formatter=format_auxiliary_result
        )
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="Auxiliary Files Deletion Error")
        raise click.ClickException(str(e))


@file.command('info')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('-p', '--path', 'file_path', required=True, help='File path to get info for')
@click.option('--include-versions', is_flag=True, help='Include version history')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def file_info(ctx: click.Context, database_id: str, asset_id: str, file_path: str, include_versions: bool, json_input: str, json_output: bool):
    """Get detailed information about a specific file.
    
    Examples:
        # Get file info
        vamscli file info -d my-db -a my-asset -p "/model.gltf"
        
        # Get file info with version history
        vamscli file info -d my-db -a my-asset -p "/model.gltf" --include-versions
        
        # Get info with JSON input
        vamscli file info --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/model.gltf"}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        file_path = json_data.get('file_path', file_path)
        include_versions = json_data.get('include_versions', include_versions)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not file_path:
            raise click.ClickException("File path is required (-p/--path)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        # Prepare query parameters
        params = {
            'filePath': file_path,
            'includeVersions': 'true' if include_versions else 'false'
        }
        
        output_status(f"Retrieving file information for '{file_path}'...", json_output)
        
        # Call API
        result = api_client.get_file_info(database_id, asset_id, params)
        
        def format_file_info(data):
            """Format file info for CLI display."""
            lines = []
            lines.append(f"\nFile: {data.get('fileName', 'N/A')}")
            lines.append(f"Path: {data.get('relativePath', 'N/A')}")
            lines.append(f"Type: {'Folder' if data.get('isFolder') else 'File'}")
            
            if not data.get('isFolder'):
                lines.append(f"Size: {data.get('size', 0)} bytes")
                lines.append(f"Content Type: {data.get('contentType', 'N/A')}")
                if data.get('primaryType'):
                    lines.append(f"Primary Type: {data.get('primaryType')}")
            
            lines.append(f"Last Modified: {data.get('lastModified', 'N/A')}")
            lines.append(f"Storage Class: {data.get('storageClass', 'N/A')}")
            lines.append(f"Archived: {'Yes' if data.get('isArchived') else 'No'}")
            
            if data.get('previewFile'):
                lines.append(f"Preview File: {data.get('previewFile')}")
            
            if include_versions and data.get('versions'):
                lines.append(f"\nVersions ({len(data['versions'])}):")
                for version in data['versions']:
                    status = "Current" if version.get('isLatest') else "Previous"
                    archived = " (archived)" if version.get('isArchived') else ""
                    lines.append(f"  {version.get('versionId', 'N/A')} - {status}{archived}")
                    lines.append(f"    Modified: {version.get('lastModified', 'N/A')}")
                    if not data.get('isFolder'):
                        lines.append(f"    Size: {version.get('size', 0)} bytes")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ File information retrieved",
            cli_formatter=format_file_info
        )
        
        return result
        
    except AssetNotFoundError as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="File Info Error")
        raise click.ClickException(str(e))


@file.command('move')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--source', required=True, help='Source file path')
@click.option('--dest', required=True, help='Destination file path')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def move_file(ctx: click.Context, database_id: str, asset_id: str, source: str, dest: str, json_input: str, json_output: bool):
    """Move a file within an asset.
    
    Examples:
        # Move a file
        vamscli file move -d my-db -a my-asset --source "/old/path.gltf" --dest "/new/path.gltf"
        
        # Move with JSON input
        vamscli file move --json-input '{"database_id": "my-db", "asset_id": "my-asset", "source": "/old.gltf", "dest": "/new.gltf"}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        source = json_data.get('source', source)
        dest = json_data.get('dest', dest)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not source:
            raise click.ClickException("Source path is required (--source)")
        if not dest:
            raise click.ClickException("Destination path is required (--dest)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        output_status(f"Moving file from '{source}' to '{dest}'...", json_output)
        
        # Call API
        result = api_client.move_file(database_id, asset_id, {
            "sourcePath": source,
            "destinationPath": dest
        })
        
        def format_move_result(data):
            """Format move result for CLI display."""
            lines = []
            lines.append(f"  From: {source}")
            lines.append(f"  To: {dest}")
            
            if data.get('affectedFiles'):
                affected_count = len(data['affectedFiles'])
                if affected_count > 2:  # More than just source and dest
                    lines.append(f"  Additional files affected: {affected_count - 2}")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ File moved successfully!",
            cli_formatter=format_move_result
        )
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError, InvalidPathError, FileAlreadyExistsError) as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="File Move Error")
        raise click.ClickException(str(e))


@file.command('copy')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--source', required=True, help='Source file path')
@click.option('--dest', required=True, help='Destination file path')
@click.option('--dest-asset', help='Destination asset ID (for cross-asset copy)')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def copy_file(ctx: click.Context, database_id: str, asset_id: str, source: str, dest: str, dest_asset: str, json_input: str, json_output: bool):
    """Copy a file within an asset or to another asset.
    
    Examples:
        # Copy within same asset
        vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/copy.gltf"
        
        # Copy to another asset
        vamscli file copy -d my-db -a my-asset --source "/file.gltf" --dest "/file.gltf" --dest-asset other-asset
        
        # Copy with JSON input
        vamscli file copy --json-input '{"database_id": "my-db", "asset_id": "my-asset", "source": "/file.gltf", "dest": "/copy.gltf"}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        source = json_data.get('source', source)
        dest = json_data.get('dest', dest)
        dest_asset = json_data.get('dest_asset', dest_asset)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not source:
            raise click.ClickException("Source path is required (--source)")
        if not dest:
            raise click.ClickException("Destination path is required (--dest)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        # Prepare request data
        copy_data = {
            "sourcePath": source,
            "destinationPath": dest
        }
        if dest_asset:
            copy_data["destinationAssetId"] = dest_asset
        
        output_status(f"Copying file from '{source}' to '{dest}'...", json_output)
        
        # Call API
        result = api_client.copy_file(database_id, asset_id, copy_data)
        
        def format_copy_result(data):
            """Format copy result for CLI display."""
            lines = []
            lines.append(f"  From: {source}")
            lines.append(f"  To: {dest}")
            if dest_asset:
                lines.append(f"  Destination Asset: {dest_asset}")
            
            if data.get('affectedFiles'):
                affected_count = len(data['affectedFiles'])
                if affected_count > 1:  # More than just the main file
                    lines.append(f"  Additional files copied: {affected_count - 1}")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ File copied successfully!",
            cli_formatter=format_copy_result
        )
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError, InvalidPathError, FileAlreadyExistsError) as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="File Copy Error")
        raise click.ClickException(str(e))


@file.command('archive')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('-p', '--path', 'file_path', required=True, help='File path to archive')
@click.option('--prefix', is_flag=True, help='Archive all files under the path as a prefix')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def archive_file(ctx: click.Context, database_id: str, asset_id: str, file_path: str, prefix: bool, json_input: str, json_output: bool):
    """Archive a file or files under a prefix (soft delete).
    
    Examples:
        # Archive a single file
        vamscli file archive -d my-db -a my-asset -p "/file.gltf"
        
        # Archive all files under a prefix
        vamscli file archive -d my-db -a my-asset -p "/folder/" --prefix
        
        # Archive with JSON input
        vamscli file archive --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf"}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        file_path = json_data.get('file_path', file_path)
        prefix = json_data.get('prefix', prefix)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not file_path:
            raise click.ClickException("File path is required (-p/--path)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        output_status(f"Archiving file(s) at '{file_path}'...", json_output)
        
        # Call API
        result = api_client.archive_file(database_id, asset_id, {
            "filePath": file_path,
            "isPrefix": prefix
        })
        
        def format_archive_result(data):
            """Format archive result for CLI display."""
            lines = []
            lines.append(f"  Path: {file_path}")
            if prefix:
                lines.append("  Operation: Archive all files under prefix")
            
            if data.get('affectedFiles'):
                affected_count = len(data['affectedFiles'])
                lines.append(f"  Files archived: {affected_count}")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ File(s) archived successfully!",
            cli_formatter=format_archive_result
        )
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="File Archive Error")
        raise click.ClickException(str(e))


@file.command('unarchive')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('-p', '--path', 'file_path', required=True, help='File path to unarchive')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def unarchive_file(ctx: click.Context, database_id: str, asset_id: str, file_path: str, json_input: str, json_output: bool):
    """Unarchive a previously archived file.
    
    Examples:
        # Unarchive a file
        vamscli file unarchive -d my-db -a my-asset -p "/file.gltf"
        
        # Unarchive with JSON input
        vamscli file unarchive --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf"}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        file_path = json_data.get('file_path', file_path)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not file_path:
            raise click.ClickException("File path is required (-p/--path)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        output_status(f"Unarchiving file '{file_path}'...", json_output)
        
        # Call API
        result = api_client.unarchive_file(database_id, asset_id, {
            "filePath": file_path
        })
        
        def format_unarchive_result(data):
            """Format unarchive result for CLI display."""
            lines = []
            lines.append(f"  Path: {file_path}")
            
            if data.get('affectedFiles'):
                affected_count = len(data['affectedFiles'])
                if affected_count > 1:
                    lines.append(f"  Additional files unarchived: {affected_count - 1}")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ File unarchived successfully!",
            cli_formatter=format_unarchive_result
        )
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="File Unarchive Error")
        raise click.ClickException(str(e))


@file.command('delete')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('-p', '--path', 'file_path', required=True, help='File path to delete')
@click.option('--prefix', is_flag=True, help='Delete all files under the path as a prefix')
@click.option('--confirm', is_flag=True, help='Confirm permanent deletion (required)')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def delete_file(ctx: click.Context, database_id: str, asset_id: str, file_path: str, prefix: bool, confirm: bool, json_input: str, json_output: bool):
    """Permanently delete a file or files under a prefix.
    
    Examples:
        # Delete a single file (requires confirmation)
        vamscli file delete -d my-db -a my-asset -p "/file.gltf" --confirm
        
        # Delete all files under a prefix
        vamscli file delete -d my-db -a my-asset -p "/folder/" --prefix --confirm
        
        # Delete with JSON input
        vamscli file delete --json-input '{"database_id": "my-db", "asset_id": "my-asset", "file_path": "/file.gltf", "confirm": true}'
    """
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        file_path = json_data.get('file_path', file_path)
        prefix = json_data.get('prefix', prefix)
        confirm = json_data.get('confirm', confirm)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        if not file_path:
            raise click.ClickException("File path is required (-p/--path)")
        
        # Require confirmation for deletion
        if not confirm:
            if json_output:
                # For JSON output, return error in JSON format
                import sys
                error_result = {
                    "error": "Confirmation required",
                    "message": "Permanent deletion requires the --confirm flag",
                    "databaseId": database_id,
                    "assetId": asset_id,
                    "filePath": file_path
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                # For CLI output, show helpful message
                raise click.ClickException("Permanent deletion requires confirmation (--confirm)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        output_status(f"Deleting file(s) at '{file_path}'...", json_output)
        
        # Call API
        result = api_client.delete_file(database_id, asset_id, {
            "filePath": file_path,
            "isPrefix": prefix,
            "confirmPermanentDelete": True
        })
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            lines = []
            lines.append(f"  Path: {file_path}")
            if prefix:
                lines.append("  Operation: Delete all files under prefix")
            
            if data.get('affectedFiles'):
                affected_count = len(data['affectedFiles'])
                lines.append(f"  Files deleted: {affected_count}")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ File(s) deleted permanently!",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        output_error(e, json_output, error_type="File Deletion Error")
        raise click.ClickException(str(e))