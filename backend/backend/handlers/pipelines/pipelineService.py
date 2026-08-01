# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pipeline CRUD service.

Handles the database-scoped pipeline endpoints:
  GET    /pipelines                                  list all pipelines (Casbin-filtered)
  POST   /database/{databaseId}/pipelines            create a pipeline
  GET    /database/{databaseId}/pipelines            list a database's pipelines
  GET    /database/{databaseId}/pipelines/{pipelineId}    pipeline details (+ its templates)
  PUT    /database/{databaseId}/pipelines/{pipelineId}    update / enable-disable
  DELETE /database/{databaseId}/pipelines/{pipelineId}    archive (soft delete)

Two-tier Casbin: Tier-1 (enforceAPI) gates the route; Tier-2 (enforce on the pipeline object, now
carrying category + name) gates the specific pipeline. Pipelines are database-scoped rows
(PK databaseId, SK pipelineId) in PipelineStorageTableV2 and are never hard-deleted — DELETE sets
archived=true. Records are built by common.workflows.pipelineRecords.
"""

import json
import os
import random
import string

import boto3
from boto3.dynamodb.conditions import Key, Attr
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.validators import validate
from common.resourceNames import get_table_name, get_bucket_name, ResourceKeys
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
from models.pipelines import (
    CreatePipelineRequestModel,
    UpdatePipelineRequestModel,
    PipelineResponseModel,
    GetPipelinesResponseModel,
)
from common.workflows import pipelineRecords as pr
from common.workflows.triggerTemplateValidation import pipeline_trigger_template_warnings

logger = safeLogger(service_name="PipelineService")

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

try:
    pipeline_table_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    templates_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATES_STORAGE_TABLE)
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    workflow_triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

# Whether the DeadlineCloud execution type is enabled for this deployment. The callback lambda + the
# workflow createJob task-state support are only built when this is true (config.app.pipelines.
# deadlineCloudExecutionTypeEnabled), so a DeadlineCloud pipeline created while it is off would launch
# executions that hang forever on an unresolvable task token — creation is rejected up front.
DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED = (
    os.environ.get("DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED", "false").strip().lower() == "true")

# Optional deploy-time env for provisioning a Lambda for an API-created Lambda-type pipeline that does
# not reference an existing function. Absent for built-in pipelines (they inject their function name at
# import via resourceOverrides) and in tests; auto-creation is skipped when any piece is unavailable.
lambda_role_to_attach = os.environ.get("ROLE_TO_ATTACH_TO_LAMBDA_PIPELINE")
lambda_pipeline_sample_function_key = os.environ.get("LAMBDA_PIPELINE_SAMPLE_FUNCTION_KEY")
lambda_python_version = os.environ.get("LAMBDA_PYTHON_VERSION")
_subnet_ids_string = os.environ.get("SUBNET_IDS", "")
_security_group_ids_string = os.environ.get("SECURITYGROUP_IDS", "")
subnet_ids = _subnet_ids_string.split(",") if _subnet_ids_string else []
security_group_ids = _security_group_ids_string.split(",") if _security_group_ids_string else []

try:
    lambda_pipeline_sample_function_bucket = get_bucket_name(ResourceKeys.ARTEFACTS_BUCKET)
except Exception:
    lambda_pipeline_sample_function_bucket = None

OBJECT_TYPE = "pipeline"

# Pipelines in this database are shared across every database in the deployment.
GLOBAL_DATABASE = "GLOBAL"

# Page cap for the cross-database id-uniqueness lookup (bounds the work one create may do).
MAX_ID_LOOKUP_PAGES = 50


#######################
# Utilities
#######################

def _pipeline_table():
    return dynamodb.Table(pipeline_table_name)


def _templates_table():
    return dynamodb.Table(templates_table_name)


def _workflow_table():
    return dynamodb.Table(workflow_table_name)


def _get_fileupload_trigger_row(workflow_database_id, workflow_id):
    """The fileUpload trigger row for a workflow (or None). Used to warn when a require-template
    pipeline is in an auto-triggered workflow whose trigger picked no default template for it."""
    composite = f"{workflow_database_id}:{workflow_id}"
    try:
        return dynamodb.Table(workflow_triggers_table_name).get_item(
            Key={"workflowDatabaseId:workflowId": composite, "triggerType": "fileUpload"}
        ).get("Item")
    except Exception:
        return None


def _pipeline_save_warnings(item):
    """Non-blocking warnings for a saved pipeline (empty list when none). Today: a require-template
    pipeline that is part of an auto-triggered workflow with no default template chosen for it."""
    require_template = bool((item.get("systemConfig") or {}).get("requireTemplate"))
    return pipeline_trigger_template_warnings(
        _workflow_table(), _get_fileupload_trigger_row,
        item.get("databaseId", ""), item.get("pipelineId", ""), require_template)


def _referencing_workflow_labels(database_id, pipeline_id):
    """`databaseId:workflowId` labels of the workflows whose specifiedPipelines snapshot lists this
    pipeline. Bounded save-path scan of the low-cardinality workflow table; best-effort — returns []
    on any read error."""
    composite = pr.pipeline_composite_key(database_id, pipeline_id)
    labels = []
    try:
        table = _workflow_table()
        kwargs = {}
        while True:
            resp = table.scan(**kwargs)
            for workflow in resp.get("Items", []):
                for ref in workflow.get("specifiedPipelines", []) or []:
                    if (ref or {}).get("pipelineDatabaseId:pipelineId") == composite:
                        labels.append(
                            f"{workflow.get('databaseId', '')}:{workflow.get('workflowId', '')}")
                        break
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception as e:
        logger.warning(f"Referencing-workflow lookup failed for {composite}: {e}")
        return []
    return labels


def _stale_deployment_warnings(database_id, pipeline_id):
    """Warnings (non-blocking) for an executionConfig change: the pipeline's execution target and
    callback/timeout values are baked into each referencing workflow's deployed Step Functions
    definition at workflow-save time, so those state machines keep invoking the previous target until
    the workflow is saved again."""
    labels = _referencing_workflow_labels(database_id, pipeline_id)
    if not labels:
        return []
    return [
        f"the execution configuration changed while workflow(s) [{', '.join(labels)}] reference this "
        f"pipeline. Their deployed state machines still invoke the previous execution target — save "
        f"each workflow again to redeploy it against the new configuration."
    ]


def _casbin_object(item):
    """The Tier-2 Casbin object for a pipeline row: the record + object__type + the flat ABAC
    constraint fields (name from pipelineName; pipelineExecutionType from executionConfig)."""
    obj = dict(item)
    obj["object__type"] = OBJECT_TYPE
    pr.apply_pipeline_constraint_fields(obj, item)
    return obj


def _template_count(database_id, pipeline_id):
    """Number of saved templates for one pipeline via a COUNT query on the templates table (no item
    read, no scan). Pages through Count (DynamoDB caps a COUNT query at 1MB of scanned index per
    page). Best-effort: returns None on any error so a count failure never breaks the pipeline
    listing."""
    composite = pr.pipeline_composite_key(database_id, pipeline_id)
    try:
        table = _templates_table()
        total = 0
        kwargs = {
            "KeyConditionExpression": Key("pipelineDatabaseId:pipelineId").eq(composite),
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
        logger.warning(f"Template count failed for {composite}: {e}")
        return None


def _item_to_response(item, templates=None, template_count=None):
    return PipelineResponseModel(
        databaseId=item.get("databaseId", ""),
        pipelineId=item.get("pipelineId", ""),
        pipelineName=item.get("pipelineName", ""),
        category=item.get("category", ""),
        description=item.get("description", ""),
        executionConfig=item.get("executionConfig", {}),
        systemConfig=item.get("systemConfig", {}),
        enabled=item.get("enabled", True),
        archived=item.get("archived", False),
        dateCreated=item.get("dateCreated", ""),
        dateModified=item.get("dateModified", ""),
        createdBy=item.get("createdBy", ""),
        modifiedBy=item.get("modifiedBy", ""),
        schemaVersion=item.get("schemaVersion", 1),
        templateCount=template_count,
        templates=templates,
    )


def _enforce(claims_and_roles, item, action):
    """Tier-2 object check. Returns True when allowed (or when there are no tokens to enforce)."""
    if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
        enforcer = CasbinEnforcer(claims_and_roles)
        return enforcer.enforce(_casbin_object(item), action)
    return False


def _enforce_missing(claims_and_roles, database_id, pipeline_id, action):
    """Tier-2 check for a pipeline row that does not exist, run against a provisional record carrying
    only the path-scoped ids. Callers run this before returning 404 so an unauthorized caller cannot
    use the 404 as an existence oracle for pipelines it may not see."""
    return _enforce(
        claims_and_roles, {"databaseId": database_id, "pipelineId": pipeline_id}, action)


def _global_scope_denied(claims_and_roles, item):
    """True when a configuration-changing request targets a GLOBAL pipeline and the caller lacks
    pipeline management (PUT) permission on it.

    A GLOBAL pipeline is visible to and runnable from every database, so creating one — or adding a
    template to one — changes behavior for every tenant. The pipeline object action POST is shared by
    "run this pipeline" and "create this pipeline", so run-only roles carry POST on GLOBAL pipelines;
    requiring the unambiguous management action (PUT) as well keeps them from reconfiguring the
    GLOBAL scope."""
    if item.get("databaseId") != GLOBAL_DATABASE:
        return False
    return not _enforce(claims_and_roles, item, "PUT")


#######################
# Lambda-pipeline provisioning
#######################

def _deadline_cloud_blocked(execution_config):
    """True when the request asks for the DeadlineCloud execution type but the deployment has it
    disabled. Creating/updating such a pipeline is rejected because the workflow createJob task-state
    support + the job-callback lambda are only deployed when the type is enabled — a DeadlineCloud
    execution would otherwise hang forever on an unresolvable Step Functions task token."""
    exec_type = (execution_config or {}).get("executionType", "Lambda")
    return exec_type == "DeadlineCloud" and not DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED


def _generate_random_string(length=8):
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def _build_lambda_name(pipeline_id):
    """Deterministic-ish unique Lambda name derived from the pipelineId (alnum, lowercased, no leading
    digits, `vams-` prefixed, capped at 64 chars) plus a random suffix to avoid collisions."""
    name = pipeline_id[-50:] if len(pipeline_id) > 50 else pipeline_id
    name = "".join(c for c in name if c.isalnum()).lower().lstrip(string.digits)
    name = "vams-" + name + _generate_random_string(8)
    if len(name) > 64:
        name = name[-63:]
    return name


def _carry_over_provisioned_lambda(execution_config, prior_execution_config):
    """Keep the Lambda a prior row already had when a request supplies no `lambda.resourceId` for the
    same execution type, so restoring an archived Lambda-type pipeline reuses its function instead of
    provisioning a second one."""
    config = dict(execution_config or {})
    prior = prior_execution_config or {}
    if config.get("executionType", "Lambda") != "Lambda":
        return config
    if prior.get("executionType", "Lambda") != "Lambda":
        return config
    lam = dict(config.get("lambda") or {})
    if lam.get("resourceId"):
        return config
    prior_lambda = prior.get("lambda") or {}
    if not prior_lambda.get("resourceId"):
        return config
    lam["resourceId"] = prior_lambda["resourceId"]
    if "isProvided" in prior_lambda:
        lam["isProvided"] = prior_lambda["isProvided"]
    config["lambda"] = lam
    return config


def _provision_lambda_for_pipeline(execution_config, pipeline_id):
    """For an API-created Lambda-type pipeline that does not already reference a function, provision a
    new Lambda (seeded from the sample pipeline package) and return the executionConfig with its
    `lambda.resourceId` set. Built-in pipelines inject their function name at import via
    resourceOverrides, so they arrive with a resourceId already set and are left untouched.

    A future backend upgrade will let the API supply the Lambda code (and other pipeline components) to
    deploy; today the provisioned function is seeded from the sample package. See the pipelines/
    workflows/execution consolidation plan doc.

    Raises VAMSGeneralErrorResponse when provisioning is required but the deploy-time role/package env
    is unavailable, so the caller does not silently create a pipeline pointing at a non-existent
    function."""
    config = dict(execution_config or {})
    if config.get("executionType", "Lambda") != "Lambda":
        return config

    lam = dict(config.get("lambda") or {})
    if lam.get("resourceId"):
        # Caller referenced an existing function (or a built-in injected its name) — do not provision.
        config["lambda"] = lam
        return config

    if not (lambda_role_to_attach and lambda_pipeline_sample_function_bucket
            and lambda_pipeline_sample_function_key and lambda_python_version):
        raise VAMSGeneralErrorResponse(
            "This deployment cannot auto-create a Lambda for a Lambda-type pipeline; supply an "
            "existing function in executionConfig.lambda.resourceId.")

    lambda_name = _build_lambda_name(pipeline_id)
    create_params = {
        "FunctionName": lambda_name,
        "Role": lambda_role_to_attach,
        "PackageType": "Zip",
        "Code": {
            "S3Bucket": lambda_pipeline_sample_function_bucket,
            "S3Key": lambda_pipeline_sample_function_key,
        },
        "Handler": "lambda_function.lambda_handler",
        "Runtime": lambda_python_version,
    }
    if subnet_ids and security_group_ids:
        create_params["VpcConfig"] = {
            "SubnetIds": subnet_ids, "SecurityGroupIds": security_group_ids}
    logger.info(f"Auto-creating Lambda '{lambda_name}' for pipeline '{pipeline_id}'")
    lambda_client.create_function(**create_params)
    lam["resourceId"] = lambda_name
    lam["isProvided"] = False
    config["lambda"] = lam
    return config


#######################
# Data operations
#######################

def _pagination_config(query_params):
    """Boto3 paginator config from the validated query params (validate_pagination_info fills
    maxItems/pageSize/startingToken). Bounds the page so a list never accumulates the whole table
    into a single response (backend Rule 15: stay under the 6 MB Lambda response limit)."""
    return {
        "MaxItems": int(query_params["maxItems"]),
        "PageSize": int(query_params["pageSize"]),
        "StartingToken": query_params["startingToken"],
    }


def _filtered_page(page_iterator, include_archived, claims_and_roles):
    """Casbin-filter + archived-filter a paginator full-result page into response models + NextToken."""
    items = []
    for item in page_iterator.get("Items", []):
        if not include_archived and item.get("archived"):
            continue
        if _enforce(claims_and_roles, item, "GET"):
            # Template count is a bounded COUNT query per authorized pipeline on this page
            # (page size caps the fan-out, so no unbounded N+1). Best-effort — None on failure.
            count = _template_count(item.get("databaseId", ""), item.get("pipelineId", ""))
            items.append(_item_to_response(item, template_count=count))
    result = GetPipelinesResponseModel(Items=items)
    if "NextToken" in page_iterator:
        result.NextToken = page_iterator["NextToken"]
    return result


def get_all_pipelines(query_params, include_archived, claims_and_roles):
    # Query the by-date GSI (constant partition, newest-first) instead of scanning the whole table.
    paginator = dynamodb.meta.client.get_paginator("query")
    page_iterator = paginator.paginate(
        TableName=pipeline_table_name,
        IndexName="PipelinesByDateGSI",
        KeyConditionExpression=Key("allListPartition").eq(pr.ALL_PIPELINES_LIST_PARTITION),
        ScanIndexForward=False,
        PaginationConfig=_pagination_config(query_params),
    ).build_full_result()
    return _filtered_page(page_iterator, include_archived, claims_and_roles)


def get_database_pipelines(database_id, query_params, include_archived, claims_and_roles):
    paginator = dynamodb.meta.client.get_paginator("query")
    page_iterator = paginator.paginate(
        TableName=pipeline_table_name,
        KeyConditionExpression=Key("databaseId").eq(database_id),
        PaginationConfig=_pagination_config(query_params),
    ).build_full_result()
    return _filtered_page(page_iterator, include_archived, claims_and_roles)


def get_pipeline_item(database_id, pipeline_id):
    response = _pipeline_table().get_item(Key={"databaseId": database_id, "pipelineId": pipeline_id})
    return response.get("Item")


def find_pipeline_id_owner(pipeline_id, excluding_database_id=None):
    """The databaseId of an existing pipeline carrying this pipelineId, or None. Ids are unique
    across all databases (GLOBAL included); archived rows still hold their id. Queries the
    constant-partition by-date GSI rather than scanning."""
    table = _pipeline_table()
    query_kwargs = {
        "IndexName": "PipelinesByDateGSI",
        "KeyConditionExpression": Key("allListPartition").eq(pr.ALL_PIPELINES_LIST_PARTITION),
        "FilterExpression": Attr("pipelineId").eq(pipeline_id),
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
        f"Pipeline id uniqueness lookup stopped after {MAX_ID_LOOKUP_PAGES} pages; "
        "treating the id as free.")
    return None


def get_pipeline_templates(database_id, pipeline_id):
    """List a pipeline's template rows (bodies as stored — details view rehydrates via the template
    service; here we return lightweight descriptors)."""
    composite = pr.pipeline_composite_key(database_id, pipeline_id)
    templates = []
    query_kwargs = {"KeyConditionExpression": Key("pipelineDatabaseId:pipelineId").eq(composite)}
    table = _templates_table()
    while True:
        response = table.query(**query_kwargs)
        for item in response.get("Items", []):
            templates.append({
                "templateId": item.get("templateId", ""),
                "templateName": item.get("templateName", ""),
                "configFormat": item.get("configFormat", "json"),
                "allowCustomEdit": item.get("allowCustomEdit", False),
            })
        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return templates


def create_pipeline(database_id, request, username, claims_and_roles):
    table = _pipeline_table()
    pipeline_id = request.pipelineId or pr.new_guid()

    record = pr.build_pipeline_record(
        database_id=database_id,
        pipeline_id=pipeline_id,
        pipeline_name=request.pipelineName,
        category=request.category or "",
        description=request.description or "",
        execution_config=request.executionConfig or pr.build_pipeline_execution_config(),
        system_config=request.systemConfig or pr.build_pipeline_system_config(),
        enabled=request.enabled if request.enabled is not None else True,
        created_by=username,
        modified_by=username,
    )

    # Tier-2 FIRST: authorize creating this pipeline object before any existence probe, so the
    # duplicate-check does not become a pre-authorization existence oracle.
    if not _enforce(claims_and_roles, record, "POST"):
        return authorization_error()

    # A GLOBAL pipeline is created for every database in the deployment, so it additionally requires
    # pipeline management permission on the GLOBAL scope.
    if _global_scope_denied(claims_and_roles, record):
        logger.info(f"Create of GLOBAL pipeline {pipeline_id} denied: no GLOBAL pipeline management")
        return authorization_error()

    if _deadline_cloud_blocked(record.get("executionConfig")):
        return validation_error(body={
            "message": "The DeadlineCloud execution type is not enabled for this deployment."})

    existing = get_pipeline_item(database_id, pipeline_id)
    if existing and not existing.get("archived"):
        logger.info(f"Pipeline {database_id}:{pipeline_id} already exists")
        return validation_error(body={"message": "A pipeline with this ID already exists."})

    # Ids are unique across databases; restoring this database's own archived row keeps its id.
    if not existing:
        other_owner = find_pipeline_id_owner(pipeline_id, excluding_database_id=database_id)
        if other_owner:
            logger.info(f"pipelineId {pipeline_id} is already in use by database {other_owner}")
            return validation_error(body={
                "message": "Pipeline ID is already in use by another database. Choose a different ID."})
    if existing:
        # The id belongs to an archived (soft-deleted) row: the create restores it in place, which is
        # the path a re-registration of an archived built-in takes. Create provenance is preserved.
        logger.info(f"Restoring archived pipeline {database_id}:{pipeline_id}")
        record["dateCreated"] = existing.get("dateCreated") or record["dateCreated"]
        record["createdBy"] = existing.get("createdBy") or record["createdBy"]
        record["executionConfig"] = _carry_over_provisioned_lambda(
            record.get("executionConfig"), existing.get("executionConfig"))

    # Provision a Lambda for a Lambda-type pipeline that does not reference an existing function
    # (after auth + duplicate check so a rejected request never creates a function). Built-ins arrive
    # with their function name already injected and are left untouched.
    record["executionConfig"] = _provision_lambda_for_pipeline(
        record.get("executionConfig"), pipeline_id)

    table.put_item(Item=record)
    body = {"message": _item_to_response(record).dict()}
    # Non-blocking save warnings (e.g. require-template pipeline in an auto-trigger with no default
    # template chosen). Included only when present so a clean save is unchanged.
    warnings = _pipeline_save_warnings(record)
    if warnings:
        body["warnings"] = warnings
    return success(body=body)


def update_pipeline(database_id, pipeline_id, request, username, claims_and_roles):
    item = get_pipeline_item(database_id, pipeline_id)
    if not item:
        # Authorize against a provisional record first so the 404 is not an existence oracle.
        if not _enforce_missing(claims_and_roles, database_id, pipeline_id, "PUT"):
            return authorization_error()
        return validation_error(status_code=404, body={"message": "Pipeline not found"})
    if not _enforce(claims_and_roles, item, "PUT"):
        return authorization_error()

    # Reject switching a pipeline to DeadlineCloud when the deployment has that type disabled.
    if request.executionConfig is not None and _deadline_cloud_blocked(request.executionConfig):
        return validation_error(body={
            "message": "The DeadlineCloud execution type is not enabled for this deployment."})

    execution_config_changed = (request.executionConfig is not None
                                and request.executionConfig != item.get("executionConfig"))

    if request.pipelineName is not None:
        item["pipelineName"] = request.pipelineName
    if request.category is not None:
        item["category"] = request.category
        item["databaseId:category"] = f"{database_id}:{request.category}"
    if request.description is not None:
        item["description"] = request.description
    if request.executionConfig is not None:
        item["executionConfig"] = request.executionConfig
    if request.systemConfig is not None:
        item["systemConfig"] = request.systemConfig
    if request.enabled is not None:
        item["enabled"] = request.enabled
    if request.archived is not None:
        item["archived"] = request.archived
    item["dateModified"] = pr.iso_now()
    item["modifiedBy"] = username

    _pipeline_table().put_item(Item=item)
    body = {"message": _item_to_response(item).dict()}
    warnings = _pipeline_save_warnings(item)
    if execution_config_changed:
        warnings = warnings + _stale_deployment_warnings(database_id, pipeline_id)
    if warnings:
        body["warnings"] = warnings
    return success(body=body)


def archive_pipeline(database_id, pipeline_id, username, claims_and_roles):
    item = get_pipeline_item(database_id, pipeline_id)
    if not item:
        # Authorize against a provisional record first so the 404 is not an existence oracle.
        if not _enforce_missing(claims_and_roles, database_id, pipeline_id, "DELETE"):
            return authorization_error()
        return validation_error(status_code=404, body={"message": "Pipeline not found"})
    if not _enforce(claims_and_roles, item, "DELETE"):
        return authorization_error()

    item["archived"] = True
    item["enabled"] = False
    item["dateModified"] = pr.iso_now()
    item["modifiedBy"] = username
    _pipeline_table().put_item(Item=item)
    return success(body={"message": "Pipeline archived"})


#######################
# Route handlers
#######################

def _request_body(event):
    """Parsed JSON request body as a mapping. A valid-JSON-but-non-object body (list/string/null)
    is a client error, not an internal one."""
    body = json.loads(event.get("body") or "{}")
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    return body


def _validate_path_ids(path_parameters):
    ids = {"databaseId": True}
    if "pipelineId" in path_parameters:
        ids["pipelineId"] = False
    for pid, allow_global in ids.items():
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
            "true", "1", "yes"
        )
        # Bound the page (100) so a list returns a small page + NextToken rather than accumulating
        # up to the utility's 10000/3000 defaults into one response (Rule 15 / 6MB cap). Both the
        # max-items and the page-size default are overridden so an unparseable pageSize falls back
        # to the same bound.
        validate_pagination_info(query_parameters, 100, 100)

        method = event["requestContext"]["http"]["method"]

        # Tier-1: API-level authorization.
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
        pipeline_id = path_parameters.get("pipelineId")

        if method == "GET":
            if pipeline_id:
                item = get_pipeline_item(database_id, pipeline_id)
                if not item or (item.get("archived") and not include_archived):
                    # Authorize (against the row when it exists, else a provisional record) before
                    # the 404 so it is not an existence oracle for pipelines the caller cannot see.
                    if not _enforce(claims_and_roles, item or {
                            "databaseId": database_id, "pipelineId": pipeline_id}, "GET"):
                        return authorization_error()
                    return validation_error(status_code=404, body={"message": "Pipeline not found"}, event=event)
                if not _enforce(claims_and_roles, item, "GET"):
                    return authorization_error()
                templates = get_pipeline_templates(database_id, pipeline_id)
                return success(body={"message": _item_to_response(
                    item, templates=templates, template_count=len(templates)).dict()})
            if database_id:
                result = get_database_pipelines(database_id, query_parameters, include_archived, claims_and_roles)
                return success(body={"message": result.dict()})
            result = get_all_pipelines(query_parameters, include_archived, claims_and_roles)
            return success(body={"message": result.dict()})

        username = (claims_and_roles["tokens"][0]
                    if claims_and_roles.get("tokens") else "")

        if method == "POST":
            if not database_id:
                return validation_error(body={"message": "databaseId required to create a pipeline"}, event=event)
            request = CreatePipelineRequestModel(**_request_body(event))
            # The pipeline is created under the path-scoped database; a body databaseId naming a
            # different one is rejected rather than silently ignored.
            if request.databaseId != database_id:
                logger.info(f"Create body databaseId {request.databaseId} does not match the path "
                            f"database {database_id}")
                return validation_error(body={
                    "message": "databaseId in the request body must match the request path."},
                    event=event)
            return create_pipeline(database_id, request, username, claims_and_roles)

        if method == "PUT":
            if not pipeline_id:
                return validation_error(body={"message": "pipelineId required to update a pipeline"}, event=event)
            request = UpdatePipelineRequestModel(**_request_body(event))
            return update_pipeline(database_id, pipeline_id, request, username, claims_and_roles)

        if method == "DELETE":
            if not pipeline_id:
                return validation_error(body={"message": "pipelineId required to archive a pipeline"}, event=event)
            return archive_pipeline(database_id, pipeline_id, username, claims_and_roles)

        return validation_error(body={"message": "Method not allowed"}, event=event)

    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={"message": str(v)}, event=event)
    except json.JSONDecodeError:
        return validation_error(body={"message": "Invalid JSON in request body"}, event=event)
    except ValueError as ve:
        logger.exception(f"Validation error: {ve}")
        return validation_error(body={"message": str(ve)}, event=event)
    except Exception as e:
        logger.exception(f"Unhandled error in pipelineService: {e}")
        return internal_error(event=event)
