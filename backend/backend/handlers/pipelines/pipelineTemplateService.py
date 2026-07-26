# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pipeline template + tag-schema service.

Handles the per-pipeline template + tag-schema endpoints:
  GET    /database/{databaseId}/pipelines/{pipelineId}/templates                 list templates
  POST   /database/{databaseId}/pipelines/{pipelineId}/templates                 create a template
  GET    /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}    template details
  PUT    /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}    update a template
  DELETE /database/{databaseId}/pipelines/{pipelineId}/templates/{templateId}    delete a template
  GET/PUT .../templates/{templateId}/tagSchema                                   get / set tag schema

Clients never touch S3: the handler stores configBody/webFormJson inline when small and offloads to
the default asset bucket under pipelines/ when large (templateBodyStorage), rehydrating inline on
read. The tag schema lives in its own table (fields as a JSON string, mirroring metadata schema) and
is validated by the shared common.templateTagSchema validator (reserved-key + primitive-type rules).

Authorization is scoped to the owning pipeline: Tier-1 gates the route; Tier-2 enforces on the parent
pipeline object (templates have no independent permission fields).
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
from models.pipelines import (
    CreateTemplateRequestModel,
    UpdateTemplateRequestModel,
    TemplateResponseModel,
    GetTemplatesResponseModel,
    SetTagSchemaRequestModel,
    TagSchemaResponseModel,
    _validate_template_bodies,
)
from common.workflows import pipelineRecords as pr
from common.workflows import templateBodyStorage as tbs
from common.workflows import templateTagSchema as tts
from common.workflows.defaultBucket import resolve_default_bucket, DefaultBucketNotFoundError
from common.workflows.triggerTemplateValidation import validate_template_not_breaking_triggers

logger = safeLogger(service_name="PipelineTemplateService")

dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")

try:
    pipeline_table_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    templates_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATES_STORAGE_TABLE)
    tag_schema_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE)
    buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

OBJECT_TYPE = "pipeline"


def _pipeline_table():
    return dynamodb.Table(pipeline_table_name)


def _templates_table():
    return dynamodb.Table(templates_table_name)


def _tag_schema_table():
    return dynamodb.Table(tag_schema_table_name)


def _triggers_table():
    return dynamodb.Table(triggers_table_name)


def _workflow_table():
    return dynamodb.Table(workflow_table_name)


def _default_bucket_name():
    return resolve_default_bucket(dynamodb.Table(buckets_table_name))["bucketName"]


def _enforce_parent_pipeline(database_id, pipeline_id, action, claims_and_roles):
    """Tier-2: authorize against the owning pipeline object. Returns (allowed, pipeline_item)."""
    response = _pipeline_table().get_item(Key={"databaseId": database_id, "pipelineId": pipeline_id})
    item = response.get("Item")
    if not item:
        return None, None
    if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
        obj = dict(item)
        obj["object__type"] = OBJECT_TYPE
        obj.setdefault("name", item.get("pipelineName", ""))
        return CasbinEnforcer(claims_and_roles).enforce(obj, action), item
    return False, item


#######################
# Tag schema
#######################

def _load_tag_schema_fields(database_id, pipeline_id, template_id):
    """Return the parsed tag-schema fields list for a template (rehydrating from S3 when offloaded),
    or [] when no schema row exists."""
    owner = pr.template_owner_key(database_id, pipeline_id, template_id)
    response = _tag_schema_table().query(
        IndexName="TagSchemaByTemplateGSI",
        KeyConditionExpression=Key("pipelineDatabaseId:pipelineId:templateId").eq(owner),
    )
    rows = response.get("Items", [])
    if not rows:
        return None
    row = rows[0]
    if row.get("bodyStorage") == tbs.BODY_STORAGE_S3 and row.get("fieldsS3Key"):
        text = tbs.read_body_from_s3(s3_client, _default_bucket_name(), row["fieldsS3Key"])
        return json.loads(text) if text else []
    fields = row.get("fields") or ""
    return json.loads(fields) if fields else []


