"""Profile management commands for VamsCLI."""

import json
import click

from ..utils.profile import ProfileManager
from ..utils.exceptions import ProfileError, InvalidProfileNameError
from ..utils.json_output import output_status, output_result, output_error, output_info
from ..constants import DEFAULT_PROFILE_NAME


@click.group()
def profile():
    """Profile management commands."""
    pass


@profile.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
def list(json_output: bool):
    """
    List all available profiles.
    
    This command lists all profiles configured in VamsCLI, showing their
    configuration status, authentication status, and which profile is currently active.
    
    Examples:
        vamscli profile list
        vamscli profile list --json-output
    """
    try:
        profiles_info = ProfileManager.get_all_profiles_info()
        
        if not profiles_info:
            result = {"profiles": [], "message": "No profiles found"}
            
            def format_no_profiles(data):
                lines = []
                lines.append("No profiles found.")
                lines.append("Run 'vamscli setup <api-gateway-url>' to create your first profile.")
                return '\n'.join(lines)
            
            output_result(result, json_output, cli_formatter=format_no_profiles)
            return result
        
        # Get active profile
        active_profile = ProfileManager().get_active_profile()
        
        result = {"profiles": profiles_info, "active_profile": active_profile}
        
        def format_profiles_list(data):
            """Format profiles list for CLI display."""
            lines = ["Available profiles:", ""]
            
            for profile_info in data['profiles']:
                profile_name = profile_info['profile_name']
                is_active = profile_info['is_active']
                
                # Profile header
                status_icon = "●" if is_active else "○"
                lines.append(f"{status_icon} {profile_name}" + (" (active)" if is_active else ""))
                
                # Configuration info
                if profile_info['has_config']:
                    lines.append(f"  API Gateway: {profile_info.get('api_gateway_url', 'Unknown')}")
                    lines.append(f"  CLI Version: {profile_info.get('cli_version', 'Unknown')}")
                    
                    # Show authType (backward compatible)
                    auth_type = profile_info.get('auth_type')
                    if auth_type:
                        lines.append(f"  Auth Type: {auth_type}")
                else:
                    lines.append("  Status: Not configured")
                
                # Authentication info
                if profile_info['has_auth']:
                    user_id = profile_info.get('user_id', 'Unknown')
                    token_type = profile_info.get('token_type', 'cognito')
                    lines.append(f"  User: {user_id}")
                    lines.append(f"  Token Type: {'Override Token' if token_type == 'override' else 'Cognito'}")
                    
                    if profile_info.get('token_expired'):
                        lines.append("  Status: ✗ Token Expired")
                    else:
                        lines.append("  Status: ✓ Authenticated")
                    
                    # Show webDeployedUrl (backward compatible - only if present)
                    web_url = profile_info.get('web_deployed_url')
                    if web_url:
                        lines.append(f"  Web URL: {web_url}")
                    
                    # Show locationServiceApiUrl (backward compatible - only if present)
                    location_url = profile_info.get('location_service_api_url')
                    if location_url:
                        lines.append(f"  Location Service URL: {location_url}")
                else:
                    lines.append("  Status: Not authenticated")
                
                if profile_info['has_credentials']:
                    lines.append("  Saved Credentials: Yes")
                
                lines.append("")
            
            return '\n'.join(lines)
        
        output_result(result, json_output, cli_formatter=format_profiles_list)
        return result
            
    except Exception as e:
        result = {"profiles": [], "error": str(e)}
        output_error(e, json_output, error_type="Profile List Error")
        return result


