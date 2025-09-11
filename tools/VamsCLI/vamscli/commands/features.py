"""Feature switches commands for VamsCLI."""

import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context, requires_feature
from ..utils.features import get_enabled_features, is_feature_enabled
from ..constants import FEATURE_GOVCLOUD, FEATURE_LOCATIONSERVICES


@click.group()
def features():
    """Feature switches management commands."""
    pass


@features.command('list')
@click.pass_context
def list_features(ctx: click.Context):
    """
    List all enabled feature switches for the current profile.
    
    This command shows which features are currently enabled based on
    the backend configuration fetched during authentication.
    
    Examples:
        vamscli features list
    """
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_auth_profile():
        click.echo("Not authenticated. Run 'vamscli auth login' to authenticate.")
        return
    
    feature_switches_info = profile_manager.get_feature_switches_info()
    
    if not feature_switches_info['has_feature_switches']:
        click.echo("No feature switches available. Try re-authenticating to fetch latest features.")
        return
    
    click.echo("Enabled Feature Switches:")
    click.echo(f"Total: {feature_switches_info['count']}")
    
    if feature_switches_info['enabled']:
        click.echo("\nFeatures:")
        for feature in sorted(feature_switches_info['enabled']):
            click.echo(f"  ✓ {feature}")
    else:
        click.echo("\nNo features are currently enabled.")
    
    if feature_switches_info['fetched_at']:
        click.echo(f"\nLast updated: {feature_switches_info['fetched_at']}")


@features.command('check')
@click.argument('feature_name')
@click.pass_context
def check_feature(ctx: click.Context, feature_name: str):
    """
    Check if a specific feature switch is enabled.
    
    Args:
        feature_name: Name of the feature to check
    
    Examples:
        vamscli features check GOVCLOUD
        vamscli features check LOCATIONSERVICES
    """
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_auth_profile():
        click.echo("Not authenticated. Run 'vamscli auth login' to authenticate.")
        return
    
    feature_switches_info = profile_manager.get_feature_switches_info()
    
    if not feature_switches_info['has_feature_switches']:
        click.echo("No feature switches available. Try re-authenticating to fetch latest features.")
        return
    
    is_enabled = is_feature_enabled(feature_name, profile_manager)
    
    if is_enabled:
        click.echo(
            click.style(f"✓ Feature '{feature_name}' is ENABLED", fg='green', bold=True)
        )
    else:
        click.echo(
            click.style(f"✗ Feature '{feature_name}' is DISABLED", fg='red', bold=True)
        )


@features.command('example-govcloud')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_GOVCLOUD, "GovCloud features are not enabled for this environment.")
def example_govcloud_command(ctx: click.Context):
    """
    Example command that requires GOVCLOUD feature to be enabled.
    
    This is a demonstration command showing how to use the @requires_feature
    decorator to restrict commands based on enabled features.
    
    Examples:
        vamscli features example-govcloud
    """
    click.echo(
        click.style("✓ GovCloud feature is enabled! This command can run.", fg='green', bold=True)
    )
    click.echo("This command would perform GovCloud-specific operations.")


@features.command('example-location')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_LOCATIONSERVICES, "Location services are not enabled for this environment.")
def example_location_command(ctx: click.Context):
    """
    Example command that requires LOCATIONSERVICES feature to be enabled.
    
    This is a demonstration command showing how to use the @requires_feature
    decorator to restrict commands based on enabled features.
    
    Examples:
        vamscli features example-location
    """
    click.echo(
        click.style("✓ Location services feature is enabled! This command can run.", fg='green', bold=True)
    )
    click.echo("This command would perform location-based operations.")
