"""Setup command for VamsCLI."""

import re
from urllib.parse import urlparse

import click

from ..utils.api_client import APIClient
from ..utils.decorators import get_profile_manager_from_context
from ..utils.exceptions import ConfigurationError
from ..utils.json_output import output_status, output_result, output_error, output_warning, output_info
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
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
def setup(ctx: click.Context, base_url: str, force: bool, skip_version_check: bool, json_output: bool):
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
        
        # JSON output mode
        vamscli setup https://vams.example.com --json-output
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if configuration already exists
    if profile_manager.has_config() and not force:
        profile_name = profile_manager.profile_name
        message = f"Configuration already exists for profile '{profile_name}'. Use --force to overwrite."
        
        if json_output:
            result = {
                'status': 'skipped',
                'message': message,
                'profile': profile_name,
                'force_required': True
            }
            output_result(result, json_output)
        else:
            output_info(message, json_output)
        return
    
    # Validate URL format
    if not validate_base_url(base_url):
        error = click.BadParameter(
            "Invalid base URL. Please provide a valid HTTP/HTTPS URL."
        )
        output_error(error, json_output, error_type="Invalid URL")
        raise error
    
    # Ensure URL doesn't end with slash
    base_url = base_url.rstrip('/')
    
    profile_name = profile_manager.profile_name
    
    # Status messages only in CLI mode
    output_status(f"Setting up VamsCLI with base URL: {base_url}", json_output)
    output_status(f"Profile: {profile_name}", json_output)
    
    try:
        # Create API client with base URL to fetch amplify config
        api_client = APIClient(base_url, profile_manager)
        
        # Check API version
        output_status("Checking API version...", json_output)
        version_info = api_client.check_version()
        
        if not version_info['match']:
            output_warning(
                "WARNING: Version mismatch detected:",
                json_output
            )
            output_info(f"   CLI version: {version_info['cli_version']}", json_output)
            output_info(f"   API version: {version_info['api_version']}", json_output)
            output_info("   This may cause compatibility issues.", json_output)
            
            if not skip_version_check and not json_output:
                if not click.confirm("Continue with setup?"):
                    output_info("Setup cancelled.", json_output)
                    return
            elif skip_version_check:
                output_info("   Skipping version check confirmation (--skip-version-check enabled)", json_output)
        else:
            output_status(
                f"✓ Version match: {version_info['cli_version']}", 
                json_output
            )
        
        # Fetch Amplify configuration
        output_status("Fetching Amplify configuration...", json_output)
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
        
        output_status(f"✓ Extracted API Gateway URL: {api_gateway_url}", json_output)
        
        # Wipe existing profile configuration if force is used
        if force:
            output_status("Removing existing configuration...", json_output)
            profile_manager.wipe_profile()
        
        # Save configuration with both base URL and extracted API Gateway URL
        from datetime import datetime, timezone
        config = {
            'base_url': base_url,
            'api_gateway_url': api_gateway_url,
            'amplify_config': amplify_config,
            'cli_version': get_version(),
            'setup_timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        profile_manager.save_config(config)
        
        # Prepare result
        result = {
            'status': 'success',
            'message': 'Setup completed successfully!',
            'profile': profile_name,
            'base_url': base_url,
            'api_gateway_url': api_gateway_url,
            'cli_version': version_info['cli_version'],
            'api_version': version_info['api_version'],
            'version_match': version_info['match'],
            'config_path': str(profile_manager.config_dir)
        }
        
        def format_setup_result(data):
            """Format setup result for CLI display."""
            lines = []
            lines.append(f"Configuration saved to: {data['config_path']}")
            lines.append("\nNext steps:")
            if profile_name != 'default':
                lines.append(f"1. Run 'vamscli --profile {profile_name} auth login -u <username>' to authenticate")
                lines.append(f"2. Use 'vamscli --profile {profile_name} --help' to see available commands")
            else:
                lines.append("1. Run 'vamscli auth login -u <username>' to authenticate")
                lines.append("2. Use 'vamscli --help' to see available commands")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="Setup completed successfully!",
            cli_formatter=format_setup_result
        )
        
    except ConfigurationError as e:
        # Only handle setup-specific business logic errors
        output_error(
            e,
            json_output,
            error_type="Configuration Error",
            helpful_message="Please verify the base URL points to a valid VAMS deployment."
        )
        raise click.ClickException(str(e))
