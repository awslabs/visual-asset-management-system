"""PLM (Product Lifecycle Management) commands for VamsCLI."""

import json
import sys
from pathlib import Path
from typing import Optional

import click

from .....utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from .....utils.exceptions import (
    AssetNotFoundError, AssetAlreadyExistsError, DatabaseNotFoundError,
    InvalidAssetDataError, FileUploadError, InvalidFileError
)
from ....assets import create as assets_create
from ....file import upload as file_upload


@click.group()
def plm():
    """Product Lifecycle Management (PLM) commands."""
    pass


@plm.group()
def plmxml():
    """PLM XML format commands."""
    pass


@plmxml.command('import')
@click.option('-d', '--database-id', required=True, 
              help='[REQUIRED] Database ID where the asset will be created')
@click.option('--name', required=True,
              help='[REQUIRED] Asset name')
@click.option('--description', required=True,
              help='[REQUIRED] Asset description')
@click.option('--distributable/--no-distributable', required=True,
              help='[REQUIRED] Whether the asset is distributable')
@click.option('--plmxml-file', required=True, type=click.Path(exists=True),
              help='[REQUIRED] Path to PLM XML file to import')
@click.option('--tags', multiple=True,
              help='[OPTIONAL] Asset tags (can be used multiple times)')
@click.option('--asset-location', default='/',
              help='[OPTIONAL] Base asset location for file (default: "/")')
