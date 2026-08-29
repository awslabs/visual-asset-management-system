# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Workflow trigger service.

Handles the per-workflow trigger endpoints:
  GET    /database/{databaseId}/workflows/{workflowId}/triggers                 list triggers
  GET    /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}   get a trigger
  PUT    /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}   set/replace a trigger
  DELETE /database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}   delete a trigger

Triggers are a sub-resource of a workflow: Tier-1 gates the route; Tier-2 enforces on the OWNING
workflow object, and a default template the trigger names is additionally scoped to a pipeline the
workflow specifies and that the caller passes Tier-2 GET on. fileUpload is the only trigger type
today; its config carries inputFileFilters (ext/path/name/wildcard) and defaultTemplateIds
({'<pipelineDatabaseId>:<pipelineId>': templateId}).
Trigger rows live in WorkflowTriggersStorageTable (PK workflowDatabaseId:workflowId, SK triggerType);
records built by common.workflows.workflowRecords.
"""

import json
from urllib.parse import unquote

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
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
from common.workflows.triggerTemplateValidation import (
    validate_trigger_default_templates,
    validate_trigger_required_templates,
    trigger_supplied_pipeline_ids,
)

logger = safeLogger(service_name="WorkflowTriggerService")

dynamodb = boto3.resource("dynamodb")
s3_client = boto3.client("s3")

# The headless-template checks read the parent workflow's pipeline records and their default templates
# advisorily: an unreadable table skips the check. Those reads are bounded so an unreachable table
# cannot hold a trigger save open on retries — this same save is what a CDK registration waits on.
# The template SCOPE check shares this resource but not that latitude: it authorizes against the
# pipeline record, so a failed read there refuses the save instead of skipping a check.
lookup_retry_config = Config(retries={"max_attempts": 2, "mode": "standard"},
                             connect_timeout=3, read_timeout=5)
dynamodb_lookup = boto3.resource("dynamodb", config=lookup_retry_config)

try:
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    triggers_table_name = get_table_name(ResourceKeys.WORKFLOW_TRIGGERS_STORAGE_TABLE)
    tag_schema_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE)
    buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    pipeline_table_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    templates_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATES_STORAGE_TABLE)
except Exception as e:
    logger.exception("Failed resolving resource names")
    raise e

OBJECT_TYPE_WORKFLOW = "workflow"
OBJECT_TYPE_PIPELINE = "pipeline"

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


def _pipelines_table():
    return dynamodb_lookup.Table(pipeline_table_name)


def _templates_table():
    return dynamodb_lookup.Table(templates_table_name)


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


def _pipeline_default_template_id(pipeline_database_id, pipeline_id):
    """The templateId of a pipeline's own default template (`isDefault`), or "" when it has none.

    Mirrors executeWorkflow._get_default_template_id, which is the fallback a require-template pipeline
    runs on when nothing names a template for it. A built-in that ships a single template has it
    promoted to default at import, so this is the source that makes those bundles runnable headlessly
    without their trigger naming anything."""
    composite = pr.pipeline_composite_key(pipeline_database_id, pipeline_id)
    kwargs = {"KeyConditionExpression": Key("pipelineDatabaseId:pipelineId").eq(composite)}
    while True:
        response = _templates_table().query(**kwargs)
        for row in response.get("Items", []):
            if row.get("isDefault"):
                return row.get("templateId", "")
        if "LastEvaluatedKey" not in response:
            return ""
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _load_workflow_pipeline_steps(database_id, workflow_item, default_template_ids):
    """The parent workflow's pipeline steps in the shape validate_trigger_required_templates reads:
    {pipelineDatabaseId, pipelineId, defaultTemplateId, systemConfig?, pipelineDefaultTemplateId?}.

    A step the incoming trigger already names a template for, or whose workflow reference carries a
    fallback template, is returned WITHOUT `systemConfig`: a template already reaches it, so whether
    its pipeline requires one is immaterial and the pipeline record is never read. That keeps built-in
    registration free of extra reads — every shipped bundle whose pipeline requires a template names
    that template in its own trigger.

    Best-effort: a step whose pipeline record is unreadable or absent, or whose default template cannot
    be queried, is also returned without `systemConfig`, so a read failure skips the check for that step
    instead of rejecting the save."""
    supplied = trigger_supplied_pipeline_ids(default_template_ids)
    steps = []
    for ref in (workflow_item or {}).get("specifiedPipelines", []) or []:
        pipeline_db = (ref or {}).get("pipelineDatabaseId") or database_id
        pipeline_id = (ref or {}).get("pipelineId", "")
        step = {
            "pipelineDatabaseId": pipeline_db,
            "pipelineId": pipeline_id,
            "defaultTemplateId": (ref or {}).get("defaultTemplateId", "") or "",
        }
        steps.append(step)
        if not pipeline_id or pipeline_id in supplied or step["defaultTemplateId"]:
            continue
        try:
            record = _pipelines_table().get_item(
                Key={"databaseId": pipeline_db, "pipelineId": pipeline_id}).get("Item")
        except Exception as e:
            logger.warning(
                f"Could not read pipeline {pipeline_db}:{pipeline_id} (skipping required-template "
                f"check for it): {e}")
            continue
        if not record:
            logger.info(f"Workflow pipeline {pipeline_db}:{pipeline_id} not found")
            continue
        system_config = record.get("systemConfig") or {}
        step["systemConfig"] = system_config
        if system_config.get("requireTemplate"):
            try:
                step["pipelineDefaultTemplateId"] = _pipeline_default_template_id(
                    pipeline_db, pipeline_id)
            except Exception as e:
                logger.warning(
                    f"Could not read default template for {pipeline_db}:{pipeline_id} (skipping "
                    f"required-template check for it): {e}")
                step.pop("systemConfig", None)
    return steps


def _workflow_pipeline_composites(database_id, workflow_item):
    """The `pipelineDatabaseId:pipelineId` keys the parent workflow's specifiedPipelines snapshot
    names.

    Each reference contributes both its stored composite attribute and the composite rebuilt from its
    id parts, with the workflow's own database as the fallback for a reference carrying no
    pipelineDatabaseId — the same resolution _load_workflow_pipeline_steps applies. A reference
    written by the workflow save, the vamsSchema import, or the deployment migration therefore
    resolves to the same set whichever of those shapes it is in."""
    composites = set()
    for ref in (workflow_item or {}).get("specifiedPipelines", []) or []:
        pipeline_id = (ref or {}).get("pipelineId", "")
        if not pipeline_id:
            continue
        stored = (ref or {}).get("pipelineDatabaseId:pipelineId")
        if stored:
            composites.add(stored)
        pipeline_db = (ref or {}).get("pipelineDatabaseId") or database_id
        composites.add(pr.pipeline_composite_key(pipeline_db, pipeline_id))
    return composites


class PipelineReadError(Exception):
    """The pipeline record behind a referenced default template could not be READ.

    Distinct from the record being absent: absence is a known state of the table, an unreadable row is
    an unknown one, and the trigger scope check answers them differently."""


def _pipeline_record(pipeline_database_id, pipeline_id):
    """A pipeline record by composite key, or None when the record is absent.

    Raises PipelineReadError when the read itself fails. The scope check authorizes the referenced
    template against what this returns, so the two conditions cannot share an answer: reporting a
    failed read as an absent record would authorize the caller against a synthesized placeholder and
    turn throttling, a missing table grant, or a transient error into a pass.

    Bounded read on the lookup resource, so an unreachable pipeline table cannot hold a trigger save
    open on retries."""
    try:
        return _pipelines_table().get_item(
            Key={"databaseId": pipeline_database_id, "pipelineId": pipeline_id}).get("Item")
    except Exception as e:
        logger.warning(
            f"Could not read pipeline {pipeline_database_id}:{pipeline_id} for the trigger "
            f"template scope check: {e}")
        raise PipelineReadError(f"{pipeline_database_id}:{pipeline_id}") from e


def _authorize_referenced_templates(database_id, workflow_item, default_template_ids,
                                    claims_and_roles, event=None):
    """Scope the default templates a trigger may name. Returns an error response, or None when every
    referenced template is in scope for this caller.

    A template is in scope when the parent workflow SPECIFIES its pipeline and the caller passes
    Tier-2 GET on that pipeline object. Both run before any tag schema is read, because the schema
    row is addressed by the caller-supplied `<pipelineDatabaseId>:<pipelineId>:<templateId>`
    composite: validating first reads a template in any database of the deployment and reports its
    required tag NAMES, so Tier-2 on the parent workflow alone would turn one authorized PUT into a
    probe over every other database's templates.

    The pipeline read the second condition needs is fail-closed: a read that raises returns an error
    response rather than an in-scope verdict, because the caller's permission on that pipeline is then
    unknown.

    The rejections name nothing they found (Rule 11) — the composite and the reason go to the log.
    Templates that ARE in scope keep the specific `triggerTemplateErrors` report: their tag names are
    already readable by a caller who passes pipeline GET, and naming the tag is what makes the error
    actionable."""
    if not default_template_ids:
        return None
    # An empty value means "no default template for this pipeline" and is skipped downstream, so such
    # an entry addresses no template and needs no scope.
    referenced = [composite for composite, template_id in default_template_ids.items()
                  if template_id]
    if not referenced:
        return None
    # Fail closed before any comparison: a caller with no authenticated identity learns neither the
    # membership verdict nor the tag names behind it.
    if not claims_and_roles or len(claims_and_roles.get("tokens") or []) == 0:
        return authorization_error()
    specified = _workflow_pipeline_composites(database_id, workflow_item)
    for composite in referenced:
        pipeline_db, _, pipeline_id = (composite or "").partition(":")
        if not pipeline_id:
            continue
        if composite not in specified:
            logger.info(
                f"Rejected trigger save: a default template names pipeline {composite}, which "
                f"workflow {database_id}:{(workflow_item or {}).get('workflowId', '')} does not "
                f"specify")
            return validation_error(body={"message": (
                "A trigger may only choose default templates for pipelines this workflow specifies. "
                "Remove the entries for pipelines that are not part of this workflow.")}, event=event)
        # A read that FAILS decides nothing about this pipeline, so it refuses the save. The
        # alternative — enforcing against a placeholder — is a check that passes on the strength of a
        # throttle or a missing grant, which is worse than no check because it looks like one.
        try:
            record = _pipeline_record(pipeline_db, pipeline_id)
        except PipelineReadError:
            logger.warning(
                f"Rejected trigger save: pipeline {composite} could not be read, so the default "
                f"template named for it cannot be authorized")
            return internal_error(event=event)
        # A pipeline row that is genuinely ABSENT is a known state: it is enforced against a
        # provisional object carrying only the composite ids (mirrors pipelineService._enforce_missing)
        # so the check still runs — and still denies a name/category-scoped role — for a workflow whose
        # snapshot names a pipeline that no longer exists, or one written so recently that this
        # eventually-consistent read has not caught up with it.
        obj = dict(record or {"databaseId": pipeline_db, "pipelineId": pipeline_id})
        obj["object__type"] = OBJECT_TYPE_PIPELINE
        pr.apply_pipeline_constraint_fields(obj, record or {})
        if not CasbinEnforcer(claims_and_roles).enforce(obj, "GET"):
            logger.info(f"Trigger default template denied: no pipeline GET on {composite}")
            return authorization_error()
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
                workflow_item=None, claims_and_roles=None):
    # Scope the templates this trigger names BEFORE anything reads them: the parent workflow must
    # specify their pipeline and the caller must pass Tier-2 GET on it.
    scope_error = _authorize_referenced_templates(
        database_id, workflow_item, request.defaultTemplateIds or {}, claims_and_roles, event)
    if scope_error:
        return scope_error

    # A trigger runs headless, so any default template it names must be renderable with no
    # user-supplied tags: reject the save if a chosen default template has a required tag with no
    # default value. (A trigger never REQUIRES a template — defaultTemplateIds is optional; this only
    # validates the templates it DID choose.)
    template_errors = validate_trigger_default_templates(
        request.defaultTemplateIds or {}, _load_template_tag_schema_fields)
    if template_errors:
        return validation_error(body={"message": {"triggerTemplateErrors": template_errors}})

    # The other half of the headless-template contract: a pipeline of the parent workflow whose
    # systemConfig REQUIRES a template, with no template reaching it from the trigger, the workflow
    # reference, or the pipeline's own default, can never run triggered. Reported under the same
    # `triggerTemplateErrors` list the trigger form and the CLI already render.
    required_template_errors = validate_trigger_required_templates(
        request.defaultTemplateIds or {},
        _load_workflow_pipeline_steps(database_id, workflow_item, request.defaultTemplateIds or {}))
    if required_template_errors:
        logger.info(f"Rejected {trigger_type} trigger: a required template has no default")
        return validation_error(
            body={"message": {"triggerTemplateErrors": required_template_errors}}, event=event)

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
                               workflow_item=workflow_item,
                               claims_and_roles=claims_and_roles)

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
