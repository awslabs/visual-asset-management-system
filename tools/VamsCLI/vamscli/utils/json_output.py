"""JSON output utilities for VamsCLI commands.

This module provides utilities to ensure pure JSON output when --json-output flag is used.
All commands with --json-output parameter should use these utilities to prevent
status messages and CLI formatting from polluting JSON output.
"""

import json
import sys
from typing import Any, Dict, Optional, Callable
import click

# Import logging for file-only logging (not console)
from .logging import log_debug, log_info, log_warning, log_error


def output_result(result: Any, json_output: bool, success_message: Optional[str] = None,
                 cli_formatter: Optional[Callable[[Any], str]] = None) -> None:
    """
    Output command result in JSON or CLI format.
    
    When json_output is True, outputs ONLY valid JSON to stdout.
    When json_output is False, outputs CLI-friendly formatted text.
    
    Args:
        result: The result data to output
        json_output: Whether to output as JSON (True) or CLI format (False)
        success_message: Success message for CLI output (ignored in JSON mode)
        cli_formatter: Optional function to format CLI output (ignored in JSON mode)
    
    Examples:
        # Simple JSON/CLI output
        output_result(result, json_output)
        
        # With success message for CLI
        output_result(result, json_output, success_message="✓ Operation successful!")
        
        # With custom CLI formatter
        output_result(
            result, 
            json_output,
            success_message="✓ Asset created!",
            cli_formatter=lambda r: f"Asset ID: {r.get('assetId')}"
        )
    """
    # Log to file (not console - console output handled below)
    try:
        result_str = str(result)
        if len(result_str) > 1000:
            log_info(f"output_result: json_output={json_output}, result (truncated): {result_str[:1000]}...", False)
        else:
            log_info(f"output_result: json_output={json_output}, result: {result_str}", False)
        if success_message:
            log_info(f"output_result: success_message='{success_message}'", False)
    except Exception:
        # Don't fail if logging fails
        pass
    
    if json_output:
        # Pure JSON output only
        click.echo(json.dumps(result, indent=2))
    else:
        # CLI-friendly output
        if success_message:
            click.secho(success_message, fg='green', bold=True)
        
        if cli_formatter:
            formatted_output = cli_formatter(result)
            if formatted_output:
                click.echo(formatted_output)
        elif isinstance(result, dict):
            # Default formatting for dict results
            for key, value in result.items():
                if isinstance(value, (dict, list)):
                    click.echo(f"{key}: {json.dumps(value, indent=2)}")
                else:
                    click.echo(f"{key}: {value}")
        elif isinstance(result, list):
            # Default formatting for list results
            for item in result:
                click.echo(f"  - {item}")
        else:
            # Default formatting for other types
            click.echo(str(result))


def output_error(error: Exception, json_output: bool, 
                error_type: str = "Error", helpful_message: Optional[str] = None) -> None:
    """
    Output error in JSON or CLI format.
    
    When json_output is True, outputs error as JSON object to stdout.
    When json_output is False, outputs styled error message to stderr.
    
    Args:
        error: The exception that occurred
        json_output: Whether to output as JSON (True) or CLI format (False)
        error_type: Type of error for CLI display (ignored in JSON mode)
        helpful_message: Additional helpful message for CLI output (ignored in JSON mode)
    
    Examples:
        # Simple error output
        output_error(e, json_output)
        
        # With error type and helpful message for CLI
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message="Use 'vamscli assets list' to see available assets."
        )
    """
    # Log to file (not console - console output handled below)
    try:
        log_error(f"output_error: json_output={json_output}, error_type='{error_type}', error='{error}', helpful_message='{helpful_message}'")
    except Exception:
        # Don't fail if logging fails
        pass
    
    if json_output:
        # Pure JSON error output
        error_data = {
            "error": str(error),
            "error_type": error.__class__.__name__
        }
        click.echo(json.dumps(error_data, indent=2))
    else:
        # CLI-friendly error output to stderr
        click.secho(f"✗ {error_type}: {error}", fg='red', bold=True, err=True)
        if helpful_message:
            click.echo(helpful_message, err=True)


def output_status(message: str, json_output: bool) -> None:
    """
    Output status message only in CLI mode.
    
    When json_output is True, this function does nothing (no output).
    When json_output is False, outputs the status message.
    
    This ensures status messages don't pollute JSON output.
    
    Args:
        message: Status message to display
        json_output: Whether JSON output is enabled (suppresses message if True)
    
    Examples:
        # Status message that only appears in CLI mode
        output_status("Processing request...", json_output)
        output_status("Retrieving databases...", json_output)
        output_status(f"Creating asset '{asset_id}'...", json_output)
    """
    # Log to file (not console - console output handled below)
    try:
        log_info(f"output_status: json_output={json_output}, message='{message}'", False)
    except Exception:
        # Don't fail if logging fails
        pass
    
    if not json_output:
        click.echo(message)


def output_warning(message: str, json_output: bool) -> None:
    """
    Output warning message only in CLI mode.
    
    When json_output is True, this function does nothing (no output).
    When json_output is False, outputs the warning message in yellow.
    
    Args:
        message: Warning message to display
        json_output: Whether JSON output is enabled (suppresses message if True)
    
    Examples:
        output_warning("⚠️  This action cannot be undone!", json_output)
        output_warning("Database must not contain active assets.", json_output)
    """
    # Log to file (not console - console output handled below)
    try:
        log_warning(f"output_warning: json_output={json_output}, message='{message}'", False)
    except Exception:
        # Don't fail if logging fails
        pass
    
    if not json_output:
        click.secho(message, fg='yellow', bold=True)


def output_info(message: str, json_output: bool) -> None:
    """
    Output informational message only in CLI mode.
    
    When json_output is True, this function does nothing (no output).
    When json_output is False, outputs the informational message in cyan.
    
    Args:
        message: Informational message to display
        json_output: Whether JSON output is enabled (suppresses message if True)
    
    Examples:
        output_info("Use --help for more information.", json_output)
        output_info("Pagination available with --starting-token.", json_output)
    """
    # Log to file (not console - console output handled below)
    try:
        log_debug(f"output_info: json_output={json_output}, message='{message}'")
    except Exception:
        # Don't fail if logging fails
        pass
    
    if not json_output:
        click.secho(message, fg='cyan')

