"""Asset links management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional, List

from ..utils.api_client import APIClient
from ..utils.decorators import requires_api_access, get_profile_manager_from_context
from ..utils.exceptions import (
    AssetLinkError, AssetLinkNotFoundError, AssetLinkValidationError, 
    AssetLinkPermissionError, CycleDetectionError, AssetLinkAlreadyExistsError,
    InvalidRelationshipTypeError, AssetLinkOperationError, AssetNotFoundError,
    DatabaseNotFoundError, AuthenticationError, APIUnavailableError
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


def validate_relationship_type(relationship_type: str) -> str:
    """Validate and normalize relationship type."""
    valid_types = ['related', 'parentChild']
    if relationship_type not in valid_types:
        raise InvalidRelationshipTypeError(
            f"Invalid relationship type '{relationship_type}'. Must be one of: {', '.join(valid_types)}"
        )
    return relationship_type


def format_asset_link_output(link_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format asset link data for CLI output."""
    if json_output:
        return json.dumps(link_data, indent=2)
    
    # Extract asset link from response
    asset_link = link_data.get('assetLink', link_data)
    
    # CLI-friendly formatting
    output_lines = []
    output_lines.append("Asset Link Details:")
    output_lines.append(f"  Link ID: {asset_link.get('assetLinkId', 'N/A')}")
    output_lines.append(f"  Relationship Type: {asset_link.get('relationshipType', 'N/A')}")
    output_lines.append("")
    output_lines.append("  From Asset:")
    output_lines.append(f"    Asset ID: {asset_link.get('fromAssetId', 'N/A')}")
    output_lines.append(f"    Database ID: {asset_link.get('fromAssetDatabaseId', 'N/A')}")
    output_lines.append("")
    output_lines.append("  To Asset:")
    output_lines.append(f"    Asset ID: {asset_link.get('toAssetId', 'N/A')}")
    output_lines.append(f"    Database ID: {asset_link.get('toAssetDatabaseId', 'N/A')}")
    
    # Tags
    tags = asset_link.get('tags', [])
    if tags:
        output_lines.append(f"  Tags: {', '.join(tags)}")
    else:
        output_lines.append("  Tags: None")
    
    return '\n'.join(output_lines)


