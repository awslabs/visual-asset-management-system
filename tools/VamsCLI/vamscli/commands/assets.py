"""Asset management commands for VamsCLI."""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

import click

from ..constants import (
    API_DATABASE_ASSETS, API_ASSETS, API_ASSET_EXPORT, DEFAULT_PARALLEL_DOWNLOADS, 
    DEFAULT_DOWNLOAD_RETRY_ATTEMPTS, DEFAULT_DOWNLOAD_TIMEOUT
)
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_warning, output_info
from ..utils.exceptions import (
    AssetNotFoundError, AssetAlreadyExistsError, DatabaseNotFoundError,
    InvalidAssetDataError, AssetAlreadyArchivedError, AssetDeletionError, 
    FileDownloadError, DownloadError, AssetDownloadError, PreviewNotFoundError,
    AssetNotDistributableError, DownloadTreeError, APIError
)
from ..utils.download_manager import (
    DownloadManager, DownloadFileInfo, DownloadProgress, FileTreeBuilder,
    AssetTreeTraverser, format_file_size, format_duration
)


def parse_json_input(json_input: str) -> Dict[str, Any]:
    """Parse JSON input from string or file."""
    # Handle None, empty string, or Click Sentinel objects
    if not json_input or (hasattr(json_input, '__class__') and 'Sentinel' in json_input.__class__.__name__):
        return {}
    
    try:
        # Try to parse as JSON string first
        return json.loads(json_input)
    except json.JSONDecodeError:
        # If that fails, try to read as file path
        try:
            with open(json_input, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, IOError):
            raise click.BadParameter(
                f"Invalid JSON input: '{json_input}' is neither valid JSON nor a readable file path"
            )


