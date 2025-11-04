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
    Log an API request with enhanced verbose information.
    
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
    
    # Enhanced logging with timestamp
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    logger.debug(f"[{timestamp}] API Request: {method} {url}")
    if safe_headers:
        logger.debug(f"[{timestamp}] Request headers: {safe_headers}")
    if body:
        # Log full body to file (with truncation for very large bodies)
        body_str = str(body)
        if len(body_str) > 10000:
            logger.debug(f"[{timestamp}] Request body: {body_str[:10000]}... (truncated, full length: {len(body_str)} chars)")
        else:
            logger.debug(f"[{timestamp}] Request body: {body_str}")
    
    if _is_verbose_mode():
        click.echo(f"\n🌐 [{timestamp}] API Request: {click.style(method, fg='cyan', bold=True)} {click.style(url, fg='blue')}", err=True)
        
        # Enhanced header display
        if safe_headers:
            click.echo(f"   📋 Headers ({len(safe_headers)} items):", err=True)
            for key, value in safe_headers.items():
                if key.lower() == 'user-agent':
                    click.echo(f"      {click.style(key, fg='green')}: {click.style(value, fg='yellow')}", err=True)
                elif key.lower() == 'content-type':
                    click.echo(f"      {click.style(key, fg='green')}: {click.style(value, fg='magenta')}", err=True)
                else:
                    click.echo(f"      {click.style(key, fg='green')}: {value}", err=True)
        
        # Enhanced body display
        if body:
            body_str = str(body)
            body_size = len(body_str)
            if body_size > 1000:
                click.echo(f"   📦 Body ({body_size} chars, truncated for display):", err=True)
                click.echo(f"      {body_str[:1000]}...", err=True)
                click.echo(f"      {click.style('(See log file for complete body)', fg='yellow', dim=True)}", err=True)
            else:
                click.echo(f"   📦 Body ({body_size} chars):", err=True)
                # Pretty print JSON if possible
                try:
                    if isinstance(body, (dict, list)):
                        import json
                        formatted_body = json.dumps(body, indent=2)
                        for line in formatted_body.split('\n'):
                            click.echo(f"      {line}", err=True)
                    else:
                        click.echo(f"      {body_str}", err=True)
                except Exception:
                    click.echo(f"      {body_str}", err=True)


def log_api_response(status_code: int, response_data: Any = None, duration: float = None):
    """
    Log an API response with enhanced verbose information and timing.
    
    Args:
        status_code: HTTP status code
        response_data: Response data (will be truncated if large)
        duration: Request duration in seconds
    """
    logger = get_logger()
    
    # Enhanced logging with timestamp and performance info
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    duration_str = f" ({duration:.3f}s)" if duration else ""
    performance_info = ""
    
    if duration:
        if duration < 0.1:
            performance_info = " ⚡ Fast"
        elif duration < 0.5:
            performance_info = " 🟢 Good"
        elif duration < 2.0:
            performance_info = " 🟡 Slow"
        else:
            performance_info = " 🔴 Very Slow"
    
    logger.debug(f"[{timestamp}] API Response: {status_code}{duration_str}{performance_info}")
    
    if response_data:
        # Log full response to file (with truncation for very large responses)
        response_str = str(response_data)
        if len(response_str) > 10000:
            logger.debug(f"[{timestamp}] Response body: {response_str[:10000]}... (truncated, full length: {len(response_str)} chars)")
        else:
            logger.debug(f"[{timestamp}] Response body: {response_str}")
    
    if _is_verbose_mode():
        # Color-coded status codes
        if 200 <= status_code < 300:
            status_color = 'green'
            status_icon = '✓'
        elif 300 <= status_code < 400:
            status_color = 'yellow'
            status_icon = '↻'
        elif 400 <= status_code < 500:
            status_color = 'red'
            status_icon = '✗'
        else:
            status_color = 'magenta'
            status_icon = '⚠'
        
        duration_display = f" {click.style(f'({duration:.3f}s)', fg='cyan')}" if duration else ""
        click.echo(f"🔄 [{timestamp}] API Response: {status_icon} {click.style(str(status_code), fg=status_color, bold=True)}{duration_display}{performance_info}", err=True)
        
        # Enhanced response data display
        if response_data:
            response_str = str(response_data)
            response_size = len(response_str)
            
            if response_size > 1000:
                click.echo(f"   📄 Response ({response_size} chars, truncated for display):", err=True)
                click.echo(f"      {response_str[:1000]}...", err=True)
                click.echo(f"      {click.style('(See log file for complete response)', fg='yellow', dim=True)}", err=True)
            else:
                click.echo(f"   📄 Response ({response_size} chars):", err=True)
                # Pretty print JSON if possible
                try:
                    if isinstance(response_data, (dict, list)):
                        import json
                        formatted_response = json.dumps(response_data, indent=2)
                        for line in formatted_response.split('\n'):
                            click.echo(f"      {line}", err=True)
                    else:
                        click.echo(f"      {response_str}", err=True)
                except Exception:
                    click.echo(f"      {response_str}", err=True)
        
        # Add timing analysis for performance insights
        if duration:
            if duration > 2.0:
                click.echo(f"   ⏱️  {click.style('Performance Note:', fg='yellow')} Request took {duration:.3f}s - consider checking network or API performance", err=True)
            elif duration < 0.1:
                click.echo(f"   ⚡ {click.style('Performance Note:', fg='green')} Excellent response time: {duration:.3f}s", err=True)


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
        click.secho(f"⚠ Warning: {message}", fg='yellow', err=True)


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


