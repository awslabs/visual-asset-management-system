"""Metadata management commands for VamsCLI."""

import json
import sys
from pathlib import Path
from typing import Dict, Any

import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.exceptions import (
    AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError
)
from ..utils.api_client import APIClient


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


def parse_value(value_str: str) -> Any:
    """Parse a value string, attempting JSON parsing first, then returning as string."""
    if not value_str:
        return value_str
    
    # Try to parse as JSON first (for numbers, booleans, objects, arrays)
    try:
        return json.loads(value_str)
    except json.JSONDecodeError:
        # If JSON parsing fails, return as string
        return value_str


def collect_metadata_interactively() -> Dict[str, Any]:
    """Collect metadata key-value pairs interactively."""
    metadata = {}
    
    click.echo("Enter metadata key-value pairs (values can be JSON objects/arrays):")
    click.echo("Type 'done' when finished, or press Ctrl+C to cancel.")
    
    while True:
        try:
            key = click.prompt("Enter metadata key (or 'done' to finish)", type=str)
            
            if key.lower() == 'done':
                break
                
            if not key.strip():
                click.echo("Key cannot be empty. Please try again.")
                continue
                
            if key in metadata:
                if not click.confirm(f"Key '{key}' already exists. Overwrite?"):
                    continue
            
            value_str = click.prompt(f"Enter value for '{key}' (JSON supported)", type=str)
            metadata[key] = parse_value(value_str)
            
            click.echo(f"Added: {key} = {json.dumps(metadata[key])}")
            
        except click.Abort:
            click.echo("\nOperation cancelled.")
            sys.exit(1)
        except Exception as e:
            click.echo(f"Error processing input: {e}")
            continue
    
    if not metadata:
        click.echo("No metadata entered.")
        
    return metadata


def format_metadata_output(metadata: Dict[str, Any], indent: int = 0) -> str:
    """Format metadata for CLI display."""
    if not metadata:
        return "No metadata found."
    
    lines = []
    prefix = "  " * indent
    
    for key, value in metadata.items():
        if isinstance(value, (dict, list)):
            lines.append(f"{prefix}{key}: {json.dumps(value, indent=2)}")
        else:
            lines.append(f"{prefix}{key}: {value}")
    
    return "\n".join(lines)


@click.group()
def metadata():
    """Metadata management commands."""
    pass


@metadata.command()
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--file-path', help='File path for file-specific metadata')
@click.option('--json-input', help='JSON input with all parameters (file path with @ prefix or JSON string)')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, database_id: str, asset_id: str, file_path: str, json_input: str, json_output: bool):
    """Get metadata for an asset or file.
    
    Examples:
        # Get asset metadata
        vamscli metadata get -d my-db -a my-asset
        
        # Get file-specific metadata
        vamscli metadata get -d my-db -a my-asset --file-path "/models/file.gltf"
        
        # Get with JSON input
        vamscli metadata get --json-input '{"database_id": "my-db", "asset_id": "my-asset"}'
        
        # Get with JSON output
        vamscli metadata get -d my-db -a my-asset --json-output
    """
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
    
    # Get profile manager and API client
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Call API
    try:
        result = api_client.get_metadata(database_id, asset_id, file_path)
    except (AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError) as e:
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        raise click.ClickException(str(e))
    
    # Output results
    if json_output:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(click.style("✓ Metadata retrieved successfully", fg='green', bold=True))
        
        target = f"file '{file_path}'" if file_path else f"asset '{asset_id}'"
        click.echo(f"Target: {target} in database '{database_id}'")
        
        metadata = result.get('metadata', {})
        if metadata:
            click.echo(f"\nMetadata:")
            click.echo(format_metadata_output(metadata, indent=1))
        else:
            click.echo("\nNo metadata found.")


