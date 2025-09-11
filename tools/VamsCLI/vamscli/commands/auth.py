"""Authentication commands for VamsCLI."""

import click

from ..auth.cognito import CognitoAuthenticator
from ..utils.decorators import requires_api_access, get_profile_manager_from_context
from ..utils.api_client import APIClient
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
@click.pass_context
@requires_api_access
def login(ctx: click.Context, username: str, password: str, save_credentials: bool, user_id: str, token_override: str, expires_at: str, skip_version_check: bool):
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
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        profile_name = profile_manager.profile_name
        raise click.ClickException(
            f"Configuration not found for profile '{profile_name}'. "
            f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
        )
    
    # Load configuration
    try:
        config = profile_manager.load_config()
    except Exception as e:
        raise click.ClickException(f"Failed to load configuration: {e}")
    
    # Check API version compatibility
    try:
        api_client = APIClient(config['api_gateway_url'], profile_manager)
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
            
            if not skip_version_check and not click.confirm("Continue with authentication?"):
                click.echo("Authentication cancelled.")
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
    except Exception as e:
        # Version check failure shouldn't block authentication
        click.echo(
            click.style(f"Warning: Could not check API version: {e}", fg='yellow')
        )
    
    # Validate input combinations
    if token_override and not user_id:
        raise click.ClickException("--user-id is required when using --token-override")
    
    if token_override and save_credentials:
        raise click.ClickException("--save-credentials cannot be used with --token-override (override tokens don't use traditional credentials)")
    
    if token_override:
        # Token override authentication path
        try:
            click.echo("Using token override authentication...")
            
            # Save the override token
            profile_manager.save_override_token(token_override, user_id, expires_at)
            
            # Call login profile API to validate the token and refresh user profile
            try:
                api_client = APIClient(config['api_gateway_url'], profile_manager)
                login_profile_result = api_client.call_login_profile(user_id)
                
                click.echo("User profile refreshed successfully.")
                
                # Fetch feature switches after successful validation
                try:
                    feature_switches_result = api_client.get_feature_switches()
                    profile_manager.save_feature_switches(feature_switches_result)
                    click.echo("Feature switches updated successfully.")
                except Exception as fs_e:
                    # Feature switches fetch failure is non-blocking
                    click.echo(
                        click.style(f"Warning: Could not fetch feature switches: {fs_e}", fg='yellow')
                    )
                
            except AuthenticationError as e:
                # If login profile fails with 401/403, credentials are already cleared
                raise click.ClickException(str(e))
            except Exception as e:
                # If login profile API fails for other reasons, warn but keep the token
                click.echo(
                    click.style(f"Warning: Could not validate token with user profile: {e}", fg='yellow')
                )
            
            click.echo(
                click.style("✓ Token override authentication successful!", fg='green', bold=True)
            )
            
            # Show expiration info if provided
            if expires_at:
                expiration_info = profile_manager.get_token_expiration_info()
                if expiration_info.get('expires_in_human'):
                    click.echo(f"Token expires in {expiration_info['expires_in_human']}")
            else:
                click.echo("No expiration time set - token will be used until it fails")
                
            click.echo("\nNote: Override tokens do not support automatic refresh.")
            click.echo("You will need to provide a new token when this one expires.")
            
        except Exception as e:
            click.echo(
                click.style(f"✗ Token override authentication failed: {e}", fg='red', bold=True),
                err=True
            )
            raise click.ClickException(str(e))
    
    else:
        # Cognito authentication path
        if not username:
            raise click.ClickException("--username is required for Cognito authentication")
        
        # Prompt for password if not provided
        if not password:
            password = click.prompt("Password", hide_input=True)
        
        try:
            # Create authenticator
            authenticator = get_authenticator(config)
            
            click.echo("Authenticating with Cognito...")
            
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
                click.echo("User profile refreshed successfully.")
                
                # Fetch feature switches after successful authentication
                try:
                    feature_switches_result = api_client.get_feature_switches()
                    profile_manager.save_feature_switches(feature_switches_result)
                    click.echo("Feature switches updated successfully.")
                except Exception as fs_e:
                    # Feature switches fetch failure is non-blocking
                    click.echo(
                        click.style(f"Warning: Could not fetch feature switches: {fs_e}", fg='yellow')
                    )
                    
            except AuthenticationError as e:
                # If login profile fails with 401/403, credentials are already cleared
                raise click.ClickException(str(e))
            except Exception as e:
                # If login profile API fails for other reasons, warn but don't fail authentication
                click.echo(
                    click.style(f"Warning: Could not refresh user profile: {e}", fg='yellow')
                )
            
            # Save credentials if requested
            if save_credentials:
                profile_manager.save_credentials({
                    'username': username,
                    'password': password
                })
                click.echo("Credentials saved for automatic re-authentication.")
            
            click.echo(
                click.style("✓ Cognito authentication successful!", fg='green', bold=True)
            )
            click.echo(f"Access token expires in {auth_result['expires_in']} seconds")
            
        except AuthenticationError as e:
            click.echo(
                click.style(f"✗ Cognito authentication failed: {e}", fg='red', bold=True),
                err=True
            )
            raise click.ClickException(str(e))
        except Exception as e:
            click.echo(
                click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
                err=True
            )
            raise click.ClickException(str(e))


