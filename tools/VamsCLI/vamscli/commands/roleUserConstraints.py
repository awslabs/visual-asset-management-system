"""Role management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional

from ..constants import API_ROLES, API_ROLE_BY_ID, API_CONSTRAINTS, API_CONSTRAINT_BY_ID, API_CONSTRAINTS_TEMPLATE_IMPORT
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    RoleNotFoundError, RoleAlreadyExistsError, RoleDeletionError, InvalidRoleDataError,
    ConstraintNotFoundError, ConstraintAlreadyExistsError, ConstraintDeletionError, InvalidConstraintDataError,
    TemplateImportError
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


def format_role_output(role_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format role data for CLI output."""
    if json_output:
        return json.dumps(role_data, indent=2)
    
    # CLI-friendly formatting
    output_lines = []
    output_lines.append("Role Details:")
    output_lines.append(f"  Role Name: {role_data.get('roleName', 'N/A')}")
    output_lines.append(f"  Description: {role_data.get('description', 'N/A')}")
    
    if role_data.get('id'):
        output_lines.append(f"  ID: {role_data.get('id')}")
    if role_data.get('createdOn'):
        output_lines.append(f"  Created On: {role_data.get('createdOn')}")
    if role_data.get('source'):
        output_lines.append(f"  Source: {role_data.get('source')}")
    if role_data.get('sourceIdentifier'):
        output_lines.append(f"  Source Identifier: {role_data.get('sourceIdentifier')}")
    
    mfa_required = role_data.get('mfaRequired', False)
    output_lines.append(f"  MFA Required: {mfa_required}")
    
    return '\n'.join(output_lines)


@click.group()
def role():
    """Role management commands."""
    pass