def log_operation_timing(operation_name: str, duration: float, details: Dict[str, Any] = None):
    """
    Log operation timing information with performance analysis.
    
    Args:
        operation_name: Name of the operation being timed
        duration: Operation duration in seconds
        details: Additional timing details (e.g., sub-operations, API calls)
    """
    logger = get_logger()
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    
    # Log to file with full details
    logger.info(f"[{timestamp}] Operation '{operation_name}' completed in {duration:.3f}s")
    if details:
        logger.debug(f"[{timestamp}] Operation details: {details}")
    
    if _is_verbose_mode():
        # Performance categorization
        if duration < 0.5:
            perf_icon = "⚡"
            perf_color = "green"
            perf_note = "Fast"
        elif duration < 2.0:
            perf_icon = "🟢"
            perf_color = "green"
            perf_note = "Good"
        elif duration < 5.0:
            perf_icon = "🟡"
            perf_color = "yellow"
            perf_note = "Moderate"
        else:
            perf_icon = "🔴"
            perf_color = "red"
            perf_note = "Slow"
        
        click.echo(f"\n⏱️  [{timestamp}] Operation: {click.style(operation_name, fg='cyan', bold=True)}", err=True)
        click.echo(f"   Duration: {perf_icon} {click.style(f'{duration:.3f}s', fg=perf_color, bold=True)} ({perf_note})", err=True)
        
        # Show breakdown if details provided
        if details:
            if 'api_calls' in details:
                api_count = details['api_calls']
                click.echo(f"   API Calls: {click.style(str(api_count), fg='blue')} requests", err=True)
            
            if 'phases' in details:
                click.echo(f"   Phase Breakdown:", err=True)
                for phase, phase_duration in details['phases'].items():
                    percentage = (phase_duration / duration) * 100 if duration > 0 else 0
                    click.echo(f"     • {phase}: {phase_duration:.3f}s ({percentage:.1f}%)", err=True)


def log_auth_diagnostic(auth_type: str, status: str, details: Dict[str, Any] = None, error: Exception = None):
    """
    Log authentication diagnostic information for troubleshooting.
    
    Args:
        auth_type: Type of authentication (cognito, override_token, etc.)
        status: Authentication status (success, failure, retry, etc.)
        details: Diagnostic details (config, token info, etc.)
        error: Exception if authentication failed
    """
    logger = get_logger()
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    
    # Log to file with full diagnostic information
    logger.info(f"[{timestamp}] Authentication: {auth_type} - {status}")
    if details:
        # Filter sensitive information for logging
        safe_details = {}
        for key, value in details.items():
            if key.lower() in ['password', 'token', 'secret', 'access_token', 'refresh_token', 'id_token']:
                if isinstance(value, str) and len(value) > 10:
                    safe_details[key] = f"{value[:4]}...{value[-4:]}"
                else:
                    safe_details[key] = '***REDACTED***'
            else:
                safe_details[key] = value
        logger.debug(f"[{timestamp}] Auth details: {safe_details}")
    
    if error:
        logger.error(f"[{timestamp}] Auth error: {error}", exc_info=True)
    
    if _is_verbose_mode():
        # Status-based icons and colors
        if status.lower() in ['success', 'authenticated', 'valid']:
            status_icon = "✓"
            status_color = "green"
        elif status.lower() in ['failure', 'failed', 'invalid', 'expired']:
            status_icon = "✗"
            status_color = "red"
        elif status.lower() in ['retry', 'refreshing', 'attempting']:
            status_icon = "↻"
            status_color = "yellow"
        else:
            status_icon = "ℹ"
            status_color = "blue"
        
        click.echo(f"\n🔐 [{timestamp}] Authentication: {click.style(auth_type.upper(), fg='cyan', bold=True)}", err=True)
        click.echo(f"   Status: {status_icon} {click.style(status, fg=status_color, bold=True)}", err=True)
        
        # Show diagnostic details
        if details:
            click.echo(f"   📊 Diagnostic Information:", err=True)
            
            # Configuration details
            if 'config' in details:
                config = details['config']
                click.echo(f"     • API Gateway: {click.style(config.get('api_gateway_url', 'Not configured'), fg='blue')}", err=True)
                if 'amplify_config' in config:
                    amplify = config['amplify_config']
                    click.echo(f"     • Region: {click.style(amplify.get('region', 'Unknown'), fg='blue')}", err=True)
                    click.echo(f"     • User Pool: {click.style(amplify.get('cognitoUserPoolId', 'Not configured'), fg='blue')}", err=True)
                    click.echo(f"     • Client ID: {click.style(amplify.get('cognitoAppClientId', 'Not configured'), fg='blue')}", err=True)
            
            # Profile information
            if 'profile_name' in details:
                click.echo(f"     • Profile: {click.style(details['profile_name'], fg='magenta')}", err=True)
            
            # Token information (redacted)
            if 'token_type' in details:
                click.echo(f"     • Token Type: {click.style(details['token_type'], fg='yellow')}", err=True)
            
            if 'expires_at' in details:
                try:
                    expires_at = details['expires_at']
                    if isinstance(expires_at, (int, float)):
                        expires_dt = datetime.fromtimestamp(expires_at)
                        now = datetime.now()
                        if expires_dt > now:
                            time_left = expires_dt - now
                            click.echo(f"     • Token Expires: {click.style(f'in {time_left}', fg='green')}", err=True)
                        else:
                            time_ago = now - expires_dt
                            click.echo(f"     • Token Expires: {click.style(f'{time_ago} ago (EXPIRED)', fg='red')}", err=True)
                except Exception:
                    click.echo(f"     • Token Expires: {click.style('Invalid timestamp', fg='red')}", err=True)
            
            # Feature switches
            if 'feature_switches' in details:
                fs = details['feature_switches']
                if isinstance(fs, dict) and 'enabled' in fs:
                    enabled_count = len(fs['enabled']) if fs['enabled'] else 0
                    click.echo(f"     • Feature Switches: {click.style(f'{enabled_count} enabled', fg='cyan')}", err=True)
            
            # User information
            if 'user_id' in details:
                click.echo(f"     • User ID: {click.style(details['user_id'], fg='green')}", err=True)
        
        # Show error information
        if error:
            click.echo(f"   ❌ Error Details:", err=True)
            click.echo(f"     • Type: {click.style(type(error).__name__, fg='red')}", err=True)
            click.echo(f"     • Message: {click.style(str(error), fg='red')}", err=True)


