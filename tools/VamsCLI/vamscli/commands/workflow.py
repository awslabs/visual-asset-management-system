"""Workflow management commands for VamsCLI.

Covers the workflow CRUD surface, the fileUpload trigger sub-resources, the asset-less multi-file
execute, and the per-asset execution history list:

    workflow create / get / list / update / delete / unarchive
    workflow trigger list / get / set / delete
    workflow execute            asset-less, multi-file
    workflow list-executions    per-asset execution history

Workflows are database-scoped (PK databaseId, SK workflowId) and reference their pipelines by
composite id. Create is a POST to the database-scoped collection; delete is a soft archive, which
`unarchive` reverses.
Global execution operations (details, logs, abort, rerun, delete) live under `vamscli execution`.
"""

import json
from typing import Dict, Any, Optional, List

import click

from ..constants import MAX_WORKFLOW_EXECUTION_PAGE_SIZE
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_warning
from ..utils.exceptions import (
    WorkflowNotFoundError, WorkflowExecutionError, WorkflowAlreadyRunningError,
    InvalidWorkflowDataError, WorkflowTriggerNotFoundError, InvalidWorkflowTriggerDataError,
    AssetNotFoundError, DatabaseNotFoundError,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _api(ctx: click.Context) -> APIClient:
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    return APIClient(config['api_gateway_url'], profile_manager)


def _message(result: Dict[str, Any]) -> Any:
    return result.get('message', result) if isinstance(result, dict) else result


def _load_json_option(inline_value: Optional[str], file_path: Optional[str], label: str) -> Optional[Any]:
    """Resolve a JSON option supplied inline (--x '{...}') or from a file (--x-file path)."""
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


def format_workflow_output(workflow: Dict[str, Any]) -> str:
    """Format a single workflow for CLI display."""
    lines = [
        f"ID: {workflow.get('workflowId', 'N/A')}",
        f"Name: {workflow.get('workflowName', 'N/A')}",
        f"Database: {workflow.get('databaseId', 'N/A')}",
        f"Category: {workflow.get('category', 'N/A')}",
        f"Enabled: {workflow.get('enabled', 'N/A')}",
        f"Archived: {workflow.get('archived', False)}",
    ]
    description = workflow.get('description')
    if description:
        lines.append(f"Description: {description}")
    specified = workflow.get('specifiedPipelines') or []
    if specified:
        refs = ', '.join(
            f"{p.get('pipelineDatabaseId', '?')}:{p.get('pipelineId', '?')}" for p in specified)
        lines.append(f"Pipelines: {refs}")
    # executionCount is present on list responses (total executions for the workflow).
    execution_count = workflow.get('executionCount')
    if execution_count is not None:
        lines.append(f"Executions: {execution_count}")
    triggers = workflow.get('triggers')
    if triggers:
        lines.append(f"Triggers: {', '.join(t.get('triggerType', '?') for t in triggers)}")
    else:
        # List responses carry counts rather than the trigger rows. Both numbers are shown when they
        # differ: "2 (1 enabled)" is a workflow that will only partly fire, which reads very
        # differently from "2" — and is why a workflow can look configured but sit idle.
        trigger_count = workflow.get('triggerCount')
        if trigger_count is not None:
            enabled_count = workflow.get('triggersEnabledCount')
            if enabled_count is not None and enabled_count != trigger_count:
                lines.append(f"Triggers: {trigger_count} ({enabled_count} enabled)")
            else:
                lines.append(f"Triggers: {trigger_count}")
    workflow_arn = workflow.get('workflow_arn')
    if workflow_arn:
        lines.append(f"State Machine ARN: {workflow_arn}")
    warnings = workflow.get('warnings')
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in warnings)
    return '\n'.join(lines)


def _parse_specified_pipelines(pipelines_json: Optional[str], pipelines_file: Optional[str],
                               pipeline_refs: List[str]) -> List[Dict[str, Any]]:
    """Build the specifiedPipelines list from either a JSON option or repeated --pipeline refs.

    A --pipeline ref is 'databaseId:pipelineId[:defaultTemplateId[:jobName]]'. The trailing segments
    are positional, so a jobName without a default template is spelled with an empty third segment
    ('db:pipe::my-step'). Supplying the full list as JSON remains the way to set anything else on a
    reference."""
    parsed = _load_json_option(pipelines_json, pipelines_file, "specifiedPipelines")
    if parsed is not None:
        if not isinstance(parsed, list):
            raise click.ClickException("specifiedPipelines must be a JSON list.")
        return parsed
    result = []
    for ref in pipeline_refs:
        parts = ref.split(':')
        if len(parts) < 2 or len(parts) > 4 or not parts[0] or not parts[1]:
            raise click.ClickException(
                f"Invalid --pipeline ref '{ref}'. Use "
                "'databaseId:pipelineId[:defaultTemplateId[:jobName]]' (leave the template segment "
                "empty to set only a job name, e.g. 'db:pipe::my-step').")
        entry = {'pipelineDatabaseId': parts[0], 'pipelineId': parts[1]}
        if len(parts) >= 3 and parts[2]:
            entry['defaultTemplateId'] = parts[2]
        # jobName becomes a folder in this step's output path (and the ASL state name); omitted means
        # the pipeline id is used. Server-side it must satisfy the id charset, so tags are rejected.
        if len(parts) >= 4 and parts[3]:
            entry['jobName'] = parts[3]
        result.append(entry)
    return result


