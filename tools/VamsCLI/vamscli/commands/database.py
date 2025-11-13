"""Database management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional, List

from ..constants import API_DATABASE, API_DATABASE_BY_ID, API_BUCKETS
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    DatabaseNotFoundError, DatabaseAlreadyExistsError, DatabaseDeletionError,
    BucketNotFoundError, InvalidDatabaseDataError
)


def parse_json_input(json_input: str) -> Dict[str, Any]:
    """Parse JSON input from string or file."""
    try:
        # Try to parse as JSON string first
        return json.loads(json_input)
    except json.JSONDecodeError:
        # If that fails, try to read as file path
        try:
            with open(json_input, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, IOError):
            raise click.BadParameter(
                f"Invalid JSON input: '{json_input}' is neither valid JSON nor a readable file path"
            )
        except json.JSONDecodeError:
            raise click.BadParameter(
                f"Invalid JSON in file '{json_input}': file contains invalid JSON format"
            )


def format_database_output(database_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format database data for CLI output."""
    if json_output:
        return json.dumps(database_data, indent=2)
    
    # CLI-friendly formatting
    output_lines = []
    output_lines.append("Database Details:")
    output_lines.append(f"  ID: {database_data.get('databaseId', 'N/A')}")
    output_lines.append(f"  Description: {database_data.get('description', 'N/A')}")
    output_lines.append(f"  Date Created: {database_data.get('dateCreated', 'N/A')}")
    output_lines.append(f"  Asset Count: {database_data.get('assetCount', 0)}")
    output_lines.append(f"  Default Bucket ID: {database_data.get('defaultBucketId', 'N/A')}")
    
    # Bucket information
    bucket_name = database_data.get('bucketName')
    if bucket_name:
        output_lines.append(f"  Bucket Name: {bucket_name}")
        base_prefix = database_data.get('baseAssetsPrefix')
        if base_prefix:
            output_lines.append(f"  Base Assets Prefix: {base_prefix}")
    
    return '\n'.join(output_lines)


def format_bucket_output(bucket_data: Dict[str, Any], json_output: bool = False) -> str:
    """Format bucket data for CLI output."""
    if json_output:
        return json.dumps(bucket_data, indent=2)
    
    # CLI-friendly formatting
    output_lines = []
    output_lines.append("Bucket Details:")
    output_lines.append(f"  ID: {bucket_data.get('bucketId', 'N/A')}")
    output_lines.append(f"  Name: {bucket_data.get('bucketName', 'N/A')}")
    output_lines.append(f"  Base Assets Prefix: {bucket_data.get('baseAssetsPrefix', 'N/A')}")
    
    return '\n'.join(output_lines)


def prompt_bucket_selection(api_client: APIClient) -> str:
    """Prompt user to select a bucket from available buckets."""
    try:
        # Get available buckets
        buckets_response = api_client.list_buckets()
        buckets = buckets_response.get('Items', [])
        
        if not buckets:
            raise click.ClickException("No buckets available. Please contact your administrator.")
        
        # Display available buckets
        click.echo("Available buckets:")
        for i, bucket in enumerate(buckets, 1):
            bucket_name = bucket.get('bucketName', 'Unknown')
            bucket_id = bucket.get('bucketId', 'Unknown')
            base_prefix = bucket.get('baseAssetsPrefix', '')
            
            click.echo(f"  [{i}] {bucket_name} ({bucket_id})")
            if base_prefix:
                click.echo(f"      Base prefix: {base_prefix}")
        
        # Prompt for selection
        while True:
            try:
                choice = click.prompt("Select bucket number", type=int)
                if 1 <= choice <= len(buckets):
                    selected_bucket = buckets[choice - 1]
                    return selected_bucket['bucketId']
                else:
                    click.echo(f"Please enter a number between 1 and {len(buckets)}")
            except (ValueError, click.Abort):
                raise click.ClickException("Bucket selection cancelled")
                
    except Exception as e:
        if isinstance(e, click.ClickException):
            raise
        raise click.ClickException(f"Failed to get bucket list: {e}")


