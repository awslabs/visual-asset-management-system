"""PLM (Product Lifecycle Management) commands for VamsCLI."""

import json
from pathlib import Path
from typing import Dict, Any, Optional
from xml.etree.ElementTree import Element  # For type hints only
import defusedxml.ElementTree as ET  # For secure XML parsing
from tqdm.rich import tqdm
import click
import os
from .....utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from .....utils.api_client import APIClient
from .....utils.json_output import output_status, output_result, output_error, output_warning, output_info
from .....utils.exceptions import (
    AssetNotFoundError,
    AssetAlreadyExistsError,
    FileUploadError,
    InvalidFileError,
)
from ....assets import create as assets_create
from ....file import upload as file_upload
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


def sanitize_path(file_path: str) -> str:
    """Sanitize file path using OS supported path separator."""
    return os.path.normpath(file_path.replace("/", os.sep).replace("\\", os.sep))


def check_asset_exists(ctx: click.Context, database_id: str, asset_id: str) -> bool:
    """Check if an asset already exists."""
    try:
        api_client.get_asset(database_id, asset_id)
        return True
    except AssetNotFoundError:
        return False
    except Exception:
        return False


def create_asset_from_component(
    ctx: click.Context, database_id: str, component: Dict[str, Any]
) -> tuple[bool, bool]:
    """
    Create an asset from a PLM component.

    Returns:
        tuple: (success, was_created) - success indicates if operation succeeded,
               was_created indicates if asset was newly created (True) or already existed (False)
    """
    try:
        # Use item_revision as the asset name
        original_asset_name = component.get("item_revision")
        if not original_asset_name:
            click.echo("  ✗ Skipping component - no item_revision found")
            return False, False

        # Sanitize the asset ID to comply with VAMS requirements
        asset_name = sanitize_asset_id(original_asset_name)

        # Store the sanitized asset ID back in the component for later use
        component["sanitized_asset_id"] = asset_name

        # Check if asset already exists
        if check_asset_exists(ctx, database_id, asset_name):
            click.echo(
                f"  ✓ Asset '{asset_name}' already exists - skipping creation, metadata, and file uploads"
            )
            return True, False  # Success but not newly created

        # Prepare description with minimum 4 characters
        description = component.get("product_name", original_asset_name)
        if len(description) < 4:
            description = f"Asset {description}"  # Ensure minimum 4 characters

        # Create the asset
        create_result = ctx.invoke(
            assets_create,
            database_id=database_id,
            name=asset_name,
            description=description,
            distributable=True,
            bucket_key=None,
            json_input=None,
            json_output=True,  # Always get JSON for parsing
        )
        
        # Extract the actual assetId from the API response
        actual_asset_id = create_result.get('assetId')
        if not actual_asset_id:
            click.echo(f"  ✗ Failed to get asset ID from create response")
            return False, False
        
        # Store the actual asset ID returned from the API
        component["actual_asset_id"] = actual_asset_id
        
        click.secho(
                f"✓ Created asset: {actual_asset_id} (from {original_asset_name})",
                fg="green",
                bold=True,
            err=True,
        )

        return True, True  # Success and newly created

    except AssetAlreadyExistsError:
        click.echo(
            f"  ✓ Asset '{asset_name}' already exists - skipping creation, metadata, and file uploads"
        )
        return True, False  # Success but not newly created
    except Exception as e:
        click.echo(f"  ✗ Failed to create asset '{asset_name}': {e}")
        return False, False


def create_asset_metadata(
    ctx: click.Context, database_id: str, asset_id: str, component: Dict[str, Any]
) -> bool:
    """Create metadata for an asset from component attributes."""
    try:
        # Skip if asset doesn't exist
        if not check_asset_exists(ctx, database_id, asset_id):
            return False

        # Prepare metadata from all component attributes
        metadata = {}

        # Skip certain fields that are not metadata
        skip_fields = {"id", "children", "parentRef", "sanitized_asset_id", "actual_asset_id"}

        for key, value in component.items():
            if key not in skip_fields and value is not None and value != "":
                metadata[key] = str(value)

        if not metadata:
            return True

        # Create metadata using the correct API method signature
        api_client.create_metadata(database_id, asset_id, metadata)
        click.echo(f"    ✓ Created {len(metadata)} metadata items for {asset_id}")
        return True

    except Exception as e:
        click.echo(f"    ✗ Failed to create metadata for {asset_id}: {e}")
        return False