@profile.command()
@click.argument('profile_name')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
def switch(profile_name: str, json_output: bool):
    """
    Switch to a different profile.
    
    This command switches the active profile to the specified profile name.
    The profile must already exist and be configured.
    
    Examples:
        vamscli profile switch production
        vamscli profile switch staging --json-output
    """
    try:
        # Validate profile name
        from ..constants import validate_profile_name
        if not validate_profile_name(profile_name):
            raise InvalidProfileNameError(
                f"Invalid profile name '{profile_name}'. Profile names must be 3-50 characters, "
                "alphanumeric with hyphens and underscores only."
            )
        
        # Check if profile exists
        profile_manager = ProfileManager(profile_name)
        if not profile_manager.has_config():
            raise ProfileError(
                f"Profile '{profile_name}' does not exist or is not configured. "
                f"Run 'vamscli setup <api-gateway-url> --profile {profile_name}' to create it."
            )
        
        # Switch to the profile
        profile_manager.set_active_profile(profile_name)
        
        # Get profile info
        profile_info = profile_manager.get_profile_info()
        
        result = {
            "success": True,
            "profile_name": profile_name,
            "message": f"Switched to profile '{profile_name}'",
            "profile_info": profile_info
        }
        
        def format_switch_result(data):
            """Format switch result for CLI display."""
            lines = []
            lines.append(f"API Gateway: {data['profile_info'].get('api_gateway_url', 'Unknown')}")
            
            if data['profile_info']['has_auth']:
                user_id = data['profile_info'].get('user_id', 'Unknown')
                lines.append(f"Authenticated as: {user_id}")
            else:
                lines.append("Status: Not authenticated")
                lines.append(f"Run 'vamscli auth login -u <username>' to authenticate")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message=f"✓ Switched to profile '{profile_name}'",
            cli_formatter=format_switch_result
        )
        
        return result
        
    except (ProfileError, InvalidProfileNameError) as e:
        output_error(e, json_output, error_type="Profile Switch Error")
        raise click.ClickException(str(e))
    except Exception as e:
        output_error(e, json_output, error_type="Profile Switch Error")
        raise click.ClickException(str(e))


