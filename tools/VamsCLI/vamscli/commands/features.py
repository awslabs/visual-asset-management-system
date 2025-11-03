"""Feature switches commands for VamsCLI."""

import json
import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context, requires_feature
from ..utils.features import get_enabled_features, is_feature_enabled
from ..utils.json_output import output_status, output_result, output_error, output_info
from ..constants import FEATURE_GOVCLOUD, FEATURE_LOCATIONSERVICES


@click.group()
def features():
    """Feature switches management commands."""
    pass


@features.command('list')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
def list_features(ctx: click.Context, json_output: bool):
    """
    List all enabled feature switches for the current profile.
    
    This command shows which features are currently enabled based on
    the backend configuration fetched during authentication.
    
    Examples:
        vamscli features list
        vamscli features list --json-output
    """
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_auth_profile():
        error_msg = "Not authenticated. Run 'vamscli auth login' to authenticate."
        output_error(
            Exception(error_msg),
            json_output,
            error_type="Authentication Required",
            helpful_message="Use 'vamscli auth login' to authenticate."
        )
        if not json_output:
            raise click.ClickException(error_msg)
        else:
            raise SystemExit(1)
    
    feature_switches_info = profile_manager.get_feature_switches_info()
    
    if not feature_switches_info['has_feature_switches']:
        error_msg = "No feature switches available. Try re-authenticating to fetch latest features."
        output_error(
            Exception(error_msg),
            json_output,
            error_type="No Feature Switches",
            helpful_message="Use 'vamscli auth login' to re-authenticate and fetch latest features."
        )
        if not json_output:
            raise click.ClickException(error_msg)
        else:
            raise SystemExit(1)
    
    # Build result data
    result = {
        'total': feature_switches_info['count'],
        'enabled': sorted(feature_switches_info['enabled']),
        'fetched_at': feature_switches_info['fetched_at']
    }
    
    def format_features_list(data):
        """Format features list for CLI display."""
        lines = []
        lines.append("Enabled Feature Switches:")
        lines.append(f"Total: {data['total']}")
        
        if data['enabled']:
            lines.append("\nFeatures:")
            for feature in data['enabled']:
                lines.append(f"  ✓ {feature}")
        else:
            lines.append("\nNo features are currently enabled.")
        
        if data['fetched_at']:
            lines.append(f"\nLast updated: {data['fetched_at']}")
        
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_features_list)
    return result


@features.command('check')
@click.argument('feature_name')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
def check_feature(ctx: click.Context, feature_name: str, json_output: bool):
    """
    Check if a specific feature switch is enabled.
    
    Args:
        feature_name: Name of the feature to check
    
    Examples:
        vamscli features check GOVCLOUD
        vamscli features check LOCATIONSERVICES
        vamscli features check GOVCLOUD --json-output
    """
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_auth_profile():
        error_msg = "Not authenticated. Run 'vamscli auth login' to authenticate."
        output_error(
            Exception(error_msg),
            json_output,
            error_type="Authentication Required",
            helpful_message="Use 'vamscli auth login' to authenticate."
        )
        if not json_output:
            raise click.ClickException(error_msg)
        else:
            raise SystemExit(1)
    
    feature_switches_info = profile_manager.get_feature_switches_info()
    
    if not feature_switches_info['has_feature_switches']:
        error_msg = "No feature switches available. Try re-authenticating to fetch latest features."
        output_error(
            Exception(error_msg),
            json_output,
            error_type="No Feature Switches",
            helpful_message="Use 'vamscli auth login' to re-authenticate and fetch latest features."
        )
        if not json_output:
            raise click.ClickException(error_msg)
        else:
            raise SystemExit(1)
    
    is_enabled = is_feature_enabled(feature_name, profile_manager)
    
    # Build result data
    result = {
        'feature_name': feature_name,
        'enabled': is_enabled
    }
    
    def format_feature_check(data):
        """Format feature check result for CLI display."""
        if data['enabled']:
            return click.style(f"✓ Feature '{data['feature_name']}' is ENABLED", fg='green', bold=True)
        else:
            return click.style(f"✗ Feature '{data['feature_name']}' is DISABLED", fg='red', bold=True)
    
    output_result(result, json_output, cli_formatter=format_feature_check)
    return result


@features.command('example-govcloud')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_GOVCLOUD, "GovCloud features are not enabled for this environment.")
def example_govcloud_command(ctx: click.Context, json_output: bool):
    """
    Example command that requires GOVCLOUD feature to be enabled.
    
    This is a demonstration command showing how to use the @requires_feature
    decorator to restrict commands based on enabled features.
    
    Examples:
        vamscli features example-govcloud
        vamscli features example-govcloud --json-output
    """
    # Build result data
    result = {
        'feature': FEATURE_GOVCLOUD,
        'enabled': True,
        'message': 'GovCloud feature is enabled! This command can run.',
        'description': 'This command would perform GovCloud-specific operations.'
    }
    
    def format_govcloud_example(data):
        """Format GovCloud example result for CLI display."""
        lines = []
        lines.append(click.style(f"✓ {data['message']}", fg='green', bold=True))
        lines.append(data['description'])
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_govcloud_example)
    return result


@features.command('example-location')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_LOCATIONSERVICES, "Location services are not enabled for this environment.")
def example_location_command(ctx: click.Context, json_output: bool):
    """
    Example command that requires LOCATIONSERVICES feature to be enabled.
    
    This is a demonstration command showing how to use the @requires_feature
    decorator to restrict commands based on enabled features.
    
    Examples:
        vamscli features example-location
        vamscli features example-location --json-output
    """
    # Build result data
    result = {
        'feature': FEATURE_LOCATIONSERVICES,
        'enabled': True,
        'message': 'Location services feature is enabled! This command can run.',
        'description': 'This command would perform location-based operations.'
    }
    
    def format_location_example(data):
        """Format location services example result for CLI display."""
        lines = []
        lines.append(click.style(f"✓ {data['message']}", fg='green', bold=True))
        lines.append(data['description'])
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_location_example)
    return result