def _parse_metadata_source_assets(sources_json: Optional[str], sources_file: Optional[str],
                                  source_refs: List[str]) -> List[Dict[str, Any]]:
    """Build the metadataSourceAssets list from either a JSON option or repeated 2-segment refs.

    A ref is 'databaseId:assetId'. It carries no file key: a metadata source is an entity, not a file,
    which is what separates it from the 3-segment --input-file ref. Both segments are required, so a
    ref with a third segment is rejected rather than read as a file selection that is then dropped."""
    parsed = _load_json_option(sources_json, sources_file, "metadataSourceAssets")
    if parsed is not None:
        if not isinstance(parsed, list):
            raise click.ClickException("metadataSourceAssets must be a JSON list.")
        return parsed
    result = []
    for ref in source_refs:
        parts = ref.split(':')
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise click.ClickException(
                f"Invalid --metadata-source-asset ref '{ref}'. Use 'databaseId:assetId' — a metadata "
                "source is an asset, not a file, so it takes no file key.")
        result.append({'databaseId': parts[0], 'assetId': parts[1]})
    return result


@click.group()
def workflow():
    """Workflow management commands."""
    pass


# ---------------------------------------------------------------------------
# Workflow CRUD
# ---------------------------------------------------------------------------

@workflow.command('list')
@click.option('-d', '--database-id', help='Database ID to list workflows from (omit for all accessible workflows)')
@click.option('--include-archived', is_flag=True, help='Include archived workflows')
@click.option('--has-triggers', type=click.Choice(['true', 'false']),
              help='Only workflows that do (true) or do not (false) have an enabled trigger')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_workflows(ctx: click.Context, database_id: Optional[str], include_archived: bool,
                   has_triggers: Optional[str], page_size: Optional[int],
                   max_items: Optional[int], starting_token: Optional[str],
                   auto_paginate: bool, json_output: bool):
    """List workflows in a database, or all accessible workflows.

    Examples:
        vamscli workflow list
        vamscli workflow list -d my-database --auto-paginate
        vamscli workflow list --has-triggers true
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    if auto_paginate and starting_token:
        raise click.ClickException("Cannot use --auto-paginate with --starting-token.")
    if max_items and not auto_paginate:
        output_warning("--max-items only applies with --auto-paginate. Ignoring.", json_output)
        max_items = None

    def _fmt(data: Dict[str, Any]) -> str:
        items = data.get('Items', [])
        if not items:
            # The backend filters archived + unauthorized rows after the DynamoDB page limit, so an
            # empty page may still carry a NextToken for later pages that do contain matches.
            if not data.get('autoPaginated') and data.get('NextToken'):
                return ("No workflows on this page; more pages available."
                        f"\n\nNext token: {data['NextToken']}")
            return "No workflows found."
        out = []
        if data.get('autoPaginated'):
            out.append(f"Auto-paginated: {data.get('totalItems', 0)} item(s) in "
                       f"{data.get('pageCount', 0)} page(s)")
        out.append(f"Found {len(items)} workflow(s):")
        out.append("-" * 80)
        for wf in items:
            out.append(format_workflow_output(wf))
            out.append("-" * 80)
        if not data.get('autoPaginated') and data.get('NextToken'):
            out.append(f"\nNext token: {data['NextToken']}")
        return '\n'.join(out)

    try:
        if auto_paginate:
            max_total = max_items or 10000
            output_status(f"Listing workflows (auto-paginating up to {max_total})...", json_output)
            all_items = []
            next_token = None
            page_count = 0
            while True:
                page_count += 1
                params = {}
                if page_size:
                    params['pageSize'] = page_size
                if has_triggers:
                    params['hasTriggers'] = has_triggers
                if next_token:
                    params['startingToken'] = next_token
                page = _message(api_client.list_workflows(
                    database_id=database_id, include_archived=include_archived, params=params))
                items = page.get('Items', [])
                all_items.extend(items)
                if not json_output:
                    output_status(f"Fetched {len(all_items)} workflows (page {page_count})...", False)
                next_token = page.get('NextToken')
                if not next_token or len(all_items) >= max_total:
                    break
            result = {'Items': all_items, 'totalItems': len(all_items),
                      'autoPaginated': True, 'pageCount': page_count}
            output_result(_message(result), json_output, cli_formatter=_fmt)
            return result

        output_status("Listing workflows...", json_output)
        params = {}
        if page_size:
            params['pageSize'] = page_size
        if has_triggers:
            params['hasTriggers'] = has_triggers
        if starting_token:
            params['startingToken'] = starting_token
        result = _message(api_client.list_workflows(
            database_id=database_id, include_archived=include_archived, params=params))
        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except DatabaseNotFoundError as e:
        output_error(e, json_output, error_type="Database Not Found",
                     helpful_message="Use 'vamscli database list' to see available databases.")
        raise click.ClickException(str(e))


@workflow.command('get')
@click.option('-d', '--database-id', required=True, help='Database ID containing the workflow')
@click.option('-w', '--workflow-id', required=True, help='Workflow ID to retrieve')
@click.option('--include-archived', is_flag=True, help='Retrieve even if archived')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get_workflow(ctx: click.Context, database_id: str, workflow_id: str,
                 include_archived: bool, json_output: bool):
    """Get a workflow and its triggers."""
    api_client = _api(ctx)
    output_status(f"Retrieving workflow '{workflow_id}'...", json_output)
    try:
        result = api_client.get_workflow(database_id, workflow_id, include_archived=include_archived)
        output_result(_message(result), json_output,
                      cli_formatter=lambda _r: format_workflow_output(_message(result)))
        return result
    except WorkflowNotFoundError as e:
        output_error(e, json_output, error_type="Workflow Not Found",
                     helpful_message=f"Use 'vamscli workflow list -d {database_id}' to see workflows.")
        raise click.ClickException(str(e))


@workflow.command('create')
@click.option('-d', '--database-id', required=True, help='Database ID to create the workflow in (GLOBAL allowed)')
@click.option('-n', '--name', 'workflow_name', required=True, help='Human-readable workflow name')
@click.option('-w', '--workflow-id', help='Explicit workflow ID (a GUID is generated when omitted)')
@click.option('--pipeline', 'pipeline_refs', multiple=True,
              help="Referenced pipeline 'databaseId:pipelineId[:defaultTemplateId[:jobName]]' "
                   "(repeatable; use an empty template segment to set only a job name, "
                   "e.g. 'db:pipe::my-step')")
@click.option('--specified-pipelines', help='specifiedPipelines as inline JSON list (alternative to --pipeline)')
@click.option('--specified-pipelines-file', type=click.Path(exists=True),
              help='specifiedPipelines from a JSON file')
@click.option('--category', default='', help='Workflow category')
@click.option('--description', default='', help='Workflow description')
@click.option('--system-config', help='systemConfig as inline JSON')
@click.option('--system-config-file', type=click.Path(exists=True), help='systemConfig from a JSON file')
@click.option('--sub-dashboard-url', default='', help='Optional sub-dashboard URL')
@click.option('--disabled', is_flag=True, help='Create the workflow disabled')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create_workflow(ctx: click.Context, database_id: str, workflow_name: str,
                    workflow_id: Optional[str], pipeline_refs, specified_pipelines: Optional[str],
                    specified_pipelines_file: Optional[str], category: str, description: str,
                    system_config: Optional[str], system_config_file: Optional[str],
                    sub_dashboard_url: str, disabled: bool, json_output: bool):
    """Create a workflow referencing one or more pipelines.

    A --pipeline ref is 'databaseId:pipelineId[:defaultTemplateId[:jobName]]'. The segments are
    positional, so leave the template segment empty to set only a job name. A jobName becomes a
    folder in that step's output path; omit it to use the pipeline id.

    systemConfig.metadataInputs is a boolean map over assetMetadata, fileMetadata, fileAttributes,
    and databaseMetadata, each defaulting to true; it gates which metadata a run of this workflow
    captures. The workflow's gate builds the one metadata envelope every step shares.

    Examples:
        vamscli workflow create -d my-db -n "Convert + Label" \\
            --pipeline global:conversion-3d-basic:to-glb --pipeline my-db:my-labeler
        vamscli workflow create -d my-db -n "Convert for web" \\
            --pipeline global:conversion-3d-basic:to-glb:convert-for-web
        vamscli workflow create -d my-db -n "Label only" \\
            --pipeline global:metadata-3d-labeling::label-step
        vamscli workflow create -d my-db -n "WF" --specified-pipelines-file pipes.json
    """
    api_client = _api(ctx)
    specified = _parse_specified_pipelines(specified_pipelines, specified_pipelines_file, list(pipeline_refs))
    if not specified:
        raise click.ClickException(
            "Provide at least one pipeline via --pipeline or --specified-pipelines[-file].")
    sys_cfg = _load_json_option(system_config, system_config_file, "systemConfig")

    body: Dict[str, Any] = {
        'databaseId': database_id,
        'workflowName': workflow_name,
        'category': category,
        'description': description,
        'specifiedPipelines': specified,
        'subDashboardUrl': sub_dashboard_url,
    }
    if workflow_id:
        body['workflowId'] = workflow_id
    if sys_cfg is not None:
        body['systemConfig'] = sys_cfg
    if disabled:
        body['enabled'] = False

    output_status(f"Creating workflow '{workflow_name}'...", json_output)
    try:
        result = api_client.create_workflow(database_id, body)
        output_result(_message(result), json_output, success_message="✓ Workflow created successfully!",
                      cli_formatter=lambda _r: format_workflow_output(_message(result)))
        return result
    except InvalidWorkflowDataError as e:
        output_error(e, json_output, error_type="Invalid Workflow Data",
                     helpful_message="Check the referenced pipelines and systemConfig.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(e, json_output, error_type="Database Not Found")
        raise click.ClickException(str(e))


@workflow.command('update')
@click.option('-d', '--database-id', required=True, help='Database ID containing the workflow')
@click.option('-w', '--workflow-id', required=True, help='Workflow ID to update')
@click.option('-n', '--name', 'workflow_name', help='New workflow name')
@click.option('--pipeline', 'pipeline_refs', multiple=True,
              help="Replacement pipeline ref 'databaseId:pipelineId[:defaultTemplateId[:jobName]]' "
                   "(repeatable; use an empty template segment to set only a job name)")
@click.option('--specified-pipelines', help='Replacement specifiedPipelines as inline JSON list')
@click.option('--specified-pipelines-file', type=click.Path(exists=True),
              help='Replacement specifiedPipelines from a JSON file')
@click.option('--category', help='New category')
@click.option('--description', help='New description')
@click.option('--system-config', help='New systemConfig as inline JSON')
@click.option('--system-config-file', type=click.Path(exists=True), help='New systemConfig from a JSON file')
@click.option('--sub-dashboard-url', help='New sub-dashboard URL')
@click.option('--enable/--disable', 'enabled', default=None, help='Enable or disable the workflow')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update_workflow(ctx: click.Context, database_id: str, workflow_id: str,
                    workflow_name: Optional[str], pipeline_refs, specified_pipelines: Optional[str],
                    specified_pipelines_file: Optional[str], category: Optional[str],
                    description: Optional[str], system_config: Optional[str],
                    system_config_file: Optional[str], sub_dashboard_url: Optional[str],
                    enabled: Optional[bool], json_output: bool):
    """Update a workflow (only supplied fields change). At least one field is required.

    Changing the pipeline set redeploys the workflow's Step Functions state machine.
    """
    api_client = _api(ctx)
    sys_cfg = _load_json_option(system_config, system_config_file, "systemConfig")

    body: Dict[str, Any] = {}
    if workflow_name is not None:
        body['workflowName'] = workflow_name
    if pipeline_refs or specified_pipelines or specified_pipelines_file:
        body['specifiedPipelines'] = _parse_specified_pipelines(
            specified_pipelines, specified_pipelines_file, list(pipeline_refs))
    if category is not None:
        body['category'] = category
    if description is not None:
        body['description'] = description
    if sys_cfg is not None:
        body['systemConfig'] = sys_cfg
    if sub_dashboard_url is not None:
        body['subDashboardUrl'] = sub_dashboard_url
    if enabled is not None:
        body['enabled'] = enabled
    if not body:
        raise click.ClickException("Provide at least one field to update.")

    output_status(f"Updating workflow '{workflow_id}'...", json_output)
    try:
        result = api_client.update_workflow(database_id, workflow_id, body)
        output_result(_message(result), json_output, success_message="✓ Workflow updated successfully!",
                      cli_formatter=lambda _r: format_workflow_output(_message(result)))
        return result
    except WorkflowNotFoundError as e:
        output_error(e, json_output, error_type="Workflow Not Found")
        raise click.ClickException(str(e))
    except InvalidWorkflowDataError as e:
        output_error(e, json_output, error_type="Invalid Workflow Data")
        raise click.ClickException(str(e))


@workflow.command('delete')
@click.option('-d', '--database-id', required=True, help='Database ID containing the workflow')
@click.option('-w', '--workflow-id', required=True, help='Workflow ID to archive')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete_workflow(ctx: click.Context, database_id: str, workflow_id: str, json_output: bool):
    """Archive (soft-delete) a workflow."""
    api_client = _api(ctx)
    output_status(f"Archiving workflow '{workflow_id}'...", json_output)
    try:
        result = api_client.delete_workflow(database_id, workflow_id)
        output_result(_message(result), json_output, success_message="✓ Workflow archived.")
        return result
    except WorkflowNotFoundError as e:
        output_error(e, json_output, error_type="Workflow Not Found")
        raise click.ClickException(str(e))


@workflow.command('unarchive')
@click.option('-d', '--database-id', required=True, help='Database ID containing the workflow')
@click.option('-w', '--workflow-id', required=True, help='Archived workflow ID to unarchive')
@click.option('--keep-disabled', is_flag=True,
              help='Leave the workflow disabled after unarchiving (default re-enables it)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def unarchive_workflow(ctx: click.Context, database_id: str, workflow_id: str,
                       keep_disabled: bool, json_output: bool):
    """Unarchive an archived workflow.

    Clears the archived flag set by `workflow delete`, returning the workflow to the default
    listing. An archived workflow keeps its ID, so no other workflow can take that ID while it is
    archived.

    Archiving also disables the workflow, so unarchiving re-enables it to leave it executable.
    Pass --keep-disabled to unarchive without re-enabling.

    Examples:
        vamscli workflow unarchive -d my-db -w my-workflow
        vamscli workflow unarchive -d my-db -w my-workflow --keep-disabled
    """
    api_client = _api(ctx)
    body: Dict[str, Any] = {'archived': False}
    if not keep_disabled:
        body['enabled'] = True
    output_status(f"Unarchiving workflow '{workflow_id}'...", json_output)
    try:
        result = api_client.update_workflow(database_id, workflow_id, body)
        output_result(_message(result), json_output, success_message="✓ Workflow unarchived.",
                      cli_formatter=lambda _r: format_workflow_output(_message(result)))
        return result
    except WorkflowNotFoundError as e:
        output_error(e, json_output, error_type="Workflow Not Found",
                     helpful_message="Use 'vamscli workflow list -d <db> --include-archived' to see "
                                     "archived workflows.")
        raise click.ClickException(str(e))
    except InvalidWorkflowDataError as e:
        output_error(e, json_output, error_type="Invalid Workflow Data")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# Trigger sub-group
# ---------------------------------------------------------------------------

@workflow.group('trigger')
def trigger():
    """Workflow trigger management commands (currently fileUpload)."""
    pass


@trigger.command('list')
@click.option('-d', '--database-id', required=True, help='Database ID containing the workflow')
@click.option('-w', '--workflow-id', required=True, help='Workflow ID whose triggers to list')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_triggers(ctx: click.Context, database_id: str, workflow_id: str, json_output: bool):
    """List a workflow's triggers."""
    api_client = _api(ctx)
    output_status(f"Listing triggers for workflow '{workflow_id}'...", json_output)
    try:
        result = api_client.list_workflow_triggers(database_id, workflow_id)
        message = _message(result)

        def _fmt(_r):
            items = message.get('Items', [])
            if not items:
                return "No triggers found."
            out = [f"Found {len(items)} trigger(s):"]
            for t in items:
                out.append(f"  {t.get('triggerType', '?')}"
                           f" (enabled={t.get('enabled', '?')})")
            return '\n'.join(out)

        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except WorkflowNotFoundError as e:
        output_error(e, json_output, error_type="Workflow Not Found")
        raise click.ClickException(str(e))


