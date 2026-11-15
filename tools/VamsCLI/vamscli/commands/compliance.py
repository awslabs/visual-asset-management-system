"""Compliance commands for VamsCLI.

Covers the compliance schema registry, schema bindings, evaluation, quarantine, cascades and the
audit trail:

    compliance schema list / get / create / update / delete
    compliance bind / unbind / bindings
    compliance evaluate / sweep / state / evaluations
    compliance quarantine list / release / exception / revoke-exception
    compliance cascade list / get / create / approve / reject
    compliance audit

A schema is bound to a database (every asset in it inherits the binding) or to a single asset as an
override. Evaluation compares an asset against its bound schema's rules; a failed quarantine-level
rule quarantines the asset until it is released or granted an exception. An exception moves the
asset to the `exception` state and is scoped to the schema name and version it was granted
against: re-evaluation records the violations but keeps the asset released until the exception is
revoked or an evaluation against another schema or a newer version supersedes it. A cascade
re-evaluates the dependents of a changed asset, optionally after a human approval.
"""

import json
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import click

from ..constants import (
    COMPLIANCE_AUDIT_DEFAULT_LIMIT,
    COMPLIANCE_BINDING_APPLY_APPLIED,
    COMPLIANCE_BINDING_APPLY_APPLYING,
    COMPLIANCE_BINDING_APPLY_DATABASE_DELETED_REASON,
    COMPLIANCE_BINDING_APPLY_FAILED,
    COMPLIANCE_BINDING_APPLY_POLL_DELAYS,
    COMPLIANCE_BINDING_APPLY_STALLED,
    DEFAULT_COMPLIANCE_BINDING_WAIT_TIMEOUT,
    MAX_COMPLIANCE_BINDING_WAIT_TIMEOUT,
    DEFAULT_COMPLIANCE_EVALUATIONS_PAGE_SIZE,
    DEFAULT_COMPLIANCE_LIST_PAGE_SIZE,
    MAX_COMPLIANCE_AUDIT_PAGE_SIZE,
    MAX_COMPLIANCE_EVALUATIONS_PAGE_SIZE,
    MAX_COMPLIANCE_LIST_PAGE_SIZE,
    MAX_COMPLIANCE_LISTING_PAGE_SIZE,
)
from ..utils.api_client import APIClient
from ..utils.decorators import get_profile_manager_from_context, requires_setup_and_auth
from ..utils.exceptions import (
    AssetNotFoundError,
    ComplianceCascadeNotFoundError,
    ComplianceSchemaNotFoundError,
    DatabaseNotFoundError,
    InvalidComplianceDataError,
)
from ..utils.json_output import output_error, output_result, output_status


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _api(ctx: click.Context) -> APIClient:
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    return APIClient(config['api_gateway_url'], profile_manager)


def load_schema_body(schema_file: str) -> Dict[str, Any]:
    """Read the schema body a --schema-file option names.

    The value is a path to a JSON file holding the schema document, or the document itself as an
    inline JSON object. Either way the result must be a JSON object: the API rejects anything else,
    and the message it gives ("schemaBody must be a non-empty object") does not say which input was
    at fault.
    """
    if not schema_file:
        raise click.BadParameter("--schema-file is required")
    try:
        parsed = json.loads(schema_file)
    except json.JSONDecodeError:
        try:
            with open(schema_file, 'r', encoding='utf-8') as handle:
                parsed = json.load(handle)
        except (FileNotFoundError, IOError):
            raise click.BadParameter(
                f"'{schema_file}' is neither a readable file path nor an inline JSON object"
            )
        except json.JSONDecodeError as e:
            raise click.BadParameter(f"Invalid JSON in schema file '{schema_file}': {e}")
    if not isinstance(parsed, dict):
        raise click.BadParameter("The schema body must be a JSON object")
    return parsed


class _ReportedComplianceError(click.ClickException):
    """A compliance error `output_error` has already printed: it ends the command with exit code 1,
    and Click's own handler prints nothing more, so the error appears once."""

    def show(self, file=None) -> None:
        return None


def _handle_compliance_error(e: Exception, json_output: bool) -> None:
    """Report a compliance business-logic error once and end the command.

    `output_error` prints the styled error (and hint) and, in JSON mode, exits the process with the
    JSON error. In CLI mode the command then ends through `_ReportedComplianceError`, which carries
    the message for logging but is not printed again.
    """
    if isinstance(e, ComplianceSchemaNotFoundError):
        output_error(e, json_output, error_type="Compliance Schema Not Found",
                     helpful_message="Use 'vamscli compliance schema list' to see registered schemas.")
    elif isinstance(e, ComplianceCascadeNotFoundError):
        output_error(e, json_output, error_type="Cascade Not Found",
                     helpful_message="Use 'vamscli compliance cascade list' to see pending cascades.")
    elif isinstance(e, DatabaseNotFoundError):
        output_error(e, json_output, error_type="Database Not Found",
                     helpful_message="Use 'vamscli database list' to see available databases.")
    elif isinstance(e, AssetNotFoundError):
        output_error(e, json_output, error_type="Asset Not Found",
                     helpful_message="Use 'vamscli assets list -d <database>' to see available assets.")
    elif isinstance(e, click.BadParameter):
        output_error(e, json_output, error_type="Invalid Input")
    else:
        output_error(e, json_output, error_type="Compliance Error")
    raise _ReportedComplianceError(str(e))


_COMPLIANCE_ERRORS = (
    ComplianceSchemaNotFoundError, ComplianceCascadeNotFoundError, DatabaseNotFoundError,
    AssetNotFoundError, InvalidComplianceDataError, click.BadParameter,
)


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _next_page_lines(result: Dict[str, Any]) -> list:
    """The continuation hint every paged listing prints when the response carries a NextToken."""
    if not result.get('NextToken'):
        return []
    return [f"\nNext token: {result['NextToken']}", "Use --starting-token to get the next page"]


def schema_format_of(schema: Dict[str, Any]) -> Optional[str]:
    """The format of a schema record: its top-level `schemaFormat`, else derived from the body.

    The listing marks a row whose body is not a vams-rules-v1 document as `legacy`; a record
    without the top-level field is classified the same way from its body. None when the record
    carries no body either.
    """
    top_level = schema.get('schemaFormat')
    if top_level:
        return top_level
    body = schema.get('schemaBody')
    if not isinstance(body, dict):
        return None
    return 'vams-rules-v1' if body.get('schemaFormat') == 'vams-rules-v1' else 'legacy'


def format_schema(schema: Dict[str, Any], include_body: bool = False) -> str:
    lines = [
        f"Schema Name: {schema.get('schemaName', 'N/A')}",
        f"Scope: {schema.get('databaseId', 'N/A')}",
        f"Version: {schema.get('version', 'N/A')}",
    ]
    description = schema.get('description')
    if description:
        lines.append(f"Description: {description}")
    schema_format = schema_format_of(schema)
    if schema_format == 'legacy':
        lines.append("Format: legacy (cannot be bound, updated or evaluated; delete it)")
    else:
        lines.append(f"Format: {schema_format or 'N/A'}")
    body = schema.get('schemaBody')
    if isinstance(body, dict):
        rules = body.get('rules')
        if isinstance(rules, dict):
            lines.append(f"Rules: {', '.join(rules) if rules else 'none'}")
        if body.get('extends'):
            lines.append(f"Extends: {body['extends']}")
    if schema.get('createdAt'):
        lines.append(f"Created: {schema['createdAt']}")
    if include_body and body is not None:
        lines.append("Schema Body:")
        lines.append(json.dumps(body, indent=2))
    return '\n'.join(lines)


def format_schema_list(result: Dict[str, Any]) -> str:
    schemas = result.get('schemas', [])
    if not schemas:
        return "No compliance schemas found."
    out = [f"Found {len(schemas)} compliance schema(s):", "-" * 80]
    for schema in schemas:
        out.append(format_schema(schema))
        out.append("-" * 80)
    return '\n'.join(out)


def format_schema_write(result: Dict[str, Any]) -> str:
    lines = [f"  Schema Name: {result.get('schemaName', 'N/A')}"]
    if result.get('databaseId'):
        lines.append(f"  Scope: {result['databaseId']}")
    if result.get('internalVersion') is not None:
        lines.append(f"  Version: {result['internalVersion']}")
    if result.get('message'):
        lines.append(f"  Message: {result['message']}")
    # The save succeeded; a warning says something about how the schema will run (pipeline rules
    # that share a workflow run one after another when it restricts concurrency).
    for warning in result.get('warnings') or []:
        if isinstance(warning, dict) and warning.get('message'):
            lines.append(f"  Warning: {warning['message']}")
    return '\n'.join(lines)


# What a waiting pipeline rule shows. It names no execution: the one running may be anyone's.
PIPELINE_RULE_WAITING_TEXT = "waiting for a running execution of its workflow"


def waiting_rule_names(evaluation: Dict[str, Any]) -> List[str]:
    """The pipeline rules of a still-pending evaluation that wait for a running execution of
    their workflow (their `pipelineExecutions` entry is `waiting` or `launching`)."""
    if (evaluation.get('status') or evaluation.get('verdict')) != 'pending_pipeline':
        return []
    return [entry.get('ruleName', 'N/A') for entry in evaluation.get('pipelineExecutions') or []
            if isinstance(entry, dict) and entry.get('status') in ('waiting', 'launching')]


def pipeline_rule_names(stored: Any) -> List[str]:
    """The rule names of an evaluation row's stored `pipelineRulesPending` (a JSON-encoded list of
    `{ruleName, rule}`, or that list decoded); none for a value that is neither."""
    if isinstance(stored, str):
        try:
            stored = json.loads(stored)
        except json.JSONDecodeError:
            return []
    if not isinstance(stored, list):
        return []
    return [entry['ruleName'] for entry in stored
            if isinstance(entry, dict) and isinstance(entry.get('ruleName'), str)]


_APPLY_OPERATION_TEXT = {
    'bind': 'bind',
    'change': 'schema change',
    'unbind': 'removal',
    'evaluatePending': 'automatic evaluation turned on',
}


