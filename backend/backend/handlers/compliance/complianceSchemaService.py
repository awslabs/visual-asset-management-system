#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Compliance Schema Service handler.

- GET    /compliance/schemas               — list schemas (latest version of each)
- POST   /compliance/schemas               — register a schema (a new version when the name exists)
- GET    /compliance/schemas/{schemaName}  — get a schema (latest version)
- PUT    /compliance/schemas/{schemaName}  — update a schema (writes a new version)
- DELETE /compliance/schemas/{schemaName}  — delete an unbound schema (every version)

Schema table (PK schemaName, SK internalVersion; GSI DatabaseIdIndex on databaseId/schemaName).
Every schema record the GET routes return carries a top-level `schemaFormat` (`vams-rules-v1` |
`legacy`) derived from the stored body.
"""

import json
from datetime import datetime, timezone

import boto3
from aws_lambda_powertools.utilities.parser import ValidationError, parse
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.conditions import Attr, Key
from botocore.config import Config

from common.apiRoutes import API_COMPLIANCE_SCHEMA_BY_NAME, API_COMPLIANCE_SCHEMAS
from common.compliance import databaseRetirement, knownDatabase
from common.compliance import evaluationEngine as engine
from common.compliance.auditRecord import build_audit_item
from common.dynamodb import query_all_items, query_has_match
from common.resourceNames import ResourceKeys, get_table_name
from common.validators import validate
from common.workflows import pipelineRecords as pr
from common.workflows import systemRecords
from common.compliance.routeAudit import (
    audit_outcome, created_value, failure_outcome, request_parameters, response_count,
)
from customLogging.auditLogging import audit_request, log_action, log_authorization
from customLogging.logger import safeLogger
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from models.common import (
    APIGatewayProxyResponseV2,
    VAMSGeneralErrorResponse,
    authorization_error,
    general_error,
    internal_error,
    success,
    validation_error,
    validation_error_message,
)
from models.compliance import (
    GLOBAL_DATABASE_ID,
    RULE_TYPE_MODELS,
    CreateSchemaRequestModel,
    UpdateSchemaRequestModel,
    VamsRulesV1Schema,
)

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
dynamodb = boto3.resource("dynamodb", config=retry_config)
logger = safeLogger(service_name="ComplianceSchemaService")

claims_and_roles = {}

COMPLIANCE_SCHEMA_OBJECT_TYPE = "complianceSchema"
COMPLIANCE_BINDING_OBJECT_TYPE = "complianceBinding"
# What the database service appends to the id of a soft-deleted database (`<id>#deleted`).
DELETED_DATABASE_MARKER = "#deleted"
# The system actor: the only identity that registers or modifies system schemas.
SYSTEM_USER = "SYSTEM_USER"

try:
    schema_table_name = get_table_name(ResourceKeys.COMPLIANCE_SCHEMA_STORAGE_TABLE)
    asset_state_table_name = get_table_name(ResourceKeys.COMPLIANCE_ASSET_STATE_STORAGE_TABLE)
    audit_table_name = get_table_name(ResourceKeys.COMPLIANCE_AUDIT_STORAGE_TABLE)
    database_table_name = get_table_name(ResourceKeys.DATABASE_STORAGE_TABLE)
    evaluation_table_name = get_table_name(ResourceKeys.COMPLIANCE_EVALUATION_STORAGE_TABLE)
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    pipeline_table_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
except Exception as e:
    logger.exception("Failed loading resource names")
    raise e

schema_table = dynamodb.Table(schema_table_name)
asset_state_table = dynamodb.Table(asset_state_table_name)
audit_table = dynamodb.Table(audit_table_name)
database_table = dynamodb.Table(database_table_name)
# Read for the retirement record of a deleted database's compliance move only
# (`databaseRetirement`; GetItem on `databaseRetirement#` keys).
evaluation_table = dynamodb.Table(evaluation_table_name)
# Read (GetItem only) to decide whether the caller saving a pipeline rule may run its workflow.
workflow_table = dynamodb.Table(workflow_table_name)
pipeline_table = dynamodb.Table(pipeline_table_name)

# A schema of a deleted database (its scope moved to `<databaseId>#deleted`) is part of that
# database's history: it is listed and read, never changed or deleted.
DELETED_DATABASE_SCHEMA_MESSAGE = "The schema belongs to a deleted database and cannot be changed"


#######################
# Pipeline rules: who may save one
#######################

# The answer to a save naming a pipeline rule whose workflow the caller may not run: the rule's name
# only, the same whether the workflow or a pipeline is missing or the caller is refused, so the
# answer says nothing of what exists.
PIPELINE_RULE_REFUSED_MESSAGE = "Not authorized to run the workflow of pipeline rule '{rule}'"
EXECUTE_ROUTE = "/workflows/{workflowDatabaseId}/{workflowId}/execute"


def _pipeline_refs(schema_body):
    """`{rule name: pipelineRef}` of a schema body's own pipeline rules."""
    return {name: rule.get("pipelineRef") or {}
            for name, rule in ((schema_body or {}).get("rules") or {}).items()
            if isinstance(rule, dict) and rule.get("ruleType") == "pipeline"}


