"""API key management commands for VamsCLI."""

import json
import click
from typing import Dict, Any

from ..constants import API_AUTH_API_KEYS, API_AUTH_API_KEY
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    ApiKeyNotFoundError,
    ApiKeyCreationError,
    ApiKeyDeletionError,
    ApiKeyUpdateError,
)


@click.group(name='api-key')
def api_key():
    """API key management commands."""
    pass


@api_key.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, json_output: bool):
    """List all API keys.

    Examples:
        vamscli api-key list
        vamscli api-key list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Retrieving API keys...", json_output)

    try:
        result = api_client.list_api_keys()
        items = result.get('Items', []) if isinstance(result, dict) else []

        output_result(
            result,
            json_output,
            success_message=f"Found {len(items)} API key(s)",
            cli_formatter=lambda r: format_list_output(r)
        )
    except ApiKeyNotFoundError as e:
        output_error(e, json_output, error_type="API Key Error")
        raise click.ClickException(str(e))


@api_key.command()
@click.option('--name', required=True, help='Name for the API key (immutable after creation)')
@click.option('--user-id', required=True, help='VAMS user ID this key acts as')
@click.option('--description', required=True, help='Description of the API key')
@click.option('--expires-at', default=None, help='Expiration date in ISO 8601 format (e.g. 2026-12-31T23:59:59Z)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, name: str, user_id: str, description: str, expires_at: str, json_output: bool):
    """Create a new API key.

    The API key value is shown ONLY ONCE at creation time. Store it securely.

    Examples:
        vamscli api-key create --name "CI Pipeline" --user-id admin@example.com --description "CI/CD pipeline key"
        vamscli api-key create --name "Script Key" --user-id bot@example.com --description "Automation" --expires-at 2026-12-31T23:59:59Z
        vamscli api-key create --name "Dev Key" --user-id dev@example.com --description "Development testing" --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Creating API key...", json_output)

    data = {
        'apiKeyName': name,
        'userId': user_id,
        'description': description,
    }
    if expires_at:
        data['expiresAt'] = expires_at

    try:
        result = api_client.create_api_key(data)

        def format_create_output(r):
            lines = []
            lines.append(f"  API Key ID:    {r.get('apiKeyId', 'N/A')}")
            lines.append(f"  Name:          {r.get('apiKeyName', 'N/A')}")
            lines.append(f"  User ID:       {r.get('userId', 'N/A')}")
            lines.append(f"  Created By:    {r.get('createdBy', 'N/A')}")
            lines.append(f"  Expires At:    {r.get('expiresAt', 'Never')}")
            lines.append("")
            lines.append(click.style("  API Key (SAVE THIS - shown only once):", fg='yellow', bold=True))
            lines.append(click.style(f"  {r.get('apiKey', 'N/A')}", fg='green', bold=True))
            lines.append("")
            return '\n'.join(lines)

        output_result(
            result,
            json_output,
            success_message="API key created successfully",
            cli_formatter=format_create_output
        )
    except ApiKeyCreationError as e:
        output_error(e, json_output, error_type="API Key Creation Error")
        raise click.ClickException(str(e))


@api_key.command()
@click.option('--api-key-id', required=True, help='ID of the API key to update')
@click.option('--description', default=None, help='New description')
@click.option('--expires-at', default=None, help='New expiration date in ISO 8601 format (use empty string "" to clear)')
@click.option('--is-active', default=None, type=click.Choice(['true', 'false']), help='Enable or disable the API key')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, api_key_id: str, description: str, expires_at: str, is_active: str, json_output: bool):
    """Update an existing API key's description, expiration, or active status.

    Examples:
        vamscli api-key update --api-key-id UUID --description "Updated description"
        vamscli api-key update --api-key-id UUID --expires-at 2027-06-30T23:59:59Z
        vamscli api-key update --api-key-id UUID --expires-at "" (clears expiration)
        vamscli api-key update --api-key-id UUID --is-active false
        vamscli api-key update --api-key-id UUID --description "New desc" --expires-at 2027-06-30T23:59:59Z --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    if description is None and expires_at is None and is_active is None:
        raise click.ClickException("At least one of --description, --expires-at, or --is-active must be provided")

    output_status("Updating API key...", json_output)

    data = {}
    if description is not None:
        data['description'] = description
    if expires_at is not None:
        data['expiresAt'] = expires_at
    if is_active is not None:
        data['isActive'] = is_active

    try:
        result = api_client.update_api_key(api_key_id, data)
        output_result(
            result,
            json_output,
            success_message="API key updated successfully",
            cli_formatter=lambda r: format_single_output(r)
        )
    except ApiKeyNotFoundError as e:
        output_error(e, json_output, error_type="API Key Not Found")
        raise click.ClickException(str(e))
    except ApiKeyUpdateError as e:
        output_error(e, json_output, error_type="API Key Update Error")
        raise click.ClickException(str(e))


@api_key.command()
@click.option('--api-key-id', required=True, help='ID of the API key to delete')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, api_key_id: str, json_output: bool):
    """Delete an API key.

    Examples:
        vamscli api-key delete --api-key-id UUID
        vamscli api-key delete --api-key-id UUID --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Deleting API key...", json_output)

    try:
        result = api_client.delete_api_key(api_key_id)
        output_result(
            result,
            json_output,
            success_message="API key deleted successfully"
        )
    except ApiKeyNotFoundError as e:
        output_error(e, json_output, error_type="API Key Not Found")
        raise click.ClickException(str(e))
    except ApiKeyDeletionError as e:
        output_error(e, json_output, error_type="API Key Deletion Error")
        raise click.ClickException(str(e))


def format_list_output(result: Dict[str, Any]) -> str:
    items = result.get('Items', []) if isinstance(result, dict) else []
    if not items:
        return "No API keys found."
    lines = []
    for item in items:
        expires = item.get('expiresAt', '') or 'Never'
        active = item.get('isActive', 'true')
        status = 'Active' if active == 'true' else 'Inactive'
        lines.append(f"  {item.get('apiKeyName', 'N/A'):30s}  {item.get('apiKeyId', 'N/A'):36s}  {item.get('userId', 'N/A'):30s}  {expires:25s}  {status}")
    header = f"  {'Name':30s}  {'Key ID':36s}  {'User ID':30s}  {'Expires':25s}  Status"
    return header + '\n' + '-' * len(header) + '\n' + '\n'.join(lines)


def format_single_output(result: Dict[str, Any]) -> str:
    lines = []
    lines.append(f"  API Key ID:    {result.get('apiKeyId', 'N/A')}")
    lines.append(f"  Name:          {result.get('apiKeyName', 'N/A')}")
    lines.append(f"  User ID:       {result.get('userId', 'N/A')}")
    lines.append(f"  Description:   {result.get('description', 'N/A')}")
    lines.append(f"  Created By:    {result.get('createdBy', 'N/A')}")
    lines.append(f"  Created At:    {result.get('createdAt', 'N/A')}")
    lines.append(f"  Expires At:    {result.get('expiresAt', 'Never') or 'Never'}")
    lines.append(f"  Active:        {result.get('isActive', 'N/A')}")
    return '\n'.join(lines)
