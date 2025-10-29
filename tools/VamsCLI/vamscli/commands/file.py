"""File management commands for VamsCLI."""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.exceptions import (
    VamsCLIError, InvalidFileError, FileTooLargeError, PreviewFileError,
    UploadSequenceError, FileUploadError, FileNotFoundError, FileOperationError,
    InvalidPathError, FilePermissionError, FileAlreadyExistsError, 
    FileArchivedError, InvalidVersionError, AssetNotFoundError, InvalidAssetDataError
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
    
    def __init__(self, hide_progress: bool = False):
        self.hide_progress = hide_progress
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
        click.echo('\033[2K\033[1A' * 10, nl=False)  # Clear up to 10 lines
        
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
        
        click.echo(f"Speed: {speed_str} | Active: {progress.active_uploads} | ETA: {eta_str}")
        
        # File progress (show up to 5 files)
        files_shown = 0
        for file_key, file_progress in progress.file_progress.items():
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
    if not json_input:
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
        elif 'directory' in json_data:
            if files_or_directory or directory:
                raise click.ClickException("Cannot specify both JSON directory and command line arguments")
            directory = json_data['directory']
        
        # Validate arguments
        file_source = validate_upload_args(
            database_id, asset_id, files_or_directory, directory, 
            asset_preview, recursive
        )
        
        # Collect files
        if not hide_progress:
            click.echo("Collecting files...")
            
        if file_source[0] == "directory":
            files = collect_files_from_directory(
                Path(file_source[1]), recursive, asset_location
            )
        else:
            files = collect_files_from_list(file_source[1], asset_location)
        
        # Validate files
        upload_type = "assetPreview" if asset_preview else "assetFile"
        
        for file_info in files:
            validate_file_for_upload(file_info.local_path, upload_type, file_info.relative_key)
        
        # Create upload sequences with enhanced validation
        try:
            sequences = create_upload_sequences(files)
            summary = get_upload_summary(sequences)
        except UploadSequenceError as e:
            # Provide helpful guidance for constraint violations
            error_msg = str(e)
            if "Too many files" in error_msg:
                from ..constants import MAX_FILES_PER_REQUEST
                click.echo(f"❌ {error_msg}")
                click.echo(f"\n💡 Tip: You can split your upload by using multiple commands:")
                click.echo(f"   vamscli file upload -d {database_id} -a {asset_id} file1.ext file2.ext ... (up to {MAX_FILES_PER_REQUEST} files)")
                click.echo(f"   vamscli file upload -d {database_id} -a {asset_id} file{MAX_FILES_PER_REQUEST + 1}.ext ...")
            elif "Total parts across all files" in error_msg:
                from ..constants import MAX_TOTAL_PARTS_PER_REQUEST
                click.echo(f"❌ {error_msg}")
                click.echo(f"\n💡 Tip: Reduce the number of large files or split into smaller uploads.")
                click.echo(f"   Each upload can have at most {MAX_TOTAL_PARTS_PER_REQUEST} parts total.")
            elif "requires" in error_msg and "parts" in error_msg:
                from ..constants import MAX_PARTS_PER_FILE
                click.echo(f"❌ {error_msg}")
                click.echo(f"\n💡 Tip: Individual files cannot exceed {MAX_PARTS_PER_FILE} parts.")
                click.echo(f"   Consider compressing very large files before upload.")
            else:
                click.echo(f"❌ Upload validation failed: {error_msg}")
            sys.exit(1)
        
        if not hide_progress:
            click.echo(f"\nUpload Summary:")
            click.echo(f"  Files: {summary['total_files']} ({summary['regular_files']} regular, {summary['preview_files']} preview)")
            click.echo(f"  Total Size: {summary['total_size_formatted']}")
            click.echo(f"  Sequences: {summary['total_sequences']}")
            click.echo(f"  Parts: {summary['total_parts']}")
            click.echo(f"  Upload Type: {upload_type}")
            
            # Show helpful info for multi-sequence uploads
            if summary['total_sequences'] > 1:
                click.echo(f"\n📋 Multi-sequence upload: Your files will be uploaded in {summary['total_sequences']} separate requests")
                click.echo(f"   to comply with backend limits. This is handled automatically.")
            
            # Show zero-byte file info if any
            zero_byte_files = [f for f in files if f.size == 0]
            if zero_byte_files:
                click.echo(f"\n📄 Zero-byte files detected: {len(zero_byte_files)} files")
                click.echo(f"   These will be created during upload completion.")
            
            click.echo()
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        # Run upload
        async def run_upload():
            progress_display = ProgressDisplay(hide_progress)
            
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
        if not hide_progress:
            click.echo("Starting upload...")
            
        result = asyncio.run(run_upload())
        
        # Clear progress display
        if not hide_progress:
            click.echo('\033[2K\033[1A' * 10, nl=False)  # Clear progress lines
        
        # Check for large file asynchronous handling across all completion results
        has_large_file_async_handling = False
        for seq_result in result.get("sequence_results", []):
            completion_result = seq_result.get("completion_result")
            if completion_result and completion_result.get("largeFileAsynchronousHandling"):
                has_large_file_async_handling = True
                break
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            # Human-readable output
            if result["overall_success"]:
                click.echo(f"✅ Upload completed successfully!")
            elif result["successful_files"] > 0:
                click.echo(f"⚠️  Upload completed with some failures")
            else:
                click.echo(f"❌ Upload failed")
            
            # Show large file async handling message if detected
            if has_large_file_async_handling:
                click.echo(f"\n📋 Large File Processing:")
                click.echo(f"   Your upload contains large files that will undergo separate asynchronous processing.")
                click.echo(f"   This may take some time, so files may take longer to appear in the asset.")
                click.echo(f"   You can check the asset files later using: vamscli file list -d {database_id} -a {asset_id}")
            
            click.echo(f"\nResults:")
            click.echo(f"  Successful files: {result['successful_files']}/{result['total_files']}")
            if result["failed_files"] > 0:
                click.echo(f"  Failed files: {result['failed_files']}")
            click.echo(f"  Total size: {result['total_size_formatted']}")
            click.echo(f"  Duration: {format_duration(result['upload_duration'])}")
            click.echo(f"  Average speed: {result['average_speed_formatted']}")
            
            # Show failed files if any
            failed_files = []
            for seq_result in result["sequence_results"]:
                failed_files.extend(seq_result.get("failed_files", []))
            
            if failed_files:
                click.echo(f"\nFailed files:")
                for failed_file in failed_files:
                    click.echo(f"  - {failed_file}")
        
        # Exit with appropriate code
        if not result["overall_success"]:
            sys.exit(1)
        
        return result
            
    except (InvalidFileError, FileTooLargeError, PreviewFileError, 
            UploadSequenceError, FileUploadError) as e:
        # Only handle file-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(f"❌ {e}", err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.create_folder(database_id, asset_id, {"keyPath": folder_path})
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ Folder created successfully!", fg='green', bold=True))
            click.echo(f"Path: {folder_path}")
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError, InvalidPathError, FileAlreadyExistsError) as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


