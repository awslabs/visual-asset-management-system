"""Asset version management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional, List

from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    AssetVersionError, AssetVersionNotFoundError, AssetVersionOperationError,
    InvalidAssetVersionDataError, AssetVersionRevertError, AssetNotFoundError,
    DatabaseNotFoundError
)


def parse_json_input(json_input: str) -> Any:
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
    
    # Show auto-pagination info if present
    if versions_data.get('autoPaginated'):
        output_lines.append(f"\nAuto-paginated: Retrieved {versions_data.get('totalItems', 0)} items in {versions_data.get('pageCount', 0)} page(s)")
        if versions_data.get('note'):
            output_lines.append(f"⚠️  {versions_data['note']}")
        output_lines.append("")
    
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
    
    # Show nextToken for manual pagination
    if not versions_data.get('autoPaginated') and versions_data.get('NextToken'):
        output_lines.append(f"\nNext token: {versions_data['NextToken']}")
        output_lines.append("Use --starting-token to get the next page")
    
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
    
    # Versioned Metadata information
    versioned_metadata = version_data.get('versionedMetadata', [])
    if versioned_metadata:
        output_lines.append(f"\nVersioned Metadata & Attributes ({len(versioned_metadata)} total):")
        output_lines.append("-" * 50)
        
        # Group by type and filePath
        asset_metadata = [m for m in versioned_metadata if m.get('type') == 'metadata' and m.get('filePath') == '/']
        asset_attributes = [m for m in versioned_metadata if m.get('type') == 'attribute' and m.get('filePath') == '/']
        file_metadata = [m for m in versioned_metadata if m.get('type') == 'metadata' and m.get('filePath') != '/']
        file_attributes = [m for m in versioned_metadata if m.get('type') == 'attribute' and m.get('filePath') != '/']
        
        # Display asset-level metadata
        if asset_metadata:
            output_lines.append("\n  Asset-Level Metadata:")
            for item in asset_metadata:
                key = item.get('metadataKey', 'N/A')
                value = item.get('metadataValue', 'N/A')
                value_type = item.get('metadataValueType', 'N/A')
                output_lines.append(f"    {key}: {value} (type: {value_type})")
        
        # Display asset-level attributes
        if asset_attributes:
            output_lines.append("\n  Asset-Level Attributes:")
            for item in asset_attributes:
                key = item.get('metadataKey', 'N/A')
                value = item.get('metadataValue', 'N/A')
                output_lines.append(f"    {key}: {value}")
        
        # Display file-level metadata grouped by file
        if file_metadata:
            output_lines.append("\n  File-Level Metadata:")
            # Group by filePath
            file_paths = sorted(set(m.get('filePath') for m in file_metadata))
            for file_path in file_paths:
                output_lines.append(f"    {file_path}:")
                file_items = [m for m in file_metadata if m.get('filePath') == file_path]
                for item in file_items:
                    key = item.get('metadataKey', 'N/A')
                    value = item.get('metadataValue', 'N/A')
                    value_type = item.get('metadataValueType', 'N/A')
                    output_lines.append(f"      {key}: {value} (type: {value_type})")
        
        # Display file-level attributes grouped by file
        if file_attributes:
            output_lines.append("\n  File-Level Attributes:")
            # Group by filePath
            file_paths = sorted(set(m.get('filePath') for m in file_attributes))
            for file_path in file_paths:
                output_lines.append(f"    {file_path}:")
                file_items = [m for m in file_attributes if m.get('filePath') == file_path]
                for item in file_items:
                    key = item.get('metadataKey', 'N/A')
                    value = item.get('metadataValue', 'N/A')
                    output_lines.append(f"      {key}: {value}")
    
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
        
        output_status(f"Creating new version for asset '{asset}' in database '{database}'...", json_output)
        
        # Create the version
        result = api_client.create_asset_version(database, asset, request_data)
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset version created successfully!",
            cli_formatter=lambda r: format_operation_output(r, json_output=False)
        )
        
        return result
        
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database} {asset}' to check if the asset exists."
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
    except InvalidAssetVersionDataError as e:
        output_error(e, json_output, error_type="Invalid Version Data")
        raise click.ClickException(str(e))
    except AssetVersionOperationError as e:
        output_error(e, json_output, error_type="Version Creation Failed")
        raise click.ClickException(str(e))


@asset_version.command()
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset', required=True, help='Asset ID to revert')
@click.option('-v', '--version', required=True, help='Version ID to revert to')
@click.option('--comment', help='Comment for the new version created by revert operation')
@click.option('--revert-metadata/--no-revert-metadata', default=False, help='Revert metadata and attributes along with files (default: false)')
@click.option('--json-input', help='JSON input file path or JSON string with complete request data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def revert(ctx: click.Context, database: str, asset: str, version: str, comment: Optional[str],
          revert_metadata: bool, json_input: Optional[str], json_output: bool):
    """
    Revert an asset to a previous version.
    
    This command reverts an asset to a previous version by creating a new version
    that contains the files from the specified target version. By default, only
    file versions are reverted. Use --revert-metadata to also revert metadata
    and attributes to their state in the target version.
    
    Examples:
        vamscli asset-version revert -d my-db -a my-asset -v 1
        vamscli asset-version revert -d my-db -a my-asset -v 2 --comment "Reverting due to issues"
        vamscli asset-version revert -d my-db -a my-asset -v 1 --revert-metadata
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
            if revert_metadata:
                request_data['revertMetadata'] = True
        
        output_status(f"Reverting asset '{asset}' in database '{database}' to version '{version}'...", json_output)
        
        # Revert the version
        result = api_client.revert_asset_version(database, asset, version, request_data)
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset version reverted successfully!",
            cli_formatter=lambda r: format_operation_output(r, json_output=False)
        )
        
        return result
        
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database} {asset}' to check if the asset exists."
        )
        raise click.ClickException(str(e))
    except AssetVersionNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Version Not Found",
            helpful_message=f"Use 'vamscli asset-version list -d {database} -a {asset}' to see available versions."
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
    except InvalidAssetVersionDataError as e:
        output_error(e, json_output, error_type="Invalid Revert Data")
        raise click.ClickException(str(e))
    except AssetVersionRevertError as e:
        output_error(e, json_output, error_type="Version Revert Failed")
        raise click.ClickException(str(e))


