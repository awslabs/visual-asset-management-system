"""Main CLI entry point for VamsCLI."""

import sys
from typing import Optional

import click

from .commands.setup import setup
from .commands.auth import auth
from .commands.assets import assets
from .commands.asset_version import asset_version
from .commands.asset_links import asset_links
from .commands.file import file
from .commands.profile import profile
from .commands.database import database
from .commands.tag import tag
from .commands.tag_type import tag_type
from .commands.metadata import metadata
from .commands.metadata_schema import metadata_schema
from .commands.features import features
from .commands.search import search
from .commands.workflow import workflow
from .commands.industry import industry
from .commands.user import user
from .commands.roleUserConstraints import role
from .utils.profile import ProfileManager
from .utils.exceptions import SetupRequiredError
from .utils.global_exceptions import handle_global_exceptions
from .constants import DEFAULT_PROFILE_NAME
from .version import get_version
from .utils.logging import initialize_logging, set_context


def check_setup_required(ctx: click.Context, param: click.Parameter, value: Optional[str]) -> Optional[str]:
    """Check if setup is required before running commands."""
    # Skip setup check if we're in a test environment
    if 'pytest' in sys.modules:
        return value
    
    # Skip setup check for setup command itself
    if 'setup' in sys.argv:
        return value
    
    # Allow help commands and version commands without setup check
    if (ctx.info_name in ['setup', 'version', 'help'] or 
        ctx.get_parameter_source('help') == click.core.ParameterSource.COMMANDLINE or
        '--help' in sys.argv or '-h' in sys.argv or
        any(arg in ['--help', '-h'] for arg in sys.argv)):
        return value
    
    # Skip setup check for help commands on subcommands (more comprehensive check)
    for i, arg in enumerate(sys.argv):
        if arg in ['--help', '-h']:
            return value
    
    # Skip setup check for version commands
    if '--version' in sys.argv or ctx.info_name == 'version':
        return value
    
    # Get profile name from context if available
    profile_name = DEFAULT_PROFILE_NAME
    if ctx.obj and 'profile_name' in ctx.obj:
        profile_name = ctx.obj['profile_name']
    
    profile_manager = ProfileManager(profile_name)
    if not profile_manager.has_config():
        raise SetupRequiredError(
            f"Setup required for profile '{profile_name}'. Please run 'vamscli setup <api-gateway-url> --profile {profile_name}' first."
        )
    
    return value


def handle_profile_option(ctx: click.Context, param: click.Parameter, value: Optional[str]) -> Optional[str]:
    """Handle global profile option."""
    if value is None:
        value = DEFAULT_PROFILE_NAME
    
    # Validate profile name
    from .constants import validate_profile_name
    if not validate_profile_name(value):
        raise click.BadParameter(
            f"Invalid profile name '{value}'. Profile names must be 3-50 characters, "
            "alphanumeric with hyphens and underscores only."
        )
    
    # Ensure context object exists
    if ctx.obj is None:
        ctx.obj = {}
    ctx.obj['profile_name'] = value
    
    return value


@click.group(invoke_without_command=True)
@click.option('--version', is_flag=True, help='Show version information')
@click.option('--verbose', is_flag=True, help='Enable verbose output with detailed error information, API requests/responses, and timing')
@click.option('--profile', 
              default=DEFAULT_PROFILE_NAME,
              callback=handle_profile_option,
              expose_value=False,
              help=f'Profile name to use (default: {DEFAULT_PROFILE_NAME})')
@click.option('--setup-check', is_flag=True, hidden=True, callback=check_setup_required, expose_value=False, is_eager=False)
@click.pass_context
@handle_global_exceptions()
def cli(ctx: click.Context, version: bool, verbose: bool):
    """
    VamsCLI - Command Line Interface for Visual Asset Management System (VAMS).
    
    VamsCLI provides a command-line interface to interact with your VAMS deployment
    running on AWS. It supports authentication, configuration management, and API
    operations.
    
    Getting Started:
    1. Run 'vamscli setup <api-gateway-url>' to configure the CLI
    2. Run 'vamscli auth login -u <username>' to authenticate
    3. Use 'vamscli --help' to see available commands
    
    Examples:
        vamscli setup https://api.example.com
        vamscli auth login -u john.doe@example.com
        vamscli auth status
        vamscli --verbose assets list  # Run with verbose output
    """
    # Initialize logging system (wrapped in try/catch to prevent logging failures from breaking CLI)
    try:
        initialize_logging(verbose)
        
        # Set profile context for logging
        profile_name = ctx.obj.get('profile_name', DEFAULT_PROFILE_NAME) if ctx.obj else DEFAULT_PROFILE_NAME
        set_context(profile_name=profile_name)
    except Exception:
        # Don't fail if logging initialization fails
        pass
    
    # Store verbose flag in context for all commands
    if ctx.obj is None:
        ctx.obj = {}
    ctx.obj['verbose'] = verbose
    
    if version:
        click.echo(f"VamsCLI version {get_version()}")
        return
    
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# Add commands to the CLI group
cli.add_command(setup)
cli.add_command(auth)
cli.add_command(assets)
cli.add_command(asset_version)
cli.add_command(asset_links)
cli.add_command(file)
cli.add_command(profile)
cli.add_command(database)
cli.add_command(tag)
cli.add_command(tag_type)
cli.add_command(metadata)
cli.add_command(metadata_schema)
cli.add_command(features)
cli.add_command(search)
cli.add_command(workflow)
cli.add_command(industry)
cli.add_command(user)
cli.add_command(role)


@cli.command()
def version():
    """Show version information."""
    click.echo(f"VamsCLI version {get_version()}")


@handle_global_exceptions()
def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == '__main__':
    main()