@click.group()
def database():
    """Database management commands."""
    pass


@database.command()
@click.option('--show-deleted', is_flag=True, help='Include deleted databases')
@click.option('--max-items', type=int, help='Maximum number of items to return')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, show_deleted: bool, max_items: Optional[int], page_size: Optional[int], 
         starting_token: Optional[str], json_output: bool):
    """
    List all databases.
    
    This command lists all databases in the VAMS system with optional pagination
    and filtering for deleted databases.
    
    Examples:
        vamscli database list
        vamscli database list --show-deleted
        vamscli database list --max-items 50 --page-size 25
        vamscli database list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Build pagination parameters
    params = {}
    if max_items:
        params['maxItems'] = max_items
    if page_size:
        params['pageSize'] = page_size
    if starting_token:
        params['startingToken'] = starting_token
    
    output_status("Retrieving databases...", json_output)
    
    # List databases
    result = api_client.list_databases(show_deleted=show_deleted, params=params)
    
    def format_databases_list(data):
        """Format databases list for CLI display."""
        databases = data.get('Items', [])
        if not databases:
            return "No databases found."
        
        lines = [f"\nFound {len(databases)} database(s):", "-" * 80]
        
        for database in databases:
            lines.append(f"ID: {database.get('databaseId', 'N/A')}")
            lines.append(f"Description: {database.get('description', 'N/A')}")
            lines.append(f"Date Created: {database.get('dateCreated', 'N/A')}")
            lines.append(f"Asset Count: {database.get('assetCount', 0)}")
            lines.append(f"Default Bucket ID: {database.get('defaultBucketId', 'N/A')}")
            
            bucket_name = database.get('bucketName')
            if bucket_name:
                lines.append(f"Bucket Name: {bucket_name}")
            
            lines.append("-" * 80)
        
        # Show pagination info if available
        next_token = data.get('NextToken')
        if next_token:
            lines.append(f"More results available. Use --starting-token '{next_token}' to see additional databases.")
        
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_databases_list)
    return result


@database.command()
@click.option('-d', '--database-id', required=True, help='Database ID to create')
@click.option('--description', help='Database description (required unless using --json-input)')
@click.option('--default-bucket-id', help='Default bucket ID (optional - will prompt if not provided)')
@click.option('--json-input', help='JSON input file path or JSON string with all database data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, database_id: str, description: Optional[str], default_bucket_id: Optional[str],
           json_input: Optional[str], json_output: bool):
    """
    Create a new database.
    
    This command creates a new database in VAMS. You can provide database details
    via individual options or use --json-input for complex data structures.
    If --default-bucket-id is not provided, you will be prompted to select from
    available buckets.
    
    Examples:
        vamscli database create -d my-database --description "My Database"
        vamscli database create -d my-database --description "My Database" --default-bucket-id "bucket-uuid"
        vamscli database create --json-input '{"databaseId":"test","description":"Test","defaultBucketId":"uuid"}'
    """
    # Get profile manager and API client (setup/auth already validated by decorator)
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build database data
        if json_input:
            # Use JSON input
            database_data = parse_json_input(json_input)
            # Override database_id from command line
            database_data['databaseId'] = database_id
        else:
            # Build from individual options
            if not description:
                raise click.BadParameter("--description is required when not using --json-input")
            
            database_data = {
                'databaseId': database_id,
                'description': description
            }
            
            # Handle bucket selection
            if default_bucket_id:
                database_data['defaultBucketId'] = default_bucket_id
            else:
                click.echo("No bucket ID provided. Please select from available buckets:")
                database_data['defaultBucketId'] = prompt_bucket_selection(api_client)
        
        output_status(f"Creating database '{database_id}'...", json_output)
        
        # Create the database
        result = api_client.create_database(database_data)
        
        def format_create_result(data):
            """Format create result for CLI display."""
            lines = []
            lines.append(f"  Database ID: {data.get('databaseId', database_id)}")
            lines.append(f"  Message: {data.get('message', 'Database created')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Database created successfully!",
            cli_formatter=format_create_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid JSON Input")
        raise click.ClickException(str(e))
    except DatabaseAlreadyExistsError as e:
        output_error(
            e,
            json_output,
            error_type="Database Already Exists",
            helpful_message="Use 'vamscli database get' to view the existing database or choose a different database ID."
        )
        raise click.ClickException(str(e))
    except BucketNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Bucket Not Found",
            helpful_message="Use 'vamscli database list-buckets' to see available buckets."
        )
        raise click.ClickException(str(e))
    except InvalidDatabaseDataError as e:
        output_error(e, json_output, error_type="Invalid Database Data")
        raise click.ClickException(str(e))


@database.command()
@click.option('-d', '--database-id', required=True, help='Database ID to update')
@click.option('--description', help='New database description')
@click.option('--default-bucket-id', help='New default bucket ID')
@click.option('--json-input', help='JSON input file path or JSON string with update data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, database_id: str, description: Optional[str], default_bucket_id: Optional[str],
           json_input: Optional[str], json_output: bool):
    """
    Update an existing database.
    
    This command updates an existing database in VAMS. You can update individual
    fields or use --json-input for complex updates.
    
    Examples:
        vamscli database update -d my-database --description "Updated description"
        vamscli database update -d my-database --default-bucket-id "new-bucket-uuid"
        vamscli database update --json-input '{"databaseId":"test","description":"Updated","defaultBucketId":"uuid"}'
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Build update data
        if json_input:
            # Use JSON input
            database_data = parse_json_input(json_input)
            # Override database_id from command line
            database_data['databaseId'] = database_id
        else:
            # Build from individual options
            database_data = {
                'databaseId': database_id
            }
            
            if description:
                database_data['description'] = description
            if default_bucket_id:
                database_data['defaultBucketId'] = default_bucket_id
            
            # Ensure at least one field is being updated
            if len(database_data) == 1:  # Only databaseId
                raise click.BadParameter(
                    "At least one field must be provided for update. "
                    "Use --description, --default-bucket-id, or --json-input."
                )
        
        output_status(f"Updating database '{database_id}'...", json_output)
        
        # Update the database
        result = api_client.update_database(database_data)
        
        def format_update_result(data):
            """Format update result for CLI display."""
            lines = []
            lines.append(f"  Database ID: {data.get('databaseId', database_id)}")
            lines.append(f"  Message: {data.get('message', 'Database updated')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Database updated successfully!",
            cli_formatter=format_update_result
        )
        
        return result
        
    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid JSON Input")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))
    except BucketNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Bucket Not Found",
            helpful_message="Use 'vamscli database list-buckets' to see available buckets."
        )
        raise click.ClickException(str(e))
    except InvalidDatabaseDataError as e:
        output_error(e, json_output, error_type="Invalid Database Data")
        raise click.ClickException(str(e))


