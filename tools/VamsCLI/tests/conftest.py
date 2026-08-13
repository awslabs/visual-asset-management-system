"""Global pytest fixtures for VamsCLI tests.

This module provides reusable fixtures for common testing patterns across
all VamsCLI test files, particularly for ProfileManager and APIClient mocking.
"""

import inspect
import sys

import pytest
from unittest.mock import Mock, patch, MagicMock
from click.testing import CliRunner
from contextlib import contextmanager

from vamscli.utils import logging as vamscli_logging

# Take pytest's own `--verbose` out of argv before any test runs.
#
# `vamscli.utils.logging._is_verbose_mode()` treats the literal `--verbose` ANYWHERE in sys.argv as a
# request for verbose output, so pytest's own flag switches VAMS verbose logging on for the whole
# session. Every log_* call then also writes to stderr, click's CliRunner merges stderr into
# `result.output`, and the ~113 tests that `json.loads(result.output)` fail on text wrapped around
# their JSON. No fixture can prevent it: the reader is consulted per call, not per test, so the flag
# has to be gone before collection.
#
# Stripping it here rather than changing the reader is deliberate. click already owns this flag
# (`main.py` registers `--verbose` and hands it to `initialize_logging`, which sets the module
# global), so the argv fallback is redundant for the real CLI and no production behavior needs to
# change to make the suite honest.
#
# The cost, stated plainly: pytest reads the same flag for its own per-test progress display, so
# `pytest --verbose` now renders as dots rather than one line per test. `-v` is unaffected — it is a
# different string, so it never triggered the sniff and never needed stripping.
sys.argv[:] = [_arg for _arg in sys.argv if _arg != "--verbose"]


class CoroutineClosingMock(MagicMock):
    """A `MagicMock` for `asyncio.run` that closes the coroutine it is handed.

    Commands call `asyncio.run(some_coro())`. Python evaluates the argument first, so the
    coroutine object is always constructed — then a plain `MagicMock` discards it un-awaited.
    Python emits "coroutine ... was never awaited" when that object is garbage collected, which
    can be inside an unrelated later test.

    Use as `patch('vamscli.commands.<mod>.asyncio.run', new_callable=CoroutineClosingMock)`.
    `return_value`, `side_effect`, and the call assertions all behave as on a normal `MagicMock`;
    `new_callable` is required rather than `side_effect=` because a `side_effect` that returns a
    value makes a later `mock.return_value = ...` silently ineffective.
    """

    def __call__(self, *args, **kwargs):
        for arg in args:
            if inspect.iscoroutine(arg):
                arg.close()
        return super().__call__(*args, **kwargs)


@pytest.fixture(autouse=True)
def isolate_logging_globals():
    """Restore the module-level logging singletons after every test.

    `vamscli.utils.logging` keeps `_verbose_mode` and `_logger` as module globals, and
    `main.py` binds `initialize_logging` at import time — so the `mock_logging` patch below
    never intercepts the call the CLI group actually makes. Every `cli_runner.invoke` runs the
    real `initialize_logging(verbose)` and writes those globals for the rest of the session.

    Leaving `_verbose_mode` True is not a cosmetic leak: in verbose mode every `log_*` call
    also writes to stderr, and `CliRunner` merges stderr into `result.output`. A later test
    doing `json.loads(result.output)` then fails on text wrapped around its JSON document,
    which is a failure in a test that never touched logging.
    """
    verbose_before = vamscli_logging._verbose_mode
    logger_before = vamscli_logging._logger
    try:
        yield
    finally:
        vamscli_logging._verbose_mode = verbose_before
        vamscli_logging._logger = logger_before


@pytest.fixture(autouse=True)
def redirect_log_dir(tmp_path_factory):
    """Point the log directory at a temp dir for every test, real logging or mocked.

    `initialize_logging()` builds a `RotatingFileHandler`, which opens the file eagerly and raises
    `FileNotFoundError` if the parent directory is missing. Patching `ensure_log_dir` (as
    `mock_logging` does) removes the only thing that would have created it, so a test calling the
    real `initialize_logging` — one that imported it directly, so the `initialize_logging` patch
    never applies — fails on a machine with no `~/.config/vamscli/logs`. That is every fresh CI
    runner, while a developer box passes because ordinary CLI use already created the directory.

    Redirecting `get_log_dir` fixes it at the source: every log path in `vamscli.utils.logging`
    derives from that one function, so the whole subsystem stays inside `tmp_path` and no test
    writes to the real user config directory. Guarded by `tests/test_logging_dir_isolation.py`.
    """
    log_dir = tmp_path_factory.mktemp("vamscli-logs")
    with patch('vamscli.utils.logging.get_log_dir', return_value=log_dir):
        yield log_dir


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