def format_asset_output(asset_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format asset data for CLI output."""
    if json_output:
        return json.dumps(asset_data, indent=2)
    
    # CLI-friendly formatting
    output_lines = []
    output_lines.append("Asset Details:")
    output_lines.append(f"  ID: {asset_data.get('assetId', 'N/A')}")
    output_lines.append(f"  Database: {asset_data.get('databaseId', 'N/A')}")
    output_lines.append(f"  Name: {asset_data.get('assetName', 'N/A')}")
    output_lines.append(f"  Description: {asset_data.get('description', 'N/A')}")
    output_lines.append(f"  Distributable: {'Yes' if asset_data.get('isDistributable') else 'No'}")
    output_lines.append(f"  Status: {asset_data.get('status', 'Active').title()}")
    
    # Tags
    tags = asset_data.get('tags', [])
    if tags:
        output_lines.append(f"  Tags: {', '.join(tags)}")
    else:
        output_lines.append("  Tags: None")
    
    # Current version info
    current_version = asset_data.get('currentVersion')
    if current_version:
        output_lines.append(f"  Current Version: {current_version.get('Version', 'N/A')}")
        output_lines.append(f"  Version Date: {current_version.get('DateModified', 'N/A')}")
        if current_version.get('Comment'):
            output_lines.append(f"  Version Comment: {current_version.get('Comment')}")
    
    # Asset location
    asset_location = asset_data.get('assetLocation')
    if asset_location and asset_location.get('Key'):
        output_lines.append(f"  S3 Location: {asset_location.get('Key')}")
    
    # Preview location
    preview_location = asset_data.get('previewLocation')
    if preview_location and preview_location.get('Key'):
        output_lines.append(f"  Preview Location: {preview_location.get('Key')}")
    
    return '\n'.join(output_lines)


def parse_tags_input(tags: List[str]) -> List[str]:
    """Parse tags input, handling both individual tags and comma-separated strings."""
    if not tags:
        return []
    
    parsed_tags = []
    for tag_input in tags:
        # Split by comma in case user provided comma-separated tags
        split_tags = [t.strip() for t in tag_input.split(',') if t.strip()]
        parsed_tags.extend(split_tags)
    
    # Remove duplicates while preserving order
    seen = set()
    unique_tags = []
    for tag in parsed_tags:
        if tag not in seen:
            seen.add(tag)
            unique_tags.append(tag)
    
    return unique_tags


class DownloadProgressDisplay:
    """Display download progress in the terminal."""
    
    def __init__(self, hide_progress: bool = False):
        self.hide_progress = hide_progress
        self.last_update = 0
        self.update_interval = 0.5  # Update every 500ms
        
    def update(self, progress: DownloadProgress):
        """Update the progress display."""
        if self.hide_progress:
            return
            
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
            
        self.last_update = current_time
        
        # Clear previous lines (using click.echo directly for terminal control sequences)
        click.echo('\033[2K\033[1A' * 10, nl=False)  # Clear up to 10 lines
        
        # Overall progress
        overall_pct = progress.overall_progress
        completed_size_str = format_file_size(progress.completed_size)
        total_size_str = format_file_size(progress.total_size)
        
        # Progress bar
        bar_width = 40
        filled = int(bar_width * overall_pct / 100)
        bar = '█' * filled + '░' * (bar_width - filled)
        
        output_info(f"\nOverall Progress: [{bar}] {overall_pct:.1f}% ({completed_size_str}/{total_size_str})", False)
        
        # Speed and ETA
        speed_str = format_file_size(int(progress.download_speed)) + "/s"
        eta = progress.estimated_time_remaining
        eta_str = format_duration(eta) if eta else "calculating..."
        
        output_info(f"Speed: {speed_str} | Active: {progress.active_downloads} | ETA: {eta_str}", False)
        
        # File progress (show up to 5 files)
        files_shown = 0
        for file_key, file_progress in progress.file_progress.items():
            if files_shown >= 5:
                remaining = len(progress.file_progress) - files_shown
                if remaining > 0:
                    output_info(f"... and {remaining} more files", False)
                break
                
            file_pct = (file_progress["completed_size"] / file_progress["total_size"]) * 100 if file_progress["total_size"] > 0 else 0
            status_icon = {
                "pending": "⏳",
                "downloading": "⬇️",
                "completed": "✅",
                "failed": "❌"
            }.get(file_progress["status"], "❓")
            
            # Truncate long filenames
            display_name = file_key
            if len(display_name) > 50:
                display_name = "..." + display_name[-47:]
                
            output_info(f"  {status_icon} {display_name}: {file_pct:.1f}%", False)
            files_shown += 1


@click.group()
def assets():
    """Asset management commands."""
    pass


@assets.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID where the asset will be created')
@click.option('--name', help='[REQUIRED unless using --json-input] Asset name')
@click.option('--description', help='[REQUIRED unless using --json-input] Asset description')
@click.option('--distributable/--no-distributable', default=None, help='[REQUIRED unless using --json-input] Whether the asset is distributable')
@click.option('--tags', multiple=True, help='[OPTIONAL] Asset tags (can be used multiple times)')
@click.option('--bucket-key', help='[OPTIONAL] Existing S3 bucket key to use')
@click.option('--json-input', help='[OPTIONAL] JSON input file path or JSON string with all asset data')
@click.option('--json-output', is_flag=True, help='[OPTIONAL] Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, database_id: str, name: Optional[str], 
          description: Optional[str], distributable: Optional[bool], tags: List[str],
          bucket_key: Optional[str], json_input: Optional[str], json_output: bool):
    """
    Create a new asset in VAMS.
    
    This command creates a new asset (metadata only) in the specified database.
    You can provide asset details via individual options or use --json-input for
    complex data structures.
    
    Examples:
        vamscli assets create -d my-database --name "My Asset" --description "Asset description" --distributable
        vamscli assets create -d my-database --json-input '{"assetName":"test","description":"desc","isDistributable":true}'
        vamscli assets create -d my-database --name "Tagged Asset" --description "With tags" --tags tag1 --tags tag2
    """
    # Get profile manager and API client (setup/auth already validated by decorator)
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        
        # Build asset data
        if json_input:
            # Use JSON input
            asset_data = parse_json_input(json_input)
            # Override database_id from command line
            asset_data['databaseId'] = database_id
            
            # Validate that assetId is not provided in JSON input
            if 'assetId' in asset_data:
                raise click.BadParameter(
                    "assetId cannot be specified during asset creation through the CLI or web front-end. "
                    "Asset IDs are automatically generated by the system. "
                    "Remove 'assetId' from your input."
                )
        else:
            # Build from individual options
            if not name:
                raise click.BadParameter("--name is required when not using --json-input")
            if not description:
                raise click.BadParameter("--description is required when not using --json-input")
            if distributable is None:
                raise click.BadParameter("--distributable or --no-distributable is required when not using --json-input")
            
            asset_data = {
                'databaseId': database_id,
                'assetName': name,
                'description': description,
                'isDistributable': distributable,
                'tags': parse_tags_input(__builtins__['list'](tags))
            }
            
            if bucket_key:
                asset_data['bucketExistingKey'] = bucket_key
        
        output_status("Creating asset...", json_output)
        
        # Create the asset
        result = api_client.create_asset(asset_data)
        
        def format_create_result(data):
            """Format create result for CLI display."""
            lines = []
            lines.append(f"  Asset ID: {data.get('assetId')}")
            lines.append(f"  Database: {database_id}")
            lines.append(f"  Message: {data.get('message', 'Asset created')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset created successfully!",
            cli_formatter=format_create_result
        )
        
        # Return result for use by wrapper commands
        return result
        
    except AssetAlreadyExistsError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Already Exists",
            helpful_message="Use 'vamscli assets get' to view the existing asset or choose a different asset ID."
        )
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))
    except InvalidAssetDataError as e:
        output_error(e, json_output, error_type="Invalid Asset Data")
        raise click.ClickException(str(e))


@assets.command()
@click.argument('asset_id')
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('--reason', help='Reason for archiving the asset')
@click.option('--json-input', type=click.File('r'), help='JSON file with parameters')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def archive(ctx: click.Context, asset_id: str, database: str, reason: Optional[str], json_input: Optional[click.File], json_output: bool):
    """
    Archive an asset (soft delete).
    
    This command archives an asset, which moves it to an archived state but allows
    it to be recovered later. The asset will no longer appear in normal listings
    unless --show-archived is used.
    
    Examples:
        vamscli assets archive my-asset -d my-database
        vamscli assets archive my-asset -d my-database --reason "No longer needed"
        vamscli assets archive my-asset -d my-database --json-input archive-params.json
        vamscli assets archive my-asset -d my-database --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Handle JSON input
        if json_input:
            try:
                json_data = json.load(json_input)
                # Override command line parameters with JSON data
                database = json_data.get('databaseId', database)
                asset_id = json_data.get('assetId', asset_id)
                reason = json_data.get('reason', reason)
            except json.JSONDecodeError as e:
                raise click.BadParameter(f"Invalid JSON in input file: {e}")
        
        output_status(f"Archiving asset '{asset_id}' in database '{database}'...", json_output)
        
        # Archive the asset
        result = api_client.archive_asset(database, asset_id, reason)
        
        def format_archive_result(data):
            """Format archive result for CLI display."""
            lines = []
            lines.append(f"  Asset ID: {asset_id}")
            lines.append(f"  Database: {database}")
            lines.append(f"  Operation: {data.get('operation', 'archive')}")
            lines.append(f"  Timestamp: {data.get('timestamp', 'N/A')}")
            lines.append(f"  Message: {data.get('message', 'Asset archived')}")
            lines.append("")
            lines.append("The asset has been moved to archived state and will not appear in normal listings.")
            lines.append("Use 'vamscli assets get --show-archived' to view archived assets.")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset archived successfully!",
            cli_formatter=format_archive_result
        )
        
        return result
        
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database} {asset_id}' to check if the asset exists."
        )
        raise click.ClickException(str(e))
    except AssetAlreadyArchivedError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Already Archived",
            helpful_message=f"Use 'vamscli assets get -d {database} {asset_id} --show-archived' to view the archived asset."
        )
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))