@auth.command()
@click.pass_context
def logout(ctx: click.Context):
    """
    Remove authentication profile and saved credentials.
    
    This command will log you out by removing your stored authentication
    tokens and any saved credentials.
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_auth_profile() and not profile_manager.has_credentials():
        click.echo("No authentication profile found.")
        return
    
    # Delete authentication profile and credentials
    profile_manager.delete_auth_profile()
    
    click.echo(
        click.style("✓ Logged out successfully!", fg='green', bold=True)
    )
    click.echo("Authentication profile and saved credentials removed.")


@auth.command()
@click.pass_context
def status(ctx: click.Context):
    """
    Show authentication status.
    
    This command displays information about your current authentication
    status, including token expiration times.
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_config():
        click.echo("Configuration not found. Please run 'vamscli setup' first.")
        return
    
    if not profile_manager.has_auth_profile():
        click.echo("Not authenticated. Run 'vamscli auth login' to authenticate.")
        return
    
    try:
        expiration_info = profile_manager.get_token_expiration_info()
        
        if not expiration_info['has_token']:
            click.echo("No authentication profile found.")
            return
        
        token_type = expiration_info['token_type']
        is_override = token_type == 'override'
        
        # Get user ID from auth profile
        auth_profile = profile_manager.load_auth_profile()
        user_id = auth_profile.get('user_id', 'Unknown') if auth_profile else 'Unknown'
        
        click.echo("Authentication Status:")
        click.echo(f"  Type: {'Override Token' if is_override else 'Cognito Token'}")
        click.echo(f"  User ID: {user_id}")
        
        if expiration_info['has_expiration']:
            if expiration_info['is_expired']:
                click.echo("  Status: ✗ Expired")
            else:
                click.echo("  Status: ✓ Valid")
                if expiration_info['expires_in_human']:
                    click.echo(f"  Expires in: {expiration_info['expires_in_human']}")
            
            # Show absolute expiration time
            import datetime
            expires_at = datetime.datetime.fromtimestamp(expiration_info['expires_at'])
            click.echo(f"  Expires at: {expires_at} UTC")
        else:
            if is_override:
                click.echo("  Status: ✓ Valid (no expiration set)")
            else:
                # For Cognito tokens, check validity using authenticator
                try:
                    config = profile_manager.load_config()
                    auth_profile = profile_manager.load_auth_profile()
                    authenticator = get_authenticator(config)
                    is_valid = authenticator.is_token_valid(auth_profile)
                    click.echo(f"  Status: {'✓ Valid' if is_valid else '✗ Expired'}")
                except Exception:
                    click.echo("  Status: Unknown")
        
        if is_override:
            click.echo("  Source: External")
            click.echo("  Refresh: Not supported")
        else:
            if profile_manager.has_credentials():
                click.echo("  Saved credentials: Yes")
            else:
                click.echo("  Saved credentials: No")
            click.echo("  Refresh: Supported")
        
        # Show feature switches information
        feature_switches_info = profile_manager.get_feature_switches_info()
        if feature_switches_info['has_feature_switches']:
            click.echo("\nFeature Switches:")
            click.echo(f"  Count: {feature_switches_info['count']}")
            if feature_switches_info['enabled']:
                click.echo("  Enabled features:")
                for feature in sorted(feature_switches_info['enabled']):
                    click.echo(f"    - {feature}")
            else:
                click.echo("  No features enabled")
            
            if feature_switches_info['fetched_at']:
                click.echo(f"  Last updated: {feature_switches_info['fetched_at']}")
        else:
            click.echo("\nFeature Switches: Not available")
            
    except Exception as e:
        click.echo(f"Error checking status: {e}")


