"""Search commands for VamsCLI - Dual-Index OpenSearch Support."""

import json
import csv
import sys
from io import StringIO
from typing import Dict, Any, List, Optional
import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_info
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


def _parse_filters(filters_str: Optional[str]) -> List[Dict[str, Any]]:
    """
    Parse filters from string input.
    
    Supports two formats:
    1. JSON array format: '[{"query_string": {"query": "field:value"}}]'
    2. Query string format: 'field1:"value1" AND field2:"value2"'
    
    Args:
        filters_str: Filter string in JSON or query string format
        
    Returns:
        List of filter dictionaries for OpenSearch
        
    Raises:
        InvalidSearchParametersError: If filter format is invalid
    """
    if not filters_str:
        return []
    
    filters_str = filters_str.strip()
    
    # Try JSON format first
    if filters_str.startswith('['):
        try:
            filters = json.loads(filters_str)
            if not isinstance(filters, list):
                raise InvalidSearchParametersError(
                    "JSON filters must be an array. "
                    "Example: '[{\"query_string\": {\"query\": \"field:value\"}}]'"
                )
            return filters
        except json.JSONDecodeError as e:
            raise InvalidSearchParametersError(f"Invalid JSON filter format: {e}")
    
    # Otherwise treat as query string format
    # Convert query string to OpenSearch filter
    return [{
        "query_string": {
            "query": filters_str
        }
    }]


def _build_sort_config(sort_field: Optional[str], sort_desc: bool) -> List[Any]:
    """Build sort configuration for search request."""
    if not sort_field:
        return ["_score"]
    
    return [{"field": sort_field, "order": "desc" if sort_desc else "asc"}]


def _format_table_output(result: Dict[str, Any], entity_type: str) -> str:
    """
    Format search results as a table.
    
    Args:
        result: Search result from API
        entity_type: Type of entity ('asset', 'file', or 'mixed')
        
    Returns:
        Formatted table string
    """
    hits = result.get("hits", {}).get("hits", [])
    
    if not hits:
        return "No results found."
    
    # Build table rows - include all fields from _source
    rows = []
    all_keys = set()
    
    # First pass: collect all unique keys from all results
    for hit in hits:
        source = hit.get("_source", {})
        all_keys.update(source.keys())
    
    # Sort keys for consistent column order
    sorted_keys = sorted(all_keys)
    
    # Second pass: build rows with all fields
    for hit in hits:
        source = hit.get("_source", {})
        row = {}
        
        for key in sorted_keys:
            value = source.get(key, "")
            # Handle list values (like tags)
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            # Handle dict values
            elif isinstance(value, dict):
                value = json.dumps(value)
            row[key] = str(value) if value else ""
        
        rows.append(row)
    
    if not rows:
        return "No results found."
    
    # Get headers (sorted keys)
    headers = sorted_keys
    
    # Calculate column widths
    col_widths = {h: len(h) for h in headers}
    for row in rows:
        for header in headers:
            col_widths[header] = max(col_widths[header], len(str(row.get(header, ""))))
    
    # Build table
    output = []
    
    # Header row
    header_row = " | ".join(h.ljust(col_widths[h]) for h in headers)
    output.append(header_row)
    
    # Separator
    separator = "-+-".join("-" * col_widths[h] for h in headers)
    output.append(separator)
    
    # Data rows
    for row in rows:
        data_row = " | ".join(str(row.get(h, "")).ljust(col_widths[h]) for h in headers)
        output.append(data_row)
    
    return "\n".join(output)


def _format_csv_output(result: Dict[str, Any], entity_type: str) -> str:
    """
    Format search results as CSV.
    
    Args:
        result: Search result from API
        entity_type: Type of entity ('asset', 'file', or 'mixed')
        
    Returns:
        CSV formatted string
    """
    hits = result.get("hits", {}).get("hits", [])
    
    if not hits:
        return ""
    
    output = StringIO()
    
    # Collect all unique keys from all results
    all_keys = set()
    for hit in hits:
        source = hit.get("_source", {})
        all_keys.update(source.keys())
    
    # Sort keys for consistent column order
    fieldnames = sorted(all_keys)
    
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    
    for hit in hits:
        source = hit.get("_source", {})
        row = {}
        
        for key in fieldnames:
            value = source.get(key, "")
            # Handle list values (like tags)
            if isinstance(value, list):
                value = ";".join(str(v) for v in value)  # Use semicolon for CSV
            # Handle dict values
            elif isinstance(value, dict):
                value = json.dumps(value)
            row[key] = str(value) if value else ""
        
        writer.writerow(row)
    
    return output.getvalue()


