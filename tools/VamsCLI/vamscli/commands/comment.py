"""Comment management commands for VamsCLI."""

import uuid
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
        lines.append(f"Key: {item.get('assetVersionId:commentId', 'N/A')}")
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
    lines.append(f"  Asset: {comment.get('assetId', 'N/A')}")
    lines.append(f"  Key: {comment.get('assetVersionId:commentId', 'N/A')}")
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

    A comment is addressed by its asset, the asset version it is attached to, and its own
    comment ID. The API joins the last two into one path segment (`assetVersionId:commentId`),
    which is why each command takes them as separate options rather than as one joined value.
    """
    pass


@comment.command()
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID to list comments for')
@click.option('-v', '--asset-version-id', default=None,
              help='List only the comments attached to this asset version')
@click.option('--page-size', type=int, default=None, help='Number of comments per page')
@click.option('--max-items', type=int, default=None, help='Maximum total comments to fetch')
@click.option('--starting-token', default=None, help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, asset_id: str, asset_version_id: Optional[str],
         page_size: Optional[int], max_items: Optional[int], starting_token: Optional[str],
         json_output: bool):
    """List the comments on an asset, or on one of its versions.

    The endpoint bounds the read with the pagination options but returns no continuation token,
    so a listing larger than the page cannot be followed to the next page.

    Examples:
        vamscli comment list -a my-asset
        vamscli comment list -a my-asset -v 1
        vamscli comment list -a my-asset --page-size 50
        vamscli comment list -a my-asset --json-output
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
        if asset_version_id:
            result = api_client.list_asset_version_comments(
                asset_id, asset_version_id,
                max_items=max_items, page_size=page_size, starting_token=starting_token)
        else:
            result = api_client.list_asset_comments(
                asset_id,
                max_items=max_items, page_size=page_size, starting_token=starting_token)

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
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID the comment is on')
@click.option('-v', '--asset-version-id', required=True,
              help='[REQUIRED] Asset version ID the comment is attached to')
@click.option('-c', '--comment-id', required=True, help='[REQUIRED] Comment ID')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, asset_id: str, asset_version_id: str, comment_id: str,
        json_output: bool):
    """Get a single comment.

    Examples:
        vamscli comment get -a my-asset -v 1 -c c9f1a0b2
        vamscli comment get -a my-asset -v 1 -c c9f1a0b2 --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status(f"Retrieving comment '{comment_id}'...", json_output)

    try:
        result = api_client.get_comment(asset_id, asset_version_id, comment_id)
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
            helpful_message=f"Use 'vamscli comment list -a {asset_id}' to see existing comments.",
        )
        raise click.ClickException(str(e))
    except InvalidCommentDataError as e:
        output_error(e, json_output, error_type="Invalid Comment Data")
        raise click.ClickException(str(e))
    except AssetNotFoundError as e:
        output_error(e, json_output, error_type="Asset Not Found")
        raise click.ClickException(str(e))


@comment.command()
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID to comment on')
@click.option('-v', '--asset-version-id', required=True,
              help='[REQUIRED] Asset version ID to attach the comment to')
@click.option('-b', '--comment-body', required=True, help='[REQUIRED] Comment text')
@click.option('-c', '--comment-id', default=None,
              help='Comment ID (generated when omitted; reusing an existing ID overwrites that comment)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def add(ctx: click.Context, asset_id: str, asset_version_id: str, comment_body: str,
        comment_id: Optional[str], json_output: bool):
    """Add a comment to an asset version.

    The comment ID is generated when --comment-id is omitted, and is reported with the result so
    a script can address the comment afterwards.

    Examples:
        vamscli comment add -a my-asset -v 1 -b "Reviewed the geometry"
        vamscli comment add -a my-asset -v 1 -c review-note-1 -b "Reviewed the geometry"
        vamscli comment add -a my-asset -v 1 -b "Looks good" --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    comment_id = comment_id or str(uuid.uuid4())

    output_status(f"Adding comment '{comment_id}' to asset '{asset_id}'...", json_output)

    try:
        result = api_client.add_comment(asset_id, asset_version_id, comment_id, comment_body)

        # The endpoint acknowledges with a status string, which would not tell a caller the ID of
        # the comment it just created when that ID was generated here.
        payload = {
            'message': _message(result),
            'assetId': asset_id,
            'assetVersionId': asset_version_id,
            'commentId': comment_id,
        }

        output_result(
            payload,
            json_output,
            success_message="✓ Comment added successfully!",
            cli_formatter=lambda r: '\n'.join([
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
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID the comment is on')
@click.option('-v', '--asset-version-id', required=True,
              help='[REQUIRED] Asset version ID the comment is attached to')
@click.option('-c', '--comment-id', required=True, help='[REQUIRED] Comment ID')
@click.option('-b', '--comment-body', required=True, help='[REQUIRED] Replacement comment text')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, asset_id: str, asset_version_id: str, comment_id: str,
           comment_body: str, json_output: bool):
    """Replace the text of a comment you created.

    Only the creator of a comment may edit it.

    Examples:
        vamscli comment update -a my-asset -v 1 -c review-note-1 -b "Corrected note"
        vamscli comment update -a my-asset -v 1 -c review-note-1 -b "Corrected note" --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status(f"Updating comment '{comment_id}'...", json_output)

    try:
        result = api_client.update_comment(asset_id, asset_version_id, comment_id, comment_body)
        output_result(
            _message(result),
            json_output,
            success_message="✓ Comment updated successfully!",
        )
    except CommentNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Comment Not Found",
            helpful_message=f"Use 'vamscli comment list -a {asset_id}' to see existing comments.",
        )
        raise click.ClickException(str(e))
    except InvalidCommentDataError as e:
        output_error(e, json_output, error_type="Invalid Comment Data")
        raise click.ClickException(str(e))


@comment.command()
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID the comment is on')
@click.option('-v', '--asset-version-id', required=True,
              help='[REQUIRED] Asset version ID the comment is attached to')
@click.option('-c', '--comment-id', required=True, help='[REQUIRED] Comment ID')
@click.option('--confirm', is_flag=True, help='Confirm comment deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, asset_id: str, asset_version_id: str, comment_id: str,
           confirm: bool, json_output: bool):
    """Delete a comment you created.

    The comment is soft-deleted: the record moves to a deleted partition and no longer appears in
    a listing. Only the creator of a comment may delete it. The --confirm flag is required.

    Examples:
        vamscli comment delete -a my-asset -v 1 -c review-note-1 --confirm
        vamscli comment delete -a my-asset -v 1 -c review-note-1 --confirm --json-output
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

        result = api_client.delete_comment(asset_id, asset_version_id, comment_id)
        output_result(
            _message(result),
            json_output,
            success_message="✓ Comment deleted successfully!",
        )
    except CommentNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Comment Not Found",
            helpful_message=f"Use 'vamscli comment list -a {asset_id}' to see existing comments.",
        )
        raise click.ClickException(str(e))
    except InvalidCommentDataError as e:
        output_error(e, json_output, error_type="Invalid Comment Data")
        raise click.ClickException(str(e))
