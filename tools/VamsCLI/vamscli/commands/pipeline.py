"""Pipeline management commands for VamsCLI.

Covers the pipeline CRUD surface plus the template and tag-schema sub-resources:

    pipeline create / get / list / update / delete / unarchive
    pipeline template create / get / list / update / delete
    pipeline tag-schema get / set

Pipelines are database-scoped (PK databaseId, SK pipelineId). Create is a POST to the
database-scoped collection; the bare `pipeline list` (no database) lists all pipelines the caller
can access. Delete is a soft archive, which `unarchive` reverses.
"""

import json
import sys
from typing import Dict, Any, Optional

import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_warning
from ..utils.exceptions import (
    PipelineNotFoundError, PipelineAlreadyExistsError, InvalidPipelineDataError,
    PipelineTemplateNotFoundError, PipelineTemplateAlreadyExistsError, InvalidPipelineTemplateDataError,
    DatabaseNotFoundError,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_json_option(inline_value: Optional[str], file_path: Optional[str], label: str) -> Optional[Any]:
    """Resolve a JSON option supplied either inline (--x '{...}') or from a file (--x-file path).
    Returns the parsed object, or None when neither is supplied. Raises click.ClickException on
    invalid JSON or when both are supplied."""
    if inline_value and file_path:
        raise click.ClickException(f"Provide {label} either inline or via a file, not both.")
    raw = None
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as e:
            raise click.ClickException(f"Could not read {label} file '{file_path}': {e}")
    elif inline_value:
        raw = inline_value
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON for {label}: {e}")


def _load_text_option(inline_value: Optional[str], file_path: Optional[str], label: str) -> Optional[str]:
    """Resolve a raw-text option supplied inline or from a file (e.g. a template config body that may
    be JSON/YAML/OpenJD/XML/raw). Returns the text, or None when neither is supplied."""
    if inline_value and file_path:
        raise click.ClickException(f"Provide {label} either inline or via a file, not both.")
    if file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError as e:
            raise click.ClickException(f"Could not read {label} file '{file_path}': {e}")
    return inline_value


def _api(ctx: click.Context) -> APIClient:
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    return APIClient(config['api_gateway_url'], profile_manager)


def _message(result: Dict[str, Any]) -> Any:
    """Unwrap the {'message': ...} envelope; returns the raw result if unenveloped."""
    return result.get('message', result) if isinstance(result, dict) else result


def _message_with_warnings(result: Dict[str, Any]) -> Any:
    """Unwrap the `message` envelope, carrying any top-level `warnings` array into the payload so
    the array survives `--json-output`. Non-dict payloads are returned unchanged."""
    message = _message(result)
    if not isinstance(result, dict) or not isinstance(message, dict):
        return message
    warnings = result.get('warnings')
    if not warnings:
        return message
    payload = dict(message)
    payload['warnings'] = warnings
    return payload


def _emit_warnings(result: Dict[str, Any], json_output: bool) -> None:
    """Print any top-level `warnings` array from a successful save as a visible warning.
    Suppressed in JSON mode, where the emitted payload carries the `warnings` array."""
    if not isinstance(result, dict):
        return
    warnings = result.get('warnings')
    if warnings:
        for warning in warnings:
            output_warning(f"⚠️  {warning}", json_output)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_pipeline(pipeline: Dict[str, Any]) -> str:
    lines = [
        f"Pipeline ID: {pipeline.get('pipelineId', 'N/A')}",
        f"Name: {pipeline.get('pipelineName', 'N/A')}",
        f"Database: {pipeline.get('databaseId', 'N/A')}",
        f"Category: {pipeline.get('category', 'N/A')}",
        f"Execution Type: {(pipeline.get('executionConfig') or {}).get('executionType', 'N/A')}",
        f"Enabled: {pipeline.get('enabled', 'N/A')}",
        f"Archived: {pipeline.get('archived', False)}",
    ]
    template_count = pipeline.get('templateCount')
    if template_count is not None:
        lines.append(f"Template Count: {template_count}")
    description = pipeline.get('description')
    if description:
        lines.append(f"Description: {description}")
    templates = pipeline.get('templates')
    if templates:
        listed = ', '.join(t.get('templateId', '?') for t in templates)
        # The details response caps its inline templates list; templateCount is the true total, so a
        # shortfall means the rest are only reachable through `pipeline template list`.
        if isinstance(template_count, int) and template_count > len(templates):
            listed += (f" (showing {len(templates)} of {template_count} — "
                       f"use 'pipeline template list' for the rest)")
        lines.append(f"Templates: {listed}")
    return '\n'.join(lines)


def format_template(template: Dict[str, Any]) -> str:
    lines = [
        f"Template ID: {template.get('templateId', 'N/A')}",
        f"Name: {template.get('templateName', 'N/A')}",
        f"Pipeline: {template.get('pipelineId', 'N/A')}",
        f"Config Format: {template.get('configFormat', 'N/A')}",
        f"Allow Custom Edit: {template.get('allowCustomEdit', False)}",
        f"Default: {template.get('isDefault', False)}",
    ]
    description = template.get('description')
    if description:
        lines.append(f"Description: {description}")
    tag_schema = template.get('tagSchema')
    if tag_schema:
        lines.append(f"Tag Fields: {', '.join(f.get('tagKey', '?') for f in tag_schema)}")
    return '\n'.join(lines)


@click.group()
def pipeline():
    """Pipeline management commands (pipelines, templates, tag schemas)."""
    pass


# ---------------------------------------------------------------------------
# Pipeline CRUD
# ---------------------------------------------------------------------------

@pipeline.command('list')
@click.option('-d', '--database-id', help='Database ID to list pipelines from (omit to list all accessible pipelines)')
@click.option('--include-archived', is_flag=True, help='Include archived pipelines')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--starting-token', help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_pipelines(ctx: click.Context, database_id: Optional[str], include_archived: bool,
                   page_size: Optional[int], starting_token: Optional[str], json_output: bool):
    """List pipelines in a database, or all accessible pipelines.

    Examples:
        vamscli pipeline list
        vamscli pipeline list -d my-database
        vamscli pipeline list -d my-database --include-archived --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    params = {}
    if page_size:
        params['pageSize'] = page_size
    if starting_token:
        params['startingToken'] = starting_token

    output_status("Listing pipelines...", json_output)
    try:
        result = api_client.list_pipelines(
            database_id=database_id, include_archived=include_archived, params=params)
        message = _message(result)

        def _fmt(_r):
            items = message.get('Items', [])
            if not items:
                # The backend filters archived + unauthorized rows after the DynamoDB page limit, so
                # an empty page may still carry a NextToken for later pages that do contain matches.
                if message.get('NextToken'):
                    return ("No pipelines on this page; more pages available."
                            f"\n\nNext token: {message['NextToken']}")
                return "No pipelines found."
            out = [f"Found {len(items)} pipeline(s):", "-" * 80]
            for item in items:
                out.append(format_pipeline(item))
                out.append("-" * 80)
            if message.get('NextToken'):
                out.append(f"\nNext token: {message['NextToken']}")
            return '\n'.join(out)

        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except DatabaseNotFoundError as e:
        output_error(e, json_output, error_type="Database Not Found",
                     helpful_message="Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))


@pipeline.command('get')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID to retrieve')
@click.option('--include-archived', is_flag=True, help='Retrieve even if archived')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get_pipeline(ctx: click.Context, database_id: str, pipeline_id: str,
                 include_archived: bool, json_output: bool):
    """Get a pipeline and its templates.

    Examples:
        vamscli pipeline get -d my-db -p my-pipeline
    """
    api_client = _api(ctx)
    output_status(f"Retrieving pipeline '{pipeline_id}'...", json_output)
    try:
        result = api_client.get_pipeline(database_id, pipeline_id, include_archived=include_archived)
        output_result(_message(result), json_output,
                      cli_formatter=lambda _r: format_pipeline(_message(result)))
        return result
    except PipelineNotFoundError as e:
        output_error(e, json_output, error_type="Pipeline Not Found",
                     helpful_message=f"Use 'vamscli pipeline list -d {database_id}' to see available pipelines.")
        raise click.ClickException(str(e))


@pipeline.command('create')
@click.option('-d', '--database-id', required=True, help='Database ID to create the pipeline in (GLOBAL allowed)')
@click.option('-n', '--name', 'pipeline_name', required=True, help='Human-readable pipeline name')
@click.option('-p', '--pipeline-id', help='Explicit pipeline ID (a GUID is generated when omitted)')
@click.option('--category', default='', help='Pipeline category')
@click.option('--description', default='', help='Pipeline description')
@click.option('--execution-config', help='executionConfig as inline JSON')
@click.option('--execution-config-file', type=click.Path(exists=True), help='executionConfig from a JSON file')
@click.option('--system-config', help='systemConfig as inline JSON')
@click.option('--system-config-file', type=click.Path(exists=True), help='systemConfig from a JSON file')
@click.option('--disabled', is_flag=True, help='Create the pipeline disabled')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create_pipeline(ctx: click.Context, database_id: str, pipeline_name: str,
                    pipeline_id: Optional[str], category: str, description: str,
                    execution_config: Optional[str], execution_config_file: Optional[str],
                    system_config: Optional[str], system_config_file: Optional[str],
                    disabled: bool, json_output: bool):
    """Create a pipeline.

    executionConfig selects the execution type (Lambda / SQS / EventBridge / DeadlineCloud) and its
    per-type resource block; systemConfig sets input-file arity, asset scope, metadata inputs, and
    template requirements. Both are JSON objects supplied inline or from a file.

    systemConfig.metadataInputs is a boolean map over assetMetadata, fileMetadata, fileAttributes,
    and databaseMetadata, each defaulting to true. It gates which metadata a run captures, not
    whether the caller must supply it: naming a metadata source is always optional.

    Examples:
        vamscli pipeline create -d my-db -n "My Converter" \\
            --execution-config '{"executionType": "Lambda"}'
        vamscli pipeline create -d my-db -n "My Converter" -p my-converter \\
            --execution-config-file exec.json --system-config-file sys.json
    """
    api_client = _api(ctx)
    exec_cfg = _load_json_option(execution_config, execution_config_file, "executionConfig")
    sys_cfg = _load_json_option(system_config, system_config_file, "systemConfig")

    body: Dict[str, Any] = {
        'databaseId': database_id,
        'pipelineName': pipeline_name,
        'category': category,
        'description': description,
    }
    if pipeline_id:
        body['pipelineId'] = pipeline_id
    if exec_cfg is not None:
        body['executionConfig'] = exec_cfg
    if sys_cfg is not None:
        body['systemConfig'] = sys_cfg
    if disabled:
        body['enabled'] = False

    output_status(f"Creating pipeline '{pipeline_name}'...", json_output)
    try:
        result = api_client.create_pipeline(database_id, body)
        output_result(_message_with_warnings(result), json_output,
                      success_message="✓ Pipeline created successfully!",
                      cli_formatter=lambda _r: format_pipeline(_message(result)))
        _emit_warnings(result, json_output)
        return result
    except PipelineAlreadyExistsError as e:
        output_error(e, json_output, error_type="Pipeline Already Exists")
        raise click.ClickException(str(e))
    except InvalidPipelineDataError as e:
        output_error(e, json_output, error_type="Invalid Pipeline Data",
                     helpful_message="Check the executionConfig/systemConfig JSON and required fields.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(e, json_output, error_type="Database Not Found")
        raise click.ClickException(str(e))


@pipeline.command('update')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID to update')
@click.option('-n', '--name', 'pipeline_name', help='New pipeline name')
@click.option('--category', help='New category')
@click.option('--description', help='New description')
@click.option('--execution-config', help='New executionConfig as inline JSON')
@click.option('--execution-config-file', type=click.Path(exists=True), help='New executionConfig from a JSON file')
@click.option('--system-config', help='New systemConfig as inline JSON')
@click.option('--system-config-file', type=click.Path(exists=True), help='New systemConfig from a JSON file')
@click.option('--enable/--disable', 'enabled', default=None, help='Enable or disable the pipeline')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update_pipeline(ctx: click.Context, database_id: str, pipeline_id: str,
                    pipeline_name: Optional[str], category: Optional[str], description: Optional[str],
                    execution_config: Optional[str], execution_config_file: Optional[str],
                    system_config: Optional[str], system_config_file: Optional[str],
                    enabled: Optional[bool], json_output: bool):
    """Update a pipeline (only supplied fields change). At least one field is required.

    Examples:
        vamscli pipeline update -d my-db -p my-pipeline --description "Updated"
        vamscli pipeline update -d my-db -p my-pipeline --disable
    """
    api_client = _api(ctx)
    exec_cfg = _load_json_option(execution_config, execution_config_file, "executionConfig")
    sys_cfg = _load_json_option(system_config, system_config_file, "systemConfig")

    body: Dict[str, Any] = {}
    if pipeline_name is not None:
        body['pipelineName'] = pipeline_name
    if category is not None:
        body['category'] = category
    if description is not None:
        body['description'] = description
    if exec_cfg is not None:
        body['executionConfig'] = exec_cfg
    if sys_cfg is not None:
        body['systemConfig'] = sys_cfg
    if enabled is not None:
        body['enabled'] = enabled
    if not body:
        raise click.ClickException("Provide at least one field to update.")

    output_status(f"Updating pipeline '{pipeline_id}'...", json_output)
    try:
        result = api_client.update_pipeline(database_id, pipeline_id, body)
        output_result(_message_with_warnings(result), json_output,
                      success_message="✓ Pipeline updated successfully!",
                      cli_formatter=lambda _r: format_pipeline(_message(result)))
        _emit_warnings(result, json_output)
        return result
    except PipelineNotFoundError as e:
        output_error(e, json_output, error_type="Pipeline Not Found")
        raise click.ClickException(str(e))
    except InvalidPipelineDataError as e:
        output_error(e, json_output, error_type="Invalid Pipeline Data")
        raise click.ClickException(str(e))


@pipeline.command('delete')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID to archive')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete_pipeline(ctx: click.Context, database_id: str, pipeline_id: str, json_output: bool):
    """Archive (soft-delete) a pipeline.

    Examples:
        vamscli pipeline delete -d my-db -p my-pipeline
    """
    api_client = _api(ctx)
    output_status(f"Archiving pipeline '{pipeline_id}'...", json_output)
    try:
        result = api_client.delete_pipeline(database_id, pipeline_id)
        output_result(_message(result), json_output, success_message="✓ Pipeline archived.")
        return result
    except PipelineNotFoundError as e:
        output_error(e, json_output, error_type="Pipeline Not Found")
        raise click.ClickException(str(e))


@pipeline.command('unarchive')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Archived pipeline ID to unarchive')
@click.option('--keep-disabled', is_flag=True,
              help='Leave the pipeline disabled after unarchiving (default re-enables it)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def unarchive_pipeline(ctx: click.Context, database_id: str, pipeline_id: str,
                       keep_disabled: bool, json_output: bool):
    """Unarchive an archived pipeline.

    Clears the archived flag set by `pipeline delete`, returning the pipeline to the default
    listing. An archived pipeline keeps its ID, so no other pipeline can take that ID while it is
    archived.

    Archiving also disables the pipeline, so unarchiving re-enables it to leave it executable.
    Pass --keep-disabled to unarchive without re-enabling.

    Examples:
        vamscli pipeline unarchive -d my-db -p my-pipeline
        vamscli pipeline unarchive -d my-db -p my-pipeline --keep-disabled
    """
    api_client = _api(ctx)
    body: Dict[str, Any] = {'archived': False}
    if not keep_disabled:
        body['enabled'] = True
    output_status(f"Unarchiving pipeline '{pipeline_id}'...", json_output)
    try:
        result = api_client.update_pipeline(database_id, pipeline_id, body)
        output_result(_message_with_warnings(result), json_output,
                      success_message="✓ Pipeline unarchived.",
                      cli_formatter=lambda _r: format_pipeline(_message(result)))
        _emit_warnings(result, json_output)
        return result
    except PipelineNotFoundError as e:
        output_error(e, json_output, error_type="Pipeline Not Found",
                     helpful_message="Use 'vamscli pipeline list -d <db> --include-archived' to see "
                                     "archived pipelines.")
        raise click.ClickException(str(e))
    except InvalidPipelineDataError as e:
        output_error(e, json_output, error_type="Invalid Pipeline Data")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# Template sub-group
# ---------------------------------------------------------------------------

@pipeline.group('template')
def template():
    """Pipeline template management commands."""
    pass


@template.command('list')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID whose templates to list')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_templates(ctx: click.Context, database_id: str, pipeline_id: str, json_output: bool):
    """List a pipeline's templates."""
    api_client = _api(ctx)
    output_status(f"Listing templates for pipeline '{pipeline_id}'...", json_output)
    try:
        result = api_client.list_pipeline_templates(database_id, pipeline_id)
        message = _message(result)

        def _fmt(_r):
            items = message.get('Items', [])
            if not items:
                return "No templates found."
            out = [f"Found {len(items)} template(s):", "-" * 80]
            for item in items:
                out.append(format_template(item))
                out.append("-" * 80)
            return '\n'.join(out)

        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except PipelineNotFoundError as e:
        output_error(e, json_output, error_type="Pipeline Not Found")
        raise click.ClickException(str(e))


