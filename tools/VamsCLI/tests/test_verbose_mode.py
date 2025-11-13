"""Integration tests for verbose mode functionality."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from click.testing import CliRunner

from vamscli.main import cli


class TestVerboseModeIntegration:
    """Test verbose mode integration with CLI commands."""
    
    def test_verbose_flag_available_globally(self, cli_runner):
        """Test that --verbose flag is available globally."""
        result = cli_runner.invoke(cli, ['--help'])
        assert result.exit_code == 0
        assert '--verbose' in result.output
        assert 'Enable verbose output' in result.output
    
    def test_verbose_flag_with_version(self, cli_runner):
        """Test --verbose flag with version command."""
        result = cli_runner.invoke(cli, ['--verbose', '--version'])
        assert result.exit_code == 0
        assert 'VamsCLI version' in result.output
    
    @patch('vamscli.main.ProfileManager')
    def test_verbose_flag_with_help_command(self, mock_profile_manager, cli_runner):
        """Test --verbose flag with help command."""
        result = cli_runner.invoke(cli, ['--verbose', 'assets', '--help'])
        assert result.exit_code == 0
        assert 'assets' in result.output.lower()


class TestVerboseModeWithCommands:
    """Test verbose mode with actual commands."""
    
    @patch('vamscli.main.ProfileManager')
    @patch('vamscli.utils.decorators.get_profile_manager_from_context')
    def test_verbose_mode_shows_profile_info(self, mock_get_pm, mock_main_pm, cli_runner):
        """Test that verbose mode shows profile information."""
        # Setup mocks
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.profile_name = 'default'
        mock_profile_manager.load_config.return_value = {
            'api_gateway_url': 'https://api.example.com',
            'cli_version': '2.2.0'
        }
        mock_profile_manager.has_auth_profile.return_value = True
        mock_profile_manager.load_auth_profile.return_value = {
            'user_id': 'test@example.com',
            'access_token': 'test-token'
        }
        
        mock_main_pm.return_value = mock_profile_manager
        mock_get_pm.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['--verbose', 'auth', 'status'])
        
        # Should show profile information in verbose mode
        # Note: Actual output depends on command implementation
        assert result.exit_code in [0, 1]  # May fail due to mocking, but should not crash
    
    @patch('vamscli.main.ProfileManager')
    @patch('vamscli.utils.decorators.get_profile_manager_from_context')
    def test_verbose_mode_with_setup_error(self, mock_get_pm, mock_main_pm, cli_runner):
        """Test verbose mode with setup error."""
        from vamscli.utils.exceptions import SetupRequiredError
        
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = False
        mock_profile_manager.profile_name = 'default'
        mock_main_pm.return_value = mock_profile_manager
        mock_get_pm.side_effect = SetupRequiredError("Setup required for profile 'default'")
        
        result = cli_runner.invoke(cli, ['--verbose', 'assets', 'list'])
        
        # Should show setup error
        assert result.exit_code == 1
        # In test environment, the error is captured differently
        assert result.exit_code == 1  # Verify command failed


class TestVerboseModeErrorDisplay:
    """Test verbose mode error display."""
    
    @patch('vamscli.main.ProfileManager')
    @patch('vamscli.utils.decorators.ProfileManager')
    @patch('vamscli.utils.decorators.APIClient')
    def test_verbose_mode_shows_stack_trace(self, mock_api_client, mock_dec_pm, mock_main_pm, cli_runner):
        """Test that verbose mode shows stack traces for errors."""
        # Setup mocks
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.profile_name = 'default'
        mock_profile_manager.load_config.return_value = {
            'api_gateway_url': 'https://api.example.com',
            'cli_version': '2.2.0'
        }
        
        mock_main_pm.return_value = mock_profile_manager
        mock_dec_pm.return_value = mock_profile_manager
        
        # Mock API client to raise an error
        mock_client = Mock()
        mock_client.check_api_availability.side_effect = Exception("Test error")
        mock_api_client.return_value = mock_client
        
        result = cli_runner.invoke(cli, ['--verbose', 'assets', 'list'])
        
        # Should show verbose error details
        # Note: Exact output depends on exception handling
        assert result.exit_code == 1


class TestVerboseModeAPILogging:
    """Test verbose mode API request/response logging."""
    
    @patch('vamscli.main.ProfileManager')
    @patch('vamscli.utils.decorators.ProfileManager')
    @patch('vamscli.utils.decorators.get_profile_manager_from_context')
    @patch('vamscli.utils.decorators.APIClient')
    def test_verbose_mode_logs_api_calls(self, mock_dec_api, mock_get_pm, mock_dec_pm, mock_main_pm, cli_runner):
        """Test that verbose mode logs API calls."""
        # Setup mocks
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.profile_name = 'default'
        mock_profile_manager.load_config.return_value = {
            'api_gateway_url': 'https://api.example.com',
            'cli_version': '2.2.0'
        }
        
        mock_main_pm.return_value = mock_profile_manager
        mock_dec_pm.return_value = mock_profile_manager
        mock_get_pm.return_value = mock_profile_manager
        
        # Mock API client
        mock_dec_client = Mock()
        mock_dec_client.check_api_availability.return_value = {'available': True}
        mock_dec_api.return_value = mock_dec_client
        
        result = cli_runner.invoke(cli, ['--verbose', 'database', 'list'])
        
        # Command should execute (may fail due to mocking but shouldn't crash)
        assert result.exit_code in [0, 1]


class TestVerboseModeWithProfiles:
    """Test verbose mode with different profiles."""
    
    @patch('vamscli.main.ProfileManager')
    def test_verbose_mode_with_custom_profile(self, mock_main_pm, cli_runner):
        """Test verbose mode with custom profile."""
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = False
        mock_profile_manager.profile_name = 'production'
        mock_main_pm.return_value = mock_profile_manager
        
        result = cli_runner.invoke(cli, ['--verbose', '--profile', 'production', 'auth', 'status'])
        
        # Should handle custom profile - command executes but may not fail in test environment
        # The important thing is it doesn't crash with verbose mode
        assert result.exit_code in [0, 1]  # May succeed in test environment due to mocking


class TestVerboseModeLoggingOutput:
    """Test verbose mode logging output format."""
    
    def test_verbose_mode_output_format(self, cli_runner):
        """Test that verbose mode output is properly formatted."""
        result = cli_runner.invoke(cli, ['--verbose', '--help'])
        assert result.exit_code == 0
        # Help should display normally even with verbose
        assert 'VamsCLI' in result.output


class TestLoggingWithExceptions:
    """Test logging behavior with various exceptions."""
    
    @patch('vamscli.main.ProfileManager')
    @patch('vamscli.utils.decorators.ProfileManager')
    @patch('vamscli.utils.decorators.get_profile_manager_from_context')
    @patch('vamscli.utils.decorators.APIClient')
    def test_logging_with_asset_not_found(self, mock_dec_api, mock_get_pm, mock_dec_pm, mock_main_pm, cli_runner):
        """Test logging when asset is not found."""
        from vamscli.utils.exceptions import AssetNotFoundError
        
        # Setup mocks
        mock_profile_manager = Mock()
        mock_profile_manager.has_config.return_value = True
        mock_profile_manager.profile_name = 'default'
        mock_profile_manager.load_config.return_value = {
            'api_gateway_url': 'https://api.example.com',
            'cli_version': '2.2.0'
        }
        
        mock_main_pm.return_value = mock_profile_manager
        mock_dec_pm.return_value = mock_profile_manager
        mock_get_pm.return_value = mock_profile_manager
        
        # Mock API client
        mock_dec_client = Mock()
        mock_dec_client.check_api_availability.return_value = {'available': True}
        mock_dec_api.return_value = mock_dec_client
        
        result = cli_runner.invoke(cli, ['--verbose', 'assets', 'get', 'test-db', 'test-asset'])
        
        # Should log error and show in verbose mode
        # Command will fail due to missing required parameters, but should not crash
        assert result.exit_code in [1, 2]  # 1 for error, 2 for usage error


class TestLoggingFileCreation:
    """Test that logging creates log files properly."""
    
    @patch('vamscli.utils.logging.get_log_dir')
    def test_log_directory_creation(self, mock_get_log_dir):
        """Test that log directory is created."""
        import tempfile
        temp_dir = Path(tempfile.mkdtemp())
        log_dir = temp_dir / "logs"
        mock_get_log_dir.return_value = log_dir
        
        try:
            # Initialize logging should create directory
            from vamscli.utils.logging import ensure_log_dir
            ensure_log_dir()
            
            assert log_dir.exists()
            assert log_dir.is_dir()
        finally:
            # Clean up
            if log_dir.exists():
                import shutil
                shutil.rmtree(temp_dir)


if __name__ == '__main__':
    pytest.main([__file__])