@trigger.command('get')
@click.option('-d', '--database-id', required=True, help='Database ID containing the workflow')
@click.option('-w', '--workflow-id', required=True, help='Workflow ID owning the trigger')
@click.option('-t', '--trigger-type', default='fileUpload',
              help="Trigger key: the bare type (default: fileUpload) for the workflow's first "
                   "trigger of that type, or 'type#triggerId' for an additional one")
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get_trigger(ctx: click.Context, database_id: str, workflow_id: str, trigger_type: str,
                json_output: bool):
    """Get a workflow trigger."""
    api_client = _api(ctx)
    output_status(f"Retrieving trigger '{trigger_type}'...", json_output)
    try:
        result = api_client.get_workflow_trigger(database_id, workflow_id, trigger_type)
        output_result(_message(result), json_output)
        return result
    except WorkflowTriggerNotFoundError as e:
        output_error(e, json_output, error_type="Trigger Not Found")
        raise click.ClickException(str(e))


@trigger.command('set')
@click.option('-d', '--database-id', required=True, help='Database ID containing the workflow')
@click.option('-w', '--workflow-id', required=True, help='Workflow ID owning the trigger')
@click.option('-t', '--trigger-type', default='fileUpload',
              help="Trigger key: the bare type (default: fileUpload) for the workflow's first "
                   "trigger of that type, or 'type#triggerId' for an additional one")