@metadata.command()
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--file-path', help='File path for file-specific metadata')
@click.option('--json-input', help='JSON input with all parameters (file path with @ prefix or JSON string)')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, database_id: str, asset_id: str, file_path: str, json_input: str, json_output: bool):
    """Create metadata for an asset or file.
    
    Examples:
        # Create metadata interactively
        vamscli metadata create -d my-db -a my-asset
        
        # Create file-specific metadata
        vamscli metadata create -d my-db -a my-asset --file-path "/models/file.gltf"
        
        # Create with JSON input
        vamscli metadata create -d my-db -a my-asset --json-input '{"title": "My Asset", "tags": ["3d", "model"]}'
        
        # Create from JSON file
        vamscli metadata create -d my-db -a my-asset --json-input @metadata.json
    """
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
    
    # Get metadata from JSON input or collect interactively
    if json_input and 'metadata' in json_data:
        metadata = json_data['metadata']
    elif json_input:
        # If JSON input provided but no 'metadata' key, treat entire input as metadata
        metadata = {k: v for k, v in json_data.items() 
                   if k not in ['database_id', 'asset_id', 'file_path']}
    else:
        # Collect metadata interactively
        metadata = collect_metadata_interactively()
    
    if not metadata:
        raise click.ClickException("No metadata provided")
    
    # Get profile manager and API client
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Call API
    try:
        result = api_client.create_metadata(database_id, asset_id, metadata, file_path)
    except (AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError) as e:
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        raise click.ClickException(str(e))
    
    # Output results
    if json_output:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(click.style("✓ Metadata created successfully!", fg='green', bold=True))
        
        target = f"file '{file_path}'" if file_path else f"asset '{asset_id}'"
        click.echo(f"Target: {target} in database '{database_id}'")
        
        click.echo(f"\nCreated metadata:")
        click.echo(format_metadata_output(metadata, indent=1))


@metadata.command()
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--file-path', help='File path for file-specific metadata')
@click.option('--json-input', help='JSON input with all parameters (file path with @ prefix or JSON string)')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, database_id: str, asset_id: str, file_path: str, json_input: str, json_output: bool):
    """Update metadata for an asset or file.
    
    Examples:
        # Update metadata interactively
        vamscli metadata update -d my-db -a my-asset
        
        # Update file-specific metadata
        vamscli metadata update -d my-db -a my-asset --file-path "/models/file.gltf"
        
        # Update with JSON input
        vamscli metadata update -d my-db -a my-asset --json-input '{"title": "Updated Asset", "version": 2}'
        
        # Update from JSON file
        vamscli metadata update -d my-db -a my-asset --json-input @updated_metadata.json
    """
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
    
    # Get metadata from JSON input or collect interactively
    if json_input and 'metadata' in json_data:
        metadata = json_data['metadata']
    elif json_input:
        # If JSON input provided but no 'metadata' key, treat entire input as metadata
        metadata = {k: v for k, v in json_data.items() 
                   if k not in ['database_id', 'asset_id', 'file_path']}
    else:
        # Collect metadata interactively
        metadata = collect_metadata_interactively()
    
    if not metadata:
        raise click.ClickException("No metadata provided")
    
    # Get profile manager and API client
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Call API
    try:
        result = api_client.update_metadata(database_id, asset_id, metadata, file_path)
    except (AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError) as e:
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        raise click.ClickException(str(e))
    
    # Output results
    if json_output:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(click.style("✓ Metadata updated successfully!", fg='green', bold=True))
        
        target = f"file '{file_path}'" if file_path else f"asset '{asset_id}'"
        click.echo(f"Target: {target} in database '{database_id}'")
        
        click.echo(f"\nUpdated metadata:")
        click.echo(format_metadata_output(metadata, indent=1))


@metadata.command()
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--file-path', help='File path for file-specific metadata')
@click.option('--json-input', help='JSON input with all parameters (file path with @ prefix or JSON string)')
@click.option('--json-output', is_flag=True, help='Output API response as JSON')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, database_id: str, asset_id: str, file_path: str, json_input: str, json_output: bool):
    """Delete metadata for an asset or file.
    
    Examples:
        # Delete asset metadata
        vamscli metadata delete -d my-db -a my-asset
        
        # Delete file-specific metadata
        vamscli metadata delete -d my-db -a my-asset --file-path "/models/file.gltf"
        
        # Delete with JSON input
        vamscli metadata delete --json-input '{"database_id": "my-db", "asset_id": "my-asset"}'
    """
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
    
    # Confirm deletion
    target = f"file '{file_path}'" if file_path else f"asset '{asset_id}'"
    if not click.confirm(f"Are you sure you want to delete all metadata for {target} in database '{database_id}'?"):
        click.echo("Operation cancelled.")
        return
    
    # Get profile manager and API client
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Call API
    try:
        result = api_client.delete_metadata(database_id, asset_id, file_path)
    except (AssetNotFoundError, DatabaseNotFoundError) as e:
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ {e}", fg='red', bold=True), err=True)
        raise click.ClickException(str(e))
    
    # Output results
    if json_output:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(click.style("✓ Metadata deleted successfully!", fg='green', bold=True))
        click.echo(f"Target: {target} in database '{database_id}'")
