"""Global pytest fixtures for VamsCLI tests.

This module provides reusable fixtures for common testing patterns across
all VamsCLI test files, particularly for ProfileManager and APIClient mocking.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from click.testing import CliRunner
from contextlib import contextmanager


@pytest.fixture(autouse=True)
def mock_logging(request):
    """Mock logging initialization to prevent file system operations during tests.
    
    This fixture is automatically used for all tests to prevent the logging
    system from creating directories and files during test execution.
    
    To disable this fixture for specific tests that need real logging,
    use the 'no_mock_logging' marker:
        @pytest.mark.no_mock_logging
        def test_real_logging():
            ...
    """
    # Check if test is marked to skip logging mock
    if 'no_mock_logging' in request.keywords:
        yield None
        return
    
    with patch('vamscli.utils.logging.ensure_log_dir'), \
         patch('vamscli.utils.logging.initialize_logging'), \
         patch('vamscli.utils.logging.get_logger') as mock_get_logger:
        # Return a mock logger that doesn't do anything
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger
        yield mock_logger


@pytest.fixture
def cli_runner():
    """Provide a CliRunner instance for CLI command testing.
    
    Returns:
        CliRunner: Pre-configured CliRunner instance for invoking CLI commands
    """
    return CliRunner()


@pytest.fixture
def mock_profile_manager():
    """Provide a properly configured mock ProfileManager.
    
    This fixture creates a ProfileManager mock with standard configuration
    that works for most test scenarios.
    
    Returns:
        Mock: ProfileManager mock with:
            - has_config() returns True
            - load_config() returns standard API gateway URL with amplify_config
            - profile_name set to 'default'
    """
    mock = Mock()
    mock.has_config.return_value = True
    mock.load_config.return_value = {
        'api_gateway_url': 'https://api.example.com',
        'amplify_config': {
            'region': 'us-east-1',
            'api': 'https://api.example.com',
            'cognitoUserPoolId': 'us-east-1_test123',
            'cognitoAppClientId': 'test-client-id',
            'cognitoIdentityPoolId': 'us-east-1:test-identity-pool',
            'stackName': 'test-stack'
        }
    }
    mock.profile_name = 'default'
    return mock


@pytest.fixture
def mock_api_client():
    """Provide a properly configured mock APIClient.
    
    This fixture creates an APIClient mock with standard configuration
    for API availability checks and common responses.
    
    Returns:
        Mock: APIClient mock with:
            - check_api_availability() returns {'available': True}
    """
    mock = Mock()
    mock.check_api_availability.return_value = {'available': True}
    return mock


@pytest.fixture
def no_setup_profile_manager():
    """Provide a ProfileManager mock that simulates no setup.
    
    This fixture is useful for testing commands when VamsCLI is not set up.
    
    Returns:
        Mock: ProfileManager mock with:
            - has_config() returns False
            - profile_name set to 'default'
    """
    mock = Mock()
    mock.has_config.return_value = False
    mock.profile_name = 'default'
    return mock


@pytest.fixture
def generic_command_mocks(mock_profile_manager, mock_api_client):
    """Provide a factory for creating comprehensive command mocks.
    
    This fixture returns a factory function that can create complete mock setups
    for any command module. It handles all the common ProfileManager and APIClient
    mocking patterns used across VamsCLI tests.
    
    Args:
        mock_profile_manager: Pre-configured ProfileManager mock
        mock_api_client: Pre-configured APIClient mock
    
    Returns:
        function: Factory function that takes a command_module parameter and
                 returns a context manager with all necessary mocks
    
    Example:
        def test_my_command(self, cli_runner, generic_command_mocks):
            with generic_command_mocks('database') as mocks:
                mocks['api_client'].list_databases.return_value = {'Items': []}
                result = cli_runner.invoke(database, ['list'])
                assert result.exit_code == 0
    """
    @contextmanager
    def _create_mocks(command_module):
        """Create comprehensive mocks for a specific command module.
        
        Args:
            command_module (str): The command module name (e.g., 'database', 'assets')
        
        Yields:
            dict: Dictionary containing:
                - 'profile_manager': Mock ProfileManager instance
                - 'api_client': Mock APIClient instance
                - 'patches': Dictionary of all patch objects for advanced usage
        """
        with patch('vamscli.main.ProfileManager') as mock_main_pm, \
             patch('vamscli.utils.decorators.get_profile_manager_from_context') as mock_dec_get_pm, \
             patch(f'vamscli.commands.{command_module}.get_profile_manager_from_context') as mock_cmd_get_pm, \
             patch('vamscli.utils.decorators.APIClient') as mock_dec_api, \
             patch(f'vamscli.commands.{command_module}.APIClient') as mock_cmd_api:
            
            # Setup all ProfileManager mocks
            mock_main_pm.return_value = mock_profile_manager
            mock_dec_get_pm.return_value = mock_profile_manager
            mock_cmd_get_pm.return_value = mock_profile_manager
            
            # Setup all APIClient mocks
            mock_dec_api.return_value = mock_api_client
            mock_cmd_api.return_value = mock_api_client
            
            yield {
                'profile_manager': mock_profile_manager,
                'api_client': mock_api_client,
                'patches': {
                    'main_pm': mock_main_pm,
                    'dec_get_pm': mock_dec_get_pm,
                    'cmd_get_pm': mock_cmd_get_pm,
                    'dec_api': mock_dec_api,
                    'cmd_api': mock_cmd_api
                }
            }
    
    return _create_mocks


@pytest.fixture
def no_setup_command_mocks(no_setup_profile_manager, mock_api_client):
    """Provide a factory for creating command mocks with no setup scenario.
    
    This fixture is useful for testing commands when VamsCLI is not configured.
    
    Args:
        no_setup_profile_manager: ProfileManager mock that returns False for has_config()
        mock_api_client: Pre-configured APIClient mock
    
    Returns:
        function: Factory function similar to generic_command_mocks but for no-setup scenarios
    """
    @contextmanager
    def _create_mocks(command_module):
        """Create mocks for no-setup scenario.
        
        Args:
            command_module (str): The command module name
        
        Yields:
            dict: Dictionary with mocks configured for no-setup scenario
        """
        with patch('vamscli.main.ProfileManager') as mock_main_pm, \
             patch('vamscli.utils.decorators.get_profile_manager_from_context') as mock_dec_get_pm, \
             patch(f'vamscli.commands.{command_module}.get_profile_manager_from_context') as mock_cmd_get_pm, \
             patch('vamscli.utils.decorators.APIClient') as mock_dec_api, \
             patch(f'vamscli.commands.{command_module}.APIClient') as mock_cmd_api:
            
            # Setup ProfileManager mocks for no-setup scenario
            mock_main_pm.return_value = no_setup_profile_manager
            mock_dec_get_pm.return_value = no_setup_profile_manager
            mock_cmd_get_pm.return_value = no_setup_profile_manager
            
            # Setup APIClient mocks
            mock_dec_api.return_value = mock_api_client
            mock_cmd_api.return_value = mock_api_client
            
            yield {
                'profile_manager': no_setup_profile_manager,
                'api_client': mock_api_client,
                'patches': {
                    'main_pm': mock_main_pm,
                    'dec_get_pm': mock_dec_get_pm,
                    'cmd_get_pm': mock_cmd_get_pm,
                    'dec_api': mock_dec_api,
                    'cmd_api': mock_cmd_api
                }
            }
    
    return _create_mocks