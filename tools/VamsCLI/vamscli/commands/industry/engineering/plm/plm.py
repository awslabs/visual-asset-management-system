"""PLM (Product Lifecycle Management) commands for VamsCLI."""

import json
from pathlib import Path
from typing import Dict, Any, Optional
from xml.etree.ElementTree import Element  # For type hints only
import defusedxml.ElementTree as ET  # For secure XML parsing
from tqdm.rich import tqdm
import click
import os
import time
import io
import sys
import contextlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from .....utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from .....utils.api_client import APIClient
from .....utils.json_output import output_status, output_result, output_error, output_warning, output_info
from .....utils.exceptions import (
    AssetNotFoundError,
    AssetAlreadyExistsError,
    FileUploadError,
    InvalidFileError,
)
from typing import List, Set, Tuple
from ....assets import create as assets_create
from ....file import upload as file_upload
from ....asset_links import create as asset_link_create
from ....asset_links_metadata import create as asset_link_metadata_create
from ....search import simple as search_simple
from datetime import datetime
import re

@click.group()
def plm():
    """Product Lifecycle Management (PLM) commands."""
    pass


@plm.group()
def plmxml():
    """PLM XML format commands."""
    pass


def parse_transform_to_matrix4x4(transform_text: str) -> List[List[float]]:
    """
    Convert 16-value transform string to 4x4 matrix.
    
    Args:
        transform_text: Space-separated string of 16 float values
        
    Returns:
        4x4 matrix as list of lists
        
    Raises:
        ValueError: If transform doesn't have exactly 16 values
    """
    values = [float(v) for v in transform_text.split()]
    if len(values) != 16:
        raise ValueError(f"Transform must have 16 values, got {len(values)}")
    
    return [
        [values[0], values[1], values[2], values[3]],
        [values[4], values[5], values[6], values[7]],
        [values[8], values[9], values[10], values[11]],
        [values[12], values[13], values[14], values[15]]
    ]


def get_or_generate_alias_id(
    parent_id: str,
    child_id: str,
    sequence_number: Optional[str],
    alias_tracker: Dict[Tuple[str, str], Set[str]]
) -> str:
    """
    Get sequence number or generate unique one for parent-child pair.
    
    Args:
        parent_id: Parent asset ID
        child_id: Child asset ID
        sequence_number: Optional sequence number from PLMXML
        alias_tracker: Dictionary tracking used aliasIds per parent-child pair
        
    Returns:
        aliasId to use for the asset link
    """
    key = (parent_id, child_id)
    
    if sequence_number:
        # Use provided sequence number
        if key not in alias_tracker:
            alias_tracker[key] = set()
        alias_tracker[key].add(sequence_number)
        return sequence_number
    
    # Generate next available number
    if key not in alias_tracker:
        alias_tracker[key] = set()
    
    next_num = 1
    while str(next_num) in alias_tracker[key]:
        next_num += 1
    
    alias_id = str(next_num)
    alias_tracker[key].add(alias_id)
    return alias_id


def sanitize_path(file_path: str) -> str:
    """Sanitize file path using OS supported path separator."""
    return os.path.normpath(file_path.replace("/", os.sep).replace("\\", os.sep))


def sanitize_asset_id(asset_id: str) -> str:
    r"""
    Sanitize asset ID to comply with VAMS requirements.

    VAMS asset IDs must follow the regexp: ^(?!.*[<>:"\/\\|?*])(?!.*[.\s]$)[\w\s.,\'-]{1,254}[^.\s]$
    This means:
    - No characters: < > : " / \ | ? *
    - Cannot end with . or space
    - Can contain word characters, spaces, periods, commas, apostrophes, hyphens
    - Length 1-254 characters
    - Cannot end with . or space
    """
    if not asset_id:
        return asset_id

    # Replace forbidden characters with underscores
    # Forbidden: < > : " / \ | ? *
    sanitized = re.sub(r'[<>:"\/\\|?*]', "_", asset_id)

    # Remove trailing dots and spaces
    sanitized = sanitized.rstrip(". ")

    # Ensure it's not empty after sanitization
    if not sanitized:
        sanitized = "asset_" + str(hash(asset_id))[:8]

    # Truncate to 254 characters if needed
    if len(sanitized) > 254:
        sanitized = sanitized[:254].rstrip(". ")

    return sanitized