@template.command('get')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID owning the template')
@click.option('-t', '--template-id', required=True, help='Template ID to retrieve')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get_template(ctx: click.Context, database_id: str, pipeline_id: str, template_id: str,
                 json_output: bool):
    """Get a template (config body rehydrated inline)."""
    api_client = _api(ctx)
    output_status(f"Retrieving template '{template_id}'...", json_output)
    try:
        result = api_client.get_pipeline_template(database_id, pipeline_id, template_id)
        output_result(_message(result), json_output,
                      cli_formatter=lambda _r: format_template(_message(result)))
        return result
    except PipelineTemplateNotFoundError as e:
        output_error(e, json_output, error_type="Template Not Found")
        raise click.ClickException(str(e))


@template.command('create')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID to add the template to')
@click.option('-n', '--name', 'template_name', required=True, help='Human-readable template name')
@click.option('-t', '--template-id', help='Explicit template ID (a GUID is generated when omitted)')
@click.option('--description', default='', help='Template description')
@click.option('--config-format', default='json',
              type=click.Choice(['json', 'yaml', 'openjd', 'xml', 'raw']), help='Config body format')
@click.option('--config-body', help='Config body inline')
@click.option('--config-body-file', type=click.Path(exists=True), help='Config body from a file')
@click.option('--web-form-json', help='Web form definition JSON inline')
@click.option('--web-form-file', type=click.Path(exists=True), help='Web form definition JSON from a file')
@click.option('--allow-custom-edit', is_flag=True, help='Allow per-execution custom override of the config')
@click.option('--default', 'is_default', is_flag=True,
              help="Set as the pipeline's default template (clears any prior default)")
