# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workflow CRUD service.

Handles the database-scoped workflow endpoints:
  GET    /workflows                                  list all workflows (Casbin-filtered)
  POST   /database/{databaseId}/workflows            create a workflow
  GET    /database/{databaseId}/workflows            list a database's workflows
  GET    /database/{databaseId}/workflows/{workflowId}   details (+ its triggers)
  PUT    /database/{databaseId}/workflows/{workflowId}   update / enable-disable
  DELETE /database/{databaseId}/workflows/{workflowId}   archive (soft delete)

Two-tier Casbin: Tier-1 (enforceAPI) gates the route; Tier-2 (enforce on the workflow object, now
carrying category + name) gates the specific workflow, and every referenced pipeline is authorized
(GET) + database-scope-checked at create/update. Workflows are database-scoped rows (PK databaseId,
SK workflowId) in WorkflowStorageTableV2 and are never hard-deleted — DELETE sets archived=true.

Create/update assemble the Step Functions ASL from the referenced pipeline records (mapping each
pipeline's executionConfig to the shape the shared generator reads) and (re)deploy the state machine.
Create/update also return a non-fatal warnings[] array from the workflow<->pipeline save-consistency
checks (executionValidation.validate_workflow_save).
"""

import json
import os
from datetime import datetime, timezone, timedelta

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.paginate import TokenEncoder
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import ValidationError

from common.validators import validate
from common.resourceNames import get_table_name, ResourceKeys
from common.auth.apiEvent import normalize_event
from common.dynamodb import validate_pagination_info
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_actions
from models.common import (
    APIGatewayProxyResponseV2,
    success,
    validation_error,
    authorization_error,
    internal_error,
    general_error,
    VAMSGeneralErrorResponse,
    validation_error_message,
)
from models.workflows import (
    CreateWorkflowRequestModel,
    UpdateWorkflowRequestModel,
    WorkflowResponseModel,
    GetWorkflowsResponseModel,
)
from common.workflows import workflowRecords as wr
from common.workflows import pipelineRecords as pr
from common.workflows import executionValidation as ev
from common.workflows import workflowAsl

logger = safeLogger(service_name="WorkflowService")

dynamodb = boto3.resource("dynamodb")

try:
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
    pipeline_table_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    workflow_execution_table_name = get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

# GSI (PK "workflowDatabaseId:workflowId", SK executionStartDate) used to COUNT a workflow's
# executions without a scan; the SK lets the count be bounded to recent executions.
WORKFLOW_EXECUTIONS_BY_WORKFLOW_GSI = "WorkflowExecutionsByWorkflowGSI"

# The list response's executionCount is a RECENT count — executions started within this many days —
# not the workflow's lifetime total. The window is what bounds the COUNT query: an unbounded count
# pages 1 MB of index at a time, so a heavily-used workflow would cost many round trips per listed row
# and a page of 500 workflows would not complete. Every surface that shows the value labels it as this
# window.
EXECUTION_COUNT_LOOKBACK_DAYS = 90

# Ceiling on the number of workflows one list page accumulates, regardless of the caller's
# maxItems/pageSize. Each returned workflow costs one COUNT query for its executionCount, so this
# bounds the per-request query fan-out; callers read the rest of the set through NextToken.
MAX_LIST_PAGE_ITEMS = 500

# Byte budget for one list page, measured over the serialized response items. The row cap alone does
# not bound the response: a workflow row carries specifiedPipelines, systemConfig and the computed
# aggregate filters, so a page at the row cap ranges from tens of KB to past the 6 MB Lambda
# synchronous-response limit — which fails the whole request with a 502 carrying no body and no
# NextToken, leaving the caller unable to page past it. The page stops accumulating at this budget and
# continues from the last row it kept.
MAX_LIST_PAGE_BYTES = 4 * 1024 * 1024

OBJECT_TYPE_WORKFLOW = "workflow"
OBJECT_TYPE_PIPELINE = "pipeline"
GLOBAL_DATABASE = "GLOBAL"

# Page cap for the cross-database id-uniqueness lookup (see pipelineService.MAX_ID_LOOKUP_PAGES).
MAX_ID_LOOKUP_PAGES = 50


def _workflow_table():
    return dynamodb.Table(workflow_table_name)


def _triggers_table():
    return dynamodb.Table(triggers_table_name)


def _pipeline_table():
    return dynamodb.Table(pipeline_table_name)


def _execution_count(database_id, workflow_id):
    """Count of RECENT executions for one workflow (started within EXECUTION_COUNT_LOOKBACK_DAYS) via
    a COUNT query on the by-workflow GSI (no item read, no scan). The GSI sort key is
    executionStartDate, so the recency window is a key-condition range — the count stays meaningful
    as history grows and the query stays bounded. Pages through Count (DynamoDB caps a COUNT query at
    1MB of scanned index per page). Best-effort: returns None on any error so a count failure never
    breaks the workflow listing.

    This is NOT the workflow's lifetime total: a workflow last run before the window counts 0 here
    while its executions are still listable through GET /workflows/executions with an explicit
    filterStartDate. Callers presenting the number must name the window."""
    composite = f"{database_id}:{workflow_id}"
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=EXECUTION_COUNT_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        table = dynamodb.Table(workflow_execution_table_name)
        total = 0
        kwargs = {
            "IndexName": WORKFLOW_EXECUTIONS_BY_WORKFLOW_GSI,
            "KeyConditionExpression": (Key("workflowDatabaseId:workflowId").eq(composite)
                                       & Key("executionStartDate").gte(cutoff)),
            "Select": "COUNT",
        }
        while True:
            resp = table.query(**kwargs)
            total += resp.get("Count", 0)
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
        return total
    except Exception as e:
        logger.warning(f"Execution count failed for {composite}: {e}")
        return None


#######################
# Casbin helpers
#######################

def _workflow_casbin_object(item):
    obj = dict(item)
    obj["object__type"] = OBJECT_TYPE_WORKFLOW
    obj.setdefault("name", item.get("workflowName", ""))
    return obj


def _enforce_workflow(claims_and_roles, item, action):
    if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
        return CasbinEnforcer(claims_and_roles).enforce(_workflow_casbin_object(item), action)
    return False


#######################
# Pipeline resolution (for authorization, save-validation, and ASL generation)
#######################

def _get_pipeline_record(pipeline_database_id, pipeline_id):
    return _pipeline_table().get_item(
        Key={"databaseId": pipeline_database_id, "pipelineId": pipeline_id}
    ).get("Item")


def _resolve_referenced_pipelines(workflow_database_id, specified_pipelines, claims_and_roles):
    """Resolve + authorize each referenced pipeline. Returns (error_response_or_None, records) where
    records is a list of (ref, pipeline_record) in workflow order. Enforces database scope (GLOBAL
    workflow -> only GLOBAL pipelines; database workflow -> GLOBAL or same-database pipelines) and
    Tier-2 GET on each pipeline object."""
    records = []
    for ref in specified_pipelines:
        pipeline_db = ref.pipelineDatabaseId or workflow_database_id
        # Database-scope rule (mirrors V1 authorize_pipelines). Log the specific ids/databases for
        # debugging; return a generic message that does not echo caller input or other databases'
        # ids (Rule 11).
        if workflow_database_id == GLOBAL_DATABASE and pipeline_db != GLOBAL_DATABASE:
            logger.info(f"GLOBAL workflow references non-GLOBAL pipeline "
                        f"{pipeline_db}:{ref.pipelineId}")
            return validation_error(body={
                "message": "A GLOBAL workflow may only reference GLOBAL pipelines."}), None
        if (workflow_database_id != GLOBAL_DATABASE
                and pipeline_db not in (GLOBAL_DATABASE, workflow_database_id)):
            logger.info(f"Workflow database {workflow_database_id} references out-of-scope pipeline "
                        f"{pipeline_db}:{ref.pipelineId}")
            return validation_error(body={
                "message": "A workflow may only reference GLOBAL or same-database pipelines."}), None

        record = _get_pipeline_record(pipeline_db, ref.pipelineId)
        if not record:
            logger.info(f"Referenced pipeline {pipeline_db}:{ref.pipelineId} not found")
            return validation_error(status_code=404, body={
                "message": "A referenced pipeline was not found."}), None
        if record.get("archived"):
            logger.info(f"Referenced pipeline {pipeline_db}:{ref.pipelineId} is archived")
            return validation_error(body={
                "message": "A referenced pipeline is archived and cannot be added to a workflow."}), None

        # Tier-2 GET on the pipeline object (surface the flat pipelineExecutionType ABAC field).
        if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
            pobj = dict(record)
            pobj["object__type"] = OBJECT_TYPE_PIPELINE
            pr.apply_pipeline_constraint_fields(pobj, record)
            if not CasbinEnforcer(claims_and_roles).enforce(pobj, "GET"):
                return authorization_error(), None

        records.append((ref, record))
    return None, records


#######################
# Data operations
#######################

def _aggregate_fields(item, pipeline_system_configs):
    """The display-only aggregates for a workflow response. `pipeline_system_configs` is the ordered
    list of the referenced pipelines' systemConfig dicts; pass None to omit the aggregates entirely
    (rather than computing them from an empty list, which would misreport an open restriction)."""
    if pipeline_system_configs is None:
        return {}
    wsc = item.get("systemConfig", {}) or {}
    return {
        "aggregateWorkflowPipelineInputFileFilters":
            ev.aggregate_input_file_filters(wsc, pipeline_system_configs),
        "aggregateWorkflowPipelineMetadataInputs":
            ev.aggregate_metadata_inputs(wsc, pipeline_system_configs),
    }


def _item_to_response(item, triggers=None, warnings=None, execution_count=None,
                      pipeline_system_configs=None, trigger_summary=None):
    summary = trigger_summary or {}
    return WorkflowResponseModel(
        **_aggregate_fields(item, pipeline_system_configs),
        triggerCount=summary.get("triggerCount"),
        triggersEnabledCount=summary.get("triggersEnabledCount"),
        databaseId=item.get("databaseId", ""),
        workflowId=item.get("workflowId", ""),
        workflowName=item.get("workflowName", ""),
        category=item.get("category", ""),
        description=item.get("description", ""),
        workflow_arn=item.get("workflow_arn", ""),
        aslSchemaVersion=item.get("aslSchemaVersion", ""),
        specifiedPipelines=item.get("specifiedPipelines", []),
        systemConfig=item.get("systemConfig", {}),
        subDashboardUrl=item.get("subDashboardUrl", ""),
        enabled=item.get("enabled", True),
        archived=item.get("archived", False),
        dateCreated=item.get("dateCreated", ""),
        dateModified=item.get("dateModified", ""),
        createdBy=item.get("createdBy", ""),
        modifiedBy=item.get("modifiedBy", ""),
        schemaVersion=item.get("schemaVersion", 1),
        triggers=triggers,
        warnings=warnings,
        executionCount=execution_count,
    )


def get_workflow_item(database_id, workflow_id):
    return _workflow_table().get_item(
        Key={"databaseId": database_id, "workflowId": workflow_id}
    ).get("Item")


def find_workflow_id_owner(workflow_id, excluding_database_id=None):
    """The databaseId of an existing workflow carrying this workflowId, or None. Ids are unique
    across all databases (GLOBAL included); archived rows still hold their id. Queries the
    constant-partition by-date GSI rather than scanning."""
    table = _workflow_table()
    query_kwargs = {
        "IndexName": "WorkflowsByDateGSI",
        "KeyConditionExpression": Key("allListPartition").eq(wr.ALL_WORKFLOWS_LIST_PARTITION),
        "FilterExpression": Attr("workflowId").eq(workflow_id),
    }
    for _ in range(MAX_ID_LOOKUP_PAGES):
        response = table.query(**query_kwargs) or {}
        for item in response.get("Items") or []:
            owner = (item or {}).get("databaseId")
            if not owner or owner == excluding_database_id:
                continue
            return owner
        last_key = response.get("LastEvaluatedKey")
        if not isinstance(last_key, dict) or not last_key:
            return None
        query_kwargs["ExclusiveStartKey"] = last_key
    logger.warning(
        f"Workflow id uniqueness lookup stopped after {MAX_ID_LOOKUP_PAGES} pages; "
        "treating the id as free.")
    return None


def _trigger_summary(database_id, workflow_id):
    """Trigger counts for one workflow: {"triggerCount": N, "triggersEnabledCount": M}.

    Queries the triggers table by its partition key, so this is one bounded query per authorized
    workflow on the page (the same shape and fan-out bound as _execution_count). The rows are read
    rather than COUNTed because the enabled/disabled split is an item attribute, not a key — a
    workflow can carry a trigger that exists but is switched off, and the list needs to show the
    difference. A workflow has at most one trigger per triggerType, so the row set is tiny.

    Best-effort: returns None on any error so a trigger-read failure never breaks the listing.
    """
    try:
        rows = _triggers_table().query(
            KeyConditionExpression=Key("workflowDatabaseId:workflowId").eq(
                wr.workflow_composite_key(database_id, workflow_id))
        ).get("Items", [])
        return {
            "triggerCount": len(rows),
            # A row with no explicit `enabled` predates the flag and is treated as enabled, matching
            # get_workflow_triggers and the trigger-dispatch default.
            "triggersEnabledCount": sum(1 for r in rows if r.get("enabled", True)),
        }
    except Exception as e:
        logger.warning(f"Trigger count failed for {database_id}:{workflow_id} (non-fatal): {e}")
        return None


def _matches_trigger_filter(summary, has_triggers_filter):
    """Whether a workflow's trigger summary satisfies the optional hasTriggers list filter.

    `has_triggers_filter` is the validated query value: "true" keeps workflows with at least one
    ENABLED trigger, "false" keeps those with none. A workflow whose summary could not be read is
    kept rather than dropped — a best-effort count failure must not silently shorten the list.
    """
    if not has_triggers_filter:
        return True
    if summary is None:
        return True
    enabled = summary.get("triggersEnabledCount", 0)
    return enabled > 0 if has_triggers_filter == "true" else enabled == 0


def get_workflow_triggers(database_id, workflow_id):
    composite = wr.workflow_composite_key(database_id, workflow_id)
    triggers = []
    response = _triggers_table().query(
        KeyConditionExpression=Key("workflowDatabaseId:workflowId").eq(composite)
    )
    for row in response.get("Items", []):
        triggers.append({
            "triggerType": row.get("triggerType", ""),
            "triggerConfig": row.get("triggerConfig", {}),
            "enabled": row.get("enabled", True),
        })
    return triggers


def _pagination_config(query_params):
    """Boto3 paginator config from the validated query params (validate_pagination_info fills
    maxItems/pageSize/startingToken). Both sizes are clamped to MAX_LIST_PAGE_ITEMS so a caller
    cannot ask one request to accumulate the whole table (and one COUNT query per accumulated row);
    the remainder is reachable through NextToken."""
    max_items = min(int(query_params["maxItems"]), MAX_LIST_PAGE_ITEMS)
    page_size = min(int(query_params["pageSize"]), max_items)
    return {
        "MaxItems": max_items,
        "PageSize": page_size,
        "StartingToken": query_params["startingToken"],
    }


def _response_item_bytes(response_item):
    """Serialized UTF-8 size of one list item, measured on the shape the response returns so the
    budget reflects what the caller actually receives. An unserializable item measures as 0 rather
    than raising — the response serializer is where that failure belongs."""
    try:
        return len(json.dumps(response_item.dict(), default=str).encode("utf-8"))
    except Exception:
        return 0


def _list_resume_key(item, on_by_date_gsi):
    """The ExclusiveStartKey that resumes the workflow listing after this row.

    A GSI continuation names both the index's own keys and the base table's, so a token for the
    by-date listing carries all four. Returns None when the row is missing any of them, so a
    malformed row yields no token rather than one that resumes from the wrong place."""
    key = {"databaseId": item.get("databaseId"), "workflowId": item.get("workflowId")}
    if on_by_date_gsi:
        key["allListPartition"] = item.get("allListPartition")
        key["dateModified"] = item.get("dateModified")
    return key if all(key.values()) else None


def _resume_token(item, on_by_date_gsi):
    """A paginator-compatible NextToken resuming after `item`, or None. Encoded exactly as the boto3
    paginator encodes its own, so the caller passes it straight back as startingToken."""
    key = _list_resume_key(item, on_by_date_gsi)
    if not key:
        return None
    return TokenEncoder().encode({"ExclusiveStartKey": key})


def _filtered_page(page_iterator, include_archived, claims_and_roles, has_triggers="",
                   on_by_date_gsi=False):
    authorized = []
    for item in page_iterator.get("Items", []):
        if not include_archived and item.get("archived"):
            continue
        if _enforce_workflow(claims_and_roles, item, "GET"):
            authorized.append(item)

    # Referenced pipelines are read once for the whole page, AFTER authorization filtering, so an
    # unauthorized workflow's pipelines are never fetched.
    pipeline_configs = _batch_pipeline_system_configs(authorized)

    items = []
    used_bytes = 0
    budget_stopped_after = None
    for item in authorized:
        # Execution count is a bounded COUNT query per authorized workflow on this page
        # (MAX_LIST_PAGE_ITEMS caps the fan-out). Best-effort — None on failure.
        count = _execution_count(item.get("databaseId", ""), item.get("workflowId", ""))
        summary = _trigger_summary(item.get("databaseId", ""), item.get("workflowId", ""))
        # The trigger filter is applied HERE rather than in the DynamoDB query: triggers live in a
        # separate table, so "has an enabled trigger" is not expressible as a condition on the
        # workflow row. Filtering after the page is read means a filtered page can return fewer
        # items than pageSize while still reporting a NextToken — the caller pages until the token
        # is absent, exactly as with the authorization filter above.
        if not _matches_trigger_filter(summary, has_triggers):
            continue
        response_item = _item_to_response(
            item, execution_count=count, trigger_summary=summary,
            pipeline_system_configs=_ordered_pipeline_system_configs(item, pipeline_configs))
        item_bytes = _response_item_bytes(response_item)
        # The first item is always kept, whatever it measures: a page that came back empty would read
        # as "no workflows" rather than as a bound, and the caller could not page past it either.
        if items and used_bytes + item_bytes > MAX_LIST_PAGE_BYTES:
            logger.info(f"Workflow list page trimmed to {len(items)} of {len(authorized)} authorized "
                        f"workflows to stay within {MAX_LIST_PAGE_BYTES} bytes.")
            break
        items.append(response_item)
        used_bytes += item_bytes
        budget_stopped_after = item
    result = GetWorkflowsResponseModel(Items=items)
    if len(items) < len(authorized) and budget_stopped_after is not None:
        # The page stopped short of what it read, so the continuation resumes from the last row it
        # kept rather than from the query's own end — otherwise the untrimmed rows would be
        # unreachable instead of deferred. A row that cannot produce a key yields no token, and the
        # paginator's own token (if any) still applies.
        token = _resume_token(budget_stopped_after, on_by_date_gsi)
        if token:
            result.NextToken = token
            return result
    if "NextToken" in page_iterator:
        result.NextToken = page_iterator["NextToken"]
    return result


def get_all_workflows(query_params, include_archived, claims_and_roles):
    # Query the by-date GSI (constant partition, newest-first) instead of scanning the whole table.
    paginator = dynamodb.meta.client.get_paginator("query")
    page_iterator = paginator.paginate(
        TableName=workflow_table_name,
        IndexName="WorkflowsByDateGSI",
        KeyConditionExpression=Key("allListPartition").eq(wr.ALL_WORKFLOWS_LIST_PARTITION),
        ScanIndexForward=False,
        PaginationConfig=_pagination_config(query_params),
    ).build_full_result()
    return _filtered_page(page_iterator, include_archived, claims_and_roles,
                          has_triggers=query_params.get("hasTriggers", ""), on_by_date_gsi=True)


def get_database_workflows(database_id, query_params, include_archived, claims_and_roles):
    paginator = dynamodb.meta.client.get_paginator("query")
    page_iterator = paginator.paginate(
        TableName=workflow_table_name,
        KeyConditionExpression=Key("databaseId").eq(database_id),
        PaginationConfig=_pagination_config(query_params),
    ).build_full_result()
    return _filtered_page(page_iterator, include_archived, claims_and_roles,
                          has_triggers=query_params.get("hasTriggers", ""))


def _save_validation(workflow_system_config, pipeline_records):
    """Run the workflow<->pipeline save-consistency checks. Returns (errors, warnings): errors are
    hard (block the save — e.g. an archived pipeline that got archived after being added), warnings
    are non-fatal and returned on the save response."""
    pipeline_configs = [{
        "pipelineId": rec.get("pipelineId", ""),
        "pipelineDatabaseId": rec.get("databaseId", ""),
        "enabled": rec.get("enabled", True),
        "archived": rec.get("archived", False),
        "systemConfig": rec.get("systemConfig", {}),
    } for rec in pipeline_records]
    return ev.validate_workflow_save(workflow_system_config, pipeline_configs)


def create_workflow(database_id, request, username, claims_and_roles, event=None):
    # A GUID is generated when the caller does not supply a workflowId (workflowRecords has no id
    # generator of its own; the shared pipelineRecords.new_guid produces a 32-hex GUID).
    workflow_id = request.workflowId or pr.new_guid()
    system_config = request.systemConfig or wr.build_workflow_system_config()

    # Tier-2 FIRST: authorize creating this workflow object BEFORE any referenced-pipeline probe, so
    # a caller not authorized for the workflow cannot use pipeline resolution (404/400/403) as a
    # cross-domain pipeline existence oracle. The workflow Casbin object does not depend on the
    # resolved pipelines (it enforces on databaseId/category/name), so it can be built up-front.
    provisional = wr.build_workflow_record(
        database_id=database_id, workflow_id=workflow_id,
        workflow_name=request.workflowName, category=request.category or "",
        description=request.description or "", specified_pipelines=[],
        system_config=system_config, sub_dashboard_url=request.subDashboardUrl or "",
        enabled=request.enabled if request.enabled is not None else True,
        asl_schema_version=str(workflowAsl.ASL_SCHEMA_VERSION),
        created_by=username, modified_by=username,
    )
    if not _enforce_workflow(claims_and_roles, provisional, "POST"):
        return authorization_error()
    if get_workflow_item(database_id, workflow_id):
        logger.info(f"Workflow {database_id}:{workflow_id} already exists")
        return validation_error(body={"message": "A workflow with this ID already exists."})

    # Ids are unique across databases; the owning database is logged, never returned.
    other_owner = find_workflow_id_owner(workflow_id, excluding_database_id=database_id)
    if other_owner:
        logger.info(f"workflowId {workflow_id} is already in use by database {other_owner}")
        return validation_error(body={
            "message": "Workflow ID is already in use by another database. Choose a different ID."})

    # Now resolve + authorize referenced pipelines (also database-scope check).
    err, ref_records = _resolve_referenced_pipelines(
        database_id, request.specifiedPipelines, claims_and_roles)
    if err:
        return err

    record = dict(provisional)
    record["specifiedPipelines"] = [
        wr.build_specified_pipeline_ref(rec.get("databaseId", ""), rec.get("pipelineId", ""),
                                        ref.jobName or "", ref.defaultTemplateId or "")
        for ref, rec in ref_records
    ]

    # Save-consistency validation: block on hard errors, surface warnings on the response.
    pipeline_records = [rec for _ref, rec in ref_records]
    errors, warnings = _save_validation(system_config, pipeline_records)
    if errors:
        return validation_error(body={"message": {"saveErrors": errors}})

    # Generate + deploy the state machine from the referenced pipelines. The returned job names are
    # the ASL's uuid-prefixed per-pipeline output-path job names; persist them on the record so the
    # execute handler reconstructs the identical output prefixes (parity with V1's jobNames). The
    # per-ref jobName (the stable ASL state name) is left untouched.
    workflow_arn, job_names = workflowAsl.deploy_state_machine(
        database_id, workflow_id, ref_records, existing_arn="")
    record["workflow_arn"] = workflow_arn or ""
    record["jobNames"] = job_names or []

    _workflow_table().put_item(Item=record)
    # AUDIT LOG: workflow created (after the write, so a failed write is never audited as a success).
    log_actions(event or {}, "workflowCreate", {
        "databaseId": database_id,
        "workflowId": workflow_id,
        "pipelineCount": len(pipeline_records),
        "operation": "create",
    })
    return success(body={"message": _item_to_response(
        record, warnings=warnings,
        pipeline_system_configs=[rec.get("systemConfig", {}) or {}
                                 for rec in pipeline_records]).dict()})


def update_workflow(database_id, workflow_id, request, username, claims_and_roles, event=None):
    item = get_workflow_item(database_id, workflow_id)
    if not item:
        return validation_error(status_code=404, body={"message": "Workflow not found"})
    if not _enforce_workflow(claims_and_roles, item, "PUT"):
        return authorization_error()

    # If pipelines are changing, re-resolve/authorize them and regenerate the state machine.
    ref_records = None
    if request.specifiedPipelines is not None:
        err, ref_records = _resolve_referenced_pipelines(
            database_id, request.specifiedPipelines, claims_and_roles)
        if err:
            return err
        item["specifiedPipelines"] = [
            wr.build_specified_pipeline_ref(rec.get("databaseId", ""), rec.get("pipelineId", ""),
                                            ref.jobName or "", ref.defaultTemplateId or "")
            for ref, rec in ref_records
        ]

    if request.workflowName is not None:
        item["workflowName"] = request.workflowName
    if request.category is not None:
        item["category"] = request.category
        item["databaseId:category"] = f"{database_id}:{request.category}"
    if request.description is not None:
        item["description"] = request.description
    if request.systemConfig is not None:
        item["systemConfig"] = request.systemConfig
    if request.subDashboardUrl is not None:
        item["subDashboardUrl"] = request.subDashboardUrl
    if request.enabled is not None:
        item["enabled"] = request.enabled
    if request.archived is not None:
        item["archived"] = request.archived
    item["dateModified"] = pr.iso_now()
    item["modifiedBy"] = username

    # Re-enforce Tier-2 PUT on the MUTATED object: workflowName (-> the `name` constraint field) and
    # category are policy-evaluated attributes, so a caller must be authorized for the workflow it is
    # writing as well as the one it read — otherwise a scoped role could move a workflow into a
    # category/name scope its own constraints deny.
    if not _enforce_workflow(claims_and_roles, item, "PUT"):
        return authorization_error()

    # Save-consistency validation against the (possibly updated) pipeline set, BEFORE persisting or
    # (re)deploying. A hard error blocks only when the caller is supplying the pipeline set; for an
    # edit that leaves the stored set untouched (rename, description, enable/disable) the same
    # conditions — e.g. a referenced pipeline archived after it was added — are reported as warnings so
    # the workflow stays editable without a full pipeline-list replacement.
    if ref_records is not None:
        pipeline_records = [rec for _ref, rec in ref_records]
    else:
        pipeline_records = _resolve_snapshot_pipeline_records(item)
    errors, warnings = _save_validation(item.get("systemConfig", {}), pipeline_records)
    if errors:
        if ref_records is not None:
            return validation_error(body={"message": {"saveErrors": errors}})
        warnings = list(warnings) + errors

    # Regenerate the state machine when the pipeline set changed. Persist the refreshed jobNames on
    # the record so the execute handler's output-path reconstruction stays in parity (the per-ref
    # stable ASL state name is left untouched).
    if ref_records is not None:
        workflow_arn, job_names = workflowAsl.deploy_state_machine(
            database_id, workflow_id, ref_records, existing_arn=item.get("workflow_arn", ""))
        item["workflow_arn"] = workflow_arn or item.get("workflow_arn", "")
        item["jobNames"] = job_names or []

    _workflow_table().put_item(Item=item)
    # AUDIT LOG: workflow updated.
    log_actions(event or {}, "workflowUpdate", {
        "databaseId": database_id,
        "workflowId": workflow_id,
        "pipelineCount": len(pipeline_records),
        "operation": "update",
    })
    return success(body={"message": _item_to_response(
        item, warnings=warnings,
        pipeline_system_configs=[rec.get("systemConfig", {}) or {}
                                 for rec in pipeline_records]).dict()})


def _batch_pipeline_system_configs(workflow_items):
    """systemConfig for every pipeline referenced by a page of workflows, as
    {(databaseId, pipelineId): systemConfig}.

    One BatchGetItem per 100 distinct pipelines instead of a get_item per reference: a page of
    workflows referencing the same few built-in pipelines would otherwise re-read each of them once
    per workflow. Best-effort — a pipeline that no longer exists is simply absent, and callers treat a
    missing entry as an empty systemConfig."""
    wanted = set()
    for item in workflow_items:
        for ref in item.get("specifiedPipelines", []) or []:
            pipeline_id = ref.get("pipelineId")
            if pipeline_id:
                wanted.add((ref.get("pipelineDatabaseId", ""), pipeline_id))
    if not wanted:
        return {}

    configs = {}
    keys = [{"databaseId": db, "pipelineId": pid} for db, pid in sorted(wanted)]
    for start in range(0, len(keys), 100):  # BatchGetItem caps at 100 keys per request
        chunk = keys[start:start + 100]
        try:
            response = dynamodb.batch_get_item(
                RequestItems={pipeline_table_name: {"Keys": chunk}})
            for record in response.get("Responses", {}).get(pipeline_table_name, []):
                configs[(record.get("databaseId", ""), record.get("pipelineId", ""))] = \
                    record.get("systemConfig", {}) or {}
            # UnprocessedKeys are left unread rather than retried: the aggregates are display-only, so
            # a partial read degrades the hint instead of failing the list.
            if response.get("UnprocessedKeys"):
                logger.warning("Pipeline batch read left keys unprocessed; workflow filter "
                               "aggregates on this page may be incomplete")
        except Exception:
            logger.exception("Failed batch-reading pipeline configs for workflow aggregates")
    return configs


def _ordered_pipeline_system_configs(workflow_item, configs_by_key):
    """The referenced pipelines' systemConfig in workflow order, from a batch-read map."""
    return [
        configs_by_key.get(
            (ref.get("pipelineDatabaseId", ""), ref.get("pipelineId", "")), {})
        for ref in workflow_item.get("specifiedPipelines", []) or []
        if ref.get("pipelineId")
    ]


def _resolve_snapshot_pipeline_records(workflow_item):
    """Fetch the pipeline records referenced by a stored workflow's specifiedPipelines snapshot
    (best-effort; skips any that no longer exist). The full record (incl. current enabled/archived
    state) is returned so save-validation can flag a pipeline archived/disabled after it was added."""
    records = []
    for ref in workflow_item.get("specifiedPipelines", []) or []:
        rec = _get_pipeline_record(ref.get("pipelineDatabaseId", ""), ref.get("pipelineId", ""))
        if rec:
            records.append(rec)
    return records


def archive_workflow(database_id, workflow_id, username, claims_and_roles, event=None):
    item = get_workflow_item(database_id, workflow_id)
    if not item:
        return validation_error(status_code=404, body={"message": "Workflow not found"})
    if not _enforce_workflow(claims_and_roles, item, "DELETE"):
        return authorization_error()
    item["archived"] = True
    item["enabled"] = False
    item["dateModified"] = pr.iso_now()
    item["modifiedBy"] = username
    _workflow_table().put_item(Item=item)
    # AUDIT LOG: workflow archived (the delete route archives rather than removing).
    log_actions(event or {}, "workflowArchive", {
        "databaseId": database_id,
        "workflowId": workflow_id,
        "operation": "archive",
    })
    return success(body={"message": "Workflow archived"})


#######################
# Route handlers
#######################

def _validate_path_ids(path_parameters):
    checks = [("databaseId", True)]
    if "workflowId" in path_parameters:
        checks.append(("workflowId", False))
    for pid, allow_global in checks:
        (valid, message) = validate({
            pid: {"value": path_parameters.get(pid), "validator": "ID", "allowGlobalKeyword": allow_global}
        })
        if not valid:
            return message
    return None


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    normalize_event(event)
    logger.info(event)
    try:
        path_parameters = event.get("pathParameters", {}) or {}
        query_parameters = event.get("queryStringParameters", {}) or {}
        include_archived = str(query_parameters.get("includeArchived", "")).lower() in (
            "true", "1", "yes")
        # Optional list filter: keep only workflows that do ("true") or do not ("false") have an
        # ENABLED trigger. Normalized to the canonical "true"/"false"/"" here so the filter helper
        # never has to re-parse; an unrecognized value is rejected rather than silently ignored,
        # which would return an unfiltered list the caller believes was filtered.
        has_triggers_raw = str(query_parameters.get("hasTriggers", "")).strip().lower()
        if has_triggers_raw in ("true", "1", "yes"):
            query_parameters["hasTriggers"] = "true"
        elif has_triggers_raw in ("false", "0", "no"):
            query_parameters["hasTriggers"] = "false"
        elif has_triggers_raw:
            return validation_error(
                body={"message": "hasTriggers must be true or false."}, event=event)
        # Bound the default page (100): an unparameterized list returns a small page + NextToken rather
        # than accumulating up to the 10000 default into one response (Rule 15 / 6MB cap).
        validate_pagination_info(query_parameters, 100)

        method = event["requestContext"]["http"]["method"]

        claims_and_roles = request_to_claims(event)
        allowed = False
        if len(claims_and_roles["tokens"]) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                allowed = True
        if not allowed:
            return authorization_error()

        if path_parameters:
            message = _validate_path_ids(path_parameters)
            if message:
                return validation_error(body={"message": message}, event=event)

        database_id = path_parameters.get("databaseId")
        workflow_id = path_parameters.get("workflowId")
        username = claims_and_roles["tokens"][0] if claims_and_roles.get("tokens") else ""

        if method == "GET":
            if workflow_id:
                item = get_workflow_item(database_id, workflow_id)
                if not item or (item.get("archived") and not include_archived):
                    return validation_error(status_code=404, body={"message": "Workflow not found"}, event=event)
                if not _enforce_workflow(claims_and_roles, item, "GET"):
                    return authorization_error()
                triggers = get_workflow_triggers(database_id, workflow_id)
                return success(body={"message": _item_to_response(
                    item, triggers=triggers,
                    pipeline_system_configs=_ordered_pipeline_system_configs(
                        item, _batch_pipeline_system_configs([item]))).dict()})
            if database_id:
                return success(body={"message": get_database_workflows(
                    database_id, query_parameters, include_archived, claims_and_roles).dict()})
            return success(body={"message": get_all_workflows(
                query_parameters, include_archived, claims_and_roles).dict()})

        if method == "POST":
            if not database_id:
                return validation_error(body={"message": "databaseId required to create a workflow"}, event=event)
            request = CreateWorkflowRequestModel(**json.loads(event.get("body") or "{}"))
            return create_workflow(database_id, request, username, claims_and_roles, event)

        if method == "PUT":
            if not workflow_id:
                return validation_error(body={"message": "workflowId required to update a workflow"}, event=event)
            request = UpdateWorkflowRequestModel(**json.loads(event.get("body") or "{}"))
            return update_workflow(database_id, workflow_id, request, username, claims_and_roles, event)

        if method == "DELETE":
            if not workflow_id:
                return validation_error(body={"message": "workflowId required to archive a workflow"}, event=event)
            return archive_workflow(database_id, workflow_id, username, claims_and_roles, event)

        return authorization_error(body={"message": "Method not allowed"})

    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={"message": str(v)}, event=event)
    except json.JSONDecodeError:
        return validation_error(body={"message": "Invalid JSON in request body"}, event=event)
    # pydantic's ValidationError SUBCLASSES ValueError, so without this arm ABOVE the one
    # below a model-validation failure is caught there and str()'d whole into the response —
    # leaking the model class name and pydantic's error taxonomy (backend Rule 11). Placing it
    # after the ValueError arm would make it dead code.
    except ValidationError as ve:
        logger.exception(f"Validation error: {ve}")
        return validation_error(body={"message": validation_error_message(ve)}, event=event)
    except ValueError as ve:
        logger.exception(f"Validation error: {ve}")
        return validation_error(body={"message": str(ve)}, event=event)
    except Exception as e:
        logger.exception(f"Unhandled error in workflowService: {e}")
        return internal_error(event=event)