def suppress_output(func):
    """
    Thread-safe decorator to suppress stdout and stderr for a function.
    
    Uses contextlib.redirect_stdout/stderr which are thread-safe and properly
    handle nested redirections without race conditions.
    """
    def wrapper(*args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return func(*args, **kwargs)
    return wrapper


# ============================================================================
# PHASE 0: XML PARSING
# ============================================================================

def parse_all_xml_files(xml_files: List[Path], json_output: bool) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Phase 0: Parse all XML files completely before any API calls.
    
    Args:
        xml_files: List of XML file paths to parse
        json_output: Whether JSON output mode is enabled
        
    Returns:
        Tuple of (global_components, global_relationships)
    """
    global_components = {}
    global_relationships = []
    
    for xml_file in tqdm(xml_files, desc="Parsing XML files", disable=json_output):
        ingestor = PLMXMLIngestor()
        ingestor.parse_file(xml_file)
        
        if not ingestor.components:
            if not json_output:
                click.echo(f"No components found in {xml_file}")
            continue
        
        # Store components and relationships globally
        global_components.update(ingestor.components)
        global_relationships.extend(ingestor.relationships)
    
    return global_components, global_relationships


# ============================================================================
# PHASE 1: ASSET CREATION (PARALLEL)
# ============================================================================

def create_single_asset(
    ctx: click.Context,
    database_id: str,
    item_revision: str,
    component: Dict[str, Any],
    progress_lock: Lock
) -> Tuple[str, bool, bool, Optional[str]]:
    """
    Create or find a single asset (thread-safe).
    
    Returns:
        Tuple of (item_revision, success, was_created, error_message)
    """
    try:
        # Use item_revision as the asset name
        original_asset_name = component.get("item_revision")
        if not original_asset_name:
            return (item_revision, False, False, "No item_revision found")

        # Sanitize the asset ID
        asset_name = sanitize_asset_id(original_asset_name)
        component["sanitized_asset_id"] = asset_name

        # Check if asset already exists
        existing_asset_id = get_existing_asset_id(ctx, database_id, asset_name)
        if existing_asset_id:
            component["actual_asset_id"] = existing_asset_id
            return (item_revision, True, False, None)

        # Prepare description
        description = component.get("product_name", original_asset_name)
        if len(description) < 4:
            description = f"Asset {description}"

        # Create the asset - let it output naturally
        create_result = ctx.invoke(
            assets_create,
            database_id=database_id,
            name=asset_name,
            description=description,
            distributable=True,
            bucket_key=None,
            json_input=None,
            json_output=False
        )
        
        actual_asset_id = create_result.get('assetId')
        if not actual_asset_id:
            return (item_revision, False, False, "Failed to get asset ID from create response")
        
        component["actual_asset_id"] = actual_asset_id
        return (item_revision, True, True, None)

    except AssetAlreadyExistsError:
        return (item_revision, True, False, None)
    except click.exceptions.Exit as e:
        # Click Exit exceptions should not stop the entire process
        return (item_revision, False, False, f"Click Exit: {e.exit_code}")
    except Exception as e:
        return (item_revision, False, False, str(e))


def process_assets_parallel(
    ctx: click.Context,
    components: Dict[str, Dict[str, Any]],
    database_id: str,
    max_workers: int,
    json_output: bool
) -> Dict[str, Any]:
    """
    Phase 1: Create/search all assets in parallel.
    
    Returns:
        Dictionary with statistics and failed asset tracking
    """
    stats = {
        'created': 0,
        'existing': 0,
        'failed': 0,
        'failed_items': set()
    }
    
    progress_lock = Lock()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                create_single_asset,
                ctx,
                database_id,
                item_revision,
                component,
                progress_lock
            ): item_revision
            for item_revision, component in components.items()
        }
        
        with tqdm(total=len(futures), desc="Creating assets", disable=json_output) as pbar:
            for future in as_completed(futures):
                item_revision, success, was_created, error = future.result()
                
                if success:
                    if was_created:
                        stats['created'] += 1
                    else:
                        stats['existing'] += 1
                else:
                    stats['failed'] += 1
                    stats['failed_items'].add(item_revision)
                
                pbar.update(1)
    
    return stats


# ============================================================================
# PHASE 2: PARALLEL OPERATIONS (METADATA, FILES, LINKS)
# ============================================================================

def create_single_metadata(
    ctx: click.Context,
    database_id: str,
    asset_id: str,
    component: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """Create metadata for a single asset (thread-safe)."""
    try:
        metadata = {}
        skip_fields = {"id", "children", "parentRef", "sanitized_asset_id", "actual_asset_id"}
        
        for key, value in component.items():
            if key not in skip_fields and value is not None and value != "":
                metadata[key] = str(value)
        
        if not metadata:
            return (True, None)
        
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)
        
        api_client.create_metadata(database_id, asset_id, metadata)
        return (True, None)
    
    except Exception as e:
        return (False, str(e))


def upload_single_geometry(
    ctx: click.Context,
    database_id: str,
    asset_id: str,
    component: Dict[str, Any],
    plmxml_dir: str
) -> Tuple[bool, int, Optional[str]]:
    """Upload geometry files for a single asset (thread-safe)."""
    try:
        geometry_location = component.get("geometry_file_location")
        if not geometry_location:
            return (True, 0, None)
        
        sanitized_path = sanitize_path(geometry_location)
        geometry_parent_dir = os.path.dirname(sanitized_path)
        geometry_filename = os.path.basename(sanitized_path)
        geometry_basename = os.path.splitext(geometry_filename)[0]
        
        full_geometry_dir = os.path.join(plmxml_dir, geometry_parent_dir)
        
        if not os.path.exists(full_geometry_dir):
            return (True, 0, None)
        
        uploaded_count = 0
        for file_name in os.listdir(full_geometry_dir):
            file_path = os.path.join(full_geometry_dir, file_name)
            
            if os.path.isfile(file_path):
                file_basename = os.path.splitext(file_name)[0]
                
                if file_basename == geometry_basename:
                    try:
                        # Suppress file upload output (has its own progress bar)
                        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                            result = ctx.invoke(
                                file_upload,
                                files_or_directory=[file_path],
                                database_id=database_id,
                                asset_id=asset_id,
                                directory=None,
                                json_input=None,
                            )
                        if result.get("overall_success", False):
                            uploaded_count += 1
                    except Exception:
                        pass
        
        return (True, uploaded_count, None)
    
    except Exception as e:
        return (False, 0, str(e))


def create_single_link(
    ctx: click.Context,
    relationship: Dict[str, Any],
    components: Dict[str, Dict[str, Any]],
    database_id: str,
    alias_tracker: Dict[Tuple[str, str], Set[str]],
    tracker_lock: Lock
) -> Tuple[bool, Optional[str], Optional[str], List[Tuple[str, str, str]]]:
    """
    Create a single asset link (thread-safe).
    
    Returns:
        Tuple of (success, asset_link_id, error_message, metadata_list)
        metadata_list is [(key, value, type), ...]
    """
    try:
        parent_item_revision = relationship.get('parent')
        child_item_revision = relationship.get('child')
        
        parent_component = components.get(parent_item_revision)
        child_component = components.get(child_item_revision)
        
        if not parent_component or not child_component:
            return (False, None, "Parent or child component not found", [])
        
        parent_asset_id = parent_component.get('actual_asset_id')
        child_asset_id = child_component.get('actual_asset_id')
        
        if not parent_asset_id or not child_asset_id:
            return (False, None, "Parent or child asset ID not found", [])
        
        # Extract metadata
        sequence_number = relationship.get('sequence_number')
        transform_text = relationship.get('transform')
        
        # Generate aliasId (thread-safe)
        with tracker_lock:
            alias_id = get_or_generate_alias_id(
                parent_asset_id,
                child_asset_id,
                sequence_number,
                alias_tracker
            )
        
        # Create asset link - let it output naturally
        link_result = ctx.invoke(
            asset_link_create,
            from_asset_id=parent_asset_id,
            from_database_id=database_id,
            to_asset_id=child_asset_id,
            to_database_id=database_id,
            relationship_type='parentChild',
            alias_id=alias_id,
            tags=[],
            json_input=None,
            json_output=False
        )
        
        asset_link_id = link_result.get('assetLinkId')
        
        if not asset_link_id:
            return (False, None, "Failed to get asset link ID", [])
        
        # Prepare metadata list
        metadata_list = []
        
        # Add Transform matrix
        if transform_text:
            try:
                matrix_4x4 = parse_transform_to_matrix4x4(transform_text)
                matrix_str_value = str(matrix_4x4)
                metadata_list.append(('Matrix', matrix_str_value, 'matrix4x4'))
            except ValueError:
                pass
        
        # Add all UserData fields
        for field_name, field_value in relationship.items():
            if field_name not in ['parent', 'child', 'transform']:
                metadata_list.append((field_name, str(field_value), 'string'))
        
        return (True, asset_link_id, None, metadata_list)
    
    except Exception as e:
        return (False, None, str(e), [])


def process_parallel_operations(
    ctx: click.Context,
    components: Dict[str, Dict[str, Any]],
    relationships: List[Dict[str, Any]],
    database_id: str,
    plmxml_dir: str,
    max_workers: int,
    json_output: bool,
    failed_assets: Set[str]
) -> Dict[str, Any]:
    """
    Phase 2: Run metadata, files, and links in parallel.
    
    Returns:
        Dictionary with statistics and link metadata to create
    """
    stats = {
        'metadata_created': 0,
        'metadata_failed': 0,
        'files_uploaded': 0,
        'files_failed': 0,
        'links_created': 0,
        'links_failed': 0,
        'link_metadata_list': []  # List of (link_id, key, value, type)
    }
    
    # Calculate total operations for progress bar
    metadata_ops = sum(1 for item_rev, comp in components.items() 
                       if item_rev not in failed_assets and comp.get('actual_asset_id'))
    file_ops = sum(1 for item_rev, comp in components.items() 
                   if item_rev not in failed_assets and comp.get('actual_asset_id') and comp.get('geometry_file_location'))
    link_ops = len(relationships)
    total_ops = metadata_ops + file_ops + link_ops
    
    alias_tracker = {}
    tracker_lock = Lock()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        
        # Submit metadata creation tasks
        for item_revision, component in components.items():
            if item_revision in failed_assets:
                continue
            asset_id = component.get('actual_asset_id')
            if asset_id:
                futures.append(('metadata', executor.submit(
                    create_single_metadata,
                    ctx,
                    database_id,
                    asset_id,
                    component
                )))
        
        # Submit file upload tasks
        for item_revision, component in components.items():
            if item_revision in failed_assets:
                continue
            asset_id = component.get('actual_asset_id')
            if asset_id and component.get('geometry_file_location'):
                futures.append(('file', executor.submit(
                    upload_single_geometry,
                    ctx,
                    database_id,
                    asset_id,
                    component,
                    plmxml_dir
                )))
        
        # Submit link creation tasks
        for relationship in relationships:
            parent_item = relationship.get('parent')
            child_item = relationship.get('child')
            # Skip if either parent or child failed
            if parent_item not in failed_assets and child_item not in failed_assets:
                futures.append(('link', executor.submit(
                    create_single_link,
                    ctx,
                    relationship,
                    components,
                    database_id,
                    alias_tracker,
                    tracker_lock
                )))
        
        # Process results
        with tqdm(total=total_ops, desc="Processing metadata/files/links", disable=json_output) as pbar:
            for op_type, future in futures:
                try:
                    if op_type == 'metadata':
                        success, error = future.result()
                        if success:
                            stats['metadata_created'] += 1
                        else:
                            stats['metadata_failed'] += 1
                    
                    elif op_type == 'file':
                        success, count, error = future.result()
                        if success:
                            stats['files_uploaded'] += count
                        else:
                            stats['files_failed'] += 1
                    
                    elif op_type == 'link':
                        success, link_id, error, metadata_list = future.result()
                        if success:
                            stats['links_created'] += 1
                            # Store metadata for Phase 3
                            for key, value, mtype in metadata_list:
                                stats['link_metadata_list'].append((link_id, key, value, mtype))
                        else:
                            stats['links_failed'] += 1
                    
                    pbar.update(1)
                
                except Exception as e:
                    pbar.update(1)
    
    return stats


# ============================================================================
# PHASE 3: LINK METADATA (PARALLEL)
# ============================================================================

def create_single_link_metadata(
    ctx: click.Context,
    link_id: str,
    key: str,
    value: str,
    metadata_type: str
) -> Tuple[bool, Optional[str]]:
    """Create metadata for a single asset link (thread-safe)."""
    try:
        ctx.invoke(
            asset_link_metadata_create,
            asset_link_id=link_id,
            key=key,
            value=value,
            metadata_type=metadata_type,
            json_input=None,
            json_output=False
        )
        
        return (True, None)
    
    except Exception as e:
        return (False, str(e))


def process_link_metadata_parallel(
    ctx: click.Context,
    link_metadata_list: List[Tuple[str, str, str, str]],
    max_workers: int,
    json_output: bool
) -> Dict[str, int]:
    """
    Phase 3: Create link metadata in parallel.
    
    Returns:
        Dictionary with statistics
    """
    stats = {
        'created': 0,
        'failed': 0
    }
    
    if not link_metadata_list:
        return stats
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(
                create_single_link_metadata,
                ctx,
                link_id,
                key,
                value,
                mtype
            )
            for link_id, key, value, mtype in link_metadata_list
        ]
        
        with tqdm(total=len(futures), desc="Creating link metadata", disable=json_output) as pbar:
            for future in as_completed(futures):
                success, error = future.result()
                if success:
                    stats['created'] += 1
                else:
                    stats['failed'] += 1
                pbar.update(1)
    
    return stats


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_existing_asset_id(ctx: click.Context, database_id: str, asset_name: str) -> Optional[str]:
    """Get the asset ID for an existing asset using search."""
    try:
        search_result = ctx.invoke(
            search_simple,
            query=None,
            asset_name=asset_name,
            asset_id=None,
            asset_type=None,
            file_key=None,
            file_ext=None,
            database=database_id,
            tags=None,
            metadata_key=None,
            metadata_value=None,
            entity_types='asset',
            include_archived=False,
            from_offset=0,
            size=1,
            output_format='json',
            json_output=False
        )
        
        hits = search_result.get("hits", {}).get("hits", [])
        if hits:
            return hits[0].get("_source", {}).get("str_assetid")
        
        return None
    
    except Exception:
        return None


# ============================================================================
# MAIN COMMAND
# ============================================================================

@plmxml.command("import")
@click.option("-d", "--database-id", required=True, help="[REQUIRED] Target Database ID")
@click.option(
    "--plmxml-dir",
    required=True,
    type=click.Path(exists=True),
    help="[REQUIRED] Path to PLM XML directory to import",
)
@click.option(
    "--max-workers",
    default=15,
    type=int,
    help="[OPTIONAL] Maximum number of parallel workers (default: 15)",
)
@click.option("--json-output", is_flag=True, help="[OPTIONAL] Output raw JSON response")
@click.pass_context
@requires_setup_and_auth
def import_plmxml(
    ctx: click.Context,
    database_id: str,
    plmxml_dir: str,
    max_workers: int,
    json_output: bool,
):
    """
    Import PLM XML files into VAMS with parallel processing.

    This command imports PLM XML files and creates assets, metadata, file uploads,
    and asset links in parallel for improved performance.

    Examples:
        # Basic PLM XML import
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --plmxml-dir /path/to/plmxml

        # Import with custom worker count
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --plmxml-dir /path/to/plmxml \\
          --max-workers 20

        # Import with JSON output
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --plmxml-dir /path/to/plmxml \\
          --json-output
    """
    start_time = time.time()
    
    # Validate directory
    plmxml_path = Path(plmxml_dir)
    if not plmxml_path.exists():
        click.secho(f"✗ PLM XML directory not found: {plmxml_dir}", fg="red", bold=True, err=True)
        raise click.ClickException(f"Directory not found: {plmxml_dir}")
    
    if not plmxml_path.is_dir():
        click.secho(f"✗ Path is not a directory: {plmxml_dir}", fg="red", bold=True, err=True)
        raise click.ClickException(f"Not a directory: {plmxml_dir}")
    
    # Find XML files
    xml_files = list(plmxml_path.glob("*.xml"))
    if not xml_files:
        click.secho(f"✗ No XML files found in: {plmxml_dir}", fg="red", bold=True, err=True)
        raise click.ClickException(f"No XML files found in: {plmxml_dir}")
    
    # Phase 0: Parse all XML files
    phase0_start = time.time()
    if not json_output:
        click.secho(f"\n📋 Phase 0: Parsing {len(xml_files)} XML files...", fg="cyan", bold=True, err=True)
    
    global_components, global_relationships = parse_all_xml_files(xml_files, json_output)
    phase0_duration = time.time() - phase0_start
    
    if not json_output:
        click.secho(
            f"✓ Parsed {len(global_components)} components and {len(global_relationships)} relationships in {phase0_duration:.2f}s",
            fg="green",
            err=True
        )
    
    # Phase 1: Create assets in parallel
    phase1_start = time.time()
    if not json_output:
        click.secho(f"\n🏗️  Phase 1: Creating assets (max {max_workers} workers)...", fg="cyan", bold=True, err=True)
    
    asset_stats = process_assets_parallel(ctx, global_components, database_id, max_workers, json_output)
    phase1_duration = time.time() - phase1_start
    
    if not json_output:
        click.secho(
            f"✓ Assets: {asset_stats['created']} created, {asset_stats['existing']} existing, {asset_stats['failed']} failed in {phase1_duration:.2f}s",
            fg="green",
            err=True
        )
    
    # Phase 2: Parallel operations (metadata, files, links)
    phase2_start = time.time()
    if not json_output:
        click.secho(f"\n⚡ Phase 2: Processing metadata/files/links (max {max_workers} workers)...", fg="cyan", bold=True, err=True)
    
    parallel_stats = process_parallel_operations(
        ctx,
        global_components,
        global_relationships,
        database_id,
        plmxml_dir,
        max_workers,
        json_output,
        asset_stats['failed_items']
    )
    phase2_duration = time.time() - phase2_start
    
    if not json_output:
        click.secho(
            f"✓ Metadata: {parallel_stats['metadata_created']} created, Files: {parallel_stats['files_uploaded']} uploaded, Links: {parallel_stats['links_created']} created in {phase2_duration:.2f}s",
            fg="green",
            err=True
        )
    
    # Phase 3: Link metadata in parallel
    phase3_start = time.time()
    if not json_output and parallel_stats['link_metadata_list']:
        click.secho(f"\n🔗 Phase 3: Creating link metadata (max {max_workers} workers)...", fg="cyan", bold=True, err=True)
    
    link_metadata_stats = process_link_metadata_parallel(
        ctx,
        parallel_stats['link_metadata_list'],
        max_workers,
        json_output
    )
    phase3_duration = time.time() - phase3_start
    
    if not json_output and parallel_stats['link_metadata_list']:
        click.secho(
            f"✓ Link metadata: {link_metadata_stats['created']} created in {phase3_duration:.2f}s",
            fg="green",
            err=True
        )
    
    # Identify top-level parents
    child_item_revisions = set(r.get('child') for r in global_relationships)
    top_level_parents = []
    for item_revision, component in global_components.items():
        if item_revision not in child_item_revisions:
            asset_id = component.get('actual_asset_id')
            asset_name = component.get('sanitized_asset_id', item_revision)
            if asset_id:
                top_level_parents.append({
                    'asset_id': asset_id,
                    'asset_name': asset_name,
                    'item_revision': item_revision
                })
    
    # Calculate total duration
    total_duration = time.time() - start_time
    
    # Display final summary (CLI mode only)
    if not json_output:
        total_assets_processed = sum(1 for comp in global_components.values() if comp.get('actual_asset_id'))
        
        click.secho(
            f"\n✅ Import Complete - Final Summary:",
            fg="green",
            bold=True,
            err=True
        )
        click.secho(f"  Total Duration: {total_duration:.2f}s", fg="cyan", err=True)
        click.secho(f"", err=True)
        
        # XML Source Statistics
        click.secho(f"  📄 XML Source:", fg="yellow", bold=True, err=True)
        click.secho(f"     Files Processed: {len(xml_files)}", fg="cyan", err=True)
        click.secho(f"     Components Found: {len(global_components)}", fg="cyan", err=True)
        click.secho(f"     Relationships Found: {len(global_relationships)}", fg="cyan", err=True)
        click.secho(f"", err=True)
        
        # VAMS Entities Created
        click.secho(f"  🏗️  VAMS Entities Created:", fg="yellow", bold=True, err=True)
        click.secho(f"     Assets: {asset_stats['created']} created, {asset_stats['existing']} existing, {asset_stats['failed']} failed", fg="cyan", err=True)
        click.secho(f"     Asset Metadata: {parallel_stats['metadata_created']} created", fg="cyan", err=True)
        click.secho(f"     Files Uploaded: {parallel_stats['files_uploaded']}", fg="cyan", err=True)
        click.secho(f"     Asset Links: {parallel_stats['links_created']} created, {parallel_stats['links_failed']} failed", fg="cyan", err=True)
        click.secho(f"     Asset Link Metadata: {link_metadata_stats['created']} created", fg="cyan", err=True)
        
        if top_level_parents:
            click.secho(
                f"\n  🎯 Top-Level Parent Assets ({len(top_level_parents)}):",
                fg="yellow",
                bold=True,
                err=True
            )
            for parent in top_level_parents:
                click.secho(
                    f"     • {parent['asset_name']} (ID: {parent['asset_id']})",
                    fg="cyan",
                    err=True
                )
    
    # Create final output structure
    total_assets_processed = sum(1 for comp in global_components.values() if comp.get('actual_asset_id'))
    
    result = {
        "success": True,
        "metadata": {
            "version": "2.0",
            "description": "PLM Structure with component definitions and hierarchical relationships",
            "created": datetime.now().strftime("%Y-%m-%d"),
            "format": "Parallel processing with configurable worker pool",
        },
        "statistics": {
            "total_files_processed": len(xml_files),
            "total_assets_processed": total_assets_processed,
            "total_components": len(global_components),
            "total_relationships": len(global_relationships),
            "assets_created": asset_stats['created'],
            "assets_existing": asset_stats['existing'],
            "assets_failed": asset_stats['failed'],
            "metadata_created": parallel_stats['metadata_created'],
            "files_uploaded": parallel_stats['files_uploaded'],
            "asset_links_created": parallel_stats['links_created'],
            "asset_link_metadata_created": link_metadata_stats['created'],
            "asset_link_failures": parallel_stats['links_failed'],
            "top_level_parents": top_level_parents
        },
        "timing": {
            "total_duration_seconds": round(total_duration, 2),
            "xml_parsing_seconds": round(phase0_duration, 2),
            "asset_creation_seconds": round(phase1_duration, 2),
            "parallel_operations_seconds": round(phase2_duration, 2),
            "link_metadata_seconds": round(phase3_duration, 2)
        }
    }
    
    # Output result based on mode
    if json_output:
        click.echo(json.dumps(result, indent=2))
    
    return result


# ============================================================================
# PLM XML INGESTOR CLASS
# ============================================================================

class PLMXMLIngestor:
    """PLM XML parser that extracts component structure and metadata."""

    def __init__(self):
        self.namespace = {"plm": "http://www.plmxml.org/Schemas/PLMXMLSchema"}
        self.components = {}
        self.relationships = []
        self.root_component = None

    def strip_id_prefix(self, id_value: str) -> str:
        """Strip # prefix from ID references."""
        if id_value and id_value.startswith("#"):
            return id_value[1:]
        return id_value

    def parse_file(self, xml_file_path: str) -> Dict[str, Any]:
        """Parse a single PLM XML file and extract structure."""
        tree = ET.parse(xml_file_path)
        root = tree.getroot()

        # Parse components and relationships
        self._parse_components(root)
        self._parse_occurrences(root)

    def _parse_components(self, root: Element):
        """Parse all components (Products and ProductRevisions) from the XML."""
        # Parse Products
        for product in root.findall(".//plm:Product", self.namespace):
            product_id = product.get("productId")
            name = product.get("name", "")

        # Parse ProductRevisions
        for product_revision in root.findall(".//plm:ProductRevision", self.namespace):
            revision_id = product_revision.get("id")
            master_ref = self.strip_id_prefix(product_revision.get("masterRef", ""))
            revision = product_revision.get("revision", "")
            name = product_revision.get("name", "")
            sub_type = product_revision.get("subType", "")

            # Find the corresponding Product
            product = root.find(f".//plm:Product[@id='{master_ref}']", self.namespace)
            if product is not None:
                product_id = product.get("productId", "")
                product_name = product.get("name", name)

                # Create item_revision key
                item_revision = f"{product_id}/{revision}"

                # Initialize component data
                component_data = {
                    "id": revision_id,
                    "revision": revision,
                    "productId": product_id,
                    "product_name": product_name,
                    "item_revision": item_revision,
                    "subType": f"{sub_type} Revision Master" if sub_type else "",
                    "subClass": f"{sub_type} Revision Master" if sub_type else "",
                }

                # Extract metadata from associated attachments
                self._extract_component_metadata(root, product_revision, component_data)

                self.components[item_revision] = component_data

    def _extract_component_metadata(
        self, root: Element, product_revision: Element, component_data: Dict[str, Any]
    ):
        """Extract metadata from associated attachments."""
        product_revision_id = product_revision.get("id")

        # Get associated datasets directly from this ProductRevision
        for assoc_dataset in product_revision.findall(".//plm:AssociatedDataSet", self.namespace):
            role = assoc_dataset.get("role", "")
            dataset_ref = self.strip_id_prefix(assoc_dataset.get("dataSetRef", ""))

            if role == "IMAN_Rendering":
                # Find the DataSet and extract geometry file location
                dataset = root.find(f".//plm:DataSet[@id='{dataset_ref}']", self.namespace)
                if dataset is not None:
                    member_refs = dataset.get("memberRefs", "").split()
                    for member_ref in member_refs:
                        member_id = self.strip_id_prefix(member_ref)
                        external_file = root.find(
                            f".//plm:ExternalFile[@id='{member_id}']", self.namespace
                        )
                        if external_file is not None:
                            location_ref = external_file.get("locationRef", "")
                            if location_ref:
                                component_data["geometry_file_location"] = location_ref
                                break

        # Extract metadata from Occurrences that reference this ProductRevision
        occurrences = root.findall(
            f".//plm:Occurrence[@instancedRef='#{product_revision_id}']", self.namespace
        )
        for occurrence in occurrences:
            assoc_attachment_refs = occurrence.get("associatedAttachmentRefs", "").split()

            for attachment_ref in assoc_attachment_refs:
                attachment_id = self.strip_id_prefix(attachment_ref)
                assoc_attachment = root.find(
                    f".//plm:AssociatedAttachment[@id='{attachment_id}']", self.namespace
                )

                if assoc_attachment is not None:
                    role = assoc_attachment.get("role", "")
                    attachment_ref = self.strip_id_prefix(assoc_attachment.get("attachmentRef", ""))

                    if role == "IMAN_master_form":
                        # Find the Form and extract attributes
                        form = root.find(f".//plm:Form[@id='{attachment_ref}']", self.namespace)
                        if form is not None:
                            # Add Form attributes
                            form_sub_type = form.get("subType", "")
                            if form_sub_type:
                                component_data["subType"] = form_sub_type
                                component_data["subClass"] = form_sub_type

                            # Extract UserData from Form
                            for user_data in form.findall(".//plm:UserData", self.namespace):
                                for user_value in user_data.findall(
                                    ".//plm:UserValue", self.namespace
                                ):
                                    title = user_value.get("title", "")
                                    value = user_value.get("value", "")
                                    if title and value:
                                        component_data[title] = value

                    elif role == "IMAN_Rendering":
                        # Only add geometry file location if this component doesn't already have one
                        if "geometry_file_location" not in component_data:
                            dataset = root.find(
                                f".//plm:DataSet[@id='{attachment_ref}']", self.namespace
                            )
                            if dataset is not None:
                                member_refs = dataset.get("memberRefs", "").split()
                                for member_ref in member_refs:
                                    member_id = self.strip_id_prefix(member_ref)
                                    external_file = root.find(
                                        f".//plm:ExternalFile[@id='{member_id}']", self.namespace
                                    )
                                    if external_file is not None:
                                        location_ref = external_file.get("locationRef", "")
                                        if location_ref:
                                            component_data["geometry_file_location"] = location_ref
                                            break

    def _parse_occurrences(self, root: Element):
        """Parse occurrence hierarchy and relationships."""
        occurrences = root.findall(".//plm:Occurrence", self.namespace)

        for occurrence in occurrences:
            occurrence_id = occurrence.get("id")
            instanced_ref = self.strip_id_prefix(occurrence.get("instancedRef", ""))
            parent_ref = occurrence.get("parentRef")

            # Find the ProductRevision this occurrence references
            product_revision = root.find(
                f".//plm:ProductRevision[@id='{instanced_ref}']", self.namespace
            )
            if product_revision is not None:
                master_ref = self.strip_id_prefix(product_revision.get("masterRef", ""))
                revision = product_revision.get("revision", "")

                # Find the Product
                product = root.find(f".//plm:Product[@id='{master_ref}']", self.namespace)
                if product is not None:
                    product_id = product.get("productId", "")
                    item_revision = f"{product_id}/{revision}"

                    # Extract occurrence-specific geometry file location
                    self._extract_occurrence_geometry(root, occurrence, item_revision)

                    # If no parent, this is the root component
                    if not parent_ref:
                        self.root_component = item_revision
                    else:
                        # Find parent occurrence and create relationship
                        parent_occurrence = root.find(
                            f".//plm:Occurrence[@id='{self.strip_id_prefix(parent_ref)}']",
                            self.namespace,
                        )
                        if parent_occurrence is not None:
                            parent_instanced_ref = self.strip_id_prefix(
                                parent_occurrence.get("instancedRef", "")
                            )
                            parent_product_revision = root.find(
                                f".//plm:ProductRevision[@id='{parent_instanced_ref}']",
                                self.namespace,
                            )
                            if parent_product_revision is not None:
                                parent_master_ref = self.strip_id_prefix(
                                    parent_product_revision.get("masterRef", "")
                                )
                                parent_revision = parent_product_revision.get("revision", "")
                                parent_product = root.find(
                                    f".//plm:Product[@id='{parent_master_ref}']", self.namespace
                                )
                                if parent_product is not None:
                                    parent_product_id = parent_product.get("productId", "")
                                    parent_item_revision = f"{parent_product_id}/{parent_revision}"

                                    # Create relationship
                                    relationship = {
                                        "parent": parent_item_revision,
                                        "child": item_revision,
                                    }

                                    # Extract transform and other metadata
                                    self._extract_relationship_metadata(occurrence, relationship)

                                    self.relationships.append(relationship)

    def _extract_occurrence_geometry(
        self, root: Element, occurrence: Element, item_revision: str
    ):
        """Extract geometry file location specific to this occurrence."""
        if item_revision not in self.components:
            return

        # Get associated attachment refs from this specific occurrence
        assoc_attachment_refs = occurrence.get("associatedAttachmentRefs", "").split()

        for attachment_ref in assoc_attachment_refs:
            attachment_id = self.strip_id_prefix(attachment_ref)
            assoc_attachment = root.find(
                f".//plm:AssociatedAttachment[@id='{attachment_id}']", self.namespace
            )

            if assoc_attachment is not None:
                role = assoc_attachment.get("role", "")

                if role == "IMAN_Rendering":
                    attachment_ref = self.strip_id_prefix(assoc_attachment.get("attachmentRef", ""))
                    dataset = root.find(f".//plm:DataSet[@id='{attachment_ref}']", self.namespace)

                    if dataset is not None:
                        member_refs = dataset.get("memberRefs", "").split()
                        for member_ref in member_refs:
                            member_id = self.strip_id_prefix(member_ref)
                            external_file = root.find(
                                f".//plm:ExternalFile[@id='{member_id}']", self.namespace
                            )
                            if external_file is not None:
                                location_ref = external_file.get("locationRef", "")
                                if location_ref:
                                    # Only set geometry file location if this component doesn't already have one
                                    if (
                                        "geometry_file_location"
                                        not in self.components[item_revision]
                                    ):
                                        self.components[item_revision][
                                            "geometry_file_location"
                                        ] = location_ref
                                    break

    def _extract_relationship_metadata(self, occurrence: Element, relationship: Dict[str, Any]):
        """Extract metadata from occurrence for the relationship."""
        # Extract Transform
        transform_elem = occurrence.find(".//plm:Transform", self.namespace)
        if transform_elem is not None and transform_elem.text:
            relationship["transform"] = transform_elem.text.strip()

        # Extract ALL UserData fields (regardless of type)
        for user_data in occurrence.findall(".//plm:UserData", self.namespace):
            # Extract all UserValue elements
            for user_value in user_data.findall(".//plm:UserValue", self.namespace):
                title = user_value.get("title", "")
                value = user_value.get("value", "")
                
                if title and value:
                    # Store SequenceNumber with its original name for aliasId generation
                    if title == "SequenceNumber":
                        relationship["sequence_number"] = value
                    
                    # Store all fields with their original names (for metadata)
                    relationship[title] = value
