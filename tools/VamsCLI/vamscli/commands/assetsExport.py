"""Asset export commands for VamsCLI."""

import asyncio
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, List

import click

from ..constants import (
    API_ASSET_EXPORT, DEFAULT_PARALLEL_DOWNLOADS, DEFAULT_DOWNLOAD_TIMEOUT
)
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_info, output_warning
from ..utils.exceptions import (
    AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError, APIError,
    FileDownloadError
)
from ..utils.download_manager import (
    DownloadManager, DownloadFileInfo, DownloadProgress, format_file_size, format_duration
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


def normalize_file_extensions(extensions: List[str]) -> List[str]:
    """Normalize file extensions to include leading dot."""
    normalized = []
    for ext in extensions:
        ext = ext.strip()
        if not ext.startswith('.'):
            ext = '.' + ext
        normalized.append(ext.lower())
    return normalized


def export_with_auto_pagination(
    api_client: APIClient,
    database_id: str,
    asset_id: str,
    export_params: Dict[str, Any],
    json_output: bool
) -> Dict[str, Any]:
    """
    Automatically fetch all pages and combine results.
    
    Args:
        api_client: API client instance
        database_id: Database ID
        asset_id: Asset ID
        export_params: Export parameters
        json_output: Whether JSON output mode is enabled
    
    Returns:
        Combined export data from all pages
    """
    all_assets = []
    all_relationships = []
    total_assets_in_tree = 0
    next_token = None
    page_count = 0
    
    while True:
        page_count += 1
        
        # Status update (CLI mode only)
        if page_count == 1:
            output_status(f"Fetching page {page_count}...", json_output)
        else:
            output_status(
                f"Fetching page {page_count} (retrieved {len(all_assets)} assets so far)...",
                json_output
            )
        
        # Add pagination token to params
        if next_token:
            export_params['startingToken'] = next_token
        
        # Fetch page
        result = api_client.export_asset(database_id, asset_id, export_params)
        
        # Accumulate results
        all_assets.extend(result.get('assets', []))
        
        # Relationships only on first page
        if page_count == 1 and result.get('relationships'):
            all_relationships = result.get('relationships', [])
        
        total_assets_in_tree = result.get('totalAssetsInTree', 0)
        next_token = result.get('NextToken')
        
        # Break if no more pages
        if not next_token:
            break
    
    # Return combined result
    return {
        'assets': all_assets,
        'relationships': all_relationships,
        'totalAssetsInTree': total_assets_in_tree,
        'assetsRetrieved': len(all_assets),
        'pagesRetrieved': page_count,
        'autoPaginated': True
    }


def export_single_page(
    api_client: APIClient,
    database_id: str,
    asset_id: str,
    export_params: Dict[str, Any],
    json_output: bool
) -> Dict[str, Any]:
    """
    Fetch a single page (manual pagination).
    
    Args:
        api_client: API client instance
        database_id: Database ID
        asset_id: Asset ID
        export_params: Export parameters
        json_output: Whether JSON output mode is enabled
    
    Returns:
        Single page export data
    """
    output_status("Fetching asset export data...", json_output)
    
    result = api_client.export_asset(database_id, asset_id, export_params)
    
    # Add pagination hint if more data exists (only for CLI mode)
    if result.get('NextToken') and not json_output:
        result['paginationHint'] = (
            "More data available. Use --starting-token with the returned token "
            "or use --auto-paginate to fetch all pages automatically."
        )
    
    return result


class DownloadProgressDisplay:
    """Display download progress in the terminal."""
    
    def __init__(self, hide_progress: bool = False):
        self.hide_progress = hide_progress
        self.last_update = 0
        self.update_interval = 0.5  # Update every 500ms
        
    def update(self, progress: DownloadProgress):
        """Update the progress display."""
        if self.hide_progress:
            return
            
        current_time = time.time()
        if current_time - self.last_update < self.update_interval:
            return
            
        self.last_update = current_time
        
        # Overall progress
        overall_pct = progress.overall_progress
        completed_size_str = format_file_size(progress.completed_size)
        total_size_str = format_file_size(progress.total_size)
        
        # Progress bar
        bar_width = 40
        filled = int(bar_width * overall_pct / 100)
        bar = '█' * filled + '░' * (bar_width - filled)
        
        output_info(f"\nDownload Progress: [{bar}] {overall_pct:.1f}% ({completed_size_str}/{total_size_str})", False)
        
        # Speed and ETA
        speed_str = format_file_size(int(progress.download_speed)) + "/s"
        eta = progress.estimated_time_remaining
        eta_str = format_duration(eta) if eta else "calculating..."
        
        output_info(f"Speed: {speed_str} | Active: {progress.active_downloads} | ETA: {eta_str}", False)


async def download_export_files(
    export_result: Dict[str, Any],
    local_path: Path,
    organize_by_asset: bool,
    flatten_downloads: bool,
    parallel_downloads: int,
    download_timeout: int,
    hide_progress: bool,
    json_output: bool
) -> Dict[str, Any]:
    """
    Download files from export result using presigned URLs.
    
    Args:
        export_result: Export result containing assets with presigned URLs
        local_path: Local directory for downloads
        organize_by_asset: Organize files by asset ID
        flatten_downloads: Save all files flat (ignore structure)
        parallel_downloads: Max parallel downloads
        download_timeout: Timeout per file
        hide_progress: Hide progress display
        json_output: Whether JSON output mode is enabled
    
    Returns:
        Download results dictionary
    """
    # Extract download info from export result
    download_files = []
    skipped_unauthorized = 0
    
    for asset in export_result.get('assets', []):
        # Skip unauthorized assets (they don't have file data)
        if asset.get('unauthorizedAsset'):
            skipped_unauthorized += 1
            continue
        
        asset_id = asset.get('assetid')
        
        for file in asset.get('files', []):
            # Skip folders
            if file.get('isFolder'):
                continue
            
            presigned_url = file.get('presignedFileDownloadUrl')
            if not presigned_url:
                continue
            
            # Determine local path based on organization strategy
            if flatten_downloads:
                file_local_path = local_path / Path(file['fileName'])
            elif organize_by_asset:
                file_local_path = local_path / asset_id / Path(file['fileName'])
            else:
                # Preserve full structure
                relative_path = file['relativePath'].lstrip('/')
                file_local_path = local_path / asset_id / relative_path
            
            download_files.append(DownloadFileInfo(
                relative_key=f"{asset_id}/{file['relativePath']}",
                local_path=file_local_path,
                download_url=presigned_url,
                file_size=file.get('size')
            ))
    
    if not download_files:
        message = 'No files to download'
        if skipped_unauthorized > 0:
            message += f' ({skipped_unauthorized} unauthorized asset(s) skipped)'
        return {
            'overall_success': True,
            'total_files': 0,
            'successful_files': 0,
            'failed_files': 0,
            'skipped_unauthorized_assets': skipped_unauthorized,
            'message': message
        }
    
    # Create progress callback
    progress_display = DownloadProgressDisplay(hide_progress=hide_progress or json_output)
    
    def progress_callback(progress: DownloadProgress):
        progress_display.update(progress)
    
    # Download files using DownloadManager
    async with DownloadManager(
        api_client=None,  # Not needed for presigned URLs
        max_parallel=parallel_downloads,
        max_retries=3,
        timeout=download_timeout,
        progress_callback=progress_callback
    ) as manager:
        result = await manager.download_files(download_files)
        
        # Add unauthorized asset count to result
        if skipped_unauthorized > 0:
            result['skipped_unauthorized_assets'] = skipped_unauthorized
        
        return result


def combine_download_results(download_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Combine download results from multiple pages."""
    if not download_results:
        return {
            'overall_success': True,
            'total_files': 0,
            'successful_files': 0,
            'failed_files': 0
        }
    
    combined = {
        'overall_success': all(r.get('overall_success', False) for r in download_results),
        'total_files': sum(r.get('total_files', 0) for r in download_results),
        'successful_files': sum(r.get('successful_files', 0) for r in download_results),
        'failed_files': sum(r.get('failed_files', 0) for r in download_results),
        'total_size': sum(r.get('total_size', 0) for r in download_results),
        'total_size_formatted': format_file_size(sum(r.get('total_size', 0) for r in download_results)),
        'download_duration': sum(r.get('download_duration', 0) for r in download_results),
        'successful_downloads': [],
        'failed_downloads': []
    }
    
    # Combine successful and failed downloads
    for result in download_results:
        combined['successful_downloads'].extend(result.get('successful_downloads', []))
        combined['failed_downloads'].extend(result.get('failed_downloads', []))
    
    # Calculate average speed
    if combined['download_duration'] > 0:
        combined['average_speed'] = combined['total_size'] / combined['download_duration']
        combined['average_speed_formatted'] = format_file_size(int(combined['average_speed'])) + "/s"
    else:
        combined['average_speed'] = 0
        combined['average_speed_formatted'] = "0 B/s"
    
    return combined


def format_export_result_cli(data: Dict[str, Any]) -> str:
    """Format export result for CLI display."""
    lines = []
    
    # Export summary
    lines.append("Export Summary:")
    
    # Check if auto-paginated
    if data.get('autoPaginated'):
        lines.append(f"  Total assets in tree: {data.get('totalAssetsInTree', 0):,}")
        lines.append(f"  Assets retrieved: {data.get('assetsRetrieved', 0):,}")
        lines.append(f"  Pages retrieved: {data.get('pagesRetrieved', 0)}")
        
        relationships = data.get('relationships', [])
        if relationships:
            lines.append(f"  Relationships: {len(relationships):,}")
        
        # Check for unauthorized assets
        assets = data.get('assets', [])
        unauthorized_count = sum(1 for asset in assets if asset.get('unauthorizedAsset'))
        if unauthorized_count > 0:
            lines.append(f"  Unauthorized assets (skipped): {unauthorized_count:,}")
        
        lines.append("")
        lines.append("  All data has been retrieved and combined.")
    else:
        # Single page result
        lines.append(f"  Assets in this page: {data.get('assetsInThisPage', 0):,}")
        lines.append(f"  Total assets in tree: {data.get('totalAssetsInTree', 0):,}")
        
        relationships = data.get('relationships', [])
        if relationships:
            lines.append(f"  Relationships: {len(relationships):,}")
        
        # Check for unauthorized assets
        assets = data.get('assets', [])
        unauthorized_count = sum(1 for asset in assets if asset.get('unauthorizedAsset'))
        if unauthorized_count > 0:
            lines.append(f"  Unauthorized assets (skipped): {unauthorized_count:,}")
        
        # Show pagination info
        if data.get('NextToken'):
            lines.append("")
            lines.append("More data available. Use the NextToken below for the next page:")
            lines.append(f"{data['NextToken']}")
            lines.append("")
            lines.append("Or use --auto-paginate to fetch all pages automatically.")
    
    # Download summary if present
    download_results = data.get('downloadResults')
    if download_results:
        lines.append("")
        lines.append("Download Summary:")
        lines.append(f"  Total files: {download_results.get('total_files', 0):,}")
        lines.append(f"  Successfully downloaded: {download_results.get('successful_files', 0):,}")
        lines.append(f"  Failed: {download_results.get('failed_files', 0):,}")
        
        # Show skipped unauthorized assets if any
        skipped_unauthorized = download_results.get('skipped_unauthorized_assets', 0)
        if skipped_unauthorized > 0:
            lines.append(f"  Skipped (unauthorized): {skipped_unauthorized:,} asset(s)")
        
        lines.append(f"  Total size: {download_results.get('total_size_formatted', 'N/A')}")
        lines.append(f"  Duration: {format_duration(download_results.get('download_duration', 0))}")
        lines.append(f"  Average speed: {download_results.get('average_speed_formatted', 'N/A')}")
        
        # Show failed downloads
        failed_downloads = download_results.get('failed_downloads', [])
        if failed_downloads:
            lines.append("")
            lines.append(f"Failed downloads ({len(failed_downloads)}):")
            for failed in failed_downloads[:5]:
                lines.append(f"  • {failed['relative_key']}: {failed['error']}")
            
            if len(failed_downloads) > 5:
                lines.append(f"  ... and {len(failed_downloads) - 5} more")
    
    # Show sample of assets (first 3) if no downloads
    if not download_results:
        assets = data.get('assets', [])
        # Filter out unauthorized assets for display
        authorized_assets = [a for a in assets if not a.get('unauthorizedAsset')]
        
        if authorized_assets:
            lines.append("")
            lines.append(f"Sample assets (showing {min(3, len(authorized_assets))} of {len(authorized_assets)}):")
            for i, asset in enumerate(authorized_assets[:3]):
                lines.append(f"  {i+1}. {asset.get('assetname', 'N/A')} (ID: {asset.get('assetid', 'N/A')})")
                lines.append(f"     Database: {asset.get('databaseid', 'N/A')}")
                lines.append(f"     Files: {len(asset.get('files', []))}")
                if asset.get('metadata'):
                    lines.append(f"     Metadata fields: {len(asset.get('metadata', {}))}")
    
    return '\n'.join(lines)


@click.group()
def assets_export():
    """Asset export commands for comprehensive data retrieval."""
    pass


@assets_export.command(name='export')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID (root of tree)')
@click.option('--auto-paginate/--no-auto-paginate', default=True,
              help='[OPTIONAL, default: True] Automatically fetch all pages and combine results')
@click.option('--max-assets', type=int, default=100,
              help='[OPTIONAL, default: 100] Maximum assets per page (1-1000)')
@click.option('--starting-token', 
              help='[OPTIONAL] Pagination token from previous response (manual pagination)')
@click.option('--generate-presigned-urls', is_flag=True,
              help='[OPTIONAL] Generate presigned download URLs for files')
@click.option('--download-files', is_flag=True,
              help='[OPTIONAL] Download files to local directory (requires --local-path)')
@click.option('--local-path', type=click.Path(),
              help='[OPTIONAL] Local directory for downloaded files (required with --download-files)')
@click.option('--organize-by-asset', is_flag=True,
              help='[OPTIONAL] Organize downloads by asset ID (creates subdirectories)')
@click.option('--flatten-downloads', is_flag=True,
              help='[OPTIONAL] Save all files flat (ignore folder structure)')
@click.option('--parallel-downloads', type=int, default=DEFAULT_PARALLEL_DOWNLOADS,
              help=f'[OPTIONAL, default: {DEFAULT_PARALLEL_DOWNLOADS}] Max parallel downloads')
@click.option('--download-timeout', type=int, default=DEFAULT_DOWNLOAD_TIMEOUT,
              help=f'[OPTIONAL, default: {DEFAULT_DOWNLOAD_TIMEOUT}] Download timeout per file in seconds')
@click.option('--hide-download-progress', is_flag=True,
              help='[OPTIONAL] Hide download progress display')
@click.option('--include-folder-files', is_flag=True,
              help='[OPTIONAL] Include folder files in export')
@click.option('--include-only-primary-type-files', is_flag=True,
              help='[OPTIONAL] Include only files with primaryType set')
@click.option('--no-file-metadata', is_flag=True,
              help='[OPTIONAL, default: False] Exclude file metadata from export')
@click.option('--no-asset-link-metadata', is_flag=True,
              help='[OPTIONAL, default: False] Exclude asset link metadata from export')
@click.option('--no-asset-metadata', is_flag=True,
              help='[OPTIONAL, default: False] Exclude asset metadata from export')
@click.option('--no-fetch-relationships', is_flag=True,
              help='[OPTIONAL, default: False] Skip fetching asset relationships (single asset mode)')
@click.option('--fetch-entire-subtrees', is_flag=True,
              help='[OPTIONAL, default: False] Fetch entire children relationship sub-trees (full tree)')
@click.option('--include-parent-relationships', is_flag=True,
              help='[OPTIONAL, default: False] Include parent relationships in the relationship data')
@click.option('--include-archived-files', is_flag=True,
              help='[OPTIONAL, default: False] Include archived files in export')
@click.option('--file-extensions', multiple=True,
              help='[OPTIONAL] Filter files by extension (e.g., .gltf .bin). Can be used multiple times.')
@click.option('--json-input', 
              help='[OPTIONAL] JSON input file path or JSON string with all parameters')
@click.option('--json-output', is_flag=True, 
              help='[OPTIONAL] Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def export_command(
    ctx: click.Context,
    database_id: str,
    asset_id: str,
    auto_paginate: bool,
    max_assets: int,
    starting_token: Optional[str],
    generate_presigned_urls: bool,
    download_files: bool,
    local_path: Optional[str],
    organize_by_asset: bool,
    flatten_downloads: bool,
    parallel_downloads: int,
    download_timeout: int,
    hide_download_progress: bool,
    include_folder_files: bool,
    include_only_primary_type_files: bool,
    no_file_metadata: bool,
    no_asset_link_metadata: bool,
    no_asset_metadata: bool,
    no_fetch_relationships: bool,
    fetch_entire_subtrees: bool,
    include_parent_relationships: bool,
    include_archived_files: bool,
    file_extensions: List[str],
    json_input: Optional[str],
    json_output: bool
):
    """
    Export comprehensive asset data including metadata, files, and relationships.
    
    This command provides a powerful way to export complete asset information for
    downstream consumption. It supports two pagination modes:
    
    \b
    1. AUTO-PAGINATION (Default, recommended for complete exports):
       Automatically fetches all pages and combines results.
       This is enabled by default. Use --no-auto-paginate to disable.
       Example: vamscli assets export -d my-db -a root-asset
    
    \b
    2. MANUAL PAGINATION (For incremental processing):
       Fetch one page at a time using pagination tokens.
       Use --no-auto-paginate to enable manual pagination.
       Example: vamscli assets export -d my-db -a root-asset --no-auto-paginate --max-assets 100
       Then: vamscli assets export -d my-db -a root-asset --no-auto-paginate --starting-token "..."
    
    The export includes:
    - Complete asset metadata (name, description, tags, version info)
    - File information with metadata and version details
    - Asset link relationships with metadata (unless --no-fetch-relationships)
    - Optional presigned URLs for file downloads
    
    \b
    Relationship Fetching Options:
        By default, the command fetches asset relationships (parent-child links).
        Use --no-fetch-relationships to export only the single root asset.
        Use --fetch-entire-subtrees to fetch the complete descendant tree.
        Without --fetch-entire-subtrees, only root + 1 level is fetched.
    
    \b
    Examples:
        # Export with auto-pagination (default behavior)
        vamscli assets export -d my-database -a my-asset
        
        # Export single page (manual pagination)
        vamscli assets export -d my-database -a my-asset --no-auto-paginate --max-assets 100
        
        # Export single asset without relationships
        vamscli assets export -d my-db -a my-asset --no-fetch-relationships
        
        # Export with full tree (all descendants)
        vamscli assets export -d my-db -a my-asset --fetch-entire-subtrees
        
        # Export with filters
        vamscli assets export -d my-db -a my-asset \\
          --file-extensions .gltf .bin --generate-presigned-urls
        
        # Export only primary type files
        vamscli assets export -d my-db -a my-asset \\
          --include-only-primary-type-files
        
        # Include archived files
        vamscli assets export -d my-db -a my-asset --include-archived-files
        
        # Manual pagination (first page)
        vamscli assets export -d my-db -a my-asset --no-auto-paginate --max-assets 100
        
        # Manual pagination (subsequent page)
        vamscli assets export -d my-db -a my-asset --no-auto-paginate --starting-token "eyJ..."
        
        # JSON output for downstream processing
        vamscli assets export -d my-db -a my-asset --json-output
        
        # Complex parameters via JSON input
        vamscli assets export -d my-db -a my-asset --json-input export-params.json
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Parse JSON input if provided
        if json_input:
            json_data = parse_json_input(json_input)
            
            # Override command line parameters with JSON data
            database_id = json_data.get('databaseId', database_id)
            asset_id = json_data.get('assetId', asset_id)
            auto_paginate = json_data.get('autoPaginate', auto_paginate)
            max_assets = json_data.get('maxAssets', max_assets)
            starting_token = json_data.get('startingToken', starting_token)
            generate_presigned_urls = json_data.get('generatePresignedUrls', generate_presigned_urls)
            download_files = json_data.get('downloadFiles', download_files)
            local_path = json_data.get('localPath', local_path)
            organize_by_asset = json_data.get('organizeByAsset', organize_by_asset)
            flatten_downloads = json_data.get('flattenDownloads', flatten_downloads)
            parallel_downloads = json_data.get('parallelDownloads', parallel_downloads)
            download_timeout = json_data.get('downloadTimeout', download_timeout)
            hide_download_progress = json_data.get('hideDownloadProgress', hide_download_progress)
            include_folder_files = json_data.get('includeFolderFiles', include_folder_files)
            include_only_primary_type_files = json_data.get('includeOnlyPrimaryTypeFiles', include_only_primary_type_files)
            no_file_metadata = json_data.get('noFileMetadata', no_file_metadata)
            no_asset_link_metadata = json_data.get('noAssetLinkMetadata', no_asset_link_metadata)
            no_asset_metadata = json_data.get('noAssetMetadata', no_asset_metadata)
            no_fetch_relationships = json_data.get('noFetchRelationships', no_fetch_relationships)
            fetch_entire_subtrees = json_data.get('fetchEntireSubtrees', fetch_entire_subtrees)
            include_parent_relationships = json_data.get('includeParentRelationships', include_parent_relationships)
            include_archived_files = json_data.get('includeArchivedFiles', include_archived_files)
            file_extensions = json_data.get('fileExtensions', list(file_extensions) if file_extensions else [])
        
        # Validate download-related options
        if download_files and not local_path:
            raise click.ClickException(
                "Option --download-files requires --local-path to be specified. "
                "Provide a local directory path where files should be downloaded."
            )
        
        if (organize_by_asset or flatten_downloads) and not download_files:
            raise click.ClickException(
                "Options --organize-by-asset and --flatten-downloads require --download-files to be enabled."
            )
        
        if organize_by_asset and flatten_downloads:
            raise click.ClickException(
                "Options --organize-by-asset and --flatten-downloads cannot be used together. "
                "Choose one file organization strategy."
            )
        
        # Auto-enable presigned URLs if downloading
        if download_files and not generate_presigned_urls:
            generate_presigned_urls = True
            output_info("Auto-enabling --generate-presigned-urls for file downloads", json_output)
        
        # Validate mutually exclusive options
        if auto_paginate and starting_token:
            raise click.ClickException(
                "Options --auto-paginate and --starting-token cannot be used together. "
                "Use --auto-paginate for automatic pagination or --starting-token for manual pagination."
            )
        
        # Validate max_assets range
        if max_assets < 1 or max_assets > 1000:
            raise click.BadParameter(
                "max-assets must be between 1 and 1000",
                param_hint="--max-assets"
            )
        
        # Build export parameters
        export_params = {
            'generatePresignedUrls': generate_presigned_urls,
            'includeFolderFiles': include_folder_files,
            'includeOnlyPrimaryTypeFiles': include_only_primary_type_files,
            'includeFileMetadata': not no_file_metadata,
            'includeAssetLinkMetadata': not no_asset_link_metadata,
            'includeAssetMetadata': not no_asset_metadata,
            'fetchAssetRelationships': not no_fetch_relationships,
            'fetchEntireChildrenSubtrees': fetch_entire_subtrees,
            'includeParentRelationships': include_parent_relationships,
            'includeArchivedFiles': include_archived_files,
            'maxAssets': max_assets
        }
        
        # Add file extensions if provided
        if file_extensions:
            export_params['fileExtensions'] = normalize_file_extensions(list(file_extensions))
        
        # Add next token if provided (manual pagination)
        if starting_token:
            export_params['startingToken'] = starting_token
        
        # Execute export based on pagination mode
        if auto_paginate:
            # Auto-pagination mode
            result = export_with_auto_pagination(
                api_client,
                database_id,
                asset_id,
                export_params,
                json_output
            )
        else:
            # Manual pagination mode (single page)
            result = export_single_page(
                api_client,
                database_id,
                asset_id,
                export_params,
                json_output
            )
        
        # Determine success message
        success_msg = "✓ Export completed successfully!"
        
        # Download files if requested
        if download_files:
            output_status(f"Downloading files to {local_path}...", json_output)
            
            # Convert local_path to Path object
            download_path = Path(local_path)
            
            # Check if presigned URLs are available
            has_urls = any(
                file.get('presignedFileDownloadUrl')
                for asset in result.get('assets', [])
                for file in asset.get('files', [])
            )
            
            if not has_urls:
                output_warning(
                    "No presigned URLs found in export result. Files cannot be downloaded. "
                    "Ensure --generate-presigned-urls is enabled.",
                    json_output
                )
            else:
                # Download files asynchronously
                download_result = asyncio.run(
                    download_export_files(
                        result,
                        download_path,
                        organize_by_asset,
                        flatten_downloads,
                        parallel_downloads,
                        download_timeout,
                        hide_download_progress,
                        json_output
                    )
                )
                
                # Add download results to output
                result['downloadResults'] = download_result
                
                # Update success message based on download results
                if download_result.get('failed_files', 0) > 0:
                    success_msg = "⚠ Export completed with download errors"
                else:
                    success_msg = "✓ Export and download completed successfully!"
        
        # Output result
        output_result(
            result,
            json_output,
            success_message=success_msg,
            cli_formatter=format_export_result_cli
        )
        
        return result
        
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database_id} {asset_id}' to check if the asset exists."
        )
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))
    except InvalidAssetDataError as e:
        output_error(
            e,
            json_output,
            error_type="Invalid Export Parameters",
            helpful_message="Check your export parameters and try again. Use --help for parameter details."
        )
        raise click.ClickException(str(e))
    except APIError as e:
        output_error(
            e,
            json_output,
            error_type="API Error",
            helpful_message="The export operation failed. Please try again or contact support if the issue persists."
        )
        raise click.ClickException(str(e))
