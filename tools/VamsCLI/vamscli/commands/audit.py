"""Audit log search commands for VamsCLI.

Searches the VAMS audit logs (who did what, when, to which entity) through the audit log search API:

    audit groups
    audit search [filters] [--no-wait]
    audit results --handle H [--page-token T | --all]
    audit show --handle H --record-id R
    audit stop --handle H
    audit export --handle H --format csv|ndjson [--output FILE]

A search starts a billable CloudWatch Logs Insights query on the server and answers a handle. The
CLI waits for it with the server's long poll (about 20 seconds per request) and then reads the rows.
Audit records can be sensitive. The API decides which log groups and rows the caller may see; these
commands never filter, count or infer rows on their own.

Not to be confused with `compliance audit`, which reads the compliance evaluation trail.
"""

import os
import sys
import tempfile
from typing import Any, Dict, Optional

import click

from ..constants import (
    AUDIT_SEARCH_DISABLED_MESSAGE,
    FEATURE_AUDITLOGSEARCH,
    AUDIT_DEFAULT_MAX_ROWS,
    AUDIT_DEFAULT_PAGE_SIZE,
    AUDIT_DEFAULT_WAIT_TIMEOUT_SECONDS,
    AUDIT_DEFAULT_WINDOW,
    AUDIT_EXPORT_FORMATS,
    AUDIT_MAX_PAGE_SIZE,
    AUDIT_OUTCOMES,
    AUDIT_SORT_ORDERS,
)
from ..utils.api_client import APIClient
from ..utils.audit_search import (
    build_filters,
    check_export_format,
    check_page_size,
    collect_rows,
    export_chunks,
    find_record,
    format_export,
    format_groups,
    format_row_detail,
    format_rows,
    format_warnings,
    is_final,
    merge_warnings,
    raise_if_not_complete,
    resolve_contains_limits,
    wait_for_audit_query,
)
from ..utils.decorators import get_profile_manager_from_context, requires_feature, requires_setup_and_auth
from ..utils.exceptions import (
    AuditQueryNotFoundError,
    AuditSearchError,
    AuditSearchFailedError,
    AuditSearchNotSupportedError,
    AuditSearchTimeoutError,
    InvalidAuditRequestError,
)
from ..utils.logging import LogMaskedOption
from ..utils.json_output import output_error, output_info, output_result, output_status, output_warning


def _api(ctx: click.Context) -> APIClient:
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    return APIClient(config['api_gateway_url'], profile_manager)


def _handle_audit_error(e: Exception, json_output: bool) -> None:
    """Report an audit error once and end the command (exit 1)."""
    if isinstance(e, AuditQueryNotFoundError):
        output_error(e, json_output, error_type="Audit Search Not Found",
                     helpful_message="Start a new search with 'vamscli audit search'. A handle "
                                     "expires after a period without use. A search that used "
                                     "--contains cannot be read after it has been exported or stopped.")
    elif isinstance(e, AuditSearchNotSupportedError):
        output_error(e, json_output, error_type="Audit Search Not Supported")
    elif isinstance(e, InvalidAuditRequestError):
        output_error(e, json_output, error_type="Invalid Audit Request",
                     helpful_message="See 'vamscli audit groups' for the actions, entity types and "
                                     "limits of this deployment.")
    elif isinstance(e, AuditSearchFailedError):
        output_error(e, json_output, error_type="Audit Search Ended")
    elif isinstance(e, AuditSearchTimeoutError):
        output_error(e, json_output, error_type="Audit Search Timeout")
    else:
        output_error(e, json_output, error_type="Audit Search Error")
    raise click.ClickException(str(e))


def _note_running_search(query_id: str, error: BaseException, json_output: bool) -> None:
    """After a failure or an interrupt that follows the start of a search, say what its handle is.

    The search keeps running on the server (a billable query that holds one of the caller's
    concurrent-search slots until it ends), so the user needs the handle to stop or read it. The note
    goes to stderr. A --json-output error carries `queryId` in its JSON document instead, which keeps
    that output a single document; only an interrupt, which prints no document, still writes the note."""
    if not json_output or not isinstance(error, Exception):
        click.echo(f"The search {query_id} may still be running. Stop it with "
                   f"'vamscli audit stop --handle {query_id}' or read it with "
                   f"'vamscli audit results --handle {query_id}'.", err=True)
    try:
        fields = dict(getattr(error, 'json_fields', None) or {})
        fields.setdefault('queryId', query_id)
        error.json_fields = fields
    except Exception:
        pass