@click.option('--input-file-filters', help='inputFileFilters as inline JSON (allow/exclude lists)')
@click.option('--input-file-filters-file', type=click.Path(exists=True),
              help='inputFileFilters from a JSON file')
@click.option('--default-template-ids', help='defaultTemplateIds map as inline JSON '
                                             '({"pipelineDatabaseId:pipelineId": "templateId"})')
@click.option('--default-template-ids-file', type=click.Path(exists=True),
              help='defaultTemplateIds from a JSON file')
@click.option('--enable/--disable', 'enabled', default=True, help='Enable or disable the trigger (default enabled)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def set_trigger(ctx: click.Context, database_id: str, workflow_id: str, trigger_type: str,
                input_file_filters: Optional[str], input_file_filters_file: Optional[str],
                default_template_ids: Optional[str], default_template_ids_file: Optional[str],
                enabled: bool, json_output: bool):
    """Set (create or replace) a workflow trigger.

    Examples:
        vamscli workflow trigger set -d my-db -w my-workflow \\
            --input-file-filters '{"allow": ["*.glb"], "exclude": []}' --enable
    """
    api_client = _api(ctx)
    filters = _load_json_option(input_file_filters, input_file_filters_file, "inputFileFilters")
    template_ids = _load_json_option(default_template_ids, default_template_ids_file, "defaultTemplateIds")

    body: Dict[str, Any] = {'enabled': enabled}
    if filters is not None:
        body['inputFileFilters'] = filters
    if template_ids is not None:
        body['defaultTemplateIds'] = template_ids

    output_status(f"Setting trigger '{trigger_type}'...", json_output)
    try:
        result = api_client.set_workflow_trigger(database_id, workflow_id, trigger_type, body)
        output_result(_message(result), json_output, success_message="✓ Trigger set successfully!")
        return result
    except WorkflowNotFoundError as e:
        output_error(e, json_output, error_type="Workflow Not Found")
        raise click.ClickException(str(e))
    except InvalidWorkflowTriggerDataError as e:
        output_error(e, json_output, error_type="Invalid Trigger Data",
                     helpful_message="Only the 'fileUpload' trigger type is currently supported.")
        raise click.ClickException(str(e))


