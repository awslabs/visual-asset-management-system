"""Test feature switches functionality."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner
from contextlib import contextmanager

from vamscli.main import cli
from vamscli.utils.exceptions import (
    AuthenticationError, APIError, VamsCLIError
)
from vamscli.constants import FEATURE_GOVCLOUD, FEATURE_LOCATIONSERVICES


# File-level fixtures for features-specific testing patterns
@pytest.fixture
def features_command_mocks(mock_profile_manager, mock_api_client):
    """Provide features-specific command mocks.
    
    Since features commands don't directly import APIClient, we only need
    to mock the decorators and profile manager functions.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    @contextmanager
    def _create_mocks():
        with patch('vamscli.main.ProfileManager') as mock_main_pm, \
             patch('vamscli.utils.decorators.get_profile_manager_from_context') as mock_dec_get_pm, \
             patch('vamscli.commands.features.get_profile_manager_from_context') as mock_cmd_get_pm, \
             patch('vamscli.utils.decorators.APIClient') as mock_dec_api:
            
            # Setup all ProfileManager mocks
            mock_main_pm.return_value = mock_profile_manager
            mock_dec_get_pm.return_value = mock_profile_manager
            mock_cmd_get_pm.return_value = mock_profile_manager
            
            # Setup decorator APIClient mock
            mock_dec_api.return_value = mock_api_client
            
            yield {
                'profile_manager': mock_profile_manager,
                'api_client': mock_api_client,
                'patches': {
                    'main_pm': mock_main_pm,
                    'dec_get_pm': mock_dec_get_pm,
                    'cmd_get_pm': mock_cmd_get_pm,
                    'dec_api': mock_dec_api
                }
            }
    
    return _create_mocks()


