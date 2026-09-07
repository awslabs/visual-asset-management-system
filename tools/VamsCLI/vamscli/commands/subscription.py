"""Subscription management commands for VamsCLI."""

from builtins import list as builtin_list  # Avoid namespace collision with the 'list' command
from typing import Any, Dict, Optional, Tuple

import click

from ..constants import SUBSCRIPTION_ENTITY_ASSET, SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE
from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    APIError,
    AssetNotFoundError,
    InvalidSubscriptionDataError,
    SubscriptionAlreadyExistsError,
    SubscriptionNotFoundError,
)


def _message(result: Dict[str, Any]) -> Any:
    """Unwrap the {'message': ...} envelope every subscription route returns."""
    return result.get('message', result) if isinstance(result, dict) else result


def format_subscription_list_output(result: Dict[str, Any]) -> str:
    """Format a subscription listing for CLI output."""
    payload = _message(result)
    items = payload.get('Items', []) if isinstance(payload, dict) else []
    if not items:
        return "No subscriptions found."

    lines = [f"Found {len(items)} subscription(s):", "=" * 100]
    for item in items:
        lines.append(f"Event: {item.get('eventName', 'N/A')}")
        lines.append(f"Entity: {item.get('entityName', 'N/A')} {item.get('entityId', 'N/A')}")
        if item.get('entityValue'):
            lines.append(f"Entity Name: {item.get('entityValue')}")
        if item.get('databaseId'):
            lines.append(f"Database: {item.get('databaseId')}")
        subscribers = item.get('subscribers') or []
        lines.append(f"Subscribers ({len(subscribers)}): {', '.join(subscribers)}")
        lines.append("-" * 100)

    if isinstance(payload, dict) and payload.get('NextToken'):
        lines.append(f"\nNext token: {payload['NextToken']}")
        lines.append("Use --starting-token to get the next page")
    return '\n'.join(lines)


@click.group()
def subscription():
    """Subscription management commands.

    A subscription is keyed on an event, an entity type, and the entity's ID, and carries the list
    of subscribed VAMS users. Notifications are delivered by e-mail, resolved from each
    subscriber's user profile.
    """
    pass


@subscription.command()
@click.option('--page-size', type=int, default=None, help='Number of subscriptions per page')
@click.option('--max-items', type=int, default=None, help='Maximum total subscriptions to fetch')
@click.option('--starting-token', default=None, help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, page_size: Optional[int], max_items: Optional[int],
         starting_token: Optional[str], json_output: bool):
    """List subscriptions.

    The listing is filtered to the assets you may read, so it shows the subscriptions you have
    access to rather than every subscription in the deployment.

    Examples:
        vamscli subscription list
        vamscli subscription list --page-size 50
        vamscli subscription list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Retrieving subscriptions...", json_output)

    try:
        result = api_client.list_subscriptions(
            max_items=max_items, page_size=page_size, starting_token=starting_token)

        payload = _message(result)
        items = payload.get('Items', []) if isinstance(payload, dict) else []
        output_result(
            payload,
            json_output,
            success_message=f"Found {len(items)} subscription(s)",
            cli_formatter=lambda _r: format_subscription_list_output(result),
        )
    except APIError as e:
        output_error(e, json_output, error_type="API Error")
        raise click.ClickException(str(e))


@subscription.command()
@click.option('-i', '--entity-id', required=True,
              help='[REQUIRED] ID of the entity to subscribe to (the assetId for an Asset)')
@click.option('-s', '--subscriber', multiple=True, required=True,
              help='[REQUIRED] VAMS user ID to subscribe (repeat for several)')
@click.option('--event-name', default=SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
              show_default=True, help='Event to subscribe to')
@click.option('--entity-name', default=SUBSCRIPTION_ENTITY_ASSET,
              show_default=True, help='Entity type the ID refers to')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, entity_id: str, subscriber: Tuple[str, ...], event_name: str,
           entity_name: str, json_output: bool):
    """Subscribe users to an entity's events.

    When a subscription already exists for the entity, the listed users are added to it. A user
    who is already subscribed is rejected rather than ignored, so add only the new ones.

    Each subscriber needs an e-mail address: either on their VAMS user profile, or as their user
    ID itself.

    Examples:
        vamscli subscription create -i my-asset -s alice@example.com
        vamscli subscription create -i my-asset -s alice@example.com -s bob@example.com
        vamscli subscription create -i my-asset -s alice@example.com --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status(f"Subscribing {len(subscriber)} user(s) to '{entity_id}'...", json_output)

    try:
        result = api_client.create_subscription(
            event_name, entity_name, entity_id, builtin_list(subscriber))
        output_result(
            _message(result),
            json_output,
            success_message="✓ Subscription created successfully!",
        )
    except SubscriptionAlreadyExistsError as e:
        output_error(
            e, json_output,
            error_type="Subscription Already Exists",
            helpful_message="Use 'vamscli subscription list' to see who is already subscribed.",
        )
        raise click.ClickException(str(e))
    except InvalidSubscriptionDataError as e:
        output_error(e, json_output, error_type="Invalid Subscription Data")
        raise click.ClickException(str(e))
    except AssetNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Asset Not Found",
            helpful_message="Use 'vamscli assets list' to see available assets.",
        )
        raise click.ClickException(str(e))


