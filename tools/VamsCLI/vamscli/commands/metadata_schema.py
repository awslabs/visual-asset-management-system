"""Metadata schema management commands for VamsCLI."""

import json
import click
from typing import Dict, Any, Optional

from ..utils.api_client import APIClient
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.json_output import output_status, output_result, output_error
from ..utils.exceptions import (
    DatabaseNotFoundError,
    APIError,
    InvalidMetadataSchemaDataError,
    MetadataSchemaDeletionError,
    MetadataSchemaNotFoundError,
)

# The entity types a schema can describe, as the API spells them. Accepted case-insensitively.
METADATA_SCHEMA_ENTITY_TYPES = [
    'databaseMetadata', 'assetMetadata', 'fileMetadata', 'fileAttribute', 'assetLinkMetadata',
]


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


def format_metadata_schema_list_output(schemas_data: Dict[str, Any]) -> str:
    """Format metadata schemas list for CLI output."""
    items = schemas_data.get('Items', [])
    
    if not items:
        return "No metadata schemas found."
    
    # CLI-friendly table formatting
    output_lines = []
    output_lines.append(f"Found {len(items)} metadata schema(s):")
    output_lines.append("=" * 100)
    
    for schema in items:
        output_lines.append(f"ID: {schema.get('metadataSchemaId', 'N/A')}")
        output_lines.append(f"Database: {schema.get('databaseId', 'N/A')}")
        output_lines.append(f"Name: {schema.get('schemaName', 'N/A')}")
        output_lines.append(f"Entity Type: {schema.get('metadataSchemaEntityType', 'N/A')}")
        output_lines.append(f"Enabled: {'Yes' if schema.get('enabled', True) else 'No'}")
        
        # Count fields
        fields = schema.get('fields', {})
        if isinstance(fields, dict):
            field_list = fields.get('fields', [])
            output_lines.append(f"Fields: {len(field_list)}")
        else:
            output_lines.append("Fields: N/A")
        
        # File restrictions
        file_restriction = schema.get('fileKeyTypeRestriction')
        if file_restriction:
            output_lines.append(f"File Restrictions: {file_restriction}")
        
        # Timestamps
        if schema.get('dateCreated'):
            output_lines.append(f"Created: {schema.get('dateCreated')}")
        if schema.get('dateModified'):
            output_lines.append(f"Modified: {schema.get('dateModified')}")
        
        output_lines.append("-" * 100)
    
    # Show nextToken for pagination
    if schemas_data.get('NextToken'):
        output_lines.append(f"\nNext token: {schemas_data['NextToken']}")
        output_lines.append("Use --starting-token to get the next page")
    
    return '\n'.join(output_lines)