def format_binding_apply(apply: Dict[str, Any]) -> List[str]:
    """The lines of a database binding change applied in the background (the `apply` of a bind or
    unbind answer, or of the bindings read): what it is, its status, its progress and its counts."""
    operation = _APPLY_OPERATION_TEXT.get(apply.get('operation'), apply.get('operation') or 'change')
    status = apply.get('status') or 'unknown'
    total = apply.get('assetCount', 0)
    lines = [f"Binding Change: {operation}, {status} "
             f"({apply.get('assetsProcessed', 0)} of {total} assets)"]
    if apply.get('operation') == 'unbind':
        lines.append(f"  Compliance Records Removed: {apply.get('removedComplianceRecords', 0)}")
    elif apply.get('operation') != 'evaluatePending':
        lines.append(f"  Assets Pending Evaluation: {apply.get('assetsPendingEvaluation', 0)}")
    if apply.get('operation') != 'evaluatePending':
        lines.append(f"  Assets Kept Quarantined: {apply.get('assetsKeptQuarantined', 0)}")
    lines.append(f"  Evaluations Queued: {apply.get('evaluationsQueued', 0)}")
    if apply.get('startedAt'):
        lines.append(f"  Started: {apply['startedAt']}")
    if apply.get('finishedAt'):
        lines.append(f"  Finished: {apply['finishedAt']}")
    elif apply.get('lastProgressAt'):
        lines.append(f"  Last Progress: {apply['lastProgressAt']}")
    if apply.get('reason'):
        lines.append(f"  Reason: {apply['reason']}")
    if apply.get('evaluationStatus'):
        # With automatic evaluation on, the evaluations are queued once every record carries the
        # binding, a share at a time; the change does not wait for them.
        lines.append(f"  Evaluations: {apply['evaluationStatus']} ({apply.get('evaluationsQueued', 0)} "
                     f"of {apply.get('evaluationsToQueue', 0)} queued)")
        if apply.get('evaluationReason'):
            lines.append(f"  Evaluations Reason: {apply['evaluationReason']}")
    return lines


def format_binding_change(result: Dict[str, Any]) -> str:
    """A bind or unbind answer whose change is applied in the background (HTTP 202)."""
    lines = [result.get('message') or 'The binding change was accepted.']
    if result.get('resumed'):
        lines.append("Resumed from where it stopped.")
    lines.extend(format_binding_apply(result.get('apply') or {}))
    if result.get('waitTimedOut'):
        lines.append("Stopped waiting; the change is still being applied.")
    if (result.get('apply') or {}).get('status') == COMPLIANCE_BINDING_APPLY_APPLYING:
        lines.append(f"Follow it with: vamscli compliance bindings -d {result.get('databaseId')}"
                     " (or pass --wait)")
    return '\n'.join(lines)


