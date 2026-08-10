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

import base64
import json

import boto3
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import ValidationError

from common.validators import validate
from common.resourceNames import get_table_name, ResourceKeys
from common.apiRoutes import API_PIPELINE_TEMPLATE_TAG_SCHEMA
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
from common.workflows import templateRender as tr
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

# Pipelines in this database are shared across every database in the deployment.
GLOBAL_DATABASE = "GLOBAL"

# HTTP methods that reconfigure a template / tag schema (and therefore the owning pipeline's
# configuration).
WRITE_METHODS = ("POST", "PUT", "DELETE")

# Templates per page of the list response, and the ceiling a caller can request. A list descriptor
# carries its inline body, which may reach the inline threshold (320KB), so the ceiling keeps a
# worst-case page well under the 6 MB Lambda synchronous-response limit.
TEMPLATES_PAGE_SIZE = 10


def _page_size(query_params):
    """Requested page size clamped to [1, TEMPLATES_PAGE_SIZE]; the default applies for an absent or
    unparseable value."""
    raw = (query_params or {}).get("pageSize") or (query_params or {}).get("maxItems")
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return TEMPLATES_PAGE_SIZE
    if size < 1:
        return TEMPLATES_PAGE_SIZE
    return min(size, TEMPLATES_PAGE_SIZE)


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


def _casbin_object(item):
    """The Tier-2 Casbin object for a pipeline row: the record + object__type + the flat ABAC
    constraint fields (name from pipelineName; pipelineExecutionType from executionConfig)."""
    obj = dict(item)
    obj["object__type"] = OBJECT_TYPE
    pr.apply_pipeline_constraint_fields(obj, item)
    return obj


def _enforce_pipeline(item, action, claims_and_roles):
    """Tier-2 object check on a pipeline row. Denies when there are no tokens to enforce."""
    if claims_and_roles and len(claims_and_roles["tokens"]) > 0:
        return CasbinEnforcer(claims_and_roles).enforce(_casbin_object(item), action)
    return False


def _enforce_missing_pipeline(database_id, pipeline_id, action, claims_and_roles):
    """Tier-2 check for a pipeline row that does not exist, run against a provisional record carrying
    only the path-scoped ids. An unauthorized caller therefore cannot use the 404 as an existence
    oracle for pipelines it may not see."""
    return _enforce_pipeline(
        {"databaseId": database_id, "pipelineId": pipeline_id}, action, claims_and_roles)


def _enforce_parent_pipeline(database_id, pipeline_id, action, claims_and_roles):
    """Tier-2: authorize against the owning pipeline object. Returns (allowed, pipeline_item)."""
    response = _pipeline_table().get_item(Key={"databaseId": database_id, "pipelineId": pipeline_id})
    item = response.get("Item")
    if not item:
        return None, None
    return _enforce_pipeline(item, action, claims_and_roles), item


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


def _delete_tag_schema_object(key):
    """Best-effort delete of an offloaded tag-schema object; a failed delete is logged, not fatal
    (the object is orphaned, not incorrect)."""
    try:
        s3_client.delete_object(Bucket=_default_bucket_name(), Key=key)
    except Exception as e:  # noqa: BLE001 - cleanup is best-effort
        logger.warning(f"Failed deleting offloaded tag-schema object {key}: {e}")


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
    prior_s3_key = ""
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
        if existing and existing[0].get("bodyStorage") == tbs.BODY_STORAGE_S3:
            prior_s3_key = existing[0].get("fieldsS3Key") or ""
        record = pr.build_tag_schema_record(
            database_id, pipeline_id, template_id, fields, tag_schema_id=tag_schema_id,
            created_by=username, modified_by=username,
        )
    _tag_schema_table().put_item(Item=record)
    # A shrink-to-inline transition leaves the prior offloaded object unreferenced; delete it only
    # after the row is rewritten so a failed write leaves a row whose S3 key still resolves.
    if prior_s3_key:
        _delete_tag_schema_object(prior_s3_key)
    return []