@trigger.command('delete')
@click.option('-d', '--database-id', required=True, help='Database ID containing the workflow')
@click.option('-w', '--workflow-id', required=True, help='Workflow ID owning the trigger')
@click.option('-t', '--trigger-type', default='fileUpload',
              help="Trigger key: the bare type (default: fileUpload) for the workflow's first "
                   "trigger of that type, or 'type#triggerId' for an additional one")
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete_trigger(ctx: click.Context, database_id: str, workflow_id: str, trigger_type: str,
                   json_output: bool):
    """Delete a workflow trigger."""
    api_client = _api(ctx)
    output_status(f"Deleting trigger '{trigger_type}'...", json_output)
    try:
        result = api_client.delete_workflow_trigger(database_id, workflow_id, trigger_type)
        output_result(_message(result), json_output, success_message="✓ Trigger deleted.")
        return result
    except WorkflowTriggerNotFoundError as e:
        output_error(e, json_output, error_type="Trigger Not Found")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# Execute (asset-less, multi-file)
# ---------------------------------------------------------------------------

@workflow.command('execute')
@click.option('--workflow-database-id', required=True, help="Workflow's database ID (GLOBAL allowed)")
@click.option('-w', '--workflow-id', required=True, help='Workflow ID to execute')
@click.option('--input-file', 'input_file_refs', multiple=True,
              help="Input file 'databaseId:assetId:relativeFileKey' (repeatable). Everything after "
                   'the second colon is the file key, so a key containing a colon is kept intact; '
                   'pin a versionId via --input-files/--input-files-file')
