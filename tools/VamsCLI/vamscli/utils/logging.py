"""Centralized logging utility for VamsCLI with file logging and verbose mode support."""

import logging
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

import click

from ..constants import (
    LOG_DIR_NAME, LOG_FILE_NAME, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
    LOG_FORMAT, LOG_DATE_FORMAT, get_config_dir
)


# Global logger instance
_logger: Optional[logging.Logger] = None
_verbose_mode: bool = False


class ProfileContextFilter(logging.Filter):
    """Add profile and command context to log records."""
    
    def __init__(self):
        super().__init__()
        self.profile_name = "unknown"
        self.command_name = "unknown"
    
    def filter(self, record):
        record.profile = self.profile_name
        record.command = self.command_name
        return True


# Global context filter instance
_context_filter = ProfileContextFilter()


def get_log_dir() -> Path:
    """Get the global logs directory path."""
    return get_config_dir() / LOG_DIR_NAME


def ensure_log_dir():
    """Ensure the logs directory exists."""
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)


def initialize_logging(verbose: bool = False):
    """
    Initialize the global logging system.
    
    Args:
        verbose: Whether verbose mode is enabled
    """
    global _logger, _verbose_mode
    
    _verbose_mode = verbose
    
    # Return existing logger if already initialized
    if _logger is not None:
        return _logger
    
    # Ensure log directory exists
    ensure_log_dir()
    
    # Create logger
    _logger = logging.getLogger('vamscli')
    _logger.setLevel(logging.DEBUG)  # Capture all levels
    
    # Remove any existing handlers
    _logger.handlers.clear()
    
    # Create rotating file handler
    log_file = get_log_dir() / LOG_FILE_NAME
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    
    # Create formatter
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    file_handler.setFormatter(formatter)
    
    # Add context filter
    file_handler.addFilter(_context_filter)
    
    # Add handler to logger
    _logger.addHandler(file_handler)
    
    # Prevent propagation to root logger
    _logger.propagate = False
    
    return _logger


def get_logger() -> logging.Logger:
    """
    Get the global logger instance.
    
    Returns:
        The global logger instance
    """
    global _logger
    
    if _logger is None:
        initialize_logging()
    
    return _logger


def _is_verbose_mode() -> bool:
    """Check if verbose mode is enabled."""
    global _verbose_mode
    
    # Also check sys.argv as fallback
    if '--verbose' in sys.argv:
        return True
    
    return _verbose_mode


def set_context(profile_name: str = None, command_name: str = None):
    """
    Set the current context for logging.
    
    Args:
        profile_name: Name of the current profile
        command_name: Name of the current command
    """
    global _context_filter
    
    if profile_name is not None:
        _context_filter.profile_name = profile_name
    
    if command_name is not None:
        _context_filter.command_name = command_name


def log_command_start(command_name: str, args: Dict[str, Any] = None):
    """
    Log the start of a command execution.
    
    Args:
        command_name: Name of the command being executed
        args: Command arguments (sensitive data will be filtered)
    """
    logger = get_logger()
    set_context(command_name=command_name)
    
    # Filter sensitive arguments
    safe_args = {}
    if args:
        for key, value in args.items():
            if key.lower() in ['password', 'token', 'secret', 'key']:
                safe_args[key] = '***REDACTED***'
            else:
                safe_args[key] = value
    
    logger.info(f"Command started: {command_name}")
    if safe_args:
        logger.debug(f"Command arguments: {safe_args}")
    
    if _is_verbose_mode():
        click.echo(f"\n🚀 Starting command: {command_name}", err=True)
        if safe_args:
            click.echo(f"📝 Arguments: {safe_args}", err=True)


def log_command_end(command_name: str, success: bool, duration: float):
    """
    Log the end of a command execution.
    
    Args:
        command_name: Name of the command that was executed
        success: Whether the command completed successfully
        duration: Execution duration in seconds
    """
    logger = get_logger()
    
    status = "successfully" if success else "with errors"
    logger.info(f"Command completed {status}: {command_name} (duration: {duration:.2f}s)")
    
    if _is_verbose_mode():
        if success:
            click.echo(f"\n✓ Command completed successfully in {duration:.2f}s", err=True)
        else:
            click.echo(f"\n✗ Command failed after {duration:.2f}s", err=True)


def log_api_request(method: str, url: str, headers: Dict[str, str] = None, body: Any = None):
    """
    Log an API request.
    
    Args:
        method: HTTP method (GET, POST, etc.)
        url: Request URL
        headers: Request headers (sensitive data will be filtered)
        body: Request body (sensitive data will be filtered)
    """
    logger = get_logger()
    
    # Filter sensitive headers
    safe_headers = {}
    if headers:
        for key, value in headers.items():
            if key.lower() in ['authorization', 'x-api-key', 'cookie']:
                safe_headers[key] = '***REDACTED***'
            else:
                safe_headers[key] = value
    
    logger.debug(f"API Request: {method} {url}")
    if safe_headers:
        logger.debug(f"Request headers: {safe_headers}")
    if body:
        # Log full body to file (with truncation for very large bodies)
        body_str = str(body)
        if len(body_str) > 10000:
            logger.debug(f"Request body: {body_str[:10000]}... (truncated, full length: {len(body_str)} chars)")
        else:
            logger.debug(f"Request body: {body_str}")
    
    if _is_verbose_mode():
        click.echo(f"\n→ API Request: {method} {url}", err=True)
        if safe_headers:
            click.echo(f"  Headers: {safe_headers}", err=True)
        if body:
            # Display body in console (with truncation for readability)
            body_str = str(body)
            if len(body_str) > 1000:
                click.echo(f"  Body: {body_str[:1000]}... (truncated, see log file for full body)", err=True)
            else:
                click.echo(f"  Body: {body_str}", err=True)


