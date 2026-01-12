"""Unified metadata management commands for VamsCLI - All entity types."""

import json
import click
from typing import Dict, Any, Optional, List
from pathlib import Path
from builtins import list as builtin_list  # Avoid namespace collision with 'list' command

from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError, InvalidDatabaseDataError,
    AssetLinkNotFoundError, AssetLinkValidationError, AssetLinkPermissionError
)


#######################
# Helper Functions
#######################

def load_json_input(json_input: str) -> Dict[str, Any]:
    """
    Load JSON input from file or string.
    
    Args:
        json_input: JSON string or file path (with @ prefix)
    
    Returns:
        Parsed JSON data
    
    Raises:
        click.ClickException: If JSON is invalid or file not found
    """
    if not json_input:
        raise click.ClickException("JSON input is required")
    
    # Check if it's a file path (starts with @)
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


def format_metadata_list(metadata_list: List[Dict[str, Any]], entity_type: str) -> str:
    """
    Format metadata list for CLI display.
    
    Args:
        metadata_list: List of metadata items
        entity_type: Entity type for context
    
    Returns:
        Formatted string for display
    """
    if not metadata_list:
        return f"No metadata found for this {entity_type}."
    
    lines = [f"Metadata ({len(metadata_list)} items):", "=" * 60]
    
    for item in metadata_list:
        key = item.get('metadataKey', 'N/A')
        value = item.get('metadataValue', 'N/A')
        value_type = item.get('metadataValueType', 'N/A')
        
        lines.append(f"Key: {key}")
        lines.append(f"Value: {value}")
        lines.append(f"Type: {value_type}")
        
        # Show schema enrichment if available
        if item.get('metadataSchemaField'):
            lines.append(f"Schema Field: Yes")
            if item.get('metadataSchemaRequired'):
                lines.append(f"Required: Yes")
        
        lines.append("-" * 40)
    
    return '\n'.join(lines)


def format_bulk_operation_result(result: Dict[str, Any], operation: str) -> str:
    """
    Format bulk operation result for CLI display.
    
    Args:
        result: BulkOperationResponseModel result
        operation: Operation name (e.g., "updated", "deleted")
    
    Returns:
        Formatted string for display
    """
    lines = []
    lines.append(f"Total Items: {result.get('totalItems', 0)}")
    lines.append(f"Successful: {result.get('successCount', 0)}")
    lines.append(f"Failed: {result.get('failureCount', 0)}")
    
    if result.get('successfulItems'):
        lines.append(f"\nSuccessfully {operation}:")
        for key in result['successfulItems']:
            lines.append(f"  • {key}")
    
    if result.get('failedItems'):
        lines.append(f"\nFailed items:")
        for item in result['failedItems']:
            lines.append(f"  • {item.get('key', 'unknown')}: {item.get('error', 'unknown error')}")
    
    return '\n'.join(lines)


#######################
# Main Metadata Group
#######################

@click.group()
def metadata():
    """Metadata management commands for all entity types."""
    pass


#######################
# Asset Metadata Commands
#######################

@metadata.group()
def asset():
    """Asset metadata commands."""
    pass