@profile.command()
@click.argument('profile_name')
@click.option('--force', '-f', is_flag=True, help='Force deletion without confirmation')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
def delete(profile_name: str, force: bool, json_output: bool):
    """
    Delete a profile and all its configuration.
    
    This command deletes a profile and all its associated configuration files,
    including authentication credentials. The default profile cannot be deleted.
    
    Examples:
        vamscli profile delete test-profile
        vamscli profile delete test-profile --force
        vamscli profile delete test-profile --force --json-output
    """
    try:
        # Validate profile name
        from ..constants import validate_profile_name
        if not validate_profile_name(profile_name):
            raise InvalidProfileNameError(
                f"Invalid profile name '{profile_name}'. Profile names must be 3-50 characters, "
                "alphanumeric with hyphens and underscores only."
            )
        
        # Cannot delete default profile
        if profile_name == DEFAULT_PROFILE_NAME:
            raise ProfileError("Cannot delete the default profile")
        
        # Check if profile exists
        profile_manager = ProfileManager(profile_name)
        if not profile_manager.profile_exists():
            result = {
                "success": False,
                "profile_name": profile_name,
                "message": f"Profile '{profile_name}' does not exist"
            }
            
            output_info(f"Profile '{profile_name}' does not exist.", json_output)
            
            if json_output:
                click.echo(json.dumps(result, indent=2))
            
            return result
        
        # Confirm deletion unless force is used
        if not force and not json_output:
            profile_info = profile_manager.get_profile_info()
            
            # Show profile info (only in CLI mode)
            click.echo(f"Profile '{profile_name}' information:")
            if profile_info['has_config']:
                click.echo(f"  API Gateway: {profile_info.get('api_gateway_url', 'Unknown')}")
            if profile_info['has_auth']:
                click.echo(f"  User: {profile_info.get('user_id', 'Unknown')}")
            
            if not click.confirm(f"Are you sure you want to delete profile '{profile_name}'?"):
                result = {
                    "success": False,
                    "profile_name": profile_name,
                    "message": "Deletion cancelled by user"
                }
                
                output_info("Deletion cancelled.", False)
                
                return result
        
        # Delete the profile
        profile_manager.delete_profile(profile_name)
        
        # Get active profile
        active_profile = ProfileManager().get_active_profile()
        
        result = {
            "success": True,
            "profile_name": profile_name,
            "message": f"Profile '{profile_name}' deleted successfully",
            "active_profile": active_profile
        }
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            return f"Active profile is now: {data['active_profile']}"
        
        output_result(
            result,
            json_output,
            success_message=f"✓ Profile '{profile_name}' deleted successfully",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except (ProfileError, InvalidProfileNameError) as e:
        output_error(e, json_output, error_type="Profile Delete Error")
        raise click.ClickException(str(e))
    except Exception as e:
        output_error(e, json_output, error_type="Profile Delete Error")
        raise click.ClickException(str(e))


@profile.command()
@click.argument('profile_name')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
def info(profile_name: str, json_output: bool):
    """
    Show detailed information about a profile.
    
    This command displays comprehensive information about a profile, including
    configuration details, authentication status, and file locations.
    
    Examples:
        vamscli profile info production
        vamscli profile info staging --json-output
    """
    try:
        # Validate profile name
        from ..constants import validate_profile_name
        if not validate_profile_name(profile_name):
            raise InvalidProfileNameError(
                f"Invalid profile name '{profile_name}'. Profile names must be 3-50 characters, "
                "alphanumeric with hyphens and underscores only."
            )
        
        profile_manager = ProfileManager(profile_name)
        
        if not profile_manager.profile_exists():
            result = {
                "profile_name": profile_name,
                "exists": False,
                "message": f"Profile '{profile_name}' does not exist"
            }
            
            output_info(f"Profile '{profile_name}' does not exist.", json_output)
            
            if json_output:
                click.echo(json.dumps(result, indent=2))
            
            return result
        
        profile_info = profile_manager.get_profile_info()
        
        result = {
            "profile_name": profile_name,
            "exists": True,
            "profile_info": profile_info
        }
        
        def format_profile_info(data):
            """Format profile info for CLI display."""
            lines = []
            info = data['profile_info']
            
            lines.append(f"Profile: {data['profile_name']}")
            lines.append(f"Active: {'Yes' if info['is_active'] else 'No'}")
            lines.append(f"Directory: {info['profile_dir']}")
            lines.append("")
            
            # Configuration
            lines.append("Configuration:")
            if info['has_config']:
                lines.append(f"  API Gateway: {info.get('api_gateway_url', 'Unknown')}")
                lines.append(f"  CLI Version: {info.get('cli_version', 'Unknown')}")
                
                # Amplify Configuration (backward compatible) - Show ALL fields
                amplify_config = info.get('amplify_config', {})
                if amplify_config:
                    lines.append("")
                    lines.append("  Amplify Configuration:")
                    lines.append(f"    Region: {amplify_config.get('region', 'Unknown')}")
                    lines.append(f"    API: {amplify_config.get('api', 'Unknown')}")
                    lines.append(f"    Cognito User Pool ID: {amplify_config.get('cognitoUserPoolId', 'Not configured')}")
                    lines.append(f"    Cognito App Client ID: {amplify_config.get('cognitoAppClientId', 'Not configured')}")
                    lines.append(f"    Cognito Identity Pool ID: {amplify_config.get('cognitoIdentityPoolId', 'Not configured')}")
                    lines.append(f"    External OAuth IDP URL: {amplify_config.get('externalOAuthIdpURL', 'Not configured')}")
                    lines.append(f"    External OAuth IDP Client ID: {amplify_config.get('externalOAuthIdpClientId', 'Not configured')}")
                    lines.append(f"    External OAuth IDP Scope: {amplify_config.get('externalOAuthIdpScope', 'Not configured')}")
                    lines.append(f"    External OAuth IDP Scope MFA: {amplify_config.get('externalOAuthIdpScopeMfa', 'Not configured')}")
                    lines.append(f"    External OAuth IDP Token Endpoint: {amplify_config.get('externalOAuthIdpTokenEndpoint', 'Not configured')}")
                    lines.append(f"    External OAuth IDP Authorization Endpoint: {amplify_config.get('externalOAuthIdpAuthorizationEndpoint', 'Not configured')}")
                    lines.append(f"    External OAuth IDP Discovery Endpoint: {amplify_config.get('externalOAuthIdpDiscoveryEndpoint', 'Not configured')}")
                    lines.append(f"    Stack Name: {amplify_config.get('stackName', 'Unknown')}")
                    lines.append(f"    Content Security Policy: {amplify_config.get('contentSecurityPolicy', 'Not set')}")
                    lines.append(f"    Banner HTML Message: {amplify_config.get('bannerHtmlMessage', 'Not set')}")
                
                # Auth Type (backward compatible)
                auth_type = info.get('auth_type')
                if auth_type:
                    lines.append(f"  Auth Type: {auth_type}")
            else:
                lines.append("  Status: Not configured")
            lines.append("")
            
            # Authentication
            lines.append("Authentication:")
            if info['has_auth']:
                user_id = info.get('user_id', 'Unknown')
                token_type = info.get('token_type', 'cognito')
                lines.append(f"  User ID: {user_id}")
                lines.append(f"  Type: {'Override Token' if token_type == 'override' else 'Cognito'}")
                
                if info.get('token_expired'):
                    lines.append("  Status: ✗ Token Expired")
                else:
                    lines.append("  Status: ✓ Authenticated")
                    
                if info.get('token_expires_at'):
                    import datetime
                    expires_at = datetime.datetime.fromtimestamp(info['token_expires_at'])
                    lines.append(f"  Expires: {expires_at} UTC")
                
                # Show webDeployedUrl (backward compatible - only if present)
                web_url = info.get('web_deployed_url')
                if web_url:
                    lines.append(f"  Web Deployed URL: {web_url}")
                
                # Show locationServiceApiUrl (backward compatible - only if present)
                location_url = info.get('location_service_api_url')
                if location_url:
                    lines.append(f"  Location Service URL: {location_url}")
            else:
                lines.append("  Status: Not authenticated")
            
            if info['has_credentials']:
                lines.append("  Saved Credentials: Yes")
            
            return '\n'.join(lines)
        
        output_result(result, json_output, cli_formatter=format_profile_info)
        
        return result
        
    except (ProfileError, InvalidProfileNameError) as e:
        output_error(e, json_output, error_type="Profile Info Error")
        raise click.ClickException(str(e))
    except Exception as e:
        output_error(e, json_output, error_type="Profile Info Error")
        raise click.ClickException(str(e))


@profile.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
def current(json_output: bool):
    """
    Show the currently active profile.
    
    This command displays information about the currently active profile,
    including its configuration and authentication status.
    
    Examples:
        vamscli profile current
        vamscli profile current --json-output
    """
    try:
        active_profile = ProfileManager().get_active_profile()
        profile_manager = ProfileManager(active_profile)
        
        result = {
            "active_profile": active_profile,
            "has_config": False,
            "has_auth": False,
            "config": {},
            "auth_profile": {}
        }
        
        if profile_manager.has_config():
            config = profile_manager.load_config()
            result["has_config"] = True
            result["config"] = config
        
        if profile_manager.has_auth_profile():
            auth_profile = profile_manager.load_auth_profile()
            if auth_profile:
                result["has_auth"] = True
                result["auth_profile"] = auth_profile
        
        def format_current_profile(data):
            """Format current profile for CLI display."""
            lines = []
            lines.append(f"Current active profile: {data['active_profile']}")
            
            if data['has_config']:
                lines.append(f"API Gateway: {data['config'].get('api_gateway_url', 'Unknown')}")
                
                # Show authType (backward compatible)
                amplify_config = data['config'].get('amplify_config', {})
                cognito_user_pool_id = amplify_config.get('cognitoUserPoolId')
                auth_type = 'Cognito' if (cognito_user_pool_id and cognito_user_pool_id not in ['undefined', 'null', '']) else 'External'
                lines.append(f"Auth Type: {auth_type}")
            
            if data['has_auth']:
                user_id = data['auth_profile'].get('user_id', 'Unknown')
                token_type = data['auth_profile'].get('token_type', 'cognito')
                lines.append(f"Authenticated as: {user_id} ({'Override' if token_type == 'override' else 'Cognito'})")
                
                # Show webDeployedUrl (backward compatible - only if present)
                web_url = data['auth_profile'].get('web_deployed_url')
                if web_url:
                    lines.append(f"Web Deployed URL: {web_url}")
                
                # Show locationServiceApiUrl (backward compatible - only if present)
                location_url = data['auth_profile'].get('location_service_api_url')
                if location_url:
                    lines.append(f"Location Service URL: {location_url}")
            else:
                lines.append("Status: Not authenticated")
            
            return '\n'.join(lines)
        
        output_result(result, json_output, cli_formatter=format_current_profile)
        
        return result
        
    except Exception as e:
        result = {
            "active_profile": None,
            "error": str(e),
            "has_config": False,
            "has_auth": False,
            "config": {},
            "auth_profile": {}
        }
        output_error(e, json_output, error_type="Profile Current Error")
        return result