@assets.command()
@click.argument('asset_id')
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('--reason', help='Reason for deleting the asset')
@click.option('--confirm', is_flag=True, help='Confirm permanent deletion')
@click.option('--json-input', type=click.File('r'), help='JSON file with parameters')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, asset_id: str, database: str, reason: Optional[str], confirm: bool, json_input: Optional[click.File], json_output: bool):
    """
    Permanently delete an asset.
    
    ⚠️  WARNING: This action cannot be undone! ⚠️
    
    This command permanently deletes an asset and all its associated data,
    including files, metadata, versions, and links. The asset cannot be recovered
    after deletion.
    
    The --confirm flag is required to prevent accidental deletions.
    
    Examples:
        vamscli assets delete my-asset -d my-database --confirm
        vamscli assets delete my-asset -d my-database --confirm --reason "Project cancelled"
        vamscli assets delete my-asset -d my-database --json-input delete-params.json
        vamscli assets delete my-asset -d my-database --confirm --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Handle JSON input
        if json_input:
            try:
                json_data = json.load(json_input)
                # Override command line parameters with JSON data
                database = json_data.get('databaseId', database)
                asset_id = json_data.get('assetId', asset_id)
                reason = json_data.get('reason', reason)
                confirm = json_data.get('confirmPermanentDelete', confirm)
            except json.JSONDecodeError as e:
                raise click.BadParameter(f"Invalid JSON in input file: {e}")
        
        # Require confirmation for permanent deletion
        if not confirm:
            if json_output:
                # For JSON output, return error in JSON format
                import sys
                error_result = {
                    "error": "Confirmation required",
                    "message": "Permanent deletion requires the --confirm flag",
                    "assetId": asset_id,
                    "databaseId": database
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                # For CLI output, show helpful message
                output_warning("⚠️  Permanent deletion requires explicit confirmation!", False)
                output_info("This action cannot be undone and will permanently delete:", False)
                output_info("  • The asset and all its metadata", False)
                output_info("  • All asset files and versions", False)
                output_info("  • All asset links and relationships", False)
                output_info("  • All comments and version history", False)
                output_info("", False)
                output_info("Use --confirm flag to proceed with permanent deletion.", False)
                raise click.ClickException("Confirmation required for permanent deletion")
        
        # Additional confirmation prompt for safety (skip in JSON mode)
        if not json_output:
            output_warning(f"⚠️  You are about to permanently delete asset '{asset_id}' from database '{database}'", False)
            output_warning("This action cannot be undone!", False)
            
            if not click.confirm("Are you sure you want to proceed?"):
                output_info("Deletion cancelled.", False)
                return None
        
        output_status(f"Permanently deleting asset '{asset_id}' from database '{database}'...", json_output)
        
        # Delete the asset
        result = api_client.delete_asset_permanent(database, asset_id, reason, confirm)
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            lines = []
            lines.append(f"  Asset ID: {asset_id}")
            lines.append(f"  Database: {database}")
            lines.append(f"  Operation: {data.get('operation', 'delete')}")
            lines.append(f"  Timestamp: {data.get('timestamp', 'N/A')}")
            lines.append(f"  Message: {data.get('message', 'Asset deleted')}")
            lines.append("")
            lines.append(click.style("The asset and all associated data have been permanently removed.", fg='yellow'))
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset permanently deleted!",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database} {asset_id} --show-archived' to check if the asset exists."
        )
        raise click.ClickException(str(e))
    except AssetDeletionError as e:
        output_error(
            e,
            json_output,
            error_type="Deletion Error",
            helpful_message="Ensure you have provided the --confirm flag for permanent deletion."
        )
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))


@assets.command()
@click.argument('asset_id')
@click.option('-d', '--database-id', required=True, help='Database ID containing the asset')
@click.option('--name', help='New asset name')
@click.option('--description', help='New asset description')
@click.option('--distributable/--no-distributable', default=None, help='Update distributable flag')
@click.option('--tags', multiple=True, help='New asset tags (replaces existing tags)')
@click.option('--json-input', help='JSON input file path or JSON string with update data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, asset_id: str, database_id: str, name: Optional[str], description: Optional[str],
          distributable: Optional[bool], tags: List[str], json_input: Optional[str], json_output: bool):
    """
    Update an existing asset in VAMS.
    
    This command updates the editable fields of an existing asset. You can update
    individual fields or use --json-input for complex updates.
    
    Examples:
        vamscli assets update my-asset -d my-database --name "Updated Name"
        vamscli assets update my-asset -d my-database --description "New description" --distributable
        vamscli assets update my-asset -d my-database --json-input '{"assetName":"updated","tags":["new","tags"]}'
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build update data
        if json_input:
            # Use JSON input
            update_data = parse_json_input(json_input)
        else:
            # Build from individual options
            update_data = {}
            
            if name:
                update_data['assetName'] = name
            if description:
                update_data['description'] = description
            if distributable is not None:
                update_data['isDistributable'] = distributable
            if tags:
                update_data['tags'] = parse_tags_input(__builtins__['list'](tags))
            
            # Ensure at least one field is being updated
            if not update_data:
                raise click.BadParameter(
                    "At least one field must be provided for update. "
                    "Use --name, --description, --distributable, --tags, or --json-input."
                )
        
        output_status(f"Updating asset '{asset_id}' in database '{database_id}'...", json_output)
        
        # Update the asset
        result = api_client.update_asset(database_id, asset_id, update_data)
        
        def format_update_result(data):
            """Format update result for CLI display."""
            lines = []
            lines.append(f"  Asset ID: {data.get('assetId', asset_id)}")
            lines.append(f"  Operation: {data.get('operation', 'update')}")
            lines.append(f"  Timestamp: {data.get('timestamp', 'N/A')}")
            lines.append(f"  Message: {data.get('message', 'Asset updated')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset updated successfully!",
            cli_formatter=format_update_result
        )
        
        return result
        
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database_id} {asset_id}' to check if the asset exists."
        )
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))
    except InvalidAssetDataError as e:
        output_error(e, json_output, error_type="Invalid Update Data")
        raise click.ClickException(str(e))