@click.option('--input-instructions', default='', help='Input instructions shown to the user')
@click.option('--overrides', help='Template overrides as inline JSON')
@click.option('--overrides-file', type=click.Path(exists=True), help='Template overrides from a JSON file')
@click.option('--tag-schema', help='Tag schema (list of field defs) as inline JSON')
@click.option('--tag-schema-file', type=click.Path(exists=True), help='Tag schema from a JSON file')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create_template(ctx: click.Context, database_id: str, pipeline_id: str, template_name: str,
                    template_id: Optional[str], description: str, config_format: str,
                    config_body: Optional[str], config_body_file: Optional[str],
                    web_form_json: Optional[str], web_form_file: Optional[str],
                    allow_custom_edit: bool, is_default: bool, input_instructions: str,
                    overrides: Optional[str], overrides_file: Optional[str],
                    tag_schema: Optional[str], tag_schema_file: Optional[str], json_output: bool):
    """Create a pipeline template.

    --overrides narrows the pipeline's own systemConfig for runs that use this template, over the keys
    inputFileArity, assetScope, metadataInputs, and inputFileFilters. metadataInputs takes the same
    boolean map as the pipeline: assetMetadata, fileMetadata, fileAttributes, databaseMetadata.

    Examples:
        vamscli pipeline template create -d my-db -p my-pipe -n "OBJ output" \\
            --config-body-file obj-config.json --tag-schema-file tags.json
    """
    api_client = _api(ctx)
    body_text = _load_text_option(config_body, config_body_file, "config body")
    web_form = _load_text_option(web_form_json, web_form_file, "web form JSON")
    overrides_obj = _load_json_option(overrides, overrides_file, "overrides")
    tag_schema_obj = _load_json_option(tag_schema, tag_schema_file, "tagSchema")

    body: Dict[str, Any] = {
        'templateName': template_name,
        'description': description,
        'configFormat': config_format,
        'allowCustomEdit': allow_custom_edit,
        'isDefault': is_default,
        'inputInstructions': input_instructions,
    }
    if template_id:
        body['templateId'] = template_id
    if body_text is not None:
        body['configBody'] = body_text
    if web_form is not None:
        body['webFormJson'] = web_form
    if overrides_obj is not None:
        body['overrides'] = overrides_obj
    if tag_schema_obj is not None:
        body['tagSchema'] = tag_schema_obj

    output_status(f"Creating template '{template_name}'...", json_output)
    try:
        result = api_client.create_pipeline_template(database_id, pipeline_id, body)
        output_result(_message(result), json_output, success_message="✓ Template created successfully!",
                      cli_formatter=lambda _r: format_template(_message(result)))
        return result
    except PipelineTemplateAlreadyExistsError as e:
        output_error(e, json_output, error_type="Template Already Exists")
        raise click.ClickException(str(e))
    except InvalidPipelineTemplateDataError as e:
        output_error(e, json_output, error_type="Invalid Template Data",
                     helpful_message="Check the config body, tag schema, and config format.")
        raise click.ClickException(str(e))
    except PipelineNotFoundError as e:
        output_error(e, json_output, error_type="Pipeline Not Found")
        raise click.ClickException(str(e))


