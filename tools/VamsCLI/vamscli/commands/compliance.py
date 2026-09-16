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
from typing import Any, Dict, Optional

import click

from ..constants import (
    COMPLIANCE_AUDIT_DEFAULT_LIMIT,
    DEFAULT_COMPLIANCE_EVALUATIONS_PAGE_SIZE,
    DEFAULT_COMPLIANCE_LIST_PAGE_SIZE,
    MAX_COMPLIANCE_AUDIT_PAGE_SIZE,
    MAX_COMPLIANCE_EVALUATIONS_PAGE_SIZE,
    MAX_COMPLIANCE_LIST_PAGE_SIZE,
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


def _handle_compliance_error(e: Exception, json_output: bool) -> None:
    """Report a compliance business-logic error and end the command.

    `output_error` exits the process in JSON mode; the ClickException it is followed by is what
    ends the command in CLI mode.
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
    raise click.ClickException(str(e))


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


def format_schema(schema: Dict[str, Any], include_body: bool = False) -> str:
    lines = [
        f"Schema Name: {schema.get('schemaName', 'N/A')}",
        f"Scope: {schema.get('databaseId', 'N/A')}",
        f"Version: {schema.get('version', 'N/A')}",
    ]
    description = schema.get('description')
    if description:
        lines.append(f"Description: {description}")
    body = schema.get('schemaBody')
    if isinstance(body, dict):
        lines.append(f"Format: {body.get('schemaFormat', 'N/A')}")
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
    return '\n'.join(lines)


def format_bindings(result: Dict[str, Any]) -> str:
    overrides = result.get('assetOverrides') or []
    lines = [
        f"Database: {result.get('databaseId', 'N/A')}",
        f"Database Schema: {result.get('databaseSchema') or 'none'}",
        f"Auto Evaluate: {result.get('complianceAutoEval', False)}",
        f"Asset Overrides: {result.get('assetOverrideCount', 0)} ({len(overrides)} on this page)",
    ]
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
    for key, label in (
        ('assetName', 'Asset Name'),
        ('lastEvaluationId', 'Last Evaluation'),
        ('lastEvaluationAt', 'Last Evaluated'),
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
    return '\n'.join(lines)


def format_database_state(result: Dict[str, Any]) -> str:
    summary = result.get('summary') or {}
    assets = result.get('assets') or []
    lines = [
        f"Database: {result.get('databaseId', 'N/A')}",
        f"Tracked Assets: {result.get('totalAssets', 0)} ({len(assets)} on this page)",
        "Summary: " + ', '.join(f"{state}={count}" for state, count in summary.items()),
    ]
    if assets:
        lines.append("-" * 80)
        for record in assets:
            name = f" ({record['assetName']})" if record.get('assetName') else ""
            lines.append(
                f"  {record.get('assetId', 'N/A')}{name}: {record.get('complianceState', 'unknown')}"
                f" [{record.get('schemaName') or 'no schema'}]"
            )
    lines.extend(_next_page_lines(result))
    return '\n'.join(lines)


def format_evaluation(result: Dict[str, Any]) -> str:
    lines = [
        f"Evaluation ID: {result.get('evaluationId', 'N/A')}",
        f"Schema: {result.get('schemaName', 'N/A')}",
    ]
    if result.get('verdict'):
        lines.append(f"Verdict: {result['verdict']}")
    if result.get('complianceState'):
        lines.append(f"Compliance State: {result['complianceState']}")
    if result.get('status'):
        lines.append(f"Status: {result['status']}")
    if result.get('evaluatedAt'):
        lines.append(f"Evaluated: {result['evaluatedAt']}")
    if result.get('executionId'):
        lines.append(f"Execution ID: {result['executionId']}")
    if result.get('exceptionApplied'):
        lines.append("Exception Applied: yes (violations recorded; the asset stays released)")
    pending = result.get('pipelineRulesPending')
    if pending:
        lines.append(f"Pipeline Rules Pending: {pending}")
    rule_results = result.get('ruleResults') or []
    if isinstance(rule_results, str):
        try:
            rule_results = json.loads(rule_results)
        except json.JSONDecodeError:
            rule_results = []
    if rule_results:
        lines.append("Rule Results:")
        for rule in rule_results:
            mark = '✓' if rule.get('passed') else '✗'
            line = f"  {mark} {rule.get('ruleName', 'N/A')} [{rule.get('enforcement', 'N/A')}]"
            if rule.get('message'):
                line += f": {rule['message']}"
            lines.append(line)
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
    lines = [
        f"Cascade ID: {cascade.get('cascadeId', 'N/A')}",
        f"State: {cascade.get('state', 'N/A')}",
        f"Triggered By: {database_id}:{asset_id}",
    ]
    for key, label in (
        ('triggerReason', 'Reason'),
        ('createdAt', 'Created'),
        ('approvalTimeoutAt', 'Approval Deadline'),
        ('approvedBy', 'Approved By'),
        ('rejectedBy', 'Rejected By'),
        ('abortReason', 'Abort Reason'),
        ('completedAt', 'Completed'),
    ):
        if cascade.get(key):
            lines.append(f"{label}: {cascade[key]}")
    return '\n'.join(lines)


def format_cascade_list(result: Dict[str, Any]) -> str:
    cascades = result.get('cascades', [])
    if not cascades:
        return "No cascades awaiting approval."
    out = [f"Found {len(cascades)} cascade(s) awaiting approval:", "-" * 80]
    for cascade in cascades:
        out.append(format_cascade(cascade))
        out.append("-" * 80)
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
    """Compliance schema, evaluation, quarantine, cascade and audit commands."""
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

    The schema body must be a vams-rules-v1 document: {"schemaFormat": "vams-rules-v1",
    "rules": {...}} where each rule has a ruleType (pipeline, metadata or relationship), an
    enforcement level (quarantine, warn or inform) and its checks. Any other body is rejected.

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
    inputFiles). Assets bound to the schema keep their state until the next evaluation or a
    'compliance sweep'; an exception granted against the previous version is superseded by that
    evaluation.

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
@click.option('--no-auto-eval', is_flag=True,
              help='Do not re-evaluate the database\'s assets automatically (database binding only)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def bind(ctx: click.Context, schema_name: str, database_id: str, asset_id: Optional[str],
         no_auto_eval: bool, json_output: bool):
    """Bind a compliance schema to a database, or to one asset as an override.

    A database binding marks every asset in the database (except those with their own override)
    pending evaluation. An asset binding takes precedence over the database binding for that asset.
    Only a GLOBAL schema or one scoped to the database can be bound.

    Examples:
        vamscli compliance bind -n cad-quality -d my-database
        vamscli compliance bind -n cad-quality -d my-database --no-auto-eval
        vamscli compliance bind -n strict-cad -d my-database -a my-asset
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    if asset_id and no_auto_eval:
        raise click.ClickException(
            "--no-auto-eval applies to a database binding; the asset route does not read it"
        )
    target = f"asset '{asset_id}'" if asset_id else f"database '{database_id}'"
    output_status(f"Binding schema '{schema_name}' to {target}...", json_output)
    try:
        result = api_client.bind_compliance_schema(
            database_id, schema_name, asset_id=asset_id,
            auto_eval=None if asset_id else not no_auto_eval)
        output_result(result, json_output, success_message="✓ Compliance schema bound.")
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@compliance.command('unbind')
@click.option('-d', '--database-id', required=True,
              help='[REQUIRED] Database to unbind, or the database of --asset-id')
@click.option('-a', '--asset-id', default=None,
              help='Remove this asset\'s override instead, falling back to the database binding')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def unbind(ctx: click.Context, database_id: str, asset_id: Optional[str], json_output: bool):
    """Remove a database's schema binding, or one asset's override.

    Unbinding a database removes the compliance records of every asset that inherited the binding;
    asset-level overrides are kept. Removing an asset's override reverts it to the database binding
    and marks it pending evaluation.

    Examples:
        vamscli compliance unbind -d my-database
        vamscli compliance unbind -d my-database -a my-asset
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    target = f"asset '{asset_id}'" if asset_id else f"database '{database_id}'"
    output_status(f"Removing the schema binding from {target}...", json_output)
    try:
        result = api_client.unbind_compliance_schema(database_id, asset_id=asset_id)
        output_result(result, json_output, success_message="✓ Compliance schema binding removed.")
        return result
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
    the response carries a NextToken when more exist — pass it back as --starting-token.

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

    Metadata and relationship rules are evaluated within the request and their results returned.
    Pipeline rules start a workflow execution and complete asynchronously; the result reports how
    many are pending, and 'compliance state' shows the final verdict once they finish. Naming a
    schema with -n evaluates against it without changing the asset's binding. While the asset
    holds an active exception against the schema evaluated, a failing verdict is recorded on the
    evaluation and the asset stays released in the 'exception' state.

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
@click.option('-n', '--schema-name', required=True, help='[REQUIRED] Schema whose assets to re-evaluate')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def sweep(ctx: click.Context, schema_name: str, json_output: bool):
    """Re-evaluate every asset bound to a schema.

    Run after 'compliance schema update' so existing assets are checked against the new version.
    Bound assets the caller is not authorized to evaluate are counted as skipped and never listed;
    bound assets beyond the per-call cap are counted as remaining, and a repeated sweep works
    through them.

    Examples:
        vamscli compliance sweep -n cad-quality
        vamscli compliance sweep -n cad-quality --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status(f"Sweeping assets bound to schema '{schema_name}'...", json_output)
    try:
        result = api_client.sweep_compliance_schema(schema_name)

        def _fmt(r):
            triggered = r.get('assetsTriggered') or []
            lines = [f"  Assets Triggered: {len(triggered)}"]
            lines.extend(f"    {t.get('databaseId', 'N/A')}:{t.get('assetId', 'N/A')}"
                         for t in triggered)
            if r.get('skipped'):
                lines.append(f"  Skipped (not authorized to evaluate): {r['skipped']}")
            if r.get('assetsRemaining'):
                lines.append(f"  Remaining (beyond this call's cap): {r['assetsRemaining']}")
            return '\n'.join(lines)

        output_result(result, json_output, success_message=f"✓ {result.get('message', 'Sweep triggered.')}",
                      cli_formatter=_fmt)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)


