"""API key management commands for VamsCLI."""

import json
import click
from typing import Callable, Dict, Any, Optional

from ..constants import API_AUTH_API_KEYS, API_AUTH_API_KEY
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    APIError,
    ApiKeyNotFoundError,
    ApiKeyCreationError,
    ApiKeyDeletionError,
    ApiKeyUpdateError,
)


@click.group(name='api-key')
def api_key():
    """API key management commands."""
    pass


def _reject_conflicting_paging_options(starting_token: Optional[str], auto_paginate: bool) -> None:
    """Guard the one combination that has no meaning: resuming and auto-paginating at once."""
    if auto_paginate and starting_token:
        raise click.ClickException(
            "Cannot use --auto-paginate with --starting-token. "
            "Use --auto-paginate for automatic pagination, or --starting-token for manual "
            "pagination."
        )


def _fetch_api_key_listing(fetch: Callable[..., Dict[str, Any]], page_size: Optional[int],
                           max_items: Optional[int], starting_token: Optional[str],
                           auto_paginate: bool, json_output: bool) -> Dict[str, Any]:
    """One page of API keys, or every page of them when auto-paginating.

    ``maxItems`` bounds a single response server-side (the deployment caps it at 3000), so
    auto-pagination is what reaches a larger set: it follows ``NextToken`` and asks each
    subsequent request only for what is still outstanding. The walk stops on an absent token and
    on an empty page — DynamoDB reports ``LastEvaluatedKey`` whenever a read stops at its limit,
    so a listing whose size is an exact multiple of the page bound ends with a token and no
    items.
    """
    if not auto_paginate:
        return fetch(max_items=max_items, page_size=page_size, starting_token=starting_token)

    all_items = []
    next_token = None
    page_count = 0
    while True:
        page_count += 1
        remaining = None if max_items is None else max_items - len(all_items)
        page = fetch(max_items=remaining, page_size=page_size, starting_token=next_token)
        items = page.get('Items', []) if isinstance(page, dict) else []
        all_items.extend(items)
        output_status(f"Fetched {len(all_items)} API key(s) (page {page_count})...", json_output)

        next_token = page.get('NextToken') if isinstance(page, dict) else None
        if not next_token or not items:
            break
        if max_items is not None and len(all_items) >= max_items:
            break

    result: Dict[str, Any] = {
        'Items': all_items,
        'totalItems': len(all_items),
        'autoPaginated': True,
        'pageCount': page_count,
    }
    if next_token and max_items is not None and len(all_items) >= max_items:
        result['NextToken'] = next_token
        result['truncated'] = True
    return result


@api_key.command()
@click.option('--page-size', type=int, default=None,
              help='API keys read per page (deployment default: 1000)')
@click.option('--max-items', type=int, default=None,
              help='Maximum API keys in one response, or in total with --auto-paginate '
                   '(deployment default and cap: 3000)')
@click.option('--starting-token', default=None,
              help="Pagination token from a previous response's NextToken")
@click.option('--auto-paginate', is_flag=True,
              help='Follow NextToken until every API key has been fetched')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, page_size: Optional[int], max_items: Optional[int],
         starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """List all API keys.

    A response carries at most 3000 keys and reports a NextToken when more remain. Follow the
    token with --starting-token, or use --auto-paginate to walk the whole set.

    Examples:
        vamscli api-key list
        vamscli api-key list --page-size 100
        vamscli api-key list --starting-token "eyJ..." --page-size 100
        vamscli api-key list --auto-paginate
        vamscli api-key list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    _reject_conflicting_paging_options(starting_token, auto_paginate)

    output_status("Retrieving API keys...", json_output)

    try:
        result = _fetch_api_key_listing(
            api_client.list_api_keys, page_size, max_items, starting_token,
            auto_paginate, json_output)
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
    except APIError as e:
        output_error(e, json_output, error_type="API Key Error")
        raise click.ClickException(str(e))


@api_key.command()
@click.option('--api-key-id', required=True, help='ID of the API key to retrieve')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, api_key_id: str, json_output: bool):
    """Get a single API key by ID.

    The key value itself is shown only once, at creation, and is never returned here.

    Examples:
        vamscli api-key get --api-key-id UUID
        vamscli api-key get --api-key-id UUID --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Retrieving API key...", json_output)

    try:
        result = api_client.get_api_key(api_key_id)
        output_result(
            result,
            json_output,
            success_message="API key retrieved successfully",
            cli_formatter=lambda r: format_single_output(r)
        )
    except ApiKeyNotFoundError as e:
        output_error(
            e, json_output,
            error_type="API Key Not Found",
            helpful_message="Use 'vamscli api-key list' to see available API keys."
        )
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


def _format_paging_footer(result: Dict[str, Any]) -> str:
    """The truncation notice for a listing that stopped short, or '' when it did not.

    Appended after the item lines rather than skipped along with them: a page bounded by
    pageSize/maxItems can legitimately carry a token and no items, and that is the page an
    operator most needs the token from.
    """
    if not isinstance(result, dict) or not result.get('NextToken'):
        return ''
    return (
        "\n\nMore API keys remain (listing truncated)."
        f"\nNext token: {result['NextToken']}"
        "\nUse --starting-token to get the next page, or --auto-paginate to fetch all keys."
    )