@template.command('update')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID owning the template')
@click.option('-t', '--template-id', required=True, help='Template ID to update')
@click.option('-n', '--name', 'template_name', help='New template name')
@click.option('--description', help='New template description')
@click.option('--config-format', type=click.Choice(['json', 'yaml', 'openjd', 'xml', 'raw']),
              help='New config body format')
@click.option('--config-body', help='New config body inline')
@click.option('--config-body-file', type=click.Path(exists=True), help='New config body from a file')
@click.option('--web-form-json', help='New web form definition JSON inline')
@click.option('--web-form-file', type=click.Path(exists=True), help='New web form JSON from a file')
@click.option('--allow-custom-edit/--no-custom-edit', 'allow_custom_edit', default=None,
              help='Toggle per-execution custom override')
@click.option('--default/--no-default', 'is_default', default=None,
              help="Set or clear this template as the pipeline's default")
@click.option('--input-instructions', help='New input instructions')
@click.option('--overrides', help='New template overrides as inline JSON')
@click.option('--overrides-file', type=click.Path(exists=True), help='New template overrides from a JSON file')
@click.option('--tag-schema', help='New tag schema (list of field defs) as inline JSON')
@click.option('--tag-schema-file', type=click.Path(exists=True), help='New tag schema from a JSON file')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update_template(ctx: click.Context, database_id: str, pipeline_id: str, template_id: str,
                    template_name: Optional[str], description: Optional[str],
                    config_format: Optional[str],
                    config_body: Optional[str], config_body_file: Optional[str],
                    web_form_json: Optional[str], web_form_file: Optional[str],
                    allow_custom_edit: Optional[bool], is_default: Optional[bool],
                    input_instructions: Optional[str],
                    overrides: Optional[str], overrides_file: Optional[str],
                    tag_schema: Optional[str], tag_schema_file: Optional[str], json_output: bool):
    """Update a pipeline template (only supplied fields change). At least one field is required."""
    api_client = _api(ctx)
    body_text = _load_text_option(config_body, config_body_file, "config body")
    web_form = _load_text_option(web_form_json, web_form_file, "web form JSON")
    overrides_obj = _load_json_option(overrides, overrides_file, "overrides")
    tag_schema_obj = _load_json_option(tag_schema, tag_schema_file, "tagSchema")

    body: Dict[str, Any] = {}
    if template_name is not None:
        body['templateName'] = template_name
    if description is not None:
        body['description'] = description
    if config_format is not None:
        body['configFormat'] = config_format
    if body_text is not None:
        body['configBody'] = body_text
    if web_form is not None:
        body['webFormJson'] = web_form
    if allow_custom_edit is not None:
        body['allowCustomEdit'] = allow_custom_edit
    if is_default is not None:
        body['isDefault'] = is_default
    if input_instructions is not None:
        body['inputInstructions'] = input_instructions
    if overrides_obj is not None:
        body['overrides'] = overrides_obj
    if tag_schema_obj is not None:
        body['tagSchema'] = tag_schema_obj
    if not body:
        raise click.ClickException("Provide at least one field to update.")

    output_status(f"Updating template '{template_id}'...", json_output)
    try:
        result = api_client.update_pipeline_template(database_id, pipeline_id, template_id, body)
        output_result(_message(result), json_output, success_message="✓ Template updated successfully!",
                      cli_formatter=lambda _r: format_template(_message(result)))
        return result
    except PipelineTemplateNotFoundError as e:
        output_error(e, json_output, error_type="Template Not Found")
        raise click.ClickException(str(e))
    except InvalidPipelineTemplateDataError as e:
        output_error(e, json_output, error_type="Invalid Template Data")
        raise click.ClickException(str(e))