@subscription.command()
@click.option('-i', '--entity-id', required=True,
              help='[REQUIRED] ID of the subscribed entity (the assetId for an Asset)')
@click.option('-s', '--subscriber', multiple=True, required=True,
              help='[REQUIRED] The complete new list of subscribed VAMS user IDs (repeat for several)')
@click.option('--event-name', default=SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
              show_default=True, help='Event the subscription is for')
@click.option('--entity-name', default=SUBSCRIPTION_ENTITY_ASSET,
              show_default=True, help='Entity type the ID refers to')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, entity_id: str, subscriber: Tuple[str, ...], event_name: str,
           entity_name: str, json_output: bool):
    """Replace a subscription's subscriber list.

    The list is a replacement, not an addition: a user left out of it is unsubscribed. To remove
    one user and leave the rest alone, use 'vamscli subscription unsubscribe'.

    Examples:
        vamscli subscription update -i my-asset -s alice@example.com -s carol@example.com
        vamscli subscription update -i my-asset -s alice@example.com --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status(f"Updating the subscription on '{entity_id}'...", json_output)

    try:
        result = api_client.update_subscription(
            event_name, entity_name, entity_id, builtin_list(subscriber))
        output_result(
            _message(result),
            json_output,
            success_message="✓ Subscription updated successfully!",
        )
    except SubscriptionNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Subscription Not Found",
            helpful_message="Use 'vamscli subscription create' to create it first.",
        )
        raise click.ClickException(str(e))
    except InvalidSubscriptionDataError as e:
        output_error(e, json_output, error_type="Invalid Subscription Data")
        raise click.ClickException(str(e))
    except AssetNotFoundError as e:
        output_error(e, json_output, error_type="Asset Not Found")
        raise click.ClickException(str(e))


@subscription.command()
@click.option('-i', '--entity-id', required=True,
              help='[REQUIRED] ID of the subscribed entity (the assetId for an Asset)')
@click.option('-s', '--subscriber', multiple=True, required=True,
              help='[REQUIRED] Subscribed VAMS user ID; the endpoint requires the list even though '
                   'the whole subscription is removed')
@click.option('--event-name', default=SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
              show_default=True, help='Event the subscription is for')
@click.option('--entity-name', default=SUBSCRIPTION_ENTITY_ASSET,
              show_default=True, help='Entity type the ID refers to')
@click.option('--confirm', is_flag=True, help='Confirm subscription deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, entity_id: str, subscriber: Tuple[str, ...], event_name: str,
           entity_name: str, confirm: bool, json_output: bool):
    """Delete a whole subscription.

    ⚠️  This removes the subscription record and, for an Asset, deletes the asset's notification
    topic — so every subscriber is unsubscribed, not only the ones named here. To remove one
    subscriber, use 'vamscli subscription unsubscribe'.

    The --confirm flag is required.

    Examples:
        vamscli subscription delete -i my-asset -s alice@example.com --confirm
        vamscli subscription delete -i my-asset -s alice@example.com --confirm --json-output
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
                    "message": "Subscription deletion requires the --confirm flag",
                    "entityId": entity_id
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                click.secho("⚠️  Subscription deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("This deletes the notification topic, unsubscribing every subscriber.")
                click.echo("Use --confirm flag to proceed with subscription deletion.")
                raise click.ClickException("Confirmation required for subscription deletion")

        output_status(f"Deleting the subscription on '{entity_id}'...", json_output)

        result = api_client.delete_subscription(
            event_name, entity_name, entity_id, builtin_list(subscriber))
        output_result(
            _message(result),
            json_output,
            success_message="✓ Subscription deleted successfully!",
        )
    except SubscriptionNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Subscription Not Found",
            helpful_message="Use 'vamscli subscription list' to see existing subscriptions.",
        )
        raise click.ClickException(str(e))
    except InvalidSubscriptionDataError as e:
        output_error(e, json_output, error_type="Invalid Subscription Data")
        raise click.ClickException(str(e))


