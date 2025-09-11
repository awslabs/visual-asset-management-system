"""Test retry functionality for 429 throttling errors."""

import os
import time
import pytest
from unittest.mock import Mock, patch, MagicMock
import requests
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import RateLimitExceededError, RetryExhaustedError
from vamscli.utils.retry_config import RetryConfig, get_retry_config, reset_retry_config
from vamscli.utils.api_client import APIClient


class TestRetryConfig:
    """Test retry configuration functionality."""

    def setup_method(self):
        """Reset retry config before each test."""
        reset_retry_config()
        # Clear environment variables
        for var in ['VAMS_CLI_MAX_RETRY_ATTEMPTS', 'VAMS_CLI_INITIAL_RETRY_DELAY', 
                   'VAMS_CLI_MAX_RETRY_DELAY', 'VAMS_CLI_RETRY_BACKOFF_MULTIPLIER', 
                   'VAMS_CLI_RETRY_JITTER']:
            if var in os.environ:
                del os.environ[var]

    def test_default_configuration(self):
        """Test default retry configuration values."""
        config = RetryConfig()
        
        assert config.max_retry_attempts == 5
        assert config.initial_retry_delay == 1.0
        assert config.max_retry_delay == 60.0
        assert config.backoff_multiplier == 2.0
        assert config.jitter == 0.1

    def test_environment_variable_configuration(self):
        """Test configuration from environment variables."""
        os.environ['VAMS_CLI_MAX_RETRY_ATTEMPTS'] = '10'
        os.environ['VAMS_CLI_INITIAL_RETRY_DELAY'] = '2.5'
        os.environ['VAMS_CLI_MAX_RETRY_DELAY'] = '120.0'
        os.environ['VAMS_CLI_RETRY_BACKOFF_MULTIPLIER'] = '1.5'
        os.environ['VAMS_CLI_RETRY_JITTER'] = '0.2'
        
        config = RetryConfig()
        
        assert config.max_retry_attempts == 10
        assert config.initial_retry_delay == 2.5
        assert config.max_retry_delay == 120.0
        assert config.backoff_multiplier == 1.5
        assert config.jitter == 0.2

    def test_invalid_environment_variables(self):
        """Test handling of invalid environment variable values."""
        os.environ['VAMS_CLI_MAX_RETRY_ATTEMPTS'] = 'invalid'
        os.environ['VAMS_CLI_INITIAL_RETRY_DELAY'] = 'not_a_number'
        
        config = RetryConfig()
        
        # Should fall back to defaults
        assert config.max_retry_attempts == 5
        assert config.initial_retry_delay == 1.0

    def test_configuration_validation(self):
        """Test configuration value validation."""
        os.environ['VAMS_CLI_MAX_RETRY_ATTEMPTS'] = '-1'  # Invalid
        os.environ['VAMS_CLI_INITIAL_RETRY_DELAY'] = '0.05'  # Too small
        os.environ['VAMS_CLI_MAX_RETRY_DELAY'] = '500'  # Too large
        os.environ['VAMS_CLI_RETRY_BACKOFF_MULTIPLIER'] = '10'  # Too large
        os.environ['VAMS_CLI_RETRY_JITTER'] = '0.8'  # Too large
        
        config = RetryConfig()
        
        assert config.max_retry_attempts == 0  # Corrected to 0
        assert config.initial_retry_delay == 0.1  # Corrected to minimum
        assert config.max_retry_delay == 300  # Corrected to maximum
        assert config.backoff_multiplier == 5.0  # Corrected to maximum
        assert config.jitter == 0.5  # Corrected to maximum

    def test_calculate_delay_exponential_backoff(self):
        """Test exponential backoff delay calculation."""
        config = RetryConfig()
        
        # Test exponential backoff progression
        delay_0 = config.calculate_delay(0)  # First retry
        delay_1 = config.calculate_delay(1)  # Second retry
        delay_2 = config.calculate_delay(2)  # Third retry
        
        # Should increase exponentially (accounting for jitter)
        assert 0.9 <= delay_0 <= 1.1  # ~1.0 seconds ± jitter
        assert 1.8 <= delay_1 <= 2.2  # ~2.0 seconds ± jitter
        assert 3.6 <= delay_2 <= 4.4  # ~4.0 seconds ± jitter

    def test_calculate_delay_with_retry_after(self):
        """Test delay calculation with server-provided Retry-After header."""
        config = RetryConfig()
        
        # Server says wait 10 seconds
        delay = config.calculate_delay(0, retry_after=10)
        
        # Should respect server's request (with jitter)
        assert 9.0 <= delay <= 11.0

    def test_calculate_delay_max_limit(self):
        """Test that delays don't exceed maximum."""
        config = RetryConfig()
        
        # High attempt number should be capped at max_retry_delay
        delay = config.calculate_delay(10)  # Very high attempt
        
        assert delay <= config.max_retry_delay

    def test_should_retry(self):
        """Test retry decision logic."""
        config = RetryConfig()
        
        # Should retry for attempts within limit
        assert config.should_retry(0) == True
        assert config.should_retry(4) == True  # Last allowed attempt
        assert config.should_retry(5) == False  # Exceeds limit

    def test_sleep_with_progress_short_delay(self):
        """Test sleep with progress for short delays."""
        config = RetryConfig()
        
        start_time = time.time()
        config.sleep_with_progress(0.5, 1, 5, show_progress=True)
        elapsed = time.time() - start_time
        
        # Should sleep for approximately the requested time
        assert 0.4 <= elapsed <= 0.6

    @patch('builtins.print')
    def test_sleep_with_progress_long_delay(self, mock_print):
        """Test sleep with progress indication for long delays."""
        config = RetryConfig()
        
        start_time = time.time()
        config.sleep_with_progress(1.5, 2, 5, show_progress=True)
        elapsed = time.time() - start_time
        
        # Should sleep for approximately the requested time
        assert 1.4 <= elapsed <= 1.6
        
        # Should have printed progress messages
        assert mock_print.call_count >= 2  # Start and end messages

    def test_global_retry_config(self):
        """Test global retry configuration instance."""
        config1 = get_retry_config()
        config2 = get_retry_config()
        
        # Should return the same instance
        assert config1 is config2
        
        # Reset and get new instance
        reset_retry_config()
        config3 = get_retry_config()
        
        # Should be a new instance
        assert config1 is not config3