@template.command('delete')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID owning the template')
@click.option('-t', '--template-id', required=True, help='Template ID to delete')
@click.option('--yes', is_flag=True, help='Skip the interactive confirmation prompt')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete_template(ctx: click.Context, database_id: str, pipeline_id: str, template_id: str,
                    yes: bool, json_output: bool):
    """Delete a pipeline template.

    This is a hard delete of the template row, its offloaded config bodies, and its tag schema —
    unlike `pipeline delete`, which is a soft archive. `--yes` is required in JSON mode, where no
    interactive prompt is possible.
    """
    api_client = _api(ctx)
    if not yes:
        if json_output:
            output_result({"error": "Confirmation required",
                           "message": "Deleting a template requires the --yes flag",
                           "templateId": template_id}, json_output=True)
            sys.exit(1)
        click.confirm(
            f"Permanently delete template '{template_id}' and its stored config bodies? "
            "This is irreversible.", abort=True)

    output_status(f"Deleting template '{template_id}'...", json_output)
    try:
        result = api_client.delete_pipeline_template(database_id, pipeline_id, template_id)
        output_result(_message(result), json_output, success_message="✓ Template deleted.")
        return result
    except PipelineTemplateNotFoundError as e:
        output_error(e, json_output, error_type="Template Not Found")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# Tag-schema sub-group
