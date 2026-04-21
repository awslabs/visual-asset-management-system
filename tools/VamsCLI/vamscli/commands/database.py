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
    # Handle None, empty string, or Click Sentinel objects
    if not json_input or (hasattr(json_input, '__class__') and 'Sentinel' in json_input.__class__.__name__):
        return {}
    
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
    
    # New configuration fields
    restrict_metadata = database_data.get('restrictMetadataOutsideSchemas', False)
    output_lines.append(f"  Restrict Metadata Outside Schemas: {restrict_metadata}")
    
    file_extensions = database_data.get('restrictFileUploadsToExtensions', '')
    if file_extensions:
        output_lines.append(f"  Restrict File Uploads To Extensions: {file_extensions}")
    else:
        output_lines.append(f"  Restrict File Uploads To Extensions: (none)")
    
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
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, show_deleted: bool, page_size: Optional[int], max_items: Optional[int],
         starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """
    List all databases.
    
    This command lists all databases in the VAMS system with optional pagination
    and filtering for deleted databases.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli database list
        
        # Auto-pagination to fetch all items (default: up to 10,000)
        vamscli database list --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli database list --auto-paginate --max-items 5000
        
        # Auto-pagination with custom page size
        vamscli database list --auto-paginate --page-size 500
        
        # Manual pagination with page size
        vamscli database list --page-size 200
        vamscli database list --starting-token "token123" --page-size 200
        
        # With filters
        vamscli database list --show-deleted
        vamscli database list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Validate pagination options
    if auto_paginate and starting_token:
        raise click.ClickException(
            "Cannot use --auto-paginate with --starting-token. "
            "Use --auto-paginate for automatic pagination, or --starting-token for manual pagination."
        )
    
    # Warn if max-items used without auto-paginate
    if max_items and not auto_paginate:
        output_status("Warning: --max-items only applies with --auto-paginate. Ignoring --max-items.", json_output)
        max_items = None
    
    if auto_paginate:
        # Auto-pagination mode: fetch all items up to max_items (default 10,000)
        max_total_items = max_items or 10000
        output_status(f"Retrieving databases (auto-paginating up to {max_total_items} items)...", json_output)
        
        all_items = []
        next_token = None
        total_fetched = 0
        page_count = 0
        
        while True:
            page_count += 1
            
            # Prepare query parameters for this page
            params = {}
            if page_size:
                params['pageSize'] = page_size  # Pass pageSize to API
            if next_token:
                params['startingToken'] = next_token
            
            # Note: maxItems is NOT passed to API - it's CLI-side limit only
            
            # Make API call
            page_result = api_client.list_databases(show_deleted=show_deleted, params=params)
            
            # Aggregate items
            items = page_result.get('Items', [])
            all_items.extend(items)
            total_fetched += len(items)
            
            # Show progress in CLI mode
            if not json_output:
                output_status(f"Fetched {total_fetched} databases (page {page_count})...", False)
            
            # Check if we should continue
            next_token = page_result.get('NextToken')
            if not next_token or total_fetched >= max_total_items:
                break
        
        # Create final result
        result = {
            'Items': all_items,
            'totalItems': len(all_items),
            'autoPaginated': True,
            'pageCount': page_count
        }
        
        if total_fetched >= max_total_items and next_token:
            result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
        
    else:
        # Manual pagination mode: single API call
        output_status("Retrieving databases...", json_output)
        
        # Build pagination parameters
        params = {}
        if page_size:
            params['pageSize'] = page_size  # Pass pageSize to API
        if starting_token:
            params['startingToken'] = starting_token
        
        # Note: maxItems is NOT passed to API in manual mode
        
        # List databases
        result = api_client.list_databases(show_deleted=show_deleted, params=params)
    
    def format_databases_list(data):
        """Format databases list for CLI display."""
        databases = data.get('Items', [])
        if not databases:
            return "No databases found."
        
        lines = []
        
        # Show auto-pagination info if present
        if data.get('autoPaginated'):
            lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
            if data.get('note'):
                lines.append(f"⚠️  {data['note']}")
            lines.append("")
        
        lines.append(f"Found {len(databases)} database(s):")
        lines.append("-" * 80)
        
        for database in databases:
            lines.append(f"ID: {database.get('databaseId', 'N/A')}")
            lines.append(f"Description: {database.get('description', 'N/A')}")
            lines.append(f"Date Created: {database.get('dateCreated', 'N/A')}")
            lines.append(f"Asset Count: {database.get('assetCount', 0)}")
            lines.append(f"Default Bucket ID: {database.get('defaultBucketId', 'N/A')}")
            
            bucket_name = database.get('bucketName')
            if bucket_name:
                lines.append(f"Bucket Name: {bucket_name}")
            
            # New configuration fields
            restrict_metadata = database.get('restrictMetadataOutsideSchemas', False)
            lines.append(f"Restrict Metadata Outside Schemas: {restrict_metadata}")
            
            file_extensions = database.get('restrictFileUploadsToExtensions', '')
            if file_extensions:
                lines.append(f"Restrict File Uploads To Extensions: {file_extensions}")
            else:
                lines.append(f"Restrict File Uploads To Extensions: (none)")
            
            lines.append("-" * 80)
        
        # Show nextToken for manual pagination
        if not data.get('autoPaginated') and data.get('NextToken'):
            lines.append(f"\nNext token: {data['NextToken']}")
            lines.append("Use --starting-token to get the next page")
        
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_databases_list)
    return result


@database.command()
@click.option('-d', '--database-id', required=True, help='Database ID to create')
@click.option('--description', help='Database description (required unless using --json-input)')
@click.option('--default-bucket-id', help='Default bucket ID (optional - will prompt if not provided)')
@click.option('--restrict-metadata-outside-schemas', is_flag=True, help='Restrict metadata to defined schemas only')
@click.option('--restrict-file-uploads-to-extensions', help='Comma-separated list of allowed file extensions (e.g., ".pdf,.docx,.jpg")')
@click.option('--json-input', help='JSON input file path or JSON string with all database data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, database_id: str, description: Optional[str], default_bucket_id: Optional[str],
           restrict_metadata_outside_schemas: bool, restrict_file_uploads_to_extensions: Optional[str],
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
        vamscli database create -d my-database --description "My Database" --restrict-metadata-outside-schemas
        vamscli database create -d my-database --description "My Database" --restrict-file-uploads-to-extensions ".pdf,.docx"
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
                if json_output:
                    # For JSON output, return error in JSON format
                    import sys
                    error_result = {
                        "error": "Missing required parameter",
                        "message": "--default-bucket-id is required when using --json-output",
                        "databaseId": database_id
                    }
                    output_result(error_result, json_output=True)
                    sys.exit(1)
                else:
                    # For CLI output, show interactive bucket selection
                    click.echo("No bucket ID provided. Please select from available buckets:")
                    database_data['defaultBucketId'] = prompt_bucket_selection(api_client)
            
            # Add new configuration fields if provided
            if restrict_metadata_outside_schemas:
                database_data['restrictMetadataOutsideSchemas'] = True
            
            if restrict_file_uploads_to_extensions:
                database_data['restrictFileUploadsToExtensions'] = restrict_file_uploads_to_extensions
        
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
@click.option('--restrict-metadata-outside-schemas', is_flag=True, help='Enable metadata restriction to defined schemas')
@click.option('--no-restrict-metadata-outside-schemas', is_flag=True, help='Disable metadata restriction')
@click.option('--restrict-file-uploads-to-extensions', help='Set allowed file extensions (e.g., ".pdf,.docx,.jpg")')
@click.option('--clear-file-extensions', is_flag=True, help='Clear file extension restrictions')
@click.option('--json-input', help='JSON input file path or JSON string with update data')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, database_id: str, description: Optional[str], default_bucket_id: Optional[str],
           restrict_metadata_outside_schemas: bool, no_restrict_metadata_outside_schemas: bool,
           restrict_file_uploads_to_extensions: Optional[str], clear_file_extensions: bool,
           json_input: Optional[str], json_output: bool):
    """
    Update an existing database.
    
    This command updates an existing database in VAMS. You can update individual
    fields or use --json-input for complex updates.
    
    Examples:
        vamscli database update -d my-database --description "Updated description"
        vamscli database update -d my-database --default-bucket-id "new-bucket-uuid"
        vamscli database update -d my-database --restrict-metadata-outside-schemas
        vamscli database update -d my-database --no-restrict-metadata-outside-schemas
        vamscli database update -d my-database --restrict-file-uploads-to-extensions ".pdf,.png"
        vamscli database update -d my-database --clear-file-extensions
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
            
            # Handle new configuration fields
            # Check for conflicting flags
            if restrict_metadata_outside_schemas and no_restrict_metadata_outside_schemas:
                raise click.BadParameter(
                    "Cannot use both --restrict-metadata-outside-schemas and --no-restrict-metadata-outside-schemas"
                )
            
            if restrict_file_uploads_to_extensions and clear_file_extensions:
                raise click.BadParameter(
                    "Cannot use both --restrict-file-uploads-to-extensions and --clear-file-extensions"
                )
            
            # Apply metadata restriction flags
            if restrict_metadata_outside_schemas:
                database_data['restrictMetadataOutsideSchemas'] = True
            elif no_restrict_metadata_outside_schemas:
                database_data['restrictMetadataOutsideSchemas'] = False
            
            # Apply file extension restrictions
            if restrict_file_uploads_to_extensions:
                database_data['restrictFileUploadsToExtensions'] = restrict_file_uploads_to_extensions
            elif clear_file_extensions:
                database_data['restrictFileUploadsToExtensions'] = ''
            
            # Ensure at least one field is being updated
            if len(database_data) == 1:  # Only databaseId
                raise click.BadParameter(
                    "At least one field must be provided for update. "
                    "Use --description, --default-bucket-id, --restrict-metadata-outside-schemas, "
                    "--no-restrict-metadata-outside-schemas, --restrict-file-uploads-to-extensions, "
                    "--clear-file-extensions, or --json-input."
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
            if json_output:
                # For JSON output, return error in JSON format
                error_result = {
                    "error": "Confirmation required",
                    "message": "Database deletion requires the --confirm flag",
                    "databaseId": database_id
                }
                output_result(error_result, json_output=True)
                raise click.ClickException("Confirmation required for database deletion")
            else:
                # For CLI output, show helpful message
                click.secho("⚠️  Database deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("This action will delete the database and cannot be undone.")
                click.echo("The database must not contain any active assets, workflows, or pipelines.")
                click.echo()
                click.echo("Use --confirm flag to proceed with deletion.")
                raise click.ClickException("Confirmation required for database deletion")
        
        # If --confirm is provided, skip the additional prompt in JSON mode
        if not json_output:
            # Additional confirmation prompt for safety (CLI mode only)
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
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_buckets(ctx: click.Context, page_size: Optional[int], max_items: Optional[int],
                starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """
    List available S3 bucket configurations.
    
    This command lists all S3 bucket configurations available for use with
    databases in VAMS.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli database list-buckets
        
        # Auto-pagination to fetch all items (default: up to 10,000)
        vamscli database list-buckets --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli database list-buckets --auto-paginate --max-items 5000
        
        # Manual pagination with page size
        vamscli database list-buckets --page-size 200
        vamscli database list-buckets --starting-token "token123" --page-size 200
        
        # JSON output
        vamscli database list-buckets --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Validate pagination options
    if auto_paginate and starting_token:
        raise click.ClickException(
            "Cannot use --auto-paginate with --starting-token. "
            "Use --auto-paginate for automatic pagination, or --starting-token for manual pagination."
        )
    
    # Warn if max-items used without auto-paginate
    if max_items and not auto_paginate:
        output_status("Warning: --max-items only applies with --auto-paginate. Ignoring --max-items.", json_output)
        max_items = None
    
    if auto_paginate:
        # Auto-pagination mode: fetch all items up to max_items (default 10,000)
        max_total_items = max_items or 10000
        output_status(f"Retrieving bucket configurations (auto-paginating up to {max_total_items} items)...", json_output)
        
        all_items = []
        next_token = None
        total_fetched = 0
        page_count = 0
        
        while True:
            page_count += 1
            
            # Prepare query parameters for this page
            params = {}
            if page_size:
                params['pageSize'] = page_size  # Pass pageSize to API
            if next_token:
                params['startingToken'] = next_token
            
            # Note: maxItems is NOT passed to API - it's CLI-side limit only
            
            # Make API call
            page_result = api_client.list_buckets(params)
            
            # Aggregate items
            items = page_result.get('Items', [])
            all_items.extend(items)
            total_fetched += len(items)
            
            # Show progress in CLI mode
            if not json_output:
                output_status(f"Fetched {total_fetched} bucket configurations (page {page_count})...", False)
            
            # Check if we should continue
            next_token = page_result.get('NextToken')
            if not next_token or total_fetched >= max_total_items:
                break
        
        # Create final result
        result = {
            'Items': all_items,
            'totalItems': len(all_items),
            'autoPaginated': True,
            'pageCount': page_count
        }
        
        if total_fetched >= max_total_items and next_token:
            result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
        
    else:
        # Manual pagination mode: single API call
        output_status("Retrieving bucket configurations...", json_output)
        
        # Build pagination parameters
        params = {}
        if page_size:
            params['pageSize'] = page_size  # Pass pageSize to API
        if starting_token:
            params['startingToken'] = starting_token
        
        # Note: maxItems is NOT passed to API in manual mode
        
        # List buckets
        result = api_client.list_buckets(params)
    
    def format_buckets_list(data):
        """Format buckets list for CLI display."""
        buckets = data.get('Items', [])
        if not buckets:
            return "No bucket configurations found."
        
        lines = []
        
        # Show auto-pagination info if present
        if data.get('autoPaginated'):
            lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
            if data.get('note'):
                lines.append(f"⚠️  {data['note']}")
            lines.append("")
        
        lines.append(f"Found {len(buckets)} bucket configuration(s):")
        lines.append("-" * 80)
        
        for bucket in buckets:
            lines.append(f"ID: {bucket.get('bucketId', 'N/A')}")
            lines.append(f"Name: {bucket.get('bucketName', 'N/A')}")
            lines.append(f"Base Assets Prefix: {bucket.get('baseAssetsPrefix', 'N/A')}")
            lines.append("-" * 80)
        
        # Show nextToken for manual pagination
        if not data.get('autoPaginated') and data.get('NextToken'):
            lines.append(f"\nNext token: {data['NextToken']}")
            lines.append("Use --starting-token to get the next page")
        
        return '\n'.join(lines)
    
    output_result(result, json_output, cli_formatter=format_buckets_list)
    return result