def log_api_response(status_code: int, response_data: Any = None, duration: float = None):
    """
    Log an API response.
    
    Args:
        status_code: HTTP status code
        response_data: Response data (will be truncated if large)
        duration: Request duration in seconds
    """
    logger = get_logger()
    
    duration_str = f" ({duration:.2f}s)" if duration else ""
    logger.debug(f"API Response: {status_code}{duration_str}")
    
    if response_data:
        # Log full response to file (with truncation for very large responses)
        response_str = str(response_data)
        if len(response_str) > 10000:
            logger.debug(f"Response body: {response_str[:10000]}... (truncated, full length: {len(response_str)} chars)")
        else:
            logger.debug(f"Response body: {response_str}")
    
    if _is_verbose_mode():
        duration_str = f" ({duration:.2f}s)" if duration else ""
        click.echo(f"← API Response: {status_code}{duration_str}", err=True)
        if response_data:
            # Display response body in console (with truncation for readability)
            response_str = str(response_data)
            if len(response_str) > 1000:
                click.echo(f"  Body: {response_str[:1000]}... (truncated, see log file for full body)", err=True)
            else:
                click.echo(f"  Body: {response_str}", err=True)


def log_warning(message: str, show_console: bool = None):
    """
    Log a warning message.
    
    Args:
        message: Warning message
        show_console: Whether to show in console (None = use verbose mode)
    """
    logger = get_logger()
    logger.warning(message)
    
    # Show in console if explicitly requested or if verbose mode
    if show_console is True or (show_console is None and _is_verbose_mode()):
        click.echo(click.style(f"⚠ Warning: {message}", fg='yellow'), err=True)


def log_error(message: str, exception: Exception = None):
    """
    Log an error message with optional exception details.
    
    Args:
        message: Error message
        exception: Exception object (will log full traceback)
    """
    logger = get_logger()
    
    if exception:
        logger.error(f"{message}: {exception}", exc_info=True)
    else:
        logger.error(message)


def log_config_info(config: Dict[str, Any]):
    """
    Log configuration information.
    
    Args:
        config: Configuration dictionary
    """
    logger = get_logger()
    
    # Filter sensitive config values
    safe_config = {}
    for key, value in config.items():
        if key.lower() in ['password', 'token', 'secret', 'key']:
            safe_config[key] = '***REDACTED***'
        else:
            safe_config[key] = value
    
    logger.debug(f"Configuration: {safe_config}")
    
    if _is_verbose_mode():
        click.echo("\n📋 Configuration:", err=True)
        for key, value in safe_config.items():
            click.echo(f"  {key}: {value}", err=True)


def log_info(message: str, show_console: bool = None):
    """
    Log an informational message.
    
    Args:
        message: Info message
        show_console: Whether to show in console (None = use verbose mode)
    """
    logger = get_logger()
    logger.info(message)
    
    # Show in console if explicitly requested or if verbose mode
    if show_console is True or (show_console is None and _is_verbose_mode()):
        click.echo(f"ℹ {message}", err=True)


def log_debug(message: str):
    """
    Log a debug message (only in verbose mode).
    
    Args:
        message: Debug message
    """
    logger = get_logger()
    logger.debug(message)
    
    if _is_verbose_mode():
        click.echo(f"🔍 Debug: {message}", err=True)


def get_log_file_path() -> Path:
    """
    Get the path to the current log file.
    
    Returns:
        Path to the log file
    """
    return get_log_dir() / LOG_FILE_NAME


def get_log_file_info() -> Dict[str, Any]:
    """
    Get information about the log file.
    
    Returns:
        Dictionary with log file information
    """
    log_file = get_log_file_path()
    
    if not log_file.exists():
        return {
            'exists': False,
            'path': str(log_file),
            'size': 0,
            'size_human': '0 B'
        }
    
    size = log_file.stat().st_size
    
    # Human-readable size
    if size < 1024:
        size_human = f"{size} B"
    elif size < 1024 * 1024:
        size_human = f"{size / 1024:.2f} KB"
    else:
        size_human = f"{size / (1024 * 1024):.2f} MB"
    
    return {
        'exists': True,
        'path': str(log_file),
        'size': size,
        'size_human': size_human,
        'modified': datetime.fromtimestamp(log_file.stat().st_mtime).isoformat()
    }
