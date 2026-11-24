"""Comment management commands for VamsCLI."""

from builtins import list as builtin_list  # Avoid namespace collision with the 'list' command
from typing import Any, Dict, List, Optional

import click

from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    APIError,
    AssetNotFoundError,
    CommentNotFoundError,
    InvalidCommentDataError,
)


def _message(result: Dict[str, Any]) -> Any:
    """Unwrap the {'message': ...} envelope every comment route returns."""
    return result.get('message', result) if isinstance(result, dict) else result


def _comment_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The comments in a listing response.

    The asset and asset-version routes answer with a bare array under `message`; the collection
    shape ({'Items': [...]}) is accepted too so a listing is read the same way either way.
    """
    payload = _message(result)
    if isinstance(payload, dict):
        payload = payload.get('Items', [])
    return payload if isinstance(payload, builtin_list) else []


def format_comment_list_output(result: Dict[str, Any]) -> str:
    """Format a comment listing for CLI output."""
    items = _comment_items(result)
    if not items:
        return "No comments found."

    lines = [f"Found {len(items)} comment(s):", "=" * 100]
    for item in items:
        lines.append(f"Comment ID: {item.get('commentId', 'N/A')}")
        lines.append(f"Asset Version: {item.get('assetVersionId', 'N/A')}")
        lines.append(f"Owner: {item.get('commentOwnerUsername') or item.get('commentOwnerID', 'N/A')}")
        lines.append(f"Created: {item.get('dateCreated', 'N/A')}")
        if item.get('dateEdited'):
            lines.append(f"Edited: {item.get('dateEdited')}")
        lines.append(f"Body: {item.get('commentBody', '')}")
        lines.append("-" * 100)
    return '\n'.join(lines)


def format_comment_detail_output(result: Dict[str, Any]) -> str:
    """Format a single comment for CLI output."""
    comment = _message(result)
    if not isinstance(comment, dict) or not comment:
        return "No comment found."

    lines = ["Comment Details:", "=" * 100]
    lines.append(f"  Database: {comment.get('databaseId', 'N/A')}")
    lines.append(f"  Asset: {comment.get('assetId', 'N/A')}")
    lines.append(f"  Comment ID: {comment.get('commentId', 'N/A')}")
    lines.append(f"  Asset Version: {comment.get('assetVersionId', 'N/A')}")
    lines.append(f"  Owner: {comment.get('commentOwnerUsername') or comment.get('commentOwnerID', 'N/A')}")
    lines.append(f"  Created: {comment.get('dateCreated', 'N/A')}")
    if comment.get('dateEdited'):
        lines.append(f"  Edited: {comment.get('dateEdited')}")
    lines.append("")
    lines.append(comment.get('commentBody', ''))
    return '\n'.join(lines)


@click.group()
def comment():
    """Comment management commands.

    A comment belongs to one asset, addressed by its database and asset ID (two databases may hold
    assets with the same asset ID), and is addressed by its own comment ID, which the API
    generates. The asset version a comment is about is recorded with it.
    """
    pass


@comment.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID of the asset')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID to list comments for')
@click.option('-v', '--asset-version-id', default=None,
              help='List only the comments attached to this asset version')
@click.option('--page-size', type=int, default=None, help='Number of comments per page')
@click.option('--max-items', type=int, default=None, help='Maximum total comments to fetch')
@click.option('--starting-token', default=None, help='Token for pagination')
@click.option('--show-archived', is_flag=True, help='Also find an archived asset')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database_id: str, asset_id: str, asset_version_id: Optional[str],
         page_size: Optional[int], max_items: Optional[int], starting_token: Optional[str],
         show_archived: bool, json_output: bool):
    """List the comments on an asset, or on one of its versions.

    The endpoint bounds the read with the pagination options but returns no continuation token,
    so a listing larger than the page cannot be followed to the next page.

    Examples:
        vamscli comment list -d my-db -a my-asset
        vamscli comment list -d my-db -a my-asset -v 1
        vamscli comment list -d my-db -a my-asset --page-size 50
        vamscli comment list -d my-db -a my-asset --json-output
        vamscli comment list -d my-db -a my-archived-asset --show-archived
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    if asset_version_id:
        output_status(
            f"Retrieving comments for asset '{asset_id}' version '{asset_version_id}'...",
            json_output)
    else:
        output_status(f"Retrieving comments for asset '{asset_id}'...", json_output)

    try:
        result = api_client.list_asset_comments(
            database_id, asset_id,
            max_items=max_items, page_size=page_size, starting_token=starting_token,
            show_archived=show_archived, asset_version_id=asset_version_id)

        items = _comment_items(result)
        output_result(
            _message(result),
            json_output,
            success_message=f"Found {len(items)} comment(s)",
            cli_formatter=lambda _r: format_comment_list_output(result),
        )
    except AssetNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Asset Not Found",
            helpful_message="Use 'vamscli assets list' to see available assets.",
        )
        raise click.ClickException(str(e))
    except APIError as e:
        output_error(e, json_output, error_type="API Error")
        raise click.ClickException(str(e))