@assets.command()
@click.argument('asset_id')
@click.option('-d', '--database-id', required=True, help='Database ID containing the asset')
@click.option('--show-archived', is_flag=True, help='Include archived assets in search')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, asset_id: str, database_id: str, show_archived: bool, json_output: bool):
    """
    Get details for a specific asset.
    
    This command retrieves detailed information about an asset, including
    metadata, version information, and file locations.
    
    Examples:
        vamscli assets get my-asset -d my-database
        vamscli assets get my-asset -d my-database --show-archived
        vamscli assets get my-asset -d my-database --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        output_status(f"Retrieving asset '{asset_id}' from database '{database_id}'...", json_output)
        
        # Get the asset
        result = api_client.get_asset(database_id, asset_id, show_archived)
        
        output_result(result, json_output, cli_formatter=format_asset_output)
        
        return result
        
    except AssetNotFoundError as e:
        helpful_msg = f"Use 'vamscli assets list -d {database_id}' to see available assets."
        if not show_archived:
            helpful_msg = "Try using --show-archived to include archived assets.\n" + helpful_msg
        
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=helpful_msg
        )
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))

@assets.command()
@click.option('-d', '--database-id', help='Database ID to list assets from (optional for all assets)')
@click.option('--show-archived', is_flag=True, help='Include archived assets')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database_id: Optional[str], show_archived: bool, page_size: int, 
         max_items: int, starting_token: str, auto_paginate: bool, json_output: bool):
    """
    List assets in a database or all assets.
    
    This command lists assets from a specific database or all assets across
    all databases if no database ID is specified.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli assets list -d my-database
        
        # Auto-pagination to fetch all items (default: up to 10,000)
        vamscli assets list -d my-database --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli assets list -d my-database --auto-paginate --max-items 5000
        
        # Auto-pagination with custom page size
        vamscli assets list -d my-database --auto-paginate --page-size 500
        
        # Manual pagination with page size
        vamscli assets list -d my-database --page-size 200
        vamscli assets list -d my-database --starting-token "token123" --page-size 200
        
        # With filters
        vamscli assets list -d my-database --show-archived
        vamscli assets list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
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
        
        if database_id:
            endpoint = API_DATABASE_ASSETS.format(databaseId=database_id)
            status_msg = f"Listing assets in database '{database_id}'..."
        else:
            endpoint = API_ASSETS
            status_msg = "Listing all assets..."
        
        if auto_paginate:
            # Auto-pagination mode: fetch all items up to max_items (default 10,000)
            max_total_items = max_items or 10000
            output_status(f"{status_msg[:-3]} (auto-paginating up to {max_total_items} items)...", json_output)
            
            all_items = []
            next_token = None
            total_fetched = 0
            page_count = 0
            
            while True:
                page_count += 1
                
                # Prepare query parameters for this page
                params = {}
                if show_archived:
                    params['showArchived'] = 'true'
                if page_size:
                    params['pageSize'] = page_size  # Pass pageSize to API
                if next_token:
                    params['startingToken'] = next_token
                
                # Note: maxItems is NOT passed to API - it's CLI-side limit only
                
                # Make API call
                response = api_client.get(endpoint, include_auth=True, params=params)
                page_result = response.json()
                
                # Aggregate items
                items = page_result.get('Items', [])
                all_items.extend(items)
                total_fetched += len(items)
                
                # Show progress in CLI mode
                if not json_output:
                    output_status(f"Fetched {total_fetched} assets (page {page_count})...", False)
                
                # Check if we should continue
                next_token = page_result.get('NextToken')
                if not next_token or total_fetched >= max_total_items:
                    break
            
            # Create final result
            result = {
                'Items': all_items,
                'totalItems': len(all_items),
                'autoPaginated': True,
                'pageCount': page_count
            }
            
            if total_fetched >= max_total_items and next_token:
                result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
            
        else:
            # Manual pagination mode: single API call
            output_status(status_msg, json_output)
            
            # Prepare query parameters
            params = {}
            if show_archived:
                params['showArchived'] = 'true'
            if page_size:
                params['pageSize'] = page_size  # Pass pageSize to API
            if starting_token:
                params['startingToken'] = starting_token
            
            # Note: maxItems is NOT passed to API in manual mode
            
            # Get the assets
            response = api_client.get(endpoint, include_auth=True, params=params)
            result = response.json()
        
        def format_assets_list(data):
            """Format assets list for CLI display."""
            items = data.get('Items', [])
            if not items:
                return "No assets found."
            
            lines = []
            
            # Show auto-pagination info if present
            if data.get('autoPaginated'):
                lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
                if data.get('note'):
                    lines.append(f"⚠️  {data['note']}")
                lines.append("")
            
            lines.append(f"Found {len(items)} asset(s):")
            lines.append("-" * 80)
            
            for asset in items:
                lines.append(f"ID: {asset.get('assetId', 'N/A')}")
                lines.append(f"Database: {asset.get('databaseId', 'N/A')}")
                lines.append(f"Name: {asset.get('assetName', 'N/A')}")
                lines.append(f"Description: {asset.get('description', 'N/A')}")
                lines.append(f"Distributable: {'Yes' if asset.get('isDistributable') else 'No'}")
                lines.append(f"Status: {asset.get('status', 'Active').title()}")
                
                tags = asset.get('tags', [])
                if tags:
                    lines.append(f"Tags: {', '.join(tags)}")
                
                lines.append("-" * 80)
            
            # Show nextToken for manual pagination
            if not data.get('autoPaginated') and data.get('NextToken'):
                lines.append(f"\nNext token: {data['NextToken']}")
                lines.append("Use --starting-token to get the next page")
            
            return '\n'.join(lines)
        
        output_result(result, json_output, cli_formatter=format_assets_list)
        
        return result
        
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))


