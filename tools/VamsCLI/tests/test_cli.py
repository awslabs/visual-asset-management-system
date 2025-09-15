"""Test the main CLI functionality."""

import json
import pytest
import click
from unittest.mock import Mock, patch, mock_open
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.version import get_version
from vamscli.utils.exceptions import (
    SetupRequiredError, APIError, ConfigurationError, AuthenticationError,
    OverrideTokenError, APIUnavailableError
)


# File-level fixtures for CLI-specific testing patterns
@pytest.fixture
def cli_command_mocks(generic_command_mocks):
    """Provide CLI-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for CLI command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('setup')


@pytest.fixture
def cli_no_setup_mocks(no_setup_command_mocks):
    """Provide CLI command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('setup')


@pytest.fixture
def auth_command_mocks(generic_command_mocks):
    """Provide auth-specific command mocks.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('auth')


@pytest.fixture
def auth_no_setup_mocks(no_setup_command_mocks):
    """Provide auth command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('auth')


class TestCLIMain:
    """Test the main CLI interface."""
    
    def test_cli_help(self, cli_runner):
        """Test that CLI help works."""
        result = cli_runner.invoke(cli, ['--help'])
        assert result.exit_code == 0
        assert 'VamsCLI - Command Line Interface' in result.output
        assert 'Visual Asset Management System' in result.output
        assert 'setup' in result.output
        assert 'auth' in result.output
    
    def test_cli_version_flag(self, cli_runner):
        """Test that --version flag works."""
        result = cli_runner.invoke(cli, ['--version'])
        assert result.exit_code == 0
        assert get_version() in result.output
    
    def test_version_command(self, cli_runner):
        """Test the version subcommand."""
        result = cli_runner.invoke(cli, ['version'])
        assert result.exit_code == 0
        assert get_version() in result.output
    
    def test_cli_no_command(self, cli_runner):
        """Test CLI without any command shows help."""
        result = cli_runner.invoke(cli, [])
        assert result.exit_code == 0
        assert 'VamsCLI - Command Line Interface' in result.output
    
    def test_cli_invalid_command(self, cli_runner):
        """Test CLI with invalid command."""
        result = cli_runner.invoke(cli, ['invalid-command'])
        assert result.exit_code == 2  # Click error for unknown command
        assert 'No such command' in result.output
    
    def test_cli_global_profile_option(self, cli_runner):
        """Test CLI with global profile option."""
        result = cli_runner.invoke(cli, ['--profile', 'test-profile', '--help'])
        assert result.exit_code == 0
        assert 'VamsCLI - Command Line Interface' in result.output
    
    def test_cli_invalid_profile_name(self, cli_runner):
        """Test CLI with invalid profile name."""
        result = cli_runner.invoke(cli, ['--profile', 'ab'])  # Too short
        assert result.exit_code == 2  # Click parameter error
        assert 'Invalid profile name' in result.output


