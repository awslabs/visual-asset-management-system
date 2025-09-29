"""Asset links metadata commands for VamsCLI."""

import json
import click
import geojson
from typing import Dict, Any, Optional, List
from datetime import datetime

from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.exceptions import (
    AssetLinkNotFoundError, AssetLinkValidationError, AssetLinkPermissionError
)






def validate_metadata_type(metadata_type: str) -> str:
    """Validate metadata type and return normalized value."""
    valid_types = [
        'string', 'number', 'boolean', 'date', 'xyz', 'wxyz', 
        'matrix4x4', 'geopoint', 'geojson', 'lla', 'json'
    ]
    normalized_type = metadata_type.lower()
    
    if normalized_type not in valid_types:
        raise click.BadParameter(
            f"Invalid metadata type '{metadata_type}'. "
            f"Valid types are: {', '.join(valid_types)}"
        )
    
    return normalized_type


def validate_metadata_value(value: str, metadata_type: str) -> str:
    """Validate metadata value based on type."""
    if metadata_type == 'string':
        # String type requires no additional validation
        return value
        
    elif metadata_type == 'number':
        try:
            float(value)
        except ValueError:
            raise click.BadParameter(f"Value '{value}' is not a valid number")
    
    elif metadata_type == 'boolean':
        if value.lower() not in ['true', 'false']:
            raise click.BadParameter(f"Value '{value}' is not a valid boolean (true/false)")
    
    elif metadata_type == 'date':
        try:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            raise click.BadParameter(f"Value '{value}' is not a valid ISO date format")
    
    elif metadata_type == 'json':
        try:
            json.loads(value)
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"Value must be valid JSON: {e}")
    
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
    
    elif metadata_type == 'wxyz':
        try:
            wxyz_data = json.loads(value)
            if not isinstance(wxyz_data, dict):
                raise ValueError("WXYZ data must be a JSON object")
            
            required_keys = {'w', 'x', 'y', 'z'}
            if not required_keys.issubset(wxyz_data.keys()):
                raise ValueError(f"WXYZ data must contain 'w', 'x', 'y', and 'z' keys")
            
            for key in required_keys:
                if not isinstance(wxyz_data[key], (int, float)):
                    raise ValueError(f"WXYZ coordinate '{key}' must be a number")
        
        except (json.JSONDecodeError, ValueError) as e:
            raise click.BadParameter(f"Invalid WXYZ data: {e}")
    
    elif metadata_type == 'matrix4x4':
        try:
            matrix_data = json.loads(value)
            if not hasattr(matrix_data, '__len__') or not hasattr(matrix_data, '__getitem__'):
                raise ValueError("MATRIX4X4 data must be a JSON array")
            
            # Validate 4x4 matrix structure
            if len(matrix_data) != 4:
                raise ValueError("MATRIX4X4 must be a 4x4 matrix (4 rows)")
            
            for i, row in enumerate(matrix_data):
                if not hasattr(row, '__len__') or not hasattr(row, '__getitem__'):
                    raise ValueError(f"MATRIX4X4 row {i} must be an array")
                if len(row) != 4:
                    raise ValueError(f"MATRIX4X4 row {i} must contain exactly 4 elements")
                
                for j, element in enumerate(row):
                    if not isinstance(element, (int, float)):
                        raise ValueError(f"MATRIX4X4 element at [{i}][{j}] must be a number")
        
        except (json.JSONDecodeError, ValueError) as e:
            raise click.BadParameter(f"Invalid MATRIX4X4 data: {e}")
    
    elif metadata_type == 'geopoint':
        try:

            geojson_obj = geojson.loads(value)

            # Additional check that type is "Point"
            json_obj = json.loads(value)
            if json_obj.get('type') != 'Point':
                raise ValueError("GEOPOINT type must be 'Point'")
        
        except (json.JSONDecodeError, ValueError) as e:
            if "GEOPOINT" in str(e):
                raise click.BadParameter(f"Invalid GEOPOINT data: {e}")
            raise click.BadParameter(f"GEOPOINT validation failed: {e}")
    
    elif metadata_type == 'geojson':
        try:

            geojson_obj = geojson.loads(value)
            # geojson.loads() will raise an exception if it's not valid GeoJSON
        
        except (json.JSONDecodeError, ValueError) as e:
            raise click.BadParameter(f"Invalid GeoJSON data: {e}")
    
    elif metadata_type == 'lla':
        try:
            lla_data = json.loads(value)
            if not isinstance(lla_data, dict):
                raise ValueError("LLA data must be a JSON object")
            
            # Check for required keys
            required_keys = {'lat', 'long', 'alt'}
            if not required_keys.issubset(lla_data.keys()):
                raise ValueError("LLA data must contain 'lat', 'long', and 'alt' keys")
            
            # Validate latitude
            lat = lla_data['lat']
            if not isinstance(lat, (int, float)):
                raise ValueError("LLA latitude must be a number")
            if lat < -90 or lat > 90:
                raise ValueError("LLA latitude must be between -90 and 90")
            
            # Validate longitude
            long_val = lla_data['long']
            if not isinstance(long_val, (int, float)):
                raise ValueError("LLA longitude must be a number")
            if long_val < -180 or long_val > 180:
                raise ValueError("LLA longitude must be between -180 and 180")
            
            # Validate altitude
            alt = lla_data['alt']
            if not isinstance(alt, (int, float)):
                raise ValueError("LLA altitude must be a number")
        
        except (json.JSONDecodeError, ValueError) as e:
            raise click.BadParameter(f"Invalid LLA data: {e}")
    
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
              help='Metadata type (string, number, boolean, date, xyz, wxyz, matrix4x4, geopoint, geojson, lla, json)')
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
        json: Any valid JSON object or array
        xyz: 3D coordinates as JSON (e.g., {"x": 1.5, "y": 2.0, "z": 0.5})
        wxyz: Quaternion as JSON (e.g., {"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0})
        matrix4x4: 4x4 transformation matrix as JSON array
        geopoint: GeoJSON Point (e.g., {"type": "Point", "coordinates": [-74.0060, 40.7128]})
        geojson: Any valid GeoJSON object
        lla: Latitude/Longitude/Altitude (e.g., {"lat": 40.7128, "long": -74.0060, "alt": 10.5})
    
    Examples:
        # String metadata (default)
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "description" --value "Connection between models"
        
        # Numeric metadata
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "distance" --value "15.5" --type number
        
        # Boolean metadata
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "active" --value "true" --type boolean
        
        # Date metadata
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "created" --value "2023-12-01T10:30:00Z" --type date
        
        # JSON metadata
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "config" --value '{"enabled": true, "count": 5}' --type json
        
        # XYZ coordinates
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "offset" --value '{"x": 1.5, "y": 2.0, "z": 0.5}' --type xyz
        
        # WXYZ quaternion
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "rotation" --value '{"w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0}' --type wxyz
        
        # 4x4 transformation matrix
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "transform" --value '[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]' --type matrix4x4
        
        # GeoJSON Point
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "location" --value '{"type": "Point", "coordinates": [-74.0060, 40.7128]}' --type geopoint
        
        # GeoJSON Polygon
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "boundary" --value '{"type": "Polygon", "coordinates": [[[-74.1, 40.7], [-74.0, 40.7], [-74.0, 40.8], [-74.1, 40.8], [-74.1, 40.7]]]}' --type geojson
        
        # LLA coordinates
        vamscli asset-links-metadata create abc123-def456-ghi789 --key "position" --value '{"lat": 40.7128, "long": -74.0060, "alt": 10.5}' --type lla
        
        # Using JSON input file
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
              help='Metadata type (string, number, boolean, date, xyz, wxyz, matrix4x4, geopoint, geojson, lla, json)')
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