@subscription.command()
@click.option('-i', '--entity-id', required=True,
              help='[REQUIRED] ID of the subscribed entity (the assetId for an Asset)')
@click.option('-s', '--subscriber', required=True,
              help='[REQUIRED] The one VAMS user ID to remove from the subscription')
@click.option('--event-name', default=SUBSCRIPTION_EVENT_ASSET_VERSION_CHANGE,
              show_default=True, help='Event the subscription is for')
@click.option('--entity-name', default=SUBSCRIPTION_ENTITY_ASSET,
              show_default=True, help='Entity type the ID refers to')
@click.option('--confirm', is_flag=True, help='Confirm removing the subscriber')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def unsubscribe(ctx: click.Context, entity_id: str, subscriber: str, event_name: str,
                entity_name: str, confirm: bool, json_output: bool):
    """Remove one subscriber from a subscription.

    The subscription and its remaining subscribers stay. The endpoint removes a single user, so
    this takes one --subscriber. The --confirm flag is required.

    Examples:
        vamscli subscription unsubscribe -i my-asset -s bob@example.com --confirm
        vamscli subscription unsubscribe -i my-asset -s bob@example.com --confirm --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Require confirmation for removal
        if not confirm:
            if json_output:
                import sys
                error_result = {
                    "error": "Confirmation required",
                    "message": "Unsubscribing requires the --confirm flag",
                    "entityId": entity_id,
                    "subscriber": subscriber
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                click.secho("⚠️  Unsubscribing requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("Use --confirm flag to proceed with removing the subscriber.")
                raise click.ClickException("Confirmation required for unsubscribing")

        output_status(f"Removing '{subscriber}' from the subscription on '{entity_id}'...", json_output)

        result = api_client.unsubscribe(event_name, entity_name, entity_id, subscriber)
        output_result(
            _message(result),
            json_output,
            success_message="✓ Subscriber removed successfully!",
        )
    except SubscriptionNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Subscription Not Found",
            helpful_message="Use 'vamscli subscription list' to see who is subscribed.",
        )
        raise click.ClickException(str(e))
    except InvalidSubscriptionDataError as e:
        output_error(e, json_output, error_type="Invalid Subscription Data")
        raise click.ClickException(str(e))


@subscription.command()
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID to check')
@click.option('-u', '--user-id', required=True, help='[REQUIRED] VAMS user ID to look for')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def check(ctx: click.Context, asset_id: str, user_id: str, json_output: bool):
    """Check whether a user is subscribed to an asset's version changes.

    The endpoint answers for the asset version-change event only, so neither the event nor the
    entity type is an option here.

    Examples:
        vamscli subscription check -a my-asset -u alice@example.com
        vamscli subscription check -a my-asset -u alice@example.com --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status(f"Checking the subscription of '{user_id}' on '{asset_id}'...", json_output)

    try:
        result = api_client.check_subscription(asset_id, user_id)
        output_result(
            _message(result),
            json_output,
            success_message="Subscription check completed",
            cli_formatter=lambda r: f"  Result: {r}",
        )
    except InvalidSubscriptionDataError as e:
        output_error(e, json_output, error_type="Invalid Subscription Data")
        raise click.ClickException(str(e))
    except AssetNotFoundError as e:
        output_error(
            e, json_output,
            error_type="Asset Not Found",
            helpful_message="Use 'vamscli assets list' to see available assets.",
        )
        raise click.ClickException(str(e))