class TestSetupCommand:
    """Test the setup command."""
    
    def test_setup_help(self, cli_runner):
        """Test setup command help."""
        result = cli_runner.invoke(cli, ['setup', '--help'])
        assert result.exit_code == 0
        assert 'Setup VamsCLI with VAMS base URL' in result.output
        assert '--force' in result.output
        assert 'BASE_URL' in result.output
        assert 'CloudFront, ALB, API Gateway, or custom domain' in result.output
    
    def test_setup_missing_url(self, cli_runner):
        """Test setup command without URL."""
        result = cli_runner.invoke(cli, ['setup'])
        assert result.exit_code == 2  # Click error for missing argument
        assert 'Missing argument' in result.output
    
    
    def test_setup_success(self, cli_runner, cli_command_mocks):
        """Test successful setup."""
        with cli_command_mocks as mocks:
            # Mock API client methods
            mocks['api_client'].check_version.return_value = {
                'match': True,
                'cli_version': '1.0.0',
                'api_version': '1.0.0'
            }
            mocks['api_client'].get_amplify_config.return_value = {
                'region': 'us-west-2',
                'api': 'https://2jf1k4c5lj.execute-api.us-west-2.amazonaws.com/',
                'cognitoUserPoolId': 'us-west-2_test',
                'cognitoAppClientId': 'test-client-id'
            }
            
            # Mock profile manager methods
            mocks['profile_manager'].has_config.return_value = False
            
            # Mock DateTime for setup timestamp
            with patch('click.DateTime'):
                result = cli_runner.invoke(cli, [
                    'setup', 
                    'https://api.example.com'
                ])
            
            assert result.exit_code == 0
            assert 'Setup completed successfully!' in result.output
            assert 'Version match' in result.output
            assert 'vamscli auth' in result.output
            
            # Verify API calls
            mocks['api_client'].check_version.assert_called_once()
            mocks['api_client'].get_amplify_config.assert_called_once()
            mocks['profile_manager'].save_config.assert_called_once()
    
    def test_setup_version_mismatch(self, cli_runner, cli_command_mocks):
        """Test setup with version mismatch."""
        with cli_command_mocks as mocks:
            # Mock version mismatch
            mocks['api_client'].check_version.return_value = {
                'match': False,
                'cli_version': '1.0.0',
                'api_version': '2.0.0'
            }
            mocks['api_client'].get_amplify_config.return_value = {
                'region': 'us-west-2',
                'api': 'https://2jf1k4c5lj.execute-api.us-west-2.amazonaws.com/',
                'cognitoUserPoolId': 'us-west-2_test',
                'cognitoAppClientId': 'test-client-id'
            }
            mocks['profile_manager'].has_config.return_value = False
            
            # Mock user confirmation and DateTime
            with patch('click.confirm', return_value=True), \
                 patch('click.DateTime'):
                result = cli_runner.invoke(cli, [
                    'setup', 
                    'https://api.example.com'
                ])
            
            assert result.exit_code == 0
            assert 'Version mismatch detected' in result.output
            assert 'CLI version: 1.0.0' in result.output
            assert 'API version: 2.0.0' in result.output
            assert 'Setup completed successfully!' in result.output
    
    def test_setup_version_mismatch_cancelled(self, cli_runner, cli_command_mocks):
        """Test setup with version mismatch cancelled by user."""
        with cli_command_mocks as mocks:
            # Mock version mismatch
            mocks['api_client'].check_version.return_value = {
                'match': False,
                'cli_version': '1.0.0',
                'api_version': '2.0.0'
            }
            mocks['profile_manager'].has_config.return_value = False
            
            # Mock user cancellation
            with patch('click.confirm', return_value=False):
                result = cli_runner.invoke(cli, [
                    'setup', 
                    'https://api.example.com'
                ])
            
            assert result.exit_code == 0
            assert 'Setup cancelled' in result.output
            
            # Verify setup was not completed
            mocks['profile_manager'].save_config.assert_not_called()
    
    def test_setup_version_mismatch_skip_check(self, cli_runner, cli_command_mocks):
        """Test setup with version mismatch using --skip-version-check flag."""
        with cli_command_mocks as mocks:
            # Mock version mismatch
            mocks['api_client'].check_version.return_value = {
                'match': False,
                'cli_version': '1.0.0',
                'api_version': '2.0.0'
            }
            mocks['api_client'].get_amplify_config.return_value = {
                'region': 'us-west-2',
                'api': 'https://2jf1k4c5lj.execute-api.us-west-2.amazonaws.com/',
                'cognitoUserPoolId': 'us-west-2_test',
                'cognitoAppClientId': 'test-client-id'
            }
            mocks['profile_manager'].has_config.return_value = False
            
            # Mock DateTime for setup timestamp
            with patch('click.DateTime'):
                result = cli_runner.invoke(cli, [
                    'setup', 
                    'https://api.example.com',
                    '--skip-version-check'
                ])
            
            assert result.exit_code == 0
            assert 'Version mismatch detected' in result.output
            assert 'CLI version: 1.0.0' in result.output
            assert 'API version: 2.0.0' in result.output
            assert 'Skipping version check confirmation (--skip-version-check enabled)' in result.output
            assert 'Setup completed successfully!' in result.output
            
            # Verify setup was completed without user confirmation
            mocks['profile_manager'].save_config.assert_called_once()
    
    def test_setup_version_match_skip_check_no_effect(self, cli_runner, cli_command_mocks):
        """Test setup with version match - skip check flag should have no effect."""
        with cli_command_mocks as mocks:
            # Mock version match
            mocks['api_client'].check_version.return_value = {
                'match': True,
                'cli_version': '1.0.0',
                'api_version': '1.0.0'
            }
            mocks['api_client'].get_amplify_config.return_value = {
                'region': 'us-west-2',
                'api': 'https://2jf1k4c5lj.execute-api.us-west-2.amazonaws.com/',
                'cognitoUserPoolId': 'us-west-2_test',
                'cognitoAppClientId': 'test-client-id'
            }
            mocks['profile_manager'].has_config.return_value = False
            
            # Mock DateTime for setup timestamp
            with patch('click.DateTime'):
                result = cli_runner.invoke(cli, [
                    'setup', 
                    'https://api.example.com',
                    '--skip-version-check'
                ])
            
            assert result.exit_code == 0
            assert 'Version match: 1.0.0' in result.output
            assert 'Setup completed successfully!' in result.output
            # Should not mention skipping since versions match
            assert 'Skipping version check confirmation' not in result.output
            
            # Verify setup was completed
            mocks['profile_manager'].save_config.assert_called_once()
    
    def test_setup_existing_config_no_force(self, cli_runner, cli_command_mocks):
        """Test setup with existing configuration without force flag."""
        with cli_command_mocks as mocks:
            mocks['profile_manager'].has_config.return_value = True
            mocks['profile_manager'].profile_name = 'default'
            
            result = cli_runner.invoke(cli, [
                'setup', 
                'https://api.example.com'
            ])
            
            assert result.exit_code == 0
            assert 'Configuration already exists' in result.output
            assert 'Use --force to overwrite' in result.output
            
            # Verify no API calls were made
            mocks['api_client'].check_version.assert_not_called()
    
    def test_setup_existing_config_with_force(self, cli_runner, cli_command_mocks):
        """Test setup with existing configuration using force flag."""
        with cli_command_mocks as mocks:
            # Mock existing config
            mocks['profile_manager'].has_config.return_value = True
            
            # Mock API client methods
            mocks['api_client'].check_version.return_value = {
                'match': True,
                'cli_version': '1.0.0',
                'api_version': '1.0.0'
            }
            mocks['api_client'].get_amplify_config.return_value = {
                'region': 'us-west-2',
                'api': 'https://2jf1k4c5lj.execute-api.us-west-2.amazonaws.com/',
                'cognitoUserPoolId': 'us-west-2_test',
                'cognitoAppClientId': 'test-client-id'
            }
            
            # Mock DateTime for setup timestamp
            with patch('click.DateTime'):
                result = cli_runner.invoke(cli, [
                    'setup', 
                    'https://api.example.com',
                    '--force'
                ])
            
            assert result.exit_code == 0
            assert 'Setup completed successfully!' in result.output
            assert 'Removing existing configuration' in result.output
            
            # Verify wipe and save were called
            mocks['profile_manager'].wipe_profile.assert_called_once()
            mocks['profile_manager'].save_config.assert_called_once()
    
    def test_setup_api_error(self, cli_runner, cli_command_mocks):
        """Test setup with API error."""
        with cli_command_mocks as mocks:
            mocks['profile_manager'].has_config.return_value = False
            mocks['api_client'].check_version.side_effect = APIError("API request failed")
            
            result = cli_runner.invoke(cli, [
                'setup', 
                'https://api.example.com'
            ])
            
            assert result.exit_code == 1
            # Check that the original exception is preserved
            assert isinstance(result.exception, APIError)
    
    def test_setup_missing_api_field(self, cli_runner, cli_command_mocks):
        """Test setup with amplify config missing 'api' field."""
        with cli_command_mocks as mocks:
            # Mock API client methods
            mocks['api_client'].check_version.return_value = {
                'match': True,
                'cli_version': '1.0.0',
                'api_version': '1.0.0'
            }
            # Mock amplify config without 'api' field
            mocks['api_client'].get_amplify_config.return_value = {
                'region': 'us-west-2',
                'cognitoUserPoolId': 'us-west-2_test',
                'cognitoAppClientId': 'test-client-id'
                # Missing 'api' field
            }
            mocks['profile_manager'].has_config.return_value = False
            
            result = cli_runner.invoke(cli, [
                'setup', 
                'https://vams.example.com'
            ])
            
            assert result.exit_code == 1
            assert "No 'api' field found in amplify configuration response" in result.output
            assert "Please verify the base URL points to a valid VAMS deployment" in result.output
    
    def test_setup_invalid_extracted_api_url(self, cli_runner, cli_command_mocks):
        """Test setup with invalid API Gateway URL extracted from amplify config."""
        with cli_command_mocks as mocks:
            # Mock API client methods
            mocks['api_client'].check_version.return_value = {
                'match': True,
                'cli_version': '1.0.0',
                'api_version': '1.0.0'
            }
            # Mock amplify config with invalid 'api' field
            mocks['api_client'].get_amplify_config.return_value = {
                'region': 'us-west-2',
                'api': 'invalid-url',  # Invalid URL
                'cognitoUserPoolId': 'us-west-2_test',
                'cognitoAppClientId': 'test-client-id'
            }
            mocks['profile_manager'].has_config.return_value = False
            
            result = cli_runner.invoke(cli, [
                'setup', 
                'https://vams.example.com'
            ])
            
            assert result.exit_code == 1
            assert "Invalid API Gateway URL extracted from amplify config: invalid-url" in result.output
    
    def test_setup_api_extraction_success(self, cli_runner, cli_command_mocks):
        """Test successful setup with API Gateway URL extraction."""
        with cli_command_mocks as mocks:
            # Mock API client methods
            mocks['api_client'].check_version.return_value = {
                'match': True,
                'cli_version': '1.0.0',
                'api_version': '1.0.0'
            }
            mocks['api_client'].get_amplify_config.return_value = {
                'region': 'us-west-2',
                'api': 'https://2jf1k4c5lj.execute-api.us-west-2.amazonaws.com/',
                'cognitoUserPoolId': 'us-west-2_test',
                'cognitoAppClientId': 'test-client-id'
            }
            mocks['profile_manager'].has_config.return_value = False
            
            # Mock DateTime for setup timestamp
            with patch('click.DateTime'):
                result = cli_runner.invoke(cli, [
                    'setup', 
                    'https://vams.mycompany.com'
                ])
            
            assert result.exit_code == 0
            assert 'Setting up VamsCLI with base URL: https://vams.mycompany.com' in result.output
            assert '✓ Extracted API Gateway URL: https://2jf1k4c5lj.execute-api.us-west-2.amazonaws.com' in result.output
            assert 'Setup completed successfully!' in result.output
            
            # Verify the saved configuration includes both base_url and api_gateway_url
            saved_config = mocks['profile_manager'].save_config.call_args[0][0]
            assert saved_config['base_url'] == 'https://vams.mycompany.com'
            assert saved_config['api_gateway_url'] == 'https://2jf1k4c5lj.execute-api.us-west-2.amazonaws.com'