@compliance.command('state')
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', default=None,
              help='One asset\'s record; omit for the database overview')
@click.option('--max-items', type=click.IntRange(1, MAX_COMPLIANCE_LIST_PAGE_SIZE), default=None,
              help=f'Asset records per page of the database overview (the API applies '
                   f'{DEFAULT_COMPLIANCE_LIST_PAGE_SIZE} when omitted; at most {MAX_COMPLIANCE_LIST_PAGE_SIZE})')
@click.option('--starting-token', default=None,
              help='Token for pagination of the database overview (the previous page\'s NextToken)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def state(ctx: click.Context, database_id: str, asset_id: Optional[str], max_items: Optional[int],
          starting_token: Optional[str], json_output: bool):
    """Show compliance state: one asset's record, or a database's overview.

    complianceState is one of compliant, non_compliant, quarantined, exception (released under an
    active exception although the last evaluation failed), pending_evaluation or unknown. The
    database overview carries a per-state summary (one bucket per state, including exception) and
    totalAssets covering every tracked asset, and one page of their records as assets; the
    response carries a NextToken when more exist — pass it back as --starting-token. An asset
    that is not tracked is reported with state 'unknown'. --max-items and --starting-token apply
    to the overview only; the single-asset route is not paged.

    Examples:
        vamscli compliance state -d my-database
        vamscli compliance state -d my-database --max-items 20 --starting-token "token123"
        vamscli compliance state -d my-database -a my-asset
        vamscli compliance state -d my-database --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    if asset_id and (max_items is not None or starting_token):
        raise click.ClickException(
            "--max-items and --starting-token apply to the database overview; the single-asset "
            "route is not paged"
        )
    try:
        if asset_id:
            output_status(f"Retrieving compliance state for asset '{asset_id}'...", json_output)
            result = api_client.get_compliance_state(database_id, asset_id)
            output_result(result, json_output, cli_formatter=format_state_record)
        else:
            output_status(f"Retrieving compliance state for database '{database_id}'...", json_output)
            result = api_client.get_database_compliance_state(
                database_id, max_items=max_items, starting_token=starting_token)
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
@click.option('--max-items', type=click.IntRange(1, MAX_COMPLIANCE_LIST_PAGE_SIZE), default=None,
              help=f'Quarantined assets per page (the API applies {DEFAULT_COMPLIANCE_LIST_PAGE_SIZE} '
                   f'when omitted; at most {MAX_COMPLIANCE_LIST_PAGE_SIZE})')
@click.option('--starting-token', default=None, help='Token for pagination (the previous page\'s NextToken)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_quarantined(ctx: click.Context, max_items: Optional[int], starting_token: Optional[str],
                     json_output: bool):
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
        result = api_client.list_quarantined_assets(max_items=max_items, starting_token=starting_token)
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
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_cascades(ctx: click.Context, json_output: bool):
    """List cascades awaiting approval.

    Each row carries databaseId and assetId naming the trigger asset (beside triggeredByDatabaseId
    and triggeredByAssetId), and only cascades whose trigger asset's database the caller may read
    are listed.

    Examples:
        vamscli compliance cascade list
        vamscli compliance cascade list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    output_status("Retrieving cascades awaiting approval...", json_output)
    try:
        result = api_client.list_compliance_cascades()
        output_result(result, json_output, cli_formatter=format_cascade_list)
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
    or aborted (an aborted cascade carries its abortReason).

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
    or aborted.

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
@click.option('-d', '--database-id', default=None, help='With --asset-id: one asset\'s history')
@click.option('-a', '--asset-id', default=None, help='With --database-id: one asset\'s history')
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
          json_output: bool):
    """Query the compliance audit trail.

    Without --database-id/--asset-id the global trail is listed, optionally narrowed to one
    --event-type (schema_bound_to_database, schema_bound_to_asset, compliance_check,
    quarantine_released, exception_granted, exception_revoked, exception_superseded,
    cascade_triggered, cascade_approved, ...); the listing carries only the entries whose database
    the caller may read. With both, one asset's history is listed; that route has no event-type
    filter.

    Entries are returned one page per call, most recent first. The response carries a NextToken
    when more entries exist; pass it back as --starting-token to read the next page. --limit is
    the same option as --max-items.

    Examples:
        vamscli compliance audit
        vamscli compliance audit --event-type quarantine_released --max-items 100
        vamscli compliance audit --start-date 2026-09-01T00:00:00Z --end-date 2026-09-30T23:59:59Z
        vamscli compliance audit --starting-token "token123"
        vamscli compliance audit -d my-database -a my-asset
    """
    # Setup/auth already validated by decorator
    api_client = _api(ctx)
    if bool(database_id) != bool(asset_id):
        raise click.ClickException(
            "--database-id and --asset-id must be given together to read one asset's history"
        )
    if asset_id and event_type:
        raise click.ClickException(
            "--event-type applies to the global audit listing; the per-asset route has no such filter"
        )
    if max_items is not None and limit is not None and max_items != limit:
        raise click.ClickException(
            "--limit is an alias of --max-items; give one of them, or the same value for both"
        )
    page_size = max_items if max_items is not None else limit
    try:
        if asset_id:
            output_status(f"Retrieving the audit history for asset '{asset_id}'...", json_output)
            result = api_client.get_asset_compliance_audit(
                database_id, asset_id, start_date=start_date, end_date=end_date,
                max_items=page_size, starting_token=starting_token)
        else:
            output_status("Querying the compliance audit trail...", json_output)
            result = api_client.query_compliance_audit(
                event_type=event_type, start_date=start_date, end_date=end_date,
                max_items=page_size, starting_token=starting_token)
        output_result(result, json_output, cli_formatter=format_audit_entries)
        return result
    except _COMPLIANCE_ERRORS as e:
        _handle_compliance_error(e, json_output)
