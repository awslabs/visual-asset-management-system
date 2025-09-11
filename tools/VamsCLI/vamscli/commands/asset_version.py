"""Asset version management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional, List

from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.exceptions import (
    AssetVersionError, AssetVersionNotFoundError, AssetVersionOperationError,
    InvalidAssetVersionDataError, AssetVersionRevertError, AssetNotFoundError,
    DatabaseNotFoundError
)


def parse_json_input(json_input: str) -> Any:
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


def parse_files_input(files_input: str) -> List[Dict[str, Any]]:
    """Parse files input from JSON string or file."""
    try:
        files_data = parse_json_input(files_input)
        
        # Ensure it's a list (use __builtins__ to get the real list type)
        builtin_list = __builtins__['list'] if isinstance(__builtins__, dict) else __builtins__.list
        if type(files_data) is not builtin_list:
            raise click.BadParameter("Files input must be a JSON array")
        
        # Validate each file entry
        builtin_dict = __builtins__['dict'] if isinstance(__builtins__, dict) else __builtins__.dict
        for file_entry in files_data:
            if type(file_entry) is not builtin_dict:
                raise click.BadParameter("Each file entry must be a JSON object")
            
            required_fields = ['relativeKey', 'versionId']
            for field in required_fields:
                if field not in file_entry:
                    raise click.BadParameter(f"File entry missing required field: {field}")
        
        return files_data
        
    except json.JSONDecodeError as e:
        raise click.BadParameter(f"Invalid JSON in files input: {e}")
    except click.BadParameter:
        # Re-raise click.BadParameter exceptions
        raise
    except Exception as e:
        raise click.BadParameter(f"Error parsing files input: {e}")


def format_version_list_output(versions_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format asset versions list for CLI output."""
    if json_output:
        return json.dumps(versions_data, indent=2)
    
    versions = versions_data.get('versions', [])
    
    if not versions:
        return "No versions found for this asset."
    
    output_lines = []
    output_lines.append(f"Asset Versions ({len(versions)} total):")
    output_lines.append("=" * 80)
    
    for version in versions:
        version_id = version.get('Version', 'N/A')
        is_current = version.get('isCurrent', False)
        current_marker = " (CURRENT)" if is_current else ""
        
        output_lines.append(f"Version: {version_id}{current_marker}")
        output_lines.append(f"  Created: {version.get('DateModified', 'N/A')}")
        output_lines.append(f"  Created By: {version.get('createdBy', 'N/A')}")
        output_lines.append(f"  File Count: {version.get('fileCount', 0)}")
        
        comment = version.get('Comment', '')
        if comment:
            output_lines.append(f"  Comment: {comment}")
        
        description = version.get('description', '')
        if description:
            output_lines.append(f"  Description: {description}")
        
        pipelines = version.get('specifiedPipelines', [])
        if pipelines:
            output_lines.append(f"  Pipelines: {', '.join(pipelines)}")
        
        output_lines.append("-" * 80)
    
    # Add pagination info if available
    if versions_data.get('nextToken'):
        output_lines.append("More versions available. Use --starting-token to see additional results.")
    
    return '\n'.join(output_lines)


