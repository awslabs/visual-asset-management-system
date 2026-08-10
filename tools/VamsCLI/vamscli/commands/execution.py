"""Workflow execution operations for VamsCLI.

Execution-keyed operations that span the whole workflow-execution lifecycle (executions may span
files across multiple assets, so these are keyed on the executionId, not an asset):

    execution list          global, permission-filtered list with filters + pagination
    execution details       full detail / traceability view
    execution details-metadata   one metadata collection of the detail view, a page at a time
    execution logs          truncated stored log, or full live CloudWatch search
    execution abort         abort one execution, or an entire group via --group-id
    execution rerun         reconstruct + relaunch from stored records
    execution permanent-delete   remove the execution's DynamoDB rows only (admin, guarded)
"""

from typing import Dict, Any, Optional

import click

from ..constants import (
    EXECUTION_DETAIL_METADATA_COLLECTIONS, MAX_EXECUTION_AUTO_PAGINATE_PAGES,
    MAX_EXECUTION_DETAIL_METADATA_PAGE_SIZE, MAX_GLOBAL_EXECUTION_PAGE_SIZE,
)
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


def _warnings(result: Dict[str, Any]) -> Any:
    """The top-level `warnings` array a save/abort returns as a sibling of `message`."""
    return result.get('warnings') if isinstance(result, dict) else None


def _payload_with_warnings(result: Dict[str, Any]) -> Any:
    """Unwrap the `message` envelope, carrying any top-level `warnings` into the payload."""
    message = _message(result)
    warnings = _warnings(result)
    if not warnings:
        return message
    if isinstance(message, dict):
        payload = dict(message)
    else:
        payload = {'message': message}
    payload['warnings'] = warnings
    return payload


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
@click.option('--filter-start-date',
              help='Only executions started on/after this UTC date-time, as '
                   'YYYY-MM-DDTHH:MM:SSZ (default: 90 days ago). The list shows recent '
                   'executions by default.')
@click.option('--filter-end-date',
              help='Only executions started on/before this UTC date-time, as '
                   'YYYY-MM-DDTHH:MM:SSZ (optional upper bound).')