def _existing_tag_schema_rows(database_id, pipeline_id, template_id):
    """Return the tag-schema rows for a template's owner key (GSI query)."""
    owner = pr.template_owner_key(database_id, pipeline_id, template_id)
    return _tag_schema_table().query(
        IndexName="TagSchemaByTemplateGSI",
        KeyConditionExpression=Key("pipelineDatabaseId:pipelineId:templateId").eq(owner),
    ).get("Items", [])


def _delete_tag_schema_rows(rows):
    """Delete a list of tag-schema rows by their (PK, SK)."""
    table = _tag_schema_table()
    for row in rows:
        table.delete_item(Key={
            "tagSchemaId": row["tagSchemaId"],
            "pipelineDatabaseId:pipelineId:templateId": row["pipelineDatabaseId:pipelineId:templateId"],
        })


def _store_tag_schema(database_id, pipeline_id, template_id, fields, username):
    """Validate + persist a template's tag schema. Idempotent: reuses the existing owner row's
    tagSchemaId (and removes any stray duplicates) so a re-set OVERWRITES a single row — matching the
    one-row-per-template MetadataSchema paradigm. Returns an errors list (empty = stored)."""
    errors = tts.validate_tag_schema(fields)
    if errors:
        return errors

    # Reuse the existing owner row's PK so put_item overwrites it; clean up any duplicate rows a
    # prior non-idempotent write may have left, keeping exactly one row per template.
    existing = _existing_tag_schema_rows(database_id, pipeline_id, template_id)
    tag_schema_id = existing[0]["tagSchemaId"] if existing else ""
    if len(existing) > 1:
        _delete_tag_schema_rows(existing[1:])

    fields_json = json.dumps(fields or [])
    # Reuse the body-size cap/threshold for the (independently-stored) schema.
    if tbs.should_offload(fields_json, ""):
        key = tbs.tag_schema_s3_key(database_id, pipeline_id, template_id)
        tbs.write_body_to_s3(s3_client, _default_bucket_name(), key, fields_json)
        record = pr.build_tag_schema_record(
            database_id, pipeline_id, template_id, fields, tag_schema_id=tag_schema_id,
            body_storage=tbs.BODY_STORAGE_S3, fields_s3_key=key,
            fields_hash=tbs.content_hash(fields_json), created_by=username, modified_by=username,
        )
    else:
        record = pr.build_tag_schema_record(
            database_id, pipeline_id, template_id, fields, tag_schema_id=tag_schema_id,
            created_by=username, modified_by=username,
        )
    _tag_schema_table().put_item(Item=record)
    return []


#######################
# Template body storage
#######################

def _store_template_bodies(database_id, pipeline_id, template_id, config_body, web_form_json,
                           prior_row=None):
    """Decide inline-vs-S3 for a template's bodies (offload to the default bucket when large) and
    return the storage fields to merge onto the template record. Raises TemplateBodyTooLargeError
    when the combined body exceeds the absolute cap. When a prior S3-offloaded row transitions to
    inline (body shrank below the threshold), its offloaded objects are cleaned up."""
    tbs.assert_within_cap(config_body, web_form_json)
    plan = tbs.plan_body_storage(config_body, web_form_json)
    if not plan["offload"]:
        # Transitioning from S3 back to inline: remove the now-unreferenced offloaded objects.
        if prior_row and prior_row.get("bodyStorage") == tbs.BODY_STORAGE_S3:
            _delete_offloaded_objects(prior_row)
        return {
            "bodyStorage": tbs.BODY_STORAGE_INLINE,
            "configBody": config_body or "",
            "webFormJson": web_form_json or "",
            "configBodyS3Key": "", "configBodyHash": plan["configBodyHash"],
            "webFormS3Key": "", "webFormHash": plan["webFormHash"],
        }
    bucket = _default_bucket_name()
    cb_key = tbs.config_body_s3_key(database_id, pipeline_id, template_id)
    wf_key = tbs.web_form_s3_key(database_id, pipeline_id, template_id)
    tbs.write_body_to_s3(s3_client, bucket, cb_key, config_body or "")
    tbs.write_body_to_s3(s3_client, bucket, wf_key, web_form_json or "")
    return {
        "bodyStorage": tbs.BODY_STORAGE_S3,
        "configBody": "", "webFormJson": "",
        "configBodyS3Key": cb_key, "configBodyHash": plan["configBodyHash"],
        "webFormS3Key": wf_key, "webFormHash": plan["webFormHash"],
    }


