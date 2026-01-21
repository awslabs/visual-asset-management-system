"""Profile and configuration management for VamsCLI with multi-profile support."""

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Union, List

from ..constants import (
    get_config_dir, get_profile_dir, validate_profile_name,
    CONFIG_FILE_NAME, AUTH_FILE_NAME, CREDENTIALS_FILE_NAME, 
    DEFAULT_PROFILE_NAME, ACTIVE_PROFILE_FILE, PROFILES_SUBDIR,
    # Legacy constants for backward compatibility
    CONFIG_FILE, AUTH_PROFILE_FILE, CREDENTIALS_FILE
)
from .exceptions import (
    ConfigurationError, ProfileNotFoundError, ProfileError, 
    InvalidProfileNameError, ProfileAlreadyExistsError
)


class ProfileManager:
    """Manages VamsCLI profiles and configuration with multi-profile support."""
    
    def __init__(self, profile_name: str = DEFAULT_PROFILE_NAME):
        self.profile_name = profile_name
        self.base_config_dir = get_config_dir()
        self.profile_dir = get_profile_dir(profile_name)
        self.active_profile_file = self.base_config_dir / ACTIVE_PROFILE_FILE
        
        # Profile-specific files
        self.config_file = self.profile_dir / CONFIG_FILE_NAME
        self.auth_profile_file = self.profile_dir / AUTH_FILE_NAME
        self.credentials_file = self.profile_dir / CREDENTIALS_FILE_NAME
        
        # Validate profile name
        if not validate_profile_name(profile_name):
            raise InvalidProfileNameError(
                f"Invalid profile name '{profile_name}'. Profile names must be 3-50 characters, "
                "alphanumeric with hyphens and underscores only, and cannot be reserved words."
            )
    
    def ensure_config_dir(self):
        """Ensure the base configuration directory exists."""
        self.base_config_dir.mkdir(parents=True, exist_ok=True)
        
    def ensure_profile_dir(self):
        """Ensure the profile-specific directory exists."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)
    
    def migrate_legacy_profile(self):
        """Migrate legacy single-profile configuration to default profile."""
        legacy_config = self.base_config_dir / CONFIG_FILE_NAME
        legacy_auth = self.base_config_dir / AUTH_FILE_NAME
        legacy_creds = self.base_config_dir / CREDENTIALS_FILE_NAME
        
        # Check if legacy files exist and default profile doesn't
        if (legacy_config.exists() or legacy_auth.exists()) and not self.profile_exists(DEFAULT_PROFILE_NAME):
            self.ensure_profile_dir()
            
            # Move legacy files to default profile
            if legacy_config.exists():
                shutil.move(str(legacy_config), str(self.profile_dir / CONFIG_FILE_NAME))
            if legacy_auth.exists():
                shutil.move(str(legacy_auth), str(self.profile_dir / AUTH_FILE_NAME))
            if legacy_creds.exists():
                shutil.move(str(legacy_creds), str(self.profile_dir / CREDENTIALS_FILE_NAME))
            
            # Set default as active profile
            self.set_active_profile(DEFAULT_PROFILE_NAME)
    
    def profile_exists(self, profile_name: str = None) -> bool:
        """Check if a profile exists."""
        if profile_name is None:
            profile_name = self.profile_name
        
        profile_dir = get_profile_dir(profile_name)
        return profile_dir.exists() and (profile_dir / CONFIG_FILE_NAME).exists()
    
    def list_profiles(self) -> List[str]:
        """List all available profiles."""
        profiles_dir = self.base_config_dir / PROFILES_SUBDIR
        if not profiles_dir.exists():
            return []
        
        profiles = []
        for item in profiles_dir.iterdir():
            if item.is_dir() and (item / CONFIG_FILE_NAME).exists():
                profiles.append(item.name)
        
        return sorted(profiles)
    
    def get_active_profile(self) -> str:
        """Get the currently active profile name."""
        if not self.active_profile_file.exists():
            return DEFAULT_PROFILE_NAME
        
        try:
            with open(self.active_profile_file, 'r') as f:
                data = json.load(f)
                return data.get('active_profile', DEFAULT_PROFILE_NAME)
        except (json.JSONDecodeError, IOError):
            return DEFAULT_PROFILE_NAME
    
    def set_active_profile(self, profile_name: str):
        """Set the active profile."""
        if not validate_profile_name(profile_name):
            raise InvalidProfileNameError(f"Invalid profile name: {profile_name}")
        
        self.ensure_config_dir()
        with open(self.active_profile_file, 'w') as f:
            json.dump({
                'active_profile': profile_name,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }, f, indent=2)
    
    def delete_profile(self, profile_name: str = None):
        """Delete an entire profile."""
        if profile_name is None:
            profile_name = self.profile_name
        
        if profile_name == DEFAULT_PROFILE_NAME:
            raise ProfileError("Cannot delete the default profile")
        
        profile_dir = get_profile_dir(profile_name)
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        
        # If this was the active profile, switch to default
        if self.get_active_profile() == profile_name:
            self.set_active_profile(DEFAULT_PROFILE_NAME)
    
    def wipe_profiles(self):
        """Remove all existing profiles and configuration."""
        if self.base_config_dir.exists():
            shutil.rmtree(self.base_config_dir)
        self.ensure_config_dir()
        
    def wipe_profile(self, profile_name: str = None):
        """Remove a specific profile's configuration."""
        if profile_name is None:
            profile_name = self.profile_name
            
        profile_dir = get_profile_dir(profile_name)
        if profile_dir.exists():
            shutil.rmtree(profile_dir)
        
        # If this was the active profile, switch to default
        if self.get_active_profile() == profile_name and profile_name != DEFAULT_PROFILE_NAME:
            self.set_active_profile(DEFAULT_PROFILE_NAME)
        
    def save_config(self, config: Dict[str, Any]):
        """Save the main configuration for this profile."""
        self.ensure_profile_dir()
        with open(self.config_file, 'w') as f:
            json.dump(config, f, indent=2)
        
        # Set this profile as active when saving config
        self.set_active_profile(self.profile_name)
            
    def load_config(self) -> Dict[str, Any]:
        """Load the main configuration for this profile."""
        # Try to migrate legacy profile first
        self.migrate_legacy_profile()
        
        if not self.config_file.exists():
            raise ProfileNotFoundError(
                f"Configuration not found for profile '{self.profile_name}'. "
                f"Please run 'vamscli setup <api-gateway-url> --profile {self.profile_name}' first."
            )
        
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            raise ConfigurationError(f"Failed to load configuration for profile '{self.profile_name}': {e}")
            
    def save_auth_profile(self, auth_data: Dict[str, Any]):
        """Save authentication profile for this profile."""
        self.ensure_profile_dir()
        with open(self.auth_profile_file, 'w') as f:
            json.dump(auth_data, f, indent=2)
            
    def load_auth_profile(self) -> Optional[Dict[str, Any]]:
        """Load authentication profile for this profile."""
        if not self.auth_profile_file.exists():
            return None
            
        try:
            with open(self.auth_profile_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
            
    def delete_auth_profile(self):
        """Delete authentication profile for this profile."""
        if self.auth_profile_file.exists():
            self.auth_profile_file.unlink()
        if self.credentials_file.exists():
            self.credentials_file.unlink()
            
    def save_credentials(self, credentials: Dict[str, str]):
        """Save user credentials for this profile (if explicitly requested)."""
        self.ensure_profile_dir()
        with open(self.credentials_file, 'w') as f:
            json.dump(credentials, f, indent=2)
            
    def load_credentials(self) -> Optional[Dict[str, str]]:
        """Load saved credentials for this profile."""
        if not self.credentials_file.exists():
            return None
            
        try:
            with open(self.credentials_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
            
    def has_config(self) -> bool:
        """Check if configuration exists for this profile."""
        # Check for legacy configuration first
        if self.profile_name == DEFAULT_PROFILE_NAME:
            legacy_config = self.base_config_dir / CONFIG_FILE_NAME
            if legacy_config.exists():
                return True
        
        return self.config_file.exists()
        
    def has_auth_profile(self) -> bool:
        """Check if authentication profile exists for this profile."""
        # Check for legacy auth profile first
        if self.profile_name == DEFAULT_PROFILE_NAME:
            legacy_auth = self.base_config_dir / AUTH_FILE_NAME
            if legacy_auth.exists():
                return True
        
        return self.auth_profile_file.exists()
        
    def has_credentials(self) -> bool:
        """Check if saved credentials exist for this profile."""
        # Check for legacy credentials first
        if self.profile_name == DEFAULT_PROFILE_NAME:
            legacy_creds = self.base_config_dir / CREDENTIALS_FILE_NAME
            if legacy_creds.exists():
                return True
        
        return self.credentials_file.exists()
    
    def get_auth_type(self) -> str:
        """
        Determine authentication type based on cognitoUserPoolId.
        Returns 'Cognito' if cognitoUserPoolId exists, otherwise 'External'.
        """
        try:
            config = self.load_config()
            amplify_config = config.get('amplify_config', {})
            cognito_user_pool_id = amplify_config.get('cognitoUserPoolId')
            
            # Check if cognitoUserPoolId is present and not empty/null/undefined
            if cognito_user_pool_id and cognito_user_pool_id not in ['undefined', 'null', '']:
                return 'Cognito'
            return 'External'
        except Exception:
            return 'Unknown'
    
    def get_profile_info(self) -> Dict[str, Any]:
        """Get information about this profile."""
        info = {
            'profile_name': self.profile_name,
            'profile_dir': str(self.profile_dir),
            'exists': self.profile_exists(),
            'has_config': self.has_config(),
            'has_auth': self.has_auth_profile(),
            'has_credentials': self.has_credentials(),
            'is_active': self.get_active_profile() == self.profile_name
        }
        
        if self.has_config():
            try:
                config = self.load_config()
                info['api_gateway_url'] = config.get('api_gateway_url', 'Unknown')
                info['cli_version'] = config.get('cli_version', 'Unknown')
                
                # Add full amplify_config (backward compatible)
                info['amplify_config'] = config.get('amplify_config', {})
                
                # Add authType (backward compatible)
                info['auth_type'] = self.get_auth_type()
            except Exception:
                info['api_gateway_url'] = 'Error loading config'
                info['cli_version'] = 'Error loading config'
        
        if self.has_auth_profile():
            try:
                auth_profile = self.load_auth_profile()
                if auth_profile:
                    info['user_id'] = auth_profile.get('user_id', 'Unknown')
                    info['token_type'] = auth_profile.get('token_type', 'cognito')
                    
                    # Get expiration info
                    expires_at = auth_profile.get('expires_at')
                    if expires_at:
                        info['token_expires_at'] = expires_at
                        info['token_expired'] = int(time.time()) >= expires_at
                    else:
                        info['token_expires_at'] = None
                        info['token_expired'] = False
                    
                    # Add webDeployedUrl (backward compatible - returns None if not present)
                    info['web_deployed_url'] = auth_profile.get('web_deployed_url')
                    
                    # Add locationServiceApiUrl (backward compatible - returns None if not present)
                    info['location_service_api_url'] = auth_profile.get('location_service_api_url')
            except Exception:
                info['user_id'] = 'Error loading auth'
                info['token_type'] = 'Error loading auth'
        
        return info
    
    def parse_expiration_time(self, expires_at: Union[str, int, None]) -> Optional[int]:
        """Parse expiration time from various formats to Unix timestamp."""
        if expires_at is None:
            return None
            
        if isinstance(expires_at, int):
            return expires_at
            
        if isinstance(expires_at, str):
            # Handle relative time (+3600 for 1 hour from now)
            if expires_at.startswith('+'):
                try:
                    seconds = int(expires_at[1:])
                    return int(time.time()) + seconds
                except ValueError:
                    raise ConfigurationError(f"Invalid relative time format: {expires_at}")
            
            # Handle Unix timestamp as string
            try:
                return int(expires_at)
            except ValueError:
                pass
            
            # Handle ISO 8601 format
            try:
                dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                return int(dt.timestamp())
            except ValueError:
                raise ConfigurationError(f"Invalid expiration time format: {expires_at}")
        
        raise ConfigurationError(f"Unsupported expiration time type: {type(expires_at)}")
    
    def save_override_token(self, token: str, user_id: str, expires_at: Union[str, int, None] = None):
        """Save override token profile for this profile."""
        expires_timestamp = self.parse_expiration_time(expires_at)
        
        override_profile = {
            'token_type': 'override',
            'user_id': user_id,
            'access_token': token,
            'expires_at': expires_timestamp,
            'override_metadata': {
                'created_at': datetime.now(timezone.utc).isoformat(),
                'source': 'external',
                'has_expiration': expires_timestamp is not None,
                'profile_name': self.profile_name
            }
        }
        
        self.save_auth_profile(override_profile)
    
    def is_override_token(self) -> bool:
        """Check if current auth profile is an override token."""
        auth_profile = self.load_auth_profile()
        return auth_profile is not None and auth_profile.get('token_type') == 'override'
    
    def is_token_expired(self) -> bool:
        """Check if current token is expired (for override tokens with expiration)."""
        auth_profile = self.load_auth_profile()
        if not auth_profile:
            return True
            
        expires_at = auth_profile.get('expires_at')
        if expires_at is None:
            return False  # No expiration set
            
        return int(time.time()) >= expires_at
    
    def get_token_expiration_info(self) -> Dict[str, Any]:
        """Get token expiration information for this profile."""
        auth_profile = self.load_auth_profile()
        if not auth_profile:
            return {'has_token': False, 'profile_name': self.profile_name}
        
        expires_at = auth_profile.get('expires_at')
        current_time = int(time.time())
        
        info = {
            'has_token': True,
            'profile_name': self.profile_name,
            'token_type': auth_profile.get('token_type', 'cognito'),
            'has_expiration': expires_at is not None,
            'expires_at': expires_at,
            'is_expired': False,
            'expires_in_seconds': None,
            'expires_in_human': None
        }
        
        if expires_at is not None:
            info['is_expired'] = current_time >= expires_at
            if not info['is_expired']:
                expires_in = expires_at - current_time
                info['expires_in_seconds'] = expires_in
                
                # Human readable format
                if expires_in < 60:
                    info['expires_in_human'] = f"{expires_in} seconds"
                elif expires_in < 3600:
                    info['expires_in_human'] = f"{expires_in // 60} minutes"
                elif expires_in < 86400:
                    info['expires_in_human'] = f"{expires_in // 3600} hours"
                else:
                    info['expires_in_human'] = f"{expires_in // 86400} days"
        
        return info
    
    def save_feature_switches(self, feature_switches_data: Dict[str, Any]):
        """
        Save feature switches, webDeployedUrl, and locationServiceApiUrl from secure-config API.
        
        Args:
            feature_switches_data: Secure config data from API containing:
                - featuresEnabled: Comma-separated feature flags
                - webDeployedUrl: Web application deployment URL
                - locationServiceApiUrl: Location service API URL
        """
        auth_profile = self.load_auth_profile()
        if not auth_profile:
            return  # No auth profile to update
        
        # Parse the featuresEnabled string into a list
        features_enabled_str = feature_switches_data.get('featuresEnabled', '')
        enabled_features = [f.strip() for f in features_enabled_str.split(',') if f.strip()]
        
        # Add feature switches to auth profile
        auth_profile['feature_switches'] = {
            'raw': features_enabled_str,
            'enabled': enabled_features,
            'fetched_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Store webDeployedUrl (backward compatible - uses .get())
        auth_profile['web_deployed_url'] = feature_switches_data.get('webDeployedUrl')
        
        # Store locationServiceApiUrl (backward compatible - uses .get())
        auth_profile['location_service_api_url'] = feature_switches_data.get('locationServiceApiUrl')
        
        # Save updated auth profile
        self.save_auth_profile(auth_profile)
    
    def get_feature_switches(self) -> List[str]:
        """
        Get parsed feature switches list from authentication profile.
        
        Returns:
            List of enabled feature switch names
        """
        auth_profile = self.load_auth_profile()
        if not auth_profile:
            return []
        
        feature_switches = auth_profile.get('feature_switches', {})
        return feature_switches.get('enabled', [])
    
    def has_feature_switch(self, feature_name: str) -> bool:
        """
        Check if a specific feature switch is enabled.
        
        Args:
            feature_name: Name of the feature switch to check
        
        Returns:
            True if the feature is enabled, False otherwise
        """
        enabled_features = self.get_feature_switches()
        return feature_name in enabled_features
    
    def get_feature_switches_info(self) -> Dict[str, Any]:
        """
        Get detailed information about feature switches.
        
        Returns:
            Dictionary with feature switches information
        """
        auth_profile = self.load_auth_profile()
        if not auth_profile:
            return {
                'has_feature_switches': False,
                'enabled': [],
                'count': 0,
                'fetched_at': None
            }
        
        feature_switches = auth_profile.get('feature_switches', {})
        enabled_features = feature_switches.get('enabled', [])
        
        return {
            'has_feature_switches': bool(feature_switches),
            'enabled': enabled_features,
            'count': len(enabled_features),
            'raw': feature_switches.get('raw', ''),
            'fetched_at': feature_switches.get('fetched_at')
        }

    @classmethod
    def get_all_profiles_info(cls) -> List[Dict[str, Any]]:
        """Get information about all profiles."""
        base_config_dir = get_config_dir()
        profiles_dir = base_config_dir / PROFILES_SUBDIR
        
        profiles_info = []
        
        # Check for legacy profile
        legacy_config = base_config_dir / CONFIG_FILE_NAME
        if legacy_config.exists():
            # Create temporary ProfileManager for default to get info
            temp_manager = cls(DEFAULT_PROFILE_NAME)
            temp_manager.migrate_legacy_profile()
        
        # Get all profile directories
        if profiles_dir.exists():
            for item in profiles_dir.iterdir():
                if item.is_dir():
                    try:
                        profile_manager = cls(item.name)
                        if profile_manager.has_config():
                            profiles_info.append(profile_manager.get_profile_info())
                    except InvalidProfileNameError:
                        # Skip invalid profile names
                        continue
        
        # If no profiles found, check if we need to create default
        if not profiles_info:
            # Create default profile manager to trigger migration
            default_manager = cls(DEFAULT_PROFILE_NAME)
            default_manager.migrate_legacy_profile()
            if default_manager.has_config():
                profiles_info.append(default_manager.get_profile_info())
        
        return profiles_info
    
    @classmethod
    def create_profile_manager(cls, profile_name: str = None) -> 'ProfileManager':
        """Create a ProfileManager instance, using active profile if none specified."""
        if profile_name is None:
            # Get active profile
            base_config_dir = get_config_dir()
            active_profile_file = base_config_dir / ACTIVE_PROFILE_FILE
            
            if active_profile_file.exists():
                try:
                    with open(active_profile_file, 'r') as f:
                        data = json.load(f)
                        profile_name = data.get('active_profile', DEFAULT_PROFILE_NAME)
                except (json.JSONDecodeError, IOError):
                    profile_name = DEFAULT_PROFILE_NAME
            else:
                profile_name = DEFAULT_PROFILE_NAME
        
        return cls(profile_name)

    # Properties for backward compatibility
    @property
    def config_dir(self):
        """Backward compatibility property."""
        return self.profile_dir
