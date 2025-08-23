"""Test profile management commands."""

import json
import pytest
import click
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    ProfileError, InvalidProfileNameError, ProfileAlreadyExistsError
)
from vamscli.constants import DEFAULT_PROFILE_NAME, validate_profile_name


# File-level fixtures for profile-specific testing patterns
@pytest.fixture
def profile_command_mocks(generic_command_mocks):
    """Provide profile-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for profile command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('profile')


@pytest.fixture
def profile_no_setup_mocks(no_setup_command_mocks):
    """Provide profile command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('profile')


class TestProfileValidation:
    """Test profile name validation utility functions."""
    
    def test_validate_profile_name_valid(self):
        """Test valid profile names."""
        valid_names = [
            "default",
            "production", 
            "staging",
            "dev-environment",
            "user_profile",
            "test123",
            "a-b-c",
            "user_123"
        ]
        
        for name in valid_names:
            assert validate_profile_name(name), f"'{name}' should be valid"
    
    def test_validate_profile_name_invalid(self):
        """Test invalid profile names."""
        invalid_names = [
            "",  # Empty
            "ab",  # Too short
            "a" * 51,  # Too long
            "help",  # Reserved
            "version",  # Reserved
            "list",  # Reserved
            "profile with spaces",  # Spaces
            "profile@domain",  # Special characters
            "profile.name",  # Dots
            "profile/name",  # Slashes
        ]
        
        for name in invalid_names:
            assert not validate_profile_name(name), f"'{name}' should be invalid"