def changed_pipeline_rules(schema_body, stored_body=None):
    """The pipeline rules of `schema_body` whose `pipelineRef` is new or changed against the stored
    version's (`stored_body`; None on create, where every pipeline rule is new), in body order, as
    `(rule name, pipelineRef)`. A rule left as it was is not re-checked, so editing another rule of a
    schema never needs the right to run a workflow someone else's rule names."""
    stored = _pipeline_refs(stored_body)
    return [(name, ref) for name, ref in _pipeline_refs(schema_body).items()
            if json.dumps(stored.get(name), sort_keys=True) != json.dumps(ref, sort_keys=True)]


def may_run_workflow(casbin_enforcer, ref):
    """Whether the caller may run the workflow a pipeline rule names, decided as the execute route
    decides it at workflow level (`executeWorkflow.execute_workflow`): Tier 1 POST on the workflow's
    execute route, Tier 2 GET on the workflow, on every pipeline the workflow references and on the
    rule's own `pipelineRef` pipeline. Deny by default: a workflow or pipeline that does not exist is
    refused as one the caller may not run, and its refusal is recorded the way a refused check is.
    Bounded: one GetItem for the workflow and one per pipeline it names (asset checks happen when the
    rule launches)."""
    database_id, workflow_id = ref.get("databaseId"), ref.get("workflowId")
    if not casbin_enforcer.enforce({"object__type": "api", "route__path": EXECUTE_ROUTE.format(
            workflowDatabaseId=database_id, workflowId=workflow_id)}, "POST"):
        return False
    workflow = workflow_table.get_item(
        Key={"databaseId": database_id, "workflowId": workflow_id}).get("Item")
    if not workflow:
        _record_missing("workflow", {"databaseId": database_id, "workflowId": workflow_id})
        return False
    obj = dict(workflow, object__type="workflow")
    obj.setdefault("name", workflow.get("workflowName", ""))
    if not casbin_enforcer.enforce(obj, "GET"):
        return False
    pipelines = [(p.get("pipelineDatabaseId") or database_id, p.get("pipelineId", ""))
                 for p in workflow.get("specifiedPipelines", []) or []]
    pipelines.append((ref.get("pipelineDatabaseId"), ref.get("pipelineId")))
    for pipeline_database_id, pipeline_id in dict.fromkeys(pipelines):
        record = pipeline_table.get_item(
            Key={"databaseId": pipeline_database_id, "pipelineId": pipeline_id}).get("Item")
        if not record:
            _record_missing("pipeline", {"databaseId": pipeline_database_id,
                                         "pipelineId": pipeline_id})
            return False
        if not casbin_enforcer.enforce(pr.apply_pipeline_constraint_fields(
                dict(record, object__type="pipeline"), record), "GET"):
            return False
    return True


def _record_missing(object_type, keys):
    """A refusal for a workflow or pipeline that does not exist, recorded as a refused check is
    (`log_authorization`), so every refusal of a save is in the authorization audit."""
    logger.info(f"Pipeline rule names a {object_type} that does not exist; the save is refused")
    try:
        log_authorization(claims_and_roles, False,
                          {"action": "GET", "obj": dict(keys, object__type=object_type)})
    except Exception as e:
        logger.exception(f"Failed to log the refused pipeline rule check: {e}")


def pipeline_rules_refused(event, casbin_enforcer, schema_body, stored_body=None):
    """The `403` refusing a save whose new or changed pipeline rule names a workflow the caller may
    not run (`may_run_workflow`), naming that rule only; None when every such rule passes. The system
    user (the default schema seed, a registration of a system schema) is not checked: evaluations,
    sweeps and cascades run as it whatever the schema says."""
    if claims_and_roles["tokens"][0] == SYSTEM_USER:
        return None
    for rule_name, ref in changed_pipeline_rules(schema_body, stored_body):
        if not may_run_workflow(casbin_enforcer, ref):
            logger.info(f"Schema save refused: the caller may not run the workflow of pipeline "
                        f"rule '{rule_name}'")
            return authorization_error(body={
                "message": PIPELINE_RULE_REFUSED_MESSAGE.format(rule=rule_name),
                "ruleName": rule_name})
    return None


def _rule_uses_system_record(ref):
    """Whether a pipeline rule's `pipelineRef` names a system workflow or pipeline: its workflow row,
    the rule's own pipeline row or a pipeline the workflow runs carries `isSystem`
    (`systemRecords.names_system_record`). One GetItem for the workflow and one per pipeline, the
    reads `may_run_workflow` makes; a row that does not exist is not a system record."""
    database_id = ref.get("databaseId")
    workflow = workflow_table.get_item(
        Key={"databaseId": database_id, "workflowId": ref.get("workflowId")}).get("Item")
    keys = systemRecords.workflow_pipeline_keys(workflow) + [
        (ref.get("pipelineDatabaseId"), ref.get("pipelineId"))]
    pipelines = [pipeline_table.get_item(
        Key={"databaseId": pipeline_database_id, "pipelineId": pipeline_id}).get("Item")
        for pipeline_database_id, pipeline_id in dict.fromkeys(keys)]
    return systemRecords.names_system_record(workflow, pipelines)