def format_list_output(result: Dict[str, Any]) -> str:
    items = result.get('Items', []) if isinstance(result, dict) else []
    footer = _format_paging_footer(result)
    if not items:
        return "No API keys found." + footer
    lines = []
    for item in items:
        expires = item.get('expiresAt', '') or 'Never'
        active = item.get('isActive', 'true')
        status = 'Active' if active == 'true' else 'Inactive'
        lines.append(f"  {item.get('apiKeyName', 'N/A'):30s}  {item.get('apiKeyId', 'N/A'):36s}  {item.get('userId', 'N/A'):30s}  {expires:25s}  {status}")
    header = f"  {'Name':30s}  {'Key ID':36s}  {'User ID':30s}  {'Expires':25s}  Status"
    return header + '\n' + '-' * len(header) + '\n' + '\n'.join(lines) + footer


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


@api_key.group()
def user():
    """Self-service API key commands (your own keys only).

    Keys created here are always tied to your authenticated user and require
    an expiration date no more than 365 days from creation. After the window
    elapses, create a new key to rotate.
    """
    pass


@user.command(name='list')
@click.option('--page-size', type=int, default=None,
              help='API keys read per page (deployment default: 1000)')
@click.option('--max-items', type=int, default=None,
              help='Maximum API keys in one response, or in total with --auto-paginate '
                   '(deployment default and cap: 3000)')
@click.option('--starting-token', default=None,
              help="Pagination token from a previous response's NextToken")
@click.option('--auto-paginate', is_flag=True,
              help='Follow NextToken until every API key has been fetched')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def user_list(ctx: click.Context, page_size: Optional[int], max_items: Optional[int],
              starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """List your own API keys.

    A response carries at most 3000 keys and reports a NextToken when more remain. Follow the
    token with --starting-token, or use --auto-paginate to walk the whole set.

    Examples:
        vamscli api-key user list
        vamscli api-key user list --page-size 100
        vamscli api-key user list --starting-token "eyJ..." --page-size 100
        vamscli api-key user list --auto-paginate
        vamscli api-key user list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    _reject_conflicting_paging_options(starting_token, auto_paginate)

    output_status("Retrieving your API keys...", json_output)

    try:
        result = _fetch_api_key_listing(
            api_client.list_user_api_keys, page_size, max_items, starting_token,
            auto_paginate, json_output)
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
    except APIError as e:
        output_error(e, json_output, error_type="API Key Error")
        raise click.ClickException(str(e))


@user.command(name='get')
@click.option('--api-key-id', required=True, help='ID of your API key to retrieve')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def user_get(ctx: click.Context, api_key_id: str, json_output: bool):
    """Get one of your own API keys by ID.

    A key belonging to another user is reported as not found. The key value itself is shown only
    once, at creation, and is never returned here.

    Examples:
        vamscli api-key user get --api-key-id UUID
        vamscli api-key user get --api-key-id UUID --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Retrieving your API key...", json_output)

    try:
        result = api_client.get_user_api_key(api_key_id)
        output_result(
            result,
            json_output,
            success_message="API key retrieved successfully",
            cli_formatter=lambda r: format_single_output(r)
        )
    except ApiKeyNotFoundError as e:
        output_error(
            e, json_output,
            error_type="API Key Not Found",
            helpful_message="Use 'vamscli api-key user list' to see your API keys."
        )
        raise click.ClickException(str(e))


@user.command(name='create')
@click.option('--name', required=True, help='Name for the API key (immutable after creation)')
@click.option('--description', required=True, help='Description of the API key')
@click.option('--expires-at', required=True, help='Expiration date in ISO 8601 format (required, max 365 days from creation)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def user_create(ctx: click.Context, name: str, description: str, expires_at: str, json_output: bool):
    """Create a new API key tied to your user.

    The API key value is shown ONLY ONCE at creation time. Store it securely.
    An expiration date is required and may be at most 365 days from creation.

    Examples:
        vamscli api-key user create --name "My Script" --description "Automation" --expires-at 2026-12-31T23:59:59Z
        vamscli api-key user create --name "Dev Key" --description "Testing" --expires-at 2026-09-30 --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Creating API key...", json_output)

    data = {
        'apiKeyName': name,
        'description': description,
        'expiresAt': expires_at,
    }

    try:
        result = api_client.create_user_api_key(data)

        def format_create_output(r):
            lines = []
            lines.append(f"  API Key ID:    {r.get('apiKeyId', 'N/A')}")
            lines.append(f"  Name:          {r.get('apiKeyName', 'N/A')}")
            lines.append(f"  User ID:       {r.get('userId', 'N/A')}")
            lines.append(f"  Expires At:    {r.get('expiresAt', 'N/A')}")
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


@user.command(name='update')
@click.option('--api-key-id', required=True, help='ID of your API key to update')
@click.option('--description', default=None, help='New description')
@click.option('--expires-at', default=None, help='New expiration date in ISO 8601 format (max 365 days from key creation; cannot be cleared)')
@click.option('--is-active', default=None, type=click.Choice(['true', 'false']), help='Enable or disable the API key')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def user_update(ctx: click.Context, api_key_id: str, description: str, expires_at: str, is_active: str, json_output: bool):
    """Update one of your own API keys.

    The expiration cannot be removed and cannot be set beyond 365 days from
    the key's original creation date. After that window, create a new key.

    Examples:
        vamscli api-key user update --api-key-id UUID --description "Updated description"
        vamscli api-key user update --api-key-id UUID --expires-at 2026-11-30T23:59:59Z
        vamscli api-key user update --api-key-id UUID --is-active false
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
        result = api_client.update_user_api_key(api_key_id, data)
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


@user.command(name='delete')
@click.option('--api-key-id', required=True, help='ID of your API key to delete')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def user_delete(ctx: click.Context, api_key_id: str, json_output: bool):
    """Delete one of your own API keys.

    Examples:
        vamscli api-key user delete --api-key-id UUID
        vamscli api-key user delete --api-key-id UUID --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Deleting API key...", json_output)

    try:
        result = api_client.delete_user_api_key(api_key_id)
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