def _set_tag_schema_on_template(database_id, pipeline_id, template_id, template_row, fields,
                                username):
    """Validate + persist a tag schema against the template that already exists, applying the same
    cross-checks the template PUT applies. Returns a mapping of error lists (empty = stored).

    The schema and the stored configBody are validated together because they are one contract: a tag's
    declared type decides whether its placeholder may stand unquoted as a JSON value, so changing a
    type here can leave a body that renders structurally invalid JSON — which the pipeline silently
    discards in favour of its built-in defaults. Trigger references are checked for the same reason
    update_template checks them: a headless run cannot supply a required tag that carries no default."""
    trigger_errors = validate_template_not_breaking_triggers(
        _triggers_table(), database_id, pipeline_id, template_id, fields)
    if trigger_errors:
        return {"triggerTemplateErrors": trigger_errors}

    config_format = template_row.get("configFormat", "json")
    config_body = ""
    if config_format == "json":
        # Only a json body is parse-checked, and only a body that references a tag can be affected by
        # the schema — so the S3 read for an offloaded body is skipped for every other case.
        config_body = _rehydrate_template(template_row)["configBody"]
        if not tr.uses_template_tags(config_body):
            config_body = ""
    if config_body:
        try:
            _validate_template_bodies(config_format, config_body, None, fields)
        except ValidationError as ve:
            logger.exception(f"Validation error: {ve}")
            return {"tagSchemaErrors": [validation_error_message(ve)]}
        except ValueError as ve:
            return {"tagSchemaErrors": [str(ve)]}

    errors = _store_tag_schema(database_id, pipeline_id, template_id, fields, username)
    if errors:
        return {"tagSchemaErrors": errors}
    return {}


#######################
# Template body storage
#######################

def _store_template_bodies(database_id, pipeline_id, template_id, config_body, web_form_json):
    """Decide inline-vs-S3 for a template's bodies (offload to the default bucket when large) and
    return the storage fields to merge onto the template record. Raises TemplateBodyTooLargeError
    when the combined body exceeds the absolute cap.

    When a prior S3-offloaded row transitions to inline (body shrank below the threshold) the
    now-unreferenced offloaded objects are left in place: the caller cleans them up via
    _delete_offloaded_objects AFTER the row is rewritten, so a failed write leaves a stored row
    whose S3 keys still resolve."""
    tbs.assert_within_cap(config_body, web_form_json)
    plan = tbs.plan_body_storage(config_body, web_form_json)
    if not plan["offload"]:
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