def system_pipeline_rule_refused(event, schema_body, stored_body=None):
    """The `400` refusing a save whose new or changed pipeline rule uses a system workflow or
    pipeline (`systemRecords.SYSTEM_RECORD_PIPELINE_RULE_MESSAGE`, naming the rule); None when no
    such rule uses one. Applies to every caller, the system user included. It runs after
    `pipeline_rules_refused`, so a caller who may not run a rule's workflow, or whose rule names a
    workflow or pipeline that does not exist, gets that 403 and never learns whether it is a system
    record. A rule left as it was is not re-checked: a stored rule that uses a system record is
    recorded as an error when it is evaluated (`complianceEvaluationStore`)."""
    for rule_name, ref in changed_pipeline_rules(schema_body, stored_body):
        if _rule_uses_system_record(ref):
            logger.info(f"Schema save refused: pipeline rule '{rule_name}' uses a system pipeline")
            return validation_error(body={
                "message": systemRecords.SYSTEM_RECORD_PIPELINE_RULE_MESSAGE,
                "ruleName": rule_name}, event=event)
    return None


#######################
# Schema body validation
#######################

# The one client-visible message for a body that is not a vams-rules-v1 document; the specifics
# (missing format marker, missing rules, model errors) are logged.
NOT_VAMS_RULES_MESSAGE = "schemaBody must be a vams-rules-v1 document"


def validate_schema_body(schema_body):
    """Validate a schema body. Only the `vams-rules-v1` format (`schemaFormat: "vams-rules-v1"` plus
    a `rules` object of pipeline, metadata, relationship, fileStructure, versionCoverage and
    assetAttributes rules) is accepted.

    Returns (True, None) when valid, (False, error_message) otherwise. A body that does not declare
    the format is refused with NOT_VAMS_RULES_MESSAGE; a declared body whose rules fail their models
    is refused with the model's caller-safe message.
    """
    if not isinstance(schema_body, dict):
        logger.info("Schema body rejected: not a JSON object")
        return False, NOT_VAMS_RULES_MESSAGE
    if schema_body.get("schemaFormat") != engine.VAMS_RULES_V1:
        logger.info(f"Schema body rejected: schemaFormat is not {engine.VAMS_RULES_V1}")
        return False, NOT_VAMS_RULES_MESSAGE
    return _validate_vams_rules_v1(schema_body)


def _validate_vams_rules_v1(schema_body):
    """Validate a vams-rules-v1 body through its Pydantic models.

    The message returned names fields by their published names only (via
    `validation_error_message`) and locates a failing rule by its position, never by the
    caller-supplied rule name; the full pydantic error is logged.
    """
    try:
        schema = VamsRulesV1Schema(**schema_body)
    except ValidationError as v:
        logger.warning(f"vams-rules-v1 body rejected: {v}")
        return False, validation_error_message(v)
    except (ValueError, TypeError) as e:
        logger.warning(f"vams-rules-v1 body rejected: {e}")
        return False, NOT_VAMS_RULES_MESSAGE

    for position, (rule_name, rule_def) in enumerate(schema.rules.items()):
        try:
            RULE_TYPE_MODELS[rule_def["ruleType"]](**rule_def)
        except ValidationError as v:
            logger.warning(f"vams-rules-v1 rule '{rule_name}' at position {position} rejected: {v}")
            return False, f"rules[{position}]: {validation_error_message(v)}"
        except (ValueError, TypeError) as e:
            logger.warning(f"vams-rules-v1 rule '{rule_name}' at position {position} rejected: {e}")
            return False, f"rules[{position}]: rule definition is not valid"
    return True, None


# Refusal of a database reference outside the compliance schema's own scope.
GLOBAL_SCHEMA_REFERENCE_MESSAGE = (
    "a GLOBAL compliance schema may reference only GLOBAL ({field}); omit metadataSchemaRef."
    "databaseId to use the evaluated asset's database, then GLOBAL")
DATABASE_SCHEMA_REFERENCE_MESSAGE = (
    "a database-scoped compliance schema may reference only GLOBAL or its own database ({field})")


def schema_reference_error(schema_body, schema_database_id):
    """The refusal for a rule that references a database outside the compliance schema's scope, or
    None. A GLOBAL compliance schema binds in every database, so a metadata or pipeline reference to
    one database would hold only in that database: it may reference GLOBAL only (a metadata rule may
    also omit `metadataSchemaRef.databaseId`, which looks the schema up in the evaluated asset's
    database, then GLOBAL). A database-scoped schema binds only in its database, so it may reference
    GLOBAL or that database. Checked on the body's own rules; rules inherited through `extends`
    were checked against the schema that defines them."""
    allowed = {GLOBAL_DATABASE_ID}
    if schema_database_id and schema_database_id != GLOBAL_DATABASE_ID:
        allowed.add(schema_database_id)
    message = (GLOBAL_SCHEMA_REFERENCE_MESSAGE if allowed == {GLOBAL_DATABASE_ID}
               else DATABASE_SCHEMA_REFERENCE_MESSAGE)
    rules = (schema_body or {}).get("rules") or {}
    for position, rule_def in enumerate(rules.values()):
        if not isinstance(rule_def, dict):
            continue
        references = []
        if rule_def.get("ruleType") == "metadata":
            ref = rule_def.get("metadataSchemaRef") or {}
            references.append(("metadataSchemaRef.databaseId", ref.get("databaseId")))
        elif rule_def.get("ruleType") == "pipeline":
            ref = rule_def.get("pipelineRef") or {}
            references.append(("pipelineRef.databaseId", ref.get("databaseId")))
            references.append(("pipelineRef.pipelineDatabaseId", ref.get("pipelineDatabaseId")))
        for field, value in references:
            value = value.strip() if isinstance(value, str) else value
            if value and value not in allowed:
                return f"rules[{position}]: {message.format(field=field)}"
    return None