@asset.command()
@click.option('-d', '--database-id', required=True, help='Database ID')
@click.option('-a', '--asset-id', required=True, help='Asset ID')
@click.option('--page-size', default=3000, type=int, help='Page size for pagination (default: 3000)')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database_id: str, asset_id: str, page_size: int, starting_token: str, json_output: bool):
    """
    List all metadata for an asset.
    
    Examples:
        vamscli metadata asset list -d my-db -a my-asset
        vamscli metadata asset list -d my-db -a my-asset --json-output
        vamscli metadata asset list -d my-db -a my-asset --page-size 100
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        output_status("Retrieving asset metadata...", json_output)
        
        result = api_client.get_asset_metadata_v2(database_id, asset_id, page_size, starting_token)
        
        def format_list_output(data):
            metadata_list = data.get('metadata', [])
            return format_metadata_list(metadata_list, 'asset')
        
        output_result(result, json_output, cli_formatter=format_list_output)
        return result
        
    except (AssetNotFoundError, DatabaseNotFoundError) as e:
        output_error(
            e,
            json_output,
            error_type=e.__class__.__name__.replace('Error', ''),
            helpful_message="Verify the database and asset IDs are correct."
        )
        raise click.ClickException(str(e))


@asset.command()
@click.option('-d', '--database-id', required=True, help='Database ID')
@click.option('-a', '--asset-id', required=True, help='Asset ID')
@click.option('--json-input', required=True, help='JSON input file (with @ prefix) or JSON string')
@click.option('--update-type', type=click.Choice(['update', 'replace_all']), default='update',
              help='Update type: update (upsert) or replace_all (replace all metadata)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, database_id: str, asset_id: str, json_input: str, update_type: str, json_output: bool):
    """
    Create or update metadata for an asset (bulk operation).
    
    JSON Input Format:
        {
          "metadata": [
            {
              "metadataKey": "key1",
              "metadataValue": "value1",
              "metadataValueType": "string"
            }
          ]
        }
    
    Update Types:
        - update: Upsert provided metadata (create or update, keep unlisted keys)
        - replace_all: Replace ALL metadata (delete unlisted keys, upsert provided)
    
    Examples:
        # Update metadata (upsert mode)
        vamscli metadata asset update -d my-db -a my-asset --json-input @metadata.json
        
        # Replace all metadata
        vamscli metadata asset update -d my-db -a my-asset --json-input @metadata.json --update-type replace_all
        
        # Inline JSON
        vamscli metadata asset update -d my-db -a my-asset --json-input '{"metadata":[{"metadataKey":"title","metadataValue":"My Asset","metadataValueType":"string"}]}'
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Load and validate JSON input
        json_data = load_json_input(json_input)
        
        if 'metadata' not in json_data:
            raise click.ClickException("JSON input must contain 'metadata' array")
        
        metadata_items = json_data['metadata']
        if not isinstance(metadata_items, builtin_list) or not metadata_items:
            raise click.ClickException("'metadata' must be a non-empty array")
        
        output_status(f"Updating asset metadata (mode: {update_type})...", json_output)
        
        result = api_client.update_asset_metadata_v2(database_id, asset_id, metadata_items, update_type)
        
        def format_update_output(data):
            return format_bulk_operation_result(data, 'updated')
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset metadata updated successfully!",
            cli_formatter=format_update_output
        )
        
        return result
        
    except (AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError) as e:
        output_error(
            e,
            json_output,
            error_type="Metadata Error",
            helpful_message="Verify the database and asset IDs are correct and metadata format is valid."
        )
        raise click.ClickException(str(e))