def format_metadata_schema_detail_output(schema_data: Dict[str, Any]) -> str:
    """Format single metadata schema details for CLI output."""
    output_lines = []
    output_lines.append("Metadata Schema Details:")
    output_lines.append("=" * 100)
    output_lines.append(f"  ID: {schema_data.get('metadataSchemaId', 'N/A')}")
    output_lines.append(f"  Database: {schema_data.get('databaseId', 'N/A')}")
    output_lines.append(f"  Name: {schema_data.get('schemaName', 'N/A')}")
    output_lines.append(f"  Entity Type: {schema_data.get('metadataSchemaEntityType', 'N/A')}")
    output_lines.append(f"  Enabled: {'Yes' if schema_data.get('enabled', True) else 'No'}")
    
    # File restrictions
    file_restriction = schema_data.get('fileKeyTypeRestriction')
    if file_restriction:
        output_lines.append(f"  File Restrictions: {file_restriction}")
    
    # Timestamps
    if schema_data.get('dateCreated'):
        output_lines.append(f"  Created: {schema_data.get('dateCreated')}")
    if schema_data.get('dateModified'):
        output_lines.append(f"  Modified: {schema_data.get('dateModified')}")
    if schema_data.get('createdBy'):
        output_lines.append(f"  Created By: {schema_data.get('createdBy')}")
    if schema_data.get('modifiedBy'):
        output_lines.append(f"  Modified By: {schema_data.get('modifiedBy')}")
    
    # Fields
    fields = schema_data.get('fields', {})
    if isinstance(fields, dict):
        field_list = fields.get('fields', [])
        if field_list:
            output_lines.append(f"\nFields ({len(field_list)}):")
            output_lines.append("-" * 100)
            output_lines.append(f"{'Field Name':<30} {'Type':<20} {'Required':<10} {'Default':<20}")
            output_lines.append("-" * 100)
            
            for field in field_list:
                field_name = field.get('metadataFieldKeyName', 'N/A')
                field_type = field.get('metadataFieldValueType', 'N/A')
                required = 'Yes' if field.get('required', False) else 'No'
                default_value = field.get('defaultMetadataFieldValue')
                
                # Convert None to string and handle truncation
                if default_value is None:
                    default_value = 'None'
                else:
                    default_value = str(default_value)
                    if len(default_value) > 18:
                        default_value = default_value[:15] + '...'
                
                output_lines.append(f"{field_name:<30} {field_type:<20} {required:<10} {default_value:<20}")
                
                # Show dependencies if present
                depends_on = field.get('dependsOnFieldKeyName')
                if depends_on:
                    # Handle both single string and list of strings
                    if isinstance(depends_on, str):
                        depends_str = depends_on
                    elif isinstance(depends_on, (type([]), type((1,)))):  # Check for list or tuple
                        depends_str = ', '.join(str(d) for d in depends_on)
                    else:
                        depends_str = str(depends_on)
                    
                    if len(depends_str) > 80:
                        depends_str = depends_str[:77] + '...'
                    output_lines.append(f"  └─ Depends on: {depends_str}")
                
                # Show controlled list keys if present
                controlled_keys = field.get('controlledListKeys')
                if controlled_keys:
                    keys_str = ', '.join(controlled_keys)
                    if len(keys_str) > 80:
                        keys_str = keys_str[:77] + '...'
                    output_lines.append(f"  └─ Allowed values: {keys_str}")
            
            output_lines.append("-" * 100)
        else:
            output_lines.append("\nNo fields defined.")
    else:
        output_lines.append("\nFields: N/A")
    
    return '\n'.join(output_lines)


@click.group()
def metadata_schema():
    """Metadata schema management commands."""
    pass