class TestAPIClientRetryLogic:
    """Test API client retry logic for 429 errors."""

    def setup_method(self):
        """Setup test environment."""
        reset_retry_config()
        self.api_client = APIClient("https://api.example.com")

    @patch('vamscli.utils.api_client.get_retry_config')
    @patch('requests.Session.request')
    def test_successful_request_no_retry(self, mock_request, mock_get_retry_config):
        """Test successful request without retry."""
        # Setup mocks
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'success': True}
        mock_request.return_value = mock_response
        
        mock_retry_config = Mock()
        mock_get_retry_config.return_value = mock_retry_config
        
        # Make request
        response = self.api_client.get('/test', include_auth=False)
        
        # Should succeed without retry
        assert response.status_code == 200
        assert mock_request.call_count == 1
        mock_retry_config.should_retry.assert_not_called()

    @patch('vamscli.utils.api_client.get_retry_config')
    @patch('requests.Session.request')
    def test_429_retry_success(self, mock_request, mock_get_retry_config):
        """Test successful retry after 429 error."""
        # Setup retry config mock
        mock_retry_config = Mock()
        mock_retry_config.should_retry.side_effect = [True, False]  # Retry once, then stop
        mock_retry_config.calculate_delay.return_value = 0.1  # Short delay for testing
        mock_retry_config.sleep_with_progress = Mock()
        mock_retry_config.max_retry_attempts = 5  # Add this attribute
        mock_get_retry_config.return_value = mock_retry_config
        
        # Setup response mocks
        mock_429_response = Mock()
        mock_429_response.status_code = 429
        mock_429_response.headers = {}
        
        mock_success_response = Mock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {'success': True}
        
        mock_request.side_effect = [mock_429_response, mock_success_response]
        
        # Make request
        response = self.api_client.get('/test', include_auth=False)
        
        # Should succeed after retry
        assert response.status_code == 200
        assert mock_request.call_count == 2
        mock_retry_config.should_retry.assert_called_once_with(0)
        mock_retry_config.calculate_delay.assert_called_once_with(0, None)
        mock_retry_config.sleep_with_progress.assert_called_once()

    @patch('vamscli.utils.api_client.get_retry_config')
    @patch('requests.Session.request')
    def test_429_retry_with_retry_after_header(self, mock_request, mock_get_retry_config):
        """Test retry with server-provided Retry-After header."""
        # Setup retry config mock
        mock_retry_config = Mock()
        mock_retry_config.should_retry.side_effect = [True, False]
        mock_retry_config.calculate_delay.return_value = 0.1
        mock_retry_config.sleep_with_progress = Mock()
        mock_retry_config.max_retry_attempts = 5  # Add this attribute
        mock_get_retry_config.return_value = mock_retry_config
        
        # Setup response mocks
        mock_429_response = Mock()
        mock_429_response.status_code = 429
        mock_429_response.headers = {'Retry-After': '5'}
        
        mock_success_response = Mock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {'success': True}
        
        mock_request.side_effect = [mock_429_response, mock_success_response]
        
        # Make request
        response = self.api_client.get('/test', include_auth=False)
        
        # Should succeed after retry
        assert response.status_code == 200
        mock_retry_config.calculate_delay.assert_called_once_with(0, 5)  # Should pass Retry-After value

    @patch('vamscli.utils.api_client.get_retry_config')
    @patch('requests.Session.request')
    def test_429_retry_exhausted(self, mock_request, mock_get_retry_config):
        """Test retry exhaustion after maximum attempts."""
        # Setup retry config mock
        mock_retry_config = Mock()
        mock_retry_config.should_retry.return_value = False  # No more retries
        mock_retry_config.max_retry_attempts = 3
        mock_get_retry_config.return_value = mock_retry_config
        
        # Setup response mock
        mock_429_response = Mock()
        mock_429_response.status_code = 429
        mock_429_response.headers = {}
        mock_request.return_value = mock_429_response
        
        # Make request and expect RetryExhaustedError
        with pytest.raises(RetryExhaustedError) as exc_info:
            self.api_client.get('/test', include_auth=False)
        
        assert "All 3 retry attempts exhausted" in str(exc_info.value)
        mock_retry_config.should_retry.assert_called_once_with(0)

    @patch('vamscli.utils.api_client.get_retry_config')
    @patch('requests.Session.request')
    def test_429_retry_multiple_attempts(self, mock_request, mock_get_retry_config):
        """Test multiple retry attempts before success."""
        # Setup retry config mock
        mock_retry_config = Mock()
        mock_retry_config.should_retry.side_effect = [True, True, False]  # Two retries
        mock_retry_config.calculate_delay.return_value = 0.1
        mock_retry_config.sleep_with_progress = Mock()
        mock_retry_config.max_retry_attempts = 5  # Add this attribute
        mock_get_retry_config.return_value = mock_retry_config
        
        # Setup response mocks
        mock_429_response = Mock()
        mock_429_response.status_code = 429
        mock_429_response.headers = {}
        
        mock_success_response = Mock()
        mock_success_response.status_code = 200
        mock_success_response.json.return_value = {'success': True}
        
        mock_request.side_effect = [mock_429_response, mock_429_response, mock_success_response]
        
        # Make request
        response = self.api_client.get('/test', include_auth=False)
        
        # Should succeed after two retries
        assert response.status_code == 200
        assert mock_request.call_count == 3
        assert mock_retry_config.should_retry.call_count == 2
        assert mock_retry_config.calculate_delay.call_count == 2
        assert mock_retry_config.sleep_with_progress.call_count == 2

    @patch('vamscli.utils.api_client.get_retry_config')
    @patch('requests.Session.request')
    def test_non_429_error_no_retry(self, mock_request, mock_get_retry_config):
        """Test that non-429 errors don't trigger retry logic."""
        # Setup retry config mock
        mock_retry_config = Mock()
        mock_get_retry_config.return_value = mock_retry_config
        
        # Setup response mock for 500 error
        mock_500_response = Mock()
        mock_500_response.status_code = 500
        mock_500_response.raise_for_status.side_effect = requests.exceptions.HTTPError()
        mock_request.return_value = mock_500_response
        
        # Make request and expect APIError
        with pytest.raises(Exception):  # Should raise APIError
            self.api_client.get('/test', include_auth=False)
        
        # Should not have called retry logic
        mock_retry_config.should_retry.assert_not_called()
        mock_retry_config.calculate_delay.assert_not_called()

    @patch('vamscli.utils.api_client.get_retry_config')
    @patch('requests.Session.request')
    def test_auth_retry_separate_from_throttle_retry(self, mock_request, mock_get_retry_config):
        """Test that auth retries and throttle retries are tracked separately."""
        # Setup retry config mock
        mock_retry_config = Mock()
        mock_retry_config.should_retry.return_value = True
        mock_retry_config.calculate_delay.return_value = 0.1
        mock_retry_config.sleep_with_progress = Mock()
        mock_retry_config.max_retry_attempts = 5  # Add this attribute
        mock_get_retry_config.return_value = mock_retry_config
        
        # Mock profile manager and auth methods
        with patch.object(self.api_client, '_validate_token_before_request'):
            with patch.object(self.api_client, '_get_headers') as mock_get_headers:
                mock_get_headers.return_value = {'Authorization': 'Bearer token'}
                
                # Setup response mocks - 401 then 429 then success
                mock_401_response = Mock()
                mock_401_response.status_code = 401
                
                mock_429_response = Mock()
                mock_429_response.status_code = 429
                mock_429_response.headers = {}
                
                mock_success_response = Mock()
                mock_success_response.status_code = 200
                mock_success_response.json.return_value = {'success': True}
                
                mock_request.side_effect = [mock_401_response, mock_429_response, mock_success_response]
                
                # Mock token refresh to succeed
                with patch.object(self.api_client, '_try_refresh_token', return_value=True):
                    # Make request
                    response = self.api_client.get('/test', include_auth=True)
                    
                    # Should succeed after both auth retry and throttle retry
                    assert response.status_code == 200
                    assert mock_request.call_count == 3