def _effective_page_size(page_size: Optional[int], max_rows: int) -> int:
    """A page never asks for more rows than the command will keep."""
    return max(1, min(check_page_size(page_size) or AUDIT_DEFAULT_PAGE_SIZE, max_rows))


def _print_rows(page: Dict[str, Any], json_output: bool) -> Dict[str, Any]:
    output_result(page, json_output, cli_formatter=format_rows)
    return page


@click.group()
def audit():
    """Search the VAMS audit logs: who did what, when, and to which entity.

    A search starts a CloudWatch Logs Insights query (billed to the deployment) and returns a handle;
    the commands wait for it, page through the rows and export them. Audit records may be sensitive.
    The API decides which log groups and rows you may see.

    This is not 'vamscli compliance audit', which reads the compliance evaluation trail.
    """
    pass


@audit.command()
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_AUDITLOGSEARCH, AUDIT_SEARCH_DISABLED_MESSAGE)
def groups(ctx: click.Context, json_output: bool):
    """List the audit log groups you may search, the actions and entity types, and the limits.

    An empty list is normal for a user who may search none. Run this first: it shows the values the
    search filters accept and the deployment's limits (longest time window, rows, concurrent searches).

    \b
    Examples:
        vamscli audit groups
        vamscli audit groups --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status("Retrieving audit log groups...", json_output)
    try:
        result = api_client.list_audit_log_groups()
        output_result(result, json_output, cli_formatter=format_groups)
        return result
    except AuditSearchError as e:
        _handle_audit_error(e, json_output)


@audit.command()
@click.option('--group', 'groups_', multiple=True, help='Log group key to search (repeatable; default: every group you may search)')
@click.option('--action', 'actions', multiple=True, help='Action name, for example assetUpdate (repeatable)')
@click.option('--entity-type', 'entity_types', multiple=True, help='Entity type, for example asset (repeatable)')
@click.option('--entity-id', 'entity_ids', multiple=True, help='Entity ID (repeatable)', cls=LogMaskedOption)
@click.option('--user', 'users', multiple=True, help='Acting user, on-behalf-of user (repeatable)', cls=LogMaskedOption)
@click.option('--api-key-id', 'api_key_ids', multiple=True, help='API key ID that made the request (repeatable)', cls=LogMaskedOption)
@click.option('--database', 'databases', multiple=True, help='Database ID (repeatable)')
@click.option('--asset', 'assets', multiple=True, help='Asset ID (repeatable)')
@click.option('--outcome', 'outcomes', multiple=True, type=click.Choice(list(AUDIT_OUTCOMES)),
              help='Outcome (repeatable)')
@click.option('--request-id', 'request_ids', multiple=True, help='Request ID (repeatable)')
@click.option('--contains', 'contains', multiple=True,
              help='Text the record must contain (repeatable, all must match; at least 3 characters of '
                   'letters, digits, space and . _ : @ + / -; the most terms and the longest term are '
                   "the limits 'audit groups' shows, 3 terms of 64 characters by default)", cls=LogMaskedOption)
@click.option('--from', 'time_from', default=None,
              help=f'Start of the window: relative (90m, 24h, 7d, 2w), ISO 8601 or epoch seconds '
                   f'(default {AUDIT_DEFAULT_WINDOW} ago)')
@click.option('--to', 'time_to', default=None,
              help='End of the window: relative, "now", ISO 8601 or epoch seconds (default now)')
@click.option('--include-probes', is_flag=True,
              help='Include permission-probe records (hidden by default)')
@click.option('--order', type=click.Choice(list(AUDIT_SORT_ORDERS)), default=None,
              help='Sort order of the rows by time (default: server default, newest first)')
@click.option('--max-rows', type=click.IntRange(min=1), default=AUDIT_DEFAULT_PAGE_SIZE, show_default=True,
              help='Most rows to read into the output')
@click.option('--page-size', type=click.IntRange(1, AUDIT_MAX_PAGE_SIZE), default=None,
              help=f'Rows per request (1-{AUDIT_MAX_PAGE_SIZE})')
@click.option('--no-wait', is_flag=True,
              help="Start the search and print its handle without waiting; read it with 'audit results'")
@click.option('--timeout', type=click.IntRange(min=1), default=AUDIT_DEFAULT_WAIT_TIMEOUT_SECONDS,
              show_default=True, help='Seconds to wait for the search to finish')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_AUDITLOGSEARCH, AUDIT_SEARCH_DISABLED_MESSAGE)
def search(ctx: click.Context, groups_, actions, entity_types, entity_ids, users, api_key_ids,
           databases, assets, outcomes, request_ids, contains, time_from, time_to, include_probes,
           order, max_rows, page_size, no_wait, timeout, json_output: bool):
    """Search the audit logs and print the matching records.

    Starts a search (a billable CloudWatch Logs Insights query), waits for it with the server long
    poll and prints up to --max-rows rows. Every filter narrows the search; the window defaults to
    the last 24 hours. Results are the records you may see; nothing is counted or inferred about
    records you may not.

    \b
    Examples:
        vamscli audit search --action assetUpdate --from 7d
        vamscli audit search --user carol --outcome denied --from 2026-11-01 --to 2026-11-02
        vamscli audit search --group auditActions --contains report.usd --json-output
        vamscli audit search --from 24h --no-wait
    """
    # Setup/auth already validated by decorator
    try:
        api_client = _api(ctx)
        body = build_filters(
            groups=groups_, actions=actions, entity_types=entity_types, entity_ids=entity_ids,
            users=users, api_key_ids=api_key_ids, databases=databases, assets=assets,
            outcomes=outcomes, request_ids=request_ids, contains=contains, time_from=time_from,
            time_to=time_to, include_probes=include_probes, order=order,
            contains_limits=resolve_contains_limits(api_client, contains))
        effective_page_size = _effective_page_size(page_size, max_rows)

        output_status("Starting the audit search...", json_output)
        started = api_client.start_audit_query(body)
        query_id = started.get('queryId')
        for warning in started.get('warnings') or []:
            output_warning(f"Warning: {warning}", json_output)

        if no_wait:
            output_result(
                started, json_output,
                cli_formatter=lambda r: (f"Search started: {r.get('queryId')}\n"
                                         f"Read it with: vamscli audit results --handle {r.get('queryId')}"))
            return started

        # The handle is shown at once: a failure or Ctrl-C while waiting would otherwise lose it.
        output_status(f"Search handle: {query_id}", json_output)
        output_status("Waiting for the search to finish...", json_output)
        try:
            final = wait_for_audit_query(api_client, query_id, timeout=timeout,
                                         page_size=effective_page_size, sort=order)
            raise_if_not_complete(final, query_id)
            page = collect_rows(api_client, query_id, final, max_rows=max_rows,
                                page_size=effective_page_size, sort=order)
        except (AuditSearchTimeoutError, AuditSearchFailedError):
            raise  # these already name the handle or end the search
        except (KeyboardInterrupt, click.Abort, Exception) as interrupted:
            _note_running_search(query_id, interrupted, json_output)
            raise
        page['queryId'] = query_id
        # The start answer's warnings and those of every page read, each once.
        page['warnings'] = merge_warnings(merge_warnings([], started.get('warnings')),
                                          page.get('warnings'))
        return _print_rows(page, json_output)
    except AuditSearchError as e:
        _handle_audit_error(e, json_output)


@audit.command()
@click.option('--handle', 'query_id', required=True, help='Search handle printed by "audit search"')
@click.option('--page-token', default=None, help='NextToken of the previous page')
@click.option('--all', 'read_all', is_flag=True, help='Read every page, up to --max-rows')
@click.option('--max-rows', type=click.IntRange(min=1), default=AUDIT_DEFAULT_MAX_ROWS, show_default=True,
              help='With --all, the most rows to read')
@click.option('--page-size', type=click.IntRange(1, AUDIT_MAX_PAGE_SIZE), default=None,
              help=f'Rows per request (1-{AUDIT_MAX_PAGE_SIZE})')
@click.option('--order', type=click.Choice(list(AUDIT_SORT_ORDERS)), default=None, help='Sort order')
@click.option('--wait', 'wait_', is_flag=True, help='Wait for a running search to finish first')
@click.option('--timeout', type=click.IntRange(min=1), default=AUDIT_DEFAULT_WAIT_TIMEOUT_SECONDS,
              show_default=True, help='Seconds to wait with --wait')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_AUDITLOGSEARCH, AUDIT_SEARCH_DISABLED_MESSAGE)
def results(ctx: click.Context, query_id: str, page_token: Optional[str], read_all: bool,
            max_rows: int, page_size: Optional[int], order: Optional[str], wait_: bool,
            timeout: int, json_output: bool):
    """Read the rows of a search by its handle: one page, or every page with --all.

    A handle expires after a period without use (an hour by default) and the API then answers that
    the search was not found. While a search is Running no rows are returned; use --wait or read it
    again later.

    \b
    Examples:
        vamscli audit results --handle <queryId>
        vamscli audit results --handle <queryId> --page-token 100
        vamscli audit results --handle <queryId> --all --max-rows 500 --json-output
    """
    # Setup/auth already validated by decorator
    try:
        if page_token and not page_token.isdigit():
            raise InvalidAuditRequestError("--page-token must be the NextToken printed by the previous page")
        api_client = _api(ctx)
        effective_page_size = _effective_page_size(page_size, max_rows if read_all else AUDIT_MAX_PAGE_SIZE)
        output_status("Reading the audit search...", json_output)
        page = api_client.get_audit_query(query_id, wait=0, page_size=effective_page_size,
                                          starting_token=page_token, sort=order)
        if not is_final(page.get('status')) and wait_:
            output_status("Waiting for the search to finish...", json_output)
            page = wait_for_audit_query(api_client, query_id, timeout=timeout,
                                        page_size=effective_page_size, sort=order)
        if is_final(page.get('status')):
            raise_if_not_complete(page, query_id)
            if read_all:
                page = collect_rows(api_client, query_id, page, max_rows=max_rows,
                                    page_size=effective_page_size, sort=order)
        else:
            output_info("The search is still running; read it again later or use --wait.", json_output)
        page['queryId'] = query_id
        return _print_rows(page, json_output)
    except AuditSearchError as e:
        _handle_audit_error(e, json_output)


@audit.command()
@click.option('--handle', 'query_id', required=True, help='Search handle printed by "audit search"')
@click.option('--record-id', required=True, help='The recordId of the row to show')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_AUDITLOGSEARCH, AUDIT_SEARCH_DISABLED_MESSAGE)
def show(ctx: click.Context, query_id: str, record_id: str, json_output: bool):
    """Show every field of one record of a search.

    Reads the search's pages until it finds the row whose recordId matches. The API has no
    single-record route, so this works only on a Complete search whose handle has not expired.
    Rows written before records carried a recordId cannot be addressed here; read them with
    'audit results'. A search that used --contains cannot be read after it was exported or stopped.

    \b
    Example:
        vamscli audit show --handle <queryId> --record-id <recordId>
    """
    # Setup/auth already validated by decorator
    try:
        api_client = _api(ctx)
        output_status("Looking for the record...", json_output)
        seen: Dict[str, int] = {}
        row = find_record(api_client, query_id, record_id, page_size=AUDIT_MAX_PAGE_SIZE, stats=seen)
        if row is None:
            message = "No record with that recordId in this search. Check the handle and the recordId."
            if seen.get('withoutId'):
                message += (f" {seen['withoutId']} rows of this search have no recordId (written before "
                            "records carried one), so 'audit show' cannot address them; read them with "
                            "'audit results --json-output'.")
            raise AuditQueryNotFoundError(message)
        output_result(row, json_output, cli_formatter=format_row_detail)
        return row
    except AuditSearchError as e:
        _handle_audit_error(e, json_output)


@audit.command()
@click.option('--handle', 'query_id', required=True, help='Search handle printed by "audit search"')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_AUDITLOGSEARCH, AUDIT_SEARCH_DISABLED_MESSAGE)
def stop(ctx: click.Context, query_id: str, json_output: bool):
    """Stop a running search and free its concurrency slot.

    Stopping your own search discards nothing you need: a stopped search returns no rows. A search
    that already ended keeps its status.

    \b
    Example:
        vamscli audit stop --handle <queryId>
    """
    # Setup/auth already validated by decorator
    try:
        api_client = _api(ctx)
        output_status("Stopping the audit search...", json_output)
        result = api_client.stop_audit_query(query_id)
        output_result(result, json_output,
                      cli_formatter=lambda r: f"Search {r.get('queryId')}: {r.get('status')}")
        return result
    except AuditSearchError as e:
        _handle_audit_error(e, json_output)


@audit.command(name='export')
@click.option('--handle', 'query_id', required=True, help='Search handle printed by "audit search"')
@click.option('--format', 'export_format', type=click.Choice(list(AUDIT_EXPORT_FORMATS)), default='csv',
              show_default=True, help='Export format')
@click.option('--output', '-o', 'output_path', type=click.Path(dir_okay=False), default=None,
              help='File to write (default: standard output; required with --json-output)')
@click.option('--overwrite', is_flag=True, help='Replace the output file if it exists')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
@requires_feature(FEATURE_AUDITLOGSEARCH, AUDIT_SEARCH_DISABLED_MESSAGE)
def export_(ctx: click.Context, query_id: str, export_format: str, output_path: Optional[str],
            overwrite: bool, json_output: bool):
    """Export the rows of a Complete search as CSV or NDJSON.

    The text is written exactly as the API returns it. The API guards CSV cells that start with
    = + - @ against spreadsheet formulas; the CLI never evaluates or rewrites cells. Audit exports
    may be sensitive: write them where only you can read them. The folder of --output must exist.
    A search that used --contains cannot be exported again after its export finished or after it was
    stopped.

    \b
    Examples:
        vamscli audit export --handle <queryId> --format csv -o audit.csv
        vamscli audit export --handle <queryId> --format ndjson > audit.ndjson
    """
    # Setup/auth already validated by decorator
    try:
        check_export_format(export_format)
        if json_output and not output_path:
            raise InvalidAuditRequestError("--json-output needs --output FILE: the export text would "
                                           "otherwise share standard output with the JSON result")
        if output_path and os.path.exists(output_path) and not overwrite:
            raise InvalidAuditRequestError(f"'{output_path}' already exists; use --overwrite to replace it")
        if output_path:
            folder = os.path.dirname(os.path.abspath(output_path))
            if not os.path.isdir(folder):
                raise InvalidAuditRequestError(
                    f"The folder for '{output_path}' does not exist. Create it first.")
        api_client = _api(ctx)
        if output_path:
            output_status("Exporting the audit search...", json_output)
        total = 0
        warnings = []
        sink = None
        temp_path = None
        if output_path:
            # Written beside the target and moved into place at the end, so a failed export leaves
            # neither a partial file nor a replaced one.
            directory = os.path.dirname(os.path.abspath(output_path))
            handle_fd, temp_path = tempfile.mkstemp(prefix='.vams-audit-', suffix='.part', dir=directory)
            sink = os.fdopen(handle_fd, 'w', encoding='utf-8', newline='')
        try:
            for content, chunk in export_chunks(api_client, query_id, export_format):
                total += int(chunk.get('rowCount') or 0)
                merge_warnings(warnings, chunk.get('warnings'))
                if sink:
                    sink.write(content)
                else:
                    sys.stdout.write(content)
            if sink:
                sink.close()
                sink = None
                os.replace(temp_path, output_path)
                temp_path = None
        finally:
            if sink:
                sink.close()
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        result = {'queryId': query_id, 'format': export_format, 'output': output_path,
                  'rowCount': total, 'warnings': warnings}
        if not output_path:
            sys.stdout.flush()
            # Standard output holds the export text only, so the warnings go to standard error.
            for line in format_warnings(warnings):
                click.secho(line, fg='yellow', bold=True, err=True)
            return result
        output_result(result, json_output, cli_formatter=format_export)
        return result
    except AuditSearchError as e:
        _handle_audit_error(e, json_output)