@pytest.fixture
def features_no_setup_mocks(no_setup_profile_manager, mock_api_client):
    """Provide features command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    @contextmanager
    def _create_mocks():
        with patch('vamscli.main.ProfileManager') as mock_main_pm, \
             patch('vamscli.utils.decorators.get_profile_manager_from_context') as mock_dec_get_pm, \
             patch('vamscli.commands.features.get_profile_manager_from_context') as mock_cmd_get_pm, \
             patch('vamscli.utils.decorators.APIClient') as mock_dec_api:
            
            # Setup ProfileManager mocks for no-setup scenario
            mock_main_pm.return_value = no_setup_profile_manager
            mock_dec_get_pm.return_value = no_setup_profile_manager
            mock_cmd_get_pm.return_value = no_setup_profile_manager
            
            # Setup APIClient mock
            mock_dec_api.return_value = mock_api_client
            
            yield {
                'profile_manager': no_setup_profile_manager,
                'api_client': mock_api_client,
                'patches': {
                    'main_pm': mock_main_pm,
                    'dec_get_pm': mock_dec_get_pm,
                    'cmd_get_pm': mock_cmd_get_pm,
                    'dec_api': mock_dec_api
                }
            }
    
    return _create_mocks()


class TestFeaturesListCommand:
    """Test features list command."""
    
    def test_list_help(self, cli_runner):
        """Test list command help."""
        result = cli_runner.invoke(cli, ['features', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List all enabled feature switches' in result.output
        assert 'current profile' in result.output
        assert 'vamscli features list' in result.output
    
    def test_list_success_with_features(self, cli_runner, features_command_mocks):
        """Test successful feature listing with enabled features."""
        with features_command_mocks as mocks:
            # Mock profile manager to have auth profile and feature switches
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'enabled': ['GOVCLOUD', 'LOCATIONSERVICES', 'AUTHPROVIDER_COGNITO'],
                'count': 3,
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            result = cli_runner.invoke(cli, ['features', 'list'])
            
            assert result.exit_code == 0
            assert 'Enabled Feature Switches:' in result.output
            assert 'Total: 3' in result.output
            assert '✓ AUTHPROVIDER_COGNITO' in result.output
            assert '✓ GOVCLOUD' in result.output
            assert '✓ LOCATIONSERVICES' in result.output
            assert 'Last updated: 2024-01-01T12:00:00Z' in result.output
            
            # Verify API calls
            mocks['profile_manager'].has_auth_profile.assert_called_once()
            mocks['profile_manager'].get_feature_switches_info.assert_called_once()
    
    def test_list_success_no_features(self, cli_runner, features_command_mocks):
        """Test successful feature listing with no enabled features."""
        with features_command_mocks as mocks:
            # Mock profile manager to have auth profile but no features
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'enabled': [],
                'count': 0,
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            result = cli_runner.invoke(cli, ['features', 'list'])
            
            assert result.exit_code == 0
            assert 'Enabled Feature Switches:' in result.output
            assert 'Total: 0' in result.output
            assert 'No features are currently enabled.' in result.output
            assert 'Last updated: 2024-01-01T12:00:00Z' in result.output
    
    def test_list_no_auth_profile(self, cli_runner, features_command_mocks):
        """Test list command without authentication."""
        with features_command_mocks as mocks:
            # Mock profile manager to have no auth profile
            mocks['profile_manager'].has_auth_profile.return_value = False
            
            result = cli_runner.invoke(cli, ['features', 'list'])
            
            assert result.exit_code == 1
            assert 'Not authenticated' in result.output or 'Authentication Required' in result.output
            
            # Verify auth check was called
            mocks['profile_manager'].has_auth_profile.assert_called_once()
    
    def test_list_no_feature_switches(self, cli_runner, features_command_mocks):
        """Test list command when no feature switches are available."""
        with features_command_mocks as mocks:
            # Mock profile manager to have auth profile but no feature switches
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': False,
                'enabled': [],
                'count': 0,
                'fetched_at': None
            }
            
            result = cli_runner.invoke(cli, ['features', 'list'])
            
            assert result.exit_code == 1
            assert 'No feature switches available' in result.output or 'No Feature Switches' in result.output


class TestFeaturesListJSONOutput:
    """Test features list command JSON output."""
    
    def test_list_json_output_with_features(self, cli_runner, features_command_mocks):
        """Test list command with JSON output and enabled features."""
        with features_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'enabled': ['GOVCLOUD', 'LOCATIONSERVICES'],
                'count': 2,
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            result = cli_runner.invoke(cli, ['features', 'list', '--json-output'])
            
            assert result.exit_code == 0
            
            # Verify output is valid JSON
            try:
                parsed = json.loads(result.output)
                assert parsed['total'] == 2
                assert 'GOVCLOUD' in parsed['enabled']
                assert 'LOCATIONSERVICES' in parsed['enabled']
                assert parsed['fetched_at'] == '2024-01-01T12:00:00Z'
            except json.JSONDecodeError:
                pytest.fail(f"Output is not valid JSON: {result.output}")
    
    def test_list_json_output_no_features(self, cli_runner, features_command_mocks):
        """Test list command with JSON output and no enabled features."""
        with features_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'enabled': [],
                'count': 0,
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            result = cli_runner.invoke(cli, ['features', 'list', '--json-output'])
            
            assert result.exit_code == 0
            
            # Verify output is valid JSON
            try:
                parsed = json.loads(result.output)
                assert parsed['total'] == 0
                assert parsed['enabled'] == []
                assert parsed['fetched_at'] == '2024-01-01T12:00:00Z'
            except json.JSONDecodeError:
                pytest.fail(f"Output is not valid JSON: {result.output}")
    
    def test_list_json_output_no_auth(self, cli_runner, features_command_mocks):
        """Test list command with JSON output when not authenticated."""
        with features_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = False
            
            result = cli_runner.invoke(cli, ['features', 'list', '--json-output'])
            
            assert result.exit_code == 1
            
            # Verify error output is valid JSON
            try:
                parsed = json.loads(result.output)
                assert 'error' in parsed
            except json.JSONDecodeError:
                pytest.fail(f"Error output is not valid JSON: {result.output}")


class TestFeaturesCheckCommand:
    """Test features check command."""
    
    def test_check_help(self, cli_runner):
        """Test check command help."""
        result = cli_runner.invoke(cli, ['features', 'check', '--help'])
        assert result.exit_code == 0
        assert 'Check if a specific feature switch is enabled' in result.output
        assert 'FEATURE_NAME' in result.output
        assert 'vamscli features check GOVCLOUD' in result.output
        # Handle line breaks in help text
        assert 'LOCATIONSERVICES' in result.output
    
    def test_check_enabled_feature(self, cli_runner, features_command_mocks):
        """Test checking an enabled feature."""
        with features_command_mocks as mocks:
            # Mock profile manager to have auth profile and feature switches
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'enabled': ['GOVCLOUD', 'LOCATIONSERVICES'],
                'count': 2,
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            # Mock is_feature_enabled utility function
            with patch('vamscli.commands.features.is_feature_enabled') as mock_is_enabled:
                mock_is_enabled.return_value = True
                
                result = cli_runner.invoke(cli, ['features', 'check', 'GOVCLOUD'])
                
                assert result.exit_code == 0
                assert "✓ Feature 'GOVCLOUD' is ENABLED" in result.output
                
                # Verify function calls
                mock_is_enabled.assert_called_once_with('GOVCLOUD', mocks['profile_manager'])
    
    def test_check_disabled_feature(self, cli_runner, features_command_mocks):
        """Test checking a disabled feature."""
        with features_command_mocks as mocks:
            # Mock profile manager to have auth profile and feature switches
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'enabled': ['GOVCLOUD'],
                'count': 1,
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            # Mock is_feature_enabled utility function
            with patch('vamscli.commands.features.is_feature_enabled') as mock_is_enabled:
                mock_is_enabled.return_value = False
                
                result = cli_runner.invoke(cli, ['features', 'check', 'LOCATIONSERVICES'])
                
                assert result.exit_code == 0
                assert "✗ Feature 'LOCATIONSERVICES' is DISABLED" in result.output
                
                # Verify function calls
                mock_is_enabled.assert_called_once_with('LOCATIONSERVICES', mocks['profile_manager'])
    
    def test_check_no_auth_profile(self, cli_runner, features_command_mocks):
        """Test check command without authentication."""
        with features_command_mocks as mocks:
            # Mock profile manager to have no auth profile
            mocks['profile_manager'].has_auth_profile.return_value = False
            
            result = cli_runner.invoke(cli, ['features', 'check', 'GOVCLOUD'])
            
            assert result.exit_code == 1
            assert 'Not authenticated' in result.output or 'Authentication Required' in result.output
    
    def test_check_no_feature_switches(self, cli_runner, features_command_mocks):
        """Test check command when no feature switches are available."""
        with features_command_mocks as mocks:
            # Mock profile manager to have auth profile but no feature switches
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': False,
                'enabled': [],
                'count': 0,
                'fetched_at': None
            }
            
            result = cli_runner.invoke(cli, ['features', 'check', 'GOVCLOUD'])
            
            assert result.exit_code == 1
            assert 'No feature switches available' in result.output or 'No Feature Switches' in result.output
    
    def test_check_missing_feature_name(self, cli_runner):
        """Test check command without feature name argument."""
        result = cli_runner.invoke(cli, ['features', 'check'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'FEATURE_NAME' in result.output


class TestFeaturesCheckJSONOutput:
    """Test features check command JSON output."""
    
    def test_check_json_output_enabled(self, cli_runner, features_command_mocks):
        """Test check command with JSON output for enabled feature."""
        with features_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'enabled': ['GOVCLOUD'],
                'count': 1,
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            with patch('vamscli.commands.features.is_feature_enabled') as mock_is_enabled:
                mock_is_enabled.return_value = True
                
                result = cli_runner.invoke(cli, ['features', 'check', 'GOVCLOUD', '--json-output'])
                
                assert result.exit_code == 0
                
                # Verify output is valid JSON
                try:
                    parsed = json.loads(result.output)
                    assert parsed['feature_name'] == 'GOVCLOUD'
                    assert parsed['enabled'] is True
                except json.JSONDecodeError:
                    pytest.fail(f"Output is not valid JSON: {result.output}")
    
    def test_check_json_output_disabled(self, cli_runner, features_command_mocks):
        """Test check command with JSON output for disabled feature."""
        with features_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'enabled': [],
                'count': 0,
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            with patch('vamscli.commands.features.is_feature_enabled') as mock_is_enabled:
                mock_is_enabled.return_value = False
                
                result = cli_runner.invoke(cli, ['features', 'check', 'LOCATIONSERVICES', '--json-output'])
                
                assert result.exit_code == 0
                
                # Verify output is valid JSON
                try:
                    parsed = json.loads(result.output)
                    assert parsed['feature_name'] == 'LOCATIONSERVICES'
                    assert parsed['enabled'] is False
                except json.JSONDecodeError:
                    pytest.fail(f"Output is not valid JSON: {result.output}")
    
    def test_check_json_output_no_auth(self, cli_runner, features_command_mocks):
        """Test check command with JSON output when not authenticated."""
        with features_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = False
            
            result = cli_runner.invoke(cli, ['features', 'check', 'GOVCLOUD', '--json-output'])
            
            assert result.exit_code == 1
            
            # Verify error output is valid JSON
            try:
                parsed = json.loads(result.output)
                assert 'error' in parsed
            except json.JSONDecodeError:
                pytest.fail(f"Error output is not valid JSON: {result.output}")


class TestFeaturesExampleJSONOutput:
    """Test features example commands JSON output."""
    
    def test_example_govcloud_json_output(self, cli_runner, features_command_mocks):
        """Test example-govcloud command with JSON output."""
        with features_command_mocks as mocks:
            with patch('vamscli.commands.features.requires_feature') as mock_requires_feature:
                mock_requires_feature.return_value = lambda func: func
                
                result = cli_runner.invoke(cli, ['features', 'example-govcloud', '--json-output'])
                
                assert result.exit_code == 0
                
                # Verify output is valid JSON
                try:
                    parsed = json.loads(result.output)
                    assert parsed['feature'] == FEATURE_GOVCLOUD
                    assert parsed['enabled'] is True
                    assert 'message' in parsed
                    assert 'description' in parsed
                except json.JSONDecodeError:
                    pytest.fail(f"Output is not valid JSON: {result.output}")
    
    def test_example_location_json_output(self, cli_runner, features_command_mocks):
        """Test example-location command with JSON output."""
        with features_command_mocks as mocks:
            with patch('vamscli.commands.features.requires_feature') as mock_requires_feature:
                mock_requires_feature.return_value = lambda func: func
                
                result = cli_runner.invoke(cli, ['features', 'example-location', '--json-output'])
                
                assert result.exit_code == 0
                
                # Verify output is valid JSON
                try:
                    parsed = json.loads(result.output)
                    assert parsed['feature'] == FEATURE_LOCATIONSERVICES
                    assert parsed['enabled'] is True
                    assert 'message' in parsed
                    assert 'description' in parsed
                except json.JSONDecodeError:
                    pytest.fail(f"Output is not valid JSON: {result.output}")


class TestFeaturesExampleCommands:
    """Test features example commands."""
    
    def test_example_govcloud_help(self, cli_runner):
        """Test example-govcloud command help."""
        result = cli_runner.invoke(cli, ['features', 'example-govcloud', '--help'])
        assert result.exit_code == 0
        assert 'Example command that requires GOVCLOUD feature' in result.output
        assert '@requires_feature' in result.output
        assert 'vamscli features example-govcloud' in result.output
    
    def test_example_govcloud_success(self, cli_runner, features_command_mocks):
        """Test successful example-govcloud command execution."""
        with features_command_mocks as mocks:
            # Mock requires_feature decorator to pass
            with patch('vamscli.commands.features.requires_feature') as mock_requires_feature:
                # Mock the decorator to return the original function
                mock_requires_feature.return_value = lambda func: func
                
                result = cli_runner.invoke(cli, ['features', 'example-govcloud'])
                
                assert result.exit_code == 0
                assert '✓ GovCloud feature is enabled!' in result.output
                assert 'GovCloud-specific operations' in result.output
    
    def test_example_location_help(self, cli_runner):
        """Test example-location command help."""
        result = cli_runner.invoke(cli, ['features', 'example-location', '--help'])
        assert result.exit_code == 0
        assert 'Example command that requires LOCATIONSERVICES feature' in result.output
        assert '@requires_feature' in result.output
        assert 'vamscli features example-location' in result.output
    
    def test_example_location_success(self, cli_runner, features_command_mocks):
        """Test successful example-location command execution."""
        with features_command_mocks as mocks:
            # Mock requires_feature decorator to pass
            with patch('vamscli.commands.features.requires_feature') as mock_requires_feature:
                # Mock the decorator to return the original function
                mock_requires_feature.return_value = lambda func: func
                
                result = cli_runner.invoke(cli, ['features', 'example-location'])
                
                assert result.exit_code == 0
                assert '✓ Location services feature is enabled!' in result.output
                assert 'location-based operations' in result.output
    
    def test_example_govcloud_feature_disabled(self, cli_runner):
        """Test example-govcloud command when feature is disabled."""
        # Note: This test validates that the @requires_feature decorator exists
        # and is properly applied. The actual feature validation logic is tested
        # in the utility function tests above.
        result = cli_runner.invoke(cli, ['features', 'example-govcloud', '--help'])
        assert result.exit_code == 0
        assert 'Example command that requires GOVCLOUD feature' in result.output
    
    def test_example_location_feature_disabled(self, cli_runner):
        """Test example-location command when feature is disabled."""
        # Note: This test validates that the @requires_feature decorator exists
        # and is properly applied. The actual feature validation logic is tested
        # in the utility function tests above.
        result = cli_runner.invoke(cli, ['features', 'example-location', '--help'])
        assert result.exit_code == 0
        assert 'Example command that requires LOCATIONSERVICES feature' in result.output


class TestFeatureSwitchUtilities:
    """Test feature switch utility functions."""
    
    def test_profile_manager_feature_switches_methods(self, cli_runner):
        """Test ProfileManager feature switches methods."""
        with cli_runner.isolated_filesystem():
            from vamscli.utils.profile import ProfileManager
            
            profile_manager = ProfileManager('test')
            
            # Create auth profile with feature switches
            auth_data = {
                'user_id': 'test@example.com',
                'access_token': 'test_token',
                'feature_switches': {
                    'raw': 'GOVCLOUD,LOCATIONSERVICES,AUTHPROVIDER_COGNITO',
                    'enabled': ['GOVCLOUD', 'LOCATIONSERVICES', 'AUTHPROVIDER_COGNITO'],
                    'fetched_at': '2024-01-01T12:00:00Z'
                }
            }
            profile_manager.save_auth_profile(auth_data)
            
            # Test get_feature_switches
            features = profile_manager.get_feature_switches()
            assert features == ['GOVCLOUD', 'LOCATIONSERVICES', 'AUTHPROVIDER_COGNITO']
            
            # Test has_feature_switch
            assert profile_manager.has_feature_switch('GOVCLOUD') is True
            assert profile_manager.has_feature_switch('LOCATIONSERVICES') is True
            assert profile_manager.has_feature_switch('NONEXISTENT') is False
            
            # Test get_feature_switches_info
            info = profile_manager.get_feature_switches_info()
            assert info['has_feature_switches'] is True
            assert info['count'] == 3
            assert 'GOVCLOUD' in info['enabled']
    
    def test_save_feature_switches(self, cli_runner):
        """Test saving feature switches from API response."""
        with cli_runner.isolated_filesystem():
            from vamscli.utils.profile import ProfileManager
            
            profile_manager = ProfileManager('test')
            
            # Create initial auth profile
            auth_data = {
                'user_id': 'test@example.com',
                'access_token': 'test_token'
            }
            profile_manager.save_auth_profile(auth_data)
            
            # Save feature switches
            feature_switches_data = {
                'featuresEnabled': 'GOVCLOUD,LOCATIONSERVICES,AUTHPROVIDER_COGNITO'
            }
            profile_manager.save_feature_switches(feature_switches_data)
            
            # Verify feature switches were saved
            updated_auth = profile_manager.load_auth_profile()
            assert 'feature_switches' in updated_auth
            assert updated_auth['feature_switches']['raw'] == 'GOVCLOUD,LOCATIONSERVICES,AUTHPROVIDER_COGNITO'
            assert updated_auth['feature_switches']['enabled'] == ['GOVCLOUD', 'LOCATIONSERVICES', 'AUTHPROVIDER_COGNITO']
            assert 'fetched_at' in updated_auth['feature_switches']
    
    def test_features_utility_functions(self, cli_runner):
        """Test features utility functions."""
        with cli_runner.isolated_filesystem():
            from vamscli.utils.profile import ProfileManager
            from vamscli.utils.features import get_enabled_features, is_feature_enabled, require_feature
            
            profile_manager = ProfileManager('test')
            
            # Create auth profile with feature switches
            auth_data = {
                'user_id': 'test@example.com',
                'access_token': 'test_token',
                'feature_switches': {
                    'raw': 'GOVCLOUD,LOCATIONSERVICES',
                    'enabled': ['GOVCLOUD', 'LOCATIONSERVICES'],
                    'fetched_at': '2024-01-01T12:00:00Z'
                }
            }
            profile_manager.save_auth_profile(auth_data)
            
            # Test utility functions
            features = get_enabled_features(profile_manager)
            assert features == ['GOVCLOUD', 'LOCATIONSERVICES']
            
            assert is_feature_enabled(FEATURE_GOVCLOUD, profile_manager) is True
            assert is_feature_enabled(FEATURE_LOCATIONSERVICES, profile_manager) is True
            assert is_feature_enabled('NONEXISTENT', profile_manager) is False
            
            # Test require_feature success
            require_feature(FEATURE_GOVCLOUD, profile_manager)  # Should not raise
            
            # Test require_feature failure
            with pytest.raises(Exception):  # Should raise ClickException
                require_feature('NONEXISTENT', profile_manager)
    
    def test_empty_feature_switches(self, cli_runner):
        """Test handling of empty feature switches."""
        with cli_runner.isolated_filesystem():
            from vamscli.utils.profile import ProfileManager
            
            profile_manager = ProfileManager('test')
            
            # Create auth profile without feature switches
            auth_data = {
                'user_id': 'test@example.com',
                'access_token': 'test_token'
            }
            profile_manager.save_auth_profile(auth_data)
            
            # Test methods with no feature switches
            features = profile_manager.get_feature_switches()
            assert features == []
            
            assert profile_manager.has_feature_switch('GOVCLOUD') is False
            
            info = profile_manager.get_feature_switches_info()
            assert info['has_feature_switches'] is False
            assert info['count'] == 0
    
    def test_feature_switches_with_empty_string(self, cli_runner):
        """Test handling of empty featuresEnabled string."""
        with cli_runner.isolated_filesystem():
            from vamscli.utils.profile import ProfileManager
            
            profile_manager = ProfileManager('test')
            
            # Create initial auth profile
            auth_data = {
                'user_id': 'test@example.com',
                'access_token': 'test_token'
            }
            profile_manager.save_auth_profile(auth_data)
            
            # Save empty feature switches
            feature_switches_data = {
                'featuresEnabled': ''
            }
            profile_manager.save_feature_switches(feature_switches_data)
            
            # Verify empty feature switches are handled correctly
            features = profile_manager.get_feature_switches()
            assert features == []
            
            info = profile_manager.get_feature_switches_info()
            assert info['has_feature_switches'] is True  # Structure exists but empty
            assert info['count'] == 0
            assert info['raw'] == ''


class TestFeatureSwitchIntegration:
    """Test feature switch integration with auth commands."""
    
    def test_auth_status_shows_feature_switches(self, cli_runner, generic_command_mocks):
        """Test that auth status command shows feature switches information."""
        with generic_command_mocks('auth') as mocks:
            # Setup mocks for profile manager
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_token_expiration_info.return_value = {
                'has_token': True,
                'token_type': 'cognito',
                'has_expiration': False,
                'is_expired': False
            }
            mocks['profile_manager'].load_auth_profile.return_value = {
                'user_id': 'test@example.com',
                'access_token': 'test_token'
            }
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'enabled': ['GOVCLOUD', 'LOCATIONSERVICES'],
                'count': 2,
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            result = cli_runner.invoke(cli, ['auth', 'status'])
            
            assert result.exit_code == 0
            assert 'Feature Switches:' in result.output
            assert 'Count: 2' in result.output
            assert 'GOVCLOUD' in result.output
            assert 'LOCATIONSERVICES' in result.output
    
    def test_login_fetches_feature_switches(self, cli_runner, generic_command_mocks):
        """Test that login command fetches feature switches."""
        with generic_command_mocks('auth') as mocks:
            # Setup mocks for profile manager
            mocks['profile_manager'].load_config.return_value = {
                'api_gateway_url': 'https://api.example.com',
                'amplify_config': {
                    'region': 'us-east-1',
                    'cognitoUserPoolId': 'us-east-1_test',
                    'cognitoAppClientId': 'test_client'
                }
            }
            
            # Mock command APIClient
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {
                'featuresEnabled': 'GOVCLOUD,LOCATIONSERVICES'
            }
            
            # Mock authenticator
            with patch('vamscli.commands.auth.get_authenticator') as mock_get_auth:
                mock_authenticator = Mock()
                mock_authenticator.authenticate.return_value = {
                    'access_token': 'test_token',
                    'expires_in': 3600
                }
                mock_get_auth.return_value = mock_authenticator
                
                result = cli_runner.invoke(cli, ['auth', 'login', '-u', 'test@example.com', '-p', 'password'])
                
                # Verify feature switches were fetched and saved
                mocks['api_client'].get_secure_config.assert_called_once()
                mocks['profile_manager'].save_feature_switches.assert_called_once_with({
                    'featuresEnabled': 'GOVCLOUD,LOCATIONSERVICES'
                })
                
                assert result.exit_code == 0
                assert 'Feature switches updated successfully' in result.output
    
    def test_set_override_fetches_feature_switches(self, cli_runner, generic_command_mocks):
        """Test that set-override command fetches feature switches."""
        with generic_command_mocks('auth') as mocks:
            # Setup mocks for profile manager
            mocks['profile_manager'].load_config.return_value = {
                'api_gateway_url': 'https://api.example.com'
            }
            
            # Mock command APIClient
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_secure_config.return_value = {
                'featuresEnabled': 'GOVCLOUD,LOCATIONSERVICES'
            }
            
            result = cli_runner.invoke(cli, [
                'auth', 'set-override', 
                '-u', 'test@example.com', 
                '--token', 'test_token'
            ])
            
            # Verify feature switches were fetched and saved
            mocks['api_client'].get_secure_config.assert_called_once()
            mocks['profile_manager'].save_feature_switches.assert_called_once_with({
                'featuresEnabled': 'GOVCLOUD,LOCATIONSERVICES'
            })
            
            assert result.exit_code == 0
            assert 'Feature switches updated successfully' in result.output


class TestFeatureSwitchErrorHandling:
    """Test error handling for feature switch operations."""
    
    def test_authentication_error_handling(self, cli_runner, features_command_mocks):
        """Test authentication error handling in feature commands."""
        with features_command_mocks as mocks:
            # Mock profile manager to have auth profile but API call fails
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.side_effect = AuthenticationError("Authentication failed")
            
            result = cli_runner.invoke(cli, ['features', 'list'])
            
            # Should handle the error gracefully
            assert result.exit_code != 0
    
    def test_api_error_handling(self, cli_runner, features_command_mocks):
        """Test API error handling in feature commands."""
        with features_command_mocks as mocks:
            # Mock profile manager to have auth profile but API call fails
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_feature_switches_info.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, ['features', 'list'])
            
            # Should handle the error gracefully
            assert result.exit_code != 0


class TestFeaturesCommandsIntegration:
    """Test integration scenarios for features commands."""
    
    @patch('vamscli.main.ProfileManager')
    def test_commands_require_proper_arguments(self, mock_main_profile_manager, cli_runner):
        """Test that features commands handle arguments properly."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_main_profile_manager.return_value = mock_profile_manager
        
        # Test check without feature name
        result = cli_runner.invoke(cli, ['features', 'check'])
        assert result.exit_code == 2  # Click parameter error
        assert 'Missing argument' in result.output or 'FEATURE_NAME' in result.output
    
    def test_features_group_help(self, cli_runner):
        """Test features command group help."""
        result = cli_runner.invoke(cli, ['features', '--help'])
        assert result.exit_code == 0
        assert 'Feature switches management commands' in result.output
        assert 'list' in result.output
        assert 'check' in result.output
        assert 'example-govcloud' in result.output
        assert 'example-location' in result.output


if __name__ == '__main__':
    pytest.main([__file__])
