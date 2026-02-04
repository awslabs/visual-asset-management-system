"""Tag management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional, List

from ..utils.api_client import APIClient
from ..utils.decorators import get_profile_manager_from_context, requires_setup_and_auth
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    TagNotFoundError, TagAlreadyExistsError, TagTypeNotFoundError,
    InvalidTagDataError
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
        except json.JSONDecodeError:
            raise click.BadParameter(
                f"Invalid JSON in file '{json_input}': file contains invalid JSON format"
            )


def format_tag_output(tag_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format tag data for CLI output."""
    if json_output:
        return json.dumps(tag_data, indent=2)
    
    # CLI-friendly formatting
    output_lines = []
    output_lines.append("Tag Details:")
    output_lines.append(f"  Name: {tag_data.get('tagName', 'N/A')}")
    output_lines.append(f"  Description: {tag_data.get('description', 'N/A')}")
    
    # Handle tag type name with required indicator
    tag_type_name = tag_data.get('tagTypeName', 'N/A')
    if tag_type_name.endswith(' [R]'):
        output_lines.append(f"  Tag Type: {tag_type_name} (Required)")
    else:
        output_lines.append(f"  Tag Type: {tag_type_name}")
    
    return '\n'.join(output_lines)


def format_tags_list_output(tags_data: List[Dict[str, Any]], json_output: bool = False) -> str:
    """Format tags list for CLI output."""
    if json_output:
        return json.dumps(tags_data, indent=2)
    
    if not tags_data:
        return "No tags found."
    
    # CLI-friendly table formatting
    output_lines = []
    output_lines.append(f"\nFound {len(tags_data)} tag(s):")
    output_lines.append("─" * 80)
    
    # Header
    output_lines.append(f"{'Name':<25} {'Tag Type':<20} {'Description':<30}")
    output_lines.append("─" * 80)
    
    for tag in tags_data:
        name = tag.get('tagName', 'N/A')[:24]
        tag_type = tag.get('tagTypeName', 'N/A')[:19]
        description = tag.get('description', 'N/A')[:29]
        
        output_lines.append(f"{name:<25} {tag_type:<20} {description:<30}")
    
    output_lines.append("─" * 80)
    return '\n'.join(output_lines)


@click.group()
def tag():
    """Tag management commands."""
    pass


@tag.command()
@click.option('--tag-name', help='Tag name')
@click.option('--description', help='Tag description')
@click.option('--tag-type-name', help='Tag type name')
@click.option('--json-input', help='JSON input file path or JSON string with tag data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, tag_name: Optional[str], description: Optional[str], 
          tag_type_name: Optional[str], json_input: Optional[str], json_output: bool):
    """
    Create a new tag in VAMS.
    
    This command creates a new tag with the specified name, description, and tag type.
    You can provide tag details via individual options or use --json-input for
    complex data structures.
    
    Examples:
        vamscli tag create --tag-name "urgent" --description "Urgent priority" --tag-type-name "priority"
        vamscli tag create --json-input '{"tags":[{"tagName":"urgent","description":"Urgent","tagTypeName":"priority"}]}'
        vamscli tag create --json-input tags.json --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build tag data
        if json_input:
            # Use JSON input
            tags_data = parse_json_input(json_input)
        else:
            # Build from individual options
            if not all([tag_name, description, tag_type_name]):
                raise click.BadParameter(
                    "All options (--tag-name, --description, --tag-type-name) are required when not using --json-input"
                )
            
            tags_data = {
                'tags': [{
                    'tagName': tag_name,
                    'description': description,
                    'tagTypeName': tag_type_name
                }]
            }
        
        output_status("Creating tag(s)...", json_output)
        
        # Create the tag(s)
        result = api_client.create_tags(tags_data)
        
        def format_create_result(data):
            """Format create result for CLI display."""
            return f"  Message: {data.get('message', 'Tags created')}"
        
        output_result(
            result,
            json_output,
            success_message="✓ Tag(s) created successfully!",
            cli_formatter=format_create_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except TagAlreadyExistsError as e:
        output_error(
            e,
            json_output,
            error_type="Tag Already Exists",
            helpful_message="Use 'vamscli tag list' to view existing tags."
        )
        raise click.ClickException(str(e))
    except TagTypeNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Tag Type Not Found",
            helpful_message="Use 'vamscli tag-type list' to see available tag types.\nCreate the tag type first with 'vamscli tag-type create'."
        )
        raise click.ClickException(str(e))
    except InvalidTagDataError as e:
        output_error(e, json_output, error_type="Invalid Tag Data")
        raise click.ClickException(str(e))


@tag.command()
@click.option('--tag-name', help='Tag name to update')
@click.option('--description', help='New tag description')
@click.option('--tag-type-name', help='New tag type name')
@click.option('--json-input', help='JSON input file path or JSON string with tag data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, tag_name: Optional[str], description: Optional[str], 
          tag_type_name: Optional[str], json_input: Optional[str], json_output: bool):
    """
    Update an existing tag in VAMS.
    
    This command updates an existing tag's description and/or tag type.
    You can update individual fields or use --json-input for complex updates.
    
    Examples:
        vamscli tag update --tag-name "urgent" --description "Updated description"
        vamscli tag update --tag-name "urgent" --tag-type-name "new-priority"
        vamscli tag update --json-input '{"tags":[{"tagName":"urgent","description":"Updated","tagTypeName":"priority"}]}'
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build update data
        if json_input:
            # Use JSON input
            tags_data = parse_json_input(json_input)
        else:
            # Build from individual options
            if not tag_name:
                raise click.BadParameter(
                    "--tag-name is required when not using --json-input"
                )
            
            if not any([description, tag_type_name]):
                raise click.BadParameter(
                    "At least one field must be provided for update. "
                    "Use --description, --tag-type-name, or --json-input."
                )
            
            # We need to get the current tag data first to preserve unchanged fields
            output_status("Retrieving current tag data...", json_output)
            current_tags = api_client.get_tags()
            current_tag = None
            
            # Find the tag in the response
            tags_list = current_tags.get('message', {}).get('Items', [])
            for tag in tags_list:
                if tag.get('tagName') == tag_name:
                    current_tag = tag
                    break
            
            if not current_tag:
                raise TagNotFoundError(f"Tag '{tag_name}' not found")
            
            # Build update data with current values as defaults
            tags_data = {
                'tags': [{
                    'tagName': tag_name,
                    'description': description or current_tag.get('description'),
                    'tagTypeName': tag_type_name or current_tag.get('tagTypeName', '').replace(' [R]', '')
                }]
            }
        
        # Get tag name for progress message
        update_tag_name = tag_name if tag_name else tags_data.get('tags', [{}])[0].get('tagName', 'unknown')
        output_status(f"Updating tag '{update_tag_name}'...", json_output)
        
        # Update the tag(s)
        result = api_client.update_tags(tags_data)
        
        def format_update_result(data):
            """Format update result for CLI display."""
            return f"  Message: {data.get('message', 'Tags updated')}"
        
        output_result(
            result,
            json_output,
            success_message="✓ Tag(s) updated successfully!",
            cli_formatter=format_update_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except TagNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Tag Not Found",
            helpful_message="Use 'vamscli tag list' to see available tags."
        )
        raise click.ClickException(str(e))
    except TagTypeNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Tag Type Not Found",
            helpful_message="Use 'vamscli tag-type list' to see available tag types."
        )
        raise click.ClickException(str(e))
    except InvalidTagDataError as e:
        output_error(e, json_output, error_type="Invalid Tag Data")
        raise click.ClickException(str(e))