@click.option('--json-output', is_flag=True,
              help='[OPTIONAL] Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def import_plmxml(ctx: click.Context, database_id: str, name: str, description: str,
                  distributable: bool, plmxml_file: str, tags: tuple, asset_location: str,
                  json_output: bool):
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
          --name "Product Assembly" \\
          --description "CAD assembly from PLM system" \\
          --distributable \\
          --plmxml-file /path/to/assembly.xml
        
        # Import with tags
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --name "Engine Component" \\
          --description "Engine part from PLM" \\
          --no-distributable \\
          --plmxml-file /path/to/engine.xml \\
          --tags mechanical --tags cad --tags v1.0
        
        # Import with custom asset location
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --name "Assembly" \\
          --description "Product assembly" \\
          --distributable \\
          --plmxml-file /path/to/assembly.xml \\
          --asset-location /plm/imports/
        
        # Import with JSON output
        vamscli industry engineering plm plmxml import \\
          -d my-database \\
          --name "Product" \\
          --description "Product data" \\
          --distributable \\
          --plmxml-file /path/to/product.xml \\
          --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    
    # Validate PLM XML file exists and is readable
    plmxml_path = Path(plmxml_file)
    if not plmxml_path.exists():
        click.echo(
            click.style(f"✗ PLM XML file not found: {plmxml_file}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(f"File not found: {plmxml_file}")
    
    if not plmxml_path.is_file():
        click.echo(
            click.style(f"✗ Path is not a file: {plmxml_file}", fg='red', bold=True),
            err=True
        )
        raise click.ClickException(f"Not a file: {plmxml_file}")
    
    # Initialize result tracking
    overall_result = {
        "success": False,
        "asset_created": False,
        "file_uploaded": False,
        "asset_id": None,
        "asset_result": None,
        "upload_result": None,
        "errors": []
    }
    
    # Step 1: Create the asset
    if not json_output:
        click.secho("Step 1/2: Creating asset...", fg='cyan', bold=True)
    
    # Invoke the assets create command programmatically
    # We need to capture the result, so we'll call it with json_output internally
    try:
        asset_result = ctx.invoke(
            assets_create,
            database_id=database_id,
            name=name,
            description=description,
            distributable=distributable,
            tags=tags,
            bucket_key=None,
            json_input=None,
            json_output=True  # Always get JSON for parsing
        )
        
        overall_result["asset_created"] = True
        overall_result["asset_result"] = asset_result
        
        # Extract asset ID from the result
        asset_id = asset_result.get('assetId')
        if not asset_id:
            raise click.ClickException("Asset created but no assetId returned")
        
        overall_result["asset_id"] = asset_id
        
        if not json_output:
            click.echo(
                click.style(f"✓ Asset created successfully: {asset_id}", fg='green', bold=True)
            )
    
    except (AssetAlreadyExistsError, DatabaseNotFoundError, InvalidAssetDataError) as e:
        overall_result["errors"].append(f"Asset creation failed: {e}")
        if json_output:
            click.echo(json.dumps(overall_result, indent=2))
        else:
            click.echo(
                click.style(f"✗ Asset creation failed: {e}", fg='red', bold=True),
                err=True
            )
            click.echo("\nThe PLM XML file was not uploaded because asset creation failed.")
        sys.exit(1)
    except click.ClickException:
        # Re-raise Click exceptions
        raise
    except Exception as e:
        # Catch any unexpected errors during asset creation
        overall_result["errors"].append(f"Unexpected error during asset creation: {e}")
        if json_output:
            click.echo(json.dumps(overall_result, indent=2))
        else:
            click.echo(
                click.style(f"✗ Unexpected error during asset creation: {e}", fg='red', bold=True),
                err=True
            )
        sys.exit(1)
    
    # Step 2: Upload the PLM XML file
    if not json_output:
        click.secho(f"\nStep 2/2: Uploading PLM XML file...", fg='cyan', bold=True)
    
    try:
        # Invoke the file upload command programmatically
        upload_result = ctx.invoke(
            file_upload,
            files_or_directory=(str(plmxml_path),),
            database_id=database_id,
            asset_id=asset_id,
            directory=None,
            asset_preview=False,
            asset_location=asset_location,
            recursive=False,
            parallel_uploads=5,
            retry_attempts=3,
            force_skip=False,
            json_input=None,
            json_output=True,  # Always get JSON for parsing
            hide_progress=json_output  # Hide progress if JSON output requested
        )
        
        overall_result["file_uploaded"] = True
        overall_result["upload_result"] = upload_result
        overall_result["success"] = True
        
        if not json_output:
            click.echo(
                click.style("✓ PLM XML file uploaded successfully!", fg='green', bold=True)
            )
    
    except (FileUploadError, InvalidFileError) as e:
        overall_result["errors"].append(f"File upload failed: {e}")
        if json_output:
            click.echo(json.dumps(overall_result, indent=2))
        else:
            click.echo(
                click.style(f"✗ File upload failed: {e}", fg='red', bold=True),
                err=True
            )
            click.echo(f"\nNote: Asset '{asset_id}' was created but the file upload failed.")
            click.echo(f"You can retry the upload with:")
            click.echo(f"  vamscli file upload -d {database_id} -a {asset_id} {plmxml_file}")
        sys.exit(1)
    except click.ClickException:
        # Re-raise Click exceptions
        raise
    except Exception as e:
        # Catch any unexpected errors during file upload
        overall_result["errors"].append(f"Unexpected error during file upload: {e}")
        if json_output:
            click.echo(json.dumps(overall_result, indent=2))
        else:
            click.echo(
                click.style(f"✗ Unexpected error during file upload: {e}", fg='red', bold=True),
                err=True
            )
            click.echo(f"\nNote: Asset '{asset_id}' was created but the file upload failed.")
        sys.exit(1)
    
    # Output final results
    if json_output:
        click.echo(json.dumps(overall_result, indent=2))
    else:
        click.echo()
        click.secho("=" * 60, fg='green')
        click.secho("✓ PLM XML Import Completed Successfully!", fg='green', bold=True)
        click.secho("=" * 60, fg='green')
        click.echo()
        click.echo(f"Asset Details:")
        click.echo(f"  Database ID: {database_id}")
        click.echo(f"  Asset ID: {asset_id}")
        click.echo(f"  Asset Name: {name}")
        click.echo(f"  Description: {description}")
        click.echo(f"  Distributable: {'Yes' if distributable else 'No'}")
        if tags:
            click.echo(f"  Tags: {', '.join(tags)}")
        click.echo()
        click.echo(f"File Details:")
        click.echo(f"  PLM XML File: {plmxml_path.name}")
        click.echo(f"  File Size: {plmxml_path.stat().st_size:,} bytes")
        click.echo(f"  Asset Location: {asset_location}")
        click.echo()
        click.echo("Next Steps:")
        click.echo(f"  • View asset: vamscli assets get -d {database_id} {asset_id}")
        click.echo(f"  • List files: vamscli file list -d {database_id} -a {asset_id}")
        click.echo(f"  • Upload more files: vamscli file upload -d {database_id} -a {asset_id} <files>")
    
    return overall_result
