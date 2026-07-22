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

import boto3
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.validators import validate
from common.resourceNames import get_table_name, ResourceKeys
from common.auth.apiEvent import normalize_event
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
    SetTriggerRequestModel,
    TriggerResponseModel,
    GetTriggersResponseModel,
    TRIGGER_TYPES,
)
from common.workflows import workflowRecords as wr

logger = safeLogger(service_name="WorkflowTriggerService")

dynamodb = boto3.resource("dynamodb")

try:
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

OBJECT_TYPE_WORKFLOW = "workflow"


def _workflow_table():
    return dynamodb.Table(workflow_table_name)


def _triggers_table():
    return dynamodb.Table(triggers_table_name)


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
    return TriggerResponseModel(
        workflowDatabaseId=row.get("workflowDatabaseId", ""),
        workflowId=row.get("workflowId", ""),
        triggerType=row.get("triggerType", ""),
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


def set_trigger(database_id, workflow_id, trigger_type, request):
    config = wr.build_file_upload_trigger_config(
        input_file_filters=request.inputFileFilters or {"allow": [], "exclude": []},
        default_template_ids=request.defaultTemplateIds or {},
    )
    # Preserve the original creation timestamp when replacing an existing trigger (PUT is both the
    # create and the edit path), so a re-set updates only dateModified — matching update_workflow.
    existing = get_trigger(database_id, workflow_id, trigger_type)
    date_created = existing.get("dateCreated", "") if existing else ""
    record = wr.build_trigger_record(
        workflow_database_id=database_id, workflow_id=workflow_id,
        trigger_type=trigger_type, trigger_config=config,
        enabled=request.enabled if request.enabled is not None else True,
        date_created=date_created,
    )
    _triggers_table().put_item(Item=record)
    return success(body={"message": _row_to_response(record).dict()})


def delete_trigger(database_id, workflow_id, trigger_type):
    composite = wr.workflow_composite_key(database_id, workflow_id)
    if not get_trigger(database_id, workflow_id, trigger_type):
        return validation_error(status_code=404, body={"message": "Trigger not found"})
    _triggers_table().delete_item(
        Key={"workflowDatabaseId:workflowId": composite, "triggerType": trigger_type}
    )
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
        trigger_type = path_parameters.get("triggerType")

        # Tier-2 against the owning workflow; action mirrors the HTTP verb.
        allowed_obj, workflow_item = _enforce_parent_workflow(
            database_id, workflow_id, method, claims_and_roles)
        if workflow_item is None:
            return validation_error(status_code=404, body={"message": "Workflow not found"}, event=event)
        if not allowed_obj:
            return authorization_error()

        # A trigger type in the path must be a supported type. Do not echo the caller's supplied
        # value back (Rule 11); the allowed set is not user input, so it is safe to list.
        if trigger_type is not None and trigger_type not in TRIGGER_TYPES:
            logger.info(f"Unsupported trigger type requested: {trigger_type}")
            return validation_error(body={
                "message": f"Unsupported trigger type. Supported types: {list(TRIGGER_TYPES)}"},
                event=event)

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
            return set_trigger(database_id, workflow_id, trigger_type, request)

        if method == "DELETE":
            if not trigger_type:
                return validation_error(body={"message": "triggerType required"}, event=event)
            return delete_trigger(database_id, workflow_id, trigger_type)

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
        logger.exception(f"Unhandled error in workflowTriggerService: {e}")
        return internal_error(event=event)