# The save-time notice for pipeline rules of one schema that name the same workflow.
SHARED_WORKFLOW_WARNING_CODE = "pipelineRulesShareWorkflow"
SHARED_WORKFLOW_WARNING_MESSAGE = (
    "Pipeline rules {rules} run the same workflow. When the workflow restricts concurrency "
    "(perAsset, perInputFile or perInputFileVersion), they run one after another on an asset, "
    "so its evaluation takes as long as their runs together.")


def shared_workflow_warnings(schema_body):
    """The warnings a saved schema body earns: one per workflow (`pipelineRef.databaseId` +
    `pipelineRef.workflowId`) that two or more of its pipeline rules name, `{code, message,
    workflowDatabaseId, workflowId, ruleNames}` in rule order. A workflow that restricts concurrency
    runs such rules one after another on an asset (the second waits for the first rule's run), so the
    evaluation lasts as long as their runs together.

    Computed from the body's own rules only, never from the workflow table: whether the workflow
    restricts concurrency is not read, because the caller may not be allowed to read the workflow,
    and the restriction may change after the save anyway. The save succeeds whatever the warnings."""
    groups = {}
    for rule_name, rule_def in ((schema_body or {}).get("rules") or {}).items():
        if not isinstance(rule_def, dict) or rule_def.get("ruleType") != "pipeline":
            continue
        ref = rule_def.get("pipelineRef") or {}
        workflow = tuple(ref.get(field).strip() if isinstance(ref.get(field), str) else ""
                         for field in ("databaseId", "workflowId"))
        if all(workflow):
            groups.setdefault(workflow, []).append(rule_name)
    return [
        {
            "code": SHARED_WORKFLOW_WARNING_CODE,
            "message": SHARED_WORKFLOW_WARNING_MESSAGE.format(rules=", ".join(names)),
            "workflowDatabaseId": workflow[0],
            "workflowId": workflow[1],
            "ruleNames": names,
        }
        for workflow, names in groups.items() if len(names) > 1
    ]


MISSING_PARENT_WARNING_CODE = "extendsSchemaNotFound"
# One message whether the parent is missing, is not vams-rules-v1, or exists but the caller may not
# read it, so the warning confirms nothing about a schema the caller may not read.
MISSING_PARENT_WARNING_MESSAGE = (
    "The parent schema '{name}' was not found as a vams-rules-v1 schema you may read. If it does "
    "not exist or is not vams-rules-v1, this schema is evaluated with its own rules only.")


def missing_parent_warnings(casbin_enforcer, schema_body):
    """The warning a saved schema body earns when its `extends` names a parent the evaluation
    would not find (`evaluationEngine.resolve_rules`: the parent's latest version, which must be
    vams-rules-v1), `[{code, message, extends}]`, or none.

    Decided only from what the caller may read through the API: the parent is looked up as
    `GET /compliance/schemas/{schemaName}` looks it up (one query for its latest version), and
    counts as found only when the caller may also read it there (Tier 1 GET on that route, Tier 2
    GET on the schema in its scope; an absent parent is checked in the GLOBAL scope). A parent that
    exists but the caller may not read gets the same warning and message as a missing one, so the
    warning leaks nothing. The save succeeds whatever the warning; only the immediate parent is
    checked."""
    parent_name = (schema_body or {}).get("extends")
    if not parent_name or not isinstance(parent_name, str):
        return []
    parent = _latest_schema_item(parent_name)
    readable = (
        casbin_enforcer.enforce({"object__type": "api",
                                 "route__path": f"/compliance/schemas/{parent_name}"}, "GET")
        and casbin_enforcer.enforce(_schema_object(parent_name, _scope_of(parent)), "GET"))
    if readable and parent is not None and engine.is_vams_rules_schema(
            engine.parse_schema_body(parent.get("schemaBody", "{}"))):
        return []
    return [{
        "code": MISSING_PARENT_WARNING_CODE,
        "message": MISSING_PARENT_WARNING_MESSAGE.format(name=parent_name),
        "extends": parent_name,
    }]


#######################
# Lambda handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    # The request's audit context: its action records are written together when it ends.
    with audit_request(event, context):
        return _handle_request(event)


