"""Authentication commands for VamsCLI."""

import click
import datetime

from ..auth.cognito import CognitoAuthenticator
from ..utils.decorators import requires_api_access, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_warning, output_info
from ..utils.exceptions import AuthenticationError, ConfigurationError, OverrideTokenError


def get_authenticator(config: dict) -> CognitoAuthenticator:
    """Create authenticator from configuration."""
    amplify_config = config.get('amplify_config', {})
    
    region = amplify_config.get('region')
    user_pool_id = amplify_config.get('cognitoUserPoolId')
    client_id = amplify_config.get('cognitoAppClientId')
    
    if not all([region, user_pool_id, client_id]):
        raise ConfigurationError(
            "Missing Cognito configuration. Please run 'vamscli setup' first."
        )
    
    return CognitoAuthenticator(region, user_pool_id, client_id)


@click.group()
def auth():
    """Authentication commands."""
    pass


@auth.command()
@click.option('-u', '--username', help='Username for Cognito authentication')
@click.option('-p', '--password', help='Password (will prompt if not provided)')
@click.option('--save-credentials', is_flag=True, help='Save credentials for automatic re-authentication')
@click.option('--user-id', help='User ID for token override authentication')
@click.option('--token-override', help='Override token for external authentication (requires --user-id)')
@click.option('--expires-at', help='Token expiration time (Unix timestamp, ISO 8601, or +seconds)')
@click.option('--skip-version-check', is_flag=True, help='Skip version mismatch confirmation prompts')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def login(ctx: click.Context, username: str, password: str, save_credentials: bool, user_id: str, 
          token_override: str, expires_at: str, skip_version_check: bool, json_output: bool):
    """
    Authenticate with VAMS using Cognito or token override.
    
    This command authenticates you with the VAMS system using AWS Cognito or
    an external token override. It will handle MFA challenges and password 
    reset requirements automatically for Cognito authentication.
    
    For token override authentication, provide --user-id and --token-override.
    The token will be saved and validated against the VAMS API.
    
    Examples:
        # Cognito authentication
        vamscli auth login -u john.doe@example.com
        vamscli auth login -u john.doe@example.com -p mypassword
        vamscli auth login -u john.doe@example.com --save-credentials
        
        # Token override authentication
        vamscli auth login --user-id john.doe@example.com --token-override "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
        vamscli auth login --user-id john.doe@example.com --token-override "token123" --expires-at "+3600"
        
        # JSON output
        vamscli auth login -u john.doe@example.com --json-output
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        profile_name = profile_manager.profile_name
        error_msg = (
            f"Configuration not found for profile '{profile_name}'. "
            f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
        )
        output_error(ConfigurationError(error_msg), json_output, error_type="Configuration Error")
        raise click.ClickException(error_msg)
    
    # Load configuration
    try:
        config = profile_manager.load_config()
    except Exception as e:
        output_error(e, json_output, error_type="Configuration Error")
        raise click.ClickException(f"Failed to load configuration: {e}")
    
    # Check API version compatibility
    try:
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        output_status("Checking API version...", json_output)
        version_info = api_client.check_version()
        
        if not version_info['match']:
            warning_msg = (
                f"Version mismatch detected:\n"
                f"   CLI version: {version_info['cli_version']}\n"
                f"   API version: {version_info['api_version']}\n"
                f"   This may cause compatibility issues."
            )
            output_warning(warning_msg, json_output)
            
            if not skip_version_check and not json_output and not click.confirm("Continue with authentication?"):
                output_info("Authentication cancelled.", json_output)
                return
            elif skip_version_check:
                output_info("Skipping version check confirmation (--skip-version-check enabled)", json_output)
        else:
            output_status(f"✓ Version match: {version_info['cli_version']}", json_output)
    except Exception as e:
        # Version check failure shouldn't block authentication
        output_warning(f"Could not check API version: {e}", json_output)
    
    # Validate input combinations
    if token_override and not user_id:
        error_msg = "--user-id is required when using --token-override"
        output_error(click.BadParameter(error_msg), json_output, error_type="Invalid Parameters")
        raise click.ClickException(error_msg)
    
    if token_override and save_credentials:
        error_msg = "--save-credentials cannot be used with --token-override (override tokens don't use traditional credentials)"
        output_error(click.BadParameter(error_msg), json_output, error_type="Invalid Parameters")
        raise click.ClickException(error_msg)
    
    if token_override:
        # Token override authentication path
        try:
            output_status("Using token override authentication...", json_output)
            
            # Save the override token
            profile_manager.save_override_token(token_override, user_id, expires_at)
            
            # Call login profile API to validate the token and refresh user profile
            try:
                api_client = APIClient(config['api_gateway_url'], profile_manager)
                login_profile_result = api_client.call_login_profile(user_id)
                
                output_status("User profile refreshed successfully.", json_output)
                
                # Fetch feature switches after successful validation
                try:
                    secure_config_result = api_client.get_secure_config()
                    profile_manager.save_feature_switches(secure_config_result)
                    output_status("Feature switches updated successfully.", json_output)
                except Exception as fs_e:
                    # Feature switches fetch failure is non-blocking
                    output_warning(f"Could not fetch feature switches: {fs_e}", json_output)
                
            except AuthenticationError as e:
                # If login profile fails with 401/403, credentials are already cleared
                output_error(e, json_output, error_type="Authentication Error")
                raise click.ClickException(str(e))
            except Exception as e:
                # If login profile API fails for other reasons, warn but keep the token
                output_warning(f"Could not validate token with user profile: {e}", json_output)
            
            # Prepare result
            result = {
                'success': True,
                'authentication_type': 'token_override',
                'user_id': user_id,
                'message': 'Token override authentication successful'
            }
            
            # Show expiration info if provided
            if expires_at:
                expiration_info = profile_manager.get_token_expiration_info()
                if expiration_info.get('expires_in_human'):
                    result['expires_in'] = expiration_info['expires_in_human']
            else:
                result['expiration_note'] = 'No expiration time set - token will be used until it fails'
            
            result['refresh_note'] = 'Override tokens do not support automatic refresh'
            
            def format_override_result(data):
                """Format override authentication result for CLI display."""
                lines = []
                lines.append(f"  User ID: {data['user_id']}")
                if 'expires_in' in data:
                    lines.append(f"  Token expires in: {data['expires_in']}")
                else:
                    lines.append(f"  Expiration: {data['expiration_note']}")
                lines.append(f"  Note: {data['refresh_note']}")
                return '\n'.join(lines)
            
            output_result(
                result,
                json_output,
                success_message="✓ Token override authentication successful!",
                cli_formatter=format_override_result
            )
            
        except Exception as e:
            output_error(e, json_output, error_type="Token Override Authentication Error")
            raise click.ClickException(str(e))
    
    else:
        # Cognito authentication path
        # NEW: Check if Cognito is configured
        amplify_config = config.get('amplify_config', {})
        cognito_user_pool_id = amplify_config.get('cognitoUserPoolId')
        
        if not cognito_user_pool_id or cognito_user_pool_id in ['undefined', 'null', '']:
            error_msg = (
                "Cognito authentication is not configured for this environment. "
                "This deployment uses external authentication. "
                "Please use token override authentication with: "
                "'vamscli auth login --user-id <user-id> --token-override <token>'"
            )
            output_error(ConfigurationError(error_msg), json_output, error_type="Configuration Error")
            raise click.ClickException(error_msg)
        
        if not username:
            error_msg = "--username is required for Cognito authentication"
            output_error(click.BadParameter(error_msg), json_output, error_type="Invalid Parameters")
            raise click.ClickException(error_msg)
        
        # Prompt for password if not provided (only in CLI mode)
        if not password:
            if json_output:
                error_msg = "--password is required when using --json-output"
                output_error(click.BadParameter(error_msg), json_output, error_type="Invalid Parameters")
                raise click.ClickException(error_msg)
            password = click.prompt("Password", hide_input=True)
        
        try:
            # Create authenticator
            authenticator = get_authenticator(config)
            
            output_status("Authenticating with Cognito...", json_output)
            
            # Authenticate user
            auth_result = authenticator.authenticate(username, password)
            
            # Add user_id to the auth result
            auth_result['user_id'] = username
            
            # Save authentication profile
            profile_manager.save_auth_profile(auth_result)
            
            # Call login profile API to refresh user profile and validate authentication
            try:
                api_client = APIClient(config['api_gateway_url'], profile_manager)
                login_profile_result = api_client.call_login_profile(username)
                output_status("User profile refreshed successfully.", json_output)
                
                # Fetch feature switches after successful authentication
                try:
                    secure_config_result = api_client.get_secure_config()
                    profile_manager.save_feature_switches(secure_config_result)
                    output_status("Feature switches updated successfully.", json_output)
                except Exception as fs_e:
                    # Feature switches fetch failure is non-blocking
                    output_warning(f"Could not fetch feature switches: {fs_e}", json_output)
                    
            except AuthenticationError as e:
                # If login profile fails with 401/403, credentials are already cleared
                output_error(e, json_output, error_type="Authentication Error")
                raise click.ClickException(str(e))
            except Exception as e:
                # If login profile API fails for other reasons, warn but don't fail authentication
                output_warning(f"Could not refresh user profile: {e}", json_output)
            
            # Save credentials if requested
            if save_credentials:
                profile_manager.save_credentials({
                    'username': username,
                    'password': password
                })
                output_status("Credentials saved for automatic re-authentication.", json_output)
            
            # Prepare result
            result = {
                'success': True,
                'authentication_type': 'cognito',
                'user_id': username,
                'expires_in_seconds': auth_result['expires_in'],
                'message': 'Cognito authentication successful'
            }
            
            if save_credentials:
                result['credentials_saved'] = True
            
            def format_cognito_result(data):
                """Format Cognito authentication result for CLI display."""
                lines = []
                lines.append(f"  User ID: {data['user_id']}")
                lines.append(f"  Access token expires in: {data['expires_in_seconds']} seconds")
                if data.get('credentials_saved'):
                    lines.append("  Credentials saved for automatic re-authentication")
                return '\n'.join(lines)
            
            output_result(
                result,
                json_output,
                success_message="✓ Cognito authentication successful!",
                cli_formatter=format_cognito_result
            )
            
        except AuthenticationError as e:
            output_error(e, json_output, error_type="Cognito Authentication Error")
            raise click.ClickException(str(e))
        except Exception as e:
            output_error(e, json_output, error_type="Unexpected Error")
            raise click.ClickException(str(e))


@auth.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
def logout(ctx: click.Context, json_output: bool):
    """
    Remove authentication profile and saved credentials.
    
    This command will log you out by removing your stored authentication
    tokens and any saved credentials.
    
    Examples:
        vamscli auth logout
        vamscli auth logout --json-output
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_auth_profile() and not profile_manager.has_credentials():
        result = {
            'success': False,
            'message': 'No authentication profile found'
        }
        output_result(result, json_output)
        return
    
    # Delete authentication profile and credentials
    profile_manager.delete_auth_profile()
    
    result = {
        'success': True,
        'message': 'Logged out successfully',
        'details': 'Authentication profile and saved credentials removed'
    }
    
    def format_logout_result(data):
        """Format logout result for CLI display."""
        return f"  {data['details']}"
    
    output_result(
        result,
        json_output,
        success_message="✓ Logged out successfully!",
        cli_formatter=format_logout_result
    )