@tag.command()
@click.argument('tag_name')
@click.option('--confirm', is_flag=True, help='Confirm tag deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, tag_name: str, confirm: bool, json_output: bool):
    """
    Delete a tag from VAMS.
    
    This command permanently deletes a tag. The --confirm flag is required
    to prevent accidental deletions.
    
    Examples:
        vamscli tag delete urgent --confirm
        vamscli tag delete urgent --confirm --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Require confirmation for deletion
        if not confirm:
            if json_output:
                # For JSON output, return error in JSON format
                import sys
                error_result = {
                    "error": "Confirmation required",
                    "message": "Tag deletion requires the --confirm flag",
                    "tagName": tag_name
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                # For CLI output, show helpful message
                click.secho("⚠️  Tag deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("Use --confirm flag to proceed with tag deletion.")
                raise click.ClickException("Confirmation required for tag deletion")
        
        output_status(f"Deleting tag '{tag_name}'...", json_output)
        
        # Delete the tag
        result = api_client.delete_tag(tag_name)
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            lines = []
            lines.append(f"  Tag: {tag_name}")
            lines.append(f"  Message: {data.get('message', 'Tag deleted')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Tag deleted successfully!",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except TagNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Tag Not Found",
            helpful_message="Use 'vamscli tag list' to see available tags."
        )
        raise click.ClickException(str(e))


@tag.command()
@click.option('--tag-type', help='Filter tags by tag type')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, tag_type: Optional[str], json_output: bool):
    """
    List all tags in VAMS.
    
    This command lists all available tags, optionally filtered by tag type.
    
    Examples:
        vamscli tag list
        vamscli tag list --tag-type priority
        vamscli tag list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    output_status("Retrieving tags...", json_output)
    
    # Get the tags
    result = api_client.get_tags()
    
    def format_tags_result(data):
        """Format tags result for CLI display."""
        # Extract tags from the response
        tags_list = data.get('message', {}).get('Items', [])
        
        # Filter by tag type if specified
        if tag_type:
            filtered_tags = []
            for tag in tags_list:
                tag_type_name = tag.get('tagTypeName', '')
                # Remove [R] indicator for comparison
                clean_tag_type = tag_type_name.replace(' [R]', '')
                if clean_tag_type.lower() == tag_type.lower():
                    filtered_tags.append(tag)
            tags_list = filtered_tags
            
            if not tags_list:
                return f"No tags found for tag type '{tag_type}'."
        
        # Format for CLI display
        output = format_tags_list_output(tags_list, json_output)
        
        # Show pagination info if available
        if data.get('message', {}).get('NextToken'):
            output += "\n\nMore results available. Use pagination to see additional tags."
        
        return output
    
    output_result(result, json_output, cli_formatter=format_tags_result)
    return result