def sanitize_asset_id(asset_id: str) -> str:
    """
    Sanitize asset ID to comply with VAMS requirements.

    VAMS asset IDs must follow the regexp: ^(?!.*[<>:"\/\\\\|?*])(?!.*[.\\s]$)[\\w\\s.,\\'-]{1,254}[^.\\s]$
    This means:
    - No characters: < > : " / \\ | ? *
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


def upload_geometry_files(
    ctx: click.Context,
    database_id: str,
    asset_id: str,
    component: Dict[str, Any],
    plmxml_dir: Optional[str],
    import_geometry_mode: str = "file",
) -> bool:
    """Upload geometry files for a component based on the specified import mode."""
    if not plmxml_dir:
        return True

    try:
        geometry_location = component.get("geometry_file_location")
        if not geometry_location:
            return True

        # Sanitize the geometry file location path
        sanitized_path = sanitize_path(geometry_location)

        uploaded_count = 0

        if import_geometry_mode == "file":
            # Mode 1: Upload only the specific file mentioned in geometry-file-location
            full_file_path = os.path.join(plmxml_dir, sanitized_path)

            if os.path.exists(full_file_path) and os.path.isfile(full_file_path):
                try:
                    result = upload_single_file_to_asset(ctx, database_id, asset_id, full_file_path)
                    if result.get("success"):
                        uploaded_count += 1
                        click.echo(f"      ✓ Uploaded: {os.path.basename(full_file_path)}")
                    else:
                        click.secho(
                                f"✗ Failed to upload: {os.path.basename(full_file_path)}",
                                fg="red",
                                bold=True,
                            err=True,
                        )
                except Exception as e:
                    click.echo(f"      ✗ Error uploading {os.path.basename(full_file_path)}: {e}")
            else:
                click.echo(f"    ⚠ Geometry file not found: {full_file_path}")

        elif import_geometry_mode == "parent-directory-contents":
            # Mode 2: Upload all files in the parent directory (current behavior)
            geometry_parent_dir = os.path.dirname(sanitized_path)
            full_geometry_path = os.path.join(plmxml_dir, geometry_parent_dir)

            if not os.path.exists(full_geometry_path):
                click.echo(f"    ⚠ Geometry directory not found: {full_geometry_path}")
                return True

            # Upload all files in the geometry directory
            for file_name in os.listdir(full_geometry_path):
                file_path = os.path.join(full_geometry_path, file_name)

                if os.path.isfile(file_path):
                    try:
                        result = upload_single_file_to_asset(ctx, database_id, asset_id, file_path)
                        if result.get("success"):
                            uploaded_count += 1
                            click.echo(f"      ✓ Uploaded: {file_name}")
                        else:
                            click.echo(f"      ✗ Failed to upload: {file_name}")
                    except Exception as e:
                        click.echo(f"      ✗ Error uploading {file_name}: {e}")

        elif import_geometry_mode == "files-with-alternate-extensions":
            # Mode 3: Upload files with different extensions but same base name
            geometry_parent_dir = os.path.dirname(sanitized_path)
            geometry_filename = os.path.basename(sanitized_path)
            geometry_basename = os.path.splitext(geometry_filename)[0]

            full_geometry_path = os.path.join(plmxml_dir, geometry_parent_dir)

            if not os.path.exists(full_geometry_path):
                click.echo(f"    ⚠ Geometry directory not found: {full_geometry_path}")
                return True

            # Find all files with the same base name but different extensions
            for file_name in os.listdir(full_geometry_path):
                file_path = os.path.join(full_geometry_path, file_name)

                if os.path.isfile(file_path):
                    file_basename = os.path.splitext(file_name)[0]

                    # Check if the base name matches
                    if file_basename == geometry_basename:
                        try:
                            result = upload_single_file_to_asset(
                                ctx, database_id, asset_id, file_path
                            )
                            if result.get("success"):
                                uploaded_count += 1
                                click.echo(f"      ✓ Uploaded: {file_name}")
                            else:
                                click.echo(f"      ✗ Failed to upload: {file_name}")
                        except Exception as e:
                            click.echo(f"      ✗ Error uploading {file_name}: {e}")

        if uploaded_count > 0:
            click.secho(
                    f"✓ Uploaded {uploaded_count} geometry files for {asset_id}",
                    fg="green",
                    bold=True,
                err=True,
            )
        elif import_geometry_mode == "file":
            click.secho(
                    f"⚠ No geometry file uploaded for {asset_id}",
                    fg="yellow",
                    bold=True,
                err=True,
            )
        return True

    except Exception as e:
        click.echo(f"    ✗ Failed to upload geometry files for {asset_id}: {e}")
        return False


def upload_single_file_to_asset(
    ctx: click.Context,
    database_id: str,
    asset_id: str,
    file_path: str,
    asset_location: str = "/",
    asset_preview: bool = False,
) -> Dict[str, Any]:
    """
    Upload a single file to an asset.

    This function is based on commands/file.py but simplified for single file uploads
    without directory support. It handles file validation, upload sequences, and
    provides progress feedback.

    Args:
        api_client: Configured API client
        database_id: Target database ID
        asset_id: Target asset ID
        file_path: Path to the file to upload
        asset_location: Asset location path (default: "/")
        asset_preview: Whether to upload as asset preview (default: False)

    Returns:
        Dict containing upload results

    Raises:
        InvalidFileError: If file is invalid for upload
        FileUploadError: If upload fails
        AssetNotFoundError: If asset doesn't exist
    """

    # Validate file exists and is a file
    file_path_obj = Path(file_path)
    if not file_path_obj.exists():
        raise InvalidFileError(f"File not found: {file_path}")
    if not file_path_obj.is_file():
        raise InvalidFileError(f"Path is not a file: {file_path}")

    try:

        # Use ctx.invoke to call the file_upload command directly
        result = ctx.invoke(
            file_upload,
            files_or_directory=[file_path],
            database_id=database_id,
            asset_id=asset_id,
            directory=None,
            json_input=None,
        )

        # Return simplified result
        return {
                "success": result.get("overall_success", False),
                "file_name": file_path_obj.name,
                "file_size": result.get("total_size_formatted", "Unknown"),
                "upload_type": "assetPreview" if asset_preview else "assetFile",
                "asset_location": asset_location,
                "duration": result.get("upload_duration", 0),
                "failed_files": result.get("failed_files", 0),
                "message": f"File {'uploaded successfully' if result.get('overall_success') else 'upload failed'}",
        }

    except click.ClickException:
        raise
    except Exception as e:
        raise FileUploadError(f"Upload failed: {e}")


@plmxml.command("import")
@click.option("-d", "--database-id", required=True, help="[REQUIRED] Target Database ID")
@click.option(
    "--plmxml-dir",
    required=True,
    type=click.Path(exists=True),
    help="[REQUIRED] Path to PLM XML directory to import",
)
@click.option(
    "--asset-location", default="/", help='[OPTIONAL] Base asset location for file (default: "/")'
)
@click.option("--json-output", is_flag=True, help="[OPTIONAL] Output raw JSON response")
@click.pass_context
@requires_setup_and_auth
def import_plmxml(
    ctx: click.Context,
    database_id: str,
    plmxml_dir: str,
    asset_location: str,
    json_output: bool,
):
    """
    Import a PLM XML file as a new VAMS asset.

    This command provides a streamlined workflow for importing PLM XML files into VAMS.
    It performs two operations in sequence:

    1. Creates a new asset with the specified metadata
    2. Uploads the PLM XML file to the newly created asset

    If asset creation fails, the upload is not attempted. If the upload fails after
    asset creation, the asset will exist but without the file attached.

    Examples:
        # Basic PLM XML import
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --plmxml-dir /path/to/assembly.xml

        # Import with tags
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --plmxml-dir /path/to/engine.xml \\
          --tags mechanical --tags cad --tags v1.0

        # Import with custom asset location
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --plmxml-dir /path/to/assembly.xml \\
          --asset-location /plm/imports/

        # Import with JSON output
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --plmxml-dir /path/to/product.xml \\
          --json-output
    """
    # Get profile manager and API client (setup/auth already validated by decorator)
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)

    # Validate PLM XML file exists and is readable
    plmxml_path = Path(plmxml_dir)
    if not plmxml_path.exists():
        click.secho(f"✗ PLM XML file not found: {plmxml_dir}", fg="red", bold=True, err=True
        )
        raise click.ClickException(f"File not found: {plmxml_dir}")

    if not plmxml_path.is_dir():
        click.secho(f"✗ Path is not a directory: {plmxml_dir}", fg="red", bold=True, err=True
        )
        raise click.ClickException(f"Not a directory: {plmxml_dir}")

    # Initialize result tracking
    overall_result = {
        "success": False,
        "asset_created": False,
        "file_uploaded": False,
        "asset_id": None,
        "asset_result": None,
        "upload_result": None,
        "errors": [],
    }

    directory = Path(plmxml_dir)
    xml_files = list(directory.glob("*.xml"))

    all_files_data = []

    for xml_file in tqdm(xml_files, "Processing plmxml file"):
        ingestor = PLMXMLIngestor()
        file_data = ingestor.parse_file(xml_file)
        all_files_data.append(file_data)

        if not ingestor.components:
            return click.echo(f"No components found in {xml_file}")

        click.secho(
                f"Processing {len(ingestor.components)} components from {xml_file}",
                fg="green", err=True
        )

        # Statistics
        stats = {
            "total_components": len(ingestor.components),
            "assets_created": 0,
            "assets_skipped": 0,
            "metadata_created": 0,
            "files_uploaded": 0,
            "links_created": 0,
        }

        # Step 1: Create assets for each component
        for key, component in ingestor.components.items():
            click.secho(
                    f"Processing component: {component.get('item_revision', component.get('id', 'Unknown'))}",
                    fg="cyan",
                    bold=True
            )

            success, was_created = create_asset_from_component(ctx, database_id, component)

            if success:
                # Use the actual asset ID returned from the API
                asset_id = component.get(
                    "actual_asset_id", 
                    component.get("sanitized_asset_id", sanitize_asset_id(component.get("item_revision", "")))
                )

                if was_created:
                    # Asset was newly created - process metadata and files
                    stats["assets_created"] += 1

                    # Step 1a: Create metadata for the asset (only for newly created assets)
                    if create_asset_metadata(ctx, database_id, asset_id, component):
                        stats["metadata_created"] += 1

                    # Step 3: Upload geometry files if geometry exists (only for newly created assets)
                    if upload_geometry_files(
                        ctx,
                        database_id,
                        asset_id,
                        component,
                        plmxml_dir,
                    ):
                        stats["files_uploaded"] += 1
                else:
                    # Asset already existed - skip metadata and file processing
                    stats["assets_skipped"] += 1
            else:
                # Asset creation failed
                stats["assets_skipped"] += 1

    # Create final output structure
    result = {
        "metadata": {
            "version": "2.0",
            "description": "PLM Structure with component definitions and hierarchical relationships",
            "created": datetime.now().strftime("%Y-%m-%d"),
            "format": "Independent files with component library and relationship metadata",
        },
        "files": all_files_data,
    }

    return result


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

        # Extract file metadata
        file_metadata = self._extract_file_metadata(root, xml_file_path)

        # Parse components and relationships
        self._parse_components(root)
        self._parse_occurrences(root)

        # Build the output structure
        # result = {
        #     "filename": os.path.basename(xml_file_path),
        #     "date": file_metadata.get("date", ""),
        #     "time": file_metadata.get("time", ""),
        #     "author": file_metadata.get("author", ""),
        #     "root_component": self.root_component,
        #     "hierarchy": {"relationships": self.relationships},
        #     "components": self.components,
        # }

        # return result

    def _extract_file_metadata(self, root: Element, file_path: str) -> Dict[str, str]:
        """Extract metadata from PLMXML root element."""
        return {
            "date": root.get("date", ""),
            "time": root.get("time", ""),
            "author": root.get("author", ""),
        }

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
        # This ensures we only get attachments that are specifically associated with this component
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
                        # and if this attachment is specifically for this occurrence
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
                                    # This ensures child-specific geometry files take precedence
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

        # Extract UserData
        for user_data in occurrence.findall(".//plm:UserData", self.namespace):
            data_type = user_data.get("type", "")

            if data_type == "AttributesInContext":
                for user_value in user_data.findall(".//plm:UserValue", self.namespace):
                    title = user_value.get("title", "")
                    value = user_value.get("value", "")
                    if title == "SequenceNumber" and value:
                        relationship["sequence_number"] = value

            elif data_type == "InstanceNotes":
                for user_value in user_data.findall(".//plm:UserValue", self.namespace):
                    title = user_value.get("title", "")
                    value = user_value.get("value", "")
                    if title and value:
                        relationship[title.lower().replace(" ", "_")] = value
