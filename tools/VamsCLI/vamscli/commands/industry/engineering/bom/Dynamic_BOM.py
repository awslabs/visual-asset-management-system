# This comment gives instructions regarding what code needs to be generated. 
# Append the generated code below the comment, do not delete this comment. 
#
# This file traverses the parent-child relationship
# use - example.json as the input example
#
# Traverse the parent-child relationship through recursion
# When you traverse to a node with "storage": "VAMS" 
# 1. retrieve the asset id of the VAMS asset with this name
# 2. Do not use the VAMS asset's glb file directly - instead use vamscli industry spatial glbassetcombine -d database_name -a asset_id to retrieve the combined glb file
# NOTE: do not write your own glb combine function - reuse functions from glb_combiner.py as much as possible
# 
# The parent's geometry is the combination of all the children's geometry using corresponding transform stored in the parent child relationship
# 
# If there is no transform specified use identity matrix
#
# Keep creating geometry for each parent node till reaching the root
# return the final geometry file of the root as the answer
# 
# Take an optional argument that is the output asset name. 
# Create this asset in the database and upload all the .glb file generated as files to this asset. 
# See # Step 5: Optionally create asset and upload section in glb.py for example
# 
# End of comment

"Dynamic BOM and GLB Assembly commands for VamsCLI."""

import asyncio
import click
import json
import os
import tempfile
import shutil
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime

# Import decorators from utils.decorators
from .....utils.decorators import requires_setup_and_auth, get_profile_manager_from_context

# Import JSON output utilities
from .....utils.json_output import output_status, output_result, output_error, output_info, output_warning

# Import API client
from .....utils.api_client import APIClient

# Import exceptions
from .....utils.exceptions import (
    AssetNotFoundError, DatabaseNotFoundError, InvalidAssetDataError,
    FileDownloadError, APIError
)

# Import GLB combining utilities
from .....utils.glb_combiner import (
    validate_export_has_glbs,
    build_transform_tree_from_export,
    merge_glb_meshes_into_tree,
    write_combined_glb,
    format_file_size,
    GLBCombineError,
    sanitize_node_name,
    build_transform_matrix_from_metadata
)

# Import existing commands to invoke programmatically
from ....assets import create as create_command
from ....file import upload as upload_command


@click.group()
def bom():
    """Bill of materital engineering commands."""
    pass

@bom.group()
def bomassemble():
    """Bill of Material assemble format commands."""
    pass

def parse_bom_json(json_path: str) -> Dict[str, Any]:
    """
    Parse BOM JSON file.
    
    Args:
        json_path: Path to BOM JSON file
        
    Returns:
        Parsed JSON data
        
    Raises:
        InvalidAssetDataError: If JSON is invalid or missing required fields
    """
    try:
        with open(json_path, 'r') as f:
            data = json.load(f)
        
        # Validate required fields
        if 'scene' not in data or 'nodes' not in data['scene']:
            raise InvalidAssetDataError(
                "Invalid BOM JSON: missing 'scene.nodes' structure"
            )
        
        if 'sources' not in data:
            raise InvalidAssetDataError(
                "Invalid BOM JSON: missing 'sources' field"
            )
        
        return data
        
    except json.JSONDecodeError as e:
        raise InvalidAssetDataError(f"Invalid JSON file: {e}")
    except FileNotFoundError:
        raise InvalidAssetDataError(f"BOM JSON file not found: {json_path}")


