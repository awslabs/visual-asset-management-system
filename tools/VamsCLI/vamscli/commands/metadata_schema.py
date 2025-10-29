"""Metadata schema management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional

from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.exceptions import DatabaseNotFoundError


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
        except json.JSONDecodeError:
            raise click.BadParameter(
                f"Invalid JSON in file '{json_input}': file contains invalid JSON format"
            )


def format_metadata_schema_output(schema_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format metadata schema data for CLI output."""
    if json_output:
        return json.dumps(schema_data, indent=2)
    
    # Extract the actual schema items from the response
    message = schema_data.get('message', {})
    items = message.get('Items', []) if isinstance(message, dict) else []
    
    if not items:
        return "No metadata schema fields found for this database."
    
    # CLI-friendly table formatting
    output_lines = []
    output_lines.append(f"Metadata Schema for Database ({len(items)} field(s)):")
    output_lines.append("=" * 80)
    
    # Table header
    output_lines.append(f"{'Field Name':<25} {'Data Type':<15} {'Required':<10} {'Depends On':<25}")
    output_lines.append("-" * 80)
    
    # Table rows
    for item in items:
        field_name = item.get('field', 'N/A')
        data_type = item.get('datatype', 'N/A')
        required = 'Yes' if item.get('required', False) else 'No'
        depends_on = item.get('dependsOn', [])
        
        # Format depends_on as comma-separated string
        depends_on_str = ', '.join(depends_on) if isinstance(depends_on, list) and depends_on else 'None'
        
        # Truncate long values for table display
        if len(depends_on_str) > 23:
            depends_on_str = depends_on_str[:20] + '...'
        
        output_lines.append(f"{field_name:<25} {data_type:<15} {required:<10} {depends_on_str:<25}")
    
    output_lines.append("-" * 80)
    
    # Show pagination info if available
    next_token = message.get('NextToken') if isinstance(message, dict) else None
    if next_token:
        output_lines.append(f"More results available. Use --starting-token '{next_token}' to see additional fields.")
    
    return '\n'.join(output_lines)


@click.group()
def metadata_schema():
    """Metadata schema management commands."""
    pass


@metadata_schema.command()
@click.option('-d', '--database', required=True, help='Database ID to get metadata schema for')
@click.option('--max-items', type=int, default=1000, help='Maximum number of items to return (default: 1000)')
@click.option('--page-size', type=int, default=100, help='Number of items per page (default: 100)')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-input', help='JSON input file path or JSON string with pagination parameters')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, database: str, max_items: int, page_size: int, 
        starting_token: Optional[str], json_input: Optional[str], json_output: bool):
    """
    Get metadata schema for a database.
    
    This command retrieves the metadata schema configuration for a specific database,
    showing all defined metadata fields, their data types, requirements, and dependencies.
    
    The metadata schema defines the structure and validation rules for metadata
    that can be associated with assets in the database.
    
    Examples:
        vamscli metadata-schema get -d my-database
        vamscli metadata-schema get -d my-database --max-items 50 --page-size 25
        vamscli metadata-schema get -d my-database --json-output
        vamscli metadata-schema get -d my-database --json-input pagination.json
        vamscli metadata-schema get -d my-database --json-input '{"maxItems":100,"pageSize":50}'
    """
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Handle JSON input for pagination parameters
    if json_input:
        json_data = parse_json_input(json_input)
        
        # Override pagination parameters from JSON input
        max_items = json_data.get('maxItems', max_items)
        page_size = json_data.get('pageSize', page_size)
        starting_token = json_data.get('startingToken', starting_token)
    
    if not json_output:
        click.echo(f"Retrieving metadata schema for database '{database}'...")
    
    # Get metadata schema
    try:
        result = api_client.get_metadata_schema(
            database_id=database,
            max_items=max_items,
            page_size=page_size,
            starting_token=starting_token
        )
    except DatabaseNotFoundError as e:
        if json_output:
            click.echo(json.dumps({"error": str(e)}, indent=2))
        else:
            click.echo(click.style(f"✗ Database Not Found: {e}", fg='red', bold=True), err=True)
            click.echo("Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))
    
    if json_output:
        click.echo(json.dumps(result, indent=2))
    else:
        formatted_output = format_metadata_schema_output(result)
        click.echo(formatted_output)
    
    return result