@assets.command()
@click.argument('local_path', required=False)
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset', required=True, help='Asset ID to download from')
@click.option('--file-key', help='Specific asset file key to download')
@click.option('--recursive', is_flag=True, help='Download all files from folder tree structure')
@click.option('--flatten-download-tree', is_flag=True, help='Ignore asset file tree, download files flat')
@click.option('--asset-preview', is_flag=True, help='Download only the asset preview file')
@click.option('--file-previews', is_flag=True, help='Additionally download file preview files')
@click.option('--asset-link-children-tree-depth', type=int, help='Traverse asset link children tree to specified depth')
@click.option('--shareable-links-only', is_flag=True, help='Return presigned URLs without downloading')
@click.option('--parallel-downloads', type=int, default=DEFAULT_PARALLEL_DOWNLOADS, 
              help=f'Max parallel downloads (default: {DEFAULT_PARALLEL_DOWNLOADS})')
@click.option('--retry-attempts', type=int, default=DEFAULT_DOWNLOAD_RETRY_ATTEMPTS,
              help=f'Retry attempts per file (default: {DEFAULT_DOWNLOAD_RETRY_ATTEMPTS})')
@click.option('--timeout', type=int, default=DEFAULT_DOWNLOAD_TIMEOUT,
              help=f'Download timeout per file in seconds (default: {DEFAULT_DOWNLOAD_TIMEOUT})')