def _request_body(event):
    """Parsed JSON request body as a mapping. A valid-JSON-but-non-object body (list/string/null)
    is a client error, not an internal one."""
    body = json.loads(event.get("body") or "{}")
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")
    return body


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
    this pipeline. Called BEFORE the template that is becoming the default is written, so a failure
    part-way through leaves the pipeline with NO default — which the execute path reports as
    "this pipeline requires a template" — rather than two, which it would resolve silently by
    templateId sort order.

    The read is strongly consistent: an eventually-consistent query can miss a default written
    moments earlier by another request and leave it flagged."""
    composite = pr.pipeline_composite_key(database_id, pipeline_id)
    table = _templates_table()
    query_kwargs = {"KeyConditionExpression": Key("pipelineDatabaseId:pipelineId").eq(composite),
                    "ConsistentRead": True}
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

def _decode_starting_token(starting_token):
    """Decode a base64 pagination token back into an ExclusiveStartKey, or None when it cannot be
    decoded or does not carry a key mapping."""
    try:
        decoded = json.loads(base64.b64decode(starting_token).decode("utf-8"))
    except Exception as e:
        logger.exception(f"Invalid startingToken: {e}")
        return None
    return decoded if isinstance(decoded, dict) and decoded else None


def list_templates(database_id, pipeline_id, query_params=None):
    """One page of a pipeline's template descriptors plus a NextToken when more remain. An inline
    body can be up to the inline threshold (320KB), so the page is bounded to keep the response
    under the 6MB Lambda limit; callers drain the pages via startingToken.

    Raises ValueError for a token that cannot be decoded: continuing without it would serve page 1
    again carrying the same NextToken, which is an endless loop for a client draining the pages."""
    params = query_params or {}
    composite = pr.pipeline_composite_key(database_id, pipeline_id)
    query_kwargs = {
        "KeyConditionExpression": Key("pipelineDatabaseId:pipelineId").eq(composite),
        "Limit": _page_size(params),
    }
    starting_token = params.get("startingToken") or params.get("NextToken")
    if starting_token:
        decoded = _decode_starting_token(starting_token)
        if decoded is None:
            raise ValueError("startingToken is invalid.")
        query_kwargs["ExclusiveStartKey"] = decoded
    response = _templates_table().query(**query_kwargs)
    # Lightweight list descriptors — no per-row S3 rehydration of offloaded bodies. Callers fetch
    # the full configBody/webFormJson via the single-template GET.
    items = [_template_to_response_light(row) for row in response.get("Items", [])]
    result = GetTemplatesResponseModel(Items=items)
    if "LastEvaluatedKey" in response:
        result.NextToken = base64.b64encode(
            json.dumps(response["LastEvaluatedKey"]).encode("utf-8")).decode("utf-8")
    return result


def create_template(database_id, pipeline_id, request, username, event=None):
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
            _triggers_table(), database_id, pipeline_id, template_id, request.tagSchema)
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
    # Enforce single-default-per-pipeline BEFORE the write: unset any prior default so the pipeline is
    # never durably left with two, which the execute path would resolve by templateId sort order.
    if request.isDefault:
        _clear_other_defaults(database_id, pipeline_id, template_id)
    _templates_table().put_item(Item=record)
    # AUDIT LOG: template created. The configuration BODY is deliberately not logged — it can carry
    # prompts and credential-shaped values; the id, format and default flag identify it.
    log_actions(event or {}, "pipelineTemplateCreate", {
        "databaseId": database_id,
        "pipelineId": pipeline_id,
        "templateId": template_id,
        "configFormat": record.get("configFormat", ""),
        "isDefault": bool(request.isDefault),
        "operation": "create",
    })

    tag_fields = None
    if request.tagSchema is not None:
        errors = _store_tag_schema(database_id, pipeline_id, template_id, request.tagSchema, username)
        if errors:
            return validation_error(body={"message": {"tagSchemaErrors": errors}})
        tag_fields = request.tagSchema

    return success(body={"message": _template_to_response(record, tag_schema_fields=tag_fields).dict()})


def update_template(database_id, pipeline_id, template_id, request, username, event=None):
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
            _triggers_table(), database_id, pipeline_id, template_id, request.tagSchema)
        if trigger_errors:
            return validation_error(body={"message": {"triggerTemplateErrors": trigger_errors}})

    # Validate the EFFECTIVE body against the EFFECTIVE format (each the request's when supplied,
    # else the stored row's). The request model sees neither stored value, so a partial update that
    # changes only one of the two must be checked here: a body-only change against the persisted
    # format, and a format-only change against the persisted body.
    effective_format = request.configFormat if request.configFormat is not None else row.get("configFormat", "json")
    format_changed = (request.configFormat is not None
                      and request.configFormat != row.get("configFormat", "json"))
    # A tagSchema-only update is checked too. The schema and the body are one contract — a tag's
    # declared type decides whether its placeholder renders into valid JSON — so retyping a tag can
    # break a body that neither the request nor this handler otherwise touches. Without this the
    # template PUT accepted a change the tag-schema PUT (which validates the same pair in
    # _set_tag_schema_on_template) rejected, leaving two routes to the same contract disagreeing.
    if request.configBody is not None or format_changed or request.tagSchema is not None:
        effective_body = (request.configBody if request.configBody is not None
                          else _rehydrate_template(row)["configBody"])
        # The EFFECTIVE tag schema, for the same reason as the body and format: a body-only update must
        # be checked against the tags the template already declares, or a typed placeholder that is
        # legal for the stored schema would be rejected. Read from storage only when the request does
        # not carry one AND the effective body actually references a tag — a body with no placeholders
        # is parse-checked directly, so the schema cannot change the verdict and the read would be
        # wasted work on the common case.
        effective_tag_schema = request.tagSchema
        if effective_tag_schema is None and tr.uses_template_tags(effective_body):
            effective_tag_schema = _load_tag_schema_fields(database_id, pipeline_id, template_id)
        try:
            _validate_template_bodies(effective_format, effective_body, None, effective_tag_schema)
        # pydantic's ValidationError SUBCLASSES ValueError, so without this arm ABOVE the one
        # below a model-validation failure is caught there and str()'d whole into the response —
        # leaking the model class name and pydantic's error taxonomy (backend Rule 11). Placing it
        # after the ValueError arm would make it dead code.
        except ValidationError as ve:
            logger.exception(f"Validation error: {ve}")
            return validation_error(body={"message": validation_error_message(ve)})
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
    orphaned_storage = None
    if request.configBody is not None or request.webFormJson is not None:
        # Snapshot the prior storage state so a shrink-to-inline transition knows which S3 objects
        # to clean up (the field mutations above do not touch bodyStorage/*S3Key, but snapshot to be
        # explicit).
        prior_storage = {
            "bodyStorage": row.get("bodyStorage"),
            "configBodyS3Key": row.get("configBodyS3Key"),
            "webFormS3Key": row.get("webFormS3Key"),
        }
        current = _rehydrate_template(row)
        config_body = request.configBody if request.configBody is not None else current["configBody"]
        web_form = request.webFormJson if request.webFormJson is not None else current["webFormJson"]
        storage = _store_template_bodies(
            database_id, pipeline_id, template_id, config_body, web_form)
        # A shrink-to-inline transition leaves the prior offloaded objects unreferenced. They are
        # deleted only after the row is rewritten below, so a failed write leaves a stored row whose
        # S3 keys still resolve instead of an unreadable template.
        if (storage["bodyStorage"] == tbs.BODY_STORAGE_INLINE
                and prior_storage["bodyStorage"] == tbs.BODY_STORAGE_S3):
            orphaned_storage = prior_storage
        row.update(storage)

    row["dateModified"] = pr.iso_now()
    row["modifiedBy"] = username
    # Enforce single-default-per-pipeline before the write, for the same reason as on create.
    if request.isDefault:
        _clear_other_defaults(database_id, pipeline_id, template_id)
    _templates_table().put_item(Item=row)
    # AUDIT LOG: template updated (body omitted, as on create).
    log_actions(event or {}, "pipelineTemplateUpdate", {
        "databaseId": database_id,
        "pipelineId": pipeline_id,
        "templateId": template_id,
        "operation": "update",
    })
    if orphaned_storage:
        _delete_offloaded_objects(orphaned_storage)

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


def delete_template(database_id, pipeline_id, template_id, event=None):
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
            _delete_tag_schema_object(tag_row["fieldsS3Key"])
    _delete_tag_schema_rows(tag_rows)
    # AUDIT LOG: template deleted (a real delete, not an archive).
    log_actions(event or {}, "pipelineTemplateDelete", {
        "databaseId": database_id,
        "pipelineId": pipeline_id,
        "templateId": template_id,
        "operation": "delete",
    })
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
        query_parameters = event.get("queryStringParameters", {}) or {}
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
            # Authorize against a provisional record first so the 404 is not an existence oracle.
            if not _enforce_missing_pipeline(database_id, pipeline_id, method, claims_and_roles):
                return authorization_error()
            return validation_error(status_code=404, body={"message": "Pipeline not found"}, event=event)
        if not allowed_obj:
            return authorization_error()

        # A GLOBAL pipeline's templates and tag schemas drive its behavior in every database, so
        # reconfiguring them additionally requires pipeline management (PUT) permission on the GLOBAL
        # scope. The pipeline object action POST is shared by "run this pipeline" and "create", so
        # run-only roles carry POST on GLOBAL pipelines.
        if (database_id == GLOBAL_DATABASE and method in WRITE_METHODS
                and not _enforce_pipeline(pipeline_item, "PUT", claims_and_roles)):
            logger.info(f"{method} on GLOBAL pipeline {pipeline_id} templates denied: no GLOBAL "
                        f"pipeline management")
            return authorization_error()

        is_tag_schema = API_PIPELINE_TEMPLATE_TAG_SCHEMA.matches(path)

        if is_tag_schema:
            # The tag schema is a sub-resource of a template; the template must exist.
            template_row = _get_template_row(database_id, pipeline_id, template_id)
            if not template_row:
                return validation_error(status_code=404, body={"message": "Template not found"}, event=event)
            if method == "GET":
                fields = _load_tag_schema_fields(database_id, pipeline_id, template_id) or []
                return success(body={"message": TagSchemaResponseModel(
                    pipelineDatabaseId=database_id, pipelineId=pipeline_id,
                    templateId=template_id, fields=fields).dict()})
            if method == "PUT":
                request = SetTagSchemaRequestModel(**_request_body(event))
                # Round-trip through JSON so enum types serialize to their string values (a plain
                # .dict() would leave TemplateTagType enum objects that are not JSON-serializable).
                fields = [json.loads(f.json()) for f in request.fields]
                errors = _set_tag_schema_on_template(
                    database_id, pipeline_id, template_id, template_row, fields, username)
                if errors:
                    return validation_error(body={"message": errors}, event=event)
                return success(body={"message": TagSchemaResponseModel(
                    pipelineDatabaseId=database_id, pipelineId=pipeline_id,
                    templateId=template_id, fields=fields).dict()})
            return validation_error(body={"message": "Method not allowed"}, event=event)

        if method == "GET":
            if template_id:
                row = _get_template_row(database_id, pipeline_id, template_id)
                if not row:
                    return validation_error(status_code=404, body={"message": "Template not found"}, event=event)
                fields = _load_tag_schema_fields(database_id, pipeline_id, template_id)
                return success(body={"message": _template_to_response(row, tag_schema_fields=fields).dict()})
            return success(body={
                "message": list_templates(database_id, pipeline_id, query_parameters).dict()})

        if method == "POST":
            request = CreateTemplateRequestModel(**_request_body(event))
            return create_template(database_id, pipeline_id, request, username, event)

        if method == "PUT":
            if not template_id:
                return validation_error(body={"message": "templateId required"}, event=event)
            request = UpdateTemplateRequestModel(**_request_body(event))
            return update_template(database_id, pipeline_id, template_id, request, username, event)

        if method == "DELETE":
            if not template_id:
                return validation_error(body={"message": "templateId required"}, event=event)
            return delete_template(database_id, pipeline_id, template_id, event)

        return validation_error(body={"message": "Method not allowed"}, event=event)

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
        logger.exception(f"Unhandled error in pipelineTemplateService: {e}")
        return internal_error(event=event)
