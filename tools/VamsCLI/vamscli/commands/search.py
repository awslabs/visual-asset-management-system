"""Search commands for VamsCLI."""

import json
import csv
import sys
from io import StringIO
from typing import Dict, Any, List, Optional
import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context, requires_feature
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


def _parse_property_filters(property_filters_str: str) -> List[Dict[str, Any]]:
    """Parse property filters from JSON string."""
    try:
        filters = json.loads(property_filters_str)
        if not isinstance(filters, list):
            raise InvalidSearchParametersError("Property filters must be a JSON array")
        
        # Validate filter structure
        for filter_item in filters:
            if not isinstance(filter_item, dict):
                raise InvalidSearchParametersError("Each property filter must be a JSON object")
            
            required_fields = ['propertyKey', 'operator', 'value']
            for field in required_fields:
                if field not in filter_item:
                    raise InvalidSearchParametersError(f"Property filter missing required field: {field}")
        
        return filters
        
    except json.JSONDecodeError as e:
        raise InvalidSearchParametersError(f"Invalid JSON in property filters: {e}")
    except Exception as e:
        raise InvalidSearchParametersError(f"Failed to parse property filters: {e}")


def _parse_tags_list(tags_str: str) -> List[str]:
    """Parse comma-separated tags list."""
    if not tags_str:
        return []
    return [tag.strip() for tag in tags_str.split(',') if tag.strip()]


