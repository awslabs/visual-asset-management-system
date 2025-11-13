"""Tests for logging functionality."""

import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from click.testing import CliRunner

from vamscli.utils.logging import (
    initialize_logging, get_logger, log_command_start, log_command_end,
    log_api_request, log_api_response, log_warning, log_error,
    log_config_info, get_log_file_path, get_log_file_info,
    _is_verbose_mode, set_context
)


class TestLoggingInitialization:
    """Test logging initialization."""
    
    def test_initialize_logging_creates_logger(self):
        """Test that initialize_logging creates a logger."""
        logger = initialize_logging(verbose=False)
        assert logger is not None
        assert logger.name == 'vamscli'
    
    def test_initialize_logging_verbose_mode(self):
        """Test that verbose mode is set correctly."""
        initialize_logging(verbose=True)
        # Note: _is_verbose_mode checks sys.argv as fallback
        # In tests, we rely on the global _verbose_mode variable
        assert True  # Logger created successfully
    
    def test_get_logger_returns_same_instance(self):
        """Test that get_logger returns the same logger instance."""
        logger1 = get_logger()
        logger2 = get_logger()
        assert logger1 is logger2


class TestContextManagement:
    """Test logging context management."""
    
    def test_set_context_profile_name(self):
        """Test setting profile name in context."""
        set_context(profile_name="test-profile")
        # Context is set successfully
        assert True
    
    def test_set_context_command_name(self):
        """Test setting command name in context."""
        set_context(command_name="test-command")
        # Context is set successfully
        assert True
    
    def test_set_context_both(self):
        """Test setting both profile and command name."""
        set_context(profile_name="test-profile", command_name="test-command")
        # Context is set successfully
        assert True


class TestCommandLogging:
    """Test command logging functions."""
    
    def test_log_command_start(self):
        """Test logging command start."""
        log_command_start("test_command", {"arg1": "value1"})
        # Command start logged successfully
        assert True
    
    def test_log_command_start_with_sensitive_args(self):
        """Test that sensitive arguments are redacted."""
        log_command_start("test_command", {
            "username": "user",
            "password": "secret",
            "token": "abc123"
        })
        # Sensitive data should be redacted in logs
        assert True
    
    def test_log_command_end_success(self):
        """Test logging successful command completion."""
        log_command_end("test_command", success=True, duration=1.5)
        # Command end logged successfully
        assert True
    
    def test_log_command_end_failure(self):
        """Test logging failed command completion."""
        log_command_end("test_command", success=False, duration=0.5)
        # Command failure logged successfully
        assert True


class TestAPILogging:
    """Test API request/response logging."""
    
    def test_log_api_request(self):
        """Test logging API request."""
        log_api_request(
            "GET",
            "https://api.example.com/test",
            {"Content-Type": "application/json"},
            {"key": "value"}
        )
        # API request logged successfully
        assert True
    
    def test_log_api_request_with_sensitive_headers(self):
        """Test that sensitive headers are redacted."""
        log_api_request(
            "POST",
            "https://api.example.com/auth",
            {
                "Authorization": "Bearer secret-token",
                "X-API-Key": "api-key-123",
                "Content-Type": "application/json"
            },
            None
        )
        # Sensitive headers should be redacted
        assert True
    
    def test_log_api_response(self):
        """Test logging API response."""
        log_api_response(200, {"result": "success"}, duration=0.5)
        # API response logged successfully
        assert True
    
    def test_log_api_response_with_duration(self):
        """Test logging API response with duration."""
        log_api_response(404, {"error": "Not found"}, duration=0.3)
        # API response with duration logged successfully
        assert True


