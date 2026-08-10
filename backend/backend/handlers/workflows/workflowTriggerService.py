# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workflow trigger service.

Handles the per-workflow trigger endpoints:
  GET    /database/{databaseId}/workflows/{workflowId}/triggers                 list triggers
  GET    /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}   get a trigger
  PUT    /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}   set/replace a trigger
  DELETE /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}   delete a trigger

Triggers are a sub-resource of a workflow: Tier-1 gates the route; Tier-2 enforces on the OWNING
workflow object. fileUpload is the only trigger type today; its config carries inputFileFilters
(ext/path/name/wildcard) and defaultTemplateIds ({'<pipelineDatabaseId>:<pipelineId>': templateId}).
Trigger rows live in WorkflowTriggersStorageTable (PK workflowDatabaseId:workflowId, SK triggerType);
records built by common.workflows.workflowRecords.
"""

import json
from urllib.parse import unquote

import boto3
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import ValidationError

from common.validators import validate
from common.resourceNames import get_table_name, ResourceKeys
from common.auth.apiEvent import normalize_event
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
    SetTriggerRequestModel,
    TriggerResponseModel,
    GetTriggersResponseModel,
    TRIGGER_TYPES,
)
from common.workflows import workflowRecords as wr
from common.workflows import pipelineRecords as pr
from common.workflows import templateBodyStorage as tbs
from common.workflows.defaultBucket import resolve_default_bucket, default_bucket_key
from common.workflows.triggerTemplateValidation import validate_trigger_default_templates

logger = safeLogger(service_name="WorkflowTriggerService")

dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")

try:
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
    tag_schema_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE)
    buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

OBJECT_TYPE_WORKFLOW = "workflow"

TRIGGER_TYPE_FILE_UPLOAD = "fileUpload"


def _build_file_upload_config(request):
    return wr.build_file_upload_trigger_config(
        input_file_filters=request.inputFileFilters or {"allow": [], "exclude": []},
        default_template_ids=request.defaultTemplateIds or {},
    )


# triggerConfig builder per trigger type. A supported type with no builder here has no persistable
# config shape, so the save fails rather than storing a config of the wrong shape.
_TRIGGER_CONFIG_BUILDERS = {
    TRIGGER_TYPE_FILE_UPLOAD: _build_file_upload_config,
}


def _workflow_table():
    return dynamodb.Table(workflow_table_name)


def _triggers_table():
    return dynamodb.Table(triggers_table_name)


def _tag_schema_table():
    return dynamodb.Table(tag_schema_table_name)


def _load_template_tag_schema_fields(pipeline_database_id, pipeline_id, template_id):
    """Load a template's tag-schema fields (rehydrating from S3 when offloaded), or None. Used to
    check a trigger's chosen default templates for required-without-default tags.

    Best-effort: returns None on any read/parse failure so the headless-template validation can never
    turn a trigger save (including the trusted deploy-time built-in registration) into a 500 — a
    load failure simply skips the check for that template."""
    try:
        owner = pr.template_owner_key(pipeline_database_id, pipeline_id, template_id)
        rows = _tag_schema_table().query(
            IndexName="TagSchemaByTemplateGSI",
            KeyConditionExpression=Key("pipelineDatabaseId:pipelineId:templateId").eq(owner),
        ).get("Items", [])
        if not rows:
            return None
        row = rows[0]
        if row.get("bodyStorage") == tbs.BODY_STORAGE_S3 and row.get("fieldsS3Key"):
            default_bucket = resolve_default_bucket(dynamodb.Table(buckets_table_name))
            text = tbs.read_body_from_s3(s3_client, default_bucket["bucketName"],
                                         default_bucket_key(default_bucket, row["fieldsS3Key"]))
            return json.loads(text) if text else []
        fields = row.get("fields") or ""
        return json.loads(fields) if fields else []
    except Exception as e:
        logger.warning(
            f"Could not load tag schema for {pipeline_database_id}:{pipeline_id}:{template_id} "
            f"(skipping headless-template check): {e}")
        return None


def _enforce_parent_workflow(database_id, workflow_id, action, claims_and_roles):
    """Tier-2: authorize against the owning workflow object. Returns (allowed, workflow_item)."""
    item = _workflow_table().get_item(
        Key={"databaseId": database_id, "workflowId": workflow_id}
    ).get("Item")
    if not item:
        return None, None
    if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
        obj = dict(item)
        obj["object__type"] = OBJECT_TYPE_WORKFLOW
        obj.setdefault("name", item.get("workflowName", ""))
        return CasbinEnforcer(claims_and_roles).enforce(obj, action), item
    return False, item


def _row_to_response(row):
    # triggerType is the row's KEY (bare type, or 'type#triggerId'), and is what a client sends back on
    # the trigger path to address this trigger. The base type and id are reported alongside it so a
    # client can group and label triggers without parsing the key. A row written before those attributes
    # existed carries only the bare type in its key, which splits to itself with an empty id.
    stored_key = row.get("triggerType", "")
    split_base, split_id = wr.split_trigger_sort_key(stored_key)
    return TriggerResponseModel(
        workflowDatabaseId=row.get("workflowDatabaseId", ""),
        workflowId=row.get("workflowId", ""),
        triggerType=stored_key,
        triggerBaseType=row.get("triggerBaseType") or split_base,
        triggerId=row.get("triggerId") or split_id,
        triggerConfig=row.get("triggerConfig", {}),
        enabled=row.get("enabled", True),
        dateCreated=row.get("dateCreated", ""),
        dateModified=row.get("dateModified", ""),
    )


def list_triggers(database_id, workflow_id):
    composite = wr.workflow_composite_key(database_id, workflow_id)
    items = []
    response = _triggers_table().query(
        KeyConditionExpression=Key("workflowDatabaseId:workflowId").eq(composite)
    )
    for row in response.get("Items", []):
        items.append(_row_to_response(row))
    return GetTriggersResponseModel(Items=items)


def get_trigger(database_id, workflow_id, trigger_type):
    composite = wr.workflow_composite_key(database_id, workflow_id)
    return _triggers_table().get_item(
        Key={"workflowDatabaseId:workflowId": composite, "triggerType": trigger_type}
    ).get("Item")


# The concurrency restriction that serializes runs per asset. Several triggers of one type fire the
# workflow once per matching trigger, so under this restriction they would contend on the same asset.
CONCURRENCY_RESTRICTION_PER_ASSET = "perAsset"


def _same_type_triggers(database_id, workflow_id, base_type):
    """Every stored trigger row of one base type for a workflow (bare-key and suffixed alike).

    Reads the base table by partition key, so it sees every trigger the workflow has whatever its sort
    key — the by-type GSI is for the dispatcher's cross-workflow lookup, not for this."""
    composite = wr.workflow_composite_key(database_id, workflow_id)
    rows = []
    kwargs = {"KeyConditionExpression": Key("workflowDatabaseId:workflowId").eq(composite)}
    while True:
        response = _triggers_table().query(**kwargs)
        for row in response.get("Items", []):
            stored_type, _ = wr.split_trigger_sort_key(row.get("triggerType", ""))
            if stored_type == base_type:
                rows.append(row)
        if "LastEvaluatedKey" not in response:
            return rows
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def set_trigger(database_id, workflow_id, trigger_type, request, event=None,
                workflow_item=None):
    # A trigger runs headless, so any default template it names must be renderable with no
    # user-supplied tags: reject the save if a chosen default template has a required tag with no
    # default value. (A trigger never REQUIRES a template — defaultTemplateIds is optional; this only
    # validates the templates it DID choose.)
    template_errors = validate_trigger_default_templates(
        request.defaultTemplateIds or {}, _load_template_tag_schema_fields)
    if template_errors:
        return validation_error(body={"message": {"triggerTemplateErrors": template_errors}})

    base_type, trigger_id = wr.split_trigger_sort_key(trigger_type)
    siblings = [row for row in _same_type_triggers(database_id, workflow_id, base_type)
                if row.get("triggerType") != trigger_type]
    if siblings:
        # Several triggers of one type fire the workflow once per matching trigger, so a workflow that
        # serializes runs per asset would have them contend on that asset. perInputFile is NOT blocked:
        # overlapping filters there are caught by the execution's own per-file check, which fails that
        # trigger's execution rather than the save.
        restriction = ((workflow_item or {}).get("systemConfig") or {}).get("concurrencyRestriction")
        if restriction == CONCURRENCY_RESTRICTION_PER_ASSET:
            logger.info(f"Rejected additional {base_type} trigger under perAsset concurrency")
            return validation_error(body={"message": (
                "This workflow restricts concurrency per asset, so it supports only one trigger of a "
                "type. Remove the restriction or the other trigger of this type first.")}, event=event)
        # What distinguishes two triggers of one type is the templates they launch with. Two that name
        # the same set are the same trigger declared twice — including two that name NO templates,
        # which is a valid choice when no pipeline requires one.
        incoming_templates = dict(request.defaultTemplateIds or {})
        for row in siblings:
            existing_templates = dict((row.get("triggerConfig") or {}).get("defaultTemplateIds") or {})
            if existing_templates == incoming_templates:
                logger.info(f"Rejected duplicate {base_type} trigger: same defaultTemplateIds")
                return validation_error(body={"message": (
                    "Another trigger of this type already uses the same default templates. Triggers of "
                    "one type must differ in the templates they launch with.")}, event=event)

    builder = _TRIGGER_CONFIG_BUILDERS.get(base_type)
    if builder is None:
        logger.error(f"No triggerConfig builder for trigger type: {trigger_type}")
        return validation_error(body={
            "message": "This trigger type cannot be configured in this deployment."})
    config = builder(request)
    # Preserve the original creation timestamp when replacing an existing trigger (PUT is both the
    # create and the edit path), so a re-set updates only dateModified — matching update_workflow.
    existing = get_trigger(database_id, workflow_id, trigger_type)
    date_created = existing.get("dateCreated", "") if existing else ""
    record = wr.build_trigger_record(
        workflow_database_id=database_id, workflow_id=workflow_id,
        trigger_type=base_type, trigger_config=config,
        enabled=request.enabled if request.enabled is not None else True,
        date_created=date_created, trigger_id=trigger_id,
    )
    _triggers_table().put_item(Item=record)
    # AUDIT LOG: trigger set. A trigger changes what runs AUTOMATICALLY, without a caller, so the
    # enabled flag and the filters that decide when it fires are the audit-worthy part.
    log_actions(event or {}, "workflowTriggerSet", {
        "databaseId": database_id,
        "workflowId": workflow_id,
        "triggerType": trigger_type,
        "enabled": bool(record.get("enabled")),
        "operation": "set",
    })
    return success(body={"message": _row_to_response(record).dict()})


