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
from boto3.dynamodb.conditions import Key
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

logger = safeLogger(service_name="PipelineService")

dynamodb = boto3.resource("dynamodb")
lambda_client = boto3.client("lambda")

try:
    pipeline_table_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    templates_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATES_STORAGE_TABLE)
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


#######################
# Utilities
#######################

def _pipeline_table():
    return dynamodb.Table(pipeline_table_name)


def _templates_table():
    return dynamodb.Table(templates_table_name)


def _casbin_object(item):
    """The Tier-2 Casbin object for a pipeline row: the record + object__type + the flat ABAC
    constraint fields (name from pipelineName; pipelineExecutionType from executionConfig)."""
    obj = dict(item)
    obj["object__type"] = OBJECT_TYPE
    pr.apply_pipeline_constraint_fields(obj, item)
    return obj


def _item_to_response(item, templates=None):
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
        templates=templates,
    )


def _enforce(claims_and_roles, item, action):
    """Tier-2 object check. Returns True when allowed (or when there are no tokens to enforce)."""
    if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
        enforcer = CasbinEnforcer(claims_and_roles)
        return enforcer.enforce(_casbin_object(item), action)
    return False


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
            items.append(_item_to_response(item))
    result = GetPipelinesResponseModel(Items=items)
    if "NextToken" in page_iterator:
        result.NextToken = page_iterator["NextToken"]
    return result


def get_all_pipelines(query_params, include_archived, claims_and_roles):
    paginator = dynamodb.meta.client.get_paginator("scan")
    page_iterator = paginator.paginate(
        TableName=pipeline_table_name,
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

    if _deadline_cloud_blocked(record.get("executionConfig")):
        return validation_error(body={
            "message": "The DeadlineCloud execution type is not enabled for this deployment."})

    if get_pipeline_item(database_id, pipeline_id):
        logger.info(f"Pipeline {database_id}:{pipeline_id} already exists")
        return validation_error(body={"message": "A pipeline with this ID already exists."})

    # Provision a Lambda for a Lambda-type pipeline that does not reference an existing function
    # (after auth + duplicate check so a rejected request never creates a function). Built-ins arrive
    # with their function name already injected and are left untouched.
    record["executionConfig"] = _provision_lambda_for_pipeline(
        record.get("executionConfig"), pipeline_id)

    table.put_item(Item=record)
    return success(body={"message": _item_to_response(record).dict()})


def update_pipeline(database_id, pipeline_id, request, username, claims_and_roles):
    item = get_pipeline_item(database_id, pipeline_id)
    if not item:
        return validation_error(status_code=404, body={"message": "Pipeline not found"})
    if not _enforce(claims_and_roles, item, "PUT"):
        return authorization_error()

    # Reject switching a pipeline to DeadlineCloud when the deployment has that type disabled.
    if request.executionConfig is not None and _deadline_cloud_blocked(request.executionConfig):
        return validation_error(body={
            "message": "The DeadlineCloud execution type is not enabled for this deployment."})

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
    item["dateModified"] = pr.iso_now()
    item["modifiedBy"] = username

    _pipeline_table().put_item(Item=item)
    return success(body={"message": _item_to_response(item).dict()})


def archive_pipeline(database_id, pipeline_id, username, claims_and_roles):
    item = get_pipeline_item(database_id, pipeline_id)
    if not item:
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
        # Bound the default page (100) so an unparameterized list returns a small page + NextToken
        # rather than accumulating up to the 10000 default into one response (Rule 15 / 6MB cap).
        validate_pagination_info(query_parameters, 100)

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
                    return validation_error(status_code=404, body={"message": "Pipeline not found"}, event=event)
                if not _enforce(claims_and_roles, item, "GET"):
                    return authorization_error()
                templates = get_pipeline_templates(database_id, pipeline_id)
                return success(body={"message": _item_to_response(item, templates=templates).dict()})
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
            request = CreatePipelineRequestModel(**json.loads(event.get("body") or "{}"))
            return create_pipeline(database_id, request, username, claims_and_roles)

        if method == "PUT":
            if not pipeline_id:
                return validation_error(body={"message": "pipelineId required to update a pipeline"}, event=event)
            request = UpdatePipelineRequestModel(**json.loads(event.get("body") or "{}"))
            return update_pipeline(database_id, pipeline_id, request, username, claims_and_roles)

        if method == "DELETE":
            if not pipeline_id:
                return validation_error(body={"message": "pipelineId required to archive a pipeline"}, event=event)
            return archive_pipeline(database_id, pipeline_id, username, claims_and_roles)

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
        logger.exception(f"Unhandled error in pipelineService: {e}")
        return internal_error(event=event)
