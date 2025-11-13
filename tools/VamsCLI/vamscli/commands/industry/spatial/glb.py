"""GLB spatial data processing commands for VamsCLI."""

import click
import json
import os
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Import decorators from utils.decorators (Rule 9)
from ....utils.decorators import requires_setup_and_auth, get_profile_manager_from_context

# Import JSON output utilities (Rule 16)
from ....utils.json_output import output_status, output_result, output_error, output_info, output_warning

# Import API client
from ....utils.api_client import APIClient

# Import exceptions (only business logic exceptions)
from ....utils.exceptions import (
    AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError,
    FileDownloadError, APIError
)

# Import GLB combining utilities
from ....utils.glb_combiner import (
    validate_export_has_glbs,
    build_transform_tree_from_export,
    merge_glb_meshes_into_tree,
    write_combined_glb,
    format_file_size,
    GLBCombineError,
    # Keep old imports for backward compatibility
    combine_glb_files,
    combine_multiple_glbs,
    build_transform_matrix_from_metadata,
    add_root_transform_node,
    create_empty_glb
)

# Import existing commands to invoke programmatically
from ...assetsExport import export_command
from ...assets import create as create_command
from ...file import upload as upload_command


@click.group()
def spatial():
    """Spatial data processing commands."""
    pass


def process_and_combine_glbs(export_result: Dict[str, Any], temp_dir: str,
                             root_asset_name: str, json_output: bool,
                             failed_operations: List[Dict]) -> Tuple[str, Dict[str, Any]]:
    """
    Process asset hierarchy and combine GLBs using tree-first approach.
    
    Args:
        export_result: Export command result with assets and relationships
        temp_dir: Temporary directory for processing
        root_asset_name: Name of root asset
        json_output: Whether JSON output mode is enabled
        failed_operations: List to collect failed operations
        
    Returns:
        Tuple of (combined_glb_path, stats_dict)
    """
    # Step 1: Validate at least one GLB exists
    output_status("Validating GLB files...", json_output)
    has_glbs, glb_count = validate_export_has_glbs(export_result)
    if not has_glbs:
        raise InvalidAssetDataError(
            "No GLB files found in asset hierarchy. "
            "At least one GLB file is required to use this command."
        )
    
    output_status(f"Found {glb_count} GLB file(s) in hierarchy", json_output)
    
    # Step 2: Build complete transform tree
    output_status("Building transform tree from asset hierarchy...", json_output)
    try:
        tree_data = build_transform_tree_from_export(export_result)
    except Exception as e:
        failed_operations.append({
            'operation': 'build_transform_tree',
            'error': str(e)
        })
        raise GLBCombineError(f"Failed to build transform tree: {e}")
    
    # Step 3: Merge all GLB meshes into the tree
    output_status("Merging GLB meshes into transform tree...", json_output)
    try:
        final_gltf, combined_binary = merge_glb_meshes_into_tree(tree_data, temp_dir)
    except Exception as e:
        failed_operations.append({
            'operation': 'merge_glb_meshes',
            'error': str(e)
        })
        raise GLBCombineError(f"Failed to merge GLB meshes: {e}")
    
    # Step 4: Write combined GLB
    output_status("Writing combined GLB file...", json_output)
    combined_glb_path = os.path.join(temp_dir, f"{root_asset_name}__COMBINED.glb")
    try:
        write_combined_glb(combined_glb_path, final_gltf, combined_binary)
    except Exception as e:
        failed_operations.append({
            'operation': 'write_combined_glb',
            'error': str(e)
        })
        raise GLBCombineError(f"Failed to write combined GLB: {e}")
    
    output_status(f"Combined GLB created: {combined_glb_path}", json_output)
    
    # Step 5: Return stats (same format as before)
    stats = {
        'total_assets_processed': len(export_result.get('assets', [])),
        'total_glbs_combined': glb_count
    }
    
    return combined_glb_path, stats


