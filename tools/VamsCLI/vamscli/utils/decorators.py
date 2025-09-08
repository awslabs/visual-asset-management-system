"""Decorators for VamsCLI commands."""

import functools
import click
from typing import Optional

from .profile import ProfileManager
from .api_client import APIClient
from .exceptions import (
    APIUnavailableError, SetupRequiredError, AuthenticationError,
    GlobalInfrastructureError
)
from ..constants import DEFAULT_PROFILE_NAME


def get_profile_manager_from_context(ctx: Optional[click.Context] = None) -> ProfileManager:
    """Get ProfileManager instance from Click context or use default."""
    if ctx and ctx.obj and 'profile_name' in ctx.obj:
        profile_name = ctx.obj['profile_name']
    else:
        profile_name = DEFAULT_PROFILE_NAME
    
    return ProfileManager(profile_name)



def requires_setup_and_auth(func):
    """
    Enhanced decorator that handles all common global validations.
    
    This decorator performs:
    1. Setup validation (raises SetupRequiredError if not configured)
    2. API availability check (raises APIUnavailableError if API is down)
    
    This replaces the need for individual commands to check setup and handle
    global infrastructure concerns.
    
    Raises:
        SetupRequiredError: If profile is not configured
        APIUnavailableError: If API is not available
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Get context from args if available
        ctx = None
        for arg in args:
            if isinstance(arg, click.Context):
                ctx = arg
                break
        
        profile_manager = get_profile_manager_from_context(ctx)
        
        # Setup validation (global infrastructure concern)
        if not profile_manager.has_config():
            profile_name = profile_manager.profile_name
            raise SetupRequiredError(
                f"Setup required for profile '{profile_name}'. "
                f"Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
            )
        
        # API availability check (global infrastructure concern)
        try:
            config = profile_manager.load_config()
            api_gateway_url = config.get('api_gateway_url')
            
            if api_gateway_url:
                api_client = APIClient(api_gateway_url, profile_manager)
                # This will raise APIUnavailableError if there are issues
                api_client.check_api_availability()
        except APIUnavailableError:
            # Re-raise API unavailable errors as global infrastructure errors
            raise
        except Exception:
            # If we can't check availability, continue with the command
            # This prevents the availability check from blocking legitimate operations
            pass
        
        return func(*args, **kwargs)
    
    return wrapper


def requires_feature(feature_name: str, error_message: str = None):
    """
    Decorator to require a specific feature switch to be enabled.
    
    Args:
        feature_name: Name of the feature switch to check
        error_message: Custom error message (optional)
    
    Raises:
        click.ClickException: If the feature is not enabled
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Get context from args if available
            ctx = None
            for arg in args:
                if isinstance(arg, click.Context):
                    ctx = arg
                    break
            
            profile_manager = get_profile_manager_from_context(ctx)
            
            # Check if feature is enabled
            if not profile_manager.has_feature_switch(feature_name):
                if error_message is None:
                    default_message = f"Feature '{feature_name}' is not enabled for this environment."
                else:
                    default_message = error_message
                raise click.ClickException(default_message)
            
            return func(*args, **kwargs)
        
        return wrapper
    return decorator


def requires_api_access(func):
    """
    Legacy decorator for backward compatibility.
    
    This decorator maintains the old behavior where setup checks were not enforced
    at the decorator level, allowing commands to handle their own setup validation.
    This is kept for backward compatibility with existing commands and tests.
    
    For new commands, use @requires_setup_and_auth instead.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Get context from args if available
        ctx = None
        for arg in args:
            if isinstance(arg, click.Context):
                ctx = arg
                break
        
        profile_manager = get_profile_manager_from_context(ctx)
        
        # Skip API check if no configuration exists (setup command handles this)
        if not profile_manager.has_config():
            return func(*args, **kwargs)
        
        try:
            config = profile_manager.load_config()
            api_gateway_url = config.get('api_gateway_url')
            
            if api_gateway_url:
                api_client = APIClient(api_gateway_url, profile_manager)
                # Check API availability - this will raise APIUnavailableError if issues
                api_client.check_api_availability()
        
        except APIUnavailableError:
            # Re-raise API unavailable errors
            raise
        except Exception:
            # If we can't check availability, continue with the command
            # This prevents the availability check from blocking legitimate operations
            pass
        
        return func(*args, **kwargs)
    
    return wrapper