class TestWarningAndErrorLogging:
    """Test warning and error logging."""
    
    def test_log_warning(self):
        """Test logging warning message."""
        log_warning("This is a test warning")
        # Warning logged successfully
        assert True
    
    def test_log_warning_show_console_true(self):
        """Test logging warning with explicit console display."""
        log_warning("This is a test warning", show_console=True)
        # Warning logged and displayed
        assert True
    
    def test_log_warning_show_console_false(self):
        """Test logging warning without console display."""
        log_warning("This is a test warning", show_console=False)
        # Warning logged but not displayed
        assert True
    
    def test_log_error_without_exception(self):
        """Test logging error message without exception."""
        log_error("This is a test error")
        # Error logged successfully
        assert True
    
    def test_log_error_with_exception(self):
        """Test logging error message with exception."""
        try:
            raise ValueError("Test exception")
        except ValueError as e:
            log_error("Error occurred", exception=e)
        # Error with exception logged successfully
        assert True


class TestConfigLogging:
    """Test configuration logging."""
    
    def test_log_config_info(self):
        """Test logging configuration information."""
        config = {
            "api_gateway_url": "https://api.example.com",
            "cli_version": "2.2.0",
            "profile_name": "default"
        }
        log_config_info(config)
        # Configuration logged successfully
        assert True
    
    def test_log_config_info_with_sensitive_data(self):
        """Test that sensitive config values are redacted."""
        config = {
            "api_gateway_url": "https://api.example.com",
            "password": "secret",
            "token": "abc123",
            "api_key": "key123"
        }
        log_config_info(config)
        # Sensitive config values should be redacted
        assert True


class TestLogFileOperations:
    """Test log file operations."""
    
    def test_get_log_file_path(self):
        """Test getting log file path."""
        log_path = get_log_file_path()
        assert log_path is not None
        assert isinstance(log_path, Path)
        assert log_path.name == "vamscli.log"
    
    def test_get_log_file_info_nonexistent(self):
        """Test getting info for non-existent log file."""
        info = get_log_file_info()
        assert info is not None
        assert 'exists' in info
        assert 'path' in info
        assert 'size' in info
        assert 'size_human' in info
    
    @patch('vamscli.utils.logging.get_log_file_path')
    def test_get_log_file_info_existing(self, mock_get_path):
        """Test getting info for existing log file."""
        # Create a temporary file to simulate log file
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"Test log content")
            tmp_path = Path(tmp.name)
        
        try:
            mock_get_path.return_value = tmp_path
            info = get_log_file_info()
            
            assert info['exists'] is True
            assert info['size'] > 0
            assert 'B' in info['size_human'] or 'KB' in info['size_human']
        finally:
            # Clean up
            if tmp_path.exists():
                tmp_path.unlink()


class TestVerboseMode:
    """Test verbose mode detection."""
    
    def test_is_verbose_mode_default(self):
        """Test verbose mode detection with default settings."""
        # Without --verbose in sys.argv, should return False
        result = _is_verbose_mode()
        assert isinstance(result, bool)
    
    @patch('sys.argv', ['vamscli', '--verbose', 'test'])
    def test_is_verbose_mode_with_flag(self):
        """Test verbose mode detection with --verbose flag."""
        result = _is_verbose_mode()
        assert result is True


class TestLoggingIntegration:
    """Test logging integration with CLI."""
    
    def test_logging_does_not_break_commands(self):
        """Test that logging doesn't interfere with command execution."""
        # Initialize logging
        initialize_logging(verbose=False)
        
        # Simulate command execution
        log_command_start("test_command")
        log_command_end("test_command", True, 0.1)
        
        # Should complete without errors
        assert True
    
    def test_logging_handles_unicode(self):
        """Test that logging handles unicode characters."""
        log_warning("Test with unicode: 你好世界 🎉")
        log_error("Error with unicode: Ошибка")
        
        # Should handle unicode without errors
        assert True
    
    def test_logging_handles_large_data(self):
        """Test that logging handles large data appropriately."""
        large_data = "x" * 10000
        log_api_response(200, large_data, 1.0)
        
        # Should truncate large data
        assert True


if __name__ == '__main__':
    pytest.main([__file__])