def _rehydrate_template(row):
    """Return the template row's configBody/webFormJson inline (reading S3 when offloaded)."""
    return tbs.rehydrate_template_bodies(s3_client, _default_bucket_name(), row)


def _template_to_response_light(row):
    """List-view descriptor: same fields as _template_to_response but WITHOUT rehydrating the
    configBody/webFormJson from S3 — an S3-offloaded body is omitted (empty) here. This keeps the
    templates list a bounded metadata response (no per-row S3 GetObject N+1, no accumulation of large
    bodies past the 6MB Lambda limit); the single-template GET rehydrates the full body."""
    return TemplateResponseModel(
        pipelineDatabaseId=row.get("pipelineDatabaseId", ""),
        pipelineId=row.get("pipelineId", ""),
        templateId=row.get("templateId", ""),
        templateName=row.get("templateName", ""),
        description=row.get("description", ""),
        configFormat=row.get("configFormat", "json"),
        # Inline bodies are cheap to include; an offloaded body is left empty in the list view.
        configBody=row.get("configBody", "") if row.get("bodyStorage") != "s3" else "",
        webFormJson=row.get("webFormJson", "") if row.get("bodyStorage") != "s3" else "",
        allowCustomEdit=row.get("allowCustomEdit", False),
        inputInstructions=row.get("inputInstructions", ""),
        overrides=row.get("overrides", {}),
        isDefault=bool(row.get("isDefault", False)),
        tagSchema=None,
        dateCreated=row.get("dateCreated", ""),
        dateModified=row.get("dateModified", ""),
        createdBy=row.get("createdBy", ""),
        modifiedBy=row.get("modifiedBy", ""),
        schemaVersion=row.get("schemaVersion", 1),
    )


def _template_to_response(row, tag_schema_fields=None):
    bodies = _rehydrate_template(row)
    return TemplateResponseModel(
        pipelineDatabaseId=row.get("pipelineDatabaseId", ""),
        pipelineId=row.get("pipelineId", ""),
        templateId=row.get("templateId", ""),
        templateName=row.get("templateName", ""),
        description=row.get("description", ""),
        configFormat=row.get("configFormat", "json"),
        configBody=bodies["configBody"],
        webFormJson=bodies["webFormJson"],
        allowCustomEdit=row.get("allowCustomEdit", False),
        inputInstructions=row.get("inputInstructions", ""),
        overrides=row.get("overrides", {}),
        isDefault=bool(row.get("isDefault", False)),
        tagSchema=tag_schema_fields,
        dateCreated=row.get("dateCreated", ""),
        dateModified=row.get("dateModified", ""),
        createdBy=row.get("createdBy", ""),
        modifiedBy=row.get("modifiedBy", ""),
        schemaVersion=row.get("schemaVersion", 1),
    )


def _get_template_row(database_id, pipeline_id, template_id):
    composite = pr.pipeline_composite_key(database_id, pipeline_id)
    response = _templates_table().get_item(
        Key={"pipelineDatabaseId:pipelineId": composite, "templateId": template_id}
    )
    return response.get("Item")


def _clear_other_defaults(database_id, pipeline_id, keep_template_id):
    """Ensure at most one default template per pipeline: unset isDefault on every OTHER template of
    this pipeline. Called when a template is created/updated as the default."""
    composite = pr.pipeline_composite_key(database_id, pipeline_id)
    table = _templates_table()
    query_kwargs = {"KeyConditionExpression": Key("pipelineDatabaseId:pipelineId").eq(composite)}
    while True:
        response = table.query(**query_kwargs)
        for row in response.get("Items", []):
            if row.get("templateId") == keep_template_id:
                continue
            if row.get("isDefault"):
                table.update_item(
                    Key={"pipelineDatabaseId:pipelineId": composite,
                         "templateId": row["templateId"]},
                    UpdateExpression="SET isDefault = :f",
                    ExpressionAttributeValues={":f": False},
                )
        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


#######################
# Data operations
#######################

