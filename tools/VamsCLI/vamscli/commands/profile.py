"""Profile management commands for VamsCLI."""

import click

from ..utils.profile import ProfileManager
from ..utils.exceptions import ProfileError, InvalidProfileNameError
from ..constants import DEFAULT_PROFILE_NAME


@click.group()
def profile():
    """Profile management commands."""
    pass


@profile.command()
def list():
    """List all available profiles."""
    try:
        profiles_info = ProfileManager.get_all_profiles_info()
        
        if not profiles_info:
            click.echo("No profiles found.")
            click.echo("Run 'vamscli setup <api-gateway-url>' to create your first profile.")
            return
        
        # Get active profile
        active_profile = ProfileManager().get_active_profile()
        
        click.echo("Available profiles:")
        click.echo()
        
        for profile_info in profiles_info:
            profile_name = profile_info['profile_name']
            is_active = profile_info['is_active']
            
            # Profile header
            status_icon = "●" if is_active else "○"
            click.echo(f"{status_icon} {profile_name}" + (" (active)" if is_active else ""))
            
            # Configuration info
            if profile_info['has_config']:
                click.echo(f"  API Gateway: {profile_info.get('api_gateway_url', 'Unknown')}")
                click.echo(f"  CLI Version: {profile_info.get('cli_version', 'Unknown')}")
            else:
                click.echo("  Status: Not configured")
            
            # Authentication info
            if profile_info['has_auth']:
                user_id = profile_info.get('user_id', 'Unknown')
                token_type = profile_info.get('token_type', 'cognito')
                click.echo(f"  User: {user_id}")
                click.echo(f"  Auth Type: {'Override Token' if token_type == 'override' else 'Cognito'}")
                
                if profile_info.get('token_expired'):
                    click.echo("  Status: ✗ Token Expired")
                else:
                    click.echo("  Status: ✓ Authenticated")
            else:
                click.echo("  Status: Not authenticated")
            
            if profile_info['has_credentials']:
                click.echo("  Saved Credentials: Yes")
            
            click.echo()
            
    except Exception as e:
        click.echo(f"Error listing profiles: {e}", err=True)


@profile.command()
@click.argument('profile_name')
def switch(profile_name: str):
    """Switch to a different profile."""
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
        
        click.echo(
            click.style(f"✓ Switched to profile '{profile_name}'", fg='green', bold=True)
        )
        
        # Show profile info
        profile_info = profile_manager.get_profile_info()
        click.echo(f"API Gateway: {profile_info.get('api_gateway_url', 'Unknown')}")
        
        if profile_info['has_auth']:
            user_id = profile_info.get('user_id', 'Unknown')
            click.echo(f"Authenticated as: {user_id}")
        else:
            click.echo("Status: Not authenticated")
            click.echo(f"Run 'vamscli auth login -u <username>' to authenticate")
        
    except (ProfileError, InvalidProfileNameError) as e:
        click.echo(f"✗ {e}", err=True)
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(f"✗ Failed to switch profile: {e}", err=True)
        raise click.ClickException(str(e))


@profile.command()
@click.argument('profile_name')
@click.option('--force', '-f', is_flag=True, help='Force deletion without confirmation')
def delete(profile_name: str, force: bool):
    """Delete a profile and all its configuration."""
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
            click.echo(f"Profile '{profile_name}' does not exist.")
            return
        
        # Confirm deletion unless force is used
        if not force:
            profile_info = profile_manager.get_profile_info()
            click.echo(f"Profile '{profile_name}' information:")
            if profile_info['has_config']:
                click.echo(f"  API Gateway: {profile_info.get('api_gateway_url', 'Unknown')}")
            if profile_info['has_auth']:
                click.echo(f"  User: {profile_info.get('user_id', 'Unknown')}")
            
            if not click.confirm(f"Are you sure you want to delete profile '{profile_name}'?"):
                click.echo("Deletion cancelled.")
                return
        
        # Delete the profile
        profile_manager.delete_profile(profile_name)
        
        click.echo(
            click.style(f"✓ Profile '{profile_name}' deleted successfully", fg='green', bold=True)
        )
        
        # Show active profile
        active_profile = ProfileManager().get_active_profile()
        click.echo(f"Active profile is now: {active_profile}")
        
    except (ProfileError, InvalidProfileNameError) as e:
        click.echo(f"✗ {e}", err=True)
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(f"✗ Failed to delete profile: {e}", err=True)
        raise click.ClickException(str(e))


@profile.command()
@click.argument('profile_name')
def info(profile_name: str):
    """Show detailed information about a profile."""
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
            click.echo(f"Profile '{profile_name}' does not exist.")
            return
        
        profile_info = profile_manager.get_profile_info()
        
        click.echo(f"Profile: {profile_name}")
        click.echo(f"Active: {'Yes' if profile_info['is_active'] else 'No'}")
        click.echo(f"Directory: {profile_info['profile_dir']}")
        click.echo()
        
        # Configuration
        click.echo("Configuration:")
        if profile_info['has_config']:
            click.echo(f"  API Gateway: {profile_info.get('api_gateway_url', 'Unknown')}")
            click.echo(f"  CLI Version: {profile_info.get('cli_version', 'Unknown')}")
        else:
            click.echo("  Status: Not configured")
        click.echo()
        
        # Authentication
        click.echo("Authentication:")
        if profile_info['has_auth']:
            user_id = profile_info.get('user_id', 'Unknown')
            token_type = profile_info.get('token_type', 'cognito')
            click.echo(f"  User ID: {user_id}")
            click.echo(f"  Type: {'Override Token' if token_type == 'override' else 'Cognito'}")
            
            if profile_info.get('token_expired'):
                click.echo("  Status: ✗ Token Expired")
            else:
                click.echo("  Status: ✓ Authenticated")
                
            if profile_info.get('token_expires_at'):
                import datetime
                expires_at = datetime.datetime.fromtimestamp(profile_info['token_expires_at'])
                click.echo(f"  Expires: {expires_at} UTC")
        else:
            click.echo("  Status: Not authenticated")
        
        if profile_info['has_credentials']:
            click.echo("  Saved Credentials: Yes")
        
    except (ProfileError, InvalidProfileNameError) as e:
        click.echo(f"✗ {e}", err=True)
        raise click.ClickException(str(e))
    except Exception as e:
        click.echo(f"✗ Failed to get profile info: {e}", err=True)
        raise click.ClickException(str(e))


@profile.command()
def current():
    """Show the currently active profile."""
    try:
        active_profile = ProfileManager().get_active_profile()
        profile_manager = ProfileManager(active_profile)
        
        click.echo(f"Current active profile: {active_profile}")
        
        if profile_manager.has_config():
            config = profile_manager.load_config()
            click.echo(f"API Gateway: {config.get('api_gateway_url', 'Unknown')}")
        
        if profile_manager.has_auth_profile():
            auth_profile = profile_manager.load_auth_profile()
            if auth_profile:
                user_id = auth_profile.get('user_id', 'Unknown')
                token_type = auth_profile.get('token_type', 'cognito')
                click.echo(f"Authenticated as: {user_id} ({'Override' if token_type == 'override' else 'Cognito'})")
        else:
            click.echo("Status: Not authenticated")
        
    except Exception as e:
        click.echo(f"Error getting current profile: {e}", err=True)