class TestCLIRetryIntegration:
    """Test CLI integration with retry functionality."""

    def setup_method(self):
        """Setup test environment."""
        reset_retry_config()

    def test_cli_handles_retry_exhausted_error(self, cli_runner, generic_command_mocks):
        """Test CLI handling of RetryExhaustedError."""
        with generic_command_mocks('database') as mocks:
            # Setup API client to raise RetryExhaustedError
            mocks['api_client'].list_databases.side_effect = RetryExhaustedError(
                "Rate limit exceeded. All 5 retry attempts exhausted. The API is currently throttling requests. Please try again later."
            )
            
            # Run CLI command
            result = cli_runner.invoke(cli, ['database', 'list'])
            
            # Should raise RetryExhaustedError (which bubbles up correctly)
            assert isinstance(result.exception, RetryExhaustedError)
            assert "Rate limit exceeded" in str(result.exception)
            assert "All 5 retry attempts exhausted" in str(result.exception)

    def test_cli_handles_rate_limit_exceeded_error(self, cli_runner, generic_command_mocks):
        """Test CLI handling of RateLimitExceededError."""
        with generic_command_mocks('database') as mocks:
            # Setup API client to raise RateLimitExceededError
            mocks['api_client'].list_databases.side_effect = RateLimitExceededError("Rate limit exceeded: HTTP 429")
            
            # Run CLI command
            result = cli_runner.invoke(cli, ['database', 'list'])
            
            # Should raise RateLimitExceededError (which bubbles up correctly)
            assert isinstance(result.exception, RateLimitExceededError)
            assert "Rate limit exceeded" in str(result.exception)