@metadata_schema.command()
@click.option('-d', '--database-id', help='Filter by database ID')
@click.option('-e', '--entity-type',
              type=click.Choice(METADATA_SCHEMA_ENTITY_TYPES, case_sensitive=False),
              help='Filter by entity type')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-input', help='JSON input file path or JSON string with parameters')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database_id: Optional[str], entity_type: Optional[str],
         page_size: Optional[int], max_items: Optional[int], starting_token: Optional[str],
         json_input: Optional[str], json_output: bool):
    """
    List metadata schemas with optional filters.
    
    This command lists metadata schemas across all databases or filtered by
    database ID and/or entity type. Metadata schemas define the structure and
    validation rules for metadata that can be associated with different entities
    (databases, assets, files, asset links).
    
    Entity Types:
        - databaseMetadata: Metadata for databases
        - assetMetadata: Metadata for assets
        - fileMetadata: Metadata for files
        - fileAttribute: File attributes (string-only metadata)
        - assetLinkMetadata: Metadata for asset links
    
    Examples:
        # List all metadata schemas
        vamscli metadata-schema list
        
        # List schemas for a specific database
        vamscli metadata-schema list -d my-database
        
        # List schemas by entity type
        vamscli metadata-schema list -e assetMetadata
        
        # List schemas for database and entity type
        vamscli metadata-schema list -d my-database -e fileMetadata
        
        # With pagination
        vamscli metadata-schema list --page-size 50 --max-items 200
        vamscli metadata-schema list --starting-token "token123"
        
        # JSON output
        vamscli metadata-schema list -d my-database --json-output
        
        # JSON input for complex parameters
        vamscli metadata-schema list --json-input '{"databaseId":"my-db","maxItems":100}'
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Handle JSON input for parameters
        if json_input:
            json_data = parse_json_input(json_input)
            
            # Override parameters from JSON input
            database_id = json_data.get('databaseId', database_id)
            entity_type = json_data.get('metadataEntityType', entity_type)
            max_items = json_data.get('maxItems', max_items)
            page_size = json_data.get('pageSize', page_size)
            starting_token = json_data.get('startingToken', starting_token)
        
        # Set defaults if not provided
        if max_items is None:
            max_items = 1000
        if page_size is None:
            page_size = 100
        
        # Build status message
        status_parts = ["Listing metadata schemas"]
        if database_id:
            status_parts.append(f"for database '{database_id}'")
        if entity_type:
            status_parts.append(f"with entity type '{entity_type}'")
        status_msg = " ".join(status_parts) + "..."
        
        output_status(status_msg, json_output)
        
        # List metadata schemas
        result = api_client.list_metadata_schemas(
            database_id=database_id,
            metadata_entity_type=entity_type,
            max_items=max_items,
            page_size=page_size,
            starting_token=starting_token
        )
        
        output_result(result, json_output, cli_formatter=format_metadata_schema_list_output)
        
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
    except APIError as e:
        output_error(e, json_output, error_type="API Error")
        raise click.ClickException(str(e))


@metadata_schema.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-s', '--schema-id', required=True, help='[REQUIRED] Metadata schema ID')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, database_id: str, schema_id: str, json_output: bool):
    """
    Get a specific metadata schema by ID.
    
    This command retrieves detailed information about a specific metadata schema,
    including all field definitions, data types, requirements, dependencies, and
    controlled list values.
    
    Examples:
        vamscli metadata-schema get -d my-database -s schema-123
        vamscli metadata-schema get -d my-database -s schema-123 --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        output_status(f"Retrieving metadata schema '{schema_id}' from database '{database_id}'...", json_output)
        
        # Get metadata schema by ID
        result = api_client.get_metadata_schema_by_id(database_id, schema_id)
        
        output_result(result, json_output, cli_formatter=format_metadata_schema_detail_output)
        
        return result
        
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))
    except APIError as e:
        # Check if it's a schema not found error
        if 'not found' in str(e).lower() and 'schema' in str(e).lower():
            output_error(
                e,
                json_output,
                error_type="Metadata Schema Not Found",
                helpful_message=f"Use 'vamscli metadata-schema list -d {database_id}' to see available schemas."
            )
        else:
            output_error(e, json_output, error_type="API Error")
        raise click.ClickException(str(e))


def normalize_fields_input(fields_input: str) -> Dict[str, Any]:
    """Parse a --fields value into the {'fields': [...]} shape the API takes.

    A bare array is accepted as well as the wrapped object, so a schema file can hold either.
    """
    parsed = parse_json_input(fields_input)
    if isinstance(parsed, dict):
        if 'fields' not in parsed:
            raise click.BadParameter(
                "--fields object must carry a 'fields' array of field definitions"
            )
        return parsed
    if isinstance(parsed, (type([]), type((1,)))):
        return {'fields': [*parsed]}
    raise click.BadParameter(
        "--fields must be an array of field definitions, or an object carrying one under 'fields'"
    )


def format_operation_output(result: Dict[str, Any]) -> str:
    """Format a create/update/delete result for CLI output."""
    lines = []
    lines.append(f"  Metadata Schema ID: {result.get('metadataSchemaId', 'N/A')}")
    lines.append(f"  Operation: {result.get('operation', 'N/A')}")
    lines.append(f"  Message: {result.get('message', 'N/A')}")
    if result.get('timestamp'):
        lines.append(f"  Timestamp: {result.get('timestamp')}")
    return '\n'.join(lines)


@metadata_schema.command()
@click.option('-d', '--database-id', required=True,
              help="[REQUIRED] Database ID, or GLOBAL for a schema that applies to every database")
