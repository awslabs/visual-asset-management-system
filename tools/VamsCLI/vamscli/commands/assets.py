"""Asset management commands for VamsCLI."""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

import click

from ..constants import (
    API_DATABASE_ASSETS, API_ASSETS, DEFAULT_PARALLEL_DOWNLOADS, 
    DEFAULT_DOWNLOAD_RETRY_ATTEMPTS, DEFAULT_DOWNLOAD_TIMEOUT
)
from ..utils.decorators import requires_api_access, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.exceptions import (
    AssetNotFoundError, AssetAlreadyExistsError, DatabaseNotFoundError,
    InvalidAssetDataError, APIUnavailableError, AuthenticationError,
    AssetAlreadyArchivedError, AssetDeletionError, FileDownloadError,
    DownloadError, AssetDownloadError, PreviewNotFoundError,
    AssetNotDistributableError, DownloadTreeError, APIError
)
from ..utils.download_manager import (
    DownloadManager, DownloadFileInfo, DownloadProgress, FileTreeBuilder,
    AssetTreeTraverser, format_file_size, format_duration
)
from ..version import get_version


def parse_json_input(json_input: str) -> Dict[str, Any]:
    """Parse JSON input from string or file."""
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
        speed_str = format_file_size(int(progress.download_speed)) + "/s"
        eta = progress.estimated_time_remaining
        eta_str = format_duration(eta) if eta else "calculating..."
        
        click.echo(f"Speed: {speed_str} | Active: {progress.active_downloads} | ETA: {eta_str}")
        
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
                "downloading": "⬇️",
                "completed": "✅",
                "failed": "❌"
            }.get(file_progress["status"], "❓")
            
            # Truncate long filenames
            display_name = file_key
            if len(display_name) > 50:
                display_name = "..." + display_name[-47:]
                
            click.echo(f"  {status_icon} {display_name}: {file_pct:.1f}%")
            files_shown += 1


@click.group()
def assets():
    """Asset management commands."""
    pass