@auth.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
def status(ctx: click.Context, json_output: bool):
    """
    Show authentication status.
    
    This command displays information about your current authentication
    status, including token expiration times and feature switches.
    
    Examples:
        vamscli auth status
        vamscli auth status --json-output
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_config():
        result = {
            'success': False,
            'message': 'Configuration not found',
            'help': 'Please run "vamscli setup" first'
        }
        output_result(result, json_output)
        return
    
    if not profile_manager.has_auth_profile():
        result = {
            'success': False,
            'authenticated': False,
            'message': 'Not authenticated',
            'help': 'Run "vamscli auth login" to authenticate'
        }
        output_result(result, json_output)
        return
    
    try:
        expiration_info = profile_manager.get_token_expiration_info()
        
        if not expiration_info['has_token']:
            result = {
                'success': False,
                'authenticated': False,
                'message': 'No authentication profile found'
            }
            output_result(result, json_output)
            return
        
        token_type = expiration_info['token_type']
        is_override = token_type == 'override'
        
        # Get user ID from auth profile
        auth_profile = profile_manager.load_auth_profile()
        user_id = auth_profile.get('user_id', 'Unknown') if auth_profile else 'Unknown'
        
        # Build result
        result = {
            'success': True,
            'authenticated': True,
            'authentication_type': 'override_token' if is_override else 'cognito_token',
            'user_id': user_id
        }
        
        if expiration_info['has_expiration']:
            result['has_expiration'] = True
            result['is_expired'] = expiration_info['is_expired']
            result['expires_at_timestamp'] = expiration_info['expires_at']
            
            expires_at = datetime.datetime.fromtimestamp(expiration_info['expires_at'])
            result['expires_at'] = expires_at.isoformat() + 'Z'
            
            if not expiration_info['is_expired'] and expiration_info['expires_in_human']:
                result['expires_in'] = expiration_info['expires_in_human']
        else:
            result['has_expiration'] = False
            if is_override:
                result['expiration_note'] = 'No expiration set'
            else:
                # For Cognito tokens, check validity using authenticator
                try:
                    config = profile_manager.load_config()
                    auth_profile = profile_manager.load_auth_profile()
                    authenticator = get_authenticator(config)
                    is_valid = authenticator.is_token_valid(auth_profile)
                    result['is_valid'] = is_valid
                except Exception:
                    result['is_valid'] = None
        
        if is_override:
            result['source'] = 'external'
            result['refresh_supported'] = False
        else:
            result['saved_credentials'] = profile_manager.has_credentials()
            result['refresh_supported'] = True
        
        # Add webDeployedUrl and locationServiceApiUrl (backward compatible)
        if auth_profile:
            web_url = auth_profile.get('web_deployed_url')
            if web_url:
                result['web_deployed_url'] = web_url
            
            location_url = auth_profile.get('location_service_api_url')
            if location_url:
                result['location_service_api_url'] = location_url
        
        # Show feature switches information
        feature_switches_info = profile_manager.get_feature_switches_info()
        if feature_switches_info['has_feature_switches']:
            result['feature_switches'] = {
                'count': feature_switches_info['count'],
                'enabled': sorted(feature_switches_info['enabled']) if feature_switches_info['enabled'] else [],
                'fetched_at': feature_switches_info['fetched_at']
            }
        else:
            result['feature_switches'] = None
        
        def format_status_result(data):
            """Format status result for CLI display."""
            lines = []
            lines.append(f"  Type: {data['authentication_type'].replace('_', ' ').title()}")
            lines.append(f"  User ID: {data['user_id']}")
            
            if data.get('has_expiration'):
                if data['is_expired']:
                    lines.append("  Status: ✗ Expired")
                else:
                    lines.append("  Status: ✓ Valid")
                    if data.get('expires_in'):
                        lines.append(f"  Expires in: {data['expires_in']}")
                lines.append(f"  Expires at: {data['expires_at']}")
            else:
                if data.get('is_valid') is not None:
                    lines.append(f"  Status: {'✓ Valid' if data['is_valid'] else '✗ Expired'}")
                elif data.get('expiration_note'):
                    lines.append(f"  Status: ✓ Valid ({data['expiration_note']})")
                else:
                    lines.append("  Status: Unknown")
            
            if data.get('source'):
                lines.append(f"  Source: {data['source'].title()}")
            
            if data.get('saved_credentials') is not None:
                lines.append(f"  Saved credentials: {'Yes' if data['saved_credentials'] else 'No'}")
            
            lines.append(f"  Refresh: {'Supported' if data['refresh_supported'] else 'Not supported'}")
            
            # Show webDeployedUrl (backward compatible - only if present)
            if data.get('web_deployed_url'):
                lines.append(f"  Web Deployed URL: {data['web_deployed_url']}")
            
            # Show locationServiceApiUrl (backward compatible - only if present)
            if data.get('location_service_api_url'):
                lines.append(f"  Location Service URL: {data['location_service_api_url']}")
            
            # Feature switches
            if data.get('feature_switches'):
                fs = data['feature_switches']
                lines.append("")
                lines.append("Feature Switches:")
                lines.append(f"  Count: {fs['count']}")
                if fs['enabled']:
                    lines.append("  Enabled features:")
                    for feature in fs['enabled']:
                        lines.append(f"    - {feature}")
                else:
                    lines.append("  No features enabled")
                if fs['fetched_at']:
                    lines.append(f"  Last updated: {fs['fetched_at']}")
            else:
                lines.append("")
                lines.append("Feature Switches: Not available")
            
            return '\n'.join(lines)
        
        output_result(result, json_output, cli_formatter=format_status_result)
            
    except Exception as e:
        error_result = {
            'success': False,
            'error': str(e),
            'message': 'Error checking status'
        }
        output_error(e, json_output, error_type="Status Check Error")
        if json_output:
            click.secho(str(error_result), fg='red', err=True)


@auth.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
def refresh(ctx: click.Context, json_output: bool):
    """
    Refresh authentication tokens.
    
    This command attempts to refresh your authentication tokens using
    the stored refresh token. If refresh fails, you'll need to login again.
    
    Examples:
        vamscli auth refresh
        vamscli auth refresh --json-output
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_config():
        error_msg = "Configuration not found. Please run 'vamscli setup' first."
        output_error(ConfigurationError(error_msg), json_output, error_type="Configuration Error")
        raise click.ClickException(error_msg)
    
    if not profile_manager.has_auth_profile():
        error_msg = "Not authenticated. Run 'vamscli auth login' to authenticate."
        output_error(AuthenticationError(error_msg), json_output, error_type="Authentication Error")
        raise click.ClickException(error_msg)
    
    try:
        config = profile_manager.load_config()
        auth_profile = profile_manager.load_auth_profile()
        
        if not auth_profile or 'refresh_token' not in auth_profile:
            error_msg = "No refresh token found. Please login again."
            output_error(AuthenticationError(error_msg), json_output, error_type="Refresh Token Error")
            raise click.ClickException(error_msg)
        
        # Create authenticator
        authenticator = get_authenticator(config)
        
        output_status("Refreshing tokens...", json_output)
        
        # Refresh tokens
        new_tokens = authenticator.refresh_token(auth_profile['refresh_token'])
        
        # Update auth profile with new tokens, keeping the refresh token
        auth_profile.update(new_tokens)
        if 'refresh_token' not in new_tokens:
            # Keep the original refresh token if not returned
            pass  # refresh_token is already in auth_profile
        
        profile_manager.save_auth_profile(auth_profile)
        
        result = {
            'success': True,
            'message': 'Tokens refreshed successfully',
            'expires_in_seconds': new_tokens.get('expires_in', 'Unknown')
        }
        
        def format_refresh_result(data):
            """Format refresh result for CLI display."""
            lines = []
            if data.get('expires_in_seconds') != 'Unknown':
                lines.append(f"  New token expires in: {data['expires_in_seconds']} seconds")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Tokens refreshed successfully!",
            cli_formatter=format_refresh_result
        )
        
    except AuthenticationError as e:
        output_error(e, json_output, error_type="Token Refresh Error", 
                    helpful_message='Please run "vamscli auth login" to re-authenticate.')
        raise click.ClickException(str(e))
    except Exception as e:
        output_error(e, json_output, error_type="Unexpected Error")
        raise click.ClickException(str(e))