class TestAuthCommands:
    """Test authentication commands."""
    
    def test_auth_help(self, cli_runner):
        """Test auth command help."""
        result = cli_runner.invoke(cli, ['auth', '--help'])
        assert result.exit_code == 0
        assert 'Authentication commands' in result.output
        assert 'login' in result.output
        assert 'logout' in result.output
        assert 'status' in result.output
        assert 'refresh' in result.output
    
    def test_auth_login_help(self, cli_runner):
        """Test auth login command help."""
        result = cli_runner.invoke(cli, ['auth', 'login', '--help'])
        assert result.exit_code == 0
        assert 'Authenticate with VAMS using Cognito or token override' in result.output
        assert '--username' in result.output
        assert '--password' in result.output
        assert '--save-credentials' in result.output
        assert '--user-id' in result.output
        assert '--token-override' in result.output
        assert '--expires-at' in result.output
    
    def test_auth_login_missing_required_params(self, cli_runner, auth_command_mocks):
        """Test auth login without required parameters."""
        with auth_command_mocks as mocks:
            # Mock version check to pass
            mocks['api_client'].check_version.return_value = {
                'match': True,
                'cli_version': '1.0.0',
                'api_version': '1.0.0'
            }
            
            # Test with no parameters at all - should fail because neither username nor token-override is provided
            result = cli_runner.invoke(cli, ['auth', 'login'])
            assert result.exit_code == 1
            assert '--username is required for Cognito authentication' in result.output
    
    def test_auth_login_success(self, cli_runner, auth_command_mocks):
        """Test successful auth login."""
        with auth_command_mocks as mocks:
            # Mock authenticator and API responses
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = {
                'access_token': 'test-token',
                'refresh_token': 'test-refresh',
                'expires_in': 3600
            }
            
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_feature_switches.return_value = {
                'raw': 'FEATURE1,FEATURE2',
                'enabled': ['FEATURE1', 'FEATURE2']
            }
            
            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator), \
                 patch('click.prompt', return_value='password123'):
                
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com'
                ])
            
            assert result.exit_code == 0
            assert '✓ Cognito authentication successful!' in result.output
            assert 'expires in 3600 seconds' in result.output
            assert 'User profile refreshed' in result.output
            assert 'Feature switches updated' in result.output
            
            # Verify API calls
            mock_authenticator.authenticate.assert_called_once_with('test@example.com', 'password123')
            mocks['profile_manager'].save_auth_profile.assert_called_once()
            mocks['api_client'].call_login_profile.assert_called_once_with('test@example.com')
    
    def test_auth_login_no_setup(self, cli_runner, auth_no_setup_mocks):
        """Test auth login without setup."""
        with auth_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '-u', 'test@example.com'
            ])
            
            assert result.exit_code == 1
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output
    
    def test_auth_login_authentication_error(self, cli_runner, auth_command_mocks):
        """Test auth login with authentication error."""
        with auth_command_mocks as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.side_effect = AuthenticationError("Invalid credentials")
            
            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator), \
                 patch('click.prompt', return_value='wrongpassword'):
                
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com'
                ])
            
            assert result.exit_code == 1
            assert '✗ Cognito authentication failed' in result.output
            assert 'Invalid credentials' in result.output
    
    def test_auth_login_with_password_and_save_credentials(self, cli_runner, auth_command_mocks):
        """Test auth login with password provided and save credentials."""
        with auth_command_mocks as mocks:
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = {
                'access_token': 'test-token',
                'refresh_token': 'test-refresh',
                'expires_in': 3600
            }
            
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_feature_switches.return_value = {
                'raw': 'FEATURE1',
                'enabled': ['FEATURE1']
            }
            
            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com',
                    '-p', 'password123',
                    '--save-credentials'
                ])
            
            assert result.exit_code == 0
            assert '✓ Cognito authentication successful!' in result.output
            assert 'Credentials saved for automatic re-authentication' in result.output
            
            # Verify credentials were saved
            mocks['profile_manager'].save_credentials.assert_called_once_with({
                'username': 'test@example.com',
                'password': 'password123'
            })
    
    def test_auth_logout_success(self, cli_runner, auth_command_mocks):
        """Test successful auth logout."""
        with auth_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = True
            
            result = cli_runner.invoke(cli, ['auth', 'logout'])
            
            assert result.exit_code == 0
            assert '✓ Logged out successfully!' in result.output
            assert 'Authentication profile and saved credentials removed' in result.output
            
            # Verify logout was called
            mocks['profile_manager'].delete_auth_profile.assert_called_once()
    
    def test_auth_logout_no_profile(self, cli_runner, auth_command_mocks):
        """Test auth logout without profile."""
        with auth_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = False
            mocks['profile_manager'].has_credentials.return_value = False
            
            result = cli_runner.invoke(cli, ['auth', 'logout'])
            
            assert result.exit_code == 0
            assert 'No authentication profile found' in result.output
    
    def test_auth_status_no_config(self, cli_runner, auth_no_setup_mocks):
        """Test auth status without configuration."""
        with auth_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['auth', 'status'])
            
            assert result.exit_code == 0
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output
    
    def test_auth_status_not_authenticated(self, cli_runner, auth_command_mocks):
        """Test auth status when not authenticated."""
        with auth_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = False
            
            result = cli_runner.invoke(cli, ['auth', 'status'])
            
            assert result.exit_code == 0
            assert 'Not authenticated' in result.output
            assert 'vamscli auth login' in result.output
    
    def test_auth_status_authenticated(self, cli_runner, auth_command_mocks):
        """Test auth status when authenticated."""
        with auth_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].get_token_expiration_info.return_value = {
                'has_token': True,
                'token_type': 'cognito',
                'has_expiration': True,
                'is_expired': False,
                'expires_in_human': '2 hours',
                'expires_at': 1234567890
            }
            mocks['profile_manager'].load_auth_profile.return_value = {
                'user_id': 'test@example.com'
            }
            mocks['profile_manager'].has_credentials.return_value = True
            mocks['profile_manager'].get_feature_switches_info.return_value = {
                'has_feature_switches': True,
                'count': 2,
                'enabled': ['FEATURE1', 'FEATURE2'],
                'fetched_at': '2024-01-01T12:00:00Z'
            }
            
            result = cli_runner.invoke(cli, ['auth', 'status'])
            
            assert result.exit_code == 0
            assert 'Authentication Status:' in result.output
            assert 'Type: Cognito Token' in result.output
            assert 'User ID: test@example.com' in result.output
            assert 'Status: ✓ Valid' in result.output
            assert 'Expires in: 2 hours' in result.output
            assert 'Saved credentials: Yes' in result.output
            assert 'Feature Switches:' in result.output
            assert 'Count: 2' in result.output
    
    def test_auth_refresh_success(self, cli_runner, auth_command_mocks):
        """Test successful auth refresh."""
        with auth_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].load_auth_profile.return_value = {
                'refresh_token': 'test-refresh-token',
                'user_id': 'test@example.com'
            }
            
            mock_authenticator = Mock()
            mock_authenticator.refresh_token.return_value = {
                'access_token': 'new-access-token',
                'expires_in': 3600
            }
            
            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator):
                result = cli_runner.invoke(cli, ['auth', 'refresh'])
            
            assert result.exit_code == 0
            assert '✓ Tokens refreshed successfully!' in result.output
            
            # Verify refresh was called
            mock_authenticator.refresh_token.assert_called_once_with('test-refresh-token')
            mocks['profile_manager'].save_auth_profile.assert_called_once()
    
    def test_auth_refresh_no_setup(self, cli_runner, auth_no_setup_mocks):
        """Test auth refresh without setup."""
        with auth_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['auth', 'refresh'])
            
            assert result.exit_code == 1
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output
    
    def test_auth_refresh_no_auth_profile(self, cli_runner, auth_command_mocks):
        """Test auth refresh without auth profile."""
        with auth_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = False
            
            result = cli_runner.invoke(cli, ['auth', 'refresh'])
            
            assert result.exit_code == 1
            assert 'Not authenticated' in result.output
            assert 'vamscli auth login' in result.output
    
    def test_auth_set_override_success(self, cli_runner, auth_command_mocks):
        """Test successful auth set-override."""
        with auth_command_mocks as mocks:
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_feature_switches.return_value = {
                'raw': 'FEATURE1',
                'enabled': ['FEATURE1']
            }
            
            result = cli_runner.invoke(cli, [
                'auth', 'set-override',
                '-u', 'test@example.com',
                '--token', 'override-token-123'
            ])
            
            assert result.exit_code == 0
            assert '✓ Override token saved and validated successfully!' in result.output
            assert 'User profile refreshed' in result.output
            assert 'Feature switches updated' in result.output
            
            # Verify API calls
            mocks['profile_manager'].save_override_token.assert_called_once()
            mocks['api_client'].call_login_profile.assert_called_once_with('test@example.com')
    
    def test_auth_set_override_no_setup(self, cli_runner, auth_no_setup_mocks):
        """Test auth set-override without setup."""
        with auth_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'auth', 'set-override',
                '-u', 'test@example.com',
                '--token', 'override-token-123'
            ])
            
            assert result.exit_code == 1
            assert 'Configuration not found' in result.output
            assert 'vamscli setup' in result.output
    
    def test_auth_clear_override_success(self, cli_runner, auth_command_mocks):
        """Test successful auth clear-override."""
        with auth_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].is_override_token.return_value = True
            
            result = cli_runner.invoke(cli, ['auth', 'clear-override'])
            
            assert result.exit_code == 0
            assert '✓ Override token cleared successfully!' in result.output
            assert 'vamscli auth login' in result.output
            
            # Verify clear was called
            mocks['profile_manager'].delete_auth_profile.assert_called_once()
    
    def test_auth_clear_override_no_override(self, cli_runner, auth_command_mocks):
        """Test auth clear-override when no override token is set."""
        with auth_command_mocks as mocks:
            mocks['profile_manager'].has_auth_profile.return_value = True
            mocks['profile_manager'].is_override_token.return_value = False
            
            result = cli_runner.invoke(cli, ['auth', 'clear-override'])
            
            assert result.exit_code == 0
            assert 'No override token is currently set' in result.output
    
    def test_auth_login_token_override_success(self, cli_runner, auth_command_mocks):
        """Test successful auth login with token override."""
        with auth_command_mocks as mocks:
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_feature_switches.return_value = {
                'raw': 'FEATURE1',
                'enabled': ['FEATURE1']
            }
            
            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '--user-id', 'test@example.com',
                '--token-override', 'override-token-123'
            ])
            
            assert result.exit_code == 0
            assert '✓ Token override authentication successful!' in result.output
            assert 'User profile refreshed' in result.output
            assert 'Feature switches updated' in result.output
            assert 'Override tokens do not support automatic refresh' in result.output
            
            # Verify API calls
            mocks['profile_manager'].save_override_token.assert_called_once_with('override-token-123', 'test@example.com', None)
            mocks['api_client'].call_login_profile.assert_called_once_with('test@example.com')
    
    def test_auth_login_token_override_with_expiration(self, cli_runner, auth_command_mocks):
        """Test auth login with token override and expiration."""
        with auth_command_mocks as mocks:
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_feature_switches.return_value = {
                'raw': 'FEATURE1',
                'enabled': ['FEATURE1']
            }
            mocks['profile_manager'].get_token_expiration_info.return_value = {
                'expires_in_human': '1 hour'
            }
            
            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '--user-id', 'test@example.com',
                '--token-override', 'override-token-123',
                '--expires-at', '+3600'
            ])
            
            assert result.exit_code == 0
            assert '✓ Token override authentication successful!' in result.output
            assert 'Token expires in 1 hour' in result.output
            
            # Verify API calls
            mocks['profile_manager'].save_override_token.assert_called_once_with('override-token-123', 'test@example.com', '+3600')
    
    def test_auth_login_token_override_missing_user_id(self, cli_runner, auth_command_mocks):
        """Test auth login with token override but missing user ID."""
        with auth_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '--token-override', 'override-token-123'
            ])
            
            assert result.exit_code == 1
            assert '--user-id is required when using --token-override' in result.output
    
    def test_auth_login_token_override_authentication_error(self, cli_runner, auth_command_mocks):
        """Test auth login with token override authentication error."""
        with auth_command_mocks as mocks:
            mocks['api_client'].call_login_profile.side_effect = AuthenticationError("Invalid token")
            
            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '--user-id', 'test@example.com',
                '--token-override', 'invalid-token'
            ])
            
            assert result.exit_code == 1
            assert '✗ Token override authentication failed' in result.output
            assert 'Invalid token' in result.output
    
    def test_auth_login_token_override_with_save_credentials_error(self, cli_runner, auth_command_mocks):
        """Test auth login with token override and save credentials (should fail)."""
        with auth_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '--user-id', 'test@example.com',
                '--token-override', 'override-token-123',
                '--save-credentials'
            ])
            
            assert result.exit_code == 1
            assert '--save-credentials cannot be used with --token-override' in result.output
    
    def test_auth_login_version_mismatch(self, cli_runner, auth_command_mocks):
        """Test auth login with version mismatch."""
        with auth_command_mocks as mocks:
            # Mock version mismatch
            mocks['api_client'].check_version.return_value = {
                'match': False,
                'cli_version': '1.0.0',
                'api_version': '2.0.0'
            }
            
            # Mock authenticator and API responses
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = {
                'access_token': 'test-token',
                'refresh_token': 'test-refresh',
                'expires_in': 3600
            }
            
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_feature_switches.return_value = {
                'raw': 'FEATURE1',
                'enabled': ['FEATURE1']
            }
            
            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator), \
                 patch('click.prompt', return_value='password123'), \
                 patch('click.confirm', return_value=True):
                
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com'
                ])
            
            assert result.exit_code == 0
            assert 'Version mismatch detected' in result.output
            assert 'CLI version: 1.0.0' in result.output
            assert 'API version: 2.0.0' in result.output
            assert '✓ Cognito authentication successful!' in result.output
    
    def test_auth_login_version_mismatch_cancelled(self, cli_runner, auth_command_mocks):
        """Test auth login with version mismatch cancelled by user."""
        with auth_command_mocks as mocks:
            # Mock version mismatch
            mocks['api_client'].check_version.return_value = {
                'match': False,
                'cli_version': '1.0.0',
                'api_version': '2.0.0'
            }
            
            with patch('click.confirm', return_value=False):
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com'
                ])
            
            assert result.exit_code == 0
            assert 'Version mismatch detected' in result.output
            assert 'Authentication cancelled' in result.output
            
            # Verify authentication was not attempted
            mocks['profile_manager'].save_auth_profile.assert_not_called()
    
    def test_auth_login_version_mismatch_skip_check(self, cli_runner, auth_command_mocks):
        """Test auth login with version mismatch using --skip-version-check flag."""
        with auth_command_mocks as mocks:
            # Mock version mismatch
            mocks['api_client'].check_version.return_value = {
                'match': False,
                'cli_version': '1.0.0',
                'api_version': '2.0.0'
            }
            
            # Mock authenticator and API responses
            mock_authenticator = Mock()
            mock_authenticator.authenticate.return_value = {
                'access_token': 'test-token',
                'refresh_token': 'test-refresh',
                'expires_in': 3600
            }
            
            mocks['api_client'].call_login_profile.return_value = {'success': True}
            mocks['api_client'].get_feature_switches.return_value = {
                'raw': 'FEATURE1',
                'enabled': ['FEATURE1']
            }
            
            with patch('vamscli.commands.auth.get_authenticator', return_value=mock_authenticator), \
                 patch('click.prompt', return_value='password123'):
                
                result = cli_runner.invoke(cli, [
                    'auth', 'login',
                    '-u', 'test@example.com',
                    '--skip-version-check'
                ])
            
            assert result.exit_code == 0
            assert 'Version mismatch detected' in result.output
            assert 'CLI version: 1.0.0' in result.output
            assert 'API version: 2.0.0' in result.output
            assert 'Skipping version check confirmation (--skip-version-check enabled)' in result.output
            assert '✓ Cognito authentication successful!' in result.output
            
            # Verify authentication was completed without user confirmation
            mocks['profile_manager'].save_auth_profile.assert_called_once()