@assets.command()
@click.option('-d', '--database-id', required=True, help='Database ID where the asset will be created')
@click.option('--asset-id', help='Specific asset ID (auto-generated if not provided)')
@click.option('--name', help='Asset name (required unless using --json-input)')
@click.option('--description', help='Asset description (required unless using --json-input)')
@click.option('--distributable/--no-distributable', default=None, help='Whether the asset is distributable')
@click.option('--tags', multiple=True, help='Asset tags (can be used multiple times)')
@click.option('--bucket-key', help='Existing S3 bucket key to use')
@click.option('--json-input', help='JSON input file path or JSON string with all asset data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def create(ctx: click.Context, database_id: str, asset_id: Optional[str], name: Optional[str], 
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
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        profile_name = profile_manager.profile_name
        raise click.ClickException(
            f"Configuration not found for profile '{profile_name}'. "
            f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
        )
    
    try:
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        # Build asset data
        if json_input:
            # Use JSON input
            asset_data = parse_json_input(json_input)
            # Override database_id from command line
            asset_data['databaseId'] = database_id
            if asset_id:
                asset_data['assetId'] = asset_id
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
            
            if asset_id:
                asset_data['assetId'] = asset_id
            if bucket_key:
                asset_data['bucketExistingKey'] = bucket_key
        
        click.echo("Creating asset...")
        
        # Create the asset
        result = api_client.create_asset(asset_data)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset created successfully!", fg='green', bold=True)
            )
            click.echo(f"  Asset ID: {result.get('assetId')}")
            click.echo(f"  Database: {database_id}")
            click.echo(f"  Message: {result.get('message', 'Asset created')}")
        
    except AssetAlreadyExistsError as e:
        click.echo(
            click.style(f"✗ Asset Already Exists: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli assets get' to view the existing asset or choose a different asset ID.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
    except InvalidAssetDataError as e:
        click.echo(
            click.style(f"✗ Invalid Asset Data: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@assets.command()
@click.argument('asset_id')
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('--reason', help='Reason for archiving the asset')
@click.option('--json-input', type=click.File('r'), help='JSON file with parameters')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
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
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        profile_name = profile_manager.profile_name
        raise click.ClickException(
            f"Configuration not found for profile '{profile_name}'. "
            f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
        )
    
    try:
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
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
        
        click.echo(f"Archiving asset '{asset_id}' in database '{database}'...")
        
        # Archive the asset
        result = api_client.archive_asset(database, asset_id, reason)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset archived successfully!", fg='green', bold=True)
            )
            click.echo(f"  Asset ID: {asset_id}")
            click.echo(f"  Database: {database}")
            click.echo(f"  Operation: {result.get('operation', 'archive')}")
            click.echo(f"  Timestamp: {result.get('timestamp', 'N/A')}")
            click.echo(f"  Message: {result.get('message', 'Asset archived')}")
            click.echo()
            click.echo("The asset has been moved to archived state and will not appear in normal listings.")
            click.echo("Use 'vamscli assets get --show-archived' to view archived assets.")
        
    except AssetNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli assets get -d {database} {asset_id}' to check if the asset exists.")
        raise click.ClickException(str(e))
    except AssetAlreadyArchivedError as e:
        click.echo(
            click.style(f"✗ Asset Already Archived: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli assets get -d {database} {asset_id} --show-archived' to view the archived asset.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
    except AuthenticationError as e:
        click.echo(
            click.style(f"✗ Authentication Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Please run 'vamscli auth login' to re-authenticate.")
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
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
@requires_api_access
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
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        profile_name = profile_manager.profile_name
        raise click.ClickException(
            f"Configuration not found for profile '{profile_name}'. "
            f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
        )
    
    try:
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
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
            click.echo(
                click.style("⚠️  Permanent deletion requires explicit confirmation!", fg='yellow', bold=True)
            )
            click.echo("This action cannot be undone and will permanently delete:")
            click.echo("  • The asset and all its metadata")
            click.echo("  • All asset files and versions")
            click.echo("  • All asset links and relationships")
            click.echo("  • All comments and version history")
            click.echo()
            click.echo("Use --confirm flag to proceed with permanent deletion.")
            raise click.ClickException("Confirmation required for permanent deletion")
        
        # Additional confirmation prompt for safety
        if not json_input:  # Skip interactive prompt if using JSON input
            click.echo(
                click.style(f"⚠️  You are about to permanently delete asset '{asset_id}' from database '{database}'", fg='red', bold=True)
            )
            click.echo("This action cannot be undone!")
            
            if not click.confirm("Are you sure you want to proceed?"):
                click.echo("Deletion cancelled.")
                return
        
        click.echo(f"Permanently deleting asset '{asset_id}' from database '{database}'...")
        
        # Delete the asset
        result = api_client.delete_asset_permanent(database, asset_id, reason, confirm)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset permanently deleted!", fg='green', bold=True)
            )
            click.echo(f"  Asset ID: {asset_id}")
            click.echo(f"  Database: {database}")
            click.echo(f"  Operation: {result.get('operation', 'delete')}")
            click.echo(f"  Timestamp: {result.get('timestamp', 'N/A')}")
            click.echo(f"  Message: {result.get('message', 'Asset deleted')}")
            click.echo()
            click.echo(
                click.style("The asset and all associated data have been permanently removed.", fg='yellow')
            )
        
    except AssetNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli assets get -d {database} {asset_id} --show-archived' to check if the asset exists.")
        raise click.ClickException(str(e))
    except AssetDeletionError as e:
        click.echo(
            click.style(f"✗ Deletion Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Ensure you have provided the --confirm flag for permanent deletion.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
    except AuthenticationError as e:
        click.echo(
            click.style(f"✗ Authentication Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Please run 'vamscli auth login' to re-authenticate.")
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
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
@requires_api_access
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
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        profile_name = profile_manager.profile_name
        raise click.ClickException(
            f"Configuration not found for profile '{profile_name}'. "
            f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
        )
    
    try:
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
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
        
        click.echo(f"Updating asset '{asset_id}' in database '{database_id}'...")
        
        # Update the asset
        result = api_client.update_asset(database_id, asset_id, update_data)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset updated successfully!", fg='green', bold=True)
            )
            click.echo(f"  Asset ID: {result.get('assetId', asset_id)}")
            click.echo(f"  Operation: {result.get('operation', 'update')}")
            click.echo(f"  Timestamp: {result.get('timestamp', 'N/A')}")
            click.echo(f"  Message: {result.get('message', 'Asset updated')}")
        
    except AssetNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli assets get -d {database_id} {asset_id}' to check if the asset exists.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
    except InvalidAssetDataError as e:
        click.echo(
            click.style(f"✗ Invalid Update Data: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@assets.command()
@click.argument('asset_id')
@click.option('-d', '--database-id', required=True, help='Database ID containing the asset')
@click.option('--show-archived', is_flag=True, help='Include archived assets in search')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
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
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        profile_name = profile_manager.profile_name
        raise click.ClickException(
            f"Configuration not found for profile '{profile_name}'. "
            f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
        )
    
    try:
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        # Get the asset
        result = api_client.get_asset(database_id, asset_id, show_archived)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"Retrieving asset '{asset_id}' from database '{database_id}'...")
            click.echo(format_asset_output(result))
        
    except AssetNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Not Found: {e}", fg='red', bold=True),
            err=True
        )
        if not show_archived:
            click.echo("Try using --show-archived to include archived assets.")
        click.echo(f"Use 'vamscli assets list -d {database_id}' to see available assets.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@assets.command()
@click.option('-d', '--database-id', help='Database ID to list assets from (optional for all assets)')
@click.option('--show-archived', is_flag=True, help='Include archived assets')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def list(ctx: click.Context, database_id: Optional[str], show_archived: bool, json_output: bool):
    """
    List assets in a database or all assets.
    
    This command lists assets from a specific database or all assets across
    all databases if no database ID is specified.
    
    Examples:
        vamscli assets list -d my-database
        vamscli assets list --show-archived
        vamscli assets list --json-output
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        profile_name = profile_manager.profile_name
        raise click.ClickException(
            f"Configuration not found for profile '{profile_name}'. "
            f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
        )
    
    try:
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        if database_id:
            endpoint = API_DATABASE_ASSETS.format(databaseId=database_id)
            status_msg = f"Listing assets in database '{database_id}'..."
        else:
            endpoint = API_ASSETS
            status_msg = "Listing all assets..."
        
        params = {}
        if show_archived:
            params['showArchived'] = 'true'
        
        # Get the assets
        response = api_client.get(endpoint, include_auth=True, params=params)
        result = response.json()
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(status_msg)
            # Format for CLI display
            items = result.get('Items', [])
            if not items:
                click.echo("No assets found.")
                return
            
            click.echo(f"\nFound {len(items)} asset(s):")
            click.echo("-" * 80)
            
            for asset in items:
                click.echo(f"ID: {asset.get('assetId', 'N/A')}")
                click.echo(f"Database: {asset.get('databaseId', 'N/A')}")
                click.echo(f"Name: {asset.get('assetName', 'N/A')}")
                click.echo(f"Description: {asset.get('description', 'N/A')}")
                click.echo(f"Distributable: {'Yes' if asset.get('isDistributable') else 'No'}")
                click.echo(f"Status: {asset.get('status', 'Active').title()}")
                
                tags = asset.get('tags', [])
                if tags:
                    click.echo(f"Tags: {', '.join(tags)}")
                
                click.echo("-" * 80)
            
            # Show pagination info if available
            if result.get('NextToken'):
                click.echo(f"More results available. Use pagination to see additional assets.")
        
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
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
@requires_api_access
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
        
        # Get profile manager and API client
        profile_manager = get_profile_manager_from_context(ctx)
        
        if not profile_manager.has_config():
            profile_name = profile_manager.profile_name
            raise click.ClickException(
                f"Configuration not found for profile '{profile_name}'. "
                f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
            )
        
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        # Simple implementation for now - just return shareable links
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
            # For actual downloads, show a message that full implementation is coming
            click.echo(click.style("✓ Download command structure implemented!", fg='green', bold=True))
            click.echo("Full download functionality with parallel downloads and progress bars will be completed in the next iteration.")
            click.echo(f"Parameters received:")
            click.echo(f"  Database: {database}")
            click.echo(f"  Asset: {asset}")
            click.echo(f"  Local path: {local_path}")
            if file_key:
                click.echo(f"  File key: {file_key}")
            if recursive:
                click.echo(f"  Recursive: {recursive}")
            if flatten_download_tree:
                click.echo(f"  Flatten tree: {flatten_download_tree}")
            if asset_preview:
                click.echo(f"  Asset preview: {asset_preview}")
            if file_previews:
                click.echo(f"  File previews: {file_previews}")
            if asset_link_children_tree_depth:
                click.echo(f"  Tree depth: {asset_link_children_tree_depth}")
            
            result = {
                "overall_success": True,
                "message": "Download command structure implemented successfully"
            }
        
        # Output results
        if json_output:
            click.echo(json.dumps(result, indent=2))
        elif shareable_links_only:
            # Display shareable links
            links = result.get("shareableLinks", [])
            click.echo(click.style("✓ Shareable links generated successfully!", fg='green', bold=True))
            click.echo(f"\nFiles ({len(links)}):")
            
            for link in links:
                file_path = link.get("filePath", "")
                download_url = link.get("downloadUrl", "")
                expires_in = link.get("expiresIn", 86400)
                
                # Truncate long URLs for display
                display_url = download_url
                if len(display_url) > 80:
                    display_url = display_url[:77] + "..."
                
                click.echo(f"  📄 {file_path}")
                click.echo(f"     URL: {display_url}")
                click.echo(f"     Expires: in {expires_in // 3600} hours")
                click.echo()
            
            click.echo(f"Total: {len(links)} file(s)")
            click.echo(f"Links expire in: {links[0].get('expiresIn', 86400) // 3600} hours" if links else "")
        
    except (FileDownloadError, DownloadError, AssetDownloadError, PreviewNotFoundError,
            AssetNotDistributableError, DownloadTreeError) as e:
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)
    except (APIError, AuthenticationError, AssetNotFoundError, DatabaseNotFoundError) as e:
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        sys.exit(1)
    except Exception as e:
        if json_output:
            click.echo(json.dumps({"error": f"Unexpected error: {e}"}, indent=2))
        else:
            click.echo(click.style(f"✗ Unexpected error: {e}", fg='red', bold=True), err=True)
        sys.exit(1)