@asset.command()
@click.option('-d', '--database-id', required=True, help='Database ID')
@click.option('-a', '--asset-id', required=True, help='Asset ID')
@click.option('--json-input', required=True, help='JSON input file (with @ prefix) or JSON string')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, database_id: str, asset_id: str, json_input: str, json_output: bool):
    """
    Delete metadata for an asset (bulk operation).
    
    JSON Input Format:
        {
          "metadataKeys": ["key1", "key2", "key3"]
        }
    
    Examples:
        vamscli metadata asset delete -d my-db -a my-asset --json-input @delete-keys.json
        vamscli metadata asset delete -d my-db -a my-asset --json-input '{"metadataKeys":["old_field","deprecated"]}'
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Load and validate JSON input
        json_data = load_json_input(json_input)
        
        if 'metadataKeys' not in json_data:
            raise click.ClickException("JSON input must contain 'metadataKeys' array")
        
        metadata_keys = json_data['metadataKeys']
        if not isinstance(metadata_keys, builtin_list) or not metadata_keys:
            raise click.ClickException("'metadataKeys' must be a non-empty array")
        
        output_status("Deleting asset metadata...", json_output)
        
        result = api_client.delete_asset_metadata_v2(database_id, asset_id, metadata_keys)
        
        def format_delete_output(data):
            return format_bulk_operation_result(data, 'deleted')
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset metadata deleted successfully!",
            cli_formatter=format_delete_output
        )
        
        return result
        
    except (AssetNotFoundError, DatabaseNotFoundError) as e:
        output_error(
            e,
            json_output,
            error_type="Metadata Error",
            helpful_message="Verify the database and asset IDs are correct."
        )
        raise click.ClickException(str(e))


#######################
# File Metadata Commands
#######################

@metadata.group()
def file():
    """File metadata and attribute commands."""
    pass


@file.command()
@click.option('-d', '--database-id', required=True, help='Database ID')
@click.option('-a', '--asset-id', required=True, help='Asset ID')
@click.option('--file-path', required=True, help='Relative file path')
@click.option('--type', 'metadata_type', type=click.Choice(['metadata', 'attribute']), required=True,
              help='Type: metadata or attribute')
@click.option('--page-size', default=3000, type=int, help='Page size for pagination (default: 3000)')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database_id: str, asset_id: str, file_path: str, metadata_type: str, 
         page_size: int, starting_token: str, json_output: bool):
    """
    List all metadata or attributes for a file.
    
    Examples:
        vamscli metadata file list -d my-db -a my-asset --file-path "models/file.gltf" --type metadata
        vamscli metadata file list -d my-db -a my-asset --file-path "models/file.gltf" --type attribute --json-output
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        output_status(f"Retrieving file {metadata_type}...", json_output)
        
        result = api_client.get_file_metadata_v2(database_id, asset_id, file_path, metadata_type, page_size, starting_token)
        
        def format_list_output(data):
            metadata_list = data.get('metadata', [])
            return format_metadata_list(metadata_list, f'file {metadata_type}')
        
        output_result(result, json_output, cli_formatter=format_list_output)
        return result
        
    except (AssetNotFoundError, DatabaseNotFoundError) as e:
        output_error(
            e,
            json_output,
            error_type="File Metadata Error",
            helpful_message="Verify the database, asset, and file path are correct."
        )
        raise click.ClickException(str(e))


@file.command()
@click.option('-d', '--database-id', required=True, help='Database ID')
@click.option('-a', '--asset-id', required=True, help='Asset ID')
@click.option('--file-path', required=True, help='Relative file path')
@click.option('--type', 'metadata_type', type=click.Choice(['metadata', 'attribute']), required=True,
              help='Type: metadata or attribute')