@click.option('--input-files', help='inputFiles as inline JSON list (alternative to --input-file)')
@click.option('--input-files-file', type=click.Path(exists=True), help='inputFiles from a JSON file')
@click.option('--metadata-source-asset', 'metadata_source_asset_refs', multiple=True,
              help="Asset read purely as a metadata source, 'databaseId:assetId' (repeatable). Not an "
                   'input file, so it takes no file key and is exempt from arity and input filters')
@click.option('--metadata-source-assets',
              help='metadataSourceAssets as inline JSON list (alternative to --metadata-source-asset)')
@click.option('--metadata-source-assets-file', type=click.Path(exists=True),
              help='metadataSourceAssets from a JSON file')
@click.option('--metadata-source-database',
              help='Database whose own metadata is read as an input. Applies only to a run with NO '
                   "input files; a run with input files derives its databases from those files' "
                   'assets (plus any metadata-source assets). One concrete database ID — GLOBAL is '
                   'rejected. Database metadata is read-only and never an output target')
@click.option('--output-asset-id',
              help='Output asset ID. Honored whenever the inputs do not resolve to a single input '
                   'asset (regardless of override); for a single input asset only when the workflow '
                   'allows override. Omit for a results-only workflow (outputTarget.locationType "none")')
@click.option('--output-database-id',
              help='Output database ID. Supply with --output-asset-id when inputs resolve to zero or '
                   'multiple assets; falls back to the input asset\'s database on a single-asset override')
@click.option('--output-path-prefix',
              help='Optional base path (under the output asset) that output files are written beneath, '
                   "inserted just above each output file's own name. May contain dynamic tag "
                   'placeholders (e.g. {{firstAssetFileFileNameNoExt}}) resolved at launch. A trailing '
                   '"/" makes it a folder; without one it joins onto the file name. OMIT to inherit the '
                   "workflow's default prefix (systemConfig.defaultOutputFileBaseExecutionPathExtension); "
                   'pass an empty string to force the asset root. Must not contain ".." or backslashes')
@click.option('--pipeline-parameters', help='pipelineExecutionParameters as inline JSON ({"pipelineId": '
                                            '{"templateId": ..., "templateTags": [...], '
                                            '"customTemplateOverride": "..."}})')
@click.option('--pipeline-parameters-file', type=click.Path(exists=True),
              help='pipelineExecutionParameters from a JSON file')