@role.command()
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, page_size: Optional[int], max_items: Optional[int],
         starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """
    List all roles.
    
    This command lists all roles in the VAMS system with optional pagination.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli role list
        
        # Auto-pagination to fetch all items (default: up to 10,000)
        vamscli role list --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli role list --auto-paginate --max-items 5000
        
        # Auto-pagination with custom page size
        vamscli role list --auto-paginate --page-size 500
        
        # Manual pagination with page size
        vamscli role list --page-size 200
        vamscli role list --starting-token "token123" --page-size 200
        
        # JSON output
        vamscli role list --json-output
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
    
    if auto_paginate:
        # Auto-pagination mode: fetch all items up to max_items (default 10,000)
        max_total_items = max_items or 10000
        output_status(f"Retrieving roles (auto-paginating up to {max_total_items} items)...", json_output)
        
        all_items = []
        next_token = None
        total_fetched = 0
        page_count = 0
        
        while True:
            page_count += 1
            
            # Prepare query parameters for this page
            params = {}
            if page_size:
                params['pageSize'] = page_size
            if next_token:
                params['startingToken'] = next_token
            
            # Make API call
            page_result = api_client.list_roles(params)
            
            # Extract items from message wrapper (backend wraps response in message field)
            message_data = page_result.get('message', page_result)
            items = message_data.get('Items', [])
            all_items.extend(items)
            total_fetched += len(items)
            
            # Show progress in CLI mode
            if not json_output:
                output_status(f"Fetched {total_fetched} roles (page {page_count})...", False)
            
            # Check if we should continue
            next_token = message_data.get('NextToken')
            if not next_token or total_fetched >= max_total_items:
                break
        
        # Create final result
        result = {
            'Items': all_items,
            'totalItems': len(all_items),
            'autoPaginated': True,
            'pageCount': page_count
        }
        
        if total_fetched >= max_total_items and next_token:
            result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
        
    else:
        # Manual pagination mode: single API call
        output_status("Retrieving roles...", json_output)
        
        # Build pagination parameters
        params = {}
        if page_size:
            params['pageSize'] = page_size
        if starting_token:
            params['startingToken'] = starting_token
        
        # List roles
        page_result = api_client.list_roles(params)
        
        # Extract items from message wrapper
        message_data = page_result.get('message', page_result)
        result = {
            'Items': message_data.get('Items', []),
            'NextToken': message_data.get('NextToken')
        }
    
    def format_roles_list(data):
        """Format roles list for CLI display."""
        roles = data.get('Items', [])
        if not roles:
            return "No roles found."
        
        lines = []
        
        # Show auto-pagination info if present
        if data.get('autoPaginated'):
            lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
            if data.get('note'):
                lines.append(f"⚠️  {data['note']}")
            lines.append("")
        
        lines.append(f"Found {len(roles)} role(s):")
        lines.append("-" * 80)
        
        for role in roles:
            lines.append(f"Role Name: {role.get('roleName', 'N/A')}")
            lines.append(f"Description: {role.get('description', 'N/A')}")
            
            if role.get('id'):
                lines.append(f"ID: {role.get('id')}")
            if role.get('createdOn'):
                lines.append(f"Created On: {role.get('createdOn')}")
            if role.get('source'):
                lines.append(f"Source: {role.get('source')}")
            if role.get('sourceIdentifier'):
                lines.append(f"Source Identifier: {role.get('sourceIdentifier')}")
            
            mfa_required = role.get('mfaRequired', False)
            lines.append(f"MFA Required: {mfa_required}")
            
            lines.append("-" * 80)
        
        # Show nextToken for manual pagination
        if not data.get('autoPaginated') and data.get('NextToken'):
            lines.append(f"\nNext token: {data['NextToken']}")
            lines.append("Use --starting-token to get the next page")
        
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_roles_list)
    return result


@role.command()
@click.option('-r', '--role-name', required=True, help='Role name to create')
@click.option('--description', help='Role description (required unless using --json-input)')
@click.option('--source', help='Role source (optional)')
@click.option('--source-identifier', help='Source identifier (optional)')
@click.option('--mfa-required', is_flag=True, help='Enable MFA requirement')
@click.option('--json-input', help='JSON input file path or JSON string with all role data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, role_name: str, description: Optional[str], source: Optional[str],
           source_identifier: Optional[str], mfa_required: bool, json_input: Optional[str], json_output: bool):
    """
    Create a new role.
    
    This command creates a new role in VAMS. You can provide role details
    via individual options or use --json-input for complex data structures.
    
    Examples:
        vamscli role create -r admin --description "Administrator role"
        vamscli role create -r admin --description "Admin role" --mfa-required
        vamscli role create -r viewer --description "Read-only access" --source "LDAP"
        vamscli role create --json-input '{"roleName":"admin","description":"Admin role"}'
        vamscli role create --json-input role.json
    """
    # Get profile manager and API client (setup/auth already validated by decorator)
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build role data
        if json_input:
            # Use JSON input
            role_data = parse_json_input(json_input)
            # Override role_name from command line
            role_data['roleName'] = role_name
        else:
            # Build from individual options
            if not description:
                raise click.BadParameter("--description is required when not using --json-input")
            
            role_data = {
                'roleName': role_name,
                'description': description
            }
            
            if source:
                role_data['source'] = source
            if source_identifier:
                role_data['sourceIdentifier'] = source_identifier
            if mfa_required:
                role_data['mfaRequired'] = True
        
        output_status(f"Creating role '{role_name}'...", json_output)
        
        # Create the role
        result = api_client.create_role(role_data)
        
        def format_create_result(data):
            """Format create result for CLI display."""
            lines = []
            lines.append(f"  Role Name: {data.get('roleName', role_name)}")
            lines.append(f"  Message: {data.get('message', 'Role created')}")
            if data.get('timestamp'):
                lines.append(f"  Timestamp: {data.get('timestamp')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Role created successfully!",
            cli_formatter=format_create_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except RoleAlreadyExistsError as e:
        output_error(
            e,
            json_output,
            error_type="Role Already Exists",
            helpful_message="Use 'vamscli role list' to see existing roles or choose a different role name."
        )
        raise click.ClickException(str(e))
    except InvalidRoleDataError as e:
        output_error(e, json_output, error_type="Invalid Role Data")
        raise click.ClickException(str(e))


@role.command()
@click.option('-r', '--role-name', required=True, help='Role name to update')
@click.option('--description', help='New role description')
@click.option('--source', help='New source')
@click.option('--source-identifier', help='New source identifier')
@click.option('--mfa-required', is_flag=True, help='Enable MFA requirement')
@click.option('--no-mfa-required', is_flag=True, help='Disable MFA requirement')
@click.option('--json-input', help='JSON input file path or JSON string with update data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, role_name: str, description: Optional[str], source: Optional[str],
           source_identifier: Optional[str], mfa_required: bool, no_mfa_required: bool,
           json_input: Optional[str], json_output: bool):
    """
    Update an existing role.
    
    This command updates an existing role in VAMS. You can update individual
    fields or use --json-input for complex updates.
    
    Examples:
        vamscli role update -r admin --description "Updated description"
        vamscli role update -r admin --mfa-required
        vamscli role update -r admin --no-mfa-required
        vamscli role update -r admin --source "LDAP" --source-identifier "cn=admin"
        vamscli role update --json-input '{"roleName":"admin","description":"Updated"}'
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build update data
        if json_input:
            # Use JSON input
            role_data = parse_json_input(json_input)
            # Override role_name from command line
            role_data['roleName'] = role_name
        else:
            # Build from individual options
            role_data = {
                'roleName': role_name
            }
            
            if description:
                role_data['description'] = description
            if source:
                role_data['source'] = source
            if source_identifier:
                role_data['sourceIdentifier'] = source_identifier
            
            # Check for conflicting flags
            if mfa_required and no_mfa_required:
                raise click.BadParameter(
                    "Cannot use both --mfa-required and --no-mfa-required"
                )
            
            # Apply MFA flags
            if mfa_required:
                role_data['mfaRequired'] = True
            elif no_mfa_required:
                role_data['mfaRequired'] = False
            
            # Ensure at least one field is being updated
            if len(role_data) == 1:  # Only roleName
                raise click.BadParameter(
                    "At least one field must be provided for update. "
                    "Use --description, --source, --source-identifier, --mfa-required, "
                    "--no-mfa-required, or --json-input."
                )
        
        output_status(f"Updating role '{role_name}'...", json_output)
        
        # Update the role
        result = api_client.update_role(role_data)
        
        def format_update_result(data):
            """Format update result for CLI display."""
            lines = []
            lines.append(f"  Role Name: {data.get('roleName', role_name)}")
            lines.append(f"  Message: {data.get('message', 'Role updated')}")
            if data.get('timestamp'):
                lines.append(f"  Timestamp: {data.get('timestamp')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Role updated successfully!",
            cli_formatter=format_update_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except RoleNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Role Not Found",
            helpful_message="Use 'vamscli role list' to see available roles."
        )
        raise click.ClickException(str(e))
    except InvalidRoleDataError as e:
        output_error(e, json_output, error_type="Invalid Role Data")
        raise click.ClickException(str(e))


