"""Feature switches utility functions for VamsCLI."""

from typing import List
from .profile import ProfileManager


def get_enabled_features(profile_manager: ProfileManager) -> List[str]:
    """
    Get list of enabled features for the current profile.
    
    Args:
        profile_manager: ProfileManager instance
    
    Returns:
        List of enabled feature switch names
    """
    return profile_manager.get_feature_switches()


def is_feature_enabled(feature_name: str, profile_manager: ProfileManager) -> bool:
    """
    Check if a specific feature switch is enabled.
    
    Args:
        feature_name: Name of the feature switch to check
        profile_manager: ProfileManager instance
    
    Returns:
        True if the feature is enabled, False otherwise
    """
    return profile_manager.has_feature_switch(feature_name)


def require_feature(feature_name: str, profile_manager: ProfileManager, error_message: str = None):
    """
    Require a specific feature to be enabled, raise exception if not.
    
    Args:
        feature_name: Name of the feature switch to check
        profile_manager: ProfileManager instance
        error_message: Custom error message (optional)
    
    Raises:
        click.ClickException: If the feature is not enabled
    """
    import click
    
    if not is_feature_enabled(feature_name, profile_manager):
        if error_message is None:
            error_message = f"Feature '{feature_name}' is not enabled for this environment."
        raise click.ClickException(error_message)


def get_feature_switches_info(profile_manager: ProfileManager) -> dict:
    """
    Get detailed information about feature switches.
    
    Args:
        profile_manager: ProfileManager instance
    
    Returns:
        Dictionary with feature switches information
    """
    return profile_manager.get_feature_switches_info()
