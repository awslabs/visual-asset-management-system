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
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.validators import validate
from common.resourceNames import get_table_name, ResourceKeys
from common.auth.apiEvent import normalize_event
from common.dynamodb import validate_pagination_info
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from models.common import (
    APIGatewayProxyResponseV2,
    success,
    validation_error,
    authorization_error,
    internal_error,
    general_error,
    VAMSGeneralErrorResponse,
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

# Execution counts (and the default executions listing) reflect only RECENT executions — those
# started within this many days. Keeps the count meaningful over time and bounds the COUNT query.
EXECUTION_COUNT_LOOKBACK_DAYS = 90

OBJECT_TYPE_WORKFLOW = "workflow"
OBJECT_TYPE_PIPELINE = "pipeline"
GLOBAL_DATABASE = "GLOBAL"


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
    breaks the workflow listing."""
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

def _item_to_response(item, triggers=None, warnings=None, execution_count=None):
    return WorkflowResponseModel(
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
    return {
        "MaxItems": int(query_params["maxItems"]),
        "PageSize": int(query_params["pageSize"]),
        "StartingToken": query_params["startingToken"],
    }


def _filtered_page(page_iterator, include_archived, claims_and_roles):
    items = []
    for item in page_iterator.get("Items", []):
        if not include_archived and item.get("archived"):
            continue
        if _enforce_workflow(claims_and_roles, item, "GET"):
            # Execution count is a bounded COUNT query per authorized workflow on this page
            # (page size caps the fan-out, so no unbounded N+1). Best-effort — None on failure.
            count = _execution_count(item.get("databaseId", ""), item.get("workflowId", ""))
            items.append(_item_to_response(item, execution_count=count))
    result = GetWorkflowsResponseModel(Items=items)
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
    return _filtered_page(page_iterator, include_archived, claims_and_roles)


def get_database_workflows(database_id, query_params, include_archived, claims_and_roles):
    paginator = dynamodb.meta.client.get_paginator("query")
    page_iterator = paginator.paginate(
        TableName=workflow_table_name,
        KeyConditionExpression=Key("databaseId").eq(database_id),
        PaginationConfig=_pagination_config(query_params),
    ).build_full_result()
    return _filtered_page(page_iterator, include_archived, claims_and_roles)


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


def create_workflow(database_id, request, username, claims_and_roles):
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
        return validation_error(body={"message": f"Workflow {workflow_id} already exists"})

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
    return success(body={"message": _item_to_response(record, warnings=warnings).dict()})


def update_workflow(database_id, workflow_id, request, username, claims_and_roles):
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
    item["dateModified"] = pr.iso_now()
    item["modifiedBy"] = username

    # Save-consistency validation against the (possibly updated) pipeline set, BEFORE persisting or
    # (re)deploying — block on hard errors (e.g. a stored pipeline archived after it was added).
    if ref_records is not None:
        pipeline_records = [rec for _ref, rec in ref_records]
    else:
        pipeline_records = _resolve_snapshot_pipeline_records(item)
    errors, warnings = _save_validation(item.get("systemConfig", {}), pipeline_records)
    if errors:
        return validation_error(body={"message": {"saveErrors": errors}})

    # Regenerate the state machine when the pipeline set changed. Persist the refreshed jobNames on
    # the record so the execute handler's output-path reconstruction stays in parity (the per-ref
    # stable ASL state name is left untouched).
    if ref_records is not None:
        workflow_arn, job_names = workflowAsl.deploy_state_machine(
            database_id, workflow_id, ref_records, existing_arn=item.get("workflow_arn", ""))
        item["workflow_arn"] = workflow_arn or item.get("workflow_arn", "")
        item["jobNames"] = job_names or []

    _workflow_table().put_item(Item=item)
    return success(body={"message": _item_to_response(item, warnings=warnings).dict()})


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


def archive_workflow(database_id, workflow_id, username, claims_and_roles):
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
                return success(body={"message": _item_to_response(item, triggers=triggers).dict()})
            if database_id:
                return success(body={"message": get_database_workflows(
                    database_id, query_parameters, include_archived, claims_and_roles).dict()})
            return success(body={"message": get_all_workflows(
                query_parameters, include_archived, claims_and_roles).dict()})

        if method == "POST":
            if not database_id:
                return validation_error(body={"message": "databaseId required to create a workflow"}, event=event)
            request = CreateWorkflowRequestModel(**json.loads(event.get("body") or "{}"))
            return create_workflow(database_id, request, username, claims_and_roles)

        if method == "PUT":
            if not workflow_id:
                return validation_error(body={"message": "workflowId required to update a workflow"}, event=event)
            request = UpdateWorkflowRequestModel(**json.loads(event.get("body") or "{}"))
            return update_workflow(database_id, workflow_id, request, username, claims_and_roles)

        if method == "DELETE":
            if not workflow_id:
                return validation_error(body={"message": "workflowId required to archive a workflow"}, event=event)
            return archive_workflow(database_id, workflow_id, username, claims_and_roles)

        return authorization_error(body={"message": "Method not allowed"})

    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={"message": str(v)}, event=event)
    except json.JSONDecodeError:
        return validation_error(body={"message": "Invalid JSON in request body"}, event=event)
    except ValueError as ve:
        logger.exception(f"Validation error: {ve}")
        return validation_error(body={"message": str(ve)}, event=event)
    except Exception as e:
        logger.exception(f"Unhandled error in workflowService: {e}")
        return internal_error(event=event)