def list_templates(database_id, pipeline_id):
    composite = pr.pipeline_composite_key(database_id, pipeline_id)
    items = []
    query_kwargs = {"KeyConditionExpression": Key("pipelineDatabaseId:pipelineId").eq(composite)}
    table = _templates_table()
    while True:
        response = table.query(**query_kwargs)
        for row in response.get("Items", []):
            # Lightweight list descriptors — no per-row S3 rehydration of offloaded bodies. Callers
            # fetch the full configBody/webFormJson via the single-template GET.
            items.append(_template_to_response_light(row))
        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return GetTemplatesResponseModel(Items=items)


def create_template(database_id, pipeline_id, request, username):
    template_id = request.templateId or pr.new_guid()
    if _get_template_row(database_id, pipeline_id, template_id):
        logger.info(f"Template {database_id}:{pipeline_id}:{template_id} already exists")
        return validation_error(body={"message": "A template with this ID already exists."})

    # Validate the tag schema BEFORE persisting the template so a bad schema does not leave an
    # orphaned template row / S3 objects (create is all-or-nothing on the validatable inputs).
    if request.tagSchema is not None:
        schema_errors = tts.validate_tag_schema(request.tagSchema)
        if schema_errors:
            return validation_error(body={"message": {"tagSchemaErrors": schema_errors}})
        # If this template is (already) a trigger default, a required-without-default tag would break
        # the headless trigger. On create the id is usually new, but a client may supply an id that a
        # trigger already references, so check here too.
        trigger_errors = validate_template_not_breaking_triggers(
            _triggers_table(), _workflow_table(), database_id, pipeline_id, template_id,
            request.tagSchema)
        if trigger_errors:
            return validation_error(body={"message": {"triggerTemplateErrors": trigger_errors}})

    storage = _store_template_bodies(
        database_id, pipeline_id, template_id, request.configBody, request.webFormJson
    )
    record = pr.build_template_record(
        pipeline_database_id=database_id, pipeline_id=pipeline_id, template_id=template_id,
        template_name=request.templateName, description=request.description or "",
        config_format=request.configFormat, allow_custom_edit=bool(request.allowCustomEdit),
        input_instructions=request.inputInstructions or "",
        body_storage=storage["bodyStorage"], config_body=storage["configBody"],
        web_form_json=storage["webFormJson"], config_body_s3_key=storage["configBodyS3Key"],
        config_body_hash=storage["configBodyHash"], web_form_s3_key=storage["webFormS3Key"],
        web_form_hash=storage["webFormHash"], overrides=request.overrides or {},
        is_default=bool(request.isDefault),
        created_by=username, modified_by=username,
    )
    _templates_table().put_item(Item=record)
    # Enforce single-default-per-pipeline: unset any prior default when this one is the default.
    if request.isDefault:
        _clear_other_defaults(database_id, pipeline_id, template_id)

    tag_fields = None
    if request.tagSchema is not None:
        errors = _store_tag_schema(database_id, pipeline_id, template_id, request.tagSchema, username)
        if errors:
            return validation_error(body={"message": {"tagSchemaErrors": errors}})
        tag_fields = request.tagSchema

    return success(body={"message": _template_to_response(record, tag_schema_fields=tag_fields).dict()})