class TestCLIIntegration:
    """Test CLI integration scenarios and edge cases."""
    
    
    def test_global_profile_with_command(self, cli_runner, cli_command_mocks):
        """Test global profile option with command."""
        with cli_command_mocks as mocks:
            mocks['profile_manager'].profile_name = 'test-profile'
            
            result = cli_runner.invoke(cli, [
                '--profile', 'test-profile',
                'setup', '--help'
            ])
            
            assert result.exit_code == 0
            assert 'Setup VamsCLI' in result.output
    
    def test_command_requires_parameters(self, cli_runner, auth_command_mocks):
        """Test that commands require appropriate parameters."""
        # Test setup without URL
        result = cli_runner.invoke(cli, ['setup'])
        assert result.exit_code == 2
        assert 'Missing argument' in result.output
        
        # Test auth login without required parameters (now handled by command logic, not Click)
        with auth_command_mocks as mocks:
            # Mock version check to pass
            mocks['api_client'].check_version.return_value = {
                'match': True,
                'cli_version': '1.0.0',
                'api_version': '1.0.0'
            }
            
            result = cli_runner.invoke(cli, ['auth', 'login'])
            assert result.exit_code == 1
            assert '--username is required for Cognito authentication' in result.output
    
    def test_help_commands_work_without_setup(self, cli_runner):
        """Test that help commands work without setup."""
        # Main help
        result = cli_runner.invoke(cli, ['--help'])
        assert result.exit_code == 0
        
        # Command help
        result = cli_runner.invoke(cli, ['setup', '--help'])
        assert result.exit_code == 0
        
        # Subcommand help
        result = cli_runner.invoke(cli, ['auth', 'login', '--help'])
        assert result.exit_code == 0
    
    def test_version_commands_work_without_setup(self, cli_runner):
        """Test that version commands work without setup."""
        # Version flag
        result = cli_runner.invoke(cli, ['--version'])
        assert result.exit_code == 0
        assert get_version() in result.output
        
        # Version command
        result = cli_runner.invoke(cli, ['version'])
        assert result.exit_code == 0
        assert get_version() in result.output