@click.option('-e', '--entity-type', required=True,
              type=click.Choice(METADATA_SCHEMA_ENTITY_TYPES, case_sensitive=False),
              help='[REQUIRED] Entity type the schema describes')
@click.option('-n', '--schema-name', required=True, help='[REQUIRED] Schema name')
@click.option('-f', '--fields', 'fields_input', required=True,
              help='[REQUIRED] Field definitions as a JSON array, or a file path holding one')
@click.option('--file-key-type-restriction', default=None,
              help='Comma-delimited file extensions the schema applies to '
                   '(fileMetadata and fileAttribute schemas only)')
@click.option('--enabled/--disabled', default=True, show_default=True,
              help='Whether the schema is enforced on metadata writes')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, database_id: str, entity_type: str, schema_name: str,
           fields_input: str, file_key_type_restriction: Optional[str], enabled: bool,
           json_output: bool):
    """
    Create a metadata schema.

    A schema declares the metadata fields an entity can carry, their value types, and which of
    them are required. Once enabled, metadata writes for that entity type are validated against it.

    Each field definition takes metadataFieldKeyName and metadataFieldValueType, plus optional
    required, sequence, dependsOnFieldKeyName, controlledListKeys and defaultMetadataFieldValue.
    A field of type inlineControlledList must declare controlledListKeys; no other type may.
    A fileAttribute schema supports only the string value type.

    Examples:
        # From a file holding the field definitions
        vamscli metadata-schema create -d my-database -e assetMetadata -n "Review" -f fields.json

        # Inline, with a required field
        vamscli metadata-schema create -d my-database -e assetMetadata -n "Review" \\
            -f '[{"metadataFieldKeyName":"reviewer","metadataFieldValueType":"string","required":true}]'

        # Scoped to file types, created disabled
        vamscli metadata-schema create -d my-database -e fileMetadata -n "CAD" -f fields.json \\
            --file-key-type-restriction ".stp,.step" --disabled

        vamscli metadata-schema create -d my-database -e assetMetadata -n "Review" -f fields.json --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        schema_data = {
            'databaseId': database_id,
            'metadataSchemaEntityType': entity_type,
            'schemaName': schema_name,
            'fields': normalize_fields_input(fields_input),
            'enabled': enabled,
        }
        if file_key_type_restriction:
            schema_data['fileKeyTypeRestriction'] = file_key_type_restriction

        output_status(f"Creating metadata schema '{schema_name}' in database '{database_id}'...", json_output)

        result = api_client.create_metadata_schema(schema_data)

        output_result(
            result,
            json_output,
            success_message="✓ Metadata schema created successfully!",
            cli_formatter=format_operation_output,
        )

        return result

    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid JSON Input")
        raise click.ClickException(str(e))
    except InvalidMetadataSchemaDataError as e:
        output_error(e, json_output, error_type="Invalid Metadata Schema Data")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))
    except APIError as e:
        output_error(e, json_output, error_type="API Error")
        raise click.ClickException(str(e))


@metadata_schema.command()
@click.option('-s', '--schema-id', required=True, help='[REQUIRED] Metadata schema ID to update')
@click.option('-n', '--schema-name', default=None, help='New schema name')
@click.option('-f', '--fields', 'fields_input', default=None,
              help='Replacement field definitions as a JSON array, or a file path holding one')
@click.option('--file-key-type-restriction', default=None,
              help='Comma-delimited file extensions the schema applies to')
@click.option('--enabled/--disabled', default=None,
              help='Whether the schema is enforced on metadata writes')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update(ctx: click.Context, schema_id: str, schema_name: Optional[str],
           fields_input: Optional[str], file_key_type_restriction: Optional[str],
           enabled: Optional[bool], json_output: bool):
    """
    Update a metadata schema.

    At least one of --schema-name, --fields, --file-key-type-restriction, --enabled or --disabled
    must be given. Field definitions are replaced wholesale, not merged, so pass the complete set.
    The database and entity type of an existing schema cannot be changed.

    Examples:
        vamscli metadata-schema update -s schema-123 -n "Review v2"
        vamscli metadata-schema update -s schema-123 -f fields.json
        vamscli metadata-schema update -s schema-123 --disabled
        vamscli metadata-schema update -s schema-123 -n "Review v2" --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    if (schema_name is None and fields_input is None
            and file_key_type_restriction is None and enabled is None):
        raise click.ClickException(
            "At least one of --schema-name, --fields, --file-key-type-restriction, "
            "--enabled or --disabled must be provided"
        )

    try:
        update_data: Dict[str, Any] = {}
        if schema_name is not None:
            update_data['schemaName'] = schema_name
        if fields_input is not None:
            update_data['fields'] = normalize_fields_input(fields_input)
        if file_key_type_restriction is not None:
            update_data['fileKeyTypeRestriction'] = file_key_type_restriction
        if enabled is not None:
            update_data['enabled'] = enabled

        output_status(f"Updating metadata schema '{schema_id}'...", json_output)

        result = api_client.update_metadata_schema(schema_id, update_data)

        output_result(
            result,
            json_output,
            success_message="✓ Metadata schema updated successfully!",
            cli_formatter=format_operation_output,
        )

        return result

    except click.BadParameter as e:
        output_error(e, json_output, error_type="Invalid JSON Input")
        raise click.ClickException(str(e))
    except MetadataSchemaNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Metadata Schema Not Found",
            helpful_message="Use 'vamscli metadata-schema list' to see available schemas."
        )
        raise click.ClickException(str(e))
    except InvalidMetadataSchemaDataError as e:
        output_error(e, json_output, error_type="Invalid Metadata Schema Data")
        raise click.ClickException(str(e))
    except APIError as e:
        output_error(e, json_output, error_type="API Error")
        raise click.ClickException(str(e))


