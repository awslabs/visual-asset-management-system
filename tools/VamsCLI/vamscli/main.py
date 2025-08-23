"""Main CLI entry point for VamsCLI."""

import sys
from typing import Optional

import click

from .commands.setup import setup
from .commands.auth import auth
from .commands.assets import assets
from .commands.asset_version import asset_version
from .commands.asset_links import asset_links
from .commands.asset_links_metadata import asset_links_metadata
from .commands.file import file
from .commands.profile import profile
from .commands.database import database
from .commands.tag import tag
from .commands.tag_type import tag_type
from .commands.metadata import metadata
from .commands.metadata_schema import metadata_schema
from .commands.features import features
from .commands.search import search
from .utils.profile import ProfileManager
from .utils.exceptions import (
    VamsCLIError, SetupRequiredError, OverrideTokenError, APIUnavailableError,
    ProfileError, InvalidProfileNameError, AssetAlreadyArchivedError, AssetDeletionError,
    DatabaseNotFoundError, DatabaseAlreadyExistsError, DatabaseDeletionError,
    BucketNotFoundError, InvalidDatabaseDataError, TagNotFoundError, TagAlreadyExistsError,
    TagTypeNotFoundError, TagTypeAlreadyExistsError, TagTypeInUseError,
    InvalidTagDataError, InvalidTagTypeDataError, AssetVersionError, AssetVersionNotFoundError,
    AssetVersionOperationError, InvalidAssetVersionDataError, AssetVersionRevertError,
    AssetLinkError, AssetLinkNotFoundError, AssetLinkValidationError, AssetLinkPermissionError,
    CycleDetectionError, AssetLinkAlreadyExistsError, InvalidRelationshipTypeError, AssetLinkOperationError,
    SearchError, SearchDisabledError, SearchUnavailableError, InvalidSearchParametersError,
    SearchQueryError, SearchMappingError
)
from .constants import DEFAULT_PROFILE_NAME
from .version import get_version


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
@click.option('--profile', 
              default=DEFAULT_PROFILE_NAME,
              callback=handle_profile_option,
              expose_value=False,
              help=f'Profile name to use (default: {DEFAULT_PROFILE_NAME})')
@click.option('--setup-check', is_flag=True, hidden=True, callback=check_setup_required, expose_value=False, is_eager=False)
@click.pass_context
def cli(ctx: click.Context, version: bool):
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
    """
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
cli.add_command(asset_links_metadata)
cli.add_command(file)
cli.add_command(profile)
cli.add_command(database)
cli.add_command(tag)
cli.add_command(tag_type)
cli.add_command(metadata)
cli.add_command(metadata_schema)
cli.add_command(features)
cli.add_command(search)


@cli.command()
def version():
    """Show version information."""
    click.echo(f"VamsCLI version {get_version()}")


def handle_exceptions():
    """Global exception handler for the CLI."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except SetupRequiredError as e:
                click.echo(
                    click.style(f"✗ Setup Required: {e}", fg='red', bold=True),
                    err=True
                )
                click.echo("Run 'vamscli setup <api-gateway-url>' to get started.")
                sys.exit(1)
            except OverrideTokenError as e:
                click.echo(
                    click.style(f"✗ Override Token Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except APIUnavailableError as e:
                click.echo(
                    click.style(f"✗ API Unavailable: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetAlreadyArchivedError as e:
                click.echo(
                    click.style(f"✗ Asset Already Archived: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetDeletionError as e:
                click.echo(
                    click.style(f"✗ Asset Deletion Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except DatabaseNotFoundError as e:
                click.echo(
                    click.style(f"✗ Database Not Found: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except DatabaseAlreadyExistsError as e:
                click.echo(
                    click.style(f"✗ Database Already Exists: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except DatabaseDeletionError as e:
                click.echo(
                    click.style(f"✗ Database Deletion Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except BucketNotFoundError as e:
                click.echo(
                    click.style(f"✗ Bucket Not Found: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except InvalidDatabaseDataError as e:
                click.echo(
                    click.style(f"✗ Invalid Database Data: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except TagNotFoundError as e:
                click.echo(
                    click.style(f"✗ Tag Not Found: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except TagAlreadyExistsError as e:
                click.echo(
                    click.style(f"✗ Tag Already Exists: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except TagTypeNotFoundError as e:
                click.echo(
                    click.style(f"✗ Tag Type Not Found: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except TagTypeAlreadyExistsError as e:
                click.echo(
                    click.style(f"✗ Tag Type Already Exists: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except TagTypeInUseError as e:
                click.echo(
                    click.style(f"✗ Tag Type In Use: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except InvalidTagDataError as e:
                click.echo(
                    click.style(f"✗ Invalid Tag Data: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except InvalidTagTypeDataError as e:
                click.echo(
                    click.style(f"✗ Invalid Tag Type Data: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetVersionNotFoundError as e:
                click.echo(
                    click.style(f"✗ Asset Version Not Found: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetVersionOperationError as e:
                click.echo(
                    click.style(f"✗ Asset Version Operation Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except InvalidAssetVersionDataError as e:
                click.echo(
                    click.style(f"✗ Invalid Asset Version Data: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetVersionRevertError as e:
                click.echo(
                    click.style(f"✗ Asset Version Revert Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetVersionError as e:
                click.echo(
                    click.style(f"✗ Asset Version Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetLinkNotFoundError as e:
                click.echo(
                    click.style(f"✗ Asset Link Not Found: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetLinkAlreadyExistsError as e:
                click.echo(
                    click.style(f"✗ Asset Link Already Exists: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except CycleDetectionError as e:
                click.echo(
                    click.style(f"✗ Cycle Detection Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetLinkPermissionError as e:
                click.echo(
                    click.style(f"✗ Asset Link Permission Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetLinkValidationError as e:
                click.echo(
                    click.style(f"✗ Asset Link Validation Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except InvalidRelationshipTypeError as e:
                click.echo(
                    click.style(f"✗ Invalid Relationship Type: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetLinkOperationError as e:
                click.echo(
                    click.style(f"✗ Asset Link Operation Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except AssetLinkError as e:
                click.echo(
                    click.style(f"✗ Asset Link Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except SearchDisabledError as e:
                click.echo(
                    click.style(f"✗ Search Disabled: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except SearchUnavailableError as e:
                click.echo(
                    click.style(f"✗ Search Unavailable: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except InvalidSearchParametersError as e:
                click.echo(
                    click.style(f"✗ Invalid Search Parameters: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except SearchQueryError as e:
                click.echo(
                    click.style(f"✗ Search Query Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except SearchMappingError as e:
                click.echo(
                    click.style(f"✗ Search Mapping Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except SearchError as e:
                click.echo(
                    click.style(f"✗ Search Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except VamsCLIError as e:
                click.echo(
                    click.style(f"✗ Error: {e}", fg='red', bold=True),
                    err=True
                )
                sys.exit(1)
            except click.ClickException:
                # Let Click handle its own exceptions
                raise
            except KeyboardInterrupt:
                click.echo("\nOperation cancelled by user.")
                sys.exit(1)
            except Exception as e:
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


@handle_exceptions()
def main():
    """Main entry point for the CLI."""
    cli()


if __name__ == '__main__':
    main()