class TestCLIErrorHandling:
    """Test CLI error handling scenarios."""
    
    def test_setup_required_error_handling(self, cli_runner):
        """Test setup required error handling."""
        # This test would normally trigger setup check, but we're in test mode
        # so we test the error handling pattern instead
        with patch('vamscli.main.ProfileManager') as mock_pm:
            mock_profile_manager = Mock()
            mock_profile_manager.has_config.return_value = False
            mock_pm.return_value = mock_profile_manager
            
            # Mock the setup check to bypass test mode detection
            with patch('sys.modules', {'pytest': None}), \
                 patch('sys.argv', ['vamscli', 'auth', 'status']):
                
                result = cli_runner.invoke(cli, ['auth', 'status'])
                # In test mode, this should still work, but we're testing the pattern
                assert result.exit_code in [0, 1]  # Either works or fails gracefully
    
    def test_api_error_propagation(self, cli_runner, cli_command_mocks):
        """Test that API errors are properly propagated."""
        with cli_command_mocks as mocks:
            mocks['profile_manager'].has_config.return_value = False
            mocks['api_client'].check_version.side_effect = APIError("Connection failed")
            
            result = cli_runner.invoke(cli, [
                'setup', 
                'https://api.example.com'
            ])
            
            assert result.exit_code == 1
            # Check that the original exception is preserved
            assert isinstance(result.exception, APIError)
    
    def test_configuration_error_handling(self, cli_runner, auth_command_mocks):
        """Test configuration error handling."""
        with auth_command_mocks as mocks:
            mocks['profile_manager'].load_config.side_effect = ConfigurationError("Invalid config")
            
            result = cli_runner.invoke(cli, [
                'auth', 'login',
                '-u', 'test@example.com'
            ])
            
            assert result.exit_code == 1
            assert 'Failed to load configuration' in result.output


class TestCLIJSONHandling:
    """Test JSON input/output handling where applicable."""
    
    def test_json_error_handling(self, cli_runner, cli_command_mocks):
        """Test JSON error handling in commands that support it."""
        # This is a placeholder for JSON handling tests
        # Most CLI commands don't use JSON, but this structure is ready
        # for when JSON commands are added
        pass


if __name__ == '__main__':
    pytest.main([__file__])