def log_config_diagnostic(config: Dict[str, Any], profile_name: str = None):
    """
    Log configuration diagnostic information for troubleshooting.
    
    Args:
        config: Configuration dictionary
        profile_name: Name of the current profile
    """
    logger = get_logger()
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    
    # Filter sensitive config values for logging
    safe_config = {}
    for key, value in config.items():
        if key.lower() in ['password', 'token', 'secret', 'key']:
            safe_config[key] = '***REDACTED***'
        else:
            safe_config[key] = value
    
    logger.debug(f"[{timestamp}] Configuration diagnostic: {safe_config}")
    
    if _is_verbose_mode():
        click.echo(f"\n⚙️  [{timestamp}] Configuration Diagnostic", err=True)
        if profile_name:
            click.echo(f"   Profile: {click.style(profile_name, fg='magenta', bold=True)}", err=True)
        
        # API Gateway configuration
        api_url = config.get('api_gateway_url')
        if api_url:
            click.echo(f"   🌐 API Gateway: {click.style(api_url, fg='blue')}", err=True)
        else:
            click.echo(f"   🌐 API Gateway: {click.style('Not configured', fg='red')}", err=True)
        
        # Amplify configuration
        amplify_config = config.get('amplify_config', {})
        if amplify_config:
            click.echo(f"   🔧 Amplify Configuration:", err=True)
            click.echo(f"     • Region: {click.style(amplify_config.get('region', 'Not set'), fg='cyan')}", err=True)
            click.echo(f"     • User Pool ID: {click.style(amplify_config.get('cognitoUserPoolId', 'Not set'), fg='cyan')}", err=True)
            click.echo(f"     • App Client ID: {click.style(amplify_config.get('cognitoAppClientId', 'Not set'), fg='cyan')}", err=True)
            
            # Check for potential issues
            if not amplify_config.get('region'):
                click.echo(f"     ⚠️  {click.style('Warning: Region not configured', fg='yellow')}", err=True)
            if not amplify_config.get('cognitoUserPoolId'):
                click.echo(f"     ⚠️  {click.style('Warning: User Pool ID not configured', fg='yellow')}", err=True)
            if not amplify_config.get('cognitoAppClientId'):
                click.echo(f"     ⚠️  {click.style('Warning: App Client ID not configured', fg='yellow')}", err=True)
        else:
            click.echo(f"   🔧 Amplify Configuration: {click.style('Not available', fg='red')}", err=True)
        
        # Additional configuration items
        other_keys = [k for k in config.keys() if k not in ['api_gateway_url', 'amplify_config']]
        if other_keys:
            click.echo(f"   📋 Additional Configuration:", err=True)
            for key in other_keys:
                value = safe_config[key]
                if isinstance(value, dict):
                    click.echo(f"     • {key}: {click.style(f'{len(value)} items', fg='cyan')}", err=True)
                elif isinstance(value, list):
                    click.echo(f"     • {key}: {click.style(f'{len(value)} items', fg='cyan')}", err=True)
                else:
                    click.echo(f"     • {key}: {click.style(str(value), fg='cyan')}", err=True)


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
