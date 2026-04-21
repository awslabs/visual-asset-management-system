"""Tag type management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional, List

from ..utils.api_client import APIClient
from ..utils.decorators import get_profile_manager_from_context, requires_setup_and_auth
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    TagTypeNotFoundError, TagTypeAlreadyExistsError, TagTypeInUseError,
    InvalidTagTypeDataError
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


def format_tag_type_output(tag_type_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format tag type data for CLI output."""
    if json_output:
        return json.dumps(tag_type_data, indent=2)
    
    # CLI-friendly formatting
    output_lines = []
    output_lines.append("Tag Type Details:")
    output_lines.append(f"  Name: {tag_type_data.get('tagTypeName', 'N/A')}")
    output_lines.append(f"  Description: {tag_type_data.get('description', 'N/A')}")
    output_lines.append(f"  Required: {'Yes' if tag_type_data.get('required') == 'True' else 'No'}")
    
    # Show associated tags if available
    tags = tag_type_data.get('tags', [])
    if tags:
        output_lines.append(f"  Associated Tags ({len(tags)}): {', '.join(tags)}")
    else:
        output_lines.append("  Associated Tags: None")
    
    return '\n'.join(output_lines)


def format_tag_types_list_output(tag_types_data: List[Dict[str, Any]], json_output: bool = False) -> str:
    """Format tag types list for CLI output."""
    if json_output:
        return json.dumps(tag_types_data, indent=2)
    
    if not tag_types_data:
        return "No tag types found."
    
    # CLI-friendly table formatting
    output_lines = []
    output_lines.append(f"\nFound {len(tag_types_data)} tag type(s):")
    output_lines.append("─" * 90)
    
    # Header
    output_lines.append(f"{'Name':<20} {'Description':<30} {'Required':<10} {'Tags Count':<15}")
    output_lines.append("─" * 90)
    
    for tag_type in tag_types_data:
        name = tag_type.get('tagTypeName', 'N/A')[:19]
        description = tag_type.get('description', 'N/A')[:29]
        required = 'Yes' if tag_type.get('required') == 'True' else 'No'
        tags_count = len(tag_type.get('tags', []))
        
        output_lines.append(f"{name:<20} {description:<30} {required:<10} {tags_count:<15}")
    
    output_lines.append("─" * 90)
    return '\n'.join(output_lines)


@click.group(name='tag-type')
def tag_type():
    """Tag type management commands."""
    pass


@tag_type.command()
@click.option('--tag-type-name', required=True, help='Tag type name')
@click.option('--description', required=True, help='Tag type description')
@click.option('--required', is_flag=True, help='Mark this tag type as required')
@click.option('--json-input', help='JSON input file path or JSON string with tag type data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, tag_type_name: Optional[str], description: Optional[str], 
          required: bool, json_input: Optional[str], json_output: bool):
    """
    Create a new tag type in VAMS.
    
    This command creates a new tag type with the specified name and description.
    You can provide tag type details via individual options or use --json-input for
    complex data structures.
    
    Examples:
        vamscli tag-type create --tag-type-name "priority" --description "Priority levels"
        vamscli tag-type create --tag-type-name "status" --description "Processing status" --required
        vamscli tag-type create --json-input '{"tagTypes":[{"tagTypeName":"priority","description":"Priority levels","required":"True"}]}'
        vamscli tag-type create --json-input tag-types.json --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build tag type data
        if json_input:
            # Use JSON input
            tag_types_data = parse_json_input(json_input)
        else:
            # Build from individual options
            if not all([tag_type_name, description]):
                raise click.BadParameter(
                    "Both --tag-type-name and --description are required when not using --json-input"
                )
            
            tag_types_data = {
                'tagTypes': [{
                    'tagTypeName': tag_type_name,
                    'description': description,
                    'required': 'True' if required else 'False'
                }]
            }
        
        output_status("Creating tag type(s)...", json_output)
        
        # Create the tag type(s)
        result = api_client.create_tag_types(tag_types_data)
        
        def format_create_result(data):
            """Format create result for CLI display."""
            return f"  Message: {data.get('message', 'Tag types created')}"
        
        output_result(
            result,
            json_output,
            success_message="✓ Tag type(s) created successfully!",
            cli_formatter=format_create_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except TagTypeAlreadyExistsError as e:
        output_error(
            e,
            json_output,
            error_type="Tag Type Already Exists",
            helpful_message="Use 'vamscli tag-type list' to view existing tag types."
        )
        raise click.ClickException(str(e))
    except InvalidTagTypeDataError as e:
        output_error(e, json_output, error_type="Invalid Tag Type Data")
        raise click.ClickException(str(e))


@tag_type.command()
@click.option('--tag-type-name', required=True, help='Tag type name to update')
@click.option('--description', help='New tag type description')
@click.option('--required/--not-required', default=None, help='Update required flag')
@click.option('--json-input', help='JSON input file path or JSON string with tag type data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, tag_type_name: str, description: Optional[str], 
          required: Optional[bool], json_input: Optional[str], json_output: bool):
    """
    Update an existing tag type in VAMS.
    
    This command updates an existing tag type's description and/or required flag.
    You can update individual fields or use --json-input for complex updates.
    
    Examples:
        vamscli tag-type update --tag-type-name "priority" --description "Updated description"
        vamscli tag-type update --tag-type-name "priority" --required
        vamscli tag-type update --json-input '{"tagTypes":[{"tagTypeName":"priority","description":"Updated","required":"True"}]}'
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build update data
        if json_input:
            # Use JSON input
            tag_types_data = parse_json_input(json_input)
        else:
            # Build from individual options
            if not any([description, required is not None]):
                raise click.BadParameter(
                    "At least one field must be provided for update. "
                    "Use --description, --required/--not-required, or --json-input."
                )
            
            # We need to get the current tag type data first to preserve unchanged fields
            output_status("Retrieving current tag type data...", json_output)
            current_tag_types = api_client.get_tag_types()
            current_tag_type = None
            
            # Find the tag type in the response
            tag_types_list = current_tag_types.get('message', {}).get('Items', [])
            for tag_type in tag_types_list:
                if tag_type.get('tagTypeName') == tag_type_name:
                    current_tag_type = tag_type
                    break
            
            if not current_tag_type:
                raise TagTypeNotFoundError(f"Tag type '{tag_type_name}' not found")
            
            # Build update data with current values as defaults
            tag_types_data = {
                'tagTypes': [{
                    'tagTypeName': tag_type_name,
                    'description': description or current_tag_type.get('description'),
                    'required': 'True' if required else ('False' if required is False else current_tag_type.get('required', 'False'))
                }]
            }
        
        output_status(f"Updating tag type '{tag_type_name}'...", json_output)
        
        # Update the tag type(s)
        result = api_client.update_tag_types(tag_types_data)
        
        def format_update_result(data):
            """Format update result for CLI display."""
            return f"  Message: {data.get('message', 'Tag types updated')}"
        
        output_result(
            result,
            json_output,
            success_message="✓ Tag type(s) updated successfully!",
            cli_formatter=format_update_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except TagTypeNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Tag Type Not Found",
            helpful_message="Use 'vamscli tag-type list' to see available tag types."
        )
        raise click.ClickException(str(e))
    except InvalidTagTypeDataError as e:
        output_error(e, json_output, error_type="Invalid Tag Type Data")
        raise click.ClickException(str(e))