def _wait_for_binding_apply(api_client: APIClient, database_id: str, accepted: Dict[str, Any],
                            timeout: int, json_output: bool) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Read the database's binding until the change `accepted` (the apply of the bind or unbind
    answer) is no longer applying, backing off over COMPLIANCE_BINDING_APPLY_POLL_DELAYS, for at
    most `timeout` seconds. Returns `(apply as last read, timed out)`. A read that names another
    change (a later one replaced it) ends the wait with that read.

    A read that answers the database not found ends the wait with the change failed: the database
    was deleted while the change was applied, so the change cannot finish, and the API records it
    `failed` with COMPLIANCE_BINDING_APPLY_DATABASE_DELETED_REASON. The apply returned is the last
    one read with that status and reason, so the failure is reported as any other is (D171). While
    the API still answers the read, the failed change it returns carries its own reason."""
    apply_id = accepted.get('applyId')
    last = accepted
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        try:
            read = api_client.get_compliance_bindings(database_id, max_items=1)
        except DatabaseNotFoundError:
            return dict(last, status=COMPLIANCE_BINDING_APPLY_FAILED,
                        reason=COMPLIANCE_BINDING_APPLY_DATABASE_DELETED_REASON), False
        apply = read.get('apply') if isinstance(read, dict) else None
        if not apply or apply.get('applyId') != apply_id:
            return apply, False
        if apply.get('status') != COMPLIANCE_BINDING_APPLY_APPLYING:
            return apply, False
        last = apply
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return apply, True
        output_status(f"  {apply.get('assetsProcessed', 0)} of {apply.get('assetCount', 0)} "
                      f"assets done; waiting...", json_output)
        delay = COMPLIANCE_BINDING_APPLY_POLL_DELAYS[
            min(attempt, len(COMPLIANCE_BINDING_APPLY_POLL_DELAYS) - 1)]
        attempt += 1
        time.sleep(min(delay, remaining))


def _report_binding_change(ctx: click.Context, api_client: APIClient, database_id: str,
                           result: Dict[str, Any], wait: bool, wait_timeout: int,
                           json_output: bool, success_message: str) -> Dict[str, Any]:
    """Print a database bind or unbind answer. A change applied in the background prints its
    apply; with `wait`, the command follows it to its end and exits non-zero unless it was applied
    (failed, stalled, or still applying when the wait timed out). It exits with `sys.exit`, as the
    other commands do: `main()` runs the CLI with `standalone_mode=False`, under which Click turns
    `ctx.exit(1)` into a return value that the entry point drops, so the installed CLI exited 0
    (D167)."""
    apply = result.get('apply') if isinstance(result, dict) else None
    if not apply:
        output_result(result, json_output, success_message=success_message)
        return result
    timed_out = False
    if wait and apply.get('status') == COMPLIANCE_BINDING_APPLY_APPLYING:
        output_status("Waiting for the binding change to be applied...", json_output)
        last, timed_out = _wait_for_binding_apply(api_client, database_id, apply, wait_timeout,
                                                  json_output)
        if last and last.get('applyId') == apply.get('applyId'):
            apply = last
            result = dict(result, apply=last, applyStatus=last.get('status'))
        if timed_out:
            result = dict(result, waitTimedOut=True)
    status = apply.get('status')
    if not wait:
        message = "✓ The binding change was accepted and is being applied."
    elif status == COMPLIANCE_BINDING_APPLY_APPLIED:
        message = "✓ The binding change was applied."
    elif status == COMPLIANCE_BINDING_APPLY_FAILED:
        message = "✗ The binding change failed."
    elif status == COMPLIANCE_BINDING_APPLY_STALLED:
        message = "✗ The binding change stopped; run the same command again to resume it."
    else:
        message = (f"✗ Stopped waiting after {wait_timeout} s; the binding change is still being "
                   "applied.")
    output_result(result, json_output, success_message=message, cli_formatter=format_binding_change)
    if wait and status != COMPLIANCE_BINDING_APPLY_APPLIED:
        sys.exit(1)
    return result


def format_bindings(result: Dict[str, Any]) -> str:
    overrides = result.get('assetOverrides') or []
    lines = [
        f"Database: {result.get('databaseId', 'N/A')}",
        f"Database Schema: {result.get('databaseSchema') or 'none'}",
        f"Auto Evaluate: {result.get('complianceAutoEval', False)}",
        f"Asset Overrides: {result.get('assetOverrideCount', 0)} ({len(overrides)} on this page)",
    ]
    if result.get('apply'):
        lines.extend(format_binding_apply(result['apply']))
    if overrides:
        lines.append("-" * 80)
        for override in overrides:
            lines.append(
                f"  {override.get('assetId', 'N/A')}: {override.get('schemaName', 'N/A')}"
                f" ({override.get('complianceState', 'N/A')})"
            )
    lines.extend(_next_page_lines(result))
    return '\n'.join(lines)


def format_state_record(record: Dict[str, Any]) -> str:
    lines = [
        f"Database: {record.get('databaseId', 'N/A')}",
        f"Asset: {record.get('assetId', 'N/A')}",
        f"Compliance State: {record.get('complianceState', 'unknown')}",
        f"Schema: {record.get('schemaName') or 'none'}",
        f"Schema Source: {record.get('schemaSource') or 'none'}",
    ]
    if record.get('recordedComplianceState'):
        lines.append(f"Recorded State: {record['recordedComplianceState']} (the asset is archived "
                     "and not evaluated; this state applies again once it is unarchived)")
    if record.get('assetName'):
        lines.append(f"Asset Name: {record['assetName']}")
    if record.get('lastEvaluationId'):
        lines.append(f"Last Evaluation: {record['lastEvaluationId']}")
    last_evaluated = record.get('lastEvaluatedAt') or record.get('lastEvaluationAt')
    if last_evaluated:
        lines.append(f"Last Evaluated: {last_evaluated}")
    for key, label in (
        # When the quarantine started; `updatedAt` moves with every change to the record (D137).
        ('quarantinedAt', 'Quarantined At'),
        ('quarantineReason', 'Quarantine Reason'),
        ('exceptionReason', 'Exception Reason'),
        ('exceptionGrantedBy', 'Exception Granted By'),
        ('exceptionGrantedAt', 'Exception Granted At'),
        ('exceptionSchemaName', 'Exception Schema'),
        ('exceptionSchemaVersion', 'Exception Schema Version'),
        ('updatedAt', 'Updated'),
    ):
        if record.get(key):
            lines.append(f"{label}: {record[key]}")
    # The failed pipeline rules' execution failure reasons, present for a caller who may read those
    # executions; any other caller's entries name the rule and the execution only.
    for failure in record.get('quarantineExecutionFailures') or []:
        reason = failure.get('executionFailureReason')
        if reason:
            lines.append(f"Execution Failure ({failure.get('ruleName', 'N/A')}, execution "
                         f"{failure.get('executionId', 'N/A')}): {reason}")
    if record.get('awaitingReevaluationSince'):
        schema = record.get('awaitingReevaluationSchema')
        if record.get('awaitingReevaluationReason') == 'evaluationPending':
            under = f" under {schema}" if schema else ""
            lines.append(f"Awaiting Re-evaluation: an evaluation{under} is waiting for its pipeline "
                         "rules (the quarantine holds until its verdict)")
        else:
            lines.append(
                f"Awaiting Re-evaluation: under {schema} (the schema binding changed; the "
                "quarantine holds until an evaluation decides it)" if schema else
                "Awaiting Re-evaluation: the schema binding was removed (the quarantine holds "
                "until the asset is released or a schema is bound and evaluates it)")
    last_status = record.get('lastEvaluationStatus')
    if last_status == 'error':
        lines.append("Last Evaluation Status: error (no verdict; the compliance state is the one "
                     "held before that evaluation)")
    elif last_status:
        lines.append(f"Last Evaluation Status: {last_status}")
    return '\n'.join(lines)


def format_database_state(result: Dict[str, Any]) -> str:
    summary = result.get('summary') or {}
    assets = result.get('assets') or []
    lines = [
        f"Database: {result.get('databaseId', 'N/A')}",
        f"Tracked Assets: {result.get('totalAssets', 0)} ({len(assets)} on this page)",
        "Summary: " + ', '.join(f"{state}={count}" for state, count in summary.items()),
    ]
    if 'error' in summary:
        lines.append("  (error counts assets whose last evaluation produced no verdict; it overlays "
                     "the state buckets rather than adding to them)")
    if result.get('nameSortUnavailable'):
        lines.append("Note: sort by name is available once indexing finishes; this page is in "
                     "asset ID order.")
    if assets:
        lines.append("-" * 80)
        for record in assets:
            name = f" ({record['assetName']})" if record.get('assetName') else ""
            flag = " (evaluation error)" if record.get('lastEvaluationStatus') == 'error' else ""
            lines.append(
                f"  {record.get('assetId', 'N/A')}{name}: {record.get('complianceState', 'unknown')}"
                f"{flag} [{record.get('schemaName') or 'no schema'}]"
            )
    lines.extend(_next_page_lines(result))
    return '\n'.join(lines)


NOT_COMPLETED_LABEL = "Not completed"


def not_completed_reason(message: Any) -> str:
    """The reason of a closed evaluation, without the "Not completed:" its stored message opens with.

    The API stores a fixed message per closed reason ("Not completed: a newer evaluation of the
    asset started", "... the asset was archived", "... the asset was deleted", "... the database was
    deleted"); the result line already says "Not completed", so the words are printed once.
    """
    text = str(message or '').strip()
    prefix = NOT_COMPLETED_LABEL.lower()
    if text.lower().startswith(prefix):
        text = text[len(prefix):].lstrip(' :').strip()
    return text or 'no verdict'


def format_evaluation(result: Dict[str, Any]) -> str:
    lines = [
        f"Evaluation ID: {result.get('evaluationId', 'N/A')}",
        f"Schema: {result.get('schemaName', 'N/A')}",
    ]
    if result.get('schemaVersion') is not None:
        lines.append(f"Schema Version: {result['schemaVersion']}")
    # An evaluation closed without a verdict (a newer evaluation replaced it while a rule waited,
    # or its asset or database went away) shows why instead of a verdict.
    closed = bool(result.get('closedReason'))
    if closed:
        lines.append(f"Result: {NOT_COMPLETED_LABEL} "
                     f"({not_completed_reason(result.get('errorMessage'))})")
    elif result.get('verdict'):
        lines.append(f"Verdict: {result['verdict']}")
    if result.get('complianceState'):
        lines.append(f"Compliance State: {result['complianceState']}")
    if result.get('status') and not closed:
        lines.append(f"Status: {result['status']}")
    if result.get('evaluatedAt'):
        lines.append(f"Evaluated: {result['evaluatedAt']}")
    if result.get('executionId'):
        lines.append(f"Execution ID: {result['executionId']}")
    if result.get('exceptionApplied'):
        lines.append("Exception Applied: yes (violations recorded; the asset stays released)")
    if result.get('notApplied'):
        # A release or exception grant made after the evaluation began outranked its outcome: the
        # verdict is recorded, the asset's state is the decision's (D159).
        lines.append(result.get('notAppliedMessage')
                     or "Not applied: a decision made after this evaluation began outranks it")
    if result.get('hasRuleErrors'):
        errored = result.get('errorRules') or []
        names = f": {', '.join(errored)}" if errored else ""
        lines.append(f"Rule Errors: yes{names} (not evaluated; their enforcement did not apply)")
    pending = result.get('pipelineRulesPending')
    if isinstance(pending, int) and not isinstance(pending, bool):
        # The evaluate answer counts them.
        if pending:
            lines.append(f"Pipeline Rules Pending: {pending}")
    elif pending:
        # A history row stores the rules themselves: print their names only.
        names = pipeline_rule_names(pending)
        if names:
            label = ("Pipeline Rules Pending" if result.get('status') == 'pending_pipeline'
                     else "Pipeline Rules")
            lines.append(f"{label}: {', '.join(names)}")
    waiting = result.get('pipelineRulesWaiting')
    if waiting:
        lines.append(f"Pipeline Rules Waiting: {waiting} ({PIPELINE_RULE_WAITING_TEXT}; "
                     "they start when it ends)")
    for rule_name in waiting_rule_names(result):
        lines.append(f"Waiting: {rule_name} ({PIPELINE_RULE_WAITING_TEXT})")
    rule_results = result.get('ruleResults') or []
    if isinstance(rule_results, str):
        try:
            rule_results = json.loads(rule_results)
        except json.JSONDecodeError:
            rule_results = []
    if rule_results:
        lines.append("Rule Results:")
        for rule in rule_results:
            # An errored rule was not evaluated: it is marked apart from a rule that failed.
            if rule.get('status') == 'error':
                mark, suffix = '!', ' (error)'
            else:
                mark, suffix = ('✓' if rule.get('passed') else '✗'), ''
            line = f"  {mark} {rule.get('ruleName', 'N/A')} [{rule.get('enforcement', 'N/A')}]{suffix}"
            if rule.get('message'):
                line += f": {rule['message']}"
            lines.append(line)
            # Why the rule's execution failed, for a caller who may read that execution.
            if rule.get('executionFailureReason'):
                lines.append(f"      Execution Failure: {rule['executionFailureReason']}")
    return '\n'.join(lines)


def format_evaluations(result: Dict[str, Any]) -> str:
    evaluations = result.get('evaluations', [])
    if not evaluations:
        return "No evaluations found."
    out = [f"Found {len(evaluations)} evaluation(s), most recent first:", "-" * 80]
    for evaluation in evaluations:
        out.append(format_evaluation(evaluation))
        out.append("-" * 80)
    out.extend(_next_page_lines(result))
    return '\n'.join(out)


def format_quarantine_list(result: Dict[str, Any]) -> str:
    assets = result.get('quarantinedAssets', [])
    if not assets:
        # The route filters the page to the caller's databases after fetching it, so an empty page
        # can still carry a token.
        if result.get('NextToken'):
            return '\n'.join(["No quarantined assets on this page."] + _next_page_lines(result))
        return "No quarantined assets."
    out = [f"Found {len(assets)} quarantined asset(s) on this page:", "-" * 80]
    for record in assets:
        out.append(format_state_record(record))
        out.append("-" * 80)
    out.extend(_next_page_lines(result))
    return '\n'.join(out)


def format_cascade(cascade: Dict[str, Any]) -> str:
    # A listing row names the trigger asset as databaseId / assetId beside the stored
    # triggeredBy* attributes; a single cascade record carries the triggeredBy* attributes only.
    database_id = cascade.get('databaseId') or cascade.get('triggeredByDatabaseId', 'N/A')
    asset_id = cascade.get('assetId') or cascade.get('triggeredByAssetId', 'N/A')
    parent_name = cascade.get('assetName') or cascade.get('triggeredByAssetName')
    named = f" ({parent_name})" if parent_name else ""
    lines = [
        f"Cascade ID: {cascade.get('cascadeId', 'N/A')}",
        f"State: {cascade.get('state', 'N/A')}",
        f"Triggered By: {database_id}:{asset_id}{named}",
    ]
    for key, label in (
        ('triggerReason', 'Reason'),
        ('createdAt', 'Created'),
        ('approvalTimeoutAt', 'Approval Deadline'),
        ('approvedBy', 'Approved By'),
        ('rejectedBy', 'Rejected By'),
        ('rejectionReason', 'Rejection Reason'),
        ('abortReason', 'Abort Reason'),
        ('completedAt', 'Completed'),
    ):
        if cascade.get(key):
            lines.append(f"{label}: {cascade[key]}")
    lines.extend(_cascade_node_lines(cascade))
    return '\n'.join(lines)


def _json_field(value: Any, default: Any) -> Any:
    """A JSON-encoded cascade attribute (`nodes`, `nodeNames`, `executionOrder`) decoded; the
    default when it is absent or unreadable."""
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _unauthorized_nodes(cascade: Dict[str, Any]) -> int:
    """How many of the cascade's nodes the caller may not read (`unauthorizedNodes`): in a database
    whose compliance the caller may not read, or an asset the caller may not read. The API leaves
    them out of `nodes`, `executionOrder`, `nodeNames`, `results` and `totalNodes` and counts them
    there only. 0 when the record carries no count."""
    count = cascade.get('unauthorizedNodes')
    if isinstance(count, bool) or not isinstance(count, (int, float)) or count != count:
        return 0
    return max(0, int(count))


def _unauthorized_text(count: int) -> str:
    """'1 node you cannot access', '2 nodes you cannot access'."""
    return f"{count} {'node' if count == 1 else 'nodes'} you cannot access"


def _cascade_node_lines(cascade: Dict[str, Any]) -> List[str]:
    """The cascade's progress (`_cascade_progress`), then one line per downstream node, in
    evaluation order: its name (when the cascade recorded one), its `databaseId:assetId` key and
    its state, then how many nodes the caller cannot access, which are not listed."""
    nodes = _json_field(cascade.get('nodes'), {})
    nodes = nodes if isinstance(nodes, dict) else {}
    unauthorized = _unauthorized_nodes(cascade)
    if not nodes and not unauthorized:
        return []
    names = _json_field(cascade.get('nodeNames'), {})
    names = names if isinstance(names, dict) else {}
    order = _json_field(cascade.get('executionOrder'), [])
    keys = [key for key in order if key in nodes] if isinstance(order, list) else []
    keys += [key for key in nodes if key not in keys]
    total = cascade.get('totalNodes')
    total = int(total) if isinstance(total, (int, float)) and not isinstance(total, bool) else None
    lines = [f"Progress: {_cascade_progress(nodes, total, unauthorized)}",
             f"Nodes ({len(keys)}):"]
    for key in keys:
        name = names.get(key)
        lines.append(f"  {name + ' ' if name else ''}({key}): {nodes[key]}")
    if unauthorized:
        lines.append(f"  {_unauthorized_text(unauthorized)} "
                     f"{'is not listed: it is' if unauthorized == 1 else 'are not listed: each is'}"
                     " in a database whose compliance you may not read, or is an asset you may "
                     "not read")
    return lines


# The node states of a descendant the cascade did not evaluate, with the reason the progress line
# gives, in the order it lists them. `skipped` is a node with no bound schema or one that no longer
# exists: the API records the two alike.
CASCADE_SKIPPED_NODE_STATES = (
    ('skipped_not_authorized', 'not authorized'),
    ('skipped_archived', 'archived'),
    ('skipped', 'no bound schema or removed'),
    ('superseded', 'superseded'),
)
CASCADE_IN_FLIGHT_NODE_STATES = ('pending', 'evaluating')


def _cascade_progress(nodes: Dict[str, Any], total: Optional[int] = None,
                      unauthorized: int = 0) -> str:
    """How many of the nodes were evaluated (their state is a verdict), then the skipped ones with
    their reasons, the failed ones (`error`) and those still to go, e.g. "1 of 2 nodes evaluated;
    1 skipped: not authorized", and last the nodes the caller cannot access (`unauthorized`, the
    cascade's `unauthorizedNodes`; in neither the counts nor the total). A skipped or superseded
    node is not counted as evaluated. `total` is the cascade's `totalNodes`, the number of nodes
    when absent."""
    states = list(nodes.values())
    skipped = [(reason, states.count(state)) for state, reason in CASCADE_SKIPPED_NODE_STATES
               if states.count(state)]
    failed = states.count('error')
    in_flight = sum(states.count(state) for state in CASCADE_IN_FLIGHT_NODE_STATES)
    evaluated = len(states) - sum(count for _, count in skipped) - failed - in_flight
    parts = [f"{evaluated} of {total if total is not None else len(states)} nodes evaluated"]
    if skipped:
        reasons = (skipped[0][0] if len(skipped) == 1
                   else ', '.join(f"{count} {reason}" for reason, count in skipped))
        parts.append(f"{sum(count for _, count in skipped)} skipped: {reasons}")
    if failed:
        parts.append(f"{failed} failed")
    if in_flight:
        parts.append(f"{in_flight} remaining")
    if unauthorized:
        parts.append(_unauthorized_text(unauthorized))
    return '; '.join(parts)


# How each listing state reads in the cascade list output; the listing defaults to pending_approval.
CASCADE_STATE_PHRASES = {
    'pending_approval': 'awaiting approval',
    'executing': 'executing',
    'completed': 'completed',
    'aborted': 'aborted',
}


def _cascade_state_phrase(state: Optional[str]) -> str:
    return CASCADE_STATE_PHRASES.get(state or 'pending_approval', state or 'awaiting approval')


def format_cascade_list(result: Dict[str, Any], state: Optional[str] = None) -> str:
    phrase = _cascade_state_phrase(state)
    cascades = result.get('cascades', [])
    if not cascades:
        # The listing is filtered to the caller's databases after it is read, so an empty page can
        # still carry a token.
        if result.get('NextToken'):
            return '\n'.join(["No cascades on this page."] + _next_page_lines(result))
        return f"No cascades {phrase}."
    out = [f"Found {len(cascades)} cascade(s) {phrase}:", "-" * 80]
    for cascade in cascades:
        out.append(format_cascade(cascade))
        out.append("-" * 80)
    out.extend(_next_page_lines(result))
    return '\n'.join(out)


def format_cascade_started(result: Dict[str, Any]) -> str:
    """A cascade create / approve response: the id, the state, and how to follow an executing one."""
    lines = [
        f"  Cascade ID: {result.get('cascadeId', 'N/A')}",
        f"  State: {result.get('state', 'N/A')}",
    ]
    if result.get('state') == 'executing':
        lines.append("  The evaluations run in the background; poll "
                     f"'vamscli compliance cascade get -c {result.get('cascadeId', '<id>')}' "
                     "until the state is completed or aborted.")
    return '\n'.join(lines)


def format_audit_entries(result: Dict[str, Any]) -> str:
    entries = result.get('entries', [])
    if not entries:
        # The trail is filtered to the caller's databases after it is read, and each request reads
        # a bounded part of it, so an empty page can still carry a token.
        if result.get('NextToken'):
            return '\n'.join(["No audit entries on this page."] + _next_page_lines(result))
        return "No audit entries found."
    out = [f"Found {len(entries)} audit entr{'y' if len(entries) == 1 else 'ies'} on this page:",
           "-" * 80]
    for entry in entries:
        out.append(f"{entry.get('timestamp', 'N/A')}  {entry.get('eventType', 'N/A')}")
        out.append(f"  Asset: {entry.get('databaseId', 'N/A')}:{entry.get('assetId', 'N/A')}")
        out.append(f"  Actor: {entry.get('actor', 'N/A')}")
        if entry.get('schemaName'):
            out.append(f"  Schema: {entry['schemaName']}")
        if entry.get('previousState') or entry.get('newState'):
            out.append(f"  State: {entry.get('previousState') or '-'} -> {entry.get('newState') or '-'}")
        if entry.get('details'):
            out.append(f"  Details: {entry['details']}")
        out.append("-" * 80)
    out.extend(_next_page_lines(result))
    return '\n'.join(out)


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------

@click.group()
def compliance():
    """Compliance schema, evaluation, quarantine, cascade and audit commands.

    Schema rules take one of six rule types: pipeline, metadata, relationship, fileStructure,
    versionCoverage and assetAttributes (see 'compliance schema create --help'). A metadata rule
    validates one of four scopes: the asset's own metadata (asset, the default), each current file's
    metadata (files) or attributes (fileAttributes), or each asset link's metadata (links).
    """
    pass


# ---------------------------------------------------------------------------
# Schema sub-group
# ---------------------------------------------------------------------------

@compliance.group('schema')
def schema():
    """Compliance schema registry commands."""
    pass


@schema.command('list')
@click.option('-d', '--database-id', default=None,
              help='Only schemas scoped to this database, plus GLOBAL schemas')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_schemas(ctx: click.Context, database_id: Optional[str], json_output: bool):
    """List compliance schemas (the latest version of each).

    Each record carries schemaFormat: vams-rules-v1, or legacy for a row whose body is not a
    vams-rules-v1 document. A legacy schema cannot be bound, updated or evaluated; it is listed so
    it can be found and deleted with 'compliance schema delete'.

    Examples:
        vamscli compliance schema list
        vamscli compliance schema list -d my-database
        vamscli compliance schema list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status("Retrieving compliance schemas...", json_output)
    try:
        result = api_client.list_compliance_schemas(database_id=database_id)
        output_result(result, json_output, cli_formatter=format_schema_list)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@schema.command('get')
@click.option('-n', '--schema-name', required=True, help='[REQUIRED] Schema name')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get_schema(ctx: click.Context, schema_name: str, json_output: bool):
    """Get the latest version of a compliance schema, including its body.

    The record's schemaFormat is vams-rules-v1, or legacy for a body that is not a vams-rules-v1
    document (such a schema cannot be bound, updated or evaluated; delete it).

    Examples:
        vamscli compliance schema get -n cad-quality
        vamscli compliance schema get -n cad-quality --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Retrieving compliance schema '{schema_name}'...", json_output)
    try:
        result = api_client.get_compliance_schema(schema_name)
        output_result(result, json_output,
                      cli_formatter=lambda r: format_schema(r, include_body=True))
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@schema.command('create')
@click.option('-n', '--schema-name', required=True,
              help='[REQUIRED] Schema name (3-63 characters; letters, digits, hyphens, underscores)')
@click.option('--schema-file', required=True,
              help='[REQUIRED] Path to a JSON file holding the schema body, or the body inline')
@click.option('-d', '--database-id', default=None,
              help='Database the schema is scoped to; omit for a GLOBAL schema every database can bind')
@click.option('--description', default=None, help='Schema description')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create_schema(ctx: click.Context, schema_name: str, schema_file: str,
                  database_id: Optional[str], description: Optional[str], json_output: bool):
    """Register a compliance schema.

    Saving a pipeline rule needs the right to run its workflow (the execute route and a read of the
    workflow and its pipelines); without it the save is refused with "Not authorized to run the
    workflow of pipeline rule '<name>'". An update re-checks only the pipeline rules it changes.

    The schema body must be a vams-rules-v1 document: {"schemaFormat": "vams-rules-v1",
    "rules": {...}} where each rule has a ruleType (pipeline, metadata, relationship,
    fileStructure, versionCoverage or assetAttributes), an enforcement level (quarantine, warn or
    inform) and its checks. Any other body is rejected.

    A fileStructure check requires (requiredPaths) or forbids (forbiddenPaths) regular expressions
    over the asset's current file paths and bounds their number (minFiles, maxFiles). A
    versionCoverage check requires every current file, except those matching excludePaths, to be
    recorded in an asset version; it fails after every upload until a version is created. An
    assetAttributes check tests the asset's assetName or description against a matches or
    notMatches regular expression, or its tags against requiredTags, forbiddenTags or minTags.

    A metadata rule validates metadata against the metadata schema its metadataSchemaRef names
    (schemaName, and optionally databaseId: omitted, the schema is looked up in the evaluated
    asset's database, then GLOBAL; given, it must be GLOBAL or - for a database-scoped compliance
    schema - its own database, as must a pipeline rule's database ids), in one of four scopes set by "scope": "asset" (the default; the
    asset's own metadata, against an assetMetadata schema), "files" or "fileAttributes" (the
    metadata or attributes of each current, non-archived file, against a fileMetadata or
    fileAttribute schema; the schema's file type restriction applies, then the rule's optional
    includePaths and excludePaths regular expressions), or "links" (the metadata of each asset link,
    against an assetLinkMetadata schema, narrowed by the optional linkDirection - parents, children
    or related - and relationshipType - parentChild or related). Every file or link in scope must
    pass; none in scope is a pass. A schema that is missing, or of another entity type than the
    scope reads, records the rule as an error rather than a pass.

    A pipeline rule names the workflow it executes and the pipeline whose measurements its checks
    read under pipelineRef: databaseId (the workflow's database), workflowId, pipelineDatabaseId,
    pipelineId and an optional templateId. Its inputFiles selects which of the asset's files the
    workflow receives: mode "matching" (the default) lists the asset's files, applies the
    workflow's and the pipeline's input-file filters and the rule's own "filter" globs; mode
    "wholeAsset" sends the asset root and is accepted only by a workflow that allows whole-asset
    selection; mode "explicit" sends the listed asset-relative "keys", every one of which must
    exist. A workflow that takes exactly one input file needs the selection to resolve to exactly
    one file, otherwise the rule records an error. A pipeline that writes no
    compliance-output.json is checked on the derived execution_success and
    processing_duration_seconds measurements only.

    Registering a name that already exists writes a new version of that schema.

    Examples:
        vamscli compliance schema create -n cad-quality --schema-file cad-quality.json
        vamscli compliance schema create -n cad-quality --schema-file cad-quality.json -d my-database
        vamscli compliance schema create -n cad-quality --schema-file cad-quality.json --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    try:
        schema_data: Dict[str, Any] = {
            'schemaName': schema_name,
            'schemaBody': load_schema_body(schema_file),
        }
        if database_id:
            schema_data['databaseId'] = database_id
        if description is not None:
            schema_data['description'] = description

        output_status(f"Registering compliance schema '{schema_name}'...", json_output)
        result = api_client.create_compliance_schema(schema_data)
        output_result(result, json_output, success_message="✓ Compliance schema registered.",
                      cli_formatter=format_schema_write)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@schema.command('update')
@click.option('-n', '--schema-name', required=True, help='[REQUIRED] Schema to update')
@click.option('--schema-file', default=None,
              help='Path to a JSON file holding the new schema body, or the body inline')
@click.option('-d', '--database-id', default=None,
              help='Re-scope the schema to this database, or GLOBAL')
@click.option('--description', default=None, help='New description')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def update_schema(ctx: click.Context, schema_name: str, schema_file: Optional[str],
                  database_id: Optional[str], description: Optional[str], json_output: bool):
    """Write a new version of a compliance schema.

    At least one of --schema-file, --database-id or --description must be given. The schema body
    is replaced wholesale, not merged, and must be a vams-rules-v1 document like the one
    'compliance schema create' takes (its pipeline rules select their input files through
    inputFiles, its metadata rules their scope through scope). Assets bound to the schema keep
    their state until the next evaluation or a 'compliance sweep'; an exception granted against
    the previous version is superseded by that evaluation.

    Examples:
        vamscli compliance schema update -n cad-quality --schema-file cad-quality-v2.json
        vamscli compliance schema update -n cad-quality --description "Tightened tolerances"
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    if schema_file is None and database_id is None and description is None:
        raise click.ClickException(
            "At least one of --schema-file, --database-id or --description must be provided"
        )
    try:
        update_data: Dict[str, Any] = {}
        if schema_file is not None:
            update_data['schemaBody'] = load_schema_body(schema_file)
        if database_id is not None:
            update_data['databaseId'] = database_id
        if description is not None:
            update_data['description'] = description

        output_status(f"Updating compliance schema '{schema_name}'...", json_output)
        result = api_client.update_compliance_schema(schema_name, update_data)
        output_result(result, json_output, success_message="✓ Compliance schema updated.",
                      cli_formatter=format_schema_write)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@schema.command('delete')
@click.option('-n', '--schema-name', required=True, help='[REQUIRED] Schema to remove')
@click.option('--confirm', is_flag=True, help='Confirm schema removal')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete_schema(ctx: click.Context, schema_name: str, confirm: bool, json_output: bool):
    """Remove a compliance schema that is not bound to any database or asset.

    ⚠️  Every version of the schema is removed and cannot be recovered. Unbind it first
    ('compliance unbind') if it is still in use; the API refuses to remove a bound schema.

    The --confirm flag is required.

    Examples:
        vamscli compliance schema delete -n cad-quality --confirm
        vamscli compliance schema delete -n cad-quality --confirm --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    try:
        if not confirm:
            if json_output:
                output_result({
                    "error": "Confirmation required",
                    "message": "Compliance schema removal requires the --confirm flag",
                    "schemaName": schema_name,
                }, json_output=True)
                sys.exit(1)
            click.secho("⚠️  Compliance schema removal requires explicit confirmation!",
                        fg='yellow', bold=True)
            click.echo("Every version of the schema will be removed.")
            click.echo("Use --confirm flag to proceed with schema removal.")
            raise click.ClickException("Confirmation required for compliance schema removal")

        output_status(f"Removing compliance schema '{schema_name}'...", json_output)
        result = api_client.delete_compliance_schema(schema_name)
        output_result(result, json_output, success_message="✓ Compliance schema removed.",
                      cli_formatter=lambda r: f"  Schema Name: {schema_name}")
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------

@compliance.command('bind')
@click.option('-n', '--schema-name', required=True, help='[REQUIRED] Schema to bind')
@click.option('-d', '--database-id', required=True,
              help='[REQUIRED] Database to bind, or the database of --asset-id')
@click.option('-a', '--asset-id', default=None,
              help='Bind this asset instead, overriding the database binding')
@click.option('--auto-eval/--no-auto-eval', 'auto_eval', default=None,
              help='Re-evaluate the database\'s assets automatically, or not (database binding '
                   'only). Omitted: a new binding turns it on, an existing binding keeps its setting')
@click.option('--wait', is_flag=True,
              help='Database binding: wait until the change is applied to every asset record '
                   '(exit code 1 when it failed, stopped, or the wait timed out)')
@click.option('--wait-timeout', type=click.IntRange(1, MAX_COMPLIANCE_BINDING_WAIT_TIMEOUT),
              default=DEFAULT_COMPLIANCE_BINDING_WAIT_TIMEOUT, show_default=True,
              help='Seconds --wait waits at most')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def bind(ctx: click.Context, schema_name: str, database_id: str, asset_id: Optional[str],
         auto_eval: Optional[bool], wait: bool, wait_timeout: int, json_output: bool):
    """Bind a compliance schema to a database, or to one asset as an override.

    A database binding marks every asset in the database (except those with their own override)
    pending evaluation. An asset binding takes precedence over the database binding for that asset.
    Only a GLOBAL schema or one scoped to the database can be bound. A legacy schema (schemaFormat
    legacy in 'schema list') is refused with 'Schema body must be a vams-rules-v1 document'.

    A database binding is applied to the asset records in the background: the command returns
    once the binding is saved, with the number of assets the change goes through, and the
    change's status (applying, stalled, applied, failed) and counts are read with 'compliance
    bindings'. --wait follows it to its end. While it is applied, another binding change of the
    database is refused; when it stopped (stalled), running the same command again resumes it.

    A quarantined asset stays quarantined until it is re-evaluated (assetsKeptQuarantined); with
    automatic evaluation on, the binding queues the evaluations (evaluationsQueued). Binding the
    schema already bound with the same setting changes nothing (changed: False).

    Examples:
        vamscli compliance bind -n cad-quality -d my-database
        vamscli compliance bind -n cad-quality -d my-database --wait
        vamscli compliance bind -n cad-quality -d my-database --wait --wait-timeout 1800
        vamscli compliance bind -n cad-quality -d my-database --no-auto-eval
        vamscli compliance bind -n cad-quality-v2 -d my-database   # keeps the auto-eval setting
        vamscli compliance bind -n strict-cad -d my-database -a my-asset
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    if asset_id and auto_eval is not None:
        raise click.ClickException(
            "--auto-eval/--no-auto-eval applies to a database binding; the asset route does not "
            "read it"
        )
    if asset_id and wait:
        raise click.ClickException(
            "--wait applies to a database binding; an asset override is written in the request")
    target = f"asset '{asset_id}'" if asset_id else f"database '{database_id}'"
    output_status(f"Binding schema '{schema_name}' to {target}...", json_output)
    try:
        result = api_client.bind_compliance_schema(
            database_id, schema_name, asset_id=asset_id,
            auto_eval=None if asset_id else auto_eval)
        unchanged = isinstance(result, dict) and result.get('changed') is False
        return _report_binding_change(
            ctx, api_client, database_id, result, wait, wait_timeout, json_output,
            "✓ Nothing changed." if unchanged else "✓ Compliance schema bound.")
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@compliance.command('unbind')
@click.option('-d', '--database-id', required=True,
              help='[REQUIRED] Database to unbind, or the database of --asset-id')
@click.option('-a', '--asset-id', default=None,
              help='Remove this asset\'s override instead, falling back to the database binding')
@click.option('--wait', is_flag=True,
              help='Database binding: wait until the removal is applied to every asset record '
                   '(exit code 1 when it failed, stopped, or the wait timed out)')
@click.option('--wait-timeout', type=click.IntRange(1, MAX_COMPLIANCE_BINDING_WAIT_TIMEOUT),
              default=DEFAULT_COMPLIANCE_BINDING_WAIT_TIMEOUT, show_default=True,
              help='Seconds --wait waits at most')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def unbind(ctx: click.Context, database_id: str, asset_id: Optional[str], wait: bool,
           wait_timeout: int, json_output: bool):
    """Remove a database's schema binding, or one asset's override.

    Unbinding a database removes the compliance records of every asset that inherited the binding,
    except a quarantined asset's, which stays quarantined until it is released or re-evaluated;
    asset-level overrides are kept. Removing an asset's override reverts it to the database binding
    and marks it pending evaluation (a quarantined one stays quarantined until re-evaluated).

    A database removal is applied to the asset records in the background, as a bind is: the
    command returns once the binding is removed from the database, and --wait follows the change to
    its end. Running the same command again resumes a removal that stopped (stalled).

    Examples:
        vamscli compliance unbind -d my-database
        vamscli compliance unbind -d my-database --wait
        vamscli compliance unbind -d my-database -a my-asset
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    if asset_id and wait:
        raise click.ClickException(
            "--wait applies to a database binding; an asset override is removed in the request")
    target = f"asset '{asset_id}'" if asset_id else f"database '{database_id}'"
    output_status(f"Removing the schema binding from {target}...", json_output)
    try:
        result = api_client.unbind_compliance_schema(database_id, asset_id=asset_id)
        return _report_binding_change(ctx, api_client, database_id, result, wait, wait_timeout,
                                      json_output, "✓ Compliance schema binding removed.")
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@compliance.command('bindings')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('--max-items', type=click.IntRange(1, MAX_COMPLIANCE_LIST_PAGE_SIZE), default=None,
              help=f'Asset overrides per page (the API applies {DEFAULT_COMPLIANCE_LIST_PAGE_SIZE} '
                   f'when omitted; at most {MAX_COMPLIANCE_LIST_PAGE_SIZE})')
@click.option('--starting-token', default=None, help='Token for pagination (the previous page\'s NextToken)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def bindings(ctx: click.Context, database_id: str, max_items: Optional[int],
             starting_token: Optional[str], json_output: bool):
    """Show a database's schema binding and its asset-level overrides.

    assetOverrideCount is the total number of overrides; assetOverrides is one page of them, and
    the response carries a NextToken when more exist — pass it back as --starting-token. apply is
    the database's last binding change applied in the background: its status (applying, stalled,
    applied, failed), its progress and its counts.

    Examples:
        vamscli compliance bindings -d my-database
        vamscli compliance bindings -d my-database --max-items 20 --starting-token "token123"
        vamscli compliance bindings -d my-database --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Retrieving compliance bindings for database '{database_id}'...", json_output)
    try:
        result = api_client.get_compliance_bindings(
            database_id, max_items=max_items, starting_token=starting_token)
        output_result(result, json_output, cli_formatter=format_bindings)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


# ---------------------------------------------------------------------------
# Evaluation and state
# ---------------------------------------------------------------------------

@compliance.command('evaluate')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID')
@click.option('-n', '--schema-name', default=None,
              help='Schema to evaluate against (defaults to the asset\'s bound schema)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def evaluate(ctx: click.Context, database_id: str, asset_id: str, schema_name: Optional[str],
             json_output: bool):
    """Evaluate an asset against its compliance schema.

    Metadata, relationship, fileStructure, versionCoverage and assetAttributes rules are evaluated
    within the request and their results returned.
    Pipeline rules start a workflow execution and complete asynchronously; the result reports how
    many are pending, and 'compliance state' shows the final verdict once they finish. Naming a
    schema with -n evaluates against it without changing the asset's binding. While the asset
    holds an active exception against the schema evaluated, a failing verdict is recorded on the
    evaluation and the asset stays released in the 'exception' state.

    The response carries schemaVersion (the schema version evaluated), exceptionApplied and
    hasRuleErrors. A rule whose tooling failed — an input selection that did not resolve to the
    files its workflow takes, or a launch that could not start — is a rule result with status
    'error': it is not a verdict, its enforcement does not apply, and the verdict comes from the
    remaining rules (hasRuleErrors true). When every rule errored the evaluation itself is an
    error and the asset's compliance state is left unchanged, with lastEvaluationStatus 'error'
    on its record. An evaluation of an asset that has child assets opens a cascade awaiting
    approval, unless one for that asset is already pending.

    Examples:
        vamscli compliance evaluate -d my-database -a my-asset
        vamscli compliance evaluate -d my-database -a my-asset -n cad-quality --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Evaluating asset '{asset_id}'...", json_output)
    try:
        result = api_client.evaluate_asset_compliance(database_id, asset_id, schema_name=schema_name)
        output_result(result, json_output, success_message=f"✓ {result.get('message', 'Evaluated.')}",
                      cli_formatter=format_evaluation)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@compliance.command('sweep')
@click.option('-n', '--schema-name', default=None,
              help='Schema whose assets to re-evaluate (in every database it is bound to)')
@click.option('-d', '--database-id', default=None,
              help='Database whose tracked assets to re-evaluate, each against its own schema')
@click.option('--starting-token', default=None,
              help='Continue an earlier sweep from the NextToken it printed')
@click.option('--no-follow', is_flag=True,
              help='Make one call only and print the NextToken to continue with')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def sweep(ctx: click.Context, schema_name: Optional[str], database_id: Optional[str],
          starting_token: Optional[str], no_follow: bool, json_output: bool):
    """Re-evaluate every asset bound to a schema (-n), or every tracked asset of a database (-d).

    Give exactly one of -n and -d. With -n, run after 'compliance schema update' so existing
    assets are checked against the new version; bound assets the caller is not authorized to
    evaluate are counted as skipped and never listed. With -d, every asset of that database with a
    compliance record is evaluated against the schema it is bound to (the database binding or its
    own override) and no other database is touched: the web overview's Evaluate All. Assets
    awaiting evaluation are evaluated first. One API call evaluates a bounded number of assets;
    the command calls again with the response's NextToken until every asset has been evaluated
    once (--no-follow makes one call and prints the token; --starting-token continues from one).
    An asset whose evaluation found a newer one of it already started is listed as superseded and
    counted: its state is that newer evaluation's.

    Examples:
        vamscli compliance sweep -n cad-quality
        vamscli compliance sweep -d my-database
        vamscli compliance sweep -d my-database --no-follow
        vamscli compliance sweep -d my-database --starting-token "token123"
        vamscli compliance sweep -n cad-quality --json-output
    """
    if bool(schema_name) == bool(database_id):
        raise click.UsageError("Give exactly one of -n/--schema-name and -d/--database-id")
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    if database_id:
        output_status(f"Evaluating the tracked assets of database '{database_id}'...", json_output)
    else:
        output_status(f"Sweeping assets bound to schema '{schema_name}'...", json_output)
    try:
        result = _follow_sweep(
            lambda token: (api_client.evaluate_database_compliance(database_id, starting_token=token)
                           if database_id
                           else api_client.sweep_compliance_schema(schema_name, starting_token=token)),
            starting_token, follow=not no_follow, json_output=json_output)

        def _fmt(r):
            triggered = r.get('assetsTriggered') or []
            lines = [f"  Assets Triggered: {len(triggered)}"]
            if r.get('calls', 1) > 1:
                lines[0] += f" (in {r['calls']} calls)"
            lines.extend(f"    {t.get('databaseId', 'N/A')}:{t.get('assetId', 'N/A')}"
                         + (" (superseded)" if t.get('superseded') else "")
                         for t in triggered)
            if r.get('superseded'):
                lines.append(f"  Superseded (a newer evaluation had started): {r['superseded']}")
            if 'skippedArchived' in r or 'skippedNotAuthorized' in r:
                if r.get('skippedNotAuthorized'):
                    lines.append(f"  Skipped (not authorized to evaluate): {r['skippedNotAuthorized']}")
                if r.get('skippedArchived'):
                    lines.append(f"  Skipped (archived, not evaluated): {r['skippedArchived']}")
            elif r.get('skipped'):
                # A server that does not break the count down skips for authorization only.
                lines.append(f"  Skipped (not authorized to evaluate): {r['skipped']}")
            if r.get('registered'):
                lines.append(f"  Registered under the database binding: {r['registered']}")
            if r.get('withoutSchema'):
                lines.append(f"  Without a bound schema (not evaluated): {r['withoutSchema']}")
            if r.get('removedOrphans'):
                lines.append(f"  Records of deleted assets removed (not evaluated): "
                             f"{r['removedOrphans']}")
            if r.get('assetsRemaining'):
                lines.append(f"  Remaining (not evaluated yet): {r['assetsRemaining']}")
            if r.get('NextToken'):
                lines.append(f"  Next token: {r['NextToken']}")
                lines.append("  Continue with --starting-token (the token continues this sweep).")
            return '\n'.join(lines)

        output_result(result, json_output, success_message=f"✓ {result.get('message', 'Sweep triggered.')}",
                      cli_formatter=_fmt)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


def _follow_sweep(call, starting_token: Optional[str], follow: bool,
                  json_output: bool) -> Dict[str, Any]:
    """Call a sweep route, following its NextToken until the sweep is done (no token), and merge
    the calls into one result: every call's `assetsTriggered`, the summed `superseded`,
    `registered` and `removedOrphans`, the last call's `skipped`, `skippedNotAuthorized`, `skippedArchived`,
    `withoutSchema` and `assetsRemaining` (each call counts
    those across the whole sweep), and `calls`. Stops early, keeping the last `NextToken`, when
    `follow` is off or a call evaluated nothing (no progress: continue later with the token)."""
    result = call(starting_token)
    triggered = list(result.get('assetsTriggered') or [])
    superseded = result.get('superseded') or 0
    registered = result.get('registered') or 0
    removed = result.get('removedOrphans') or 0
    calls = 1
    while follow and result.get('NextToken') and result.get('assetsTriggered'):
        output_status(f"  {len(triggered)} evaluated, {result.get('assetsRemaining', 0)} "
                      f"remaining; continuing...", json_output)
        result = call(result['NextToken'])
        calls += 1
        triggered.extend(result.get('assetsTriggered') or [])
        superseded += result.get('superseded') or 0
        registered += result.get('registered') or 0
        removed += result.get('removedOrphans') or 0
    if calls == 1:
        return result
    merged = dict(result, assetsTriggered=triggered, calls=calls)
    if 'superseded' in result or superseded:
        merged['superseded'] = superseded
    if 'registered' in result or registered:
        merged['registered'] = registered
    if 'removedOrphans' in result or removed:
        merged['removedOrphans'] = removed
    merged['message'] = f"Sweep triggered for {len(triggered)} assets in {calls} calls"
    return merged


@compliance.command('state')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', default=None,
              help='One asset\'s record; omit for the database overview')
@click.option('--max-items', type=click.IntRange(1, MAX_COMPLIANCE_LISTING_PAGE_SIZE), default=None,
              help=f'Asset records per page of the database overview (the API applies '
                   f'{DEFAULT_COMPLIANCE_LIST_PAGE_SIZE} when omitted; at most {MAX_COMPLIANCE_LISTING_PAGE_SIZE})')
@click.option('--starting-token', default=None,
              help='Token for pagination of the database overview (the previous page\'s NextToken)')
@click.option('--state', 'state_filter',
              type=click.Choice(['compliant', 'non_compliant', 'pending_evaluation', 'quarantined',
                                 'exception', 'unknown']),
              default=None, help='Overview: only assets in this state')
@click.option('--schema-name', default=None, help='Overview: only assets bound to this schema')
@click.option('--schema-source', type=click.Choice(['database', 'asset']), default=None,
              help='Overview: only assets whose binding is the database\'s or their own')
@click.option('--evaluation-error/--no-evaluation-error', default=None,
              help='Overview: only assets whose last evaluation errored (or did not)')
@click.option('--text', default=None,
              help='Overview: case-insensitive contains on the asset name or id')
@click.option('--sort', type=click.Choice(['assetId', 'name']), default=None,
              help='Overview: order of the rows (assetId by default)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def state(ctx: click.Context, database_id: str, asset_id: Optional[str], max_items: Optional[int],
          starting_token: Optional[str], state_filter: Optional[str], schema_name: Optional[str],
          schema_source: Optional[str], evaluation_error: Optional[bool], text: Optional[str],
          sort: Optional[str], json_output: bool):
    """Show compliance state: one asset's record, or a database's overview.

    complianceState is one of compliant, non_compliant, quarantined, exception (released under an
    active exception although the last evaluation failed), pending_evaluation or unknown, or
    archived for an archived asset, which is not evaluated while it is archived (its stored state
    is recordedComplianceState). A record also carries lastEvaluationStatus (completed,
    pending_pipeline or error): 'error' means the last evaluation produced no verdict, so
    complianceState is the state held before it. The
    database overview carries a per-state summary (one bucket per state, including exception, plus
    an error count of the assets whose last evaluation errored — an overlay on the state buckets,
    not a state) and totalAssets covering every tracked asset, and one page of their records as
    assets; the response carries a NextToken when more exist — pass it back as --starting-token.
    Archived assets are left out of the overview.
    An asset that is not tracked is reported with state 'unknown'. --max-items and
    --starting-token apply to the overview only; the single-asset route is not paged.

    The overview filters on the server (--state, --schema-name, --schema-source,
    --evaluation-error, --text) and sorts by asset id or --sort name; the summary always covers
    the whole database. A filtered page can hold fewer rows than --max-items while a NextToken is
    present: keep paging until no token is returned.

    Examples:
        vamscli compliance state -d my-database
        vamscli compliance state -d my-database --state quarantined --text turbine --sort name
        vamscli compliance state -d my-database --max-items 20 --starting-token "token123"
        vamscli compliance state -d my-database -a my-asset
        vamscli compliance state -d my-database --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    overview_filters = {
        'state': state_filter, 'schemaName': schema_name, 'schemaSource': schema_source,
        'evaluationError': None if evaluation_error is None else str(evaluation_error).lower(),
        'text': text, 'sort': sort,
    }
    if asset_id and (max_items is not None or starting_token
                     or any(value for value in overview_filters.values())):
        raise click.ClickException(
            "--max-items, --starting-token and the filters apply to the database overview; the "
            "single-asset route is not paged"
        )
    try:
        if asset_id:
            output_status(f"Retrieving compliance state for asset '{asset_id}'...", json_output)
            result = api_client.get_compliance_state(database_id, asset_id)
            output_result(result, json_output, cli_formatter=format_state_record)
        else:
            output_status(f"Retrieving compliance state for database '{database_id}'...", json_output)
            result = api_client.get_database_compliance_state(
                database_id, max_items=max_items, starting_token=starting_token,
                filters=overview_filters)
            output_result(result, json_output, cli_formatter=format_database_state)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@compliance.command('evaluations')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID')
@click.option('--max-items', type=click.IntRange(1, MAX_COMPLIANCE_EVALUATIONS_PAGE_SIZE), default=None,
              help=f'Evaluations per page (the API applies {DEFAULT_COMPLIANCE_EVALUATIONS_PAGE_SIZE} '
                   f'when omitted; at most {MAX_COMPLIANCE_EVALUATIONS_PAGE_SIZE})')
@click.option('--starting-token', default=None, help='Token for pagination (the previous page\'s NextToken)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def evaluations(ctx: click.Context, database_id: str, asset_id: str, max_items: Optional[int],
                starting_token: Optional[str], json_output: bool):
    """List an asset's evaluation history, most recent first.

    The response carries a NextToken when more evaluations exist; pass it back as
    --starting-token to read the next page.

    Examples:
        vamscli compliance evaluations -d my-database -a my-asset
        vamscli compliance evaluations -d my-database -a my-asset --max-items 10
        vamscli compliance evaluations -d my-database -a my-asset --starting-token "token123"
        vamscli compliance evaluations -d my-database -a my-asset --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Retrieving evaluations for asset '{asset_id}'...", json_output)
    try:
        result = api_client.list_compliance_evaluations(
            database_id, asset_id, max_items=max_items, starting_token=starting_token)
        output_result(result, json_output, cli_formatter=format_evaluations)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


# ---------------------------------------------------------------------------
# Quarantine sub-group
# ---------------------------------------------------------------------------

@compliance.group('quarantine')
def quarantine():
    """Quarantine listing, release and exception commands."""
    pass


@quarantine.command('list')
@click.option('--max-items', type=click.IntRange(1, MAX_COMPLIANCE_LISTING_PAGE_SIZE), default=None,
              help=f'Quarantined assets per page (the API applies {DEFAULT_COMPLIANCE_LIST_PAGE_SIZE} '
                   f'when omitted; at most {MAX_COMPLIANCE_LISTING_PAGE_SIZE})')
@click.option('--starting-token', default=None, help='Token for pagination (the previous page\'s NextToken)')
@click.option('-d', '--database-id', default=None, help='Only quarantined assets of this database')
@click.option('--text', default=None, help='Case-insensitive contains on the asset name or id')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_quarantined(ctx: click.Context, max_items: Optional[int], starting_token: Optional[str],
                     database_id: Optional[str], text: Optional[str], json_output: bool):
    """List quarantined assets across every database the caller may read.

    The response carries a NextToken when more quarantined assets exist; pass it back as
    --starting-token. Each page is filtered to the caller's databases after it is read, so a page
    can be empty while a NextToken is present — keep paging until no token is returned.

    Examples:
        vamscli compliance quarantine list
        vamscli compliance quarantine list --max-items 20 --starting-token "token123"
        vamscli compliance quarantine list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status("Retrieving quarantined assets...", json_output)
    try:
        result = api_client.list_quarantined_assets(max_items=max_items, starting_token=starting_token,
                                                    database_id=database_id, text=text)
        output_result(result, json_output, cli_formatter=format_quarantine_list)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@quarantine.command('release')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Quarantined asset ID')
@click.option('--reason', default=None, help='Reason recorded in the audit trail')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def release(ctx: click.Context, database_id: str, asset_id: str, reason: Optional[str],
            json_output: bool):
    """Release an asset from quarantine, returning it to the compliant state.

    The release is recorded in the audit trail. The next evaluation can quarantine the asset
    again; use 'quarantine exception' to keep it out of quarantine deliberately.

    Examples:
        vamscli compliance quarantine release -d my-database -a my-asset
        vamscli compliance quarantine release -d my-database -a my-asset --reason "Source data corrected"
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Releasing asset '{asset_id}' from quarantine...", json_output)
    try:
        result = api_client.release_quarantine(database_id, asset_id, reason=reason)
        output_result(result, json_output, success_message="✓ Asset released from quarantine.",
                      cli_formatter=lambda r: f"  {r.get('message', '')}")
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@quarantine.command('exception')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Quarantined asset ID')
@click.option('--reason', required=True, help='[REQUIRED] Justification recorded on the asset')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def exception(ctx: click.Context, database_id: str, asset_id: str, reason: str, json_output: bool):
    """Grant a quarantined asset an exception.

    The asset moves to the 'exception' state with the justification and the granting user
    recorded on its compliance record and in the audit trail. The exception is scoped to the
    asset's bound schema at its current version: a re-evaluation against that version records
    any violations on the evaluation but keeps the asset released, while an evaluation against
    another schema or a newer version supersedes the exception. Use 'quarantine revoke-exception'
    to end it deliberately.

    Examples:
        vamscli compliance quarantine exception -d my-database -a my-asset --reason "Legacy part, waived"
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Granting an exception for asset '{asset_id}'...", json_output)
    try:
        result = api_client.grant_quarantine_exception(database_id, asset_id, reason)
        output_result(result, json_output, success_message="✓ Exception granted.",
                      cli_formatter=lambda r: f"  Reason: {r.get('reason', reason)}\n"
                                              f"  Granted By: {r.get('grantedBy', 'N/A')}\n"
                                              f"  Compliance State: {r.get('complianceState', 'N/A')}")
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@quarantine.command('revoke-exception')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset holding an active exception')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def revoke_exception(ctx: click.Context, database_id: str, asset_id: str, json_output: bool):
    """Revoke an asset's active exception.

    The exception fields are cleared and the asset returns to the state its last evaluation's
    verdict maps to — quarantined again, with its subscribers notified, when that verdict was
    quarantined — or to 'pending_evaluation' when it has no recorded evaluation. The revocation
    is recorded in the audit trail. An asset without an active exception is refused.

    Examples:
        vamscli compliance quarantine revoke-exception -d my-database -a my-asset
        vamscli compliance quarantine revoke-exception -d my-database -a my-asset --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Revoking the exception of asset '{asset_id}'...", json_output)
    try:
        result = api_client.revoke_quarantine_exception(database_id, asset_id)
        output_result(result, json_output, success_message="✓ Exception revoked.",
                      cli_formatter=lambda r: f"  Asset: {r.get('databaseId', database_id)}:"
                                              f"{r.get('assetId', asset_id)}\n"
                                              f"  Compliance State: {r.get('complianceState', 'N/A')}")
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


# ---------------------------------------------------------------------------
# Cascade sub-group
# ---------------------------------------------------------------------------

@compliance.group('cascade')
def cascade():
    """Cascade listing, creation, approval and rejection commands."""
    pass


@cascade.command('list')
@click.option('--state', 'state_filter',
              type=click.Choice(['pending_approval', 'executing', 'completed', 'aborted']),
              default=None, help='Cascades in this state (pending_approval by default)')
@click.option('-d', '--database-id', default=None,
              help='Only cascades whose trigger asset is in this database')
@click.option('--text', default=None,
              help='Case-insensitive contains on the trigger asset\'s name or id')
@click.option('--max-items', type=click.IntRange(1, MAX_COMPLIANCE_LISTING_PAGE_SIZE), default=None,
              help=f'Cascades per page (at most {MAX_COMPLIANCE_LISTING_PAGE_SIZE})')
@click.option('--starting-token', default=None, help='Token for pagination (the previous page\'s NextToken)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_cascades(ctx: click.Context, state_filter: Optional[str], database_id: Optional[str],
                  text: Optional[str], max_items: Optional[int], starting_token: Optional[str],
                  json_output: bool):
    """List cascades (awaiting approval by default), newest first, one page per call.

    Each row carries databaseId and assetId naming the trigger asset (beside triggeredByDatabaseId
    and triggeredByAssetId), and only cascades whose trigger asset's database the caller may read
    are listed. A cascade opened automatically after the evaluation of a parent is not duplicated:
    while one for that parent is pending approval, further evaluations of it open no new one.

    The response carries a NextToken when more cascades exist; pass it back as --starting-token.
    A page can hold fewer cascades than --max-items, even none, while a NextToken is present:
    keep paging until no token is returned.

    Examples:
        vamscli compliance cascade list
        vamscli compliance cascade list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Retrieving cascades {_cascade_state_phrase(state_filter)}...", json_output)
    try:
        result = api_client.list_compliance_cascades(
            state=state_filter, database_id=database_id, text=text, max_items=max_items,
            starting_token=starting_token)
        output_result(result, json_output,
                      cli_formatter=lambda page: format_cascade_list(page, state_filter))
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@cascade.command('get')
@click.option('-c', '--cascade-id', required=True, help='[REQUIRED] Cascade ID')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get_cascade(ctx: click.Context, cascade_id: str, json_output: bool):
    """Get a cascade's record and state.

    An executing cascade runs in the background; poll this command until the state is completed
    or aborted (a rejected cascade carries its rejectionReason, any other aborted cascade its
    abortReason). Downstream nodes in a database whose compliance you may not read, or assets
    you may not read, are not listed; the progress line counts them ("1 node you cannot
    access").

    Examples:
        vamscli compliance cascade get -c 3f0c...-...
        vamscli compliance cascade get -c 3f0c...-... --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Retrieving cascade '{cascade_id}'...", json_output)
    try:
        result = api_client.get_compliance_cascade(cascade_id)
        output_result(result, json_output, cli_formatter=format_cascade)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@cascade.command('create')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database of the triggering asset')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset whose dependents to re-evaluate')
@click.option('--reason', default=None, help='Trigger reason recorded on the cascade')
@click.option('--no-approval', is_flag=True,
              help='Start executing at once instead of waiting for approval')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create_cascade(ctx: click.Context, database_id: str, asset_id: str, reason: Optional[str],
                   no_approval: bool, json_output: bool):
    """Create a cascade that re-evaluates the dependents of an asset.

    By default the cascade waits in pending_approval for 'cascade approve' or 'cascade reject';
    an unapproved cascade expires after the approval timeout. With --no-approval it is created in
    the executing state and the evaluations run in the background: the command returns the
    cascadeId and state at once, and 'cascade get' shows the outcome once the state is completed
    or aborted. This command always opens a new cascade; only the cascade an evaluation opens
    automatically is de-duplicated against one already pending for the same parent.

    Examples:
        vamscli compliance cascade create -d my-database -a my-asset
        vamscli compliance cascade create -d my-database -a my-asset --reason "Geometry revised" --no-approval
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Creating a cascade for asset '{asset_id}'...", json_output)
    try:
        result = api_client.create_compliance_cascade(
            database_id, asset_id, reason=reason, require_approval=not no_approval)
        output_result(result, json_output, success_message="✓ Cascade created.",
                      cli_formatter=format_cascade_started)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@cascade.command('approve')
@click.option('-c', '--cascade-id', required=True, help='[REQUIRED] Pending cascade ID')
@click.option('--reason', default=None, help='Approval reason recorded on the cascade')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def approve_cascade(ctx: click.Context, cascade_id: str, reason: Optional[str], json_output: bool):
    """Approve a pending cascade and start its execution.

    The cascade moves to the executing state and the evaluations run in the background; the
    command returns the cascadeId and state at once, and 'cascade get' shows the outcome once
    the state is completed or aborted. Only a cascade in pending_approval can be approved.

    Examples:
        vamscli compliance cascade approve -c 3f0c...-...
        vamscli compliance cascade approve -c 3f0c...-... --reason "Reviewed dependents"
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Approving cascade '{cascade_id}'...", json_output)
    try:
        result = api_client.approve_compliance_cascade(cascade_id, reason=reason)
        output_result(result, json_output, success_message="✓ Cascade approved.",
                      cli_formatter=format_cascade_started)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@cascade.command('reject')
@click.option('-c', '--cascade-id', required=True, help='[REQUIRED] Pending cascade ID')
@click.option('--reason', default=None, help='Rejection reason recorded on the cascade')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def reject_cascade(ctx: click.Context, cascade_id: str, reason: Optional[str], json_output: bool):
    """Reject a pending cascade. Only a cascade in pending_approval can be rejected.

    Examples:
        vamscli compliance cascade reject -c 3f0c...-...
        vamscli compliance cascade reject -c 3f0c...-... --reason "Dependents already re-evaluated"
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Rejecting cascade '{cascade_id}'...", json_output)
    try:
        result = api_client.reject_compliance_cascade(cascade_id, reason=reason)
        output_result(result, json_output, success_message="✓ Cascade rejected.",
                      cli_formatter=lambda r: f"  Cascade ID: {r.get('cascadeId', 'N/A')}")
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@compliance.command('audit')
@click.option('-d', '--database-id', default=None,
              help='Only entries of this database (with --asset-id: one asset\'s history)')
@click.option('-a', '--asset-id', default=None, help='With --database-id: one asset\'s history')
@click.option('--text', default=None,
              help='Case-insensitive contains on the asset name at the time, asset id or actor')
@click.option('--event-type', default=None,
              help='Only entries of this event type (global listing only)')
@click.option('--start-date', default=None, help='Earliest entry timestamp (ISO 8601)')
@click.option('--end-date', default=None, help='Latest entry timestamp (ISO 8601)')
@click.option('--max-items', type=click.IntRange(1, MAX_COMPLIANCE_AUDIT_PAGE_SIZE), default=None,
              help=f'Entries per page (the API applies {COMPLIANCE_AUDIT_DEFAULT_LIMIT} when omitted; '
                   f'at most {MAX_COMPLIANCE_AUDIT_PAGE_SIZE})')
@click.option('--limit', type=click.IntRange(1, MAX_COMPLIANCE_AUDIT_PAGE_SIZE), default=None,
              help='Alias of --max-items')
@click.option('--starting-token', default=None, help='Token for pagination (the previous page\'s NextToken)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def audit(ctx: click.Context, database_id: Optional[str], asset_id: Optional[str],
          event_type: Optional[str], start_date: Optional[str], end_date: Optional[str],
          max_items: Optional[int], limit: Optional[int], starting_token: Optional[str],
          text: Optional[str], json_output: bool):
    """Query the compliance audit trail.

    Without --database-id/--asset-id the global trail is listed, optionally narrowed to one
    --event-type (schema_bound_to_database, schema_bound_to_asset, compliance_check,
    evaluation_error, quarantine_released, exception_granted, exception_revoked,
    exception_superseded, cascade_triggered, cascade_approved, ...); the listing carries only the
    entries whose database the caller may read. With both, one asset's history is listed; that
    route has no event-type filter. An evaluation_error entry names the rules that errored
    (ruleNames) in an evaluation, or the schema that could not be loaded.

    Entries are returned one page per call, most recent first. The response carries a NextToken
    when more entries exist; pass it back as --starting-token to read the next page. A page can
    hold fewer entries than --max-items, even none, while a NextToken is present: keep paging
    until no token is returned. --limit is the same option as --max-items.

    Examples:
        vamscli compliance audit
        vamscli compliance audit --event-type quarantine_released --max-items 100
        vamscli compliance audit --start-date 2026-09-01T00:00:00Z --end-date 2026-09-30T23:59:59Z
        vamscli compliance audit --starting-token "token123"
        vamscli compliance audit -d my-database -a my-asset
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    if asset_id and not database_id:
        raise click.ClickException("--asset-id requires --database-id")
    if max_items is not None and limit is not None and max_items != limit:
        raise click.ClickException(
            "--limit is an alias of --max-items; give one of them, or the same value for both"
        )
    page_size = max_items if max_items is not None else limit
    try:
        if asset_id and not event_type and not text:
            output_status(f"Retrieving the audit history for asset '{asset_id}'...", json_output)
            result = api_client.get_asset_compliance_audit(
                database_id, asset_id, start_date=start_date, end_date=end_date,
                max_items=page_size, starting_token=starting_token)
        else:
            output_status("Querying the compliance audit trail...", json_output)
            result = api_client.query_compliance_audit(
                event_type=event_type, start_date=start_date, end_date=end_date,
                max_items=page_size, starting_token=starting_token, database_id=database_id,
                asset_id=asset_id, text=text)
        output_result(result, json_output, cli_formatter=format_audit_entries)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)
