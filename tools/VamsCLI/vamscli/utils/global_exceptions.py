"""Global exception handler for VamsCLI."""

import sys
import time
import click


def _is_json_output() -> bool:
    """Check if JSON output is requested in command arguments."""
    return '--json-output' in sys.argv


def _global_error_data(error_type: str, error_message: str, additional_info=None, exc=None) -> dict:
    """The --json-output document of a global error, the same under pytest and in production."""
    error_data = {"error": f"{error_type}: {error_message}"}
    if exc is not None:
        error_data["error_type"] = type(exc).__name__
        # Machine-readable fields an exception names of its own; they never replace the two above.
        fields = getattr(exc, "json_fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                error_data.setdefault(key, value)
    if additional_info:
        if isinstance(additional_info, list):
            error_data["additional_info"] = additional_info
        else:
            error_data["additional_info"] = [additional_info]
    return error_data


def _handle_global_error(error_type: str, error_message: str, additional_info=None, exc=None):
    """Handle global errors with JSON output support."""
    # In test environment, output error messages and raise ClickException with full message
    if 'pytest' in sys.modules:
        if _is_json_output():
            import json
            error_data = _global_error_data(error_type, error_message, additional_info, exc)
            error_output = json.dumps(error_data, indent=2)
            click.echo(error_output)
            raise click.ClickException(error_output)
        else:
            # Format error message for test capture - output to stdout for test capture
            formatted_message = f"✗ {error_type}: {error_message}"
            click.echo(formatted_message)  # Use stdout instead of stderr for test capture
            if additional_info:
                if isinstance(additional_info, list):
                    for info in additional_info:
                        click.echo(info)
                else:
                    click.echo(additional_info)
            raise click.ClickException(formatted_message)
    
    # Production environment - output and exit
    if _is_json_output():
        import json
        error_data = _global_error_data(error_type, error_message, additional_info, exc)
        click.echo(json.dumps(error_data, indent=2))
    else:
        click.echo(
            click.style(f"✗ {error_type}: {error_message}", fg='red', bold=True),
            err=True
        )
        if additional_info:
            if isinstance(additional_info, list):
                for info in additional_info:
                    click.echo(info)
            else:
                click.echo(additional_info)
    
    sys.exit(1)


def handle_global_exceptions():
    """
    Streamlined global exception handler for infrastructure concerns only.
    
    This handler only catches global infrastructure exceptions that should be
    handled consistently across all commands. Command-specific business logic
    exceptions are handled by individual commands.
    
    Supports JSON output formatting when --json-output flag is detected in arguments.
    Enhanced with comprehensive logging for all exceptions and command execution.
    """
    def decorator(func):
        import functools
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Import logging utilities
            from .logging import (
                get_logger, log_command_start, log_command_end,
                log_error, log_debug, redact_to_text, _is_verbose_mode
            )
            
            logger = get_logger()
            start_time = time.time()
            command_name = func.__name__
            
            # Log command start with kwargs
            try:
                log_command_start(command_name, kwargs)
            except Exception:
                # Don't fail if logging fails
                pass
            
            try:
                # Execute command
                result = func(*args, **kwargs)
                
                # Log successful completion with result
                duration = time.time() - start_time
                try:
                    log_command_end(command_name, True, duration)
                    # Log result (truncate if too large). The wrapped callable's return value is the
                    # API response body for most commands, so it is redacted before it reaches the
                    # rotating log file (Rule 10).
                    result_str = redact_to_text(result)
                    if len(result_str) > 1000:
                        log_debug(f"Global handler: Command '{command_name}' returned result (truncated): {result_str[:1000]}...")
                    else:
                        log_debug(f"Global handler: Command '{command_name}' returned result: {result_str}")
                except Exception:
                    pass
                
                return result
                
            except Exception as e:
                # Log exception with full details
                duration = time.time() - start_time
                try:
                    log_error(f"Exception in {command_name}", e)
                    log_command_end(command_name, False, duration)
                except Exception:
                    # Don't fail if logging fails
                    pass
                
                # Display verbose error details if requested
                verbose = _is_verbose_mode()
                if verbose and not isinstance(e, click.ClickException):
                    import traceback
                    click.echo("\n" + "="*60, err=True)
                    click.echo("VERBOSE ERROR DETAILS:", err=True)
                    click.echo("="*60, err=True)
                    click.echo(traceback.format_exc(), err=True)
                    click.echo("="*60 + "\n", err=True)
                # Import here to avoid circular imports
                from .exceptions import (
                    SetupRequiredError, AuthenticationError, APIUnavailableError, ProfileError,
                    InvalidProfileNameError, ConfigurationError, OverrideTokenError, VersionMismatchError,
                    RetryExhaustedError, RateLimitExceededError, TokenExpiredError, PermissionDeniedError,
                    RequestBlockedError, APIError
                )
                from ..constants import WAF_ADMIN_HINT, WAF_OR_STAGE_ADMIN_HINT
                
                if isinstance(e, SetupRequiredError):
                    _handle_global_error("Setup Required", str(e), "Run 'vamscli setup <api-gateway-url>' to get started.", exc=e)
                elif isinstance(e, RequestBlockedError):
                    info = (WAF_ADMIN_HINT if e.confirmed else WAF_OR_STAGE_ADMIN_HINT) + (f" Request ID: {e.request_id}." if e.request_id else "")
                    _handle_global_error("Request Blocked", str(e), info, exc=e)
                elif isinstance(e, AuthenticationError):
                    _handle_global_error("Authentication Error", str(e), "Run 'vamscli auth login' to re-authenticate.", exc=e)
                elif isinstance(e, APIError):
                    _handle_global_error("API Error", str(e), exc=e)
                elif isinstance(e, APIUnavailableError):
                    _handle_global_error("API Unavailable", str(e), exc=e)
                elif isinstance(e, ProfileError):
                    _handle_global_error("Profile Error", str(e), exc=e)
                elif isinstance(e, InvalidProfileNameError):
                    _handle_global_error("Invalid Profile Name", str(e), exc=e)
                elif isinstance(e, ConfigurationError):
                    _handle_global_error("Configuration Error", str(e), exc=e)
                elif isinstance(e, OverrideTokenError):
                    _handle_global_error("Override Token Error", str(e), exc=e)
                elif isinstance(e, VersionMismatchError):
                    _handle_global_error("Version Mismatch", str(e), exc=e)
                elif isinstance(e, RetryExhaustedError):
                    additional_info = [
                        "The API is currently throttling requests. You can:",
                        "• Wait a few minutes and try again",
                        "• Adjust retry settings with environment variables:",
                        "  - VAMS_CLI_MAX_RETRY_ATTEMPTS (default: 5)",
                        "  - VAMS_CLI_INITIAL_RETRY_DELAY (default: 1.0)",
                        "  - VAMS_CLI_MAX_RETRY_DELAY (default: 60.0)"
                    ]
                    _handle_global_error("Rate Limit Exceeded", str(e), additional_info, exc=e)
                elif isinstance(e, RateLimitExceededError):
                    _handle_global_error("Rate Limit Exceeded", str(e), exc=e)
                elif isinstance(e, TokenExpiredError):
                    _handle_global_error("Token Expired", str(e), "Your authentication token has expired. Run 'vamscli auth login' to re-authenticate.", exc=e)
                elif isinstance(e, PermissionDeniedError):
                    _handle_global_error("Permission Denied", str(e), "You do not have permission to perform this action. Contact your administrator if you believe this is an error.", exc=e)
                elif isinstance(e, click.ClickException):
                    # Let Click handle its own exceptions
                    raise
                # An interrupt cannot arrive here: KeyboardInterrupt derives from BaseException, so
                # `except Exception` above never binds it. Click converts it to `Abort` under
                # standalone_mode=False, and `main()` is the only wrapper that sees it — the
                # cancellation payload is emitted there.
                else:
                    # Unexpected error
                    if _is_json_output():
                        import json
                        error_data = {"error": f"Unexpected error: {e}"}
                        if '--debug' in sys.argv:
                            import traceback
                            error_data["traceback"] = traceback.format_exc()
                        click.echo(json.dumps(error_data, indent=2))
                    else:
                        click.echo(
                            click.style(f"✗ Unexpected error: {e}", fg='red', bold=True),
                            err=True
                        )
                        if '--debug' in sys.argv:
                            import traceback
                            traceback.print_exc()
                    sys.exit(1)
        return wrapper
    return decorator