def _format_mapping_table(mapping: Dict[str, Any]) -> str:
    """
    Format search mapping as a table.
    
    Args:
        mapping: Search mapping from API
        
    Returns:
        Formatted table string
    """
    mappings = mapping.get("mappings", {})
    
    if not mappings:
        return "No mapping information available."
    
    rows = []
    
    for index_name, index_data in mappings.items():
        properties = index_data.get("mappings", {}).get("properties", {})
        
        for field_name, field_info in properties.items():
            field_type = field_info.get("type", "unknown")
            row = {
                "Index": index_name,
                "Field": field_name,
                "Type": field_type
            }
            rows.append(row)
    
    if not rows:
        return "No fields found in mapping."
    
    # Get headers
    headers = ["Index", "Field", "Type"]
    
    # Calculate column widths
    col_widths = {h: len(h) for h in headers}
    for row in rows:
        for header in headers:
            col_widths[header] = max(col_widths[header], len(str(row.get(header, ""))))
    
    # Build table
    output = []
    
    # Header row
    header_row = " | ".join(h.ljust(col_widths[h]) for h in headers)
    output.append(header_row)
    
    # Separator
    separator = "-+-".join("-" * col_widths[h] for h in headers)
    output.append(separator)
    
    # Data rows
    for row in rows:
        data_row = " | ".join(str(row.get(h, "")).ljust(col_widths[h]) for h in headers)
        output.append(data_row)
    
    return "\n".join(output)