@tag_type.command()
@click.argument('tag_type_name')
@click.option('--confirm', is_flag=True, help='Confirm tag type deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, tag_type_name: str, confirm: bool, json_output: bool):
    """
    Delete a tag type from VAMS.
    
    This command permanently deletes a tag type. The --confirm flag is required
    to prevent accidental deletions. Tag types that are currently in use by tags
    cannot be deleted.
    
    Examples:
        vamscli tag-type delete priority --confirm
        vamscli tag-type delete priority --confirm --json-output
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
                    "message": "Tag type deletion requires the --confirm flag",
                    "tagTypeName": tag_type_name
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                # For CLI output, show helpful message
                click.secho("⚠️  Tag type deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("Use --confirm flag to proceed with tag type deletion.")
                raise click.ClickException("Confirmation required for tag type deletion")
        
        output_status(f"Deleting tag type '{tag_type_name}'...", json_output)
        
        # Delete the tag type
        result = api_client.delete_tag_type(tag_type_name)
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            lines = []
            lines.append(f"  Tag Type: {tag_type_name}")
            lines.append(f"  Message: {data.get('message', 'Tag type deleted')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Tag type deleted successfully!",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except TagTypeNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Tag Type Not Found",
            helpful_message="Use 'vamscli tag-type list' to see available tag types."
        )
        raise click.ClickException(str(e))
    except TagTypeInUseError as e:
        output_error(
            e,
            json_output,
            error_type="Tag Type In Use",
            helpful_message="Delete all tags using this tag type before deleting the tag type.\nUse 'vamscli tag list --tag-type <name>' to see tags using this type."
        )
        raise click.ClickException(str(e))


@tag_type.command()
@click.option('--show-tags', is_flag=True, help='Include associated tags in output')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, show_tags: bool, json_output: bool):
    """
    List all tag types in VAMS.
    
    This command lists all available tag types, optionally including
    the tags associated with each type.
    
    Examples:
        vamscli tag-type list
        vamscli tag-type list --show-tags
        vamscli tag-type list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    output_status("Retrieving tag types...", json_output)
    
    # Get the tag types
    result = api_client.get_tag_types()
    
    def format_tag_types_result(data):
        """Format tag types result for CLI display."""
        # Extract tag types from the response
        tag_types_list = data.get('message', {}).get('Items', [])
        
        # Format for CLI display
        if show_tags:
            # Show detailed view with tags
            if not tag_types_list:
                return "No tag types found."
            
            output_lines = []
            for i, tag_type in enumerate(tag_types_list):
                if i > 0:
                    output_lines.append("")  # Add spacing between tag types
                output_lines.append(format_tag_type_output(tag_type, json_output))
            
            output = '\n'.join(output_lines)
        else:
            # Show table view
            output = format_tag_types_list_output(tag_types_list, json_output)
        
        # Show pagination info if available
        if data.get('message', {}).get('NextToken'):
            output += "\n\nMore results available. Use pagination to see additional tag types."
        
        return output
    
    output_result(result, json_output, cli_formatter=format_tag_types_result)
    return result