@click.option('--execution-group-id', help='Group this execution under an executionGroupId')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def execute(ctx: click.Context, workflow_database_id: str, workflow_id: str, input_file_refs,
            input_files: Optional[str], input_files_file: Optional[str],
            metadata_source_asset_refs, metadata_source_assets: Optional[str],
            metadata_source_assets_file: Optional[str], metadata_source_database: Optional[str],
            output_asset_id: Optional[str], output_database_id: Optional[str],
            output_path_prefix: Optional[str],
            pipeline_parameters: Optional[str], pipeline_parameters_file: Optional[str],
            execution_group_id: Optional[str], json_output: bool):
    """Execute a workflow on a set of input files (asset-less, multi-file).

    Input files may span multiple assets. A relativeFileKey of '/' selects the whole asset;
    '/folder/' selects a folder. Per-pipeline template selection, tag values, and a one-off custom
    config body are supplied via --pipeline-parameters (keyed by pipelineId, with the keys
    templateId / templateTags / customTemplateOverride).

    Metadata sources: entities whose stored metadata is read as an input. Naming them is always
    optional and never enforced — a pipeline that genuinely needs metadata validates and fails on its
    own. --metadata-source-asset names an asset that is not an input file, so it plays no part in
    arity, input filters, or output-target resolution. --metadata-source-database applies only to a
    run with no input files; with input files the databases are derived from those files' assets (plus
    any metadata-source assets') instead. Database metadata is read-only — a database is never an
    output target. Metadata is capped per entity, and a capped capture comes back as a warning.

    Output target: when the inputs resolve to a single input asset the output is locked to that
    asset unless the workflow allows override; when they resolve to zero or multiple assets, supply
    both --output-asset-id and --output-database-id. Omit both for a results-only workflow
    (outputTarget.locationType "none"), which records only results text + logs and writes no asset.

    Output path prefix: omitting --output-path-prefix inherits the workflow's default prefix, which
    may carry template tags resolved per run (e.g. '/{{jobName}}/'). Pass an empty string to write at
    the asset root regardless of that default.

    Examples:
        vamscli workflow execute --workflow-database-id global -w my-workflow \\
            --input-file my-db:asset1:/model.glb
        vamscli workflow execute --workflow-database-id global -w my-workflow \\
            --metadata-source-database my-db --metadata-source-asset my-db:asset1
        vamscli workflow execute --workflow-database-id global -w my-workflow \\
            --input-files-file inputs.json --pipeline-parameters-file params.json
    """
    api_client = _api(ctx)

    parsed_inputs = _load_json_option(input_files, input_files_file, "inputFiles")
    if parsed_inputs is None:
        parsed_inputs = []
        for ref in input_file_refs:
            # Split on the first two colons only: a ':' is legal in an S3 key (ISO-timestamped and
            # bucket-sync-ingested names carry them routinely) and an S3 VersionId shares the file
            # key's charset, so a trailing segment cannot be told apart from part of the key. The key
            # wins here; a versionId is pinned through the named-field JSON options instead.
            parts = ref.split(':', 2)
            if len(parts) < 3 or not parts[0] or not parts[1] or not parts[2]:
                raise click.ClickException(
                    f"Invalid --input-file ref '{ref}'. Use "
                    "'databaseId:assetId:relativeFileKey' — the file key is everything after the "
                    "second colon. To pin a versionId, use --input-files/--input-files-file.")
            parsed_inputs.append(
                {'databaseId': parts[0], 'assetId': parts[1], 'relativeFileKey': parts[2]})
    elif not isinstance(parsed_inputs, list):
        raise click.ClickException("inputFiles must be a JSON list.")

    parsed_sources = _parse_metadata_source_assets(
        metadata_source_assets, metadata_source_assets_file, list(metadata_source_asset_refs))

    # GLOBAL is the unscoped/all-databases keyword rather than a database record, so there is no
    # entity whose metadata could be read. It IS accepted for --workflow-database-id and inside a
    # source asset's ref, which makes it an easy value to reach for here; rejected locally so the
    # reason is stated rather than arriving as a validation failure from the service.
    if metadata_source_database and metadata_source_database.strip().lower() == 'global':
        raise click.ClickException(
            "--metadata-source-database must name one concrete database; GLOBAL is the "
            "all-databases keyword, not a database whose metadata can be read.")

    pipeline_params = _load_json_option(
        pipeline_parameters, pipeline_parameters_file, "pipelineExecutionParameters")

    body: Dict[str, Any] = {'inputFiles': parsed_inputs}
    if parsed_sources:
        body['metadataSourceAssets'] = parsed_sources
    if metadata_source_database:
        body['metadataSourceDatabaseId'] = metadata_source_database
    if output_asset_id:
        body['outputAssetId'] = output_asset_id
    if output_database_id:
        body['outputDatabaseId'] = output_database_id
    # Sent whenever the flag was given at all, "" included: omitting it means "inherit the workflow's
    # default prefix", so dropping an explicit empty string would re-apply the very default the caller
    # asked to opt out of.
    if output_path_prefix is not None:
        body['outputFileBaseExecutionPathExtension'] = output_path_prefix
    if pipeline_params is not None:
        body['pipelineExecutionParameters'] = pipeline_params
    if execution_group_id:
        body['executionGroupId'] = execution_group_id

    output_status(f"Executing workflow '{workflow_id}'...", json_output)
    try:
        result = api_client.execute_workflow(workflow_database_id, workflow_id, body)
        message = _message(result)

        def _fmt(_r):
            execution_id = message.get('executionId', 'N/A') if isinstance(message, dict) else message
            out = [f"Execution ID: {execution_id}"]
            if isinstance(message, dict):
                if message.get('executionGroupId'):
                    out.append(f"Group: {message['executionGroupId']}")
                if message.get('warnings'):
                    out.append("Warnings:")
                    out.extend(f"  - {w}" for w in message['warnings'])
            out.append("Use 'vamscli execution details <id>' to check status.")
            return '\n'.join(out)

        output_result(_message(result), json_output, success_message="✓ Workflow execution started successfully!",
                      cli_formatter=_fmt)
        return result
    except WorkflowNotFoundError as e:
        output_error(e, json_output, error_type="Workflow Not Found",
                     helpful_message=f"Use 'vamscli workflow list -d {workflow_database_id}' to see workflows.")
        raise click.ClickException(str(e))
    except WorkflowAlreadyRunningError as e:
        output_error(e, json_output, error_type="Conflicting Execution Running",
                     helpful_message="Use 'vamscli execution list -w " + workflow_id + "' to check status.")
        raise click.ClickException(str(e))
    except WorkflowExecutionError as e:
        output_error(e, json_output, error_type="Workflow Execution Error",
                     helpful_message="Ensure the workflow is enabled, its pipelines are accessible, "
                                     "and the input files exist.")
        raise click.ClickException(str(e))


# ---------------------------------------------------------------------------
# Per-asset execution history list
# ---------------------------------------------------------------------------