def build_node_tree(nodes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Build a tree structure from flat node list.
    
    Args:
        nodes: List of node dictionaries with 'node', 'parent_node', 'source', 'matrix'
        
    Returns:
        Dictionary mapping node IDs to node data with children list
    """
    node_tree = {}
    
    # First pass: create all nodes
    for node in nodes:
        node_id = node['node']
        node_tree[node_id] = {
            'id': node_id,
            'source': node['source'],
            'matrix': node.get('matrix', [
                1, 0, 0, 0,
                0, 1, 0, 0,
                0, 0, 1, 0,
                0, 0, 0, 1
            ]),
            'bomdata': node.get('bomdata', {}),
            'parent_id': node.get('parent_node'),
            'children': []
        }
    
    # Second pass: build parent-child relationships
    for node_id, node_data in node_tree.items():
        parent_id = node_data['parent_id']
        if parent_id and parent_id in node_tree:
            node_tree[parent_id]['children'].append(node_id)
    
    return node_tree


def find_root_nodes(node_tree: Dict[str, Dict[str, Any]]) -> List[str]:
    """
    Find root nodes (nodes without parents).
    
    Args:
        node_tree: Node tree dictionary
        
    Returns:
        List of root node IDs
    """
    root_nodes = []
    for node_id, node_data in node_tree.items():
        if not node_data['parent_id']:
            root_nodes.append(node_id)
    
    return root_nodes


def get_asset_id_by_name(ctx: click.Context, database_id: str, 
                         asset_name: str, json_output: bool) -> Optional[str]:
    """
    Retrieve asset ID by asset name from VAMS using search.
    
    Args:
        ctx: Click context
        database_id: Database ID to search in
        asset_name: Asset name to search for
        json_output: Whether JSON output mode is enabled
        
    Returns:
        Asset ID if found, None otherwise
    """
    try:
        output_status(f"Searching for asset: {asset_name}", json_output)
        
        # Get API client from context
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client_local = APIClient(config['api_gateway_url'], profile_manager)
        
        # Build search request directly (using API client instead of ctx.invoke for reliability)
        search_request = {
            "from": 0,
            "size": 1,  # Only need first match
            "includeArchived": False,
            "assetName": asset_name,
            "databaseId": database_id,
            "entityTypes": ["asset"]
        }
        
        # Execute search directly via API client
        search_result = api_client_local.search_simple(search_request)
        
        # Extract asset ID from search results
        hits = search_result.get("hits", {}).get("hits", [])
        if hits:
            asset_id = hits[0].get("_source", {}).get("str_assetid")
            if asset_id:
                output_status(f"Found asset: {asset_name} -> {asset_id}", json_output)
                return asset_id
        
        output_warning(f"Asset not found: {asset_name}", json_output)
        return None
        
    except Exception as e:
        output_warning(f"Error searching for asset {asset_name}: {e}", json_output)
        return None


def download_glb_for_node(ctx: click.Context, api_client: APIClient, database_id: str, 
                          node_data: Dict[str, Any], temp_dir: str,
                          json_output: bool) -> Optional[str]:
    """
    Get combined GLB file for a stored node using glbassetcombine.
    
    This uses the glbassetcombine command which handles asset hierarchies
    and combines child GLBs with proper transforms.
    
    Args:
        ctx: Click context
        api_client: API client instance
        database_id: Database ID
        node_data: Node data dictionary
        temp_dir: Temporary directory for downloads
        json_output: Whether JSON output mode is enabled
        
    Returns:
        Path to combined GLB file, or None if failed
    """
    source_name = node_data['source']
    
    # Get asset ID
    asset_id = get_asset_id_by_name(ctx, database_id, source_name, json_output)
    if not asset_id:
        return None
    
    try:
        from ...spatial.glb import glbassetcombine
        
        output_status(f"Getting combined GLB for asset: {source_name}", json_output)
        
        # Use fixed output directory for debugging
        base_output_dir = Path.cwd() / "temp" 
        base_output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create subdirectory for this asset's combined GLB
        asset_temp_dir = base_output_dir / sanitize_node_name(source_name)
        asset_temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Use glbassetcombine to get the combined GLB for this asset
        # This handles any child assets and combines them with proper transforms
        try:
            combine_result = ctx.invoke(
                glbassetcombine,
                database_id=database_id,
                asset_id=asset_id,
                local_path=str(asset_temp_dir),
                include_only_primary_type_files=False,
                no_file_metadata=False,
                no_asset_metadata=False,
                fetch_entire_subtrees=True,
                include_parent_relationships=False,
                asset_create_name=None,  # Don't create new asset
                delete_temporary_files=False,  # Keep files for BOM assembly
                json_output=True  # Suppress CLI output
            )
        except click.ClickException as e:
            output_warning(f"glbassetcombine Click exception for {source_name}: {e.message}", json_output)
            return None
        except Exception as e:
            output_warning(f"glbassetcombine failed for {source_name}: {e}", json_output)
            return None
        
        # Get the combined GLB path from result
        combined_glb_path = combine_result.get('combined_glb_path')
        
        if combined_glb_path and os.path.exists(combined_glb_path):
            output_status(f"✓ Got combined GLB: {combined_glb_path}", json_output)
            output_status(f"  File size: {format_file_size(os.path.getsize(combined_glb_path))}", json_output)
            return combined_glb_path
        elif combined_glb_path:
            output_warning(f"Combined GLB path returned but file doesn't exist: {combined_glb_path}", json_output)
            return None
        else:
            output_warning(f"No combined_glb_path in result for asset: {source_name}", json_output)
            return None
        
    except Exception as e:
        output_warning(f"Error getting combined GLB for {source_name}: {e}", json_output)
        return None


def download_all_glbs_for_tree(ctx: click.Context,
                               node_tree: Dict[str, Dict[str, Any]],
                               api_client: APIClient,
                               database_id: str,
                               temp_dir: str,
                               sources: List[Dict[str, Any]],
                               json_output: bool) -> Dict[str, str]:
    """
    Download all GLB files for stored nodes in the tree (non-recursive).
    
    Args:
        ctx: Click context
        node_tree: Complete node tree
        api_client: API client instance
        database_id: Database ID
        temp_dir: Temporary directory
        sources: List of source definitions with storage status
        json_output: Whether JSON output mode is enabled
        
    Returns:
        Dictionary mapping source names to GLB file paths
    """
    glb_cache = {}
    
    # Find all stored sources that need GLB files
    stored_sources = set()
    for node_id, node_data in node_tree.items():
        source_name = node_data['source']
        source_info = next((s for s in sources if s['source'] == source_name), None)
        if source_info and source_info.get('storage') == 'VAMS':
            stored_sources.add(source_name)
    
    # Download GLB files for all stored sources
    for source_name in stored_sources:
        output_status(f"Downloading GLB for source: {source_name}", json_output)
        
        # Find a node with this source (any will do for downloading)
        node_data = None
        for node_id, node in node_tree.items():
            if node['source'] == source_name:
                node_data = node
                break
        
        if node_data:
            glb_path = download_glb_for_node(
                ctx, api_client, database_id, node_data, temp_dir, json_output
            )
            if glb_path:
                glb_cache[source_name] = glb_path
    
    return glb_cache


def build_complete_export_from_bom(node_tree: Dict[str, Dict[str, Any]],
                                   root_node_id: str,
                                   glb_cache: Dict[str, str],
                                   sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Build a complete export structure from BOM tree for GLB combining.
    
    This creates the export structure that the GLB combining utilities expect,
    representing the entire BOM hierarchy in one structure.
    
    Args:
        node_tree: Complete node tree
        root_node_id: Root node ID
        glb_cache: Cache of source name to GLB file path
        sources: List of source definitions with storage status
        
    Returns:
        Export structure compatible with build_transform_tree_from_export
    """
    mock_assets = []
    mock_relationships = []
    
    # Create assets for all nodes
    for node_id, node_data in node_tree.items():
        source_name = node_data['source']
        source_info = next((s for s in sources if s['source'] == source_name), None)
        is_stored = source_info and source_info.get('storage') == 'VAMS'
        is_root = (node_id == root_node_id)
        
        # Create asset
        asset = {
            'assetid': node_id,
            'assetname': sanitize_node_name(source_name),
            'is_root_lookup_asset': is_root,
            'files': []
        }
        
        # Add GLB file if this source is stored and we have it cached
        if is_stored and source_name in glb_cache:
            glb_path = glb_cache[source_name]
            glb_filename = os.path.basename(glb_path)
            asset['files'].append({
                'fileName': glb_filename,
                'key': glb_filename
            })
        
        mock_assets.append(asset)
    
    # Create relationships for all parent-child connections
    for node_id, node_data in node_tree.items():
        parent_id = node_data.get('parent_id')
        if parent_id and parent_id in node_tree:
            # Create relationship from parent to this child
            mock_relationships.append({
                'parentAssetId': parent_id,
                'childAssetId': node_id,
                'assetLinkAliasId': f"alias_{parent_id}_{node_id}",
                'metadata': {
                    'Matrix': {
                        'value': node_data['matrix']
                    }
                }
            })
    
    return {
        'assets': mock_assets,
        'relationships': mock_relationships
    }


def combine_bom_hierarchy_optimized(ctx: click.Context,
                                    node_tree: Dict[str, Dict[str, Any]], 
                                    root_node_id: str,
                                    api_client: APIClient,
                                    database_id: str,
                                    temp_dir: str,
                                    sources: List[Dict[str, Any]],
                                    json_output: bool) -> Optional[str]:
    """
    Optimized BOM hierarchy combining using tree-first approach.
    
    This function replaces the recursive combine_node_geometries approach with
    a more efficient tree-first strategy that:
    1. Downloads all GLB files in one pass
    2. Builds complete export structure representing entire hierarchy
    3. Uses GLB combining utilities once to process everything
    
    Args:
        ctx: Click context
        node_tree: Complete node tree
        root_node_id: Root node ID to process
        api_client: API client instance
        database_id: Database ID
        temp_dir: Temporary directory
        sources: List of source definitions with storage status
        json_output: Whether JSON output mode is enabled
        
    Returns:
        Path to combined GLB file for the root node, or None if failed
    """
    try:
        root_node_data = node_tree[root_node_id]
        root_source_name = root_node_data['source']
        
        output_status(f"Processing BOM hierarchy for root node {root_node_id}: {root_source_name}", json_output)
        
        # Step 1: Download all GLB files for stored sources
        output_status("Downloading all required GLB files...", json_output)
        glb_cache = download_all_glbs_for_tree(
            ctx, node_tree, api_client, database_id, temp_dir, sources, json_output
        )
        
        if not glb_cache:
            output_warning(f"No GLB files found for BOM hierarchy", json_output)
            return None
        
        output_status(f"Downloaded {len(glb_cache)} GLB files", json_output)
        
        # Step 2: Build complete export structure
        output_status("Building complete export structure from BOM...", json_output)
        export_structure = build_complete_export_from_bom(
            node_tree, root_node_id, glb_cache, sources
        )
        
        # Step 3: Copy GLB files to temp directory with expected names
        for source_name, glb_path in glb_cache.items():
            glb_filename = os.path.basename(glb_path)
            dest_path = os.path.join(temp_dir, glb_filename)
            if not os.path.exists(dest_path):
                import shutil
                shutil.copy2(glb_path, dest_path)
        
        # Step 4: Use GLB combining utilities to process entire hierarchy
        output_status("Building transform tree from complete BOM structure...", json_output)
        tree_data = build_transform_tree_from_export(export_structure)
        
        output_status("Merging all GLB meshes into transform tree...", json_output)
        final_gltf, combined_binary = merge_glb_meshes_into_tree(tree_data, temp_dir)
        
        # Step 5: Write final combined GLB
        output_path = os.path.join(
            temp_dir, 
            f"{sanitize_node_name(root_source_name)}_root_{root_node_id}_combined.glb"
        )
        
        write_combined_glb(output_path, final_gltf, combined_binary)
        
        output_status(f"✓ Created optimized combined GLB: {output_path}", json_output)
        output_status(f"  File size: {format_file_size(os.path.getsize(output_path))}", json_output)
        
        return output_path
        
    except Exception as e:
        output_warning(f"Error in optimized BOM combining: {e}", json_output)
        return None



@bom.command(name='bomassemble')
@click.option('-j', '--json-file', required=True,
              help='[REQUIRED] Path to BOM JSON file (e.g., example.json)')
@click.option('-d', '--database-id', required=True,
              help='[REQUIRED] Database ID containing the assets')
@click.option('--local-path',
              help='[OPTIONAL] Local path for temp files (default: system temp)')
@click.option('--keep-temp-files', is_flag=True, default=False,
              help='[OPTIONAL] Keep temporary files after processing')
@click.option('--asset-create-name',
              help='[OPTIONAL] Create new asset with this name and upload all generated GLB files')
@click.option('--delete-temporary-files', is_flag=True, default=True,
              help='[OPTIONAL, default: True] Delete temp files after upload (only with --asset-create-name)')
@click.option('--json-output', is_flag=True,
              help='[OPTIONAL] Output JSON')
@click.pass_context
@requires_setup_and_auth
def bomassemble(ctx, json_file, database_id, local_path, keep_temp_files, 
                asset_create_name, delete_temporary_files, json_output):
    """
    Assemble GLB geometry from BOM JSON hierarchy.
    
    This command:
    1. Parses BOM JSON file with parent-child relationships
    2. Recursively traverses the node tree from leaves to root
    3. Downloads GLB files for nodes with storage="stored"
    4. Combines child geometries using transform matrices
    5. Returns final assembled GLB for the root node
    6. Optionally creates a new asset and uploads all generated GLB files
    
    Transform Handling:
    - Uses 'matrix' field from node (4x4 transform, 16 floats)
    - Defaults to identity matrix if not specified
    - Applies transforms when combining child geometries
    
    Asset Creation:
    - Use --asset-create-name to create a new asset
    - Uploads all assembled root GLBs
    - Uploads all downloaded/cached component GLBs
    - Uploads the original BOM JSON file
    - Use --delete-temporary-files to clean up after upload (default: True)
    
    Examples:
        # Basic assembly
        vamscli industry bom bomassemble -j example.json -d my-database
        
        # Assembly with asset creation
        vamscli industry bom bomassemble -j example.json -d my-database --asset-create-name "Engine Assembly"
        
        # With custom temp directory
        vamscli industry bom bomassemble -j example.json -d my-database --local-path ./temp
        
        # Keep temp files for debugging
        vamscli industry bom bomassemble -j example.json -d my-database --keep-temp-files
        
        # JSON output
        vamscli industry bom bomassemble -j example.json -d my-database --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    temp_dir = None
    
    try:
        # Step 1: Parse BOM JSON
        output_status(f"Parsing BOM JSON: {json_file}", json_output)
        bom_data = parse_bom_json(json_file)
        
        nodes = bom_data['scene']['nodes']
        sources = bom_data['sources']
        
        output_status(f"Found {len(nodes)} nodes and {len(sources)} sources", json_output)
        
        # Step 2: Build node tree
        output_status("Building node tree...", json_output)
        node_tree = build_node_tree(nodes)
        
        # Step 3: Find root nodes
        root_nodes = find_root_nodes(node_tree)
        if not root_nodes:
            raise InvalidAssetDataError("No root nodes found in BOM hierarchy")
        
        output_status(f"Found {len(root_nodes)} root node(s): {', '.join(root_nodes)}", json_output)
        
        # Step 4: Setup temp directory
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_folder = f"bomassemble_{timestamp}"
        
        if not local_path:
            base_temp_dir = tempfile.mkdtemp(prefix='vams_bom_assembly_')
            temp_dir = os.path.join(base_temp_dir, unique_folder)
        else:
            temp_dir = os.path.join(local_path, unique_folder)
        
        os.makedirs(temp_dir, exist_ok=True)
        output_status(f"Using temp directory: {temp_dir}", json_output)
        
        # Step 5: Process each root node using optimized tree-first approach
        results = []
        
        for root_id in root_nodes:
            output_status(f"Processing root node: {root_id}", json_output)
            
            try:
                combined_glb = combine_bom_hierarchy_optimized(
                    ctx, node_tree, root_id, api_client,
                    database_id, temp_dir, sources, json_output
                )
                
                if combined_glb:
                    if os.path.exists(combined_glb):
                        results.append({
                            'root_node_id': root_id,
                            'root_source': node_tree[root_id]['source'],
                            'combined_glb_path': combined_glb,
                            'combined_glb_size': os.path.getsize(combined_glb),
                            'combined_glb_size_formatted': format_file_size(os.path.getsize(combined_glb))
                        })
                        
                        output_status(
                            f"✓ Root node {root_id} assembled: {combined_glb}", 
                            json_output
                        )
                    else:
                        output_warning(f"Root node {root_id} returned path but file doesn't exist: {combined_glb}", json_output)
                else:
                    output_warning(f"Failed to assemble root node {root_id} - returned None", json_output)
            except Exception as e:
                output_warning(f"Error assembling root node {root_id}: {e}", json_output)
        
        # Step 6: Build result
        if not results:
            raise GLBCombineError("Failed to assemble any root nodes")
        
        result = {
            'status': 'success',
            'bom_json_file': json_file,
            'database_id': database_id,
            'total_nodes': len(nodes),
            'total_sources': len(sources),
            'root_nodes_processed': len(results),
            'assemblies': results,
            'temporary_directory': temp_dir if keep_temp_files else 'deleted',
            'optimization': 'tree-first approach (optimized)'
        }
        
        # Step 7: Optionally create asset and upload all GLB files
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
                
                # Use json_input to ensure proper field names (assetName not name)
                asset_create_data = {
                    'databaseId': database_id,
                    'assetName': asset_create_name,
                    'description': f"BOM Assembly from {os.path.basename(json_file)}",
                    'isDistributable': True,
                    'tags': []
                }
                
                create_result = ctx.invoke(
                    create_command,
                    database_id=database_id,
                    name=None,  # Not using individual params
                    description=None,
                    distributable=None,
                    tags=[],
                    bucket_key=None,
                    json_input=json.dumps(asset_create_data),  # Use JSON input for proper field names
                    json_output=True  # Internal call uses JSON
                )
                
                if json_output:
                    sys.stdout = old_stdout
                
                new_asset_id = create_result['assetId']
                
                # Collect all GLB files to upload
                glb_files_to_upload = []
                
                # Add all assembled root GLBs
                for assembly in results:
                    glb_path = assembly['combined_glb_path']
                    if os.path.exists(glb_path):
                        glb_files_to_upload.append(glb_path)
                
                # Add all GLB files from temp directory (downloaded and intermediate files)
                if temp_dir and os.path.exists(temp_dir):
                    for file in os.listdir(temp_dir):
                        if file.endswith('.glb'):
                            glb_path = os.path.join(temp_dir, file)
                            if os.path.exists(glb_path) and glb_path not in glb_files_to_upload:
                                glb_files_to_upload.append(glb_path)
                
                output_status(f"Uploading {len(glb_files_to_upload)} GLB file(s)...", json_output)
                
                # Upload all GLB files
                uploaded_files = []
                for glb_path in glb_files_to_upload:
                    output_status(f"Uploading {os.path.basename(glb_path)}...", json_output)
                    
                    if json_output:
                        sys.stdout = StringIO()
                    
                    ctx.invoke(
                        upload_command,
                        files_or_directory=(glb_path,),
                        database_id=database_id,
                        asset_id=new_asset_id,
                        directory=None,
                        asset_preview=False,
                        asset_location='/',
                        recursive=False,
                        parallel_uploads=10,
                        retry_attempts=3,
                        force_skip=False,
                        json_input=None,
                        json_output=True,  # Internal call uses JSON
                        hide_progress=True  # Hide progress to avoid serialization issues
                    )
                    
                    if json_output:
                        sys.stdout = old_stdout
                    
                    uploaded_files.append(os.path.basename(glb_path))
                
                # Upload BOM JSON file
                output_status("Uploading BOM JSON file...", json_output)
                
                if json_output:
                    sys.stdout = StringIO()
                
                ctx.invoke(
                    upload_command,
                    files_or_directory=(json_file,),
                    database_id=database_id,
                    asset_id=new_asset_id,
                    directory=None,
                    asset_preview=False,
                    asset_location='/',
                    recursive=False,
                    parallel_uploads=10,
                    retry_attempts=3,
                    force_skip=False,
                    json_input=None,
                    json_output=True,  # Internal call uses JSON
                    hide_progress=True  # Hide progress to avoid serialization issues
                )
                
                if json_output:
                    sys.stdout = old_stdout
                
                uploaded_files.append(os.path.basename(json_file))
                
                new_asset_info = {
                    "asset_id": new_asset_id,
                    "database_id": database_id,
                    "name": asset_create_name,
                    "files_uploaded": uploaded_files,
                    "total_files": len(uploaded_files)
                }
                
                # Delete temp files if requested
                if delete_temporary_files and temp_dir and not local_path:
                    output_status("Cleaning up temporary files...", json_output)
                    shutil.rmtree(temp_dir)
                    temp_dir = None
                
            except Exception as e:
                output_warning(f"Failed to create/upload to new asset: {e}", json_output)
        
        # Add new asset info to result
        if new_asset_info:
            result['new_asset'] = new_asset_info
        
        # Cleanup temp files if not keeping (and not already deleted)
        if not keep_temp_files and temp_dir and not local_path and os.path.exists(temp_dir):
            output_status("Cleaning up temporary files...", json_output)
            shutil.rmtree(temp_dir)
            temp_dir = None
        
        # Output result
        output_result(
            result,
            json_output,
            success_message="✓ BOM assembly completed successfully!",
            cli_formatter=lambda r: format_assembly_result(r)
        )
        
        return result
        
    except (InvalidAssetDataError, GLBCombineError) as e:
        output_error(
            e,
            json_output,
            error_type="BOM Assembly Error",
            helpful_message="Check that the BOM JSON is valid and assets exist in the database."
        )
        raise click.ClickException(str(e))
    
    finally:
        # Cleanup temp directory if not keeping
        if temp_dir and not keep_temp_files and not local_path and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass


def format_assembly_result(data: Dict[str, Any]) -> str:
    """Format assembly result for CLI display."""
    lines = []
    
    lines.append(f"BOM JSON: {data.get('bom_json_file')}")
    lines.append(f"Database: {data.get('database_id')}")
    lines.append(f"Total Nodes: {data.get('total_nodes', 0)}")
    lines.append(f"Total Sources: {data.get('total_sources', 0)}")
    lines.append(f"Root Nodes Processed: {data.get('root_nodes_processed', 0)}")
    
    if data.get('optimization'):
        lines.append(f"Processing: {data.get('optimization')}")
    
    if data.get('assemblies'):
        lines.append("\nAssembled Root Nodes:")
        for assembly in data['assemblies']:
            lines.append(f"  Node {assembly['root_node_id']} ({assembly['root_source']}):")
            lines.append(f"    GLB: {assembly['combined_glb_path']}")
            lines.append(f"    Size: {assembly['combined_glb_size_formatted']}")
    
    if data.get('new_asset'):
        lines.append(f"\n✓ New Asset Created:")
        lines.append(f"  Asset ID: {data['new_asset'].get('asset_id')}")
        lines.append(f"  Database: {data['new_asset'].get('database_id')}")
        lines.append(f"  Name: {data['new_asset'].get('name')}")
        lines.append(f"  Total Files Uploaded: {data['new_asset'].get('total_files', 0)}")
        lines.append(f"  Files: {', '.join(data['new_asset'].get('files_uploaded', []))}")
    
    if data.get('temporary_directory') and data['temporary_directory'] != 'deleted':
        lines.append(f"\nTemporary Directory: {data['temporary_directory']}")
    
    return '\n'.join(lines)