def update_template(database_id, pipeline_id, template_id, request, username):
    row = _get_template_row(database_id, pipeline_id, template_id)
    if not row:
        return validation_error(status_code=404, body={"message": "Template not found"})

    # Validate the tag schema before applying any field/body changes (all-or-nothing).
    if request.tagSchema is not None:
        schema_errors = tts.validate_tag_schema(request.tagSchema)
        if schema_errors:
            return validation_error(body={"message": {"tagSchemaErrors": schema_errors}})
        # A template already chosen as a trigger default must stay renderable headlessly: reject an
        # update that introduces a required tag with no default value while triggers reference it.
        trigger_errors = validate_template_not_breaking_triggers(
            _triggers_table(), _workflow_table(), database_id, pipeline_id, template_id,
            request.tagSchema)
        if trigger_errors:
            return validation_error(body={"message": {"triggerTemplateErrors": trigger_errors}})

    # Validate a new configBody against its EFFECTIVE format (the request's when supplied, else the
    # stored row's). The request model cannot see the stored format, so a partial update that changes
    # only configBody must be JSON-checked here against the template's persisted configFormat.
    if request.configBody is not None:
        effective_format = request.configFormat if request.configFormat is not None else row.get("configFormat", "json")
        try:
            _validate_template_bodies(effective_format, request.configBody, None)
        except ValueError as ve:
            return validation_error(body={"message": str(ve)})

    if request.templateName is not None:
        row["templateName"] = request.templateName
    if request.description is not None:
        row["description"] = request.description
    if request.configFormat is not None:
        row["configFormat"] = request.configFormat
    if request.allowCustomEdit is not None:
        row["allowCustomEdit"] = request.allowCustomEdit
    if request.inputInstructions is not None:
        row["inputInstructions"] = request.inputInstructions
    if request.overrides is not None:
        row["overrides"] = request.overrides
    if request.isDefault is not None:
        row["isDefault"] = bool(request.isDefault)

    # Re-run body storage when either body is being changed (rehydrate the unchanged one first).
    if request.configBody is not None or request.webFormJson is not None:
        # Snapshot the prior storage state so a shrink-to-inline transition can clean up S3 objects
        # (the field mutations above do not touch bodyStorage/*S3Key, but snapshot to be explicit).
        prior_storage = {
            "bodyStorage": row.get("bodyStorage"),
            "configBodyS3Key": row.get("configBodyS3Key"),
            "webFormS3Key": row.get("webFormS3Key"),
        }
        current = _rehydrate_template(row)
        config_body = request.configBody if request.configBody is not None else current["configBody"]
        web_form = request.webFormJson if request.webFormJson is not None else current["webFormJson"]
        storage = _store_template_bodies(
            database_id, pipeline_id, template_id, config_body, web_form, prior_row=prior_storage)
        row.update(storage)

    row["dateModified"] = pr.iso_now()
    row["modifiedBy"] = username
    _templates_table().put_item(Item=row)
    # Enforce single-default-per-pipeline when this update sets the template as the default.
    if request.isDefault:
        _clear_other_defaults(database_id, pipeline_id, template_id)

    tag_fields = None
    if request.tagSchema is not None:
        errors = _store_tag_schema(database_id, pipeline_id, template_id, request.tagSchema, username)
        if errors:
            return validation_error(body={"message": {"tagSchemaErrors": errors}})
        tag_fields = request.tagSchema

    return success(body={"message": _template_to_response(row, tag_schema_fields=tag_fields).dict()})


def _delete_offloaded_objects(row):
    """Best-effort delete of a template row's offloaded S3 bodies (deterministic keys). No-op for
    inline rows or missing keys; a failed delete is logged, not fatal (the object is orphaned, not
    incorrect)."""
    if row.get("bodyStorage") != tbs.BODY_STORAGE_S3:
        return
    bucket = _default_bucket_name()
    for key in (row.get("configBodyS3Key"), row.get("webFormS3Key")):
        if key:
            try:
                s3_client.delete_object(Bucket=bucket, Key=key)
            except Exception as e:  # noqa: BLE001 - cleanup is best-effort
                logger.warning(f"Failed deleting offloaded template object {key}: {e}")


def delete_template(database_id, pipeline_id, template_id):
    composite = pr.pipeline_composite_key(database_id, pipeline_id)
    row = _get_template_row(database_id, pipeline_id, template_id)
    if not row:
        return validation_error(status_code=404, body={"message": "Template not found"})
    _templates_table().delete_item(
        Key={"pipelineDatabaseId:pipelineId": composite, "templateId": template_id}
    )
    # Clean up offloaded S3 bodies + tag-schema S3 objects, then the associated tag-schema row(s).
    _delete_offloaded_objects(row)
    tag_rows = _existing_tag_schema_rows(database_id, pipeline_id, template_id)
    for tag_row in tag_rows:
        if tag_row.get("bodyStorage") == tbs.BODY_STORAGE_S3 and tag_row.get("fieldsS3Key"):
            try:
                s3_client.delete_object(Bucket=_default_bucket_name(), Key=tag_row["fieldsS3Key"])
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed deleting offloaded tag-schema object: {e}")
    _delete_tag_schema_rows(tag_rows)
    return success(body={"message": "Template deleted"})