@click.option('--json-input', required=True, help='JSON input file (with @ prefix) or JSON string')
@click.option('--update-type', type=click.Choice(['update', 'replace_all']), default='update',
              help='Update type: update (upsert) or replace_all (replace all metadata)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, database_id: str, asset_id: str, file_path: str, metadata_type: str,
           json_input: str, update_type: str, json_output: bool):
    """
    Create or update metadata/attributes for a file (bulk operation).
    
    JSON Input Format:
        {
          "metadata": [
            {
              "metadataKey": "key1",
              "metadataValue": "value1",
              "metadataValueType": "string"
            }
          ]
        }
    
    Note: File attributes only support 'string' metadataValueType.
    
    Examples:
        vamscli metadata file update -d my-db -a my-asset --file-path "models/file.gltf" --type metadata --json-input @metadata.json
        vamscli metadata file update -d my-db -a my-asset --file-path "models/file.gltf" --type attribute --json-input @attributes.json --update-type replace_all
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Load and validate JSON input
        json_data = load_json_input(json_input)
        
        if 'metadata' not in json_data:
            raise click.ClickException("JSON input must contain 'metadata' array")
        
        metadata_items = json_data['metadata']
        if not isinstance(metadata_items, builtin_list) or not metadata_items:
            raise click.ClickException("'metadata' must be a non-empty array")
        
        output_status(f"Updating file {metadata_type} (mode: {update_type})...", json_output)
        
        result = api_client.update_file_metadata_v2(database_id, asset_id, file_path, metadata_type, metadata_items, update_type)
        
        def format_update_output(data):
            return format_bulk_operation_result(data, 'updated')
        
        output_result(
            result,
            json_output,
            success_message=f"✓ File {metadata_type} updated successfully!",
            cli_formatter=format_update_output
        )
        
        return result
        
    except (AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError) as e:
        output_error(
            e,
            json_output,
            error_type="File Metadata Error",
            helpful_message="Verify the database, asset, file path, and metadata format are correct."
        )
        raise click.ClickException(str(e))


@file.command()
@click.option('-d', '--database-id', required=True, help='Database ID')
@click.option('-a', '--asset-id', required=True, help='Asset ID')
@click.option('--file-path', required=True, help='Relative file path')
@click.option('--type', 'metadata_type', type=click.Choice(['metadata', 'attribute']), required=True,
              help='Type: metadata or attribute')
@click.option('--json-input', required=True, help='JSON input file (with @ prefix) or JSON string')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, database_id: str, asset_id: str, file_path: str, metadata_type: str,
           json_input: str, json_output: bool):
    """
    Delete metadata/attributes for a file (bulk operation).
    
    JSON Input Format:
        {
          "metadataKeys": ["key1", "key2", "key3"]
        }
    
    Examples:
        vamscli metadata file delete -d my-db -a my-asset --file-path "models/file.gltf" --type metadata --json-input @delete-keys.json
        vamscli metadata file delete -d my-db -a my-asset --file-path "models/file.gltf" --type attribute --json-input '{"metadataKeys":["old_attr"]}'
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Load and validate JSON input
        json_data = load_json_input(json_input)
        
        if 'metadataKeys' not in json_data:
            raise click.ClickException("JSON input must contain 'metadataKeys' array")
        
        metadata_keys = json_data['metadataKeys']
        if not isinstance(metadata_keys, builtin_list) or not metadata_keys:
            raise click.ClickException("'metadataKeys' must be a non-empty array")
        
        output_status(f"Deleting file {metadata_type}...", json_output)
        
        result = api_client.delete_file_metadata_v2(database_id, asset_id, file_path, metadata_type, metadata_keys)
        
        def format_delete_output(data):
            return format_bulk_operation_result(data, 'deleted')
        
        output_result(
            result,
            json_output,
            success_message=f"✓ File {metadata_type} deleted successfully!",
            cli_formatter=format_delete_output
        )
        
        return result
        
    except (AssetNotFoundError, DatabaseNotFoundError) as e:
        output_error(
            e,
            json_output,
            error_type="File Metadata Error",
            helpful_message="Verify the database, asset, and file path are correct."
        )
        raise click.ClickException(str(e))


#######################
# Asset Link Metadata Commands
#######################

@metadata.group()
def asset_link():
    """Asset link metadata commands."""
    pass


