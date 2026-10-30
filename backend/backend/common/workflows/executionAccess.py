#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Who may read a workflow execution: the rule the execution routes enforce, in one place.

The execution detail, log and list routes (`handlers/workflows/executionService.py`) decide every
read of an execution with `execution_access_decision`, and any other route that discloses what an
execution recorded (a compliance rule result's execution failure reason) decides it the same way,
so a caller reaches an execution's recorded details through another route exactly when it could
open the execution itself.

The decision is evaluated over readers the caller supplies, so each route keeps its own reads,
memos and page budget; the record readers below take the table handle to read from. This module
reads no environment and binds no table.
"""

from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from boto3.dynamodb.conditions import Key

from common.apiRoutes import API_WORKFLOW_EXECUTION_DETAILS

# Suffix of the partition an archived asset's row moves to (assetService.archive_asset).
ARCHIVED_DATABASE_SUFFIX = "#deleted"

# The configuration row's sort key value.
CONFIGURATION_RECORD_TYPE = "configuration"


def execution_details_api_object(execution_id: str) -> Dict[str, str]:
    """The Tier-1 object `enforceAPI` evaluates for GET /workflows/executions/{executionId}/details,
    for a check made on behalf of that route from another one."""
    return {
        "object__type": "api",
        "route__path": API_WORKFLOW_EXECUTION_DETAILS.path.replace("{executionId}", execution_id),
    }


def workflow_casbin_object(definition: Optional[Dict[str, Any]], workflow_database_id: str,
                           workflow_id: str) -> Dict[str, Any]:
    """The workflow Casbin object for a run's workflow, shaped exactly as the workflow routes shape
    theirs (workflowService._workflow_casbin_object): every attribute of the definition row rides
    along so the ABAC-visible `category` and `name` are present, with `name` falling back to
    `workflowName`. A rule scoped on those fields therefore decides an execution the same way it
    decides the workflow.

    The ids from the execution's own row stay authoritative, so a definition that no longer
    resolves (the workflow was deleted while its runs remain) is still authorized on the ids
    alone."""
    definition = definition or {}
    obj = dict(definition)
    obj.update({
        "object__type": "workflow",
        "workflowId": workflow_id,
        "databaseId": workflow_database_id,
    })
    obj.setdefault("name", definition.get("workflowName", ""))
    return obj


def metadata_source_entities(config_row: Optional[Dict[str, Any]]
                             ) -> Tuple[List[str], List[Tuple[str, str]]]:
    """The entities an execution captured metadata from, from its configuration row:
    ([databaseId, ...], [(databaseId, assetId), ...]).

    The databases are the ones the caller NAMED as a metadata source (`inputMetadataDatabaseId`),
    which is the deliberate act that makes a database's own metadata part of the run. A database
    merely DERIVED from an input file's asset is deliberately excluded: reading such an execution
    already requires GET on that input asset, and gating additionally on its database would narrow
    every ordinary execution to callers holding database-level GET — `databaseMetadata` defaults on,
    so every run records its input databases. `metadataSourceDatabases` (all captured databases)
    drives the detail view, not this gate. Metadata sources are not input FILES, so they appear only
    here — never in the workflow-input rows — and a row written before metadata sources existed
    carries none of these attributes, yielding empty values."""
    row = config_row or {}
    databases = []
    seen_databases = set()
    for database_id in [row.get('inputMetadataDatabaseId', '') or '']:
        if database_id and database_id not in seen_databases:
            seen_databases.add(database_id)
            databases.append(database_id)
    assets = []
    seen = set()
    for source in row.get('metadataSourceAssets') or []:
        pair = ((source or {}).get('databaseId', ''), (source or {}).get('assetId', ''))
        if all(pair) and pair not in seen:
            seen.add(pair)
            assets.append(pair)
    return databases, assets


def read_assets_of_execution(input_assets: Iterable[Tuple[str, str]],
                             metadata_source_assets: Iterable[Tuple[str, str]]
                             ) -> List[Tuple[str, str]]:
    """The distinct assets an execution read: its input-file assets plus the assets named purely as
    metadata sources. An asset that is both appears once, in input-file order."""
    merged = list(input_assets)
    seen = set(merged)
    for pair in metadata_source_assets:
        if pair not in seen:
            seen.add(pair)
            merged.append(pair)
    return merged


def execution_access_decision(
    main_item: Dict[str, Any],
    asset_action: str,
    *,
    enforce: Callable[[Dict[str, Any], str], bool],
    workflow_object: Callable[[str, str], Dict[str, Any]],
    config_row: Callable[[], Dict[str, Any]],
    input_assets: Callable[[], List[Tuple[str, str]]],
    asset_row: Callable[[str, str], Optional[Dict[str, Any]]],
    prewarm_assets: Optional[Callable[[List[Tuple[str, str]]], Any]] = None,
    entities_within_budget: Optional[Callable[[List[Tuple[str, str]]], bool]] = None,
) -> Tuple[bool, str]:
    """The Tier-2 rule over the entities an execution actually read or wrote: workflow GET, GET on
    EVERY database whose metadata was captured, `asset_action` on EVERY distinct asset the run read,
    and `asset_action` on the asset it wrote to whenever the configuration row names one.

    Every asset is required rather than any one of them: the read paths return the metadata of all
    of them, so a caller who can reach only some must not reach the execution. The assets a run read
    are its input-file assets plus the assets named purely as metadata sources. The asset a run
    WROTE to gates it on the same footing, independently of whether the run also read anything: the
    read paths return that asset's identity, the inventory of files written into it, the metadata
    produced against it and the pipelines' results text, so it is a data-level association of the
    run either way — and the launch already required POST on it. A run that names no output asset
    (a results-only run) has none, leaving workflow GET as the sole control, which is what makes
    such a run readable at all. The output gate keys on the ids being PRESENT rather than on
    `outputLocationType`: the read paths project the ids whenever they are set, so ids-present is
    exactly the condition under which the identity is disclosed.

    An asset that no longer resolves substitutes the DATABASE it lived in, under the same action, so
    a run outlives the asset it ran against instead of becoming unreachable to everyone. That
    applies to the output asset too. A database is never removed — deleting one rewrites the record
    under a '#deleted' id — so its permissions remain answerable for as long as the execution exists.

    The readers: `enforce(obj, action)` is the caller's Casbin check; `workflow_object(databaseId,
    workflowId)` builds the workflow object (`workflow_casbin_object` over the definition row);
    `config_row()` returns the execution's configuration row and is called only once workflow GET
    has passed, so a row the caller cannot see at all does not pay for a read; `input_assets()`
    returns the distinct (databaseId, assetId) input-file assets; `asset_row(databaseId, assetId)`
    resolves an asset row, archived rows included, or None. `prewarm_assets(pairs)` (optional)
    resolves the assets in batched reads before the per-asset checks; `entities_within_budget(pairs)`
    (optional) is a list page's bound on the distinct assets it resolves, and a row past it is
    DENIED rather than authorized on assets it never resolved.

    Returns (allowed, denied_reason); denied_reason is for logging only."""
    # Workflow-level GET (the run's workflow; not modifying the workflow itself).
    workflow_obj = workflow_object(main_item.get('workflowDatabaseId', ''),
                                   main_item.get('workflowId', ''))
    if not enforce(workflow_obj, "GET"):
        return False, "workflow GET denied"

    row = config_row() or {}
    metadata_source_databases, metadata_source_assets = metadata_source_entities(row)

    # The captured database metadata is returned by the read paths, so reading the execution needs
    # GET on every database it was captured from. A database is authorized on its ids alone.
    for database_id in metadata_source_databases:
        if not enforce({"object__type": "database", "databaseId": database_id}, "GET"):
            return False, f"metadata-source database GET denied ({database_id})"

    read_assets = read_assets_of_execution(input_assets(), metadata_source_assets)
    output_database_id = row.get('outputDatabaseId', '')
    output_asset_id = row.get('outputAssetId', '')
    entity_pairs = read_assets + ([(output_database_id, output_asset_id)]
                                  if output_database_id and output_asset_id else [])
    if entities_within_budget is not None and not entities_within_budget(entity_pairs):
        return False, "per-page entity-resolution bound reached"
    if prewarm_assets is not None:
        prewarm_assets(entity_pairs)

    missing_asset_databases = set()
    for database_id, asset_id in read_assets:
        asset = asset_row(database_id, asset_id)
        if not asset:
            missing_asset_databases.add(database_id)
            continue
        asset.update({"object__type": "asset"})
        if not enforce(asset, asset_action):
            return False, f"asset {asset_action} denied ({database_id}/{asset_id})"

    if output_database_id and output_asset_id:
        output_asset = asset_row(output_database_id, output_asset_id)
        if not output_asset:
            missing_asset_databases.add(output_database_id)
        else:
            output_asset.update({"object__type": "asset"})
            if not enforce(output_asset, asset_action):
                return False, (f"output asset {asset_action} denied "
                               f"({output_database_id}/{output_asset_id})")

    # Each database whose asset the run can no longer resolve. Reaching here means every asset that
    # DOES resolve already authorized, so these are the run's only remaining data-level association.
    for database_id in sorted(missing_asset_databases):
        if not enforce({"object__type": "database", "databaseId": database_id}, asset_action):
            return False, f"deleted asset's database {asset_action} denied ({database_id})"

    return True, ""


# --- Record readers (each takes the table handle it reads) ---


def read_execution_main_row(table, execution_id: str,
                            consistent_read: bool = False) -> Optional[Dict[str, Any]]:
    """The V2 main execution row (PK workflowExecutionId), or None."""
    kwargs = {
        "KeyConditionExpression": Key('workflowExecutionId').eq(execution_id),
        "ScanIndexForward": False,
    }
    if consistent_read:
        kwargs["ConsistentRead"] = True
    rows = table.query(**kwargs).get('Items', [])
    return rows[0] if rows else None


def read_execution_input_assets(table, execution_id: str) -> List[Tuple[str, str]]:
    """Distinct (databaseId, assetId) input-file assets of an execution, from the workflow-inputs
    table (PK workflowExecutionId, one row per input file)."""
    seen = set()
    query_kwargs = {'KeyConditionExpression': Key('workflowExecutionId').eq(execution_id)}
    resp = table.query(**query_kwargs)
    while True:
        for row in resp.get('Items', []):
            pair = (row.get('databaseId', ''), row.get('assetId', ''))
            if all(pair):
                seen.add(pair)
        if 'LastEvaluatedKey' not in resp:
            break
        query_kwargs['ExclusiveStartKey'] = resp['LastEvaluatedKey']
        resp = table.query(**query_kwargs)
    return list(seen)


def read_execution_configuration_row(table, execution_id: str) -> Dict[str, Any]:
    """The execution's configuration row (PK workflowExecutionId, SK 'configuration'), or {} when it
    genuinely does not exist. A failed read raises: the row carries the metadata sources and the
    output target the read rule checks, so answering an unreadable row with {} would remove those
    checks."""
    resp = table.get_item(Key={'workflowExecutionId': execution_id,
                               'recordType': CONFIGURATION_RECORD_TYPE})
    return resp.get('Item') or {}


def read_workflow_definition(table, workflow_database_id: str, workflow_id: str) -> Dict[str, Any]:
    """A workflow definition row, or {} when the ids are empty or name no workflow."""
    if not workflow_database_id or not workflow_id:
        return {}
    resp = table.get_item(Key={'databaseId': workflow_database_id, 'workflowId': workflow_id})
    return resp.get('Item', {}) or {}


def read_asset_row_including_archived(table, database_id: str,
                                      asset_id: str) -> Optional[Dict[str, Any]]:
    """An asset row, including an ARCHIVED asset's.

    Archiving an asset is reversible and moves its row to the `databaseId + '#deleted'` partition,
    so a query of the active partition alone reports an archived asset as absent. The read rule
    treats an asset it cannot resolve as permanently gone and falls back to the asset's DATABASE,
    which is a weaker check — so resolving the archived row keeps an archived asset authorized as an
    archived record. Only a genuinely deleted asset, whose row exists in neither partition, returns
    None."""
    for partition in (database_id, f"{database_id}{ARCHIVED_DATABASE_SUFFIX}"):
        response = table.query(
            KeyConditionExpression=Key('databaseId').eq(partition) & Key('assetId').eq(asset_id),
            ScanIndexForward=False,
        )
        items = response.get('Items')
        if items:
            return items[0]
    return None
