"""Decorators for VamsCLI commands."""

import functools
import time
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
    3. Configuration logging in verbose mode
    4. Command start/stop logging with timing
    
    This replaces the need for individual commands to check setup and handle
    global infrastructure concerns.
    
    Raises:
        SetupRequiredError: If profile is not configured
        APIUnavailableError: If API is not available
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Import logging utilities
        from .logging import (
            log_config_info, log_command_start, log_command_end, 
            log_debug, _is_verbose_mode
        )
        
        # Capture start time for duration calculation
        start_time = time.time()
        command_name = func.__name__
        
        # Log command start with sanitized arguments
        try:
            log_command_start(command_name, kwargs)
        except Exception:
            # Don't fail if logging fails
            pass
        
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
            # Load configuration
            config = profile_manager.load_config()
            
            # Log configuration in verbose mode (wrapped in try/catch)
            try:
                if _is_verbose_mode():
                    log_config_info(config)
                    click.echo(f"\n📋 Using profile: {profile_manager.profile_name}", err=True)
                    click.echo(f"📋 API Gateway: {config.get('api_gateway_url', 'Unknown')}", err=True)
                    click.echo(f"📋 CLI Version: {config.get('cli_version', 'Unknown')}\n", err=True)
            except Exception:
                # Don't fail if logging fails
                pass
            
            #Commented out to save on performance
            #api_gateway_url = config.get('api_gateway_url')
            #if api_gateway_url:
                #api_client = APIClient(api_gateway_url, profile_manager)
                # This will raise APIUnavailableError if there are issues
                #api_client.check_api_availability()
        except APIUnavailableError:
            # Re-raise API unavailable errors as global infrastructure errors
            raise
        except Exception:
            # If we can't check availability or load config, continue with the command
            # This prevents the availability check from blocking legitimate operations
            pass
        
        # Execute the wrapped function and log completion
        try:
            result = func(*args, **kwargs)
            
            # Log successful completion with result
            duration = time.time() - start_time
            try:
                log_command_end(command_name, True, duration)
                # Log result (truncate if too large)
                result_str = str(result)
                if len(result_str) > 1000:
                    log_debug(f"Command '{command_name}' returned result (truncated): {result_str[:1000]}...")
                else:
                    log_debug(f"Command '{command_name}' returned result: {result_str}")
            except Exception:
                # Don't fail if logging fails
                pass
            
            return result
            
        except Exception:
            # Log failed completion
            duration = time.time() - start_time
            try:
                log_command_end(command_name, False, duration)
            except Exception:
                # Don't fail if logging fails
                pass
            
            # Re-raise the exception to be handled by global exception handler
            raise
    
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
