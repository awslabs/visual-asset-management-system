"""User management commands for VamsCLI."""

import click
from typing import Dict, Any, Optional

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    CognitoUserNotFoundError,
    CognitoUserAlreadyExistsError,
    InvalidCognitoUserDataError,
    CognitoUserOperationError
)


@click.group()
def user():
    """User management commands."""
    pass


@user.group()
def cognito():
    """Cognito user management commands."""
    pass


@cognito.command()
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
    List all Cognito users.
    
    This command lists all Cognito users in the user pool with optional pagination
    and auto-pagination support.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli user cognito list
        
        # Auto-pagination to fetch all items (default: up to 10,000)
        vamscli user cognito list --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli user cognito list --auto-paginate --max-items 5000
        
        # Auto-pagination with custom page size
        vamscli user cognito list --auto-paginate --page-size 50
        
        # Manual pagination with page size
        vamscli user cognito list --page-size 50
        vamscli user cognito list --starting-token "token123" --page-size 50
        
        # JSON output
        vamscli user cognito list --json-output
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
            output_status(f"Retrieving Cognito users (auto-paginating up to {max_total_items} items)...", json_output)
            
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
                page_result = api_client.list_cognito_users(params)
                
                # Aggregate items
                items = page_result.get('Items', [])
                all_items.extend(items)
                total_fetched += len(items)
                
                # Show progress in CLI mode
                if not json_output:
                    output_status(f"Fetched {total_fetched} users (page {page_count})...", False)
                
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
            output_status("Retrieving Cognito users...", json_output)
            
            # Build pagination parameters
            params = {}
            if page_size:
                params['pageSize'] = page_size
            if starting_token:
                params['startingToken'] = starting_token
            
            # List users
            result = api_client.list_cognito_users(params)
        
        def format_users_list(data):
            """Format users list for CLI display."""
            users = data.get('Items', [])
            if not users:
                return "No Cognito users found."
            
            lines = []
            
            # Show auto-pagination info if present
            if data.get('autoPaginated'):
                lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
                if data.get('note'):
                    lines.append(f"⚠️  {data['note']}")
                lines.append("")
            
            lines.append(f"Found {len(users)} Cognito user(s):")
            lines.append("-" * 80)
            
            for user_data in users:
                lines.append(f"User ID: {user_data.get('userId', 'N/A')}")
                lines.append(f"Email: {user_data.get('email', 'N/A')}")
                
                phone = user_data.get('phone')
                if phone:
                    lines.append(f"Phone: {phone}")
                
                lines.append(f"Status: {user_data.get('userStatus', 'N/A')}")
                lines.append(f"Enabled: {user_data.get('enabled', False)}")
                lines.append(f"MFA Enabled: {user_data.get('mfaEnabled', False)}")
                
                created = user_data.get('userCreateDate')
                if created:
                    lines.append(f"Created: {created}")
                
                modified = user_data.get('userLastModifiedDate')
                if modified:
                    lines.append(f"Last Modified: {modified}")
                
                lines.append("-" * 80)
            
            # Show nextToken for manual pagination
            if not data.get('autoPaginated') and data.get('NextToken'):
                lines.append(f"\nNext token: {data['NextToken']}")
                lines.append("Use --starting-token to get the next page")
            
            return '\n'.join(lines)
        
        output_result(result, json_output, cli_formatter=format_users_list)
        return result
        
    except CognitoUserOperationError as e:
        output_error(
            e,
            json_output,
            error_type="Cognito Operation Error",
            helpful_message="Ensure Cognito is enabled for this VAMS environment."
        )
        raise click.ClickException(str(e))