def delete_trigger(database_id, workflow_id, trigger_type, event=None):
    composite = wr.workflow_composite_key(database_id, workflow_id)
    if not get_trigger(database_id, workflow_id, trigger_type):
        return validation_error(status_code=404, body={"message": "Trigger not found"})
    _triggers_table().delete_item(
        Key={"workflowDatabaseId:workflowId": composite, "triggerType": trigger_type}
    )
    # AUDIT LOG: trigger deleted — the workflow stops firing automatically.
    log_actions(event or {}, "workflowTriggerDelete", {
        "databaseId": database_id,
        "workflowId": workflow_id,
        "triggerType": trigger_type,
        "operation": "delete",
    })
    return success(body={"message": "Trigger deleted"})


def _validate_ids(path_parameters):
    for pid, allow_global in (("databaseId", True), ("workflowId", False)):
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
        method = event["requestContext"]["http"]["method"]

        claims_and_roles = request_to_claims(event)
        allowed = False
        if len(claims_and_roles["tokens"]) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                allowed = True
        if not allowed:
            return authorization_error()

        message = _validate_ids(path_parameters)
        if message:
            return validation_error(body={"message": message}, event=event)

        database_id = path_parameters["databaseId"]
        workflow_id = path_parameters["workflowId"]
        # API Gateway hands pathParameters through PERCENT-ENCODED. A trigger key may be
        # "type#triggerId", and a client must encode the '#' or the request never routes (a raw '#' is a
        # URL fragment delimiter), so the encoded form is what arrives here and the handler decodes it.
        # Verified live: an un-decoded value reached this handler as "fileUpload%23smoke2", whose split
        # found no '#' and was rejected as an unsupported type.
        trigger_type = path_parameters.get("triggerType")
        if trigger_type:
            trigger_type = unquote(trigger_type)

        # Tier-2 against the owning workflow; action mirrors the HTTP verb.
        allowed_obj, workflow_item = _enforce_parent_workflow(
            database_id, workflow_id, method, claims_and_roles)
        if workflow_item is None:
            return validation_error(status_code=404, body={"message": "Workflow not found"}, event=event)
        if not allowed_obj:
            return authorization_error()

        # A trigger type in the path must be a supported type. The path value is a trigger KEY: the
        # bare type addresses a workflow's first trigger of that type, and "type#triggerId" addresses an
        # additional one, so only the part before the '#' is matched against the supported set. Do not
        # echo the caller's supplied value back (Rule 11); the allowed set is not user input, so it is
        # safe to list.
        if trigger_type is not None:
            base_type, supplied_trigger_id = wr.split_trigger_sort_key(trigger_type)
            if base_type not in TRIGGER_TYPES:
                logger.info(f"Unsupported trigger type requested: {trigger_type}")
                return validation_error(body={
                    "message": f"Unsupported trigger type. Supported types: {list(TRIGGER_TYPES)}"},
                    event=event)
            # A trigger id shares the id character class, so a malformed suffix is rejected here rather
            # than becoming an unreachable row.
            if supplied_trigger_id:
                (valid, message) = validate({
                    "triggerId": {"value": supplied_trigger_id, "validator": "ID"}})
                if not valid:
                    logger.info(f"Invalid trigger id in path: {message}")
                    return validation_error(body={"message": "Invalid trigger id."}, event=event)

        if method == "GET":
            if trigger_type:
                row = get_trigger(database_id, workflow_id, trigger_type)
                if not row:
                    return validation_error(status_code=404, body={"message": "Trigger not found"}, event=event)
                return success(body={"message": _row_to_response(row).dict()})
            return success(body={"message": list_triggers(database_id, workflow_id).dict()})

        if method == "PUT":
            if not trigger_type:
                return validation_error(body={"message": "triggerType required"}, event=event)
            request = SetTriggerRequestModel(**json.loads(event.get("body") or "{}"))
            return set_trigger(database_id, workflow_id, trigger_type, request, event,
                               workflow_item=workflow_item)

        if method == "DELETE":
            if not trigger_type:
                return validation_error(body={"message": "triggerType required"}, event=event)
            return delete_trigger(database_id, workflow_id, trigger_type, event)

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
        logger.exception(f"Unhandled error in workflowTriggerService: {e}")
        return internal_error(event=event)
