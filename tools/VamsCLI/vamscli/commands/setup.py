"""Setup command for VamsCLI."""

import re
from urllib.parse import urlparse

import click

from ..utils.api_client import APIClient
from ..utils.decorators import get_profile_manager_from_context
from ..utils.exceptions import APIError, ConfigurationError
from ..version import get_version


def validate_api_gateway_url(url: str) -> bool:
    """Validate API Gateway URL format."""
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        
        # Check if it looks like an API Gateway URL
        if 'execute-api' in parsed.netloc or 'amazonaws.com' in parsed.netloc:
            return True
            
        # Allow other HTTPS URLs for flexibility
        return parsed.scheme == 'https'
    except Exception:
        return False


@click.command()
@click.argument('api_gateway_url')
@click.option('--force', '-f', is_flag=True, help='Force setup even if configuration exists')
@click.option('--skip-version-check', is_flag=True, help='Skip version mismatch confirmation prompts')
@click.pass_context
def setup(ctx: click.Context, api_gateway_url: str, force: bool, skip_version_check: bool):
    """
    Setup VamsCLI with API Gateway URL.
    
    This command configures VamsCLI to work with your VAMS API Gateway.
    It will fetch the Amplify configuration and store it locally for the
    specified profile.
    
    Examples:
        # Setup default profile
        vamscli setup https://7bx3w05l79.execute-api.us-west-2.amazonaws.com
        
        # Setup specific profile
        vamscli --profile production setup https://prod-api.example.com
        
        # Force overwrite existing configuration
        vamscli --profile dev setup https://dev-api.example.com --force
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if configuration already exists
    if profile_manager.has_config() and not force:
        profile_name = profile_manager.profile_name
        click.echo(f"Configuration already exists for profile '{profile_name}'. Use --force to overwrite.")
        return
    
    # Validate URL format
    if not validate_api_gateway_url(api_gateway_url):
        raise click.BadParameter(
            "Invalid API Gateway URL. Please provide a valid HTTPS URL."
        )
    
    # Ensure URL doesn't end with slash
    api_gateway_url = api_gateway_url.rstrip('/')
    
    profile_name = profile_manager.profile_name
    click.echo(f"Setting up VamsCLI with API Gateway: {api_gateway_url}")
    click.echo(f"Profile: {profile_name}")
    
    try:
        # Create API client
        api_client = APIClient(api_gateway_url, profile_manager)
        
        # Check API version
        click.echo("Checking API version...")
        version_info = api_client.check_version()
        
        if not version_info['match']:
            click.echo(
                click.style(
                    "WARNING: Version mismatch detected:", 
                    fg='yellow', bold=True
                )
            )
            click.echo(f"   CLI version: {version_info['cli_version']}")
            click.echo(f"   API version: {version_info['api_version']}")
            click.echo("   This may cause compatibility issues.")
            
            if not skip_version_check and not click.confirm("Continue with setup?"):
                click.echo("Setup cancelled.")
                return
            elif skip_version_check:
                click.echo("   Skipping version check confirmation (--skip-version-check enabled)")
        else:
            click.echo(
                click.style(
                    f"✓ Version match: {version_info['cli_version']}", 
                    fg='green'
                )
            )
        
        # Fetch Amplify configuration
        click.echo("Fetching Amplify configuration...")
        amplify_config = api_client.get_amplify_config()
        
        # Wipe existing profile configuration if force is used
        if force:
            click.echo("Removing existing configuration...")
            profile_manager.wipe_profile()
        
        # Save configuration
        from datetime import datetime
        config = {
            'api_gateway_url': api_gateway_url,
            'amplify_config': amplify_config,
            'cli_version': get_version(),
            'setup_timestamp': datetime.utcnow().isoformat()
        }
        
        profile_manager.save_config(config)
        
        click.echo(
            click.style("Setup completed successfully!", fg='green', bold=True)
        )
        click.echo(f"Configuration saved to: {profile_manager.config_dir}")
        click.echo("\nNext steps:")
        if profile_name != 'default':
            click.echo(f"1. Run 'vamscli --profile {profile_name} auth login -u <username>' to authenticate")
            click.echo(f"2. Use 'vamscli --profile {profile_name} --help' to see available commands")
        else:
            click.echo("1. Run 'vamscli auth login -u <username>' to authenticate")
            click.echo("2. Use 'vamscli --help' to see available commands")
        
    except APIError as e:
        click.echo(
            click.style(f"✗ API Error: {e}", fg='red', bold=True), 
            err=True
        )
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Setup failed: {e}", fg='red', bold=True), 
            err=True
        )
        raise click.ClickException(str(e))