@auth.command()
@click.pass_context
def refresh(ctx: click.Context):
    """
    Refresh authentication tokens.
    
    This command attempts to refresh your authentication tokens using
    the stored refresh token. If refresh fails, you'll need to login again.
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_config():
        raise click.ClickException(
            "Configuration not found. Please run 'vamscli setup' first."
        )
    
    if not profile_manager.has_auth_profile():
        raise click.ClickException(
            "Not authenticated. Run 'vamscli auth login' to authenticate."
        )
    
    try:
        config = profile_manager.load_config()
        auth_profile = profile_manager.load_auth_profile()
        
        if not auth_profile or 'refresh_token' not in auth_profile:
            raise click.ClickException(
                "No refresh token found. Please login again."
            )
        
        # Create authenticator
        authenticator = get_authenticator(config)
        
        click.echo("Refreshing tokens...")
        
        # Refresh tokens
        new_tokens = authenticator.refresh_token(auth_profile['refresh_token'])
        
        # Update auth profile with new tokens, keeping the refresh token
        auth_profile.update(new_tokens)
        if 'refresh_token' not in new_tokens:
            # Keep the original refresh token if not returned
            pass  # refresh_token is already in auth_profile
        
        profile_manager.save_auth_profile(auth_profile)
        
        click.echo(
            click.style("✓ Tokens refreshed successfully!", fg='green', bold=True)
        )
        
    except AuthenticationError as e:
        click.echo(
            click.style(f"✗ Token refresh failed: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Please run 'vamscli auth login' to re-authenticate.")
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(
            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@auth.command('set-override')
@click.option('-u', '--user-id', required=True, help='User ID associated with the override token')
@click.option('--token', required=True, help='Override token to use for authentication')
@click.option('--expires-at', help='Token expiration time (Unix timestamp, ISO 8601, or +seconds)')
@click.pass_context
def set_override(ctx: click.Context, user_id: str, token: str, expires_at: str):
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
        vamscli auth set-override -u john.doe@example.com --token "token123" --expires-at "+3600"
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Check if setup has been completed
    if not profile_manager.has_config():
        raise click.ClickException(
            "Configuration not found. Please run 'vamscli setup <api-gateway-url>' first."
        )
    
    try:
        # Save the override token with user_id
        profile_manager.save_override_token(token, user_id, expires_at)
        
        # Load configuration for API client
        config = profile_manager.load_config()
        
        # Call login profile API to validate the token and refresh user profile
        try:
            api_client = APIClient(config['api_gateway_url'], profile_manager)
            login_profile_result = api_client.call_login_profile(user_id)
            
            click.echo(
                click.style("✓ Override token saved and validated successfully!", fg='green', bold=True)
            )
            click.echo("User profile refreshed successfully.")
            
            # Fetch feature switches after successful validation
            try:
                feature_switches_result = api_client.get_feature_switches()
                profile_manager.save_feature_switches(feature_switches_result)
                click.echo("Feature switches updated successfully.")
            except Exception as fs_e:
                # Feature switches fetch failure is non-blocking
                click.echo(
                    click.style(f"Warning: Could not fetch feature switches: {fs_e}", fg='yellow')
                )
            
        except AuthenticationError as e:
            # If login profile fails with 401/403, credentials are already cleared
            raise click.ClickException(str(e))
        except Exception as e:
            # If login profile API fails for other reasons, warn but keep the token
            click.echo(
                click.style("✓ Override token saved successfully!", fg='green', bold=True)
            )
            click.echo(
                click.style(f"Warning: Could not validate token with user profile: {e}", fg='yellow')
            )
        
        # Show expiration info if provided
        if expires_at:
            expiration_info = profile_manager.get_token_expiration_info()
            if expiration_info.get('expires_in_human'):
                click.echo(f"Token expires in {expiration_info['expires_in_human']}")
        else:
            click.echo("No expiration time set - token will be used until it fails")
            
        click.echo("\nNote: Override tokens do not support automatic refresh.")
        click.echo("You will need to provide a new token when this one expires.")
        
    except Exception as e:
        click.echo(
            click.style(f"✗ Failed to set override token: {e}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(str(e))


@auth.command('clear-override')
@click.pass_context
def clear_override(ctx: click.Context):
    """
    Clear the current override token.
    
    This command removes any stored override token and returns to normal
    Cognito authentication mode.
    """
    # Get profile manager from context
    profile_manager = get_profile_manager_from_context(ctx)
    
    if not profile_manager.has_auth_profile():
        click.echo("No authentication profile found.")
        return
    
    if not profile_manager.is_override_token():
        click.echo("No override token is currently set.")
        return
    
    # Delete the override token
    profile_manager.delete_auth_profile()
    
    click.echo(
        click.style("✓ Override token cleared successfully!", fg='green', bold=True)
    )
    click.echo("You can now use 'vamscli auth login' for Cognito authentication.")