@cognito.command()
@click.option('-u', '--user-id', required=True, help='User ID (email format)')
@click.option('-e', '--email', required=True, help='Email address')
@click.option('-p', '--phone', help='Phone number in E.164 format (e.g., +12345678900)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, user_id: str, email: str, phone: Optional[str], json_output: bool):
    """
    Create a new Cognito user.
    
    This command creates a new user in the Cognito user pool. A temporary password
    will be generated by Cognito and returned in the response.
    
    Examples:
        vamscli user cognito create -u user@example.com -e user@example.com
        vamscli user cognito create -u user@example.com -e user@example.com -p +12345678900
        vamscli user cognito create -u user@example.com -e user@example.com --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build user data
        user_data = {
            'userId': user_id,
            'email': email
        }
        
        if phone:
            user_data['phone'] = phone
        
        output_status(f"Creating Cognito user '{user_id}'...", json_output)
        
        # Create the user
        result = api_client.create_cognito_user(user_data)
        
        def format_create_result(data):
            """Format create result for CLI display."""
            lines = []
            lines.append(f"  User ID: {data.get('userId', user_id)}")
            lines.append(f"  Operation: {data.get('operation', 'create')}")
            lines.append(f"  Message: {data.get('message', 'User created')}")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Cognito user created successfully!",
            cli_formatter=format_create_result
        )
        
        return result
        
    except CognitoUserAlreadyExistsError as e:
        output_error(
            e,
            json_output,
            error_type="User Already Exists",
            helpful_message="Use 'vamscli user cognito list' to see existing users."
        )
        raise click.ClickException(str(e))
    except InvalidCognitoUserDataError as e:
        output_error(
            e,
            json_output,
            error_type="Invalid User Data",
            helpful_message="Check that email is valid and phone is in E.164 format (e.g., +12345678900)."
        )
        raise click.ClickException(str(e))
    except CognitoUserOperationError as e:
        output_error(
            e,
            json_output,
            error_type="Cognito Operation Error",
            helpful_message="Ensure Cognito is enabled for this VAMS environment."
        )
        raise click.ClickException(str(e))


@cognito.command()
@click.option('-u', '--user-id', required=True, help='User ID to update')
@click.option('-e', '--email', help='New email address')
@click.option('-p', '--phone', help='New phone number in E.164 format (e.g., +12345678900)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, user_id: str, email: Optional[str], phone: Optional[str], json_output: bool):
    """
    Update a Cognito user's email or phone.
    
    This command updates a user's email address and/or phone number. At least one
    field must be provided for update.
    
    Examples:
        vamscli user cognito update -u user@example.com -e newemail@example.com
        vamscli user cognito update -u user@example.com -p +12345678900
        vamscli user cognito update -u user@example.com -e newemail@example.com -p +12345678900
        vamscli user cognito update -u user@example.com -e newemail@example.com --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Validate that at least one field is provided
    if not email and not phone:
        raise click.BadParameter(
            "At least one field must be provided for update. "
            "Use --email or --phone."
        )
    
    try:
        # Build update data
        update_data = {}
        
        if email:
            update_data['email'] = email
        if phone:
            update_data['phone'] = phone
        
        output_status(f"Updating Cognito user '{user_id}'...", json_output)
        
        # Update the user
        result = api_client.update_cognito_user(user_id, update_data)
        
        def format_update_result(data):
            """Format update result for CLI display."""
            lines = []
            lines.append(f"  User ID: {data.get('userId', user_id)}")
            lines.append(f"  Operation: {data.get('operation', 'update')}")
            lines.append(f"  Message: {data.get('message', 'User updated')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Cognito user updated successfully!",
            cli_formatter=format_update_result
        )
        
        return result
        
    except CognitoUserNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="User Not Found",
            helpful_message="Use 'vamscli user cognito list' to see available users."
        )
        raise click.ClickException(str(e))
    except InvalidCognitoUserDataError as e:
        output_error(
            e,
            json_output,
            error_type="Invalid User Data",
            helpful_message="Check that email is valid and phone is in E.164 format (e.g., +12345678900)."
        )
        raise click.ClickException(str(e))
    except CognitoUserOperationError as e:
        output_error(
            e,
            json_output,
            error_type="Cognito Operation Error",
            helpful_message="Ensure Cognito is enabled for this VAMS environment."
        )
        raise click.ClickException(str(e))