def _format_mapping_csv(mapping: Dict[str, Any]) -> str:
    """
    Format search mapping as CSV.
    
    Args:
        mapping: Search mapping from API
        
    Returns:
        CSV formatted string
    """
    mappings = mapping.get("mappings", {})
    
    if not mappings:
        return ""
    
    output = StringIO()
    fieldnames = ["index", "field_name", "field_type"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    
    for index_name, index_data in mappings.items():
        properties = index_data.get("mappings", {}).get("properties", {})
        
        for field_name, field_info in properties.items():
            field_type = field_info.get("type", "unknown")
            writer.writerow({
                "index": index_name,
                "field_name": field_name,
                "field_type": field_type
            })
    
    return output.getvalue()


def _check_search_availability(profile_manager, json_output: bool = False):
    """Check if search is available (not disabled by NOOPENSEARCH feature)."""
    if is_feature_enabled(FEATURE_NOOPENSEARCH, profile_manager):
        raise SearchDisabledError(
            "Search functionality is disabled for this environment. "
            "Use 'vamscli assets list' instead."
        )


@click.group()
def search():
    """
    Search assets and files using OpenSearch dual-index system.
    
    Note: Search functionality requires OpenSearch to be enabled. If search is disabled
    (NOOPENSEARCH feature is enabled), use 'vamscli assets list' instead.
    
    The search system uses separate indexes for assets and files, allowing for optimized
    queries and better performance.
    
    Examples:
        vamscli search assets -q "model" --filters 'str_databaseid:"my-db"'
        vamscli search files --filters 'str_fileext:"gltf"'
        vamscli search simple -q "training" --entity-types asset
        vamscli search mapping
    """
    pass


@search.command()
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
@click.option('--filters', help='Advanced filters in JSON array or query string format (see examples)')
@click.option('--include-archived', is_flag=True, help='Include archived assets')
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def assets(ctx: click.Context, query: Optional[str], metadata_query: Optional[str],
           metadata_mode: str, include_metadata: bool, explain_results: bool,
           sort_field: Optional[str], sort_desc: bool, from_offset: int, size: int,
           filters: Optional[str], include_archived: bool,
           output_format: str, json_output: bool):
    """
    Search assets using OpenSearch with advanced filter support.
    
    Search across all assets with flexible filtering, metadata search, and sorting options.
    Supports general text search, metadata-specific search, and various output formats.
    
    Filter Examples:
        Query String Format:
            --filters 'str_databaseid:"my-db"'
            --filters 'str_databaseid:"my-db" AND str_assettype:"3d-model"'
            --filters 'list_tags:("training" OR "simulation")'
            --filters 'str_assetname:model* AND str_databaseid:"db-123"'
        
        JSON Format:
            --filters '[{"query_string": {"query": "str_databaseid:\\"my-db\\""}}]'
            --filters '[{"term": {"str_assettype": "3d-model"}}, {"range": {"num_version": {"gte": 1}}}]'
    
    Metadata Search Examples:
        --metadata-query "MD_str_product:Training"              # Exact field:value match
        --metadata-query "MD_str_product:Train*"                # Wildcard search
        --metadata-query "MD_str_product:A AND MD_num_version:1" # Multiple conditions
        --metadata-query "product" --metadata-mode key          # Search field names only
        --metadata-query "Training" --metadata-mode value       # Search values only
    
    General Examples:
        vamscli search assets -q "training model" --filters 'str_databaseid:"my-db"'
        vamscli search assets --filters 'str_assettype:"3d-model" AND list_tags:"training"'
        vamscli search assets -q "model" --metadata-query "MD_str_category:Training"
        vamscli search assets -q "model" --output-format csv > results.csv
        vamscli search assets -q "model" --explain-results --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Handle legacy jsonOutput flag
    if output_format == 'json':
        json_output = True
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager, json_output)
        
        output_status("Building search request...", json_output)
        
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
        
        # Parse and add filters
        if filters:
            parsed_filters = _parse_filters(filters)
            search_request["filters"] = parsed_filters
        
        output_status("Executing search...", json_output)
        
        # Execute search
        result = api_client.search_query(search_request)
        
        # Handle output format
        if json_output:
            output_result(result, json_output)
        elif output_format == 'table':
            total = result.get("hits", {}).get("total", {}).get("value", 0)
            
            def format_assets_table(data):
                """Format assets search results for CLI display."""
                lines = [f"\nFound {total} assets\n"]
                lines.append(_format_table_output(data, "asset"))
                return '\n'.join(lines)
            
            output_result(
                result,
                json_output,
                success_message=f"✓ Search completed. Found {total} assets.",
                cli_formatter=format_assets_table
            )
        elif output_format == 'csv':
            csv_output = _format_csv_output(result, "asset")
            # CSV output goes directly to stdout without formatting
            click.echo(csv_output)
        
        return result
    
    except SearchDisabledError as e:
        output_error(
            e,
            json_output,
            error_type="Search Disabled",
            helpful_message="Use 'vamscli assets list' instead."
        )
        raise click.ClickException(str(e))
    except InvalidSearchParametersError as e:
        output_error(
            e,
            json_output,
            error_type="Invalid Parameters",
            helpful_message="Check your search parameters and try again."
        )
        raise click.ClickException(str(e))
    except SearchQueryError as e:
        output_error(
            e,
            json_output,
            error_type="Search Query Error",
            helpful_message="Use 'vamscli search mapping' to see available search fields."
        )
        raise click.ClickException(str(e))


@search.command()
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
@click.option('--filters', help='Advanced filters in JSON array or query string format (see examples)')
@click.option('--include-archived', is_flag=True, help='Include archived files')
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def files(ctx: click.Context, query: Optional[str], metadata_query: Optional[str],
          metadata_mode: str, include_metadata: bool, explain_results: bool,
          sort_field: Optional[str], sort_desc: bool, from_offset: int, size: int,
          filters: Optional[str], include_archived: bool,
          output_format: str, json_output: bool):
    """
    Search files using OpenSearch with advanced filter support.
    
    Search across all asset files with flexible filtering, metadata search, and sorting options.
    Supports general text search, metadata-specific search, and various output formats.
    
    Filter Examples:
        Query String Format:
            --filters 'str_databaseid:"my-db"'
            --filters 'str_fileext:"gltf"'
            --filters 'str_fileext:"gltf" AND str_databaseid:"my-db"'
            --filters 'list_tags:("ui" OR "interface")'
            --filters 'str_key:*texture* AND str_fileext:"png"'
        
        JSON Format:
            --filters '[{"query_string": {"query": "str_fileext:\\"gltf\\""}}]'
            --filters '[{"term": {"str_fileext": "png"}}, {"range": {"num_filesize": {"lte": 1048576}}}]'
    
    Examples:
        vamscli search files -q "texture" --filters 'str_databaseid:"my-db"'
        vamscli search files --filters 'str_fileext:"gltf"'
        vamscli search files -q "texture" --output-format csv > files.csv
        vamscli search files -q "texture" --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Handle legacy jsonOutput flag
    if output_format == 'json':
        json_output = True
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager, json_output)
        
        output_status("Building search request...", json_output)
        
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
        
        # Parse and add filters
        if filters:
            parsed_filters = _parse_filters(filters)
            search_request["filters"] = parsed_filters
        
        output_status("Executing search...", json_output)
        
        # Execute search
        result = api_client.search_query(search_request)
        
        # Handle output format
        if json_output:
            output_result(result, json_output)
        elif output_format == 'table':
            total = result.get("hits", {}).get("total", {}).get("value", 0)
            
            def format_files_table(data):
                """Format files search results for CLI display."""
                lines = [f"\nFound {total} files\n"]
                lines.append(_format_table_output(data, "file"))
                return '\n'.join(lines)
            
            output_result(
                result,
                json_output,
                success_message=f"✓ Search completed. Found {total} files.",
                cli_formatter=format_files_table
            )
        elif output_format == 'csv':
            csv_output = _format_csv_output(result, "file")
            # CSV output goes directly to stdout without formatting
            click.echo(csv_output)
        
        return result
    
    except SearchDisabledError as e:
        output_error(
            e,
            json_output,
            error_type="Search Disabled",
            helpful_message="Use 'vamscli assets list' instead."
        )
        raise click.ClickException(str(e))
    except InvalidSearchParametersError as e:
        output_error(
            e,
            json_output,
            error_type="Invalid Parameters",
            helpful_message="Check your search parameters and try again."
        )
        raise click.ClickException(str(e))
    except SearchQueryError as e:
        output_error(
            e,
            json_output,
            error_type="Search Query Error",
            helpful_message="Use 'vamscli search mapping' to see available search fields."
        )
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
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def simple(ctx: click.Context, query: Optional[str], asset_name: Optional[str], asset_id: Optional[str],
           asset_type: Optional[str], file_key: Optional[str], file_ext: Optional[str],
           database: Optional[str], tags: Optional[str], metadata_key: Optional[str],
           metadata_value: Optional[str], entity_types: Optional[str], include_archived: bool,
           from_offset: int, size: int, output_format: str, json_output: bool):
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
        vamscli search simple -q "model" --output-format csv > results.csv
        vamscli search simple -q "model" --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Handle legacy output format
    if output_format == 'json':
        json_output = True
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager, json_output)
        
        output_status("Building simple search request...", json_output)
        
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
        
        output_status("Executing search...", json_output)
        
        # Execute simple search
        result = api_client.search_simple(search_request)
        
        # Handle output format
        if json_output:
            output_result(result, json_output)
        elif output_format == 'table':
            total = result.get("hits", {}).get("total", {}).get("value", 0)
            
            def format_simple_table(data):
                """Format simple search results for CLI display."""
                lines = [f"\nFound {total} results\n"]
                lines.append(_format_table_output(data, "mixed"))
                return '\n'.join(lines)
            
            output_result(
                result,
                json_output,
                success_message=f"✓ Search completed. Found {total} results.",
                cli_formatter=format_simple_table
            )
        elif output_format == 'csv':
            csv_output = _format_csv_output(result, "mixed")
            # CSV output goes directly to stdout without formatting
            click.echo(csv_output)
        
        return result
    
    except SearchDisabledError as e:
        output_error(
            e,
            json_output,
            error_type="Search Disabled",
            helpful_message="Use 'vamscli assets list' instead."
        )
        raise click.ClickException(str(e))
    except InvalidSearchParametersError as e:
        output_error(
            e,
            json_output,
            error_type="Invalid Parameters",
            helpful_message="Check your search parameters and try again."
        )
        raise click.ClickException(str(e))