class TestProfileListCommand:
    """Test profile list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['profile', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all available profiles' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager.get_all_profiles_info')
    @patch('vamscli.commands.profile.ProfileManager')
    def test_list_success_with_profiles(self, mock_profile_manager_class, mock_get_all_profiles, cli_runner):
        """Test successful profile listing with existing profiles."""
        # Mock ProfileManager instance for get_active_profile call
        mock_profile_manager = Mock()
        mock_profile_manager.get_active_profile.return_value = 'default'
        mock_profile_manager_class.return_value = mock_profile_manager
        
        # Mock profiles data
        mock_get_all_profiles.return_value = [
            {
                'profile_name': 'default',
                'is_active': True,
                'has_config': True,
                'has_auth': True,
                'has_credentials': False,
                'api_gateway_url': 'https://api.example.com',
                'cli_version': '2.2.0',
                'user_id': 'test@example.com',
                'token_type': 'cognito',
                'token_expired': False
            },
            {
                'profile_name': 'staging',
                'is_active': False,
                'has_config': True,
                'has_auth': False,
                'has_credentials': False,
                'api_gateway_url': 'https://staging.example.com',
                'cli_version': '2.2.0'
            }
        ]
        
        result = cli_runner.invoke(cli, ['profile', 'list'])
        
        assert result.exit_code == 0
        assert 'Available profiles:' in result.output
        assert '● default (active)' in result.output
        assert '○ staging' in result.output
        assert 'https://api.example.com' in result.output
        assert 'test@example.com' in result.output
        assert '✓ Authenticated' in result.output
        assert 'Not authenticated' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager.get_all_profiles_info')
    @patch('vamscli.commands.profile.ProfileManager')
    def test_list_no_profiles(self, mock_profile_manager_class, mock_get_all_profiles, cli_runner):
        """Test profile listing when no profiles exist."""
        mock_get_all_profiles.return_value = []
        
        result = cli_runner.invoke(cli, ['profile', 'list'])
        
        assert result.exit_code == 0
        assert 'No profiles found.' in result.output
        assert 'vamscli setup' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager.get_all_profiles_info')
    def test_list_error_handling(self, mock_get_all_profiles, cli_runner):
        """Test profile list error handling."""
        mock_get_all_profiles.side_effect = Exception("Profile access error")
        
        result = cli_runner.invoke(cli, ['profile', 'list'])
        
        assert result.exit_code == 0  # Command handles error gracefully
        assert 'Error listing profiles' in result.output


class TestProfileSwitchCommand:
    """Test profile switch command."""
    
    def test_switch_help(self, cli_runner):
        """Test switch command help."""
        result = cli_runner.invoke(cli, ['profile', 'switch', '--help'])
        assert result.exit_code == 0
        assert 'Switch to a different profile' in result.output
        assert 'PROFILE_NAME' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_switch_success(self, mock_profile_manager_class, cli_runner):
        """Test successful profile switch."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.set_active_profile.return_value = None
        mock_profile_manager.get_profile_info.return_value = {
            'api_gateway_url': 'https://staging.example.com',
            'has_auth': True,
            'user_id': 'test@staging.com'
        }
        mock_profile_manager_class.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['profile', 'switch', 'staging'])
        
        assert result.exit_code == 0
        assert "✓ Switched to profile 'staging'" in result.output
        assert 'https://staging.example.com' in result.output
        assert 'test@staging.com' in result.output
        
        # Verify ProfileManager was called correctly
        mock_profile_manager_class.assert_called_with('staging')
        mock_profile_manager.has_config.assert_called_once()
        mock_profile_manager.set_active_profile.assert_called_once_with('staging')
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_switch_nonexistent_profile(self, mock_profile_manager_class, cli_runner):
        """Test switching to non-existent profile."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = False
        mock_profile_manager_class.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['profile', 'switch', 'nonexistent'])
        
        assert result.exit_code == 1
        assert 'does not exist or is not configured' in result.output
        assert 'vamscli setup' in result.output
    
    def test_switch_invalid_profile_name(self, cli_runner):
        """Test switching to invalid profile name."""
        result = cli_runner.invoke(cli, ['profile', 'switch', 'invalid name'])
        
        assert result.exit_code == 1
        assert 'Invalid profile name' in result.output
        assert 'alphanumeric with hyphens and underscores' in result.output
    
    def test_switch_missing_profile_name(self, cli_runner):
        """Test switch command without profile name."""
        result = cli_runner.invoke(cli, ['profile', 'switch'])
        
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'PROFILE_NAME' in result.output


class TestProfileDeleteCommand:
    """Test profile delete command."""
    
    def test_delete_help(self, cli_runner):
        """Test delete command help."""
        result = cli_runner.invoke(cli, ['profile', 'delete', '--help'])
        assert result.exit_code == 0
        assert 'Delete a profile and all its configuration' in result.output
        assert '--force' in result.output
        assert 'PROFILE_NAME' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_delete_success_with_force(self, mock_profile_manager_class, cli_runner):
        """Test successful profile deletion with force flag."""
        mock_profile_manager = Mock()
        mock_profile_manager.profile_exists.return_value = True
        mock_profile_manager.delete_profile.return_value = None
        mock_profile_manager.get_active_profile.return_value = 'default'
        mock_profile_manager_class.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['profile', 'delete', 'test-profile', '--force'])
        
        assert result.exit_code == 0
        assert "✓ Profile 'test-profile' deleted successfully" in result.output
        assert 'Active profile is now: default' in result.output
        
        # Verify ProfileManager was called correctly
        mock_profile_manager.delete_profile.assert_called_once_with('test-profile')
    
    @patch('click.confirm')
    @patch('vamscli.commands.profile.ProfileManager')
    def test_delete_success_with_confirmation(self, mock_profile_manager_class, mock_confirm, cli_runner):
        """Test successful profile deletion with user confirmation."""
        mock_profile_manager = Mock()
        mock_profile_manager.profile_exists.return_value = True
        mock_profile_manager.get_profile_info.return_value = {
            'has_config': True,
            'has_auth': True,
            'api_gateway_url': 'https://test.example.com',
            'user_id': 'test@example.com'
        }
        mock_profile_manager.delete_profile.return_value = None
        mock_profile_manager.get_active_profile.return_value = 'default'
        mock_profile_manager_class.return_value = mock_profile_manager
        
        mock_confirm.return_value = True  # User confirms deletion
        
        result = cli_runner.invoke(cli, ['profile', 'delete', 'test-profile'])
        
        assert result.exit_code == 0
        assert "✓ Profile 'test-profile' deleted successfully" in result.output
        assert 'https://test.example.com' in result.output
        assert 'test@example.com' in result.output
        
        # Verify confirmation was requested
        mock_confirm.assert_called_once()
    
    @patch('click.confirm')
    @patch('vamscli.commands.profile.ProfileManager')
    def test_delete_cancelled_by_user(self, mock_profile_manager_class, mock_confirm, cli_runner):
        """Test profile deletion cancelled by user."""
        mock_profile_manager = Mock()
        mock_profile_manager.profile_exists.return_value = True
        mock_profile_manager.get_profile_info.return_value = {
            'has_config': True,
            'has_auth': False
        }
        mock_profile_manager_class.return_value = mock_profile_manager
        
        mock_confirm.return_value = False  # User cancels deletion
        
        result = cli_runner.invoke(cli, ['profile', 'delete', 'test-profile'])
        
        assert result.exit_code == 0
        assert 'Deletion cancelled.' in result.output
        
        # Verify delete_profile was not called
        mock_profile_manager.delete_profile.assert_not_called()
    
    def test_delete_default_profile(self, cli_runner):
        """Test that default profile cannot be deleted."""
        result = cli_runner.invoke(cli, ['profile', 'delete', DEFAULT_PROFILE_NAME, '--force'])
        
        assert result.exit_code == 1
        assert 'Cannot delete the default profile' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_delete_nonexistent_profile(self, mock_profile_manager_class, cli_runner):
        """Test deleting non-existent profile."""
        mock_profile_manager = Mock()
        mock_profile_manager.profile_exists.return_value = False
        mock_profile_manager_class.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['profile', 'delete', 'nonexistent', '--force'])
        
        assert result.exit_code == 0
        assert "Profile 'nonexistent' does not exist." in result.output
        
        # Verify delete_profile was not called
        mock_profile_manager.delete_profile.assert_not_called()
    
    def test_delete_invalid_profile_name(self, cli_runner):
        """Test deleting profile with invalid name."""
        result = cli_runner.invoke(cli, ['profile', 'delete', 'invalid name', '--force'])
        
        assert result.exit_code == 1
        assert 'Invalid profile name' in result.output


class TestProfileInfoCommand:
    """Test profile info command."""
    
    def test_info_help(self, cli_runner):
        """Test info command help."""
        result = cli_runner.invoke(cli, ['profile', 'info', '--help'])
        assert result.exit_code == 0
        assert 'Show detailed information about a profile' in result.output
        assert 'PROFILE_NAME' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_info_success(self, mock_profile_manager_class, cli_runner):
        """Test successful profile info display."""
        mock_profile_manager = Mock()
        mock_profile_manager.profile_exists.return_value = True
        mock_profile_manager.get_profile_info.return_value = {
            'is_active': True,
            'profile_dir': '/path/to/profile',
            'has_config': True,
            'has_auth': True,
            'has_credentials': True,
            'api_gateway_url': 'https://api.example.com',
            'cli_version': '2.2.0',
            'user_id': 'test@example.com',
            'token_type': 'cognito',
            'token_expired': False,
            'token_expires_at': 1640995200  # 2022-01-01 00:00:00 UTC
        }
        mock_profile_manager_class.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['profile', 'info', 'test-profile'])
        
        assert result.exit_code == 0
        assert 'Profile: test-profile' in result.output
        assert 'Active: Yes' in result.output
        assert '/path/to/profile' in result.output
        assert 'https://api.example.com' in result.output
        assert 'test@example.com' in result.output
        assert '✓ Authenticated' in result.output
        assert 'Saved Credentials: Yes' in result.output
        assert 'Expires:' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_info_nonexistent_profile(self, mock_profile_manager_class, cli_runner):
        """Test info for non-existent profile."""
        mock_profile_manager = Mock()
        mock_profile_manager.profile_exists.return_value = False
        mock_profile_manager_class.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['profile', 'info', 'nonexistent'])
        
        assert result.exit_code == 0
        assert "Profile 'nonexistent' does not exist." in result.output
    
    def test_info_invalid_profile_name(self, cli_runner):
        """Test info for invalid profile name."""
        result = cli_runner.invoke(cli, ['profile', 'info', 'invalid name'])
        
        assert result.exit_code == 1
        assert 'Invalid profile name' in result.output
    
    def test_info_missing_profile_name(self, cli_runner):
        """Test info command without profile name."""
        result = cli_runner.invoke(cli, ['profile', 'info'])
        
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'PROFILE_NAME' in result.output


class TestProfileCurrentCommand:
    """Test profile current command."""
    
    def test_current_help(self, cli_runner):
        """Test current command help."""
        result = cli_runner.invoke(cli, ['profile', 'current', '--help'])
        assert result.exit_code == 0
        assert 'Show the currently active profile' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_current_success_with_auth(self, mock_profile_manager_class, cli_runner):
        """Test current profile display with authentication."""
        mock_profile_manager = Mock()
        mock_profile_manager.get_active_profile.return_value = 'production'
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.has_auth_profile.return_value = True
        mock_profile_manager.load_config.return_value = {
            'api_gateway_url': 'https://prod.example.com'
        }
        mock_profile_manager.load_auth_profile.return_value = {
            'user_id': 'prod@example.com',
            'token_type': 'cognito'
        }
        mock_profile_manager_class.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['profile', 'current'])
        
        assert result.exit_code == 0
        assert 'Current active profile: production' in result.output
        assert 'https://prod.example.com' in result.output
        assert 'Authenticated as: prod@example.com (Cognito)' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_current_success_no_auth(self, mock_profile_manager_class, cli_runner):
        """Test current profile display without authentication."""
        mock_profile_manager = Mock()
        mock_profile_manager.get_active_profile.return_value = 'staging'
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.has_auth_profile.return_value = False
        mock_profile_manager.load_config.return_value = {
            'api_gateway_url': 'https://staging.example.com'
        }
        mock_profile_manager_class.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['profile', 'current'])
        
        assert result.exit_code == 0
        assert 'Current active profile: staging' in result.output
        assert 'https://staging.example.com' in result.output
        assert 'Status: Not authenticated' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_current_error_handling(self, mock_profile_manager_class, cli_runner):
        """Test current profile error handling."""
        mock_profile_manager = Mock()
        mock_profile_manager.get_active_profile.side_effect = Exception("Profile access error")
        mock_profile_manager_class.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['profile', 'current'])
        
        assert result.exit_code == 0  # Command handles error gracefully
        assert 'Error getting current profile' in result.output


class TestProfileManagerIntegration:
    """Test ProfileManager class integration scenarios."""
    
    def test_profile_manager_initialization(self, cli_runner):
        """Test ProfileManager initialization with valid profile name."""
        from vamscli.utils.profile import ProfileManager
        
        # Test basic initialization - focus on what we can reliably test
        profile_manager = ProfileManager("test-profile")
        
        assert profile_manager.profile_name == "test-profile"
        # Test that the base_config_dir is a Path object and exists as an attribute
        assert hasattr(profile_manager, 'base_config_dir')
        assert profile_manager.base_config_dir is not None
    
    def test_profile_manager_invalid_name(self, cli_runner):
        """Test ProfileManager with invalid profile name."""
        from vamscli.utils.profile import ProfileManager
        
        with pytest.raises(InvalidProfileNameError):
            ProfileManager("invalid name")
    
    @patch('vamscli.utils.profile.ProfileManager.has_config')
    @patch('vamscli.utils.profile.ProfileManager.load_config')
    def test_profile_manager_config_operations(self, mock_load_config, mock_has_config, cli_runner):
        """Test ProfileManager configuration operations."""
        from vamscli.utils.profile import ProfileManager
        
        mock_has_config.return_value = True
        mock_load_config.return_value = {
            'api_gateway_url': 'https://api.example.com',
            'cli_version': '2.2.0'
        }
        
        profile_manager = ProfileManager("test-profile")
        
        assert profile_manager.has_config()
        config = profile_manager.load_config()
        assert config['api_gateway_url'] == 'https://api.example.com'


class TestProfileCommandsIntegration:
    """Test integration scenarios for profile commands."""
    
    def test_commands_require_profile_name_where_appropriate(self, cli_runner):
        """Test that profile commands require profile name where appropriate."""
        # Test switch without profile name
        result = cli_runner.invoke(cli, ['profile', 'switch'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'PROFILE_NAME' in result.output
        
        # Test delete without profile name
        result = cli_runner.invoke(cli, ['profile', 'delete'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'PROFILE_NAME' in result.output
        
        # Test info without profile name
        result = cli_runner.invoke(cli, ['profile', 'info'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'PROFILE_NAME' in result.output
    
    @patch('vamscli.commands.profile.ProfileManager')
    def test_profile_error_handling_consistency(self, mock_profile_manager_class, cli_runner):
        """Test consistent error handling across profile commands."""
        # Configure the mock to raise an exception when instantiated
        mock_profile_manager_class.side_effect = Exception("Unexpected error")
        
        # Test that all commands handle unexpected errors gracefully
        commands_to_test = [
            (['profile', 'switch', 'test'], 1),  # Should exit with error
            (['profile', 'delete', 'test', '--force'], 1),  # Should exit with error
            (['profile', 'info', 'test'], 1),  # Should exit with error
        ]
        
        for command, expected_exit_code in commands_to_test:
            result = cli_runner.invoke(cli, command)
            assert result.exit_code == expected_exit_code
            assert 'error' in result.output.lower() or 'failed' in result.output.lower()


if __name__ == '__main__':
    pytest.main([__file__])
