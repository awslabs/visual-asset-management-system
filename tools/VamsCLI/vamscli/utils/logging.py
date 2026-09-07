"""Centralized logging utility for VamsCLI with file logging and verbose mode support."""

import logging
import os
import re
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


REDACTED = '***REDACTED***'

# Key fragments that mark a value as a credential. Compared against the key with separators
# stripped and lowercased, so `refresh_token`, `refreshToken`, and `REFRESH-TOKEN` all match
# `token`.
#
# Deliberately NOT the bare word "key": this CLI logs S3 object keys constantly (`s3Key`,
# `objectKey`, `keyName`, `bucketExistingKey`), and redacting those would make the log useless for
# the debugging it exists for. Credential-bearing key names are spelled out instead.
_SENSITIVE_KEY_FRAGMENTS = (
    'password',
    'passwd',
    'secret',
    'token',          # access_token, refresh_token, id_token, token_override, idToken
    'credential',
    'apikey',         # apiKey, api_key, apiKeyValue
    'authorization',
    'signature',
    'privatekey',
    'cookie',
)

# Value-level backstop for credentials that arrive without a helpful key — for example when a
# response has already been rendered to a string before it reaches a log call. Matches the shapes
# VAMS actually issues rather than attempting to detect secrets generally.
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r'vams_[A-Za-z0-9_\-]{16,}'),                                  # VAMS API key
    re.compile(r'eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+'),  # JWT (Cognito/OIDC)
    re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}'),                       # Bearer header value
    re.compile(r'(?i)(X-Amz-Signature|X-Amz-Security-Token)=[^&\s\'"]+'),     # presigned URL secrets
)

_MAX_REDACT_DEPTH = 12

# A key containing a sensitive fragment but ENDING in one of these describes a credential rather
# than carrying one: `apiKeyId` and `apiKeyName` are identifiers, `tokenType` is "Bearer",
# `credentialsSecretArn` is a pointer to a secret in Secrets Manager, `tokenCount` is a number.
# Redacting those would cost real diagnostic value for no security gain.
#
# The match is on the ending, so it also covers a field added later with one of these suffixes.
# `passwordHash` / `apiKeyHash` deliberately do NOT match and stay redacted — a hash is still
# credential-derived material.
_DESCRIPTOR_SUFFIXES = (
    'id',
    'ids',
    'name',
    'names',
    'arn',
    'arns',
    'type',
    'types',
    'count',
    'expiry',
    'expiration',
    'enabled',
    'status',
)

# A key whose whole normalized form is one of these is a cursor into a paginated read rather than a
# credential: the `--starting-token` / `--next-token` options carried by roughly two dozen command
# groups, and the `startingToken` / `NextToken` they map onto in request parameters and response
# bodies. A cursor is the one value needed to resume or to diagnose a pagination walk, so it stays in
# the clear.
#
# The comparison is against the whole normalized name rather than a fragment, so `access_token`,
# `refresh_token`, `id_token`, `token_override` and a bare `token` (`auth set-override --token`) are
# unaffected, and a cursor spelled some other way stays redacted.
_PAGINATION_CURSOR_KEYS = (
    'startingtoken',
    'nexttoken',
)


def _is_sensitive_key(key: Any) -> bool:
    """True when a mapping key names a value that is itself a credential."""
    if not isinstance(key, str):
        return False
    normalized = re.sub(r'[\s_\-]', '', key).lower()
    if normalized in _PAGINATION_CURSOR_KEYS:
        return False
    if not any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS):
        return False
    # `apiKey`/`token`/`secret` on their own must stay redacted, so only treat a descriptive suffix
    # as decisive when the key is more than the sensitive fragment itself.
    if normalized.endswith(_DESCRIPTOR_SUFFIXES) and normalized not in _SENSITIVE_KEY_FRAGMENTS:
        return False
    return True


# Names that must be redacted but carry no credential FRAGMENT: the fragment list deliberately omits
# the bare word "key" (it is an Amazon S3 object key everywhere else in this CLI), and `authorizer`
# names a token in the API-key payloads.
_EXTRA_SENSITIVE_EXACT_KEYS = ('key', 'authorizer')