@asset_link.command()
@click.option('--asset-link-id', required=True, help='Asset link ID')
@click.option('--page-size', default=3000, type=int, help='Page size for pagination (default: 3000)')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, asset_link_id: str, page_size: int, starting_token: str, json_output: bool):
    """
    List all metadata for an asset link.
    
    Examples:
        vamscli metadata asset-link list --asset-link-id abc123-def456-ghi789
        vamscli metadata asset-link list --asset-link-id abc123-def456-ghi789 --json-output
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        output_status("Retrieving asset link metadata...", json_output)
        
        result = api_client.get_asset_link_metadata_v2(asset_link_id, page_size, starting_token)
        
        def format_list_output(data):
            metadata_list = data.get('metadata', [])
            return format_metadata_list(metadata_list, 'asset link')
        
        output_result(result, json_output, cli_formatter=format_list_output)
        return result
        
    except (AssetLinkNotFoundError, AssetLinkPermissionError) as e:
        output_error(
            e,
            json_output,
            error_type="Asset Link Metadata Error",
            helpful_message="Verify the asset link ID is correct and you have permission to access it."
        )
        raise click.ClickException(str(e))


@asset_link.command()
@click.option('--asset-link-id', required=True, help='Asset link ID')
@click.option('--json-input', required=True, help='JSON input file (with @ prefix) or JSON string')
@click.option('--update-type', type=click.Choice(['update', 'replace_all']), default='update',
              help='Update type: update (upsert) or replace_all (replace all metadata)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, asset_link_id: str, json_input: str, update_type: str, json_output: bool):
    """
    Create or update metadata for an asset link (bulk operation).
    
    JSON Input Format:
        {
          "metadata": [
            {
              "metadataKey": "key1",
              "metadataValue": "value1",
              "metadataValueType": "string"
            }
          ]
        }
    
    Examples:
        vamscli metadata asset-link update --asset-link-id abc123 --json-input @metadata.json
        vamscli metadata asset-link update --asset-link-id abc123 --json-input @metadata.json --update-type replace_all
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Load and validate JSON input
        json_data = load_json_input(json_input)
        
        if 'metadata' not in json_data:
            raise click.ClickException("JSON input must contain 'metadata' array")
        
        metadata_items = json_data['metadata']
        if not isinstance(metadata_items, builtin_list) or not metadata_items:
            raise click.ClickException("'metadata' must be a non-empty array")
        
        output_status(f"Updating asset link metadata (mode: {update_type})...", json_output)
        
        result = api_client.update_asset_link_metadata_v2(asset_link_id, metadata_items, update_type)
        
        def format_update_output(data):
            return format_bulk_operation_result(data, 'updated')
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset link metadata updated successfully!",
            cli_formatter=format_update_output
        )
        
        return result
        
    except (AssetLinkNotFoundError, AssetLinkValidationError, AssetLinkPermissionError) as e:
        output_error(
            e,
            json_output,
            error_type="Asset Link Metadata Error",
            helpful_message="Verify the asset link ID is correct and metadata format is valid."
        )
        raise click.ClickException(str(e))


@asset_link.command()
@click.option('--asset-link-id', required=True, help='Asset link ID')
@click.option('--json-input', required=True, help='JSON input file (with @ prefix) or JSON string')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, asset_link_id: str, json_input: str, json_output: bool):
    """
    Delete metadata for an asset link (bulk operation).
    
    JSON Input Format:
        {
          "metadataKeys": ["key1", "key2", "key3"]
        }
    
    Examples:
        vamscli metadata asset-link delete --asset-link-id abc123 --json-input @delete-keys.json
        vamscli metadata asset-link delete --asset-link-id abc123 --json-input '{"metadataKeys":["old_field"]}'
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Load and validate JSON input
        json_data = load_json_input(json_input)
        
        if 'metadataKeys' not in json_data:
            raise click.ClickException("JSON input must contain 'metadataKeys' array")
        
        metadata_keys = json_data['metadataKeys']
        if not isinstance(metadata_keys, builtin_list) or not metadata_keys:
            raise click.ClickException("'metadataKeys' must be a non-empty array")
        
        output_status("Deleting asset link metadata...", json_output)
        
        result = api_client.delete_asset_link_metadata_v2(asset_link_id, metadata_keys)
        
        def format_delete_output(data):
            return format_bulk_operation_result(data, 'deleted')
        
        output_result(
            result,
            json_output,
            success_message="✓ Asset link metadata deleted successfully!",
            cli_formatter=format_delete_output
        )
        
        return result
        
    except (AssetLinkNotFoundError, AssetLinkPermissionError) as e:
        output_error(
            e,
            json_output,
            error_type="Asset Link Metadata Error",
            helpful_message="Verify the asset link ID is correct and you have permission to delete metadata."
        )
        raise click.ClickException(str(e))


#######################
# Database Metadata Commands
#######################

@metadata.group()
def database():
    """Database metadata commands."""
    pass


@database.command()
@click.option('-d', '--database-id', required=True, help='Database ID')
@click.option('--page-size', default=3000, type=int, help='Page size for pagination (default: 3000)')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database_id: str, page_size: int, starting_token: str, json_output: bool):
    """
    List all metadata for a database.
    
    Examples:
        vamscli metadata database list -d my-db
        vamscli metadata database list -d my-db --json-output
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        output_status("Retrieving database metadata...", json_output)
        
        result = api_client.get_database_metadata_v2(database_id, page_size, starting_token)
        
        def format_list_output(data):
            metadata_list = data.get('metadata', [])
            return format_metadata_list(metadata_list, 'database')
        
        output_result(result, json_output, cli_formatter=format_list_output)
        return result
        
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Error",
            helpful_message="Verify the database ID is correct."
        )
        raise click.ClickException(str(e))