class TestRetryEnvironmentVariables:
    """Test retry configuration with various environment variable scenarios."""

    def setup_method(self):
        """Reset environment before each test."""
        reset_retry_config()
        for var in ['VAMS_CLI_MAX_RETRY_ATTEMPTS', 'VAMS_CLI_INITIAL_RETRY_DELAY', 
                   'VAMS_CLI_MAX_RETRY_DELAY', 'VAMS_CLI_RETRY_BACKOFF_MULTIPLIER', 
                   'VAMS_CLI_RETRY_JITTER']:
            if var in os.environ:
                del os.environ[var]

    def test_production_environment_config(self):
        """Test configuration suitable for production environment."""
        os.environ['VAMS_CLI_MAX_RETRY_ATTEMPTS'] = '8'
        os.environ['VAMS_CLI_INITIAL_RETRY_DELAY'] = '2.0'
        os.environ['VAMS_CLI_MAX_RETRY_DELAY'] = '180.0'
        
        config = RetryConfig()
        
        assert config.max_retry_attempts == 8
        assert config.initial_retry_delay == 2.0
        assert config.max_retry_delay == 180.0

    def test_development_environment_config(self):
        """Test configuration suitable for development environment."""
        os.environ['VAMS_CLI_MAX_RETRY_ATTEMPTS'] = '3'
        os.environ['VAMS_CLI_INITIAL_RETRY_DELAY'] = '0.5'
        os.environ['VAMS_CLI_MAX_RETRY_DELAY'] = '30.0'
        
        config = RetryConfig()
        
        assert config.max_retry_attempts == 3
        assert config.initial_retry_delay == 0.5
        assert config.max_retry_delay == 30.0

    def test_disable_retries(self):
        """Test disabling retries by setting max attempts to 0."""
        os.environ['VAMS_CLI_MAX_RETRY_ATTEMPTS'] = '0'
        
        config = RetryConfig()
        
        assert config.max_retry_attempts == 0
        assert config.should_retry(0) == False

    def test_aggressive_retry_config(self):
        """Test aggressive retry configuration."""
        os.environ['VAMS_CLI_MAX_RETRY_ATTEMPTS'] = '10'
        os.environ['VAMS_CLI_INITIAL_RETRY_DELAY'] = '0.1'
        os.environ['VAMS_CLI_RETRY_BACKOFF_MULTIPLIER'] = '1.2'
        os.environ['VAMS_CLI_RETRY_JITTER'] = '0.05'
        
        config = RetryConfig()
        
        assert config.max_retry_attempts == 10
        assert config.initial_retry_delay == 0.1
        assert config.backoff_multiplier == 1.2
        assert config.jitter == 0.05


if __name__ == '__main__':
    pytest.main([__file__])