# ---------------------------------------------------------------------------

@pipeline.group('tag-schema')
def tag_schema_group():
    """Pipeline template tag-schema commands."""
    pass


@tag_schema_group.command('get')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID owning the template')
@click.option('-t', '--template-id', required=True, help='Template ID whose tag schema to read')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get_tag_schema(ctx: click.Context, database_id: str, pipeline_id: str, template_id: str,
                   json_output: bool):
    """Get a template's tag schema."""
    api_client = _api(ctx)
    output_status(f"Retrieving tag schema for template '{template_id}'...", json_output)
    try:
        result = api_client.get_pipeline_template_tag_schema(database_id, pipeline_id, template_id)
        message = _message(result)

        def _fmt(_r):
            fields = message.get('fields', [])
            if not fields:
                return "No tag schema fields defined."
            out = [f"{len(fields)} tag field(s):"]
            for f in fields:
                out.append(f"  {f.get('tagKey', '?')} ({f.get('type', 'string')})"
                           + (" [required]" if f.get('required') else ""))
            return '\n'.join(out)

        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except PipelineTemplateNotFoundError as e:
        output_error(e, json_output, error_type="Template Not Found")
        raise click.ClickException(str(e))


@tag_schema_group.command('set')
@click.option('-d', '--database-id', required=True, help='Database ID containing the pipeline')
@click.option('-p', '--pipeline-id', required=True, help='Pipeline ID owning the template')
@click.option('-t', '--template-id', required=True, help='Template ID whose tag schema to set')
@click.option('--fields', help='Tag field definitions as inline JSON list')
@click.option('--fields-file', type=click.Path(exists=True), help='Tag field definitions from a JSON file')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def set_tag_schema(ctx: click.Context, database_id: str, pipeline_id: str, template_id: str,
                   fields: Optional[str], fields_file: Optional[str], json_output: bool):
    """Set (replace) a template's tag schema. Provide the fields list as JSON.

    The stored schema is replaced by the supplied list; pass an explicit empty list to clear it.

    Examples:
        vamscli pipeline tag-schema set -d my-db -p my-pipe -t my-template \\
            --fields '[{"tagKey": "quality", "type": "enum", "enumValues": ["low", "high"]}]'
        vamscli pipeline tag-schema set -d my-db -p my-pipe -t my-template --fields '[]'
    """
    api_client = _api(ctx)
    fields_obj = _load_json_option(fields, fields_file, "fields")
    if fields_obj is None:
        fields_obj = []
    if not isinstance(fields_obj, list):
        raise click.ClickException("Tag schema fields must be a JSON list of field definitions.")

    output_status(f"Setting tag schema for template '{template_id}'...", json_output)
    try:
        result = api_client.set_pipeline_template_tag_schema(
            database_id, pipeline_id, template_id, fields_obj)
        output_result(_message(result), json_output, success_message="✓ Tag schema set successfully!")
        return result
    except PipelineTemplateNotFoundError as e:
        output_error(e, json_output, error_type="Template Not Found")
        raise click.ClickException(str(e))
    except InvalidPipelineTemplateDataError as e:
        output_error(e, json_output, error_type="Invalid Tag Schema",
                     helpful_message="Each field needs a tagKey and a valid type "
                                     "(string/integer/number/boolean/string-list/enum).")
        raise click.ClickException(str(e))