@search.command()
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def mapping(ctx: click.Context, output_format: str, json_output: bool):
    """
    Get search index mapping (available fields and types).
    
    Retrieves the OpenSearch index mapping showing all available fields
    that can be used in search queries. The dual-index system has separate
    mappings for asset and file indexes.
    
    Examples:
        vamscli search mapping
        vamscli search mapping --output-format csv
        vamscli search mapping --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    # Handle legacy output format
    if output_format == 'json':
        json_output = True
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager, json_output)
        
        output_status("Retrieving search index mappings...", json_output)
        
        # Get search mapping
        mapping = api_client.get_search_mapping()
        
        # Handle output format
        if json_output:
            output_result(mapping, json_output)
        elif output_format == 'table':
            def format_mapping_table_output(data):
                """Format mapping for CLI display."""
                lines = ["\nSearch Index Mappings\n"]
                lines.append(_format_mapping_table(data))
                return '\n'.join(lines)
            
            output_result(
                mapping,
                json_output,
                success_message="✓ Retrieved search index mappings.",
                cli_formatter=format_mapping_table_output
            )
        elif output_format == 'csv':
            csv_output = _format_mapping_csv(mapping)
            # CSV output goes directly to stdout without formatting
            click.echo(csv_output)
        
        return mapping
    
    except SearchDisabledError as e:
        output_error(
            e,
            json_output,
            error_type="Search Disabled",
            helpful_message="Use 'vamscli assets list' instead."
        )
        raise click.ClickException(str(e))
    except SearchMappingError as e:
        output_error(
            e,
            json_output,
            error_type="Search Mapping Error",
            helpful_message="Check if search is properly configured."
        )
        raise click.ClickException(str(e))