def redact_mapping_for_log(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of a flat log mapping with credential-bearing entries replaced.

    One predicate for every per-key log filter in this module. Each site used to keep its own exact
    membership list (`key.lower() in ['password', 'token', 'secret', 'key']`), which missed the
    parameter names the CLI actually declares — `new_password`, `old_password`, `token_override`,
    `access_token` — while looking like it covered them. `_is_sensitive_key` matches on the
    normalized fragment instead and still lets a pagination cursor and a descriptive name through.
    """
    safe: Dict[str, Any] = {}
    for key, value in (mapping or {}).items():
        if (isinstance(key, str) and key.lower() in _EXTRA_SENSITIVE_EXACT_KEYS) \
                or _is_sensitive_key(key):
            safe[key] = REDACTED
        else:
            safe[key] = redact_sensitive(value)
    return safe


def scrub_text(value: str) -> str:
    """Replace credential-shaped substrings in already-rendered text."""
    if not isinstance(value, str) or not value:
        return value
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def redact_sensitive(obj: Any, _depth: int = 0) -> Any:
    """
    Return a copy of ``obj`` with credential values replaced by ``REDACTED``.

    Redacts by key name (recursively through dicts and sequences) and additionally scrubs
    credential-shaped substrings out of any string it passes, so a token still gets masked when it
    arrives inside a message that was already rendered to text.

    Never raises: logging must not be able to fail a command.
    """
    try:
        if _depth > _MAX_REDACT_DEPTH:
            return '***DEPTH_LIMIT***'
        if isinstance(obj, dict):
            return {
                k: (REDACTED if _is_sensitive_key(k) else redact_sensitive(v, _depth + 1))
                for k, v in obj.items()
            }
        if isinstance(obj, (list, tuple, set)):
            rendered = [redact_sensitive(v, _depth + 1) for v in obj]
            return type(obj)(rendered) if isinstance(obj, (list, tuple)) else rendered
        if isinstance(obj, str):
            return scrub_text(obj)
        return obj
    except Exception:
        # A redaction failure must never leak the original value, and must never break the command.
        return '***REDACTION_ERROR***'


def redact_to_text(obj: Any) -> str:
    """Render ``obj`` for a log line with credentials removed."""
    try:
        return scrub_text(str(redact_sensitive(obj)))
    except Exception:
        return '***REDACTION_ERROR***'


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


# Modes for the CLI's own on-disk artefacts. The log file carries redacted payloads but still every
# URL, user id, profile name and error the CLI has seen, and the profile directory carries the live
# refresh token, so both are owner-only. Shared with `utils/profile.py`.
OWNER_ONLY_DIR_MODE = 0o700
OWNER_ONLY_FILE_MODE = 0o600


def restrict_path(path, mode: int):
    """Narrow an existing path to ``mode``.

    Best-effort: Windows reflects only the read-only bit, and a chmod is refused outright on some
    mounted and network filesystems. Neither logging nor a profile write may fail a command over a
    hardening step, so a failure is ignored.
    """
    try:
        os.chmod(path, mode)
    except Exception:
        pass


class _OwnerOnlyRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler whose log files stay readable only by their owner.

    ``_open`` runs on the first open and again after every rollover, so the mode is reapplied to
    each freshly created ``vamscli.log`` rather than once at startup. ``doRollover`` additionally
    narrows the renamed backups: a rename carries the mode over on POSIX, but the CLI keeps up to
    ``LOG_BACKUP_COUNT`` of them and the narrowing is stated for each one explicitly.
    """

    def _open(self):
        stream = super()._open()
        restrict_path(self.baseFilename, OWNER_ONLY_FILE_MODE)
        return stream

    def doRollover(self):
        super().doRollover()
        for index in range(1, (self.backupCount or 0) + 1):
            backup = f"{self.baseFilename}.{index}"
            if os.path.exists(backup):
                restrict_path(backup, OWNER_ONLY_FILE_MODE)


def get_log_dir() -> Path:
    """Get the global logs directory path."""
    return get_config_dir() / LOG_DIR_NAME


def ensure_log_dir():
    """Ensure the logs directory exists and is readable only by its owner."""
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True, mode=OWNER_ONLY_DIR_MODE)
    # `mode` applies only to a directory this call creates, so an already existing one — every
    # machine that has run the CLI before — is narrowed here.
    restrict_path(log_dir, OWNER_ONLY_DIR_MODE)


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
    file_handler = _OwnerOnlyRotatingFileHandler(
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
    """Check if verbose mode is enabled.

    Reads only the module global, which `initialize_logging()` sets from Click's parsed `--verbose`
    (`main.py` registers the option and calls it on every invocation). Scanning `sys.argv` for the
    literal instead would treat the string anywhere on the line as a request — including as an
    option VALUE, so `vamscli search assets -q "--verbose"` turned on full request/response logging
    — and would make any process whose argv happens to carry it verbose for its whole lifetime.
    """
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
    
    # Filter sensitive arguments. `args` is the Click kwargs of the command, so the keys are its own
    # option names: the shared predicate masks a credential option (`password`, `new_password`,
    # `token_override`) and leaves a pagination cursor such as `starting_token` readable.
    safe_args = redact_mapping_for_log(args) if args else {}

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
    
    # Filter sensitive headers through the shared predicate: `X-Api-Key` normalizes to `apikey` and
    # `Authorization`/`Cookie` match their own fragments, so an added credential header (say
    # `X-Amz-Security-Token`) is covered without editing a list here.
    safe_headers = redact_mapping_for_log(headers) if headers else {}

    # Enhanced logging with timestamp
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    logger.debug(f"[{timestamp}] API Request: {method} {url}")
    if safe_headers:
        logger.debug(f"[{timestamp}] Request headers: {safe_headers}")
    if body:
        # Log the request body to file with credentials removed. The log file is a rotating on-disk
        # artifact, so a credential-shaped value in a body would persist well past the command.
        body_str = redact_to_text(body)
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
        # Log the response body to file with credentials removed. An `api-key create` response
        # carries the one-time plaintext key and every upload/download response carries presigned
        # Amazon S3 URLs, so an unredacted body would persist a live credential in a rotating
        # on-disk artifact well past the command.
        response_str = redact_to_text(response_data)
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
    safe_config = redact_mapping_for_log(config)

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
        # Filter sensitive information for logging. Fully redacted rather than previewed as
        # first-four/last-four: the widened predicate now also catches `password`-bearing names, and
        # a preview of a password leaks more than it diagnoses. `tokenType` and `*_expiry` still
        # survive, which is what identifies which credential was in play.
        safe_details = redact_mapping_for_log(details)
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
    safe_config = redact_mapping_for_log(config)

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
