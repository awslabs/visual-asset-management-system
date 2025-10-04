"""Search commands for VamsCLI - Dual-Index OpenSearch Support."""

import json
import csv
import sys
from io import StringIO
from typing import Dict, Any, List, Optional
import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.exceptions import (
    SearchError, SearchDisabledError, SearchUnavailableError, 
    InvalidSearchParametersError, SearchQueryError, SearchMappingError
)
from ..utils.features import is_feature_enabled
from ..constants import FEATURE_NOOPENSEARCH


def _load_json_input(json_input_path: str) -> Dict[str, Any]:
    """Load JSON input from file."""
    try:
        with open(json_input_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        raise InvalidSearchParametersError(f"JSON input file not found: {json_input_path}")
    except json.JSONDecodeError as e:
        raise InvalidSearchParametersError(f"Invalid JSON in input file: {e}")
    except Exception as e:
        raise InvalidSearchParametersError(f"Failed to load JSON input: {e}")


def _parse_tags_list(tags_str: str) -> List[str]:
    """Parse comma-separated tags list."""
    if not tags_str:
        return []
    return [tag.strip() for tag in tags_str.split(',') if tag.strip()]


def _parse_entity_types(entity_types_str: str) -> List[str]:
    """Parse comma-separated entity types list."""
    if not entity_types_str:
        return []
    
    valid_types = {"asset", "file"}
    types = [t.strip().lower() for t in entity_types_str.split(',') if t.strip()]
    
    # Validate entity types
    invalid_types = [t for t in types if t not in valid_types]
    if invalid_types:
        raise InvalidSearchParametersError(
            f"Invalid entity types: {', '.join(invalid_types)}. "
            f"Valid types are: {', '.join(valid_types)}"
        )
    
    return types


def _build_sort_config(sort_field: Optional[str], sort_desc: bool) -> List[Any]:
    """Build sort configuration for search request."""
    if not sort_field:
        return ["_score"]
    
    return [{"field": sort_field, "order": "desc" if sort_desc else "asc"}]


def _check_search_availability(profile_manager):
    """Check if search is available (not disabled by NOOPENSEARCH feature)."""
    if is_feature_enabled(FEATURE_NOOPENSEARCH, profile_manager):
        raise SearchDisabledError(
            "Search functionality is disabled for this environment. "
            "Use 'vamscli assets list' or 'vamscli database list-assets' instead."
        )


@click.group()
def search():
    """
    Search assets and files using OpenSearch dual-index system.
    
    Note: Search functionality requires OpenSearch to be enabled. If search is disabled
    (NOOPENSEARCH feature is enabled), use 'vamscli assets list' or 'vamscli database list-assets' instead.
    
    The search system uses separate indexes for assets and files, allowing for optimized
    queries and better performance.
    
    Examples:
        vamscli search assets -q "model" -d my-database
        vamscli search files --file-ext "gltf"
        vamscli search simple -q "training" --entity-types asset
        vamscli search mapping
    """
    pass


@search.command()
@click.option('-d', '--database', help='Database ID to search within')
@click.option('-q', '--query', help='General text search query')
@click.option('--metadata-query', help='Metadata search query (field:value format, supports AND/OR)')
@click.option('--metadata-mode', type=click.Choice(['key', 'value', 'both']), default='both',
              help='Metadata search mode: key (field names), value (field values), or both (default: both)')
@click.option('--include-metadata/--no-metadata', default=True,
              help='Include metadata fields in general search (default: include)')
@click.option('--explain-results', is_flag=True, help='Include match explanations in results')
@click.option('--sort-field', help='Field to sort by (e.g., str_assetname, str_description)')
@click.option('--sort-desc', is_flag=True, help='Sort in descending order')
@click.option('--from', 'from_offset', type=int, default=0, help='Pagination start offset (default: 0)')
@click.option('--size', type=int, default=100, help='Number of results per page (default: 100, max: 2000)')
@click.option('--asset-type', help='Filter by asset type')
@click.option('--tags', help='Filter by tags (comma-separated)')
@click.option('--include-archived', is_flag=True, help='Include archived assets')
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.option('--jsonOutput', is_flag=True, help='Output raw API response as JSON (legacy)')
@click.pass_context
@requires_setup_and_auth
def assets(ctx: click.Context, database: Optional[str], query: Optional[str], metadata_query: Optional[str],
           metadata_mode: str, include_metadata: bool, explain_results: bool,
           sort_field: Optional[str], sort_desc: bool, from_offset: int, size: int,
           asset_type: Optional[str], tags: Optional[str], include_archived: bool,
           output_format: str, jsonoutput: bool):
    """
    Search assets using OpenSearch.
    
    Search across all assets with flexible filtering, metadata search, and sorting options.
    Supports general text search, metadata-specific search, and various output formats.
    
    Metadata Search Examples:
        --metadata-query "MD_str_product:Training"              # Exact field:value match
        --metadata-query "MD_str_product:Train*"                # Wildcard search
        --metadata-query "MD_str_product:A AND MD_num_version:1" # Multiple conditions
        --metadata-query "product" --metadata-mode key          # Search field names only
        --metadata-query "Training" --metadata-mode value       # Search values only
    
    General Examples:
        vamscli search assets -q "training model" -d my-database
        vamscli search assets --asset-type "3d-model" --tags "training,simulation"
        vamscli search assets -q "model" --metadata-query "MD_str_category:Training"
        vamscli search assets -q "model" --output-format csv > results.csv
        vamscli search assets -q "model" --explain-results
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager)
        
        # Parse tags
        parsed_tags = _parse_tags_list(tags) if tags else None
        
        # Build search request using new SearchRequestModel format
        search_request = {
            "entityTypes": ["asset"],  # Asset search only
            "from": from_offset,
            "size": size,
            "includeArchived": include_archived,
            "explainResults": explain_results,
            "includeMetadataInSearch": include_metadata,
            "sort": _build_sort_config(sort_field, sort_desc)
        }
        
        # Add general query
        if query:
            search_request["query"] = query
        
        # Add metadata query
        if metadata_query:
            search_request["metadataQuery"] = metadata_query
            search_request["metadataSearchMode"] = metadata_mode
        
        # Build filters for specific criteria
        filters = []
        
        if database:
            filters.append({
                "query_string": {
                    "query": f'str_databaseid:"{database}"'
                }
            })
        
        if asset_type:
            filters.append({
                "query_string": {
                    "query": f'str_assettype:"{asset_type}"'
                }
            })
        
        if parsed_tags:
            tags_query = " OR ".join([f'"{tag}"' for tag in parsed_tags])
            filters.append({
                "query_string": {
                    "query": f"list_tags:({tags_query})"
                }
            })
        
        if filters:
            search_request["filters"] = filters
        
        # Execute search
        result = api_client.search_query(search_request)
        
        # Handle output format
        if jsonoutput or output_format == 'json':
            click.echo(json.dumps(result, indent=2))
        else:
            total = result.get("hits", {}).get("total", {}).get("value", 0)
            click.echo(f"Found {total} assets")
            click.echo(
                click.style(f"✓ Search completed. Found {total} assets.", fg='green', bold=True)
            )
    
    except SearchDisabledError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Search Disabled: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli assets list' or 'vamscli database list-assets' instead.")
        raise click.ClickException(str(e))
    except InvalidSearchParametersError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Invalid Parameters: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Check your search parameters and try again.")
        raise click.ClickException(str(e))
    except SearchQueryError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Search Query Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli search mapping' to see available search fields.")
        raise click.ClickException(str(e))


@search.command()
@click.option('-d', '--database', help='Database ID to search within')
@click.option('-q', '--query', help='General text search query')
@click.option('--metadata-query', help='Metadata search query (field:value format, supports AND/OR)')
@click.option('--metadata-mode', type=click.Choice(['key', 'value', 'both']), default='both',
              help='Metadata search mode: key (field names), value (field values), or both (default: both)')
@click.option('--include-metadata/--no-metadata', default=True,
              help='Include metadata fields in general search (default: include)')
@click.option('--explain-results', is_flag=True, help='Include match explanations in results')
@click.option('--sort-field', help='Field to sort by (e.g., str_key, str_fileext)')
@click.option('--sort-desc', is_flag=True, help='Sort in descending order')
@click.option('--from', 'from_offset', type=int, default=0, help='Pagination start offset (default: 0)')
@click.option('--size', type=int, default=100, help='Number of results per page (default: 100, max: 2000)')
@click.option('--file-ext', help='Filter by file extension')
@click.option('--tags', help='Filter by tags (comma-separated)')
@click.option('--include-archived', is_flag=True, help='Include archived files')
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.option('--jsonOutput', is_flag=True, help='Output raw API response as JSON (legacy)')
@click.pass_context
@requires_setup_and_auth
def files(ctx: click.Context, database: Optional[str], query: Optional[str], metadata_query: Optional[str],
          metadata_mode: str, include_metadata: bool, explain_results: bool,
          sort_field: Optional[str], sort_desc: bool, from_offset: int, size: int,
          file_ext: Optional[str], tags: Optional[str], include_archived: bool,
          output_format: str, jsonoutput: bool):
    """
    Search files using OpenSearch.
    
    Search across all asset files with flexible filtering, metadata search, and sorting options.
    Supports general text search, metadata-specific search, and various output formats.
    
    Examples:
        vamscli search files -q "texture" -d my-database
        vamscli search files --file-ext "gltf"
        vamscli search files -q "texture" --output-format csv > files.csv
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager)
        
        # Parse tags
        parsed_tags = _parse_tags_list(tags) if tags else None
        
        # Build search request using new SearchRequestModel format
        search_request = {
            "entityTypes": ["file"],  # File search only
            "from": from_offset,
            "size": size,
            "includeArchived": include_archived,
            "explainResults": explain_results,
            "includeMetadataInSearch": include_metadata,
            "sort": _build_sort_config(sort_field, sort_desc)
        }
        
        # Add general query
        if query:
            search_request["query"] = query
        
        # Add metadata query
        if metadata_query:
            search_request["metadataQuery"] = metadata_query
            search_request["metadataSearchMode"] = metadata_mode
        
        # Build filters for specific criteria
        filters = []
        
        if database:
            filters.append({
                "query_string": {
                    "query": f'str_databaseid:"{database}"'
                }
            })
        
        if file_ext:
            filters.append({
                "query_string": {
                    "query": f'str_fileext:"{file_ext}"'
                }
            })
        
        if parsed_tags:
            tags_query = " OR ".join([f'"{tag}"' for tag in parsed_tags])
            filters.append({
                "query_string": {
                    "query": f"list_tags:({tags_query})"
                }
            })
        
        if filters:
            search_request["filters"] = filters
        
        # Execute search
        result = api_client.search_query(search_request)
        
        # Handle output format
        if jsonoutput or output_format == 'json':
            click.echo(json.dumps(result, indent=2))
        else:
            total = result.get("hits", {}).get("total", {}).get("value", 0)
            click.echo(f"Found {total} files")
            click.echo(
                click.style(f"✓ Search completed. Found {total} files.", fg='green', bold=True)
            )
    
    except SearchDisabledError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Search Disabled: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli assets list' or 'vamscli database list-assets' instead.")
        raise click.ClickException(str(e))
    except InvalidSearchParametersError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Invalid Parameters: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Check your search parameters and try again.")
        raise click.ClickException(str(e))
    except SearchQueryError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Search Query Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli search mapping' to see available search fields.")
        raise click.ClickException(str(e))