@metadata_schema.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID owning the schema')
@click.option('-s', '--schema-id', required=True, help='[REQUIRED] Metadata schema ID to delete')
@click.option('--confirm', is_flag=True, help='Confirm metadata schema deletion')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, database_id: str, schema_id: str, confirm: bool, json_output: bool):
    """
    Delete a metadata schema.

    ⚠️  Deleting a schema changes what metadata writes the database accepts: the fields it declared
    are no longer required, validated, or defaulted. Metadata already stored is left as it is.

    The --confirm flag is required.

    Examples:
        vamscli metadata-schema delete -d my-database -s schema-123 --confirm
        vamscli metadata-schema delete -d my-database -s schema-123 --confirm --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    try:
        # Require confirmation for deletion
        if not confirm:
            if json_output:
                import sys
                error_result = {
                    "error": "Confirmation required",
                    "message": "Metadata schema deletion requires the --confirm flag",
                    "metadataSchemaId": schema_id
                }
                output_result(error_result, json_output=True)
                sys.exit(1)
            else:
                click.secho("⚠️  Metadata schema deletion requires explicit confirmation!", fg='yellow', bold=True)
                click.echo("The fields this schema declares will no longer be validated on metadata writes.")
                click.echo("Use --confirm flag to proceed with metadata schema deletion.")
                raise click.ClickException("Confirmation required for metadata schema deletion")

        output_status(f"Deleting metadata schema '{schema_id}' from database '{database_id}'...", json_output)

        result = api_client.delete_metadata_schema(database_id, schema_id)

        output_result(
            result,
            json_output,
            success_message="✓ Metadata schema deleted successfully!",
            cli_formatter=format_operation_output,
        )

        return result

    except MetadataSchemaNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Metadata Schema Not Found",
            helpful_message=f"Use 'vamscli metadata-schema list -d {database_id}' to see available schemas."
        )
        raise click.ClickException(str(e))
    except MetadataSchemaDeletionError as e:
        output_error(e, json_output, error_type="Metadata Schema Deletion Error")
        raise click.ClickException(str(e))
    except APIError as e:
        output_error(e, json_output, error_type="API Error")
        raise click.ClickException(str(e))