def _build_search_request(
    search_type: str,
    query: Optional[str] = None,
    database: Optional[str] = None,
    operation: str = "AND",
    sort_field: Optional[str] = None,
    sort_desc: bool = False,
    from_offset: int = 0,
    size: int = 100,
    asset_type: Optional[str] = None,
    file_ext: Optional[str] = None,
    tags: Optional[List[str]] = None,
    property_filters: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Build search request body."""
    
    # Map search type to record type
    record_type = "asset" if search_type == "asset" else "s3object"
    
    # Build base request
    request_body = {
        "tokens": property_filters or [],
        "operation": operation.upper(),
        "from": from_offset,
        "size": min(size, 2000),  # API limit
        "filters": [
            {
                "query_string": {
                    "query": f"(_rectype:(\"{record_type}\"))"
                }
            }
        ]
    }
    
    # Add general query if provided
    if query:
        request_body["query"] = query
    
    # Add sorting
    if sort_field:
        sort_field_index = sort_field
        if sort_field.startswith("str_"):
            sort_field_index = sort_field + ".raw"
        
        request_body["sort"] = [
            {
                sort_field_index: {
                    "missing": "_last",
                    "order": "desc" if sort_desc else "asc"
                }
            },
            "_score"
        ]
    else:
        request_body["sort"] = ["_score"]
    
    # Add database filter
    if database:
        request_body["filters"].append({
            "query_string": {
                "query": f"(str_databaseid:(\"{database}\"))"
            }
        })
    
    # Add asset type filter
    if asset_type:
        request_body["filters"].append({
            "query_string": {
                "query": f"(str_assettype:(\"{asset_type}\"))"
            }
        })
    
    # Add file extension filter
    if file_ext:
        request_body["filters"].append({
            "query_string": {
                "query": f"(str_fileext:(\"{file_ext}\"))"
            }
        })
    
    # Add tags filter
    if tags:
        tags_query = " OR ".join([f'"{tag}"' for tag in tags])
        request_body["filters"].append({
            "query_string": {
                "query": f"(list_tags:({tags_query}))"
            }
        })
    
    return request_body


def _format_search_results_table(results: Dict[str, Any], search_type: str) -> str:
    """Format search results as table."""
    hits = results.get("hits", {}).get("hits", [])
    total = results.get("hits", {}).get("total", {}).get("value", 0)
    
    if not hits:
        return f"No {search_type}s found."
    
    output = [f"Search Results ({total} found):\n"]
    
    for hit in hits:
        source = hit.get("_source", {})
        score = hit.get("_score", 0)
        
        if search_type == "asset":
            output.append(f"Asset: {source.get('str_assetname', 'N/A')}")
            output.append(f"Database: {source.get('str_databaseid', 'N/A')}")
            output.append(f"Type: {source.get('str_assettype', 'N/A')}")
            output.append(f"Description: {source.get('str_description', 'N/A')}")
            tags = source.get('list_tags', [])
            if tags:
                output.append(f"Tags: {', '.join(tags)}")
            output.append(f"Score: {score:.2f}")
            output.append("")
        else:  # file search
            output.append(f"File: {source.get('str_filename', 'N/A')}")
            output.append(f"Asset: {source.get('str_assetname', 'N/A')}")
            output.append(f"Database: {source.get('str_databaseid', 'N/A')}")
            output.append(f"Path: {source.get('str_key', 'N/A')}")
            if source.get('num_size'):
                size_mb = source.get('num_size', 0) / (1024 * 1024)
                output.append(f"Size: {size_mb:.2f} MB")
            output.append(f"Type: {source.get('str_fileext', 'N/A')}")
            output.append(f"Score: {score:.2f}")
            output.append("")
    
    return "\n".join(output)


def _format_search_results_csv(results: Dict[str, Any], search_type: str) -> str:
    """Format search results as CSV."""
    hits = results.get("hits", {}).get("hits", [])
    
    if not hits:
        return ""
    
    output = StringIO()
    
    if search_type == "asset":
        fieldnames = ['Asset Name', 'Database', 'Type', 'Description', 'Tags', 'Score']
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        
        for hit in hits:
            source = hit.get("_source", {})
            score = hit.get("_score", 0)
            tags = source.get('list_tags', [])
            
            writer.writerow({
                'Asset Name': source.get('str_assetname', 'N/A'),
                'Database': source.get('str_databaseid', 'N/A'),
                'Type': source.get('str_assettype', 'N/A'),
                'Description': source.get('str_description', 'N/A'),
                'Tags': ', '.join(tags) if tags else '',
                'Score': f"{score:.2f}"
            })
    else:  # file search
        fieldnames = ['File Name', 'Asset', 'Database', 'Path', 'Size (MB)', 'Type', 'Score']
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        
        for hit in hits:
            source = hit.get("_source", {})
            score = hit.get("_score", 0)
            size_mb = source.get('num_size', 0) / (1024 * 1024) if source.get('num_size') else 0
            
            writer.writerow({
                'File Name': source.get('str_filename', 'N/A'),
                'Asset': source.get('str_assetname', 'N/A'),
                'Database': source.get('str_databaseid', 'N/A'),
                'Path': source.get('str_key', 'N/A'),
                'Size (MB)': f"{size_mb:.2f}" if size_mb > 0 else 'N/A',
                'Type': source.get('str_fileext', 'N/A'),
                'Score': f"{score:.2f}"
            })
    
    return output.getvalue()


def _format_search_results_json(results: Dict[str, Any], search_type: str) -> str:
    """Format search results as JSON."""
    hits = results.get("hits", {}).get("hits", [])
    
    formatted_results = []
    for hit in hits:
        source = hit.get("_source", {})
        score = hit.get("_score", 0)
        
        if search_type == "asset":
            formatted_results.append({
                "assetName": source.get('str_assetname'),
                "database": source.get('str_databaseid'),
                "type": source.get('str_assettype'),
                "description": source.get('str_description'),
                "tags": source.get('list_tags', []),
                "score": score
            })
        else:  # file search
            result_item = {
                "fileName": source.get('str_filename'),
                "assetName": source.get('str_assetname'),
                "database": source.get('str_databaseid'),
                "path": source.get('str_key'),
                "type": source.get('str_fileext'),
                "score": score
            }
            if source.get('num_size'):
                result_item["sizeBytes"] = source.get('num_size')
                result_item["sizeMB"] = source.get('num_size') / (1024 * 1024)
            
            formatted_results.append(result_item)
    
    return json.dumps(formatted_results, indent=2)


def _format_mapping_table(mapping: Dict[str, Any]) -> str:
    """Format search mapping as table."""
    properties = mapping.get("mappings", {}).get("properties", {})
    
    if not properties:
        return "No search fields available."
    
    output = ["Available Search Fields:\n"]
    
    # Group fields by prefix
    field_groups = {}
    for field_name, field_info in properties.items():
        if "_" in field_name:
            prefix = field_name.split("_")[0]
            if prefix not in field_groups:
                field_groups[prefix] = []
            field_groups[prefix].append((field_name, field_info))
        else:
            if "other" not in field_groups:
                field_groups["other"] = []
            field_groups["other"].append((field_name, field_info))
    
    for prefix, fields in sorted(field_groups.items()):
        prefix_name = {
            "str": "String Fields",
            "num": "Numeric Fields", 
            "date": "Date Fields",
            "bool": "Boolean Fields",
            "list": "List Fields",
            "geo": "Geographic Fields",
            "other": "Other Fields"
        }.get(prefix, f"{prefix.upper()} Fields")
        
        output.append(f"{prefix_name}:")
        for field_name, field_info in sorted(fields):
            field_type = field_info.get("type", "unknown")
            display_name = field_name.replace("_", " ").title()
            output.append(f"  {field_name:<30} ({field_type}) - {display_name}")
        output.append("")
    
    return "\n".join(output)


def _format_mapping_csv(mapping: Dict[str, Any]) -> str:
    """Format search mapping as CSV."""
    properties = mapping.get("mappings", {}).get("properties", {})
    
    if not properties:
        return "Field Name,Field Type,Display Name\n"
    
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=['Field Name', 'Field Type', 'Display Name'])
    writer.writeheader()
    
    for field_name, field_info in sorted(properties.items()):
        field_type = field_info.get("type", "unknown")
        display_name = field_name.replace("_", " ").title()
        
        writer.writerow({
            'Field Name': field_name,
            'Field Type': field_type,
            'Display Name': display_name
        })
    
    return output.getvalue()


def _check_search_availability(profile_manager):
    """Check if search is available (not disabled by NOOPENSEARCH feature)."""
    if is_feature_enabled(FEATURE_NOOPENSEARCH, profile_manager):
        raise SearchDisabledError(
            "Search functionality is disabled for this environment. "
            "Use 'vamscli assets list' or 'vamscli database list-assets' instead."
        )


def _execute_paginated_search(
    api_client: APIClient, 
    base_request: Dict[str, Any], 
    max_results: Optional[int] = None,
    show_progress: bool = True
) -> List[Dict[str, Any]]:
    """Execute search with automatic pagination for large result sets."""
    all_hits = []
    current_from = base_request.get("from", 0)
    page_size = base_request.get("size", 100)
    page_num = 1
    
    while True:
        # Update pagination in request
        request_body = {**base_request, "from": current_from, "size": page_size}
        
        if show_progress and page_num > 1:
            click.echo(f"Fetching page {page_num}...", err=True)
        
        try:
            result = api_client.search_query(request_body)
            hits = result.get("hits", {}).get("hits", [])
            total_available = result.get("hits", {}).get("total", {}).get("value", 0)
            
            if not hits:
                break
            
            all_hits.extend(hits)
            
            # Check if we've reached max_results limit
            if max_results and len(all_hits) >= max_results:
                all_hits = all_hits[:max_results]
                break
            
            # Check if we've got all available results
            if len(all_hits) >= total_available:
                break
            
            # Check if we got fewer results than requested (last page)
            if len(hits) < page_size:
                break
            
            # Prepare for next page
            current_from += page_size
            page_num += 1
            
            # Safety check to prevent infinite loops
            if page_num > 100:  # Max 100 pages
                if show_progress:
                    click.echo("Reached maximum page limit (100 pages). Results may be incomplete.", err=True)
                break
                
        except Exception as e:
            if page_num == 1:
                # If first page fails, re-raise the error
                raise
            else:
                # If subsequent pages fail, return what we have
                if show_progress:
                    click.echo(f"Warning: Failed to fetch page {page_num}: {e}", err=True)
                break
    
    return all_hits


@click.group()
def search():
    """
    Search assets and files using OpenSearch.
    
    Note: Search functionality requires OpenSearch to be enabled. If search is disabled
    (NOOPENSEARCH feature is enabled), use 'vamscli assets list' or 'vamscli database list-assets' instead.
    
    Examples:
        vamscli search assets -q "model" -d my-database
        vamscli search files --file-ext "gltf"
        vamscli search mapping
    """
    pass


@search.command()
@click.option('-d', '--database', help='Database ID to search within')
@click.option('-q', '--query', help='General text search query')
@click.option('--operation', type=click.Choice(['AND', 'OR']), default='AND', 
              help='Token operation for property filters (default: AND)')
@click.option('--sort-field', help='Field to sort by (e.g., str_assetname, str_description)')
@click.option('--sort-desc', is_flag=True, help='Sort in descending order')
@click.option('--sort-asc', is_flag=True, help='Sort in ascending order (default)')
@click.option('--from', 'from_offset', type=int, default=0, help='Pagination start offset (default: 0)')
@click.option('--size', type=int, default=100, help='Number of results per page (default: 100, max: 2000)')
@click.option('--max-results', type=int, help='Maximum total results to fetch (default: unlimited)')
@click.option('--asset-type', help='Filter by asset type')
@click.option('--tags', help='Filter by tags (comma-separated)')
@click.option('--property-filters', help='JSON string of property filter tokens')
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.option('--jsonInput', help='JSON file with complete search parameters')
@click.option('--jsonOutput', is_flag=True, help='Output raw API response as JSON (legacy)')
@click.option('--show-progress/--no-progress', default=True, help='Show pagination progress')
@click.pass_context
@requires_setup_and_auth
def assets(ctx: click.Context, database: Optional[str], query: Optional[str], operation: str,
           sort_field: Optional[str], sort_desc: bool, sort_asc: bool, from_offset: int, size: int,
           max_results: Optional[int], asset_type: Optional[str], tags: Optional[str],
           property_filters: Optional[str], output_format: str, jsoninput: Optional[str],
           jsonoutput: bool, show_progress: bool):
    """
    Search assets using OpenSearch.
    
    Search across all assets with flexible filtering and sorting options.
    Supports general text search, property-based filtering, and various output formats.
    
    Examples:
        vamscli search assets -q "training model" -d my-database
        vamscli search assets --asset-type "3d-model" --tags "training,simulation"
        vamscli search assets --property-filters '[{"propertyKey":"str_description","operator":"=","value":"training"}]'
        vamscli search assets -q "model" --output-format csv > results.csv
        vamscli search assets --jsonInput search_params.json
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager)
        
        # Handle JSON input
        if jsoninput:
            json_data = _load_json_input(jsoninput)
            
            # Override command line parameters with JSON data
            database = json_data.get('database', database)
            query = json_data.get('query', query)
            operation = json_data.get('operation', operation)
            sort_field = json_data.get('sort_field', sort_field)
            sort_desc = json_data.get('sort_desc', sort_desc)
            from_offset = json_data.get('from', from_offset)
            size = json_data.get('size', size)
            max_results = json_data.get('max_results', max_results)
            asset_type = json_data.get('asset_type', asset_type)
            tags = json_data.get('tags', tags)
            property_filters = json.dumps(json_data.get('tokens', [])) if json_data.get('tokens') else property_filters
            output_format = json_data.get('output_format', output_format)
        
        # Validate sort options
        if sort_desc and sort_asc:
            raise click.ClickException("Cannot specify both --sort-desc and --sort-asc")
        
        # Parse property filters
        parsed_property_filters = None
        if property_filters:
            parsed_property_filters = _parse_property_filters(property_filters)
        
        # Parse tags
        parsed_tags = _parse_tags_list(tags) if tags else None
        
        # Build search request
        search_request = _build_search_request(
            search_type="asset",
            query=query,
            database=database,
            operation=operation,
            sort_field=sort_field,
            sort_desc=sort_desc,
            from_offset=from_offset,
            size=size,
            asset_type=asset_type,
            tags=parsed_tags,
            property_filters=parsed_property_filters
        )
        
        # Execute search with pagination if needed
        if max_results and max_results > 2000:
            # Use paginated search for large result sets
            all_hits = _execute_paginated_search(
                api_client, search_request, max_results, show_progress
            )
            
            # Reconstruct result structure
            result = {
                "hits": {
                    "hits": all_hits,
                    "total": {"value": len(all_hits)}
                }
            }
        else:
            # Single API call
            result = api_client.search_query(search_request)
        
        # Handle output format
        if jsonoutput:
            # Legacy JSON output - raw API response
            click.echo(json.dumps(result, indent=2))
        elif output_format == 'json':
            # Formatted JSON output
            formatted_output = _format_search_results_json(result, "asset")
            click.echo(formatted_output)
        elif output_format == 'csv':
            # CSV output
            formatted_output = _format_search_results_csv(result, "asset")
            click.echo(formatted_output)
        else:
            # Table output (default)
            formatted_output = _format_search_results_table(result, "asset")
            click.echo(formatted_output)
        
        # Show summary
        total = result.get("hits", {}).get("total", {}).get("value", 0)
        if not jsonoutput and output_format != 'csv':
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
@click.option('--operation', type=click.Choice(['AND', 'OR']), default='AND',
              help='Token operation for property filters (default: AND)')