@role.command()
@click.option('-r', '--role-name', required=True, help='Role name to delete')
@click.option('--confirm', is_flag=True, help='Confirm role deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, role_name: str, confirm: bool, json_output: bool):
    """
    Delete a role.
    
    ⚠️  WARNING: This action will delete the role! ⚠️
    
    This command deletes a role from VAMS. The role must not be assigned to
    any users before it can be deleted. The backend will automatically clean up
    any user role assignments when the role is deleted.
    
    The --confirm flag is required to prevent accidental deletions.
    
    Examples:
        vamscli role delete -r old-role --confirm
        vamscli role delete -r old-role --confirm --json-output
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
                    "message": "Role deletion requires the --confirm flag",
                    "roleName": role_name
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                # For CLI output, show helpful message
                click.secho("⚠️  Role deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("This action will delete the role and cannot be undone.")
                click.echo("The backend will automatically clean up any user role assignments.")
                click.echo()
                click.echo("Use --confirm flag to proceed with deletion.")
                raise click.ClickException("Confirmation required for role deletion")
        
        # Additional confirmation prompt for safety (skip in JSON mode)
        if not json_output:
            click.secho(f"⚠️  You are about to delete role '{role_name}'", fg='red', bold=True)
            click.echo("This action cannot be undone!")
            
            if not click.confirm("Are you sure you want to proceed?"):
                click.echo("Deletion cancelled.")
                return None
        
        output_status(f"Deleting role '{role_name}'...", json_output)
        
        # Delete the role
        result = api_client.delete_role(role_name)
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            lines = []
            lines.append(f"  Role Name: {role_name}")
            lines.append(f"  Message: {data.get('message', 'Role deleted')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Role deleted successfully!",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except RoleNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Role Not Found",
            helpful_message="Use 'vamscli role list' to see available roles."
        )
        raise click.ClickException(str(e))
    except RoleDeletionError as e:
        output_error(
            e,
            json_output,
            error_type="Role Deletion Error",
            helpful_message="Ensure the role is not assigned to any users or check for other dependencies."
        )
        raise click.ClickException(str(e))


# ============================================================================
# Constraint Management Commands (Sub-group under role)
# ============================================================================

def format_constraint_output(constraint_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format constraint data for CLI output."""
    if json_output:
        return json.dumps(constraint_data, indent=2)
    
    # CLI-friendly formatting
    output_lines = []
    output_lines.append("Constraint Details:")
    output_lines.append(f"  Constraint ID: {constraint_data.get('constraintId', 'N/A')}")
    output_lines.append(f"  Name: {constraint_data.get('name', 'N/A')}")
    output_lines.append(f"  Description: {constraint_data.get('description', 'N/A')}")
    output_lines.append(f"  Object Type: {constraint_data.get('objectType', 'N/A')}")
    
    # Criteria AND
    criteria_and = constraint_data.get('criteriaAnd', [])
    if criteria_and:
        output_lines.append(f"  Criteria AND ({len(criteria_and)} conditions):")
        for i, criteria in enumerate(criteria_and, 1):
            field = criteria.get('field', 'N/A')
            operator = criteria.get('operator', 'N/A')
            value = criteria.get('value', 'N/A')
            output_lines.append(f"    [{i}] {field} {operator} {value}")
    
    # Criteria OR
    criteria_or = constraint_data.get('criteriaOr', [])
    if criteria_or:
        output_lines.append(f"  Criteria OR ({len(criteria_or)} conditions):")
        for i, criteria in enumerate(criteria_or, 1):
            field = criteria.get('field', 'N/A')
            operator = criteria.get('operator', 'N/A')
            value = criteria.get('value', 'N/A')
            output_lines.append(f"    [{i}] {field} {operator} {value}")
    
    # Group Permissions
    group_perms = constraint_data.get('groupPermissions', [])
    if group_perms:
        output_lines.append(f"  Group Permissions ({len(group_perms)}):")
        for i, perm in enumerate(group_perms, 1):
            group_id = perm.get('groupId', 'N/A')
            permission = perm.get('permission', 'N/A')
            perm_type = perm.get('permissionType', 'N/A')
            output_lines.append(f"    [{i}] {group_id}: {permission} ({perm_type})")
    
    # User Permissions
    user_perms = constraint_data.get('userPermissions', [])
    if user_perms:
        output_lines.append(f"  User Permissions ({len(user_perms)}):")
        for i, perm in enumerate(user_perms, 1):
            user_id = perm.get('userId', 'N/A')
            permission = perm.get('permission', 'N/A')
            perm_type = perm.get('permissionType', 'N/A')
            output_lines.append(f"    [{i}] {user_id}: {permission} ({perm_type})")
    
    # Metadata
    if constraint_data.get('dateCreated'):
        output_lines.append(f"  Date Created: {constraint_data.get('dateCreated')}")
    if constraint_data.get('dateModified'):
        output_lines.append(f"  Date Modified: {constraint_data.get('dateModified')}")
    if constraint_data.get('createdBy'):
        output_lines.append(f"  Created By: {constraint_data.get('createdBy')}")
    if constraint_data.get('modifiedBy'):
        output_lines.append(f"  Modified By: {constraint_data.get('modifiedBy')}")
    
    return '\n'.join(output_lines)


@click.group()
def constraint():
    """Constraint management commands."""
    pass


# Register constraint as a sub-group of role
role.add_command(constraint)


@constraint.command('list')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_constraints(ctx: click.Context, page_size: Optional[int], max_items: Optional[int],
                    starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """
    List all constraints.
    
    This command lists all constraints in the VAMS system with optional pagination.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli role constraint list
        
        # Auto-pagination to fetch all items (default: up to 10,000)
        vamscli role constraint list --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli role constraint list --auto-paginate --max-items 5000
        
        # Auto-pagination with custom page size
        vamscli role constraint list --auto-paginate --page-size 500
        
        # Manual pagination with page size
        vamscli role constraint list --page-size 200
        vamscli role constraint list --starting-token "token123" --page-size 200
        
        # JSON output
        vamscli role constraint list --json-output
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
    
    if auto_paginate:
        # Auto-pagination mode: fetch all items up to max_items (default 10,000)
        max_total_items = max_items or 10000
        output_status(f"Retrieving constraints (auto-paginating up to {max_total_items} items)...", json_output)
        
        all_items = []
        next_token = None
        total_fetched = 0
        page_count = 0
        
        while True:
            page_count += 1
            
            # Prepare query parameters for this page
            params = {}
            if page_size:
                params['pageSize'] = page_size
            if next_token:
                params['startingToken'] = next_token
            
            # Make API call (API client already unwraps message field)
            page_result = api_client.list_constraints(params)
            
            # Get items
            items = page_result.get('Items', [])
            all_items.extend(items)
            total_fetched += len(items)
            
            # Show progress in CLI mode
            if not json_output:
                output_status(f"Fetched {total_fetched} constraints (page {page_count})...", False)
            
            # Check if we should continue
            next_token = page_result.get('NextToken')
            if not next_token or total_fetched >= max_total_items:
                break
        
        # Create final result
        result = {
            'Items': all_items,
            'totalItems': len(all_items),
            'autoPaginated': True,
            'pageCount': page_count
        }
        
        if total_fetched >= max_total_items and next_token:
            result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
        
    else:
        # Manual pagination mode: single API call
        output_status("Retrieving constraints...", json_output)
        
        # Build pagination parameters
        params = {}
        if page_size:
            params['pageSize'] = page_size
        if starting_token:
            params['startingToken'] = starting_token
        
        # List constraints (API client already unwraps message field)
        result = api_client.list_constraints(params)
    
    def format_constraints_list(data):
        """Format constraints list for CLI display."""
        constraints = data.get('Items', [])
        if not constraints:
            return "No constraints found."
        
        lines = []
        
        # Show auto-pagination info if present
        if data.get('autoPaginated'):
            lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
            if data.get('note'):
                lines.append(f"⚠️  {data['note']}")
            lines.append("")
        
        lines.append(f"Found {len(constraints)} constraint(s):")
        lines.append("-" * 80)
        
        for constraint in constraints:
            lines.append(f"Constraint ID: {constraint.get('constraintId', 'N/A')}")
            lines.append(f"Name: {constraint.get('name', 'N/A')}")
            lines.append(f"Description: {constraint.get('description', 'N/A')}")
            lines.append(f"Object Type: {constraint.get('objectType', 'N/A')}")
            
            # Show counts for complex fields
            criteria_and = constraint.get('criteriaAnd', [])
            criteria_or = constraint.get('criteriaOr', [])
            group_perms = constraint.get('groupPermissions', [])
            user_perms = constraint.get('userPermissions', [])
            
            if criteria_and:
                lines.append(f"Criteria AND: {len(criteria_and)} condition(s)")
            if criteria_or:
                lines.append(f"Criteria OR: {len(criteria_or)} condition(s)")
            if group_perms:
                lines.append(f"Group Permissions: {len(group_perms)}")
            if user_perms:
                lines.append(f"User Permissions: {len(user_perms)}")
            
            lines.append("-" * 80)
        
        # Show nextToken for manual pagination
        if not data.get('autoPaginated') and data.get('NextToken'):
            lines.append(f"\nNext token: {data['NextToken']}")
            lines.append("Use --starting-token to get the next page")
        
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_constraints_list)
    return result


@constraint.command('get')
@click.option('-c', '--constraint-id', required=True, help='Constraint ID to retrieve')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get_constraint(ctx: click.Context, constraint_id: str, json_output: bool):
    """
    Get details for a specific constraint.
    
    This command retrieves detailed information about a constraint, including
    all criteria, group permissions, and user permissions.
    
    Examples:
        vamscli role constraint get -c my-constraint
        vamscli role constraint get -c my-constraint --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        output_status(f"Retrieving constraint '{constraint_id}'...", json_output)
        
        # Get the constraint
        result = api_client.get_constraint(constraint_id)
        
        output_result(result, json_output, cli_formatter=format_constraint_output)
        
        return result
        
    except ConstraintNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Constraint Not Found",
            helpful_message="Use 'vamscli role constraint list' to see available constraints."
        )
        raise click.ClickException(str(e))


@constraint.command('create')
@click.option('-c', '--constraint-id', required=True, help='Constraint ID to create')
@click.option('--name', help='Constraint name (required unless using --json-input)')
@click.option('--description', help='Constraint description (required unless using --json-input)')
@click.option('--object-type', help='Object type (required unless using --json-input)')
@click.option('--json-input', help='JSON input file path or JSON string with all constraint data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create_constraint(ctx: click.Context, constraint_id: str, name: Optional[str], description: Optional[str],
                     object_type: Optional[str], json_input: Optional[str], json_output: bool):
    """
    Create a new constraint.
    
    This command creates a new constraint in VAMS. Due to the complexity of
    constraint data (criteria, permissions), it's recommended to use --json-input
    for creating constraints.
    
    Constraint JSON structure:
    {
        "identifier": "constraint-id",
        "name": "Constraint Name",
        "description": "Description",
        "objectType": "asset",
        "criteriaAnd": [
            {"field": "databaseId", "operator": "equals", "value": "db1"}
        ],
        "criteriaOr": [
            {"field": "assetType", "operator": "equals", "value": "model"}
        ],
        "groupPermissions": [
            {"groupId": "admin", "permission": "read", "permissionType": "allow"}
        ],
        "userPermissions": [
            {"userId": "user@example.com", "permission": "write", "permissionType": "allow"}
        ]
    }
    
    Examples:
        vamscli role constraint create -c my-constraint --json-input constraint.json
        vamscli role constraint create -c my-constraint --json-input '{"name":"Test","description":"Test constraint","objectType":"asset","criteriaAnd":[{"field":"databaseId","operator":"equals","value":"db1"}],"groupPermissions":[{"groupId":"admin","permission":"read","permissionType":"allow"}]}'
    """
    # Get profile manager and API client (setup/auth already validated by decorator)
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build constraint data
        if json_input:
            # Use JSON input
            constraint_data = parse_json_input(json_input)
            # Override constraint_id from command line
            constraint_data['identifier'] = constraint_id
        else:
            # Build from individual options (basic fields only)
            if not all([name, description, object_type]):
                raise click.BadParameter(
                    "--name, --description, and --object-type are required when not using --json-input. "
                    "For complex constraints with criteria and permissions, use --json-input."
                )
            
            constraint_data = {
                'identifier': constraint_id,
                'name': name,
                'description': description,
                'objectType': object_type,
                'criteriaAnd': [],
                'criteriaOr': [],
                'groupPermissions': [],
                'userPermissions': []
            }
        
        output_status(f"Creating constraint '{constraint_id}'...", json_output)
        
        # Create the constraint
        result = api_client.create_constraint(constraint_data)
        
        def format_create_result(data):
            """Format create result for CLI display."""
            lines = []
            lines.append(f"  Constraint ID: {data.get('constraintId', constraint_id)}")
            lines.append(f"  Message: {data.get('message', 'Constraint created')}")
            if data.get('timestamp'):
                lines.append(f"  Timestamp: {data.get('timestamp')}")
            if data.get('operation'):
                lines.append(f"  Operation: {data.get('operation')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Constraint created successfully!",
            cli_formatter=format_create_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except ConstraintAlreadyExistsError as e:
        output_error(
            e,
            json_output,
            error_type="Constraint Already Exists",
            helpful_message="Use 'vamscli role constraint get' to view the existing constraint or choose a different constraint ID."
        )
        raise click.ClickException(str(e))
    except InvalidConstraintDataError as e:
        output_error(e, json_output, error_type="Invalid Constraint Data")
        raise click.ClickException(str(e))


@constraint.command('update')
@click.option('-c', '--constraint-id', required=True, help='Constraint ID to update')
@click.option('--name', help='New constraint name')
@click.option('--description', help='New constraint description')
@click.option('--object-type', help='New object type')
@click.option('--json-input', help='JSON input file path or JSON string with update data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update_constraint(ctx: click.Context, constraint_id: str, name: Optional[str], description: Optional[str],
                     object_type: Optional[str], json_input: Optional[str], json_output: bool):
    """
    Update an existing constraint.
    
    This command updates an existing constraint in VAMS. Due to the complexity of
    constraint data (criteria, permissions), it's recommended to use --json-input
    for updating constraints. The update replaces the entire constraint.
    
    Examples:
        vamscli role constraint update -c my-constraint --json-input constraint.json
        vamscli role constraint update -c my-constraint --json-input '{"name":"Updated","description":"Updated constraint","objectType":"asset","criteriaAnd":[{"field":"databaseId","operator":"equals","value":"db1"}],"groupPermissions":[{"groupId":"admin","permission":"read","permissionType":"allow"}]}'
        vamscli role constraint update -c my-constraint --name "Updated Name" --description "Updated Description"
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build update data
        if json_input:
            # Use JSON input
            constraint_data = parse_json_input(json_input)
            # Override constraint_id from command line
            constraint_data['identifier'] = constraint_id
        else:
            # For updates without JSON input, we need to get the existing constraint first
            # and then apply the updates
            output_status(f"Retrieving existing constraint '{constraint_id}'...", json_output)
            existing_constraint = api_client.get_constraint(constraint_id)
            
            # Start with existing data
            constraint_data = existing_constraint.copy()
            constraint_data['identifier'] = constraint_id
            
            # Apply updates
            if name:
                constraint_data['name'] = name
            if description:
                constraint_data['description'] = description
            if object_type:
                constraint_data['objectType'] = object_type
            
            # Ensure at least one field is being updated
            updates_made = name or description or object_type
            if not updates_made:
                raise click.BadParameter(
                    "At least one field must be provided for update. "
                    "Use --name, --description, --object-type, or --json-input."
                )
        
        output_status(f"Updating constraint '{constraint_id}'...", json_output)
        
        # Update the constraint
        result = api_client.update_constraint(constraint_id, constraint_data)
        
        def format_update_result(data):
            """Format update result for CLI display."""
            lines = []
            lines.append(f"  Constraint ID: {data.get('constraintId', constraint_id)}")
            lines.append(f"  Message: {data.get('message', 'Constraint updated')}")
            if data.get('timestamp'):
                lines.append(f"  Timestamp: {data.get('timestamp')}")
            if data.get('operation'):
                lines.append(f"  Operation: {data.get('operation')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Constraint updated successfully!",
            cli_formatter=format_update_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except ConstraintNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Constraint Not Found",
            helpful_message="Use 'vamscli role constraint list' to see available constraints."
        )
        raise click.ClickException(str(e))
    except InvalidConstraintDataError as e:
        output_error(e, json_output, error_type="Invalid Constraint Data")
        raise click.ClickException(str(e))


@constraint.command('delete')
@click.option('-c', '--constraint-id', required=True, help='Constraint ID to delete')
@click.option('--confirm', is_flag=True, help='Confirm constraint deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete_constraint(ctx: click.Context, constraint_id: str, confirm: bool, json_output: bool):
    """
    Delete a constraint.
    
    WARNING: This action will delete the constraint!
    
    This command deletes a constraint from VAMS. The constraint and all its
    associated permissions will be permanently removed.
    
    The --confirm flag is required to prevent accidental deletions.
    
    Examples:
        vamscli role constraint delete -c old-constraint --confirm
        vamscli role constraint delete -c old-constraint --confirm --json-output
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
                    "message": "Constraint deletion requires the --confirm flag",
                    "constraintId": constraint_id
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                # For CLI output, show helpful message
                click.secho("⚠️  Constraint deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("This action will delete the constraint and cannot be undone.")
                click.echo("All associated permissions will be permanently removed.")
                click.echo()
                click.echo("Use --confirm flag to proceed with deletion.")
                raise click.ClickException("Confirmation required for constraint deletion")
        
        # Additional confirmation prompt for safety (skip in JSON mode)
        if not json_output:
            click.secho(f"⚠️  You are about to delete constraint '{constraint_id}'", fg='red', bold=True)
            click.echo("This action cannot be undone!")
            
            if not click.confirm("Are you sure you want to proceed?"):
                click.echo("Deletion cancelled.")
                return None
        
        output_status(f"Deleting constraint '{constraint_id}'...", json_output)
        
        # Delete the constraint
        result = api_client.delete_constraint(constraint_id)
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            lines = []
            lines.append(f"  Constraint ID: {constraint_id}")
            lines.append(f"  Message: {data.get('message', 'Constraint deleted')}")
            if data.get('timestamp'):
                lines.append(f"  Timestamp: {data.get('timestamp')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Constraint deleted successfully!",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except ConstraintNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Constraint Not Found",
            helpful_message="Use 'vamscli role constraint list' to see available constraints."
        )
        raise click.ClickException(str(e))
    except ConstraintDeletionError as e:
        output_error(
            e,
            json_output,
            error_type="Constraint Deletion Error",
            helpful_message="Check for dependencies or contact your administrator."
        )
        raise click.ClickException(str(e))


# ============================================================================
# User Role Management Commands (Sub-group under role)
# ============================================================================

def format_user_role_output(user_role_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format user role data for CLI output."""
    if json_output:
        return json.dumps(user_role_data, indent=2)
    
    # CLI-friendly formatting
    output_lines = []
    output_lines.append("User Role Details:")
    output_lines.append(f"  User ID: {user_role_data.get('userId', 'N/A')}")
    
    # Role names
    role_names = user_role_data.get('roleName', [])
    if role_names:
        output_lines.append(f"  Roles ({len(role_names)}):")
        for i, role_name in enumerate(role_names, 1):
            output_lines.append(f"    [{i}] {role_name}")
    else:
        output_lines.append("  Roles: (none)")
    
    if user_role_data.get('createdOn'):
        output_lines.append(f"  Created On: {user_role_data.get('createdOn')}")
    
    return '\n'.join(output_lines)


@click.group()
def user():
    """User role management commands."""
    pass


# Register user as a sub-group of role
role.add_command(user)


@user.command('list')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_user_roles(ctx: click.Context, page_size: Optional[int], max_items: Optional[int],
                   starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """
    List all user role assignments.
    
    This command lists all user role assignments in the VAMS system with optional pagination.
    Results are grouped by user ID, showing all roles assigned to each user.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli role user list
        
        # Auto-pagination to fetch all items (default: up to 10,000)
        vamscli role user list --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli role user list --auto-paginate --max-items 5000
        
        # Auto-pagination with custom page size
        vamscli role user list --auto-paginate --page-size 500
        
        # Manual pagination with page size
        vamscli role user list --page-size 200
        vamscli role user list --starting-token "token123" --page-size 200
        
        # JSON output
        vamscli role user list --json-output
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
    
    if auto_paginate:
        # Auto-pagination mode: fetch all items up to max_items (default 10,000)
        max_total_items = max_items or 10000
        output_status(f"Retrieving user roles (auto-paginating up to {max_total_items} items)...", json_output)
        
        all_items = []
        next_token = None
        total_fetched = 0
        page_count = 0
        
        while True:
            page_count += 1
            
            # Prepare query parameters for this page
            params = {}
            if page_size:
                params['pageSize'] = page_size
            if next_token:
                params['startingToken'] = next_token
            
            # Make API call (API client already unwraps message field)
            page_result = api_client.list_user_roles(params)
            
            # Get items
            items = page_result.get('Items', [])
            all_items.extend(items)
            total_fetched += len(items)
            
            # Show progress in CLI mode
            if not json_output:
                output_status(f"Fetched {total_fetched} user role assignments (page {page_count})...", False)
            
            # Check if we should continue
            next_token = page_result.get('NextToken')
            if not next_token or total_fetched >= max_total_items:
                break
        
        # Create final result
        result = {
            'Items': all_items,
            'totalItems': len(all_items),
            'autoPaginated': True,
            'pageCount': page_count
        }
        
        if total_fetched >= max_total_items and next_token:
            result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
        
    else:
        # Manual pagination mode: single API call
        output_status("Retrieving user roles...", json_output)
        
        # Build pagination parameters
        params = {}
        if page_size:
            params['pageSize'] = page_size
        if starting_token:
            params['startingToken'] = starting_token
        
        # List user roles (API client already unwraps message field)
        result = api_client.list_user_roles(params)
    
    def format_user_roles_list(data):
        """Format user roles list for CLI display."""
        user_roles = data.get('Items', [])
        if not user_roles:
            return "No user role assignments found."
        
        lines = []
        
        # Show auto-pagination info if present
        if data.get('autoPaginated'):
            lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
            if data.get('note'):
                lines.append(f"⚠️  {data['note']}")
            lines.append("")
        
        lines.append(f"Found {len(user_roles)} user role assignment(s):")
        lines.append("-" * 80)
        
        for user_role in user_roles:
            user_id = user_role.get('userId', 'N/A')
            role_names = user_role.get('roleName', [])
            created_on = user_role.get('createdOn', 'N/A')
            
            lines.append(f"User ID: {user_id}")
            lines.append(f"Roles ({len(role_names)}):")
            for i, role_name in enumerate(role_names, 1):
                lines.append(f"  [{i}] {role_name}")
            lines.append(f"Created On: {created_on}")
            lines.append("-" * 80)
        
        # Show nextToken for manual pagination
        if not data.get('autoPaginated') and data.get('NextToken'):
            lines.append(f"\nNext token: {data['NextToken']}")
            lines.append("Use --starting-token to get the next page")
        
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_user_roles_list)
    return result


@user.command('create')
@click.option('-u', '--user-id', required=True, help='User ID to assign roles to')
@click.option('--role-name', multiple=True, help='Role name(s) to assign (can be specified multiple times)')
@click.option('--json-input', help='JSON input file path or JSON string with user role data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create_user_roles(ctx: click.Context, user_id: str, role_name: tuple, json_input: Optional[str], json_output: bool):
    """
    Assign roles to a user.
    
    This command creates new user role assignments in VAMS. You can assign
    multiple roles to a user at once.
    
    User Role JSON structure:
    {
        "userId": "user@example.com",
        "roleName": ["role1", "role2", "role3"]
    }
    
    Examples:
        vamscli role user create -u user@example.com --role-name admin
        vamscli role user create -u user@example.com --role-name admin --role-name viewer
        vamscli role user create -u user@example.com --json-input '{"roleName":["admin","viewer"]}'
        vamscli role user create -u user@example.com --json-input user-roles.json
    """
    # Get profile manager and API client (setup/auth already validated by decorator)
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build user role data
        if json_input:
            # Use JSON input
            user_role_data = parse_json_input(json_input)
            # Override user_id from command line
            user_role_data['userId'] = user_id
        else:
            # Build from individual options
            if not role_name:
                raise click.BadParameter("At least one --role-name is required when not using --json-input")
            
            # Use explicit reference to built-in list function to avoid namespace collision
            user_role_data = {
                'userId': user_id,
                'roleName': __builtins__['list'](role_name)
            }
        
        output_status(f"Assigning roles to user '{user_id}'...", json_output)
        
        # Create the user roles
        result = api_client.create_user_roles(user_role_data)
        
        def format_create_result(data):
            """Format create result for CLI display."""
            lines = []
            lines.append(f"  User ID: {data.get('userId', user_id)}")
            lines.append(f"  Message: {data.get('message', 'User roles created')}")
            if data.get('timestamp'):
                lines.append(f"  Timestamp: {data.get('timestamp')}")
            if data.get('operation'):
                lines.append(f"  Operation: {data.get('operation')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ User roles assigned successfully!",
            cli_formatter=format_create_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except Exception as e:
        # Import user role exceptions
        from ..utils.exceptions import UserRoleAlreadyExistsError, InvalidUserRoleDataError
        
        if isinstance(e, UserRoleAlreadyExistsError):
            output_error(
                e,
                json_output,
                error_type="User Role Already Exists",
                helpful_message="One or more roles are already assigned to this user. Use 'vamscli role user list' to see existing assignments."
            )
            raise click.ClickException(str(e))
        elif isinstance(e, InvalidUserRoleDataError):
            output_error(e, json_output, error_type="Invalid User Role Data")
            raise click.ClickException(str(e))
        else:
            raise


@user.command('update')
@click.option('-u', '--user-id', required=True, help='User ID to update roles for')
@click.option('--role-name', multiple=True, help='Role name(s) to assign (can be specified multiple times)')
@click.option('--json-input', help='JSON input file path or JSON string with user role data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update_user_roles(ctx: click.Context, user_id: str, role_name: tuple, json_input: Optional[str], json_output: bool):
    """
    Update roles for a user (differential update).
    
    This command updates user role assignments in VAMS. It performs a differential
    update: roles not in the new list are removed, and new roles are added.
    
    Examples:
        vamscli role user update -u user@example.com --role-name admin
        vamscli role user update -u user@example.com --role-name admin --role-name viewer
        vamscli role user update -u user@example.com --json-input '{"roleName":["admin","viewer"]}'
        vamscli role user update -u user@example.com --json-input user-roles.json
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build user role data
        if json_input:
            # Use JSON input
            user_role_data = parse_json_input(json_input)
            # Override user_id from command line
            user_role_data['userId'] = user_id
        else:
            # Build from individual options
            if not role_name:
                raise click.BadParameter("At least one --role-name is required when not using --json-input")
            
            # Use explicit reference to built-in list function to avoid namespace collision
            user_role_data = {
                'userId': user_id,
                'roleName': __builtins__['list'](role_name)
            }
        
        output_status(f"Updating roles for user '{user_id}'...", json_output)
        
        # Update the user roles
        result = api_client.update_user_roles(user_role_data)
        
        def format_update_result(data):
            """Format update result for CLI display."""
            lines = []
            lines.append(f"  User ID: {data.get('userId', user_id)}")
            lines.append(f"  Message: {data.get('message', 'User roles updated')}")
            if data.get('timestamp'):
                lines.append(f"  Timestamp: {data.get('timestamp')}")
            if data.get('operation'):
                lines.append(f"  Operation: {data.get('operation')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ User roles updated successfully!",
            cli_formatter=format_update_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid Input")
        raise click.ClickException(str(e))
    except Exception as e:
        # Import user role exceptions
        from ..utils.exceptions import UserRoleNotFoundError, InvalidUserRoleDataError
        
        if isinstance(e, UserRoleNotFoundError):
            output_error(
                e,
                json_output,
                error_type="User Role Not Found",
                helpful_message="Use 'vamscli role user list' to see existing user role assignments."
            )
            raise click.ClickException(str(e))
        elif isinstance(e, InvalidUserRoleDataError):
            output_error(e, json_output, error_type="Invalid User Role Data")
            raise click.ClickException(str(e))
        else:
            raise


@user.command('delete')
@click.option('-u', '--user-id', required=True, help='User ID to remove all roles from')
@click.option('--confirm', is_flag=True, help='Confirm user role deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete_user_roles(ctx: click.Context, user_id: str, confirm: bool, json_output: bool):
    """
    Delete all roles for a user.
    
    ⚠️  WARNING: This action will remove ALL role assignments for the user! ⚠️
    
    This command deletes all role assignments for a specific user in VAMS.
    The user will lose access to all resources granted through these roles.
    
    The --confirm flag is required to prevent accidental deletions.
    
    Examples:
        vamscli role user delete -u user@example.com --confirm
        vamscli role user delete -u user@example.com --confirm --json-output
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
                    "message": "User role deletion requires the --confirm flag",
                    "userId": user_id
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                # For CLI output, show helpful message
                click.secho("⚠️  User role deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("This action will remove ALL role assignments for the user.")
                click.echo("The user will lose access to all resources granted through these roles.")
                click.echo()
                click.echo("Use --confirm flag to proceed with deletion.")
                raise click.ClickException("Confirmation required for user role deletion")
        
        # Additional confirmation prompt for safety (skip in JSON mode)
        if not json_output:
            click.secho(f"⚠️  You are about to delete ALL roles for user '{user_id}'", fg='red', bold=True)
            click.echo("This action cannot be undone!")
            
            if not click.confirm("Are you sure you want to proceed?"):
                click.echo("Deletion cancelled.")
                return None
        
        output_status(f"Deleting all roles for user '{user_id}'...", json_output)
        
        # Delete the user roles
        result = api_client.delete_user_roles(user_id)
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            lines = []
            lines.append(f"  User ID: {user_id}")
            lines.append(f"  Message: {data.get('message', 'User roles deleted')}")
            if data.get('timestamp'):
                lines.append(f"  Timestamp: {data.get('timestamp')}")
            if data.get('operation'):
                lines.append(f"  Operation: {data.get('operation')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ User roles deleted successfully!",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except Exception as e:
        # Import user role exceptions
        from ..utils.exceptions import UserRoleNotFoundError, UserRoleDeletionError
        
        if isinstance(e, UserRoleNotFoundError):
            output_error(
                e,
                json_output,
                error_type="User Role Not Found",
                helpful_message="Use 'vamscli role user list' to see existing user role assignments."
            )
            raise click.ClickException(str(e))
        elif isinstance(e, UserRoleDeletionError):
            output_error(
                e,
                json_output,
                error_type="User Role Deletion Error",
                helpful_message="Check for dependencies or contact your administrator."
            )
            raise click.ClickException(str(e))
        else:
            raise


#######################
# Template Commands
#######################

@click.group()
def template():
    """Constraint template management commands."""
    pass


# Register template as a sub-group of constraint
constraint.add_command(template)


@template.command('import')
@click.option('--json-input', '-j', required=True,
              help='Template JSON data as a string or path to a JSON file')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def template_import(ctx: click.Context, json_input: str, json_output: bool):
    """
    Import constraints from a permission template.

    This command imports constraints from a JSON permission template file.
    The template defines constraints with variable placeholders (e.g., {{DATABASE_ID}})
    that are substituted with the values provided in variableValues.

    The ROLE_NAME variable is always required in variableValues and is used as the
    groupId for all created constraint permissions.

    Examples:
        # Import from a template JSON file
        vamscli role constraint template import -j ./database-admin.json

        # Import from inline JSON
        vamscli role constraint template import -j '{"variableValues": {"ROLE_NAME": "my-admin", "DATABASE_ID": "db1"}, "constraints": [...]}'

        # Import with JSON output
        vamscli role constraint template import -j ./database-admin.json --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Parse JSON input (handles both JSON strings and file paths)
        template_data = parse_json_input(json_input)

        if not template_data:
            output_error(
                ValueError("Template data is empty"),
                json_output,
                error_type="Invalid Template Data",
                helpful_message="Provide a valid JSON template file or JSON string with --json-input."
            )
            raise click.ClickException("Template data is empty")

        # Validate required fields
        if 'variableValues' not in template_data:
            output_error(
                ValueError("Missing 'variableValues' field"),
                json_output,
                error_type="Invalid Template Data",
                helpful_message="Template must include 'variableValues' with at least 'ROLE_NAME'."
            )
            raise click.ClickException("Missing 'variableValues' field in template data")

        if 'ROLE_NAME' not in template_data.get('variableValues', {}):
            output_error(
                ValueError("Missing 'ROLE_NAME' in variableValues"),
                json_output,
                error_type="Invalid Template Data",
                helpful_message="variableValues must include 'ROLE_NAME' (used as groupId for all constraints)."
            )
            raise click.ClickException("Missing 'ROLE_NAME' in variableValues")

        if 'constraints' not in template_data or not template_data['constraints']:
            output_error(
                ValueError("Missing or empty 'constraints' field"),
                json_output,
                error_type="Invalid Template Data",
                helpful_message="Template must include at least one constraint definition."
            )
            raise click.ClickException("Missing or empty 'constraints' field in template data")

        role_name = template_data['variableValues']['ROLE_NAME']
        constraint_count = len(template_data['constraints'])
        template_name = template_data.get('metadata', {}).get('name', 'unknown')

        output_status(
            f"Importing {constraint_count} constraint(s) from template '{template_name}' for role '{role_name}'...",
            json_output
        )

        # Call the API
        result = api_client.import_constraints_template(template_data)

        def format_import_result(data):
            """Format template import result for CLI display."""
            lines = []
            lines.append(f"  Template: {template_name}")
            lines.append(f"  Role: {role_name}")
            lines.append(f"  Constraints Created: {data.get('constraintsCreated', 0)}")

            constraint_ids = data.get('constraintIds', [])
            if constraint_ids:
                lines.append(f"  Constraint IDs:")
                for cid in constraint_ids:
                    lines.append(f"    - {cid}")

            if data.get('message'):
                lines.append(f"  Message: {data.get('message')}")
            if data.get('timestamp'):
                lines.append(f"  Timestamp: {data.get('timestamp')}")
            return '\n'.join(lines)

        output_result(
            result,
            json_output,
            success_message="Constraint template imported successfully!",
            cli_formatter=format_import_result
        )

        return result

    except click.ClickException:
        raise
    except click.BadParameter:
        raise
    except Exception as e:
        if isinstance(e, InvalidConstraintDataError):
            output_error(
                e,
                json_output,
                error_type="Invalid Template Data",
                helpful_message="Check your template JSON format and variable values. "
                                "See 'documentation/permissionsTemplates/' for example templates."
            )
            raise click.ClickException(str(e))
        elif isinstance(e, TemplateImportError):
            output_error(
                e,
                json_output,
                error_type="Template Import Error",
                helpful_message="The template import failed on the server. Check the template data and try again."
            )
            raise click.ClickException(str(e))
        else:
            raise