@cognito.command()
@click.option('-u', '--user-id', required=True, help='User ID to delete')
@click.option('--confirm', is_flag=True, help='Confirm user deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, user_id: str, confirm: bool, json_output: bool):
    """
    Delete a Cognito user.
    
    ⚠️  WARNING: This action will permanently delete the user! ⚠️
    
    This command deletes a user from the Cognito user pool. The --confirm flag
    is required to prevent accidental deletions.
    
    Examples:
        vamscli user cognito delete -u user@example.com --confirm
        vamscli user cognito delete -u user@example.com --confirm --json-output
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
                    "message": "User deletion requires the --confirm flag",
                    "userId": user_id
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                # For CLI output, show helpful message
                click.secho("⚠️  User deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("This action will permanently delete the user and cannot be undone.")
                click.echo()
                click.echo("Use --confirm flag to proceed with deletion.")
                raise click.ClickException("Confirmation required for user deletion")
        
        # If --confirm is provided, skip the additional prompt in JSON mode
        if not json_output:
            # Additional confirmation prompt for safety (CLI mode only)
            click.secho(f"⚠️  You are about to delete user '{user_id}'", fg='red', bold=True)
            click.echo("This action cannot be undone!")
            
            if not click.confirm("Are you sure you want to proceed?"):
                click.echo("Deletion cancelled.")
                return None
        
        output_status(f"Deleting Cognito user '{user_id}'...", json_output)
        
        # Delete the user
        result = api_client.delete_cognito_user(user_id)
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            lines = []
            lines.append(f"  User ID: {user_id}")
            lines.append(f"  Operation: {data.get('operation', 'delete')}")
            lines.append(f"  Message: {data.get('message', 'User deleted')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Cognito user deleted successfully!",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except CognitoUserNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="User Not Found",
            helpful_message="Use 'vamscli user cognito list' to see available users."
        )
        raise click.ClickException(str(e))
    except CognitoUserOperationError as e:
        output_error(
            e,
            json_output,
            error_type="Cognito Operation Error",
            helpful_message="Ensure Cognito is enabled for this VAMS environment."
        )
        raise click.ClickException(str(e))


@cognito.command('reset-password')
@click.option('-u', '--user-id', required=True, help='User ID to reset password for')
@click.option('--confirm', is_flag=True, help='Confirm password reset')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def reset_password(ctx: click.Context, user_id: str, confirm: bool, json_output: bool):
    """
    Reset a Cognito user's password.
    
    This command resets a user's password. A new temporary password will be
    generated by Cognito and returned in the response. The user must change
    this password on their next login.
    
    The --confirm flag is required to prevent accidental password resets.
    
    Examples:
        vamscli user cognito reset-password -u user@example.com --confirm
        vamscli user cognito reset-password -u user@example.com --confirm --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Require confirmation for password reset
        if not confirm:
            if json_output:
                # For JSON output, return error in JSON format
                import sys
                error_result = {
                    "error": "Confirmation required",
                    "message": "Password reset requires the --confirm flag",
                    "userId": user_id
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                # For CLI output, show helpful message
                click.secho("⚠️  Password reset requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("This action will reset the user's password.")
                click.echo()
                click.echo("Use --confirm flag to proceed with password reset.")
                raise click.ClickException("Confirmation required for password reset")
        
        output_status(f"Resetting password for Cognito user '{user_id}'...", json_output)
        
        # Reset the password
        result = api_client.reset_cognito_user_password(user_id, confirm_reset=True)
        
        def format_reset_result(data):
            """Format reset result for CLI display."""
            lines = []
            lines.append(f"  User ID: {data.get('userId', user_id)}")
            lines.append(f"  Operation: {data.get('operation', 'resetPassword')}")
            lines.append(f"  Message: {data.get('message', 'Password reset')}")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Password reset successfully!",
            cli_formatter=format_reset_result
        )
        
        return result
        
    except CognitoUserNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="User Not Found",
            helpful_message="Use 'vamscli user cognito list' to see available users."
        )
        raise click.ClickException(str(e))
    except InvalidCognitoUserDataError as e:
        output_error(
            e,
            json_output,
            error_type="Invalid Reset Request",
            helpful_message="Ensure the --confirm flag is provided."
        )
        raise click.ClickException(str(e))
    except CognitoUserOperationError as e:
        output_error(
            e,
            json_output,
            error_type="Cognito Operation Error",
            helpful_message="Ensure Cognito is enabled for this VAMS environment."
        )
        raise click.ClickException(str(e))