@database.command()
@click.option('-d', '--database-id', required=True, help='Database ID')
@click.option('--json-input', required=True, help='JSON input file (with @ prefix) or JSON string')
@click.option('--update-type', type=click.Choice(['update', 'replace_all']), default='update',
              help='Update type: update (upsert) or replace_all (replace all metadata)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, database_id: str, json_input: str, update_type: str, json_output: bool):
    """
    Create or update metadata for a database (bulk operation).
    
    JSON Input Format:
        {
          "metadata": [
            {
              "metadataKey": "key1",
              "metadataValue": "value1",
              "metadataValueType": "string"
            }
          ]
        }
    
    Examples:
        vamscli metadata database update -d my-db --json-input @metadata.json
        vamscli metadata database update -d my-db --json-input @metadata.json --update-type replace_all
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Load and validate JSON input
        json_data = load_json_input(json_input)
        
        if 'metadata' not in json_data:
            raise click.ClickException("JSON input must contain 'metadata' array")
        
        metadata_items = json_data['metadata']
        if not isinstance(metadata_items, builtin_list) or not metadata_items:
            raise click.ClickException("'metadata' must be a non-empty array")
        
        output_status(f"Updating database metadata (mode: {update_type})...", json_output)
        
        result = api_client.update_database_metadata_v2(database_id, metadata_items, update_type)
        
        def format_update_output(data):
            return format_bulk_operation_result(data, 'updated')
        
        output_result(
            result,
            json_output,
            success_message="✓ Database metadata updated successfully!",
            cli_formatter=format_update_output
        )
        
        return result
        
    except (DatabaseNotFoundError, InvalidDatabaseDataError) as e:
        output_error(
            e,
            json_output,
            error_type="Database Metadata Error",
            helpful_message="Verify the database ID is correct and metadata format is valid."
        )
        raise click.ClickException(str(e))


@database.command()
@click.option('-d', '--database-id', required=True, help='Database ID')
@click.option('--json-input', required=True, help='JSON input file (with @ prefix) or JSON string')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, database_id: str, json_input: str, json_output: bool):
    """
    Delete metadata for a database (bulk operation).
    
    JSON Input Format:
        {
          "metadataKeys": ["key1", "key2", "key3"]
        }
    
    Examples:
        vamscli metadata database delete -d my-db --json-input @delete-keys.json
        vamscli metadata database delete -d my-db --json-input '{"metadataKeys":["old_field","deprecated"]}'
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Load and validate JSON input
        json_data = load_json_input(json_input)
        
        if 'metadataKeys' not in json_data:
            raise click.ClickException("JSON input must contain 'metadataKeys' array")
        
        metadata_keys = json_data['metadataKeys']
        if not isinstance(metadata_keys, builtin_list) or not metadata_keys:
            raise click.ClickException("'metadataKeys' must be a non-empty array")
        
        output_status("Deleting database metadata...", json_output)
        
        result = api_client.delete_database_metadata_v2(database_id, metadata_keys)
        
        def format_delete_output(data):
            return format_bulk_operation_result(data, 'deleted')
        
        output_result(
            result,
            json_output,
            success_message="✓ Database metadata deleted successfully!",
            cli_formatter=format_delete_output
        )
        
        return result
        
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Error",
            helpful_message="Verify the database ID is correct."
        )
        raise click.ClickException(str(e))