@auth.command('set-override')
@click.option('-u', '--user-id', required=True, help='User ID associated with the override token')
@click.option('--token', required=True, help='Override token to use for authentication')
@click.option('--expires-at', help='Token expiration time (Unix timestamp, ISO 8601, or +seconds)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
def set_override(ctx: click.Context, user_id: str, token: str, expires_at: str, json_output: bool):
    """
    Set an override token for external authentication.
    
    This command allows you to use tokens from external authentication systems
    that are not natively supported by the CLI. The token will be used directly
    in API requests without any refresh capability.
    
    Expiration formats supported:
    - Unix timestamp: 1735689599
    - ISO 8601: 2024-12-31T23:59:59Z
    - Relative: +3600 (3600 seconds from now)
    
    Examples:
        vamscli auth set-override -u john.doe@example.com --token "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
        vamscli auth set-override -u john.doe@example.com --token "token123" --expires-at "2024-12-31T23:59:59Z"
        vamscli auth set-override -u john.doe@example.com --token "token123" --expires-at "+3600" --json-output
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        error_msg = "Configuration not found. Please run 'vamscli setup <api-gateway-url>' first."
        output_error(ConfigurationError(error_msg), json_output, error_type="Configuration Error")
        raise click.ClickException(error_msg)
    
    try:
        # Save the override token with user_id
        profile_manager.save_override_token(token, user_id, expires_at)
        
        # Load configuration for API client
        config = profile_manager.load_config()
        
        # Call login profile API to validate the token and refresh user profile
        validation_successful = False
        try:
            api_client = APIClient(config['api_gateway_url'], profile_manager)
            login_profile_result = api_client.call_login_profile(user_id)
            
            output_status("User profile refreshed successfully.", json_output)
            validation_successful = True
            
            # Fetch feature switches after successful validation
            try:
                secure_config_result = api_client.get_secure_config()
                profile_manager.save_feature_switches(secure_config_result)
                output_status("Feature switches updated successfully.", json_output)
            except Exception as fs_e:
                # Feature switches fetch failure is non-blocking
                output_warning(f"Could not fetch feature switches: {fs_e}", json_output)
            
        except AuthenticationError as e:
            # If login profile fails with 401/403, credentials are already cleared
            output_error(e, json_output, error_type="Authentication Error")
            raise click.ClickException(str(e))
        except Exception as e:
            # If login profile API fails for other reasons, warn but keep the token
            output_warning(f"Could not validate token with user profile: {e}", json_output)
        
        # Prepare result
        result = {
            'success': True,
            'user_id': user_id,
            'validated': validation_successful,
            'message': 'Override token saved successfully'
        }
        
        # Show expiration info if provided
        if expires_at:
            expiration_info = profile_manager.get_token_expiration_info()
            if expiration_info.get('expires_in_human'):
                result['expires_in'] = expiration_info['expires_in_human']
        else:
            result['expiration_note'] = 'No expiration time set - token will be used until it fails'
        
        result['refresh_note'] = 'Override tokens do not support automatic refresh'
        
        def format_set_override_result(data):
            """Format set-override result for CLI display."""
            lines = []
            lines.append(f"  User ID: {data['user_id']}")
            lines.append(f"  Validated: {'Yes' if data['validated'] else 'No'}")
            if 'expires_in' in data:
                lines.append(f"  Token expires in: {data['expires_in']}")
            elif 'expiration_note' in data:
                lines.append(f"  Expiration: {data['expiration_note']}")
            lines.append(f"  Note: {data['refresh_note']}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Override token saved successfully!",
            cli_formatter=format_set_override_result
        )
        
    except Exception as e:
        output_error(e, json_output, error_type="Set Override Token Error")
        raise click.ClickException(str(e))


@auth.command('clear-override')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
def clear_override(ctx: click.Context, json_output: bool):
    """
    Clear the current override token.
    
    This command removes any stored override token and returns to normal
    Cognito authentication mode.
    
    Examples:
        vamscli auth clear-override
        vamscli auth clear-override --json-output
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_auth_profile():
        result = {
            'success': False,
            'message': 'No authentication profile found'
        }
        output_result(result, json_output)
        return
    
    if not profile_manager.is_override_token():
        result = {
            'success': False,
            'message': 'No override token is currently set'
        }
        output_result(result, json_output)
        return
    
    # Delete the override token
    profile_manager.delete_auth_profile()
    
    result = {
        'success': True,
        'message': 'Override token cleared successfully',
        'help': 'You can now use "vamscli auth login" for Cognito authentication'
    }
    
    def format_clear_override_result(data):
        """Format clear-override result for CLI display."""
        return f"  {data['help']}"
    
    output_result(
        result,
        json_output,
        success_message="✓ Override token cleared successfully!",
        cli_formatter=format_clear_override_result
    )