@click.option('--sort-field', help='Field to sort by (e.g., str_filename, str_key)')
@click.option('--sort-desc', is_flag=True, help='Sort in descending order')
@click.option('--sort-asc', is_flag=True, help='Sort in ascending order (default)')
@click.option('--from', 'from_offset', type=int, default=0, help='Pagination start offset (default: 0)')
@click.option('--size', type=int, default=100, help='Number of results per page (default: 100, max: 2000)')
@click.option('--max-results', type=int, help='Maximum total results to fetch (default: unlimited)')
@click.option('--file-ext', help='Filter by file extension')
@click.option('--asset-type', help='Filter by parent asset type')
@click.option('--tags', help='Filter by tags (comma-separated)')
@click.option('--property-filters', help='JSON string of property filter tokens')
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.option('--jsonInput', help='JSON file with complete search parameters')
@click.option('--jsonOutput', is_flag=True, help='Output raw API response as JSON (legacy)')
@click.option('--show-progress/--no-progress', default=True, help='Show pagination progress')
@click.pass_context
@requires_setup_and_auth
def files(ctx: click.Context, database: Optional[str], query: Optional[str], operation: str,
          sort_field: Optional[str], sort_desc: bool, sort_asc: bool, from_offset: int, size: int,
          max_results: Optional[int], file_ext: Optional[str], asset_type: Optional[str], 
          tags: Optional[str], property_filters: Optional[str], output_format: str, 
          jsoninput: Optional[str], jsonoutput: bool, show_progress: bool):
    """
    Search files using OpenSearch.
    
    Search across all asset files with flexible filtering and sorting options.
    Supports general text search, property-based filtering, and various output formats.
    
    Examples:
        vamscli search files -q "texture" -d my-database
        vamscli search files --file-ext "gltf" --asset-type "3d-model"
        vamscli search files --property-filters '[{"propertyKey":"str_filename","operator":"=","value":"model.gltf"}]'
        vamscli search files -q "texture" --output-format csv > files.csv
        vamscli search files --jsonInput search_params.json
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Check if search is available
        _check_search_availability(profile_manager)
        
        # Handle JSON input
        if jsoninput:
            json_data = _load_json_input(jsoninput)
            
            # Override command line parameters with JSON data
            database = json_data.get('database', database)
            query = json_data.get('query', query)
            operation = json_data.get('operation', operation)
            sort_field = json_data.get('sort_field', sort_field)
            sort_desc = json_data.get('sort_desc', sort_desc)
            from_offset = json_data.get('from', from_offset)
            size = json_data.get('size', size)
            max_results = json_data.get('max_results', max_results)
            file_ext = json_data.get('file_ext', file_ext)
            asset_type = json_data.get('asset_type', asset_type)
            tags = json_data.get('tags', tags)
            property_filters = json.dumps(json_data.get('tokens', [])) if json_data.get('tokens') else property_filters
            output_format = json_data.get('output_format', output_format)
        
        # Validate sort options
        if sort_desc and sort_asc:
            raise click.ClickException("Cannot specify both --sort-desc and --sort-asc")
        
        # Parse property filters
        parsed_property_filters = None
        if property_filters:
            parsed_property_filters = _parse_property_filters(property_filters)
        
        # Parse tags
        parsed_tags = _parse_tags_list(tags) if tags else None
        
        # Build search request
        search_request = _build_search_request(
            search_type="file",
            query=query,
            database=database,
            operation=operation,
            sort_field=sort_field,
            sort_desc=sort_desc,
            from_offset=from_offset,
            size=size,
            asset_type=asset_type,
            file_ext=file_ext,
            tags=parsed_tags,
            property_filters=parsed_property_filters
        )
        
        # Execute search with pagination if needed
        if max_results and max_results > 2000:
            # Use paginated search for large result sets
            all_hits = _execute_paginated_search(
                api_client, search_request, max_results, show_progress
            )
            
            # Reconstruct result structure
            result = {
                "hits": {
                    "hits": all_hits,
                    "total": {"value": len(all_hits)}
                }
            }
        else:
            # Single API call
            result = api_client.search_query(search_request)
        
        # Handle output format
        if jsonoutput:
            # Legacy JSON output - raw API response
            click.echo(json.dumps(result, indent=2))
        elif output_format == 'json':
            # Formatted JSON output
            formatted_output = _format_search_results_json(result, "file")
            click.echo(formatted_output)
        elif output_format == 'csv':
            # CSV output
            formatted_output = _format_search_results_csv(result, "file")
            click.echo(formatted_output)
        else:
            # Table output (default)
            formatted_output = _format_search_results_table(result, "file")
            click.echo(formatted_output)
        
        # Show summary
        total = result.get("hits", {}).get("total", {}).get("value", 0)
        if not jsonoutput and output_format != 'csv':
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
@click.option('--output-format', type=click.Choice(['table', 'json', 'csv']), default='table',
              help='Output format (default: table)')
@click.option('--jsonOutput', is_flag=True, help='Output raw mapping as JSON (legacy)')
@click.pass_context
@requires_setup_and_auth
def mapping(ctx: click.Context, output_format: str, jsonoutput: bool):
    """
    Get search index mapping (available fields and types).
    
    Retrieves the OpenSearch index mapping showing all available fields
    that can be used in search queries and property filters.
    
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
        if jsonoutput:
            # Legacy JSON output - raw API response
            click.echo(json.dumps(mapping, indent=2))
        elif output_format == 'json':
            # Formatted JSON output
            click.echo(json.dumps(mapping, indent=2))
        elif output_format == 'csv':
            # CSV output
            formatted_output = _format_mapping_csv(mapping)
            click.echo(formatted_output)
        else:
            # Table output (default)
            formatted_output = _format_mapping_table(mapping)
            click.echo(formatted_output)
        
        # Show summary
        properties_count = len(mapping.get("mappings", {}).get("properties", {}))
        if not jsonoutput and output_format != 'csv':
            click.echo(
                click.style(f"✓ Retrieved mapping for {properties_count} search fields.", fg='green', bold=True)
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