@database.command()
@click.option('-d', '--database-id', required=True, help='Database ID to retrieve')
@click.option('--show-deleted', is_flag=True, help='Include deleted databases in search')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, database_id: str, show_deleted: bool, json_output: bool):
    """
    Get details for a specific database.
    
    This command retrieves detailed information about a database, including
    metadata, bucket information, and asset count.
    
    Examples:
        vamscli database get -d my-database
        vamscli database get -d my-database --show-deleted
        vamscli database get -d my-database --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        output_status(f"Retrieving database '{database_id}'...", json_output)
        
        # Get the database
        result = api_client.get_database(database_id, show_deleted)
        
        output_result(result, json_output, cli_formatter=format_database_output)
        
        return result
        
    except DatabaseNotFoundError as e:
        helpful_msg = "Use 'vamscli database list' to see available databases."
        if not show_deleted:
            helpful_msg = "Try using --show-deleted to include deleted databases.\n" + helpful_msg
        
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message=helpful_msg
        )
        raise click.ClickException(str(e))


@database.command()
@click.option('-d', '--database-id', required=True, help='Database ID to delete')
@click.option('--confirm', is_flag=True, help='Confirm database deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, database_id: str, confirm: bool, json_output: bool):
    """
    Delete a database.
    
    ⚠️  WARNING: This action will delete the database! ⚠️
    
    This command deletes a database from VAMS. The database must not contain
    any active assets, workflows, or pipelines before it can be deleted.
    
    The --confirm flag is required to prevent accidental deletions.
    
    Examples:
        vamscli database delete -d my-database --confirm
        vamscli database delete -d my-database --confirm --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Require confirmation for deletion
        if not confirm:
            click.secho("⚠️  Database deletion requires explicit confirmation!", fg='yellow', bold=True)
            click.echo("This action will delete the database and cannot be undone.")
            click.echo("The database must not contain any active assets, workflows, or pipelines.")
            click.echo()
            click.echo("Use --confirm flag to proceed with deletion.")
            raise click.ClickException("Confirmation required for database deletion")
        
        # Additional confirmation prompt for safety
        click.secho(f"⚠️  You are about to delete database '{database_id}'", fg='red', bold=True)
        click.echo("This action cannot be undone!")
        
        if not click.confirm("Are you sure you want to proceed?"):
            click.echo("Deletion cancelled.")
            return None
        
        output_status(f"Deleting database '{database_id}'...", json_output)
        
        # Delete the database
        result = api_client.delete_database(database_id)
        
        def format_delete_result(data):
            """Format delete result for CLI display."""
            lines = []
            lines.append(f"  Database ID: {database_id}")
            lines.append(f"  Message: {data.get('message', 'Database deleted')}")
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Database deleted successfully!",
            cli_formatter=format_delete_result
        )
        
        return result
        
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))
    except DatabaseDeletionError as e:
        output_error(
            e,
            json_output,
            error_type="Database Deletion Error",
            helpful_message="Ensure the database does not contain any active assets, workflows, or pipelines."
        )
        raise click.ClickException(str(e))