def format_combine_result(data: Dict[str, Any]) -> str:
    """Format combine result for CLI display."""
    lines = []
    
    lines.append(f"Root Asset: {data.get('root_asset_name')}")
    lines.append(f"Combined GLB: {data.get('combined_glb_path')}")
    lines.append(f"Combined GLB Size: {data.get('combined_glb_size_formatted')}")
    lines.append(f"Export JSON: {data.get('export_json_path')}")
    lines.append(f"Assets Processed: {data.get('total_assets_processed', 0)}")
    lines.append(f"GLBs Combined: {data.get('total_glbs_combined', 0)}")
    
    if data.get('temporary_directory') and data['temporary_directory'] != 'deleted':
        lines.append(f"Temporary Directory: {data['temporary_directory']}")
    
    if data.get('failed_operations'):
        lines.append(f"\n⚠ Failed Operations ({len(data['failed_operations'])}):")
        for i, failure in enumerate(data['failed_operations'][:5], 1):
            lines.append(f"  {i}. {failure.get('operation')}: {failure.get('error')}")
        
        if len(data['failed_operations']) > 5:
            lines.append(f"  ... and {len(data['failed_operations']) - 5} more")
    
    if data.get('new_asset'):
        lines.append(f"\n✓ New Asset Created:")
        lines.append(f"  Asset ID: {data['new_asset'].get('asset_id')}")
        lines.append(f"  Database: {data['new_asset'].get('database_id')}")
        lines.append(f"  Name: {data['new_asset'].get('name')}")
        lines.append(f"  Files Uploaded: {', '.join(data['new_asset'].get('files_uploaded', []))}")
    
    return '\n'.join(lines)


@spatial.command(name='glbassetcombine')
@click.option('-d', '--database-id', required=True, 
              help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, 
              help='[REQUIRED] Root asset ID')
@click.option('--local-path', 
              help='[OPTIONAL] Local path for temp files (default: system temp)')
@click.option('--include-only-primary-type-files', is_flag=True, default=False,
              help='[OPTIONAL, default: False] Include only primary type files')
@click.option('--no-file-metadata', is_flag=True, default=False,
              help='[OPTIONAL, default: False] Exclude file metadata')
@click.option('--no-asset-metadata', is_flag=True, default=False,
              help='[OPTIONAL, default: False] Exclude asset metadata')
@click.option('--fetch-entire-subtrees', is_flag=True, default=True,
              help='[OPTIONAL, default: True] Fetch entire subtrees')
@click.option('--include-parent-relationships', is_flag=True, default=False,
              help='[OPTIONAL, default: False] Include parent relationships')
@click.option('--asset-create-name',
              help='[OPTIONAL] Create new asset with combined GLB')
@click.option('--delete-temporary-files', is_flag=True, default=True,
              help='[OPTIONAL, default: True] Delete temp files after upload (only with --asset-create-name)')
@click.option('--json-output', is_flag=True,
              help='[OPTIONAL] Output as JSON')