@file.command('list')
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--prefix', help='Filter files by prefix')
@click.option('--include-archived', is_flag=True, help='Include archived files')
@click.option('--max-items', type=int, default=1000, help='Maximum number of items to return')
@click.option('--page-size', type=int, default=100, help='Number of items per page')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def list_files(ctx: click.Context, database_id: str, asset_id: str, prefix: str, include_archived: bool,
               max_items: int, page_size: int, starting_token: str, json_input: str, json_output: bool):
    """List files in an asset."""
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database_id = json_data.get('database_id', database_id)
        asset_id = json_data.get('asset_id', asset_id)
        prefix = json_data.get('prefix', prefix)
        include_archived = json_data.get('include_archived', include_archived)
        max_items = json_data.get('max_items', max_items)
        page_size = json_data.get('page_size', page_size)
        starting_token = json_data.get('starting_token', starting_token)
        
        # Validate required arguments
        if not database_id:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset_id:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        # Prepare query parameters
        params = {
            'maxItems': max_items,
            'pageSize': page_size
        }
        if prefix:
            params['prefix'] = prefix
        if include_archived:
            params['includeArchived'] = 'true'
        if starting_token:
            params['startingToken'] = starting_token
        
        # Call API
        result = api_client.list_asset_files(database_id, asset_id, params)
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            items = result.get('items', [])
            click.echo(click.style(f"✓ Found {len(items)} files", fg='green', bold=True))
            
            if items:
                click.echo("\nFiles:")
                for item in items:
                    file_type = "📁" if item.get('isFolder') else "📄"
                    archived = " (archived)" if item.get('isArchived') else ""
                    size_info = f" ({item.get('size', 0)} bytes)" if not item.get('isFolder') else ""
                    primary_type = f" [{item.get('primaryType')}]" if item.get('primaryType') else ""
                    
                    click.echo(f"  {file_type} {item.get('relativePath', '')}{size_info}{primary_type}{archived}")
            
            if result.get('nextToken'):
                click.echo(f"\nNext token: {result['nextToken']}")
        
        return result
        
    except AssetNotFoundError as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.revert_file_version(database_id, asset_id, version_id, {
            "filePath": file_path
        })
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ File reverted successfully!", fg='green', bold=True))
            click.echo(f"File: {file_path}")
            click.echo(f"Reverted from version: {result.get('revertedFromVersionId', version_id)}")
            if result.get('newVersionId'):
                click.echo(f"New version ID: {result.get('newVersionId')}")
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError, InvalidVersionError) as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.set_primary_file(database_id, asset_id, primary_data)
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            if primary_type == '':
                click.echo(click.style("✓ Primary type removed successfully!", fg='green', bold=True))
            else:
                final_type = type_other if primary_type == 'other' else primary_type
                click.echo(click.style("✓ Primary type set successfully!", fg='green', bold=True))
                click.echo(f"Type: {final_type}")
            click.echo(f"File: {file_path}")
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.delete_asset_preview(database_id, asset_id)
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ Asset preview deleted successfully!", fg='green', bold=True))
            click.echo(f"Asset: {asset_id}")
        
        return result
        
    except AssetNotFoundError as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.delete_auxiliary_preview_files(database_id, asset_id, {
            "filePath": file_path
        })
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ Auxiliary preview files deleted successfully!", fg='green', bold=True))
            click.echo(f"Path prefix: {file_path}")
            if result.get('deletedCount'):
                click.echo(f"Files deleted: {result.get('deletedCount')}")
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.get_file_info(database_id, asset_id, params)
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ File information retrieved", fg='green', bold=True))
            click.echo(f"\nFile: {result.get('fileName', 'N/A')}")
            click.echo(f"Path: {result.get('relativePath', 'N/A')}")
            click.echo(f"Type: {'Folder' if result.get('isFolder') else 'File'}")
            
            if not result.get('isFolder'):
                click.echo(f"Size: {result.get('size', 0)} bytes")
                click.echo(f"Content Type: {result.get('contentType', 'N/A')}")
                if result.get('primaryType'):
                    click.echo(f"Primary Type: {result.get('primaryType')}")
            
            click.echo(f"Last Modified: {result.get('lastModified', 'N/A')}")
            click.echo(f"Storage Class: {result.get('storageClass', 'N/A')}")
            click.echo(f"Archived: {'Yes' if result.get('isArchived') else 'No'}")
            
            if result.get('previewFile'):
                click.echo(f"Preview File: {result.get('previewFile')}")
            
            if include_versions and result.get('versions'):
                click.echo(f"\nVersions ({len(result['versions'])}):")
                for version in result['versions']:
                    status = "Current" if version.get('isLatest') else "Previous"
                    archived = " (archived)" if version.get('isArchived') else ""
                    click.echo(f"  {version.get('versionId', 'N/A')} - {status}{archived}")
                    click.echo(f"    Modified: {version.get('lastModified', 'N/A')}")
                    if not result.get('isFolder'):
                        click.echo(f"    Size: {version.get('size', 0)} bytes")
        
        return result
        
    except AssetNotFoundError as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.move_file(database_id, asset_id, {
            "sourcePath": source,
            "destinationPath": dest
        })
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ File moved successfully!", fg='green', bold=True))
            click.echo(f"From: {source}")
            click.echo(f"To: {dest}")
            
            if result.get('affectedFiles'):
                affected_count = len(result['affectedFiles'])
                if affected_count > 2:  # More than just source and dest
                    click.echo(f"Additional files affected: {affected_count - 2}")
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError, InvalidPathError, FileAlreadyExistsError) as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.copy_file(database_id, asset_id, copy_data)
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ File copied successfully!", fg='green', bold=True))
            click.echo(f"From: {source}")
            click.echo(f"To: {dest}")
            if dest_asset:
                click.echo(f"Destination Asset: {dest_asset}")
            
            if result.get('affectedFiles'):
                affected_count = len(result['affectedFiles'])
                if affected_count > 1:  # More than just the main file
                    click.echo(f"Additional files copied: {affected_count - 1}")
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError, InvalidPathError, FileAlreadyExistsError) as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.archive_file(database_id, asset_id, {
            "filePath": file_path,
            "isPrefix": prefix
        })
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ File(s) archived successfully!", fg='green', bold=True))
            click.echo(f"Path: {file_path}")
            if prefix:
                click.echo("Operation: Archive all files under prefix")
            
            if result.get('affectedFiles'):
                affected_count = len(result['affectedFiles'])
                click.echo(f"Files archived: {affected_count}")
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        
        # Call API
        result = api_client.unarchive_file(database_id, asset_id, {
            "filePath": file_path
        })
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ File unarchived successfully!", fg='green', bold=True))
            click.echo(f"Path: {file_path}")
            
            if result.get('affectedFiles'):
                affected_count = len(result['affectedFiles'])
                if affected_count > 1:
                    click.echo(f"Additional files unarchived: {affected_count - 1}")
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)


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
        if not confirm:
            raise click.ClickException("Permanent deletion requires confirmation (--confirm)")
        
        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        # Call API
        result = api_client.delete_file(database_id, asset_id, {
            "filePath": file_path,
            "isPrefix": prefix,
            "confirmPermanentDelete": True
        })
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(click.style("✓ File(s) deleted permanently!", fg='green', bold=True))
            click.echo(f"Path: {file_path}")
            if prefix:
                click.echo("Operation: Delete all files under prefix")
            
            if result.get('affectedFiles'):
                affected_count = len(result['affectedFiles'])
                click.echo(f"Files deleted: {affected_count}")
        
        return result
        
    except (AssetNotFoundError, InvalidAssetDataError) as e:
        # Only handle command-specific business logic errors
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)