@search.command()
@click.option('-q', '--query', help='General keyword search')
@click.option('--asset-name', help='Search by asset name')
@click.option('--asset-id', help='Search by asset ID')
@click.option('--asset-type', help='Filter by asset type')
@click.option('--file-key', help='Search by file key')
@click.option('--file-ext', help='Filter by file extension')
@click.option('-d', '--database', help='Filter by database ID')
@click.option('--tags', help='Filter by tags (comma-separated)')
@click.option('--metadata-key', help='Search metadata field names')
@click.option('--metadata-value', help='Search metadata field values')
@click.option('--entity-types', help='Filter by entity type: asset, file, or both (comma-separated)')
@click.option('--include-archived', is_flag=True, help='Include archived items')
@click.option('--from', 'from_offset', type=int, default=0, help='Pagination offset (default: 0)')
@click.option('--size', type=int, default=100, help='Results per page (default: 100, max: 1000)')
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.pass_context
@requires_setup_and_auth
def simple(ctx: click.Context, query: Optional[str], asset_name: Optional[str], asset_id: Optional[str],
           asset_type: Optional[str], file_key: Optional[str], file_ext: Optional[str],
           database: Optional[str], tags: Optional[str], metadata_key: Optional[str],
           metadata_value: Optional[str], entity_types: Optional[str], include_archived: bool,
           from_offset: int, size: int, output_format: str):
    """
    Simple search with user-friendly parameters.
    
    This command provides a simplified interface for searching using the SimpleSearchRequestModel.
    It's easier to use than the complex search commands and suitable for most search needs.
    
    Examples:
        vamscli search simple -q "training"
        vamscli search simple --asset-name "model" --entity-types asset
        vamscli search simple --file-ext "gltf" --entity-types file
        vamscli search simple --metadata-key "product" --metadata-value "Training"
        vamscli search simple -d my-database --tags "simulation,training"
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager)
        
        # Build simple search request
        search_request = {
            "from": from_offset,
            "size": size,
            "includeArchived": include_archived
        }
        
        # Add search parameters
        if query:
            search_request["query"] = query
        if asset_name:
            search_request["assetName"] = asset_name
        if asset_id:
            search_request["assetId"] = asset_id
        if asset_type:
            search_request["assetType"] = asset_type
        if file_key:
            search_request["fileKey"] = file_key
        if file_ext:
            search_request["fileExtension"] = file_ext
        if database:
            search_request["databaseId"] = database
        if tags:
            search_request["tags"] = _parse_tags_list(tags)
        if metadata_key:
            search_request["metadataKey"] = metadata_key
        if metadata_value:
            search_request["metadataValue"] = metadata_value
        if entity_types:
            search_request["entityTypes"] = _parse_entity_types(entity_types)
        
        # Execute simple search
        result = api_client.search_simple(search_request)
        
        # Handle output format
        if output_format == 'json':
            click.echo(json.dumps(result, indent=2))
        else:
            total = result.get("hits", {}).get("total", {}).get("value", 0)
            click.echo(f"Found {total} results")
            click.echo(
                click.style(f"✓ Search completed. Found {total} results.", fg='green', bold=True)
            )
    
    except SearchDisabledError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Search Disabled: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli assets list' or 'vamscli database list-assets' instead.")
        raise click.ClickException(str(e))
    except InvalidSearchParametersError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Invalid Parameters: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Check your search parameters and try again.")
        raise click.ClickException(str(e))


@search.command()
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.option('--jsonOutput', is_flag=True, help='Output raw mapping as JSON (legacy)')
@click.pass_context
@requires_setup_and_auth
def mapping(ctx: click.Context, output_format: str, jsonoutput: bool):
    """
    Get search index mapping (available fields and types).
    
    Retrieves the OpenSearch index mapping showing all available fields
    that can be used in search queries. The dual-index system has separate
    mappings for asset and file indexes.
    
    Examples:
        vamscli search mapping
        vamscli search mapping --output-format csv
        vamscli search mapping --jsonOutput
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager)
        
        # Get search mapping
        mapping = api_client.get_search_mapping()
        
        # Handle output format
        if jsonoutput or output_format == 'json':
            click.echo(json.dumps(mapping, indent=2))
        else:
            click.echo("Search mapping retrieved successfully")
            click.echo(
                click.style("✓ Retrieved search index mappings.", fg='green', bold=True)
            )
    
    except SearchDisabledError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Search Disabled: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Use 'vamscli assets list' or 'vamscli database list-assets' instead.")
        raise click.ClickException(str(e))
    except SearchMappingError as e:
        # Command-specific business logic error
        click.echo(
            click.style(f"✗ Search Mapping Error: {e}", fg='red', bold=True),
            err=True
        )
        click.echo("Check if search is properly configured.")
        raise click.ClickException(str(e))