@click.option('--json-input', help='JSON input with all parameters')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.option('--hide-progress', is_flag=True, help='Hide download progress display')
@click.pass_context
@requires_setup_and_auth
def download(ctx: click.Context, local_path: Optional[str], database: str, asset: str, 
            file_key: Optional[str], recursive: bool, flatten_download_tree: bool,
            asset_preview: bool, file_previews: bool, asset_link_children_tree_depth: Optional[int],
            shareable_links_only: bool, parallel_downloads: int, retry_attempts: int, timeout: int,
            json_input: Optional[str], json_output: bool, hide_progress: bool):
    """
    Download files from an asset.
    
    This command downloads files from a VAMS asset to a local directory. It supports
    various download scenarios including individual files, folders, whole assets,
    asset previews, and traversing asset link trees.
    
    Examples:
        # Download whole asset
        vamscli assets download /local/path -d my-db -a my-asset
        
        # Download specific file
        vamscli assets download /local/path -d my-db -a my-asset --file-key "/model.gltf"
        
        # Download folder recursively
        vamscli assets download /local/path -d my-db -a my-asset --file-key "/models/" --recursive
        
        # Download asset preview only
        vamscli assets download /local/path -d my-db -a my-asset --asset-preview
        
        # Download with file previews
        vamscli assets download /local/path -d my-db -a my-asset --file-key "/model.gltf" --file-previews
        
        # Download asset tree (2 levels deep)
        vamscli assets download /local/path -d my-db -a my-asset --asset-link-children-tree-depth 2
        
        # Get shareable links only (no download)
        vamscli assets download -d my-db -a my-asset --shareable-links-only
        
        # Flatten download (ignore folder structure)
        vamscli assets download /local/path -d my-db -a my-asset --flatten-download-tree
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Parse JSON input if provided
        json_data = parse_json_input(json_input) if json_input else {}
        
        # Override arguments with JSON data
        database = json_data.get('database', database)
        asset = json_data.get('asset', asset)
        local_path = json_data.get('local_path', local_path)
        file_key = json_data.get('file_key', file_key)
        recursive = json_data.get('recursive', recursive)
        flatten_download_tree = json_data.get('flatten_download_tree', flatten_download_tree)
        asset_preview = json_data.get('asset_preview', asset_preview)
        file_previews = json_data.get('file_previews', file_previews)
        asset_link_children_tree_depth = json_data.get('asset_link_children_tree_depth', asset_link_children_tree_depth)
        shareable_links_only = json_data.get('shareable_links_only', shareable_links_only)
        parallel_downloads = json_data.get('parallel_downloads', parallel_downloads)
        retry_attempts = json_data.get('retry_attempts', retry_attempts)
        timeout = json_data.get('timeout', timeout)
        hide_progress = json_data.get('hide_progress', hide_progress)
        
        # Suppress progress display in JSON mode
        if json_output:
            hide_progress = True
        
        # Validate arguments
        if not shareable_links_only and not local_path:
            raise click.ClickException("Local path is required for downloads (not needed for --shareable-links-only)")
        
        if not database:
            raise click.ClickException("Database ID is required (-d/--database)")
        if not asset:
            raise click.ClickException("Asset ID is required (-a/--asset)")
        
        # Validate conflicting options
        if asset_preview and file_key:
            raise click.ClickException("Cannot specify both --asset-preview and --file-key")
        if asset_preview and asset_link_children_tree_depth:
            raise click.ClickException("Cannot specify both --asset-preview and --asset-link-children-tree-depth")
        if file_previews and not file_key:
            raise click.ClickException("--file-previews requires --file-key to be specified")
        if flatten_download_tree and not file_key:
            raise click.ClickException("--flatten-download-tree requires --file-key to be specified")
        if recursive and not file_key:
            raise click.ClickException("--recursive requires --file-key to be specified")
        
        # Handle shareable links only mode
        if shareable_links_only:
            if asset_preview:
                # Get asset preview link
                try:
                    download_response = api_client.download_asset_preview(database, asset)
                    result = {
                        "shareableLinks": [{
                            "filePath": "asset_preview",
                            "downloadUrl": download_response.get('downloadUrl'),
                            "expiresIn": download_response.get('expiresIn', 86400),
                            "downloadType": "assetPreview"
                        }],
                        "totalFiles": 1,
                        "message": "Shareable link generated successfully"
                    }
                except Exception as e:
                    raise PreviewNotFoundError(f"Asset preview not available: {e}")
            else:
                # Get file links
                try:
                    if file_key:
                        download_response = api_client.download_asset_file(database, asset, file_key)
                        result = {
                            "shareableLinks": [{
                                "filePath": file_key,
                                "downloadUrl": download_response.get('downloadUrl'),
                                "expiresIn": download_response.get('expiresIn', 86400),
                                "downloadType": "assetFile"
                            }],
                            "totalFiles": 1,
                            "message": "Shareable link generated successfully"
                        }
                    else:
                        # Get all files
                        files_response = api_client.list_asset_files(database, asset, {
                            'includeArchived': 'false',
                            'maxItems': 1000
                        })
                        
                        all_files = files_response.get('items', [])
                        target_files = [f for f in all_files if not f.get('isFolder')]
                        
                        if not target_files:
                            raise FileDownloadError(f"Asset '{asset}' currently has no files to download")
                        
                        shareable_links = []
                        for file_item in target_files:
                            relative_path = file_item.get('relativePath', '')
                            try:
                                download_response = api_client.download_asset_file(database, asset, relative_path)
                                shareable_links.append({
                                    "filePath": relative_path,
                                    "downloadUrl": download_response.get('downloadUrl'),
                                    "expiresIn": download_response.get('expiresIn', 86400),
                                    "downloadType": "assetFile"
                                })
                            except Exception as e:
                                # Skip files that can't be downloaded
                                pass
                        
                        result = {
                            "shareableLinks": shareable_links,
                            "totalFiles": len(shareable_links),
                            "message": "Shareable links generated successfully"
                        }
                        
                except Exception as e:
                    raise AssetDownloadError(f"Failed to generate shareable links: {e}")
        else:
            # Actual file downloads
            try:
                # Determine what to download
                files_to_download = []
                
                if asset_preview:
                    # Download asset preview only
                    output_status("Fetching asset preview...", json_output)
                    download_response = api_client.download_asset_preview(database, asset)
                    
                    # Create download info for asset preview
                    preview_filename = f"assetpreview_{Path(download_response.get('key', 'preview')).name}"
                    local_file_path = Path(local_path) / preview_filename
                    
                    files_to_download.append(DownloadFileInfo(
                        relative_key="asset_preview",
                        local_path=local_file_path,
                        download_url=download_response.get('downloadUrl'),
                        file_size=download_response.get('size')
                    ))
                    
                elif asset_link_children_tree_depth is not None:
                    # Download asset tree
                    output_status(f"Traversing asset tree (depth: {asset_link_children_tree_depth})...", json_output)
                    
                    # Use async function to traverse tree
                    async def traverse_tree_async():
                        traverser = AssetTreeTraverser(api_client)
                        return await traverser.traverse_asset_tree(
                            database, asset, asset_link_children_tree_depth
                        )
                    
                    assets_to_download = asyncio.run(traverse_tree_async())
                    
                    # For each asset in tree, get its files
                    for asset_info in assets_to_download:
                        asset_db = asset_info['databaseId']
                        asset_id = asset_info['assetId']
                        depth = asset_info['depth']
                        
                        # Create subdirectory for this asset
                        asset_dir = Path(local_path) / asset_id
                        
                        # Get files for this asset
                        files_response = api_client.list_asset_files(asset_db, asset_id, {
                            'includeArchived': 'false',
                            'maxItems': 1000
                        })
                        
                        asset_files = files_response.get('items', [])
                        target_files = [f for f in asset_files if not f.get('isFolder')]
                        
                        # Generate download info for each file
                        for file_item in target_files:
                            relative_path = file_item.get('relativePath', '')
                            file_local_path = asset_dir / relative_path.lstrip('/')
                            
                            try:
                                download_response = api_client.download_asset_file(asset_db, asset_id, relative_path)
                                files_to_download.append(DownloadFileInfo(
                                    relative_key=f"{asset_id}/{relative_path}",
                                    local_path=file_local_path,
                                    download_url=download_response.get('downloadUrl'),
                                    file_size=file_item.get('size')
                                ))
                            except Exception as e:
                                output_warning(f"Skipping file {relative_path} from asset {asset_id}: {e}", json_output)
                    
                elif file_key:
                    # Download specific file or folder
                    output_status(f"Fetching file(s) for key: {file_key}...", json_output)
                    
                    if recursive or file_key.endswith('/'):
                        # Download folder contents
                        files_response = api_client.list_asset_files(database, asset, {
                            'includeArchived': 'false',
                            'maxItems': 1000
                        })
                        
                        all_files = files_response.get('items', [])
                        
                        # Get files under the specified prefix
                        target_files = FileTreeBuilder.get_files_under_prefix(
                            all_files, file_key, recursive
                        )
                        
                        if not target_files:
                            raise FileDownloadError(f"No files found under '{file_key}'")
                        
                        # Handle flattening
                        if flatten_download_tree:
                            target_files = FileTreeBuilder.flatten_file_list(target_files)
                        
                        # Generate download info for each file
                        for file_item in target_files:
                            relative_path = file_item.get('relativePath', '')
                            
                            if flatten_download_tree:
                                # Save to root of local path
                                file_local_path = Path(local_path) / Path(relative_path).name
                            else:
                                # Preserve directory structure
                                file_local_path = Path(local_path) / relative_path.lstrip('/')
                            
                            try:
                                download_response = api_client.download_asset_file(database, asset, relative_path)
                                files_to_download.append(DownloadFileInfo(
                                    relative_key=relative_path,
                                    local_path=file_local_path,
                                    download_url=download_response.get('downloadUrl'),
                                    file_size=file_item.get('size')
                                ))
                            except Exception as e:
                                output_warning(f"Skipping file {relative_path}: {e}", json_output)
                        
                        # Download file previews if requested
                        if file_previews:
                            for file_item in target_files:
                                relative_path = file_item.get('relativePath', '')
                                # Try to get preview for this file
                                try:
                                    preview_response = api_client.download_asset_file(
                                        database, asset, f"{relative_path}_preview"
                                    )
                                    
                                    if flatten_download_tree:
                                        preview_local_path = Path(local_path) / Path(relative_path).name
                                    else:
                                        preview_local_path = Path(local_path) / relative_path.lstrip('/')
                                    
                                    files_to_download.append(DownloadFileInfo(
                                        relative_key=f"{relative_path}_preview",
                                        local_path=preview_local_path,
                                        download_url=preview_response.get('downloadUrl'),
                                        file_size=None
                                    ))
                                except Exception:
                                    # Preview doesn't exist, skip silently
                                    pass
                    else:
                        # Download single file
                        download_response = api_client.download_asset_file(database, asset, file_key)
                        
                        if flatten_download_tree:
                            file_local_path = Path(local_path) / Path(file_key).name
                        else:
                            file_local_path = Path(local_path) / file_key.lstrip('/')
                        
                        files_to_download.append(DownloadFileInfo(
                            relative_key=file_key,
                            local_path=file_local_path,
                            download_url=download_response.get('downloadUrl'),
                            file_size=None
                        ))
                        
                        # Download file preview if requested
                        if file_previews:
                            try:
                                preview_response = api_client.download_asset_file(
                                    database, asset, f"{file_key}_preview"
                                )
                                
                                files_to_download.append(DownloadFileInfo(
                                    relative_key=f"{file_key}_preview",
                                    local_path=file_local_path,  # Same name as source file
                                    download_url=preview_response.get('downloadUrl'),
                                    file_size=None
                                ))
                            except Exception:
                                # Preview doesn't exist, skip silently
                                pass
                else:
                    # Download all files from asset
                    output_status("Fetching all files from asset...", json_output)
                    
                    files_response = api_client.list_asset_files(database, asset, {
                        'includeArchived': 'false',
                        'maxItems': 1000
                    })
                    
                    all_files = files_response.get('items', [])
                    target_files = [f for f in all_files if not f.get('isFolder')]
                    
                    if not target_files:
                        raise FileDownloadError(f"Asset '{asset}' currently has no files to download")
                    
                    # Generate download info for each file
                    for file_item in target_files:
                        relative_path = file_item.get('relativePath', '')
                        file_local_path = Path(local_path) / relative_path.lstrip('/')
                        
                        try:
                            download_response = api_client.download_asset_file(database, asset, relative_path)
                            files_to_download.append(DownloadFileInfo(
                                relative_key=relative_path,
                                local_path=file_local_path,
                                download_url=download_response.get('downloadUrl'),
                                file_size=file_item.get('size')
                            ))
                        except Exception as e:
                            output_warning(f"Skipping file {relative_path}: {e}", json_output)
                
                if not files_to_download:
                    raise FileDownloadError("No files to download")
                
                # Check for conflicts if flattening
                if flatten_download_tree and len(files_to_download) > 1:
                    filenames = [f.local_path.name for f in files_to_download]
                    if len(filenames) != len(set(filenames)):
                        # Conflicts detected
                        conflicts = [name for name in filenames if filenames.count(name) > 1]
                        unique_conflicts = list(set(conflicts))
                        
                        if not json_output:
                            output_warning(f"Filename conflicts detected: {', '.join(unique_conflicts)}", False)
                            output_info("Options:", False)
                            output_info("  1. Skip conflicting files", False)
                            output_info("  2. Overwrite existing files", False)
                            output_info("  3. Rename with numeric suffix", False)
                            output_info("  4. Abort download", False)
                            
                            choice = click.prompt("Choose an option", type=int, default=4)
                            
                            if choice == 4:
                                raise click.ClickException("Download aborted due to filename conflicts")
                            elif choice == 1:
                                # Remove duplicates, keep first occurrence
                                seen = set()
                                filtered_files = []
                                for f in files_to_download:
                                    if f.local_path.name not in seen:
                                        seen.add(f.local_path.name)
                                        filtered_files.append(f)
                                files_to_download = filtered_files
                            elif choice == 3:
                                # Rename with numeric suffix
                                name_counts = {}
                                renamed_files = []
                                for f in files_to_download:
                                    name = f.local_path.name
                                    if name in name_counts:
                                        name_counts[name] += 1
                                        stem = f.local_path.stem
                                        suffix = f.local_path.suffix
                                        new_name = f"{stem}_{name_counts[name]}{suffix}"
                                        new_path = f.local_path.parent / new_name
                                        renamed_files.append(DownloadFileInfo(
                                            relative_key=f.relative_key,
                                            local_path=new_path,
                                            download_url=f.download_url,
                                            file_size=f.file_size
                                        ))
                                    else:
                                        name_counts[name] = 0
                                        renamed_files.append(f)
                                files_to_download = renamed_files
                            # Option 2 (overwrite) - no changes needed
                        else:
                            raise FileDownloadError(
                                f"Filename conflicts detected in flattened download: {', '.join(unique_conflicts)}. "
                                "Use CLI mode for interactive conflict resolution."
                            )
                
                # Perform downloads
                output_status(f"Downloading {len(files_to_download)} file(s)...", json_output)
                
                # Create progress callback
                progress_display = DownloadProgressDisplay(hide_progress=hide_progress)
                
                def progress_callback(progress: DownloadProgress):
                    progress_display.update(progress)
                
                # Use async download manager
                async def download_files_async():
                    async with DownloadManager(
                        api_client,
                        max_parallel=parallel_downloads,
                        max_retries=retry_attempts,
                        timeout=timeout,
                        progress_callback=progress_callback
                    ) as manager:
                        return await manager.download_files(files_to_download)
                
                # Run async download
                result = asyncio.run(download_files_async())
                
                # Verify downloaded files
                verified_files = []
                verification_failures = []
                
                for file_info in files_to_download:
                    if file_info.local_path.exists():
                        actual_size = file_info.local_path.stat().st_size
                        expected_size = file_info.file_size
                        
                        if expected_size is None or actual_size == expected_size:
                            verified_files.append(str(file_info.local_path))
                        else:
                            verification_failures.append({
                                'path': str(file_info.local_path),
                                'expected_size': expected_size,
                                'actual_size': actual_size,
                                'reason': 'size_mismatch'
                            })
                    else:
                        # Check if this file was in the failed downloads
                        failed = any(
                            f['relative_key'] == file_info.relative_key 
                            for f in result.get('failed_downloads', [])
                        )
                        if not failed:
                            verification_failures.append({
                                'path': str(file_info.local_path),
                                'reason': 'file_missing'
                            })
                
                # Add verification info to result
                result['verified_files'] = len(verified_files)
                result['verification_failures'] = verification_failures
                
                # Format output
                def format_download_result(data):
                    """Format download result for CLI display."""
                    lines = []
                    lines.append(f"Total files: {data.get('total_files', 0)}")
                    lines.append(f"Successful: {data.get('successful_files', 0)}")
                    lines.append(f"Failed: {data.get('failed_files', 0)}")
                    lines.append(f"Verified: {data.get('verified_files', 0)}")
                    
                    if data.get('verification_failures'):
                        lines.append(f"\nVerification failures: {len(data['verification_failures'])}")
                        for failure in data['verification_failures'][:5]:
                            lines.append(f"  • {failure['path']}: {failure['reason']}")
                    
                    lines.append(f"\nTotal size: {data.get('total_size_formatted', 'N/A')}")
                    lines.append(f"Duration: {format_duration(data.get('download_duration', 0))}")
                    lines.append(f"Average speed: {data.get('average_speed_formatted', 'N/A')}")
                    
                    if data.get('failed_downloads'):
                        lines.append(f"\nFailed downloads:")
                        for failed in data['failed_downloads'][:5]:
                            lines.append(f"  • {failed['relative_key']}: {failed['error']}")
                        
                        if len(data['failed_downloads']) > 5:
                            lines.append(f"  ... and {len(data['failed_downloads']) - 5} more")
                    
                    return '\n'.join(lines)
                
                success_msg = "✓ Download completed successfully!" if result['overall_success'] else "⚠ Download completed with errors"
                
                output_result(
                    result,
                    json_output,
                    success_message=success_msg,
                    cli_formatter=format_download_result
                )
                
            except asyncio.TimeoutError as e:
                raise DownloadError(f"Download operation timed out: {e}")
            except Exception as e:
                if isinstance(e, (FileDownloadError, DownloadError, AssetDownloadError)):
                    raise
                raise DownloadError(f"Download failed: {e}")
        
        # Output shareable links results
        if shareable_links_only:
            def format_shareable_links(data):
                """Format shareable links for CLI display."""
                links = data.get("shareableLinks", [])
                lines = [f"\nFiles ({len(links)}):"]
                
                for link in links:
                    file_path = link.get("filePath", "")
                    download_url = link.get("downloadUrl", "")
                    expires_in = link.get("expiresIn", 86400)
                    
                    # Truncate long URLs for display
                    display_url = download_url
                    if len(display_url) > 80:
                        display_url = display_url[:77] + "..."
                    
                    lines.append(f"  📄 {file_path}")
                    lines.append(f"     URL: {display_url}")
                    lines.append(f"     Expires: in {expires_in // 3600} hours")
                    lines.append("")
                
                lines.append(f"Total: {len(links)} file(s)")
                if links:
                    lines.append(f"Links expire in: {links[0].get('expiresIn', 86400) // 3600} hours")
                
                return '\n'.join(lines)
            
            output_result(
                result,
                json_output,
                success_message="✓ Shareable links generated successfully!",
                cli_formatter=format_shareable_links
            )
        
        return result
        
    except (FileDownloadError, DownloadError, AssetDownloadError, PreviewNotFoundError,
            AssetNotDistributableError, DownloadTreeError) as e:
        # Handle download-specific business logic errors
        output_error(e, json_output, error_type="Download Error")
        raise click.ClickException(str(e))
    except (AssetNotFoundError, DatabaseNotFoundError) as e:
        # Handle asset/database business logic errors
        output_error(e, json_output, error_type="Resource Not Found")
        raise click.ClickException(str(e))


# Import and register export command from assetsExport module
from .assetsExport import export_command

# Add export command to assets group
assets.add_command(export_command, name='export')