#######################
# Route handlers
#######################

def _validate_ids(path_parameters):
    """Validate databaseId + pipelineId (+ templateId when present) via the ID validator, so a
    path id can't reach the S3-key / DynamoDB-key construction unvalidated."""
    checks = [("databaseId", True), ("pipelineId", False)]
    if path_parameters.get("templateId") is not None:
        checks.append(("templateId", False))
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
        method = event["requestContext"]["http"]["method"]
        path = event["requestContext"]["http"]["path"]

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
        pipeline_id = path_parameters["pipelineId"]
        template_id = path_parameters.get("templateId")
        username = claims_and_roles["tokens"][0] if claims_and_roles.get("tokens") else ""

        # Tier-2 against the owning pipeline; the action mirrors the HTTP verb.
        allowed_obj, pipeline_item = _enforce_parent_pipeline(
            database_id, pipeline_id, method, claims_and_roles
        )
        if pipeline_item is None:
            return validation_error(status_code=404, body={"message": "Pipeline not found"}, event=event)
        if not allowed_obj:
            return authorization_error()

        is_tag_schema = path.endswith("/tagSchema")

        if is_tag_schema:
            # The tag schema is a sub-resource of a template; the template must exist.
            if not _get_template_row(database_id, pipeline_id, template_id):
                return validation_error(status_code=404, body={"message": "Template not found"}, event=event)
            if method == "GET":
                fields = _load_tag_schema_fields(database_id, pipeline_id, template_id) or []
                return success(body={"message": TagSchemaResponseModel(
                    pipelineDatabaseId=database_id, pipelineId=pipeline_id,
                    templateId=template_id, fields=fields).dict()})
            if method == "PUT":
                request = SetTagSchemaRequestModel(**json.loads(event.get("body") or "{}"))
                # Round-trip through JSON so enum types serialize to their string values (a plain
                # .dict() would leave TemplateTagType enum objects that are not JSON-serializable).
                fields = [json.loads(f.json()) for f in request.fields]
                errors = _store_tag_schema(database_id, pipeline_id, template_id, fields, username)
                if errors:
                    return validation_error(body={"message": {"tagSchemaErrors": errors}}, event=event)
                return success(body={"message": TagSchemaResponseModel(
                    pipelineDatabaseId=database_id, pipelineId=pipeline_id,
                    templateId=template_id, fields=fields).dict()})
            return authorization_error(body={"message": "Method not allowed"})

        if method == "GET":
            if template_id:
                row = _get_template_row(database_id, pipeline_id, template_id)
                if not row:
                    return validation_error(status_code=404, body={"message": "Template not found"}, event=event)
                fields = _load_tag_schema_fields(database_id, pipeline_id, template_id)
                return success(body={"message": _template_to_response(row, tag_schema_fields=fields).dict()})
            return success(body={"message": list_templates(database_id, pipeline_id).dict()})

        if method == "POST":
            request = CreateTemplateRequestModel(**json.loads(event.get("body") or "{}"))
            return create_template(database_id, pipeline_id, request, username)

        if method == "PUT":
            if not template_id:
                return validation_error(body={"message": "templateId required"}, event=event)
            request = UpdateTemplateRequestModel(**json.loads(event.get("body") or "{}"))
            return update_template(database_id, pipeline_id, template_id, request, username)

        if method == "DELETE":
            if not template_id:
                return validation_error(body={"message": "templateId required"}, event=event)
            return delete_template(database_id, pipeline_id, template_id)

        return authorization_error(body={"message": "Method not allowed"})

    except tbs.TemplateBodyTooLargeError as te:
        return validation_error(body={"message": str(te)}, event=event)
    except DefaultBucketNotFoundError as de:
        logger.exception(f"Default bucket not resolved: {de}")
        return internal_error(event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={"message": str(v)}, event=event)
    except json.JSONDecodeError:
        return validation_error(body={"message": "Invalid JSON in request body"}, event=event)
    except ValueError as ve:
        logger.exception(f"Validation error: {ve}")
        return validation_error(body={"message": str(ve)}, event=event)
    except Exception as e:
        logger.exception(f"Unhandled error in pipelineTemplateService: {e}")
        return internal_error(event=event)
