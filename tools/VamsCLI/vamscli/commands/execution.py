"""Workflow execution operations for VamsCLI.

Execution-keyed operations that span the whole workflow-execution lifecycle (executions may span
files across multiple assets, so these are keyed on the executionId, not an asset):

    execution list          global, permission-filtered list with filters + pagination
    execution details       full detail / traceability view
    execution logs          truncated stored log, or full live CloudWatch search
    execution abort         abort one execution, or an entire group via --group-id
    execution rerun         reconstruct + relaunch from stored records
    execution permanent-delete   remove the execution's DynamoDB rows only (admin, guarded)
"""

from typing import Dict, Any, Optional

import click

from ..constants import MAX_WORKFLOW_EXECUTION_PAGE_SIZE
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_warning
from ..utils.exceptions import (
    ExecutionNotFoundError, ExecutionInProgressError, InvalidExecutionDataError,
)


def _api(ctx: click.Context) -> APIClient:
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    return APIClient(config['api_gateway_url'], profile_manager)


def _message(result: Dict[str, Any]) -> Any:
    return result.get('message', result) if isinstance(result, dict) else result


@click.group()
def execution():
    """Workflow execution operations (list, details, logs, abort, rerun, delete)."""
    pass


@execution.command('list')
@click.option('-w', '--workflow-id', help='Filter by workflow ID')
@click.option('--workflow-database-id', help='Filter by workflow database ID')
@click.option('--status', help='Filter by execution status (e.g. RUNNING, SUCCEEDED, FAILED)')
@click.option('--trigger-type', help='Filter by trigger type (Manual / File-Upload)')
@click.option('--group-id', help='Filter by executionGroupId')
@click.option('--triggered-by', help='Filter by the user ID that triggered the execution')
@click.option('--page-size', type=int, help='Number of items per page (max 100)')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_executions(ctx: click.Context, workflow_id: Optional[str], workflow_database_id: Optional[str],
                    status: Optional[str], trigger_type: Optional[str], group_id: Optional[str],
                    triggered_by: Optional[str], page_size: Optional[int], max_items: Optional[int],
                    starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """List workflow executions globally (permission-filtered), with optional filters.

    Only executions whose workflow you can read AND whose input/output asset you can read are shown.

    Examples:
        vamscli execution list
        vamscli execution list -w my-workflow --status RUNNING
        vamscli execution list --group-id grp-123 --auto-paginate
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)

    if auto_paginate and starting_token:
        raise click.ClickException(
            "Cannot use --auto-paginate with --starting-token.")
    if max_items and not auto_paginate:
        output_warning("--max-items only applies with --auto-paginate. Ignoring.", json_output)
        max_items = None

    def _base_params() -> Dict[str, Any]:
        params: Dict[str, Any] = {}
        if workflow_id:
            params['workflowId'] = workflow_id
        if workflow_database_id:
            params['workflowDatabaseId'] = workflow_database_id
        if status:
            params['status'] = status
        if trigger_type:
            params['triggerType'] = trigger_type
        if group_id:
            params['groupId'] = group_id
        if triggered_by:
            params['triggeredByUserId'] = triggered_by
        if page_size:
            params['pageSize'] = page_size
        return params

    def _fmt(data: Dict[str, Any]) -> str:
        items = data.get('Items', [])
        if not items:
            return "No executions found."
        out = []
        if data.get('autoPaginated'):
            out.append(f"Auto-paginated: {data.get('totalItems', 0)} item(s) in "
                       f"{data.get('pageCount', 0)} page(s)")
        out.append(f"Found {len(items)} execution(s):")
        out.append("-" * 80)
        for ex in items:
            out.append(f"Execution ID: {ex.get('workflowExecutionId', 'N/A')}")
            out.append(f"  Workflow: {ex.get('workflowDatabaseId', 'N/A')}:{ex.get('workflowId', 'N/A')}")
            out.append(f"  Status: {ex.get('executionStatus', 'N/A')}")
            out.append(f"  Started: {ex.get('executionStartDate', 'N/A')}")
            if ex.get('executionGroupId'):
                out.append(f"  Group: {ex['executionGroupId']}")
            out.append("-" * 80)
        if not data.get('autoPaginated') and data.get('NextToken'):
            out.append(f"\nNext token: {data['NextToken']}")
        return '\n'.join(out)

    try:
        if auto_paginate:
            max_total = max_items or 10000
            output_status(f"Listing executions (auto-paginating up to {max_total})...", json_output)
            all_items = []
            next_token = None
            page_count = 0
            while True:
                page_count += 1
                params = _base_params()
                if next_token:
                    params['startingToken'] = next_token
                page = _message(api_client.list_executions(params=params))
                items = page.get('Items', [])
                all_items.extend(items)
                if not json_output:
                    output_status(f"Fetched {len(all_items)} executions (page {page_count})...", False)
                next_token = page.get('NextToken')
                if not next_token or len(all_items) >= max_total:
                    break
            result = {'Items': all_items, 'totalItems': len(all_items),
                      'autoPaginated': True, 'pageCount': page_count}
            if next_token and len(all_items) >= max_total:
                result['note'] = f"Reached maximum of {max_total} items. More may be available."
            output_result(_message(result), json_output, cli_formatter=_fmt)
            return result

        output_status("Listing executions...", json_output)
        params = _base_params()
        if starting_token:
            params['startingToken'] = starting_token
        result = _message(api_client.list_executions(params=params))
        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except InvalidExecutionDataError as e:
        output_error(e, json_output, error_type="Invalid Filter")
        raise click.ClickException(str(e))


@execution.command('details')
@click.argument('execution_id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def details(ctx: click.Context, execution_id: str, json_output: bool):
    """Get an execution's full detail and traceability (pipelines, inputs, outputs, metadata).

    Examples:
        vamscli execution details my-execution-id
    """
    api_client = _api(ctx)
    output_status(f"Retrieving details for execution '{execution_id}'...", json_output)
    try:
        result = api_client.get_execution_details(execution_id)
        message = _message(result)

        def _fmt(_r):
            out = [
                f"Execution ID: {message.get('workflowExecutionId', 'N/A')}",
                f"Workflow: {message.get('workflowDatabaseId', 'N/A')}:{message.get('workflowId', 'N/A')}",
                f"Status: {message.get('executionStatus', 'N/A')}",
                f"Started: {message.get('executionStartDate', 'N/A')}",
                f"Stopped: {message.get('executionStopDate', 'N/A')}",
                f"Trigger: {message.get('triggerType', 'N/A')} (by {message.get('triggeredByUserId', 'N/A')})",
            ]
            if message.get('executionError'):
                out.append(f"Error: {message['executionError']}")
            pipelines = message.get('pipelines', [])
            if pipelines:
                out.append(f"\nPipelines ({len(pipelines)}):")
                for p in pipelines:
                    out.append(f"  {p.get('name', p.get('pipelineId', '?'))}"
                               f" [{p.get('executionStatus', 'N/A')}]")
            inputs = message.get('inputFiles', [])
            if inputs:
                out.append(f"\nInput files ({len(inputs)}):")
                for f in inputs:
                    # versionId is the concrete S3 version read (resolved at launch); shown when present.
                    version = f.get('versionId', '')
                    version_suffix = f"  (v {version})" if version else ""
                    out.append(f"  {f.get('databaseId', '?')}:{f.get('assetId', '?')}"
                               f"{f.get('inputAssetFileKey', '')}{version_suffix}")
            outputs = message.get('outputs', {})
            files = outputs.get('files', []) if isinstance(outputs, dict) else []
            if files:
                out.append(f"\nOutput files ({len(files)}):")
                for f in files:
                    out.append(f"  {f.get('relativeFilePath', '?')}")
            if message.get('truncatedCollections'):
                out.append(f"\n(Truncated collections: {', '.join(message['truncatedCollections'])})")
            return '\n'.join(out)

        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except ExecutionNotFoundError as e:
        output_error(e, json_output, error_type="Execution Not Found",
                     helpful_message="Use 'vamscli execution list' to see available executions.")
        raise click.ClickException(str(e))


@execution.command('logs')
@click.argument('execution_id')
@click.option('--mode', type=click.Choice(['truncated', 'full']), default='truncated',
              help='truncated = stored log text; full = live CloudWatch search')
@click.option('--pipeline-execution-id', help='Scope logs to a single pipeline execution')
@click.option('--filter-pattern', help='(full mode) additional CloudWatch filter pattern')
@click.option('--limit', type=int, help='(full mode) max events to return (capped at 1000)')
@click.option('--start-time', type=int, help='(full mode) start time, epoch milliseconds')
@click.option('--end-time', type=int, help='(full mode) end time, epoch milliseconds')
@click.option('--next-token', help='(full mode) CloudWatch pagination token')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def logs(ctx: click.Context, execution_id: str, mode: str, pipeline_execution_id: Optional[str],
         filter_pattern: Optional[str], limit: Optional[int], start_time: Optional[int],
         end_time: Optional[int], next_token: Optional[str], json_output: bool):
    """Retrieve an execution's logs (truncated stored text or full live CloudWatch search).

    Examples:
        vamscli execution logs my-execution-id
        vamscli execution logs my-execution-id --mode full --limit 200
    """
    api_client = _api(ctx)
    params: Dict[str, Any] = {'mode': mode}
    if pipeline_execution_id:
        params['pipelineExecutionId'] = pipeline_execution_id
    if mode == 'full':
        if filter_pattern:
            params['filterPattern'] = filter_pattern
        if limit:
            params['limit'] = limit
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time
        if next_token:
            params['nextToken'] = next_token

    output_status(f"Retrieving {mode} logs for execution '{execution_id}'...", json_output)
    try:
        result = api_client.get_execution_logs(execution_id, params=params)
        message = _message(result)

        def _fmt(_r):
            out = [f"Mode: {message.get('mode', mode)}"]
            # In truncated mode the backend serves stored log text, or falls back to a live
            # CloudWatch search when the stored copy is empty; logsSource reports which.
            if message.get('logsSource'):
                out.append(f"Source: {message['logsSource']}")
            if message.get('pipelineExecutionId'):
                out.append(f"Pipeline Execution: {message['pipelineExecutionId']}")
            for key, label in (('executionLog', 'Execution Log'), ('executionError', 'Execution Error'),
                               ('resultLog', 'Result Log'), ('errorLog', 'Error Log')):
                if message.get(key):
                    out.append(f"\n== {label} ==\n{message[key]}")
            events = message.get('events')
            if events:
                out.append(f"\n== Events ({len(events)}) ==")
                for ev in events:
                    out.append(f"  [{ev.get('timestamp', '')}] {ev.get('message', '')}".rstrip())
                if message.get('nextToken'):
                    out.append(f"\nNext token: {message['nextToken']}")
            return '\n'.join(out) if len(out) > 1 else "No log content."

        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except ExecutionNotFoundError as e:
        output_error(e, json_output, error_type="Execution Not Found")
        raise click.ClickException(str(e))
    except InvalidExecutionDataError as e:
        output_error(e, json_output, error_type="Invalid Log Request")
        raise click.ClickException(str(e))


@execution.command('abort')
@click.argument('execution_id', required=False)
@click.option('--group-id', help='Abort every active execution in this execution group')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def abort(ctx: click.Context, execution_id: Optional[str], group_id: Optional[str], json_output: bool):
    """Abort a running execution, or an entire execution group with --group-id.

    Provide either an EXECUTION_ID (abort one) or --group-id (abort the group). When aborting a
    group, pass any member execution id as EXECUTION_ID (the id in the path is ignored for grouping).

    Examples:
        vamscli execution abort my-execution-id
        vamscli execution abort my-execution-id --group-id grp-123
    """
    api_client = _api(ctx)
    if not execution_id and not group_id:
        raise click.ClickException("Provide an EXECUTION_ID or --group-id to abort.")
    # The abort-by-group route is still keyed on an executionId path param; require one.
    if group_id and not execution_id:
        raise click.ClickException(
            "Provide a member EXECUTION_ID along with --group-id (the group abort route is keyed on "
            "an execution id).")

    target = f"group '{group_id}'" if group_id else f"execution '{execution_id}'"
    output_status(f"Aborting {target}...", json_output)
    try:
        result = api_client.abort_execution(execution_id, group_id=group_id)
        message = _message(result)

        def _fmt(_r):
            if group_id and isinstance(message, dict):
                results = message.get('results', [])
                out = [f"Group: {message.get('groupId', group_id)}",
                       f"Processed {len(results)} member(s):"]
                for r in results:
                    out.append(f"  {r.get('executionId', '?')}: {r.get('status', '?')}")
                if message.get('skippedInaccessibleCount'):
                    out.append(f"Skipped (inaccessible): {message['skippedInaccessibleCount']}")
                if message.get('moreRemaining'):
                    out.append("More executions remain; run again to continue aborting the group.")
                return '\n'.join(out)
            return str(message)

        output_result(_message(result), json_output, success_message="✓ Abort request submitted.",
                      cli_formatter=_fmt)
        return result
    except ExecutionNotFoundError as e:
        output_error(e, json_output, error_type="Execution Not Found")
        raise click.ClickException(str(e))


@execution.command('rerun')
@click.argument('execution_id')
@click.option('--execution-group-id', help='Reuse or assign an executionGroupId for the re-run')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def rerun(ctx: click.Context, execution_id: str, execution_group_id: Optional[str], json_output: bool):
    """Re-run an execution (reconstructed from its stored records; launches a new execution).

    Examples:
        vamscli execution rerun my-execution-id
        vamscli execution rerun my-execution-id --execution-group-id grp-123
    """
    api_client = _api(ctx)
    output_status(f"Re-running execution '{execution_id}'...", json_output)
    try:
        result = api_client.rerun_execution(execution_id, execution_group_id=execution_group_id)
        message = _message(result)

        def _fmt(_r):
            new_id = message.get('executionId', 'N/A') if isinstance(message, dict) else message
            out = [f"New Execution ID: {new_id}"]
            if isinstance(message, dict) and message.get('executionGroupId'):
                out.append(f"Group: {message['executionGroupId']}")
            out.append("Use 'vamscli execution details <id>' to check status.")
            return '\n'.join(out)

        output_result(_message(result), json_output, success_message="✓ Re-run started successfully!",
                      cli_formatter=_fmt)
        return result
    except ExecutionNotFoundError as e:
        output_error(e, json_output, error_type="Execution Not Found")
        raise click.ClickException(str(e))
    except InvalidExecutionDataError as e:
        output_error(e, json_output, error_type="Re-run Unavailable")
        raise click.ClickException(str(e))


@execution.command('permanent-delete')
@click.argument('execution_id')
@click.option('--yes', is_flag=True, help='Skip the interactive confirmation prompt')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def permanent_delete(ctx: click.Context, execution_id: str, yes: bool, json_output: bool):
    """Permanently delete an execution's DynamoDB records (admin; does not touch Step Functions history).

    The execution must not be in progress. This is irreversible.

    Examples:
        vamscli execution permanent-delete my-execution-id --yes
    """
    api_client = _api(ctx)
    if not yes and not json_output:
        click.confirm(
            f"Permanently delete all DynamoDB records for execution '{execution_id}'? "
            "This is irreversible.", abort=True)

    output_status(f"Permanently deleting execution '{execution_id}'...", json_output)
    try:
        result = api_client.permanent_delete_execution(execution_id)
        output_result(_message(result), json_output, success_message="✓ Execution records permanently deleted.")
        return result
    except ExecutionNotFoundError as e:
        output_error(e, json_output, error_type="Execution Not Found")
        raise click.ClickException(str(e))
    except ExecutionInProgressError as e:
        output_error(e, json_output, error_type="Execution In Progress",
                     helpful_message="Abort the execution first, then permanent-delete it.")
        raise click.ClickException(str(e))
    except InvalidExecutionDataError as e:
        output_error(e, json_output, error_type="Invalid Request")
        raise click.ClickException(str(e))