def format_version_details_output(version_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format asset version details for CLI output."""
    if json_output:
        return json.dumps(version_data, indent=2)
    
    output_lines = []
    output_lines.append("Asset Version Details:")
    output_lines.append("=" * 50)
    output_lines.append(f"Asset ID: {version_data.get('assetId', 'N/A')}")
    output_lines.append(f"Version ID: {version_data.get('assetVersionId', 'N/A')}")
    output_lines.append(f"Created: {version_data.get('dateCreated', 'N/A')}")
    output_lines.append(f"Created By: {version_data.get('createdBy', 'N/A')}")
    
    comment = version_data.get('comment', '')
    if comment:
        output_lines.append(f"Comment: {comment}")
    
    # Files information
    files = version_data.get('files', [])
    output_lines.append(f"\nFiles ({len(files)} total):")
    output_lines.append("-" * 50)
    
    if not files:
        output_lines.append("No files in this version.")
    else:
        for file_info in files:
            relative_key = file_info.get('relativeKey', 'N/A')
            version_id = file_info.get('versionId', 'N/A')
            size = file_info.get('size', 0)
            
            # Format file size
            if size:
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                elif size < 1024 * 1024 * 1024:
                    size_str = f"{size / (1024 * 1024):.1f} MB"
                else:
                    size_str = f"{size / (1024 * 1024 * 1024):.1f} GB"
            else:
                size_str = "Unknown"
            
            output_lines.append(f"  {relative_key}")
            output_lines.append(f"    Version ID: {version_id}")
            output_lines.append(f"    Size: {size_str}")
            output_lines.append(f"    Last Modified: {file_info.get('lastModified', 'N/A')}")
            
            # Status indicators
            status_indicators = []
            if file_info.get('isPermanentlyDeleted'):
                status_indicators.append("PERMANENTLY DELETED")
            if file_info.get('isLatestVersionArchived'):
                status_indicators.append("LATEST VERSION ARCHIVED")
            
            if status_indicators:
                output_lines.append(f"    Status: {', '.join(status_indicators)}")
            
            output_lines.append("")
    
    return '\n'.join(output_lines)


def format_operation_output(operation_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format asset version operation results for CLI output."""
    if json_output:
        return json.dumps(operation_data, indent=2)
    
    output_lines = []
    success = operation_data.get('success', False)
    operation = operation_data.get('operation', 'operation')
    
    if success:
        output_lines.append(
            click.style(f"✓ Asset version {operation} completed successfully!", fg='green', bold=True)
        )
    else:
        output_lines.append(
            click.style(f"✗ Asset version {operation} failed!", fg='red', bold=True)
        )
    
    output_lines.append(f"  Asset ID: {operation_data.get('assetId', 'N/A')}")
    output_lines.append(f"  Version ID: {operation_data.get('assetVersionId', 'N/A')}")
    output_lines.append(f"  Operation: {operation.title()}")
    output_lines.append(f"  Timestamp: {operation_data.get('timestamp', 'N/A')}")
    output_lines.append(f"  Message: {operation_data.get('message', 'N/A')}")
    
    # Show skipped files if any
    skipped_files = operation_data.get('skippedFiles', [])
    if skipped_files:
        output_lines.append(f"\nSkipped Files ({len(skipped_files)}):")
        for file_path in skipped_files:
            output_lines.append(f"  - {file_path}")
        output_lines.append("\nSkipped files may have been permanently deleted or are no longer accessible.")
    
    return '\n'.join(output_lines)


@click.group()
def asset_version():
    """Asset version management commands."""
    pass


@asset_version.command()
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset', required=True, help='Asset ID to create version for')
@click.option('--comment', required=True, help='Comment for the new version')
@click.option('--use-latest-files/--no-use-latest-files', default=True, help='Use latest files in S3 (default: true)')
@click.option('--files', help='JSON string or file path with specific files to version')
@click.option('--json-input', help='JSON input file path or JSON string with complete request data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, database: str, asset: str, comment: str, use_latest_files: bool,
          files: Optional[str], json_input: Optional[str], json_output: bool):
    """
    Create a new asset version.
    
    This command creates a new version of an asset, capturing the current state
    of files or specific file versions. By default, it uses the latest files
    in the S3 bucket.
    
    Examples:
        vamscli asset-version create -d my-db -a my-asset --comment "New version"
        vamscli asset-version create -d my-db -a my-asset --comment "Specific files" --files '[{"relativeKey":"file.obj","versionId":"abc123","isArchived":false}]'
        vamscli asset-version create -d my-db -a my-asset --json-input version-data.json
        vamscli asset-version create -d my-db -a my-asset --comment "Version" --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build request data
        if json_input:
            # Use JSON input
            request_data = parse_json_input(json_input)
        else:
            # Build from individual options
            request_data = {
                'useLatestFiles': use_latest_files,
                'comment': comment
            }
            
            # Handle files input
            if files and not use_latest_files:
                request_data['files'] = parse_files_input(files)
                request_data['useLatestFiles'] = False
            elif files and use_latest_files:
                raise click.BadParameter("Cannot specify --files when --use-latest-files is true")
        
        click.echo(f"Creating new version for asset '{asset}' in database '{database}'...")
        
        # Create the version
        result = api_client.create_asset_version(database, asset, request_data)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(format_operation_output(result))
        
    except AssetNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli assets get -d {database} {asset}' to check if the asset exists.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
    except InvalidAssetVersionDataError as e:
        click.echo(
            click.style(f"✗ Invalid Version Data: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except AssetVersionOperationError as e:
        click.echo(
            click.style(f"✗ Version Creation Failed: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@asset_version.command()
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset', required=True, help='Asset ID to revert')
@click.option('-v', '--version', required=True, help='Version ID to revert to')
@click.option('--comment', help='Comment for the new version created by revert operation')
@click.option('--json-input', help='JSON input file path or JSON string with complete request data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def revert(ctx: click.Context, database: str, asset: str, version: str, comment: Optional[str],
          json_input: Optional[str], json_output: bool):
    """
    Revert an asset to a previous version.
    
    This command reverts an asset to a previous version by creating a new version
    that contains the files from the specified target version. The original
    versions are preserved for audit purposes.
    
    Examples:
        vamscli asset-version revert -d my-db -a my-asset -v 1
        vamscli asset-version revert -d my-db -a my-asset -v 2 --comment "Reverting due to issues"
        vamscli asset-version revert -d my-db -a my-asset -v 1 --json-input revert-data.json
        vamscli asset-version revert -d my-db -a my-asset -v 1 --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build request data
        if json_input:
            # Use JSON input
            request_data = parse_json_input(json_input)
        else:
            # Build from individual options
            request_data = {}
            if comment:
                request_data['comment'] = comment
        
        click.echo(f"Reverting asset '{asset}' in database '{database}' to version '{version}'...")
        
        # Revert the version
        result = api_client.revert_asset_version(database, asset, version, request_data)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(format_operation_output(result))
        
    except AssetNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli assets get -d {database} {asset}' to check if the asset exists.")
        raise click.ClickException(str(e))
    except AssetVersionNotFoundError as e:
        click.echo(
            click.style(f"✗ Version Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli asset-version list -d {database} -a {asset}' to see available versions.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
    except InvalidAssetVersionDataError as e:
        click.echo(
            click.style(f"✗ Invalid Revert Data: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except AssetVersionRevertError as e:
        click.echo(
            click.style(f"✗ Version Revert Failed: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@asset_version.command()
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset', required=True, help='Asset ID to list versions for')
@click.option('--max-items', type=int, default=100, help='Maximum number of versions to return (default: 100)')
@click.option('--starting-token', help='Pagination token for retrieving additional results')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database: str, asset: str, max_items: int, starting_token: Optional[str], json_output: bool):
    """
    List all versions for an asset.
    
    This command retrieves all versions for the specified asset, showing
    version metadata, creation dates, comments, and file counts.
    
    Examples:
        vamscli asset-version list -d my-db -a my-asset
        vamscli asset-version list -d my-db -a my-asset --max-items 50
        vamscli asset-version list -d my-db -a my-asset --starting-token abc123
        vamscli asset-version list -d my-db -a my-asset --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build query parameters
        params = {
            'maxItems': max_items
        }
        if starting_token:
            params['startingToken'] = starting_token
        
        click.echo(f"Retrieving versions for asset '{asset}' in database '{database}'...")
        
        # Get the versions
        result = api_client.get_asset_versions(database, asset, params)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(format_version_list_output(result))
        
    except AssetNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli assets get -d {database} {asset}' to check if the asset exists.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))


@asset_version.command()
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset', required=True, help='Asset ID to get version details for')
@click.option('-v', '--version', required=True, help='Version ID to retrieve details for')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, database: str, asset: str, version: str, json_output: bool):
    """
    Get details for a specific asset version.
    
    This command retrieves detailed information about a specific asset version,
    including all files, their version IDs, sizes, and status information.
    
    Examples:
        vamscli asset-version get -d my-db -a my-asset -v 1
        vamscli asset-version get -d my-db -a my-asset -v 2 --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        click.echo(f"Retrieving details for version '{version}' of asset '{asset}' in database '{database}'...")
        
        # Get the version details
        result = api_client.get_asset_version(database, asset, version)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(format_version_details_output(result))
        
    except AssetNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli assets get -d {database} {asset}' to check if the asset exists.")
        raise click.ClickException(str(e))
    except AssetVersionNotFoundError as e:
        click.echo(
            click.style(f"✗ Version Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo(f"Use 'vamscli asset-version list -d {database} -a {asset}' to see available versions.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        click.echo(
            click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
