"""Asset links metadata commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional

from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.exceptions import (
    AssetLinkNotFoundError, AssetLinkValidationError, AssetLinkPermissionError
)


def validate_metadata_type(metadata_type: str) -> str:
    """Validate metadata type and return normalized value."""
    valid_types = ['string', 'number', 'boolean', 'date', 'xyz']
    normalized_type = metadata_type.lower()
    
    if normalized_type not in valid_types:
        raise click.BadParameter(
            f"Invalid metadata type '{metadata_type}'. "
            f"Valid types are: {', '.join(valid_types)}"
        )
    
    return normalized_type


def validate_metadata_value(value: str, metadata_type: str) -> str:
    """Validate metadata value based on type."""
    if metadata_type == 'number':
        try:
            float(value)
        except ValueError:
            raise click.BadParameter(f"Value '{value}' is not a valid number")
    
    elif metadata_type == 'boolean':
        if value.lower() not in ['true', 'false']:
            raise click.BadParameter(f"Value '{value}' is not a valid boolean (true/false)")
    
    elif metadata_type == 'date':
        from datetime import datetime
        try:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise click.BadParameter(f"Value '{value}' is not a valid ISO date format")
    
    elif metadata_type == 'xyz':
        try:
            xyz_data = json.loads(value)
            if not isinstance(xyz_data, dict):
                raise ValueError("XYZ data must be a JSON object")
            
            required_keys = {'x', 'y', 'z'}
            if not required_keys.issubset(xyz_data.keys()):
                raise ValueError(f"XYZ data must contain 'x', 'y', and 'z' keys")
            
            for key in required_keys:
                if not isinstance(xyz_data[key], (int, float)):
                    raise ValueError(f"XYZ coordinate '{key}' must be a number")
        
        except (json.JSONDecodeError, ValueError) as e:
            raise click.BadParameter(f"Invalid XYZ data: {e}")
    
    return value


def load_json_input(json_input_file: str) -> Dict[str, Any]:
    """Load and validate JSON input file."""
    try:
        with open(json_input_file, 'r') as f:
            data = json.load(f)
        
        # Validate required fields for metadata operations
        if not isinstance(data, dict):
            raise ValueError("JSON input must be an object")
        
        return data
    
    except FileNotFoundError:
        raise click.ClickException(f"JSON input file '{json_input_file}' not found")
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON in input file: {e}")
    except Exception as e:
        raise click.ClickException(f"Error reading JSON input file: {e}")


def format_metadata_output(metadata_list: list, json_output: bool = False) -> None:
    """Format and display metadata output."""
    if json_output:
        click.echo(json.dumps(metadata_list, indent=2))
        return
    
    if not metadata_list:
        click.echo("No metadata found for this asset link.")
        return
    
    click.echo(f"Asset Link Metadata ({len(metadata_list)} items):")
    click.echo("=" * 50)
    
    for metadata in metadata_list:
        click.echo(f"Key: {metadata.get('metadataKey', 'N/A')}")
        click.echo(f"Value: {metadata.get('metadataValue', 'N/A')}")
        click.echo(f"Type: {metadata.get('metadataValueType', 'N/A')}")
        click.echo("-" * 30)


@click.group()
def asset_links_metadata():
    """Manage metadata for asset links."""
    pass


@asset_links_metadata.command()
@click.argument('asset_link_id', required=True)
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, asset_link_id: str, json_output: bool):
    """
    List all metadata for an asset link.
    
    Get all metadata key-value pairs associated with a specific asset link.
    
    Examples:
        vamscli asset-links-metadata list abc123-def456-ghi789
        vamscli asset-links-metadata list abc123-def456-ghi789 --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        
        # Get metadata
        result = api_client.get_asset_link_metadata(asset_link_id)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            metadata_list = result.get('metadata', [])
            format_metadata_output(metadata_list)
        
    except AssetLinkNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Link Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except AssetLinkPermissionError as e:
        click.echo(
            click.style(f"✗ Permission Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@asset_links_metadata.command()
@click.argument('asset_link_id', required=True)
@click.option('--key', '-k', required=False, help='Metadata key')
@click.option('--value', '-v', required=False, help='Metadata value')
@click.option('--type', '-t', 'metadata_type', default='string', 
              help='Metadata type (string, number, boolean, date, xyz)')
@click.option('--json-input', type=click.Path(),
              help='JSON file containing metadata fields')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, asset_link_id: str, key: Optional[str], 
           value: Optional[str], metadata_type: str, json_input: Optional[str], 
           json_output: bool):
    """
    Create metadata for an asset link.
    
    Add new metadata key-value pairs to an asset link. You can specify the metadata
    directly with --key and --value options, or provide a JSON file with --json-input.
    
    Metadata Types:
        string: Plain text values (default)
        number: Numeric values (integers or floats)
        boolean: Boolean values (true/false)
        date: ISO date format (e.g., 2023-12-01T10:30:00Z)
        xyz: 3D coordinates as JSON (e.g., {"x": 1.5, "y": 2.0, "z": 0.5})
    
    Examples:
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "description" --value "Connection between models"
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "distance" --value "15.5" --type number
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "offset" --value '{"x": 1.5, "y": 2.0, "z": 0.5}' --type xyz
        vamscli asset-links-metadata create abc123-def456-ghi789 --json-input metadata.json
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Validate input method
    if json_input:
        if key or value:
            raise click.ClickException(
                "Cannot use --key/--value options with --json-input. Choose one input method."
            )
        
        # Load from JSON file
        json_data = load_json_input(json_input)
        
        # Validate required fields
        if 'metadataKey' not in json_data:
            raise click.ClickException("JSON input must contain 'metadataKey' field")
        if 'metadataValue' not in json_data:
            raise click.ClickException("JSON input must contain 'metadataValue' field")
        
        metadata_data = {
            'metadataKey': json_data['metadataKey'],
            'metadataValue': str(json_data['metadataValue']),
            'metadataValueType': json_data.get('metadataValueType', 'string').lower()
        }
    else:
        if not key:
            raise click.ClickException("Metadata key is required. Use --key option or --json-input.")
        if not value:
            raise click.ClickException("Metadata value is required. Use --value option or --json-input.")
        
        # Validate metadata type and value
        normalized_type = validate_metadata_type(metadata_type)
        validated_value = validate_metadata_value(value, normalized_type)
        
        metadata_data = {
            'metadataKey': key,
            'metadataValue': validated_value,
            'metadataValueType': normalized_type
        }
    
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        
        # Create metadata
        result = api_client.create_asset_link_metadata(asset_link_id, metadata_data)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset link metadata created successfully!", fg='green', bold=True)
            )
            click.echo(f"Key: {metadata_data['metadataKey']}")
            click.echo(f"Value: {metadata_data['metadataValue']}")
            click.echo(f"Type: {metadata_data['metadataValueType']}")
        
    except AssetLinkNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Link Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except AssetLinkValidationError as e:
        click.echo(
            click.style(f"✗ Validation Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except AssetLinkPermissionError as e:
        click.echo(
            click.style(f"✗ Permission Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@asset_links_metadata.command()
@click.argument('asset_link_id', required=True)
@click.argument('metadata_key', required=True)
@click.option('--value', '-v', required=False, help='New metadata value')
@click.option('--type', '-t', 'metadata_type', default='string',
              help='Metadata type (string, number, boolean, date, xyz)')
@click.option('--json-input', type=click.Path(),
              help='JSON file containing metadata fields')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, asset_link_id: str, metadata_key: str, 
           value: Optional[str], metadata_type: str, json_input: Optional[str], 
           json_output: bool):
    """
    Update metadata for an asset link.
    
    Update existing metadata for a specific key in an asset link. You can specify
    the new value directly with --value option, or provide a JSON file with --json-input.
    
    Examples:
        vamscli asset-links-metadata update abc123-def456-ghi789 description --value "Updated connection info"
        vamscli asset-links-metadata update abc123-def456-ghi789 distance --value "20.0" --type number
        vamscli asset-links-metadata update abc123-def456-ghi789 offset --json-input updated_metadata.json
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Validate input method
    if json_input:
        if value:
            raise click.ClickException(
                "Cannot use --value option with --json-input. Choose one input method."
            )
        
        # Load from JSON file
        json_data = load_json_input(json_input)
        
        # Validate required fields
        if 'metadataValue' not in json_data:
            raise click.ClickException("JSON input must contain 'metadataValue' field")
        
        metadata_data = {
            'metadataValue': str(json_data['metadataValue']),
            'metadataValueType': json_data.get('metadataValueType', 'string').lower()
        }
    else:
        if not value:
            raise click.ClickException("Metadata value is required. Use --value option or --json-input.")
        
        # Validate metadata type and value
        normalized_type = validate_metadata_type(metadata_type)
        validated_value = validate_metadata_value(value, normalized_type)
        
        metadata_data = {
            'metadataValue': validated_value,
            'metadataValueType': normalized_type
        }
    
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        
        # Update metadata
        result = api_client.update_asset_link_metadata(asset_link_id, metadata_key, metadata_data)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset link metadata updated successfully!", fg='green', bold=True)
            )
            click.echo(f"Key: {metadata_key}")
            click.echo(f"New Value: {metadata_data['metadataValue']}")
            click.echo(f"Type: {metadata_data['metadataValueType']}")
        
    except AssetLinkNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Link Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except AssetLinkValidationError as e:
        click.echo(
            click.style(f"✗ Validation Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except AssetLinkPermissionError as e:
        click.echo(
            click.style(f"✗ Permission Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@asset_links_metadata.command()
@click.argument('asset_link_id', required=True)
@click.argument('metadata_key', required=True)
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, asset_link_id: str, metadata_key: str, json_output: bool):
    """
    Delete metadata for an asset link.
    
    Remove a specific metadata key-value pair from an asset link.
    
    Examples:
        vamscli asset-links-metadata delete abc123-def456-ghi789 description
        vamscli asset-links-metadata delete abc123-def456-ghi789 offset --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        
        # Delete metadata
        result = api_client.delete_asset_link_metadata(asset_link_id, metadata_key)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset link metadata deleted successfully!", fg='green', bold=True)
            )
            click.echo(f"Deleted key: {metadata_key}")
        
    except AssetLinkNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Link Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except AssetLinkValidationError as e:
        click.echo(
            click.style(f"✗ Validation Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except AssetLinkPermissionError as e:
        click.echo(
            click.style(f"✗ Permission Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