@click.option('--page-size', type=int,
              help=f'Number of items per page (max {MAX_GLOBAL_EXECUTION_PAGE_SIZE})')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_executions(ctx: click.Context, workflow_id: Optional[str], workflow_database_id: Optional[str],
                    status: Optional[str], trigger_type: Optional[str], group_id: Optional[str],
                    triggered_by: Optional[str], filter_start_date: Optional[str],
                    filter_end_date: Optional[str], page_size: Optional[int], max_items: Optional[int],
                    starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """List workflow executions globally (permission-filtered), with optional filters.

    Only executions whose workflow you can read AND whose input/output asset you can read are shown.
    By default only recent executions (started within the last 90 days) are listed; use
    --filter-start-date / --filter-end-date to query an explicit date range.

    Examples:
        vamscli execution list
        vamscli execution list -w my-workflow --status RUNNING
        vamscli execution list --filter-start-date 2026-01-01T00:00:00Z --filter-end-date 2026-02-01T00:00:00Z
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
    if page_size and page_size > MAX_GLOBAL_EXECUTION_PAGE_SIZE:
        output_warning(
            f"--page-size above {MAX_GLOBAL_EXECUTION_PAGE_SIZE} is clamped by the service. "
            f"Using {MAX_GLOBAL_EXECUTION_PAGE_SIZE}.", json_output)
        page_size = MAX_GLOBAL_EXECUTION_PAGE_SIZE

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
        if filter_start_date:
            params['filterStartDate'] = filter_start_date
        if filter_end_date:
            params['filterEndDate'] = filter_end_date
        if page_size:
            params['pageSize'] = page_size
        return params

    def _fmt(data: Dict[str, Any]) -> str:
        items = data.get('Items', [])
        if not items:
            # The backend applies filters after the DynamoDB page limit, so an empty page may still
            # carry a NextToken for later pages that do contain matches.
            if not data.get('autoPaginated') and data.get('NextToken'):
                return ("No executions on this page; more pages available."
                        f"\n\nNext token: {data['NextToken']}")
            if data.get('note'):
                return f"No executions found.\n\n{data['note']}"
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
            # Output target of the run. Omitted for a results-only execution, which writes no files
            # and therefore has no destination asset.
            if ex.get('outputLocationType'):
                out.append(f"  Output Type: {ex['outputLocationType']}")
            if ex.get('outputAssetId') or ex.get('outputDatabaseId'):
                out.append(f"  Output Asset: {ex.get('outputDatabaseId', 'N/A')}:"
                           f"{ex.get('outputAssetId', 'N/A')}")
            out.append("-" * 80)
        if not data.get('autoPaginated') and data.get('NextToken'):
            out.append(f"\nNext token: {data['NextToken']}")
        if data.get('note'):
            out.append(f"\n{data['note']}")
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
                if (not next_token or len(all_items) >= max_total
                        or page_count >= MAX_EXECUTION_AUTO_PAGINATE_PAGES):
                    break
            result = {'Items': all_items, 'totalItems': len(all_items),
                      'autoPaginated': True, 'pageCount': page_count}
            # Both stop conditions carry the outstanding token: it is the only way to continue, and
            # without it a caller chunking a large deployment has to re-walk every page already paid
            # for (each of which re-pays the per-page authorization fan-out).
            if next_token and len(all_items) >= max_total:
                result['NextToken'] = next_token
                result['note'] = (
                    f"Reached maximum of {max_total} items. More may be available — resume with "
                    f"--starting-token {next_token}")
            elif next_token and page_count >= MAX_EXECUTION_AUTO_PAGINATE_PAGES:
                result['NextToken'] = next_token
                result['note'] = (
                    f"Stopped after {page_count} pages. More may be available — narrow the filters, "
                    f"or resume with --starting-token {next_token}")
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

    Asset/file metadata (inputMetadata) and database metadata (inputDatabaseMetadata) are separate
    collections, and each is reported as a row count here — use --json-output for the rows themselves.
    The metadata sources the run read from are listed alongside them.

    Large collections are bounded server-side. Any section that came back partial is named in
    truncatedCollections and marked in the output; a pipeline whose configuration body was truncated
    reports the Amazon S3 location of the full body. A truncated metadata collection can be read in
    full with 'vamscli execution details-metadata'.

    Examples:
        vamscli execution details my-execution-id
    """
    api_client = _api(ctx)
    output_status(f"Retrieving details for execution '{execution_id}'...", json_output)
    try:
        result = api_client.get_execution_details(execution_id)
        message = _message(result)

        def _fmt(_r):
            # Collections the server reports as partial. A section rendered below carries the marker in
            # its own header so a shortened list is never read as the complete set.
            truncated = set(message.get('truncatedCollections') or [])

            def _mark(collection_name):
                return " [PARTIAL - more rows exist]" if collection_name in truncated else ""

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
                    # The inline body is pre-system-tag; the S3 object is the FULLY substituted body
                    # the pipeline read, and is present whether or not the inline copy was trimmed.
                    location = p.get('renderedConfigLocation') or {}
                    if p.get('renderedConfigTruncated'):
                        out.append("    Config body: truncated in this response")
                    if location.get('key'):
                        out.append(f"    Full config body: s3://{location.get('bucket', '?')}/"
                                   f"{location['key']}")
            inputs = message.get('inputFiles', [])
            if inputs:
                out.append(f"\nInput files ({len(inputs)}){_mark('inputFiles')}:")
                for f in inputs:
                    # versionId is the concrete S3 version read (resolved at launch); shown when present.
                    version = f.get('versionId', '')
                    version_suffix = f"  (v {version})" if version else ""
                    out.append(f"  {f.get('databaseId', '?')}:{f.get('assetId', '?')}"
                               f"{f.get('inputAssetFileKey', '')}{version_suffix}")
            # Metadata sources: the entities the run read stored metadata from. The named database is
            # the caller's own selection, which only a run with no input files has; the databases list
            # is every database actually captured, derived from the input files' assets otherwise.
            # Source assets are not input files and appear in neither the list above nor arity.
            source_databases = message.get('metadataSourceDatabases') or []
            source_assets = message.get('metadataSourceAssets') or []
            named_database = message.get('metadataSourceDatabaseId')
            if named_database or source_databases or source_assets:
                out.append("\nMetadata sources:")
                if named_database:
                    out.append(f"  Named database: {named_database}")
                if source_databases:
                    out.append(f"  Databases captured: {', '.join(source_databases)}")
                for s in source_assets:
                    out.append(f"  Asset: {s.get('databaseId', '?')}:{s.get('assetId', '?')}")
            # Counts only for the metadata collections (the rows themselves are in --json-output), so a
            # partial metadata section is still visible here rather than silently absent.
            for label, field in (("Input metadata", "inputMetadata"),
                                 ("Input database metadata", "inputDatabaseMetadata")):
                rows = message.get(field) or []
                if rows or field in truncated:
                    out.append(f"\n{label}: {len(rows)} row(s){_mark(field)}")
            outputs = message.get('outputs', {})
            files = outputs.get('files', []) if isinstance(outputs, dict) else []
            if files:
                out.append(f"\nOutput files ({len(files)}){_mark('outputs.files')}:")
                for f in files:
                    out.append(f"  {f.get('relativeFilePath', '?')}")
            if truncated:
                out.append(f"\n(Truncated collections: {', '.join(sorted(truncated))})")
            return '\n'.join(out)

        output_result(_message(result), json_output, cli_formatter=_fmt)
        return result
    except ExecutionNotFoundError as e:
        output_error(e, json_output, error_type="Execution Not Found",
                     helpful_message="Use 'vamscli execution list' to see available executions.")
        raise click.ClickException(str(e))


@execution.command('details-metadata')
@click.argument('execution_id')
@click.option('--collection', type=click.Choice(list(EXECUTION_DETAIL_METADATA_COLLECTIONS)),
              default='input',
              help='Which metadata collection to page: input (asset/file), inputDatabase, or output')
@click.option('--pipeline-id', help="Only rows produced by this pipeline (one workflow step)")
@click.option('--page-size', type=int,
              help=f'Rows per page (max {MAX_EXECUTION_DETAIL_METADATA_PAGE_SIZE})')
@click.option('--max-items', type=int, help='Maximum total rows to fetch (only with --auto-paginate)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all rows')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def details_metadata(ctx: click.Context, execution_id: str, collection: str,
                     pipeline_id: Optional[str], page_size: Optional[int], max_items: Optional[int],
                     starting_token: Optional[str], auto_paginate: bool, json_output: bool):
    """Page one metadata collection of an execution's detail view.

    'execution details' bounds its metadata collections and names any that came back partial in
    truncatedCollections; this command reads a named collection in full, a page at a time. Rows carry
    the same shape the details view returns plus the pipelineId that produced them.

    A pagination token is only valid alongside the --collection and --pipeline-id it was issued with,
    so pass the same ones when resuming with --starting-token.

    Examples:
        vamscli execution details-metadata my-execution-id
        vamscli execution details-metadata my-execution-id --collection output --auto-paginate
        vamscli execution details-metadata my-execution-id --pipeline-id my-pipeline --page-size 500
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)

    if auto_paginate and starting_token:
        raise click.ClickException("Cannot use --auto-paginate with --starting-token.")
    if max_items and not auto_paginate:
        output_warning("--max-items only applies with --auto-paginate. Ignoring.", json_output)
        max_items = None
    if page_size and page_size > MAX_EXECUTION_DETAIL_METADATA_PAGE_SIZE:
        output_warning(
            f"--page-size above {MAX_EXECUTION_DETAIL_METADATA_PAGE_SIZE} is clamped by the service. "
            f"Using {MAX_EXECUTION_DETAIL_METADATA_PAGE_SIZE}.", json_output)
        page_size = MAX_EXECUTION_DETAIL_METADATA_PAGE_SIZE

    def _base_params() -> Dict[str, Any]:
        params: Dict[str, Any] = {'collection': collection}
        if pipeline_id:
            params['pipelineId'] = pipeline_id
        if page_size:
            params['pageSize'] = page_size
        return params

    def _row(row: Dict[str, Any]) -> str:
        pipeline = row.get('pipelineId', '')
        pipeline_suffix = f"  [{pipeline}]" if pipeline else ""
        if collection == 'output':
            return (f"  {row.get('targetFilePath', '?')}  "
                    f"{row.get('metadataKey', '?')}={row.get('metadataValue', '')}{pipeline_suffix}")
        # The input collections key on the entity the metadata was read from; a database-scope row
        # carries no asset, so the scope is named rather than inferred from the blank fields.
        entries = row.get('metadata') or {}
        # File attributes are a separate grant (fileAttributes vs fileMetadata), so they are counted
        # separately — a row carrying only attributes would otherwise read as "0 entries".
        attributes = row.get('attributes') or {}
        counts = f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}"
        if attributes:
            counts += f", {len(attributes)} attribute{'' if len(attributes) == 1 else 's'}"
        return (f"  {row.get('databaseId', '?')}:{row.get('assetId', '') or '-'}"
                f"{row.get('filePath', '')}  scope={row.get('scope', '?')}  "
                f"{counts}{pipeline_suffix}")

    def _fmt(data: Dict[str, Any]) -> str:
        items = data.get('Items', [])
        if not items:
            return f"No {collection} metadata rows found."
        out = []
        if data.get('autoPaginated'):
            out.append(f"Auto-paginated: {data.get('totalItems', 0)} row(s) in "
                       f"{data.get('pageCount', 0)} page(s)")
        out.append(f"Collection: {data.get('collection', collection)}")
        out.append(f"Found {len(items)} row(s):")
        out.extend(_row(row) for row in items)
        if data.get('NextToken'):
            out.append(f"\nNext token: {data['NextToken']}")
        if data.get('note'):
            out.append(f"\n{data['note']}")
        return '\n'.join(out)

    try:
        if auto_paginate:
            max_total = max_items or 10000
            output_status(
                f"Retrieving {collection} metadata for execution '{execution_id}' "
                f"(auto-paginating up to {max_total})...", json_output)
            all_items = []
            next_token = None
            page_count = 0
            while True:
                page_count += 1
                params = _base_params()
                if next_token:
                    params['startingToken'] = next_token
                page = _message(api_client.get_execution_details_metadata(
                    execution_id, params=params))
                all_items.extend(page.get('Items', []))
                if not json_output:
                    output_status(f"Fetched {len(all_items)} row(s) (page {page_count})...", False)
                # Absent on the last page, so its presence is the only signal there is more.
                next_token = page.get('NextToken')
                if (not next_token or len(all_items) >= max_total
                        or page_count >= MAX_EXECUTION_AUTO_PAGINATE_PAGES):
                    break
            result = {'Items': all_items, 'collection': collection, 'totalItems': len(all_items),
                      'autoPaginated': True, 'pageCount': page_count}
            # The token is emitted on both stop conditions: a token is only valid alongside the
            # --collection and --pipeline-id it was issued with, and dropping it leaves the caller
            # re-walking the collection from row one.
            if next_token and len(all_items) >= max_total:
                result['NextToken'] = next_token
                result['note'] = (
                    f"Reached maximum of {max_total} rows. More may be available — resume with "
                    f"--starting-token {next_token}")
            elif next_token and page_count >= MAX_EXECUTION_AUTO_PAGINATE_PAGES:
                result['NextToken'] = next_token
                result['note'] = (
                    f"Stopped after {page_count} pages. More may be available — resume with "
                    f"--starting-token {next_token}")
            output_result(result, json_output, cli_formatter=_fmt)
            return result

        output_status(f"Retrieving {collection} metadata for execution '{execution_id}'...",
                      json_output)
        params = _base_params()
        if starting_token:
            params['startingToken'] = starting_token
        result = _message(api_client.get_execution_details_metadata(execution_id, params=params))
        output_result(result, json_output, cli_formatter=_fmt)
        return result
    except ExecutionNotFoundError as e:
        output_error(e, json_output, error_type="Execution Not Found",
                     helpful_message="Use 'vamscli execution list' to see available executions.")
        raise click.ClickException(str(e))
    except InvalidExecutionDataError as e:
        output_error(e, json_output, error_type="Invalid Metadata Page Request",
                     helpful_message="A pagination token is only valid with the --collection and "
                                     "--pipeline-id it was issued for.")
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
            def _events(key, label):
                evs = message.get(key)
                if not evs:
                    return
                out.append(f"\n== {label} ({len(evs)}) ==")
                for ev in evs:
                    line = f"  [{ev.get('timestamp', '')}] {ev.get('message', '')}".rstrip()
                    # subProcessEvents mix several log groups; name the source so a step's own
                    # invocation log is distinguishable from the pipeline's registered logs.
                    if ev.get('logGroupArn'):
                        line += f"  ({ev['logGroupArn'].rsplit(':log-group:', 1)[-1]})"
                    out.append(line)

            _events('events', 'Events')
            if message.get('nextToken'):
                out.append(f"\nNext token: {message['nextToken']}")
            # The state-transition timeline and the per-step logs (step invocation log, the
            # pipeline's registered logs, any sub-execution history) were reachable only via
            # --json-output before; they are the logs that explain a failed launch.
            _events('sfnHistoryEvents', 'State Machine History')
            _events('subProcessEvents', 'Sub-Process Logs')
            warnings = message.get('warnings')
            if warnings:
                # A log VAMS could not read (missing permission, or past the per-request cap). Shown
                # rather than swallowed: the alternative is output that looks complete but is not.
                out.append(f"\n== Warnings ({len(warnings)}) ==")
                out.extend(f"  {w}" for w in warnings)
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
@click.option('--yes', is_flag=True, help='Skip the interactive confirmation prompt for --group-id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def abort(ctx: click.Context, execution_id: Optional[str], group_id: Optional[str], yes: bool,
          json_output: bool):
    """Abort a running execution, or an entire execution group with --group-id.

    Provide either an EXECUTION_ID (abort one) or --group-id (abort the group). When aborting a
    group, pass any member execution id as EXECUTION_ID (the id in the path is ignored for grouping).
    A group abort terminates every active execution the caller can reach in the group, so it is
    confirmed interactively unless `--yes` is passed; `--yes` is required in JSON mode.

    Examples:
        vamscli execution abort my-execution-id
        vamscli execution abort my-execution-id --group-id grp-123 --yes
    """
    api_client = _api(ctx)
    if not execution_id and not group_id:
        raise click.ClickException("Provide an EXECUTION_ID or --group-id to abort.")
    # The abort-by-group route is still keyed on an executionId path param; require one.
    if group_id and not execution_id:
        raise click.ClickException(
            "Provide a member EXECUTION_ID along with --group-id (the group abort route is keyed on "
            "an execution id).")
    if group_id and not yes and not json_output:
        click.confirm(
            f"Abort every active execution in group '{group_id}'? This cannot be undone.",
            abort=True)

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
            else:
                out = [str(message)]
            warnings = _warnings(result)
            if warnings:
                out.append("Warnings:")
                out.extend(f"  - {w}" for w in warnings)
            return '\n'.join(out)

        output_result(_payload_with_warnings(result), json_output,
                      success_message="✓ Abort request submitted.", cli_formatter=_fmt)
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
            if isinstance(message, dict):
                if message.get('executionGroupId'):
                    out.append(f"Group: {message['executionGroupId']}")
                # The re-run is launched by the execute handler, so it returns that handler's
                # non-fatal warnings (e.g. a metadata capture bounded by the per-entity cap, or a
                # source database that could not be read). Shown rather than dropped: the run
                # succeeded but its inputs are not what the caller named.
                if message.get('warnings'):
                    out.append("Warnings:")
                    out.extend(f"  - {w}" for w in message['warnings'])
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