def format_asset_links_list_output(links_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format asset links list data for CLI output."""
    if json_output:
        return json.dumps(links_data, indent=2)
    
    output_lines = []
    
    # Related assets
    related = links_data.get('related', [])
    output_lines.append(f"Related Assets ({len(related)}):")
    if related:
        for asset in related:
            link_id_short = asset.get('assetLinkId', 'N/A')[:8] + '...' if asset.get('assetLinkId') else 'N/A'
            output_lines.append(f"  • {asset.get('assetName', asset.get('assetId', 'N/A'))} ({asset.get('databaseId', 'N/A')}) - Link ID: {link_id_short}")
    else:
        output_lines.append("  None")
    
    output_lines.append("")
    
    # Parent assets
    parents = links_data.get('parents', [])
    output_lines.append(f"Parent Assets ({len(parents)}):")
    if parents:
        for asset in parents:
            link_id_short = asset.get('assetLinkId', 'N/A')[:8] + '...' if asset.get('assetLinkId') else 'N/A'
            output_lines.append(f"  • {asset.get('assetName', asset.get('assetId', 'N/A'))} ({asset.get('databaseId', 'N/A')}) - Link ID: {link_id_short}")
    else:
        output_lines.append("  None")
    
    output_lines.append("")
    
    # Child assets
    children = links_data.get('children', [])
    output_lines.append(f"Child Assets ({len(children)}):")
    if children:
        # Check if it's tree view (children have nested structure)
        if children and isinstance(children[0], dict) and 'children' in children[0]:
            # Tree view formatting
            def format_tree_node(node, indent=2):
                lines = []
                link_id_short = node.get('assetLinkId', 'N/A')[:8] + '...' if node.get('assetLinkId') else 'N/A'
                lines.append(f"{'  ' * indent}• {node.get('assetName', node.get('assetId', 'N/A'))} ({node.get('databaseId', 'N/A')}) - Link ID: {link_id_short}")
                
                for child in node.get('children', []):
                    lines.extend(format_tree_node(child, indent + 1))
                
                return lines
            
            for child in children:
                output_lines.extend(format_tree_node(child))
        else:
            # Flat list formatting
            for asset in children:
                link_id_short = asset.get('assetLinkId', 'N/A')[:8] + '...' if asset.get('assetLinkId') else 'N/A'
                output_lines.append(f"  • {asset.get('assetName', asset.get('assetId', 'N/A'))} ({asset.get('databaseId', 'N/A')}) - Link ID: {link_id_short}")
    else:
        output_lines.append("  None")
    
    # Unauthorized counts
    unauthorized = links_data.get('unauthorizedCounts', {})
    if any(unauthorized.get(key, 0) > 0 for key in ['related', 'parents', 'children']):
        output_lines.append("")
        output_lines.append("Unauthorized Assets:")
        if unauthorized.get('related', 0) > 0:
            output_lines.append(f"  Related: {unauthorized['related']}")
        if unauthorized.get('parents', 0) > 0:
            output_lines.append(f"  Parents: {unauthorized['parents']}")
        if unauthorized.get('children', 0) > 0:
            output_lines.append(f"  Children: {unauthorized['children']}")
    
    return '\n'.join(output_lines)


@click.group()
def asset_links():
    """Asset links management commands."""
    pass


@asset_links.command()
@click.option('--from-asset-id', required=True, help='Source asset ID')
@click.option('--from-database-id', required=True, help='Source asset database ID')
@click.option('--to-asset-id', required=True, help='Target asset ID')
@click.option('--to-database-id', required=True, help='Target asset database ID')
@click.option('--relationship-type', required=True, 
              type=click.Choice(['related', 'parentChild'], case_sensitive=False),
              help='Type of relationship (related or parentChild)')
@click.option('--tags', multiple=True, help='Tags for the asset link (can be used multiple times)')
@click.option('--json-input', help='JSON input file path or JSON string with all asset link data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def create(ctx: click.Context, from_asset_id: str, from_database_id: str, 
          to_asset_id: str, to_database_id: str, relationship_type: str,
          tags: List[str], json_input: Optional[str], json_output: bool):
    """
    Create a new asset link between two assets.
    
    This command creates a relationship between two assets. The relationship can be
    'related' (bidirectional) or 'parentChild' (directional with cycle detection).
    
    Examples:
        vamscli asset-links create --from-asset-id asset1 --from-database-id db1 --to-asset-id asset2 --to-database-id db2 --relationship-type related
        vamscli asset-links create --from-asset-id parent --from-database-id db1 --to-asset-id child --to-database-id db1 --relationship-type parentChild --tags tag1 --tags tag2
        vamscli asset-links create --json-input '{"fromAssetId":"asset1","fromAssetDatabaseId":"db1","toAssetId":"asset2","toAssetDatabaseId":"db2","relationshipType":"related"}'
    """
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
        
        # Build asset link data
        if json_input:
            # Use JSON input
            link_data = parse_json_input(json_input)
        else:
            # Build from individual options
            link_data = {
                'fromAssetId': from_asset_id,
                'fromAssetDatabaseId': from_database_id,
                'toAssetId': to_asset_id,
                'toAssetDatabaseId': to_database_id,
                'relationshipType': validate_relationship_type(relationship_type),
                'tags': parse_tags_input(__builtins__['list'](tags))
            }
        
        if not json_output:
            click.echo("Creating asset link...")
        
        # Create the asset link
        result = api_client.create_asset_link(link_data)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset link created successfully!", fg='green', bold=True)
            )
            click.echo(f"  Asset Link ID: {result.get('assetLinkId')}")
            click.echo(f"  Relationship: {link_data.get('relationshipType')}")
            click.echo(f"  From: {link_data.get('fromAssetId')} ({link_data.get('fromAssetDatabaseId')}) → To: {link_data.get('toAssetId')} ({link_data.get('toAssetDatabaseId')})")
            if link_data.get('tags'):
                click.echo(f"  Tags: {', '.join(link_data.get('tags'))}")
            click.echo(f"  Message: {result.get('message', 'Asset link created')}")
        
    except AssetLinkAlreadyExistsError as e:
        click.echo(
            click.style(f"✗ Asset Link Already Exists: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("A relationship already exists between these assets.")
        raise click.ClickException(str(e))
    except CycleDetectionError as e:
        click.echo(
            click.style(f"✗ Cycle Detection Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Creating this parent-child relationship would create a cycle in the asset hierarchy.")
        raise click.ClickException(str(e))
    except AssetNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Ensure both assets exist before creating a link between them.")
        raise click.ClickException(str(e))
    except AssetLinkPermissionError as e:
        click.echo(
            click.style(f"✗ Permission Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("You need permissions on both assets to create a link between them.")
        raise click.ClickException(str(e))
    except AssetLinkValidationError as e:
        click.echo(
            click.style(f"✗ Validation Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except InvalidRelationshipTypeError as e:
        click.echo(
            click.style(f"✗ Invalid Relationship Type: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@asset_links.command()
@click.option('--asset-link-id', required=True, help='Asset link ID to retrieve')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def get(ctx: click.Context, asset_link_id: str, json_output: bool):
    """
    Get details for a specific asset link.
    
    This command retrieves detailed information about an asset link, including
    the connected assets, relationship type, and associated tags.
    
    Examples:
        vamscli asset-links get --asset-link-id 12345678-1234-1234-1234-123456789012
        vamscli asset-links get --asset-link-id 12345678-1234-1234-1234-123456789012 --json-output
    """
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
        
        if not json_output:
            click.echo(f"Retrieving asset link '{asset_link_id}'...")
        
        # Get the asset link
        result = api_client.get_single_asset_link(asset_link_id)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(format_asset_link_output(result))
        
    except AssetLinkNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Link Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli asset-links list' to see available asset links.")
        raise click.ClickException(str(e))
    except AssetLinkPermissionError as e:
        click.echo(
            click.style(f"✗ Permission Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("You need permissions on the linked assets to view this asset link.")
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@asset_links.command()
@click.option('--asset-link-id', required=True, help='Asset link ID to update')
@click.option('--tags', multiple=True, help='New tags for the asset link (replaces existing tags)')
@click.option('--json-input', help='JSON input file path or JSON string with update data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def update(ctx: click.Context, asset_link_id: str, tags: List[str], 
          json_input: Optional[str], json_output: bool):
    """
    Update an existing asset link.
    
    This command updates an asset link. Currently, only tags can be updated.
    The tags provided will replace all existing tags on the asset link.
    
    Examples:
        vamscli asset-links update --asset-link-id 12345678-1234-1234-1234-123456789012 --tags tag1 --tags tag2
        vamscli asset-links update --asset-link-id 12345678-1234-1234-1234-123456789012 --json-input '{"tags":["new-tag1","new-tag2"]}'
    """
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
            update_data = {
                'tags': parse_tags_input(__builtins__['list'](tags))
            }
            
            # Ensure at least one field is being updated
            if not any(update_data.values()):
                raise click.BadParameter(
                    "At least one field must be provided for update. "
                    "Use --tags or --json-input."
                )
        
        click.echo(f"Updating asset link '{asset_link_id}'...")
        
        # Update the asset link
        result = api_client.update_asset_link(asset_link_id, update_data)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset link updated successfully!", fg='green', bold=True)
            )
            click.echo(f"  Asset Link ID: {asset_link_id}")
            if update_data.get('tags'):
                click.echo(f"  New Tags: {', '.join(update_data.get('tags'))}")
            click.echo(f"  Message: {result.get('message', 'Asset link updated')}")
        
    except AssetLinkNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Link Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli asset-links list' to see available asset links.")
        raise click.ClickException(str(e))
    except AssetLinkPermissionError as e:
        click.echo(
            click.style(f"✗ Permission Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("You need permissions on both linked assets to update this asset link.")
        raise click.ClickException(str(e))
    except AssetLinkValidationError as e:
        click.echo(
            click.style(f"✗ Validation Error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@asset_links.command()
@click.option('--asset-link-id', required=True, help='Asset link ID to delete')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def delete(ctx: click.Context, asset_link_id: str, json_output: bool):
    """
    Delete an asset link.
    
    This command permanently deletes an asset link and all its associated metadata.
    The operation cannot be undone.
    
    Examples:
        vamscli asset-links delete --asset-link-id 12345678-1234-1234-1234-123456789012
        vamscli asset-links delete --asset-link-id 12345678-1234-1234-1234-123456789012 --json-output
    """
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
        
        # Confirmation prompt for safety
        click.echo(
            click.style(f"⚠️  You are about to delete asset link '{asset_link_id}'", fg='yellow', bold=True)
        )
        click.echo("This will remove the relationship and all associated metadata.")
        
        if not click.confirm("Are you sure you want to proceed?"):
            click.echo("Deletion cancelled.")
            return
        
        click.echo(f"Deleting asset link '{asset_link_id}'...")
        
        # Delete the asset link
        result = api_client.delete_asset_link(asset_link_id)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(
                click.style("✓ Asset link deleted successfully!", fg='green', bold=True)
            )
            click.echo(f"  Asset Link ID: {asset_link_id}")
            click.echo(f"  Message: {result.get('message', 'Asset link deleted')}")
        
    except AssetLinkNotFoundError as e:
        click.echo(
            click.style(f"✗ Asset Link Not Found: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli asset-links list' to see available asset links.")
        raise click.ClickException(str(e))
    except AssetLinkPermissionError as e:
        click.echo(
            click.style(f"✗ Permission Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("You need permissions on both linked assets to delete this asset link.")
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@asset_links.command(name='list')
@click.option('-d', '--database-id', required=True, help='Database ID containing the asset')
@click.option('--asset-id', required=True, help='Asset ID to get links for')
@click.option('--tree-view', is_flag=True, help='Display children as a tree structure')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def list_links(ctx: click.Context, database_id: str, asset_id: str, tree_view: bool, json_output: bool):
    """
    List all asset links for a specific asset.
    
    This command retrieves all asset links (related, parents, children) for a specific asset.
    Use --tree-view to display child relationships in a hierarchical tree structure.
    
    Examples:
        vamscli asset-links list -d my-database --asset-id my-asset
        vamscli asset-links list -d my-database --asset-id my-asset --tree-view
        vamscli asset-links list -d my-database --asset-id my-asset --json-output
    """
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
        
        click.echo(f"Retrieving asset links for '{asset_id}' in database '{database_id}'...")
        
        # Get asset links
        result = api_client.get_asset_links_for_asset(database_id, asset_id, tree_view)
        
        if json_output:
            click.echo(json.dumps(result, indent=2))
        else:
            click.echo(f"\nAsset Links for {asset_id} in database {database_id}:")
            click.echo("=" * 60)
            click.echo(format_asset_links_list_output(result))
        
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
    except AssetLinkPermissionError as e:
        click.echo(
            click.style(f"✗ Permission Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("You need permissions on the asset to view its links.")
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))