@comment.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID of the asset')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID the comment is on')
@click.option('-c', '--comment-id', required=True, help='[REQUIRED] Comment ID')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, database_id: str, asset_id: str, comment_id: str, json_output: bool):
    """Get a single comment.

    Examples:
        vamscli comment get -d my-db -a my-asset -c 3f2c9a1e-5b7d-4c11-9e0a-2d6f8b4c1a90
        vamscli comment get -d my-db -a my-asset -c 3f2c9a1e-5b7d-4c11-9e0a-2d6f8b4c1a90 --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status(f"Retrieving comment '{comment_id}'...", json_output)

    try:
        result = api_client.get_comment(database_id, asset_id, comment_id)
        output_result(
            _message(result),
            json_output,
            success_message="Comment retrieved successfully",
            cli_formatter=lambda _r: format_comment_detail_output(result),
        )
    except CommentNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Comment Not Found",
            helpful_message=f"Use 'vamscli comment list -d {database_id} -a {asset_id}' to see existing comments.",
        )
        raise click.ClickException(str(e))
    except InvalidCommentDataError as e:
        output_error(e, json_output, error_type="Invalid Comment Data")
        raise click.ClickException(str(e))
    except AssetNotFoundError as e:
        output_error(e, json_output, error_type="Asset Not Found")
        raise click.ClickException(str(e))


@comment.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID of the asset')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID to comment on')
@click.option('-v', '--asset-version-id', required=True,
              help='[REQUIRED] Asset version ID the comment is about')
@click.option('-b', '--comment-body', required=True, help='[REQUIRED] Comment text')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def add(ctx: click.Context, database_id: str, asset_id: str, asset_version_id: str,
        comment_body: str, json_output: bool):
    """Add a comment about an asset version.

    The API generates the comment ID; it is reported with the result so a script can address the
    comment afterwards.

    Examples:
        vamscli comment add -d my-db -a my-asset -v 1 -b "Reviewed the geometry"
        vamscli comment add -d my-db -a my-asset -v 1 -b "Looks good" --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status(f"Adding a comment to asset '{asset_id}'...", json_output)

    try:
        result = api_client.add_comment(database_id, asset_id, asset_version_id, comment_body)

        payload = {
            'message': _message(result),
            'databaseId': database_id,
            'assetId': asset_id,
            'assetVersionId': asset_version_id,
            'commentId': result.get('commentId') if isinstance(result, dict) else None,
        }

        output_result(
            payload,
            json_output,
            success_message="✓ Comment added successfully!",
            cli_formatter=lambda r: '\n'.join([
                f"  Database: {r.get('databaseId')}",
                f"  Asset: {r.get('assetId')}",
                f"  Asset Version: {r.get('assetVersionId')}",
                f"  Comment ID: {r.get('commentId')}",
                f"  Message: {r.get('message')}",
            ]),
        )
    except InvalidCommentDataError as e:
        output_error(e, json_output, error_type="Invalid Comment Data")
        raise click.ClickException(str(e))
    except AssetNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Asset Not Found",
            helpful_message="Use 'vamscli assets list' to see available assets.",
        )
        raise click.ClickException(str(e))


@comment.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID of the asset')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID the comment is on')
@click.option('-c', '--comment-id', required=True, help='[REQUIRED] Comment ID')
@click.option('-b', '--comment-body', required=True, help='[REQUIRED] Replacement comment text')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, database_id: str, asset_id: str, comment_id: str,
           comment_body: str, json_output: bool):
    """Replace the text of a comment you created.

    Only the creator of a comment may edit it.

    Examples:
        vamscli comment update -d my-db -a my-asset -c 3f2c9a1e-5b7d-4c11-9e0a-2d6f8b4c1a90 -b "Corrected note"
        vamscli comment update -d my-db -a my-asset -c 3f2c9a1e-5b7d-4c11-9e0a-2d6f8b4c1a90 -b "Corrected note" --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status(f"Updating comment '{comment_id}'...", json_output)

    try:
        result = api_client.update_comment(database_id, asset_id, comment_id, comment_body)
        output_result(
            _message(result),
            json_output,
            success_message="✓ Comment updated successfully!",
        )
    except CommentNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Comment Not Found",
            helpful_message=f"Use 'vamscli comment list -d {database_id} -a {asset_id}' to see existing comments.",
        )
        raise click.ClickException(str(e))
    except InvalidCommentDataError as e:
        output_error(e, json_output, error_type="Invalid Comment Data")
        raise click.ClickException(str(e))


@comment.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID of the asset')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID the comment is on')
@click.option('-c', '--comment-id', required=True, help='[REQUIRED] Comment ID')
@click.option('--confirm', is_flag=True, help='Confirm comment deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, database_id: str, asset_id: str, comment_id: str,
           confirm: bool, json_output: bool):
    """Delete a comment you created.

    The comment is soft-deleted: the record moves to a deleted partition and no longer appears in
    a listing. Only the creator of a comment may delete it. The --confirm flag is required.

    Examples:
        vamscli comment delete -d my-db -a my-asset -c 3f2c9a1e-5b7d-4c11-9e0a-2d6f8b4c1a90 --confirm
        vamscli comment delete -d my-db -a my-asset -c 3f2c9a1e-5b7d-4c11-9e0a-2d6f8b4c1a90 --confirm --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Require confirmation for deletion
        if not confirm:
            if json_output:
                import sys
                error_result = {
                    "error": "Confirmation required",
                    "message": "Comment deletion requires the --confirm flag",
                    "commentId": comment_id
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                click.secho("⚠️  Comment deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("Use --confirm flag to proceed with comment deletion.")
                raise click.ClickException("Confirmation required for comment deletion")

        output_status(f"Deleting comment '{comment_id}'...", json_output)

        result = api_client.delete_comment(database_id, asset_id, comment_id)
        output_result(
            _message(result),
            json_output,
            success_message="✓ Comment deleted successfully!",
        )
    except CommentNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Comment Not Found",
            helpful_message=f"Use 'vamscli comment list -d {database_id} -a {asset_id}' to see existing comments.",
        )
        raise click.ClickException(str(e))
    except InvalidCommentDataError as e:
        output_error(e, json_output, error_type="Invalid Comment Data")
        raise click.ClickException(str(e))