def _handle_request(event) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        method = event["requestContext"]["http"]["method"]

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            log_action(event, **failure_outcome(403))
            return authorization_error()

        if method == "GET":
            return handle_get_request(event)
        elif method == "POST":
            return handle_post_request(event)
        elif method == "PUT":
            return handle_put_request(event)
        elif method == "DELETE":
            return handle_delete_request(event)
        else:
            return validation_error(body={"message": "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        log_action(event, **failure_outcome(400))
        return validation_error(body={"message": validation_error_message(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        log_action(event, **failure_outcome(400))
        return general_error(body={"message": str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        log_action(event, **failure_outcome(500))
        return internal_error(event=event)


#######################
# Method handlers
#######################

def handle_get_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    query_params = event.get("queryStringParameters", {}) or {}

    if API_COMPLIANCE_SCHEMA_BY_NAME.matches(path):
        response = get_schema(event, path_params.get("schemaName"))
        log_action(event, "complianceSchemaGet", **audit_outcome(response),
                   schemaName=path_params.get("schemaName"))
        return response
    if API_COMPLIANCE_SCHEMAS.matches(path):
        response = list_schemas(event, knownDatabase.decoded_scope(query_params.get("databaseId")))
        log_action(event, "complianceSchemaList", **audit_outcome(response),
                   request_parameters=request_parameters(query_params, ("databaseId",)),
                   item_count=response_count(response, "schemas"))
        return response
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_post_request(event):
    path = event["requestContext"]["http"]["path"]
    if API_COMPLIANCE_SCHEMAS.matches(path):
        body = _parse_body(event)
        request = parse(body, model=CreateSchemaRequestModel)
        response = register_schema(event, request)
        # The record names the schema and the version the request wrote (from the service's
        # answer, D197); a name that existed gains a new version.
        log_action(event, "complianceSchemaCreate", **audit_outcome(response),
                   schemaName=request.schemaName, database_id=request.databaseId,
                   changes={"databaseId": request.databaseId, "description": request.description,
                            **_version_written(response)})
        return response
    return validation_error(body={"message": "Method not allowed"}, event=event)


def _version_written(response):
    """`{"internalVersion": n}` of the schema version a successful create or update wrote, from the
    service's answer (`created_value`); empty for any other answer."""
    version = created_value(response, "internalVersion")
    return {"internalVersion": version} if isinstance(version, int) else {}


def handle_put_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_SCHEMA_BY_NAME.matches(path):
        body = _parse_body(event)
        request = parse(body, model=UpdateSchemaRequestModel)
        response = update_schema(event, path_params.get("schemaName"), request)
        # The rule document is caller-authored content: only whether it changed is recorded.
        log_action(event, "complianceSchemaUpdate", **audit_outcome(response),
                   schemaName=path_params.get("schemaName"),
                   changes={"databaseId": request.databaseId, "description": request.description,
                            **_version_written(response)},
                   details={"schemaBodyChanged": request.schemaBody is not None})
        return response
    return validation_error(body={"message": "Method not allowed"}, event=event)


def handle_delete_request(event):
    path = event["requestContext"]["http"]["path"]
    path_params = event.get("pathParameters", {}) or {}
    if API_COMPLIANCE_SCHEMA_BY_NAME.matches(path):
        response = delete_schema(event, path_params.get("schemaName"))
        log_action(event, "complianceSchemaDelete", **audit_outcome(response),
                   schemaName=path_params.get("schemaName"))
        return response
    return validation_error(body={"message": "Method not allowed"}, event=event)


def _parse_body(event):
    """The JSON request body as a dict; a body that is not JSON raises a validation error."""
    raw = event.get("body") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise VAMSGeneralErrorResponse("Invalid JSON in request body")


#######################
# Business logic
#######################

def normalize_schema_item(item):
    """A schema row in its API response shape.

    `schemaFormat` is derived from the stored body: `vams-rules-v1` when the body declares that
    format, `legacy` for a row written before the format existed (a JSON-Schema body). A legacy
    schema cannot be evaluated, updated or bound — it is listed so an operator can find and delete
    it.
    """
    schema_body = item.get("schemaBody", "{}")
    if isinstance(schema_body, str):
        try:
            schema_body = json.loads(schema_body)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "schemaName": item.get("schemaName"),
        "databaseId": item.get("databaseId", GLOBAL_DATABASE_ID),
        "description": item.get("description", ""),
        "schemaFormat": engine.schema_format(schema_body),
        "schemaBody": schema_body,
        "version": int(item.get("internalVersion", 1)),
        "createdAt": item.get("registeredAt"),
        "isSystem": bool(item.get("isSystem", False)),
    }


def _schema_object(schema_name, database_id):
    """The Tier-2 authorization object for a schema: its name and its scope (`databaseId`, a
    database id or GLOBAL)."""
    return {
        "object__type": COMPLIANCE_SCHEMA_OBJECT_TYPE,
        "complianceSchemaName": schema_name or "",
        "databaseId": database_id or GLOBAL_DATABASE_ID,
    }


def _enforce(schema_name, database_id, action):
    """Tier-2 check on a schema in its scope. Fails closed on an empty token list."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    return CasbinEnforcer(claims_and_roles).enforce(_schema_object(schema_name, database_id), action)


def _binding_object(database_id):
    return {"object__type": COMPLIANCE_BINDING_OBJECT_TYPE, "databaseId": database_id or ""}


def schema_permissions(casbin_enforcer, schema, binding_database_id=None):
    """The caller's allowed actions on one listed schema, computed server-side so a client hides
    what the caller cannot do: `edit` (PUT), `delete` (DELETE) and `bind`. `bind` needs GET on the
    schema (the caller has it, the schema is listed) and PUT on the `complianceBinding` of the
    database it would be bound in: `binding_database_id` when the listing names one, the schema's
    own database for a database-scoped schema, and for a GLOBAL schema listed without a database
    the GET alone (whether a given database may bind it is checked when it is bound). A system
    schema is never editable or deletable by a caller other than the system user."""
    name, database_id = schema["schemaName"], schema["databaseId"]
    is_system_locked = (schema.get("isSystem") and claims_and_roles["tokens"][0] != SYSTEM_USER
                        ) or databaseRetirement.is_deleted_scope(database_id)
    # A GLOBAL filter names no database to bind in (a new database's form asks for the schemas any
    # database may bind), so it is treated as no filter.
    if binding_database_id == GLOBAL_DATABASE_ID:
        binding_database_id = None
    target = binding_database_id or (database_id if database_id != GLOBAL_DATABASE_ID else None)
    bind = not databaseRetirement.is_deleted_scope(database_id) and schema.get(
        "schemaFormat") == engine.VAMS_RULES_V1 and (
        target is None or casbin_enforcer.enforce(_binding_object(target), "PUT"))
    return {
        "edit": not is_system_locked and casbin_enforcer.enforce(
            _schema_object(name, database_id), "PUT"),
        "delete": not is_system_locked and casbin_enforcer.enforce(
            _schema_object(name, database_id), "DELETE"),
        "bind": bool(bind),
    }


def creatable_scopes(casbin_enforcer):
    """The scopes the caller may create a schema in: GLOBAL and every database whose
    `complianceSchema` POST passes. The database table is small and is scanned for its ids. A
    soft-deleted database (its id carries `#deleted`) is not a scope, as the database listing
    leaves it out: a schema cannot be created in it."""
    candidates = [GLOBAL_DATABASE_ID] + sorted(
        item["databaseId"] for item in _scan_all_items(database_table, ProjectionExpression="databaseId")
        if item.get("databaseId") and item["databaseId"] != GLOBAL_DATABASE_ID
        and DELETED_DATABASE_MARKER not in item["databaseId"])
    return [database_id for database_id in candidates
            if casbin_enforcer.enforce(_schema_object("", database_id), "POST")]


def list_schemas(event, database_id_filter=None):
    """List the latest version of every schema, filtered to those the caller may GET.

    With `databaseId`, only GLOBAL schemas and those scoped to that database are listed
    (DatabaseIdIndex GSI); without it, every partition of the GSI is read.
    """
    presenter = databaseRetirement.Presenter(evaluation_table)
    if database_id_filter:
        (valid, message) = knownDatabase.validate_scope(database_id_filter)
        if not valid:
            return validation_error(body={"message": message}, event=event)
        database_ids = [GLOBAL_DATABASE_ID]
        # While a deleted database's schemas move under `<databaseId>#deleted`, those still under
        # the live id are the deleted database's, not the live id's (`databaseRetirement`).
        if database_id_filter != GLOBAL_DATABASE_ID and not presenter.moving(database_id_filter):
            database_ids.append(database_id_filter)
        items = _query_schemas_for_databases(database_ids)
    else:
        stored = _query_all_schemas()
        # The retirement records of every schema's database are read together (D133).
        presenter.prime(item.get("databaseId") for item in stored)
        items = [presenter.present(item) for item in stored]

    schemas_by_name = {}
    for item in items:
        name = item["schemaName"]
        version = int(item.get("internalVersion", 1))
        if name not in schemas_by_name or version > schemas_by_name[name]["version"]:
            schemas_by_name[name] = normalize_schema_item(item)

    casbin_enforcer = CasbinEnforcer(claims_and_roles) if len(claims_and_roles["tokens"]) > 0 else None
    allowed = []
    for schema in schemas_by_name.values():
        # List filtering appends only when enforce() passes, so empty tokens yield an empty list.
        if casbin_enforcer and casbin_enforcer.enforce(
                _schema_object(schema["schemaName"], schema["databaseId"]), "GET"):
            schema["permissions"] = schema_permissions(casbin_enforcer, schema, database_id_filter)
            allowed.append(schema)

    body = {"schemas": allowed}
    if not database_id_filter:
        # The Schemas page's create form offers these scopes only.
        body["createScopes"] = creatable_scopes(casbin_enforcer) if casbin_enforcer else []
    return success(body=body)


def _query_schemas_for_databases(database_ids):
    """Every schema row whose databaseId is one of `database_ids` (DatabaseIdIndex, paged)."""
    items = []
    for database_id in database_ids:
        items.extend(query_all_items(
            schema_table,
            IndexName="DatabaseIdIndex",
            KeyConditionExpression=Key("databaseId").eq(database_id),
        ))
    return items


def _query_all_schemas():
    """Every schema row. The schema table has no constant-partition index, so the complete
    unfiltered listing is a scan paged to exhaustion (schemas are administrator-defined and few)."""
    return _scan_all_items(schema_table)


def _scan_all_items(table, **scan_kwargs):
    """Scan a table to exhaustion, following LastEvaluatedKey by presence."""
    items = []
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            return items
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def get_schema(event, schema_name):
    """The latest version of one schema."""
    (valid, message) = validate({"schemaName": {"value": schema_name, "validator": "ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    item = _latest_schema_item(schema_name)
    # An absent schema is checked in the GLOBAL scope, so a caller without schema read access is
    # refused rather than told whether the name exists.
    if not _enforce(schema_name, _scope_of(item), "GET"):
        return authorization_error()
    if not item:
        return general_error(body={"message": "Schema not found"}, event=event)
    return success(body=normalize_schema_item(item))


def _scope_of(item):
    """The scope (`databaseId`) of a stored schema row; GLOBAL for a row without one or no row."""
    return (item or {}).get("databaseId") or GLOBAL_DATABASE_ID


def _latest_schema_item(schema_name):
    """The highest internalVersion row of a schema, or None."""
    response = schema_table.query(
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


def register_schema(event, request: CreateSchemaRequestModel):
    """Register a schema. A name that already exists gains a new version."""
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    # Creating checks the target scope: POST on a schema of that database, or of GLOBAL.
    if not _enforce(request.schemaName, request.databaseId, "POST"):
        return authorization_error()
    # A name that exists gains a new version, which replaces the current one (and may move it to
    # another scope), so the caller must also be allowed to update the current version.
    current = _latest_schema_item(request.schemaName)
    if current and not _enforce(request.schemaName, _scope_of(current), "PUT"):
        return authorization_error()

    if current and databaseRetirement.is_deleted_scope(_scope_of(current)):
        return general_error(body={"message": DELETED_DATABASE_SCHEMA_MESSAGE}, event=event)
    if databaseRetirement.move_in_progress(evaluation_table, request.databaseId):
        return databaseRetirement.moving_response(event)
    if request.databaseId != GLOBAL_DATABASE_ID and not _database_exists(request.databaseId):
        return general_error(body={"message": "Database not found"}, event=event)

    valid, err = validate_schema_body(request.schemaBody)
    if not valid:
        return _schema_body_rejected(event, err)
    err = schema_reference_error(request.schemaBody, request.databaseId)
    if err:
        return _schema_body_rejected(event, err)
    refused = (pipeline_rules_refused(event, CasbinEnforcer(claims_and_roles), request.schemaBody,
                                      _stored_body(current))
               or system_pipeline_rule_refused(event, request.schemaBody, _stored_body(current)))
    if refused is not None:
        return refused

    # Only the system actor registers system schemas (which no other caller can later modify).
    is_system = bool(request.isSystem) and claims_and_roles["tokens"][0] == SYSTEM_USER
    return _write_schema_version(event, request.schemaName, request.databaseId,
                                 request.description or "", request.schemaBody, is_system)


def _stored_body(item):
    """The schema body a stored version holds, or None (no version, or a body that is not JSON)."""
    if not item:
        return None
    try:
        body = json.loads(item.get("schemaBody") or "{}")
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _schema_body_rejected(event, err):
    """The 400 for a schema body `validate_schema_body` refused: the generic format message as is,
    a model message prefixed as an invalid schema."""
    logger.info(f"Schema body rejected: {err}")
    message = err if err == NOT_VAMS_RULES_MESSAGE else f"Invalid schema: {err}"
    return validation_error(body={"message": message}, event=event)


def _write_schema_version(event, schema_name, database_id, description, schema_body, is_system):
    """Write the next internalVersion row of a schema."""
    current = _latest_schema_item(schema_name)
    next_version = int(current["internalVersion"]) + 1 if current else 1
    now = datetime.now(timezone.utc).isoformat()

    schema_table.put_item(Item={
        "schemaName": schema_name,
        "internalVersion": next_version,
        "databaseId": database_id,
        "description": description,
        "schemaBody": json.dumps(schema_body),
        "registeredAt": now,
        "registeredBy": claims_and_roles["tokens"][0],
        "isSystem": is_system,
    })

    warnings = (shared_workflow_warnings(schema_body)
                + missing_parent_warnings(CasbinEnforcer(claims_and_roles), schema_body))
    logger.info(f"Registered schema '{schema_name}' v{next_version}"
                + (f" with {len(warnings)} warning(s)" if warnings else ""))
    return success(body={
        "message": f"Schema '{schema_name}' registered as v{next_version}",
        "schemaName": schema_name,
        "databaseId": database_id,
        "internalVersion": next_version,
        "warnings": warnings,
    })


def update_schema(event, schema_name, request: UpdateSchemaRequestModel):
    """Update a schema by writing a new version. A system schema is only updated by SYSTEM_USER."""
    (valid, message) = validate({"schemaName": {"value": schema_name, "validator": "ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    current = _latest_schema_item(schema_name)
    if not _enforce(schema_name, _scope_of(current), "PUT"):
        return authorization_error()
    if not current:
        return general_error(body={"message": "Schema not found"}, event=event)

    if current.get("isSystem") and claims_and_roles["tokens"][0] != SYSTEM_USER:
        return general_error(body={"message": "System schemas cannot be modified"}, event=event)
    if databaseRetirement.is_deleted_scope(_scope_of(current)):
        return general_error(body={"message": DELETED_DATABASE_SCHEMA_MESSAGE}, event=event)

    database_id = request.databaseId or current.get("databaseId", GLOBAL_DATABASE_ID)
    # Moving a schema to another scope also needs PUT in the scope it moves to.
    if database_id != _scope_of(current) and not _enforce(schema_name, database_id, "PUT"):
        return authorization_error()
    if databaseRetirement.move_in_progress(evaluation_table, database_id) or \
            databaseRetirement.move_in_progress(evaluation_table, _scope_of(current)):
        return databaseRetirement.moving_response(event)
    if database_id != GLOBAL_DATABASE_ID and not _database_exists(database_id):
        return general_error(body={"message": "Database not found"}, event=event)

    if request.schemaBody is not None:
        schema_body = request.schemaBody
    else:
        schema_body = normalize_schema_item(current)["schemaBody"]
    valid, err = validate_schema_body(schema_body)
    if not valid:
        return _schema_body_rejected(event, err)
    # Checked against the scope the new version has, so moving a schema to another scope is
    # refused while its rules reference a database outside it.
    err = schema_reference_error(schema_body, database_id)
    if err:
        return _schema_body_rejected(event, err)
    refused = (pipeline_rules_refused(event, CasbinEnforcer(claims_and_roles), schema_body,
                                      _stored_body(current))
               or system_pipeline_rule_refused(event, schema_body, _stored_body(current)))
    if refused is not None:
        return refused

    description = request.description if request.description is not None else current.get("description", "")
    return _write_schema_version(event, schema_name, database_id, description, schema_body,
                                 bool(current.get("isSystem", False)))


def delete_schema(event, schema_name):
    """Delete every version of a schema that no database or asset is bound to."""
    (valid, message) = validate({"schemaName": {"value": schema_name, "validator": "ID"}})
    if not valid:
        return validation_error(body={"message": message}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforce(schema_name, _scope_of(_latest_schema_item(schema_name)), "DELETE"):
        return authorization_error()

    rows = query_all_items(
        schema_table,
        KeyConditionExpression=Key("schemaName").eq(schema_name),
        ProjectionExpression="schemaName, internalVersion",
    )
    if not rows:
        return general_error(body={"message": "Schema not found"}, event=event)

    current = _latest_schema_item(schema_name) or {}
    if current.get("isSystem") and claims_and_roles["tokens"][0] != SYSTEM_USER:
        return general_error(body={"message": "System schemas cannot be deleted"}, event=event)
    if databaseRetirement.is_deleted_scope(_scope_of(current)) or \
            databaseRetirement.move_in_progress(evaluation_table, _scope_of(current)):
        return general_error(body={"message": DELETED_DATABASE_SCHEMA_MESSAGE}, event=event)

    if _schema_is_bound(schema_name):
        logger.info(f"Schema '{schema_name}' is still bound; delete refused")
        return general_error(
            body={"message": "Schema is bound to a database or asset and cannot be deleted"},
            event=event)

    with schema_table.batch_writer() as batch:
        for row in rows:
            batch.delete_item(Key={
                "schemaName": row["schemaName"], "internalVersion": row["internalVersion"],
            })

    audit_table.put_item(Item=build_audit_item(
        "*", "*", "schema_deleted", claims_and_roles["tokens"][0],
        {"versionsDeleted": len(rows)}, schema_name=schema_name,
    ))

    logger.info(f"Deleted schema '{schema_name}' ({len(rows)} versions)")
    return success(body={
        "message": "Schema deleted",
        "schemaName": schema_name,
        "versionsDeleted": len(rows),
    })


def _schema_is_bound(schema_name):
    """Whether any asset-state row (SchemaNameIndex) or any database row references the schema.

    A database binding is the `complianceSchemaName` attribute on the database row, which the
    database table carries no index on, so that half is a scan paged to exhaustion and filtered
    to the attribute (empty `Items` with a `LastEvaluatedKey` means "keep paging", not "unbound").
    """
    if query_has_match(
        asset_state_table,
        IndexName="SchemaNameIndex",
        KeyConditionExpression=Key("schemaName").eq(schema_name),
    ):
        return True
    scan_kwargs = {
        "FilterExpression": Attr("complianceSchemaName").eq(schema_name),
        "ProjectionExpression": "databaseId",
    }
    while True:
        response = database_table.scan(**scan_kwargs)
        if response.get("Items"):
            return True
        if "LastEvaluatedKey" not in response:
            return False
        scan_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]


def _database_exists(database_id):
    """Whether a database row exists."""
    return "Item" in database_table.get_item(Key={"databaseId": database_id})