@click.pass_context
@requires_setup_and_auth  # Rule 2: Use decorator for setup/auth
def glbassetcombine(ctx, database_id, asset_id, local_path,
                    include_only_primary_type_files, no_file_metadata,
                    no_asset_metadata, fetch_entire_subtrees,
                    include_parent_relationships, asset_create_name,
                    delete_temporary_files, json_output):
    """
    Combine multiple GLB files from an asset hierarchy into a single GLB.
    
    This command:
    1. Exports asset hierarchy and downloads GLB files
    2. Builds complete transform tree with all asset nodes
    3. Combines GLBs using transform data from relationships
    4. Optionally creates a new asset and uploads the combined GLB
    
    Transform Priority:
    - Uses 'Matrix' metadata if provided (supports multiple formats)
      * 2D arrays (row-major or column-major): [[1,0,0,0], [0,1,0,0], ...]
      * 1D arrays: [1, 0, 0, 0, 0, 1, 0, 0, ...]
      * Space-separated strings: "1 0 0 0 0 1 0 0 ..."
    - Otherwise builds from Transform/Translation, Rotation, Scale components
    - Defaults to identity matrix if no transform data
    
    Asset Instancing:
    - Same asset can appear multiple times with different alias IDs
    - Each instance creates a separate transform node
    - Node names include alias suffix: AssetName__AliasID
    - Each instance can have different transform matrices
    - Useful for assemblies with repeated components (bolts, screws, etc.)
    
    Examples:
        # Basic combine
        vamscli industry spatial glbassetcombine -d my-db -a root-asset
        
        # Combine assembly with repeated components
        vamscli industry spatial glbassetcombine -d assembly-db -a engine-root
        
        # Combine and create new asset
        vamscli industry spatial glbassetcombine -d my-db -a root-asset --asset-create-name "Combined Model"
        
        # With custom temp directory
        vamscli industry spatial glbassetcombine -d my-db -a root-asset --local-path ./temp
        
        # JSON output
        vamscli industry spatial glbassetcombine -d my-db -a root-asset --json-output
    """
    # Setup/auth already validated by decorator (Rule 14)
    profile_manager = get_profile_manager_from_context(ctx)  # Rule 6
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    temp_dir = None
    failed_operations = []
    
    try:
        # Step 1: Setup temp directory (Rule 16: status only in CLI mode)
        output_status("Setting up temporary directory...", json_output)
        
        # Create unique subdirectory with timestamp to avoid conflicts
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_folder = f"glbassetcombine_{timestamp}"
        
        if not local_path:
            base_temp_dir = tempfile.mkdtemp(prefix='vams_glb_combine_')
            temp_dir = os.path.join(base_temp_dir, unique_folder)
        else:
            temp_dir = os.path.join(local_path, unique_folder)
        
        os.makedirs(temp_dir, exist_ok=True)
        
        # Step 2: Call export command with json_output=True (internal)
        output_status(f"Exporting assets from database '{database_id}'...", json_output)
        
        # Suppress stdout during export to prevent multiple JSON outputs
        import sys
        from io import StringIO
        
        if json_output:
            old_stdout = sys.stdout
            sys.stdout = StringIO()
        
        export_result = ctx.invoke(
            export_command,
            database_id=database_id,
            asset_id=asset_id,
            download_files=True,
            local_path=temp_dir,
            file_extensions=['.glb'],
            include_only_primary_type_files=include_only_primary_type_files,
            no_file_metadata=no_file_metadata,
            no_asset_metadata=no_asset_metadata,
            fetch_entire_subtrees=fetch_entire_subtrees,
            include_parent_relationships=include_parent_relationships,
            json_output=True  # Internal call uses JSON
        )
        
        if json_output:
            sys.stdout = old_stdout
        
        # Step 3: Save export JSON
        root_asset_name = 'combined'
        for asset in export_result.get('assets', []):
            if asset.get('is_root_lookup_asset'):
                root_asset_name = asset.get('assetname', 'combined')
                break
        
        # Import sanitize_node_name to clean the asset name for file naming
        from ....utils.glb_combiner import sanitize_node_name
        
        # Use sanitized asset name for file/folder names
        sanitized_root_name = sanitize_node_name(root_asset_name)
        
        json_path = os.path.join(temp_dir, f"{sanitized_root_name}_export.json")
        with open(json_path, 'w') as f:
            json.dump(export_result, f, indent=2)
        
        output_status(f"Export JSON saved to: {json_path}", json_output)
        
        # Step 4: Build asset tree and combine GLBs
        output_status("Processing asset hierarchy and combining GLBs...", json_output)
        
        combined_glb_path, combine_stats = process_and_combine_glbs(
            export_result, temp_dir, sanitized_root_name, json_output, failed_operations
        )
        
        output_status(f"Combined GLB created: {combined_glb_path}", json_output)
        
        # Step 5: Optionally create asset and upload
        new_asset_info = None
        if asset_create_name:
            output_status(f"Creating new asset '{asset_create_name}'...", json_output)
            
            try:
                # Suppress stdout during internal command calls to prevent multiple JSON outputs
                import sys
                from io import StringIO
                
                # Create asset (capture output if json_output mode)
                if json_output:
                    old_stdout = sys.stdout
                    sys.stdout = StringIO()
                
                create_result = ctx.invoke(
                    create_command,
                    database_id=database_id,
                    name=asset_create_name,
                    description=f"{root_asset_name} COMBINED GLB ASSET",
                    distributable=True,
                    json_output=True  # Internal call uses JSON
                )
                
                if json_output:
                    sys.stdout = old_stdout
                
                new_asset_id = create_result['assetId']
                
                # Upload combined GLB (capture output if json_output mode)
                output_status("Uploading combined GLB...", json_output)
                
                if json_output:
                    sys.stdout = StringIO()
                
                ctx.invoke(
                    upload_command,
                    files_or_directory=(combined_glb_path,),
                    database_id=database_id,
                    asset_id=new_asset_id,
                    json_output=True,  # Internal call uses JSON
                    hide_progress=True  # Hide progress to avoid serialization issues
                )
                
                if json_output:
                    sys.stdout = old_stdout
                
                # Upload export JSON (capture output if json_output mode)
                output_status("Uploading export JSON...", json_output)
                
                if json_output:
                    sys.stdout = StringIO()
                
                ctx.invoke(
                    upload_command,
                    files_or_directory=(json_path,),
                    database_id=database_id,
                    asset_id=new_asset_id,
                    json_output=True,  # Internal call uses JSON
                    hide_progress=True  # Hide progress to avoid serialization issues
                )
                
                if json_output:
                    sys.stdout = old_stdout
                
                new_asset_info = {
                    "asset_id": new_asset_id,
                    "database_id": database_id,
                    "name": asset_create_name,
                    "files_uploaded": [
                        os.path.basename(combined_glb_path),
                        os.path.basename(json_path)
                    ]
                }
                
                # Delete temp files if requested
                if delete_temporary_files and temp_dir and not local_path:
                    output_status("Cleaning up temporary files...", json_output)
                    shutil.rmtree(temp_dir)
                    temp_dir = None
                
            except Exception as e:
                failed_operations.append({
                    'operation': 'asset_creation_upload',
                    'error': str(e)
                })
                output_warning(f"Failed to create/upload to new asset: {e}", json_output)
        
        # Step 6: Build and return results
        result = {
            "status": "partial_success" if failed_operations else "success",
            "combined_glb_path": combined_glb_path,
            "export_json_path": json_path,
            "combined_glb_size": os.path.getsize(combined_glb_path),
            "combined_glb_size_formatted": format_file_size(os.path.getsize(combined_glb_path)),
            "root_asset_name": root_asset_name,
            "temporary_directory": temp_dir if temp_dir else "deleted",
            **combine_stats
        }
        
        if failed_operations:
            result['failed_operations'] = failed_operations
        
        if new_asset_info:
            result['new_asset'] = new_asset_info
        
        # Output result (Rule 16: JSON or CLI formatted)
        success_msg = "✓ GLB combination completed successfully!"
        if failed_operations:
            success_msg = "⚠ GLB combination completed with some failures"
        
        output_result(
            result,
            json_output,
            success_message=success_msg,
            cli_formatter=format_combine_result
        )
        
        return result
        
    except (AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError,
            FileDownloadError, GLBCombineError) as e:
        # Only handle business logic exceptions (Rule 14)
        output_error(
            e,
            json_output,
            error_type="GLB Combine Error",
            helpful_message="Check that the database and asset exist and contain GLB files."
        )
        raise click.ClickException(str(e))
    
    finally:
        # Cleanup temp directory if not deleted and not user-provided
        if temp_dir and not local_path and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass  # Best effort cleanup