@database.command('list-buckets')
@click.option('--max-items', type=int, help='Maximum number of items to return')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_buckets(ctx: click.Context, max_items: Optional[int], page_size: Optional[int], 
                starting_token: Optional[str], json_output: bool):
    """
    List available S3 bucket configurations.
    
    This command lists all S3 bucket configurations available for use with
    databases in VAMS.
    
    Examples:
        vamscli database list-buckets
        vamscli database list-buckets --max-items 10
        vamscli database list-buckets --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Build pagination parameters
    params = {}
    if max_items:
        params['maxItems'] = max_items
    if page_size:
        params['pageSize'] = page_size
    if starting_token:
        params['startingToken'] = starting_token
    
    output_status("Retrieving bucket configurations...", json_output)
    
    # List buckets
    result = api_client.list_buckets(params)
    
    def format_buckets_list(data):
        """Format buckets list for CLI display."""
        buckets = data.get('Items', [])
        if not buckets:
            return "No bucket configurations found."
        
        lines = [f"\nFound {len(buckets)} bucket configuration(s):", "-" * 80]
        
        for bucket in buckets:
            lines.append(f"ID: {bucket.get('bucketId', 'N/A')}")
            lines.append(f"Name: {bucket.get('bucketName', 'N/A')}")
            lines.append(f"Base Assets Prefix: {bucket.get('baseAssetsPrefix', 'N/A')}")
            lines.append("-" * 80)
        
        # Show pagination info if available
        next_token = data.get('NextToken')
        if next_token:
            lines.append(f"More results available. Use --starting-token '{next_token}' to see additional buckets.")
        
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_buckets_list)
    return result