@workflow.command('list-executions')
@click.option('-d', '--database-id', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset-id', required=True, help='Asset ID to list executions for')
@click.option('-w', '--workflow-id', help='Filter by specific workflow ID')
@click.option('--workflow-database-id', help="Workflow's database ID (for filtering)")
@click.option('--filter-start-date',
              help='Only executions started on/after this UTC date-time, as '
                   'YYYY-MM-DDTHH:MM:SSZ (default: 90 days ago). Widen it to reach older history.')
@click.option('--filter-end-date',
              help='Only executions started on/before this UTC date-time, as '
                   'YYYY-MM-DDTHH:MM:SSZ (optional upper bound).')
@click.option('--page-size', type=int, help='Number of items per page (max 50 due to API throttling)')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_executions(ctx: click.Context, database_id: str, asset_id: str, workflow_id: Optional[str],
                    workflow_database_id: Optional[str], filter_start_date: Optional[str],
                    filter_end_date: Optional[str], page_size: Optional[int],
                    max_items: Optional[int], starting_token: Optional[str], auto_paginate: bool,
                    json_output: bool):
    """List an asset's workflow executions (per-asset history).

    Only recent executions (started within the last 90 days) are listed by default; use
    --filter-start-date / --filter-end-date to query an explicit date range. The window actually
    applied is reported alongside the results.

    For the global, cross-asset execution list with rich filters, use 'vamscli execution list'.

    Examples:
        vamscli workflow list-executions -d my-db -a my-asset
        vamscli workflow list-executions -d my-db -a my-asset --filter-start-date 2025-01-01T00:00:00Z
        vamscli workflow list-executions -d my-db -a my-asset -w workflow-123 --auto-paginate
    """
    api_client = _api(ctx)
    if auto_paginate and starting_token:
        raise click.ClickException("Cannot use --auto-paginate with --starting-token.")
    if max_items and not auto_paginate:
        output_warning("--max-items only applies with --auto-paginate. Ignoring.", json_output)
        max_items = None
    if page_size and page_size > MAX_WORKFLOW_EXECUTION_PAGE_SIZE:
        raise click.ClickException(
            f"Maximum page size for workflow executions is {MAX_WORKFLOW_EXECUTION_PAGE_SIZE} "
            "due to API throttling. Use --auto-paginate to fetch more across pages.")
    if not page_size:
        page_size = MAX_WORKFLOW_EXECUTION_PAGE_SIZE

    def _base_params() -> Dict[str, Any]:
        params: Dict[str, Any] = {'pageSize': page_size}
        if filter_start_date:
            params['filterStartDate'] = filter_start_date
        if filter_end_date:
            params['filterEndDate'] = filter_end_date
        return params

    def _window(data: Dict[str, Any]) -> str:
        """The date window the service reports applying, which is the 90-day default unless the
        caller set one — the reason an asset with older history can list as empty."""
        applied = data.get('filterStartDate')
        if not applied:
            return ""
        end = data.get('filterEndDate')
        return f"Window: {applied} to {end}" if end else f"Window: from {applied}"

    def _fmt(data: Dict[str, Any]) -> str:
        items = data.get('Items', [])
        window = _window(data)
        if not items:
            # The backend applies authorization and filters after the candidate cap, so an empty
            # page may still carry a NextToken for later pages that do contain matches.
            if not data.get('autoPaginated') and data.get('NextToken'):
                return ("No workflow executions on this page; more pages available."
                        f"\n\nNext token: {data['NextToken']}")
            empty = "No workflow executions found."
            return f"{empty}\n{window}" if window else empty
        out = []
        if window:
            out.append(window)
        if data.get('autoPaginated'):
            out.append(f"Auto-paginated: {data.get('totalItems', 0)} item(s) in "
                       f"{data.get('pageCount', 0)} page(s)")
        out.append(f"Found {len(items)} execution(s):")
        out.append("-" * 80)
        for ex in items:
            out.append(f"Execution ID: {ex.get('workflowExecutionId', 'N/A')}")
            out.append(f"  Workflow: {ex.get('workflowDatabaseId', 'N/A')}:{ex.get('workflowId', 'N/A')}")
            out.append(f"  Status: {ex.get('executionStatus', 'N/A')}")
            out.append(f"  Started: {ex.get('startDate', 'N/A')}")
            if ex.get('inputAssetFileKey'):
                out.append(f"  Input File: {ex['inputAssetFileKey']}")
            out.append("-" * 80)
        if not data.get('autoPaginated') and data.get('NextToken'):
            out.append(f"\nNext token: {data['NextToken']}")
        if data.get('note'):
            out.append(f"\n{data['note']}")
        return '\n'.join(out)

    try:
        if auto_paginate:
            max_total = max_items or 10000
            output_status(f"Listing executions for asset '{asset_id}' "
                          f"(auto-paginating up to {max_total})...", json_output)
            all_items = []
            next_token = None
            page_count = 0
            applied_window: Dict[str, Any] = {}
            while True:
                page_count += 1
                params = _base_params()
                if next_token:
                    params['startingToken'] = next_token
                page = _message(api_client.list_workflow_executions(
                    database_id=database_id, asset_id=asset_id,
                    workflow_database_id=workflow_database_id, workflow_id=workflow_id, params=params))
                items = page.get('Items', [])
                all_items.extend(items)
                for key in ('filterStartDate', 'filterEndDate'):
                    if page.get(key):
                        applied_window[key] = page[key]
                if not json_output:
                    output_status(f"Fetched {len(all_items)} executions (page {page_count})...", False)
                next_token = page.get('NextToken')
                if not next_token or len(all_items) >= max_total:
                    break
            result = {'Items': all_items, 'totalItems': len(all_items),
                      'autoPaginated': True, 'pageCount': page_count, **applied_window}
            if next_token and len(all_items) >= max_total:
                # The outstanding token is the only way to resume; a bare "more may be available"
                # would force the caller to re-walk every page already paid for.
                result['NextToken'] = next_token
                result['note'] = (
                    f"Reached maximum of {max_total} items. More may be available — resume with "
                    f"--starting-token {next_token}")
            output_result(_message(result), json_output, cli_formatter=_fmt)
            return result

        output_status(f"Listing executions for asset '{asset_id}'...", json_output)
        params = _base_params()
        if starting_token:
            params['startingToken'] = starting_token
        result = _message(api_client.list_workflow_executions(
            database_id=database_id, asset_id=asset_id, workflow_database_id=workflow_database_id,
            workflow_id=workflow_id, params=params))
        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except AssetNotFoundError as e:
        output_error(e, json_output, error_type="Asset Not Found",
                     helpful_message=f"Use 'vamscli assets get -d {database_id} {asset_id}' to check the asset.")
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(e, json_output, error_type="Database Not Found")
        raise click.ClickException(str(e))
