"""Authentication commands for VamsCLI."""

import click
import datetime

from ..auth.cognito import CognitoAuthenticator
from ..utils.decorators import requires_api_access, requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_warning, output_info
from ..utils.exceptions import AuthenticationError, ConfigurationError, OverrideTokenError, AuthRoutesError


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
@click.option('--new-password', help='New password to set when a forced password change is required (Cognito only)')
@click.option('--save-credentials', is_flag=True, help='Save credentials for automatic re-authentication')
@click.option('--user-id', help='User ID for token override authentication')
@click.option('--token-override', help='Pre-generated token to use directly, mostly for external IDP auth (requires --user-id)')
@click.option('--expires-at', help='Token expiration time (Unix timestamp, ISO 8601, or +seconds)')
@click.option('--skip-version-check', is_flag=True, help='Skip version mismatch confirmation prompts')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def login(ctx: click.Context, username: str, password: str, new_password: str, save_credentials: bool,
          user_id: str, token_override: str, expires_at: str, skip_version_check: bool, json_output: bool):
    """
    Authenticate with VAMS using Cognito or a token override.

    This command authenticates you with the VAMS system using AWS Cognito or a
    token override. It will handle MFA challenges and password reset
    requirements automatically for Cognito authentication.

    Token override is for supplying a pre-generated token directly instead of
    having the CLI sign you in. It is used mostly for external identity provider
    authentication, but any valid pre-generated token works (including an AWS
    Cognito token obtained outside VAMS). To use it, provide --user-id and
    --token-override; the token is saved and validated against the VAMS API.

    If Cognito requires a password change on login (for example, on a new
    account's first sign-in), provide the new password with --new-password.
    In interactive mode you will be prompted for it if omitted; with
    --json-output, --new-password is required when a change is forced.

    Examples:
        # Cognito authentication
        vamscli auth login -u john.doe@example.com
        vamscli auth login -u john.doe@example.com -p mypassword
        vamscli auth login -u john.doe@example.com --save-credentials

        # First login when a password change is forced by Cognito
        vamscli auth login -u john.doe@example.com -p temp-password --new-password new-password

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
            api_client = APIClient(config['api_gateway_url'], profile_manager)
            try:
                login_profile_result = api_client.call_login_profile(user_id)
                output_status("User profile refreshed successfully.", json_output)
            except AuthenticationError as e:
                # If login profile fails with 401/403, credentials are already cleared
                output_error(e, json_output, error_type="Authentication Error")
                raise click.ClickException(str(e))
            except Exception as e:
                # If login profile API fails for other reasons, warn but keep the token
                # and continue with the rest of the auth process below.
                output_warning(f"Could not validate token with user profile: {e}", json_output)

            # Fetch feature switches independently of the login-profile result
            try:
                secure_config_result = api_client.get_secure_config()
                profile_manager.save_feature_switches(secure_config_result)
                output_status("Feature switches updated successfully.", json_output)
            except Exception as fs_e:
                # Feature switches fetch failure is non-blocking
                output_warning(f"Could not fetch feature switches: {fs_e}", json_output)

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

            # Authenticate user. In JSON mode we must not block on interactive
            # prompts, so unmet challenges (e.g. a forced password change with no
            # --new-password) surface as a clean error instead.
            auth_result = authenticator.authenticate(
                username, password, new_password=new_password, interactive=not json_output
            )
            
            # Add user_id to the auth result
            auth_result['user_id'] = username
            
            # Save authentication profile
            profile_manager.save_auth_profile(auth_result)
            
            # Call login profile API to refresh user profile and validate authentication
            api_client = APIClient(config['api_gateway_url'], profile_manager)
            try:
                login_profile_result = api_client.call_login_profile(username)
                output_status("User profile refreshed successfully.", json_output)
            except AuthenticationError as e:
                # If login profile fails with 401/403, credentials are already cleared
                output_error(e, json_output, error_type="Authentication Error")
                raise click.ClickException(str(e))
            except Exception as e:
                # If login profile API fails for other reasons, warn but don't fail
                # authentication or block the rest of the auth process below.
                output_warning(f"Could not refresh user profile: {e}", json_output)

            # Fetch feature switches independently of the login-profile result
            try:
                secure_config_result = api_client.get_secure_config()
                profile_manager.save_feature_switches(secure_config_result)
                output_status("Feature switches updated successfully.", json_output)
            except Exception as fs_e:
                # Feature switches fetch failure is non-blocking
                output_warning(f"Could not fetch feature switches: {fs_e}", json_output)

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


@auth.command('change-password')
@click.option('-u', '--username', required=True, help='Username for Cognito authentication')
@click.option('--old-password', help='Current password (will prompt if not provided)')
@click.option('--new-password', help='New password to set (will prompt if not provided)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def change_password(ctx: click.Context, username: str, old_password: str, new_password: str,
                    json_output: bool):
    """
    Change your Cognito password when you know your current password.

    Use this command when you know your current password and want to set a new
    one. It signs in with the current password and sets the new one. It also
    satisfies a forced password change when Cognito requires one (for example,
    on a new account's first sign-in).

    If you have forgotten your current password, use 'vamscli auth
    forgot-password' instead, which resets it using a code emailed by Cognito.

    This command is only available for deployments that use AWS Cognito
    authentication. In interactive mode you are prompted for any password not
    provided on the command line; with --json-output, both --old-password and
    --new-password are required.

    Examples:
        vamscli auth change-password -u john.doe@example.com
        vamscli auth change-password -u john.doe@example.com --old-password old --new-password new
        vamscli auth change-password -u john.doe@example.com --old-password old --new-password new --json-output
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

    # Verify Cognito is configured for this environment
    amplify_config = config.get('amplify_config', {})
    cognito_user_pool_id = amplify_config.get('cognitoUserPoolId')

    if not cognito_user_pool_id or cognito_user_pool_id in ['undefined', 'null', '']:
        error_msg = (
            "Password changes are only supported for Cognito authentication. "
            "This deployment uses external authentication."
        )
        output_error(ConfigurationError(error_msg), json_output, error_type="Configuration Error")
        raise click.ClickException(error_msg)

    # Collect passwords. In JSON mode we must not block on prompts, so both
    # passwords are required up front.
    if json_output:
        if not old_password or not new_password:
            error_msg = "--old-password and --new-password are required when using --json-output"
            output_error(click.BadParameter(error_msg), json_output, error_type="Invalid Parameters")
            raise click.ClickException(error_msg)
    else:
        if not old_password:
            old_password = click.prompt("Current password", hide_input=True)
        if not new_password:
            new_password = click.prompt("New password", hide_input=True, confirmation_prompt=True)

    try:
        authenticator = get_authenticator(config)

        output_status("Authenticating with Cognito...", json_output)

        # Sign in with the current password. If the account is in a forced
        # password-change state, the new password is applied while answering the
        # challenge, so a separate change call is unnecessary.
        auth_result = authenticator.authenticate(
            username, old_password, new_password=new_password, interactive=False
        )

        if not auth_result.get('password_changed_via_challenge'):
            output_status("Changing password...", json_output)
            authenticator.change_password(
                auth_result['access_token'], old_password, new_password
            )

        result = {
            'success': True,
            'user_id': username,
            'message': 'Password changed successfully'
        }

        def format_change_password_result(data):
            """Format change-password result for CLI display."""
            return f"  User ID: {data['user_id']}"

        output_result(
            result,
            json_output,
            success_message="✓ Password changed successfully!",
            cli_formatter=format_change_password_result
        )

    except AuthenticationError as e:
        output_error(e, json_output, error_type="Password Change Error")
        raise click.ClickException(str(e))
    except Exception as e:
        output_error(e, json_output, error_type="Unexpected Error")
        raise click.ClickException(str(e))


@auth.command('forgot-password')
@click.option('-u', '--username', required=True, help='Username for Cognito authentication')
@click.option('--code', help='Verification code emailed by Cognito (confirm step)')
@click.option('--new-password', help='New password to set (confirm step)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_api_access
def forgot_password(ctx: click.Context, username: str, code: str, new_password: str,
                    json_output: bool):
    """
    Reset a forgotten Cognito password using an emailed verification code.

    Use this command when you do not know your current password. If you do know
    your current password and simply want to change it, use 'vamscli auth
    change-password' instead.

    This is a two-step, self-service flow that does not require knowing the
    current password:

    1. Request a code: run with --username only. Cognito emails a verification
       code to the user's verified email or phone.
    2. Confirm the reset: run again with --code and --new-password to set the
       new password.

    In interactive mode, after the code is requested you are prompted for the
    code and new password to complete both steps in one invocation. With
    --json-output, prompts are not possible: provide --code and --new-password
    together to confirm, or neither to only request a code.

    This command is only available for deployments that use AWS Cognito
    authentication. After a successful reset, sign in with 'vamscli auth login'.

    Examples:
        # Step 1: request a verification code
        vamscli auth forgot-password -u john.doe@example.com

        # Step 2: confirm with the emailed code and a new password
        vamscli auth forgot-password -u john.doe@example.com --code 123456 --new-password new-password

        # JSON output (request only)
        vamscli auth forgot-password -u john.doe@example.com --json-output
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

    # Verify Cognito is configured for this environment
    amplify_config = config.get('amplify_config', {})
    cognito_user_pool_id = amplify_config.get('cognitoUserPoolId')

    if not cognito_user_pool_id or cognito_user_pool_id in ['undefined', 'null', '']:
        error_msg = (
            "Password resets are only supported for Cognito authentication. "
            "This deployment uses external authentication."
        )
        output_error(ConfigurationError(error_msg), json_output, error_type="Configuration Error")
        raise click.ClickException(error_msg)

    try:
        authenticator = get_authenticator(config)

        # Confirm phase: a code and new password complete the reset directly.
        if code or new_password:
            if not code or not new_password:
                error_msg = "--code and --new-password must be provided together to confirm a reset"
                output_error(click.BadParameter(error_msg), json_output, error_type="Invalid Parameters")
                raise click.ClickException(error_msg)
            _confirm_forgot_password(authenticator, username, code, new_password, json_output)
            return

        # Request phase: send a verification code.
        output_status(f"Requesting password reset code for '{username}'...", json_output)
        request_result = authenticator.forgot_password(username)
        destination = request_result.get('code_delivery', {}).get('Destination')

        # In JSON mode we cannot prompt, so stop after requesting the code.
        if json_output:
            result = {
                'code_sent': True,
                'user_id': username,
                'code_delivery': request_result.get('code_delivery', {}),
                'message': 'Verification code sent. Re-run with --code and --new-password to confirm.'
            }
            output_result(result, json_output)
            return

        # Interactive mode: prompt for the code and new password to finish.
        if destination:
            output_status(f"Verification code sent to {destination}.", json_output)
        else:
            output_status("Verification code sent.", json_output)

        code = click.prompt("Enter verification code")
        new_password = click.prompt("New password", hide_input=True, confirmation_prompt=True)
        _confirm_forgot_password(authenticator, username, code, new_password, json_output)

    except AuthenticationError as e:
        output_error(e, json_output, error_type="Password Reset Error")
        raise click.ClickException(str(e))
    except click.ClickException:
        raise
    except Exception as e:
        output_error(e, json_output, error_type="Unexpected Error")
        raise click.ClickException(str(e))


def _confirm_forgot_password(authenticator, username: str, code: str, new_password: str,
                             json_output: bool):
    """Confirm a forgot-password reset and report the result."""
    output_status("Resetting password...", json_output)
    authenticator.confirm_forgot_password(username, code, new_password)

    result = {
        'success': True,
        'user_id': username,
        'message': 'Password reset successfully'
    }

    def format_forgot_password_result(data):
        """Format forgot-password result for CLI display."""
        lines = [f"  User ID: {data['user_id']}"]
        lines.append("  Sign in with 'vamscli auth login' using your new password.")
        return '\n'.join(lines)

    output_result(
        result,
        json_output,
        success_message="✓ Password reset successfully!",
        cli_formatter=format_forgot_password_result
    )


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
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        try:
            login_profile_result = api_client.call_login_profile(user_id)
            output_status("User profile refreshed successfully.", json_output)
            validation_successful = True
        except AuthenticationError as e:
            # If login profile fails with 401/403, credentials are already cleared
            output_error(e, json_output, error_type="Authentication Error")
            raise click.ClickException(str(e))
        except Exception as e:
            # If login profile API fails for other reasons, warn but keep the token
            # and continue with the rest of the auth process below.
            output_warning(f"Could not validate token with user profile: {e}", json_output)

        # Fetch feature switches independently of the login-profile result
        try:
            secure_config_result = api_client.get_secure_config()
            profile_manager.save_feature_switches(secure_config_result)
            output_status("Feature switches updated successfully.", json_output)
        except Exception as fs_e:
            # Feature switches fetch failure is non-blocking
            output_warning(f"Could not fetch feature switches: {fs_e}", json_output)

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


@auth.group()
def routes():
    """API route listing commands."""
    pass


@routes.command(name='list')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_routes(ctx: click.Context, json_output: bool):
    """
    List all available VAMS API routes.

    Returns the full list of API endpoint routes with their HTTP methods and
    categories from the deployment's master route definitions. Useful when
    authoring API authorization constraints (route__path values).

    Examples:
        vamscli auth routes list
        vamscli auth routes list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Retrieving API routes...", json_output)

    try:
        result = api_client.list_api_routes()
        route_list = result.get('routes', [])
        output_result(
            result,
            json_output,
            success_message=f"Found {len(route_list)} API route(s)",
            cli_formatter=format_api_routes_output
        )
    except AuthRoutesError as e:
        output_error(e, json_output, error_type="Auth Routes Error")
        raise click.ClickException(str(e))


@routes.command(name='allowed')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def allowed_routes(ctx: click.Context, json_output: bool):
    """
    List the VAMS API routes the current user is authorized to call.

    Returns the API routes (and the HTTP methods on each) permitted by the
    current user's authorization constraints.

    Examples:
        vamscli auth routes allowed
        vamscli auth routes allowed --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    output_status("Retrieving allowed API routes...", json_output)

    try:
        result = api_client.list_allowed_api_routes()
        route_list = result.get('routes', [])
        output_result(
            result,
            json_output,
            success_message=f"Found {len(route_list)} allowed API route(s)",
            cli_formatter=format_api_routes_output
        )
    except AuthRoutesError as e:
        output_error(e, json_output, error_type="Auth Routes Error")
        raise click.ClickException(str(e))


def format_api_routes_output(result):
    """Format API routes list for CLI output, grouped by category."""
    route_list = result.get('routes', [])
    if not route_list:
        return "No API routes found."

    by_category = {}
    for route in route_list:
        by_category.setdefault(route.get('category', 'other'), []).append(route)

    lines = []
    for category in sorted(by_category):
        lines.append(f"  {category}:")
        for route in sorted(by_category[category], key=lambda r: r.get('path', '')):
            methods = ','.join(route.get('methods', []))
            lines.append(f"    {methods:25s} {route.get('path', '')}")
    return '\n'.join(lines)