@asset_version.command()
@click.option('-d', '--database', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset', required=True, help='Asset ID to list versions for')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database: str, asset: str, page_size: Optional[int], max_items: Optional[int],
         starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """
    List all versions for an asset.
    
    This command retrieves all versions for the specified asset with optional pagination
    and filtering.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli asset-version list -d my-db -a my-asset
        
        # Auto-pagination to fetch all versions (default: up to 10,000)
        vamscli asset-version list -d my-db -a my-asset --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli asset-version list -d my-db -a my-asset --auto-paginate --max-items 5000
        
        # Auto-pagination with custom page size
        vamscli asset-version list -d my-db -a my-asset --auto-paginate --page-size 500
        
        # Manual pagination with page size
        vamscli asset-version list -d my-db -a my-asset --page-size 200
        vamscli asset-version list -d my-db -a my-asset --starting-token "token123" --page-size 200
        
        # JSON output
        vamscli asset-version list -d my-db -a my-asset --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
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
    
    try:
        if auto_paginate:
            # Auto-pagination mode: fetch all items up to max_items (default 10,000)
            max_total_items = max_items or 10000
            output_status(f"Retrieving versions for asset '{asset}' (auto-paginating up to {max_total_items} items)...", json_output)
            
            all_versions = []
            next_token = None
            total_fetched = 0
            page_count = 0
            
            has_more_items = False
            
            while True:
                page_count += 1
                
                # Prepare query parameters for this page
                params = {}
                if page_size:
                    params['pageSize'] = page_size  # Pass pageSize to API
                if next_token:
                    params['startingToken'] = next_token
                
                # Note: maxItems is NOT passed to API - it's CLI-side limit only
                
                # Make API call
                page_result = api_client.get_asset_versions(database, asset, params)
                
                # Get versions from this page
                versions = page_result.get('versions', [])
                
                # Check if adding all versions would exceed limit
                remaining_slots = max_total_items - total_fetched
                if len(versions) > remaining_slots:
                    # Truncate to fit within limit
                    versions = versions[:remaining_slots]
                    all_versions.extend(versions)
                    total_fetched += len(versions)
                    
                    # Check if there are more items available
                    has_more_items = page_result.get('NextToken') is not None
                    
                    # Show progress in CLI mode
                    if not json_output:
                        output_status(f"Fetched {total_fetched} versions (page {page_count})...", False)
                    
                    # We've hit the limit
                    break
                else:
                    # Add all versions from this page
                    all_versions.extend(versions)
                    total_fetched += len(versions)
                    
                    # Show progress in CLI mode
                    if not json_output:
                        output_status(f"Fetched {total_fetched} versions (page {page_count})...", False)
                    
                    # Check if we should continue
                    next_token = page_result.get('NextToken')
                    if not next_token:
                        break
            
            # Create final result
            result = {
                'versions': all_versions,
                'totalItems': len(all_versions),
                'autoPaginated': True,
                'pageCount': page_count
            }
            
            if total_fetched >= max_total_items and has_more_items:
                result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
            
        else:
            # Manual pagination mode: single API call
            output_status(f"Retrieving versions for asset '{asset}' in database '{database}'...", json_output)
            
            # Build pagination parameters
            params = {}
            if page_size:
                params['pageSize'] = page_size  # Pass pageSize to API
            if starting_token:
                params['startingToken'] = starting_token
            
            # Note: maxItems is NOT passed to API in manual mode
            
            # Get the versions
            result = api_client.get_asset_versions(database, asset, params)
        
        output_result(
            result,
            json_output,
            cli_formatter=lambda r: format_version_list_output(r, json_output=False)
        )
        
        return result
        
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database} {asset}' to check if the asset exists."
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
        output_status(f"Retrieving details for version '{version}' of asset '{asset}' in database '{database}'...", json_output)
        
        # Get the version details
        result = api_client.get_asset_version(database, asset, version)
        
        output_result(
            result,
            json_output,
            cli_formatter=lambda r: format_version_details_output(r, json_output=False)
        )
        
        return result
        
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database} {asset}' to check if the asset exists."
        )
        raise click.ClickException(str(e))
    except AssetVersionNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Version Not Found",
            helpful_message=f"Use 'vamscli asset-version list -d {database} -a {asset}' to see available versions."
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