"""Setup command for VamsCLI."""

import re
from urllib.parse import urlparse

import click

from ..utils.api_client import APIClient
from ..utils.decorators import get_profile_manager_from_context
from ..utils.exceptions import ConfigurationError
from ..version import get_version


def validate_base_url(url: str) -> bool:
    """Validate base URL format - accepts any HTTP/HTTPS URL."""
    try:
        parsed = urlparse(url)
        # Must have scheme and netloc
        if not parsed.scheme or not parsed.netloc:
            return False
        
        # Must be HTTP or HTTPS
        return parsed.scheme.lower() in ['http', 'https']
    except Exception:
        return False


@click.command()
@click.argument('base_url')
@click.option('--force', '-f', is_flag=True, help='Force setup even if configuration exists')
@click.option('--skip-version-check', is_flag=True, help='Skip version mismatch confirmation prompts')
@click.pass_context
def setup(ctx: click.Context, base_url: str, force: bool, skip_version_check: bool):
    """
    Setup VamsCLI with VAMS base URL.
    
    This command configures VamsCLI to work with your VAMS deployment.
    It accepts any HTTP/HTTPS base URL (CloudFront, ALB, API Gateway, or custom domain),
    fetches the Amplify configuration, and extracts the API Gateway URL for storage.
    
    Examples:
        # Setup with CloudFront distribution
        vamscli setup https://d1234567890.cloudfront.net
        
        # Setup with custom domain
        vamscli setup https://vams.mycompany.com
        
        # Setup with ALB
        vamscli setup https://my-alb-123456789.us-west-2.elb.amazonaws.com
        
        # Setup with API Gateway directly
        vamscli setup https://abc123.execute-api.us-west-2.amazonaws.com
        
        # Setup specific profile
        vamscli --profile production setup https://prod-vams.example.com
        
        # Force overwrite existing configuration
        vamscli --profile dev setup https://dev-vams.example.com --force
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if configuration already exists
    if profile_manager.has_config() and not force:
        profile_name = profile_manager.profile_name
        click.echo(f"Configuration already exists for profile '{profile_name}'. Use --force to overwrite.")
        return
    
    # Validate URL format
    if not validate_base_url(base_url):
        raise click.BadParameter(
            "Invalid base URL. Please provide a valid HTTP/HTTPS URL."
        )
    
    # Ensure URL doesn't end with slash
    base_url = base_url.rstrip('/')
    
    profile_name = profile_manager.profile_name
    click.echo(f"Setting up VamsCLI with base URL: {base_url}")
    click.echo(f"Profile: {profile_name}")
    
    try:
        # Create API client with base URL to fetch amplify config
        api_client = APIClient(base_url, profile_manager)
        
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
        
        # Extract API Gateway URL from amplify config
        api_gateway_url = amplify_config.get('api')
        if not api_gateway_url:
            raise ConfigurationError(
                "No 'api' field found in amplify configuration response. "
                "Please verify the base URL points to a valid VAMS deployment."
            )
        
        # Validate extracted API Gateway URL
        if not validate_base_url(api_gateway_url):
            raise ConfigurationError(
                f"Invalid API Gateway URL extracted from amplify config: {api_gateway_url}"
            )
        
        # Ensure extracted API Gateway URL doesn't end with slash
        api_gateway_url = api_gateway_url.rstrip('/')
        
        click.echo(f"✓ Extracted API Gateway URL: {api_gateway_url}")
        
        # Wipe existing profile configuration if force is used
        if force:
            click.echo("Removing existing configuration...")
            profile_manager.wipe_profile()
        
        # Save configuration with both base URL and extracted API Gateway URL
        from datetime import datetime
        config = {
            'base_url': base_url,
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
        
    except ConfigurationError as e:
        # Only handle setup-specific business logic errors
        click.echo(
            click.style(f"✗ Configuration Error: {e}", fg='red', bold=True), 
            err=True
        )
        raise click.ClickException(str(e))
