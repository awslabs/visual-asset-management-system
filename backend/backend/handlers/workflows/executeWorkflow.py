# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Asset-less multi-file workflow execute handler.

The execute route carries an asset-less request with a multi-file input object array, an optional
output-target override, per-pipeline template execution parameters, and an optional execution group
id:

  POST /workflows/{workflowDatabaseId}/{workflowId}/execute

Flow (before launch):
  1. Tier-1 (enforceAPI) + parse/validate the request.
  2. Resolve + authorize (Tier-2) the workflow; gate enabled + not archived.
  3. Resolve + authorize (Tier-2) every referenced pipeline; gate enabled + not archived.
  4. Resolve + authorize the input assets (GET) and the output-target asset (POST).
  5. Verify every selected input file exists in its own asset bucket (version-aware).
  6. Per-pipeline template resolution (templateResolution) + tag validation.
  7. Cross-entity validation (executionValidation.validate_execution) — arity, scope, filters.
  8. Build the grouped input-metadata payload, launch the state machine, persist the V2 records
     (including a per-pipeline config snapshot: templateId, tag schema version, tags, override flag).

Run I/O (manifests, per-pipeline config files, shared output/aux prefixes) lives in the VAMS default
asset bucket (defaultBucket.resolve_default_bucket); input files are still read from their OWN asset
buckets (each manifest entry carries its own bucket). Output-target write-back targets the output
asset's own bucket, resolved independently by the end-state process-output lambda.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor

import boto3
import botocore
from boto3.dynamodb.conditions import Key
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError

from common.validators import validate
from common.resourceNames import get_table_name, get_bucket_name, ResourceKeys
from common.auth.apiEvent import normalize_event
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_actions
from common.workflows import executionRecords as er
from common.workflows import executionValidation as ev
from common.workflows import templateResolution as tres
from common.workflows import templateRender as tr
from common.workflows import pipelineRecords as pr
from common.workflows import workflowRecords as wr
from common.workflows import templateBodyStorage as tbs
from common.workflows import outputPathExtension as ope
from common.workflows.defaultBucket import resolve_default_bucket, DefaultBucketNotFoundError
from models.common import (
    APIGatewayProxyResponseV2,
    internal_error,
    success,
    validation_error,
    authorization_error,
    general_error,
    VAMSGeneralErrorResponse,
)
from models.executions import (
    ExecuteWorkflowRequestV2Model,
    ExecuteWorkflowResponseModel,
    TRIGGER_TYPE_TO_STORED,
)

logger = safeLogger(service="ExecuteWorkflow")

# Claims/roles for the current request (set per-invocation in lambda_handler).
claims_and_roles = {}

# Per-request memo of asset-bucket details keyed by bucketId (reset at each invocation). Bucket rows
# are immutable for a launch, so this collapses the repeated per-input/per-pass buckets-table reads.
_bucket_details_cache = {}

GLOBAL_DATABASE = "GLOBAL"
OBJECT_TYPE_WORKFLOW = "workflow"
OBJECT_TYPE_PIPELINE = "pipeline"
OBJECT_TYPE_ASSET = "asset"
OBJECT_TYPE_DATABASE = "database"

# Output-target location types. "asset" writes outputs onto an asset; "none" is results-only —
# the execution produces no asset files/metadata, only results text + logs stored against the
# execution transaction (e.g. an LLM-style pipeline returning a textual response). "none" must be
# a non-empty sentinel: the record builders coerce a falsy location_type back to "asset".
OUTPUT_LOCATION_TYPE_ASSET = "asset"
OUTPUT_LOCATION_TYPE_NONE = "none"

# Upper bound on candidate input rows inspected by the concurrency guard so a launch never fans out
# into an unbounded number of describe_execution calls (mirrors the V1 handler).
MAX_CONCURRENCY_CANDIDATES_INSPECTED = 200

# Worker bound for the per-input-file fan-out (S3 existence checks + metadata-service reads). The
# input selection is capped at MAX_INPUT_FILES_PER_EXECUTION, so a large selection issues that many
# independent calls; running them through a bounded pool keeps a many-file launch inside the API
# request window without letting a single execute saturate downstream concurrency.
MAX_PARALLEL_INPUT_WORKERS = 10

# Caps on the metadata captured for ONE entity row of the grouped envelope — each database, each asset,
# and each file's metadata and attributes are bounded independently, so a run over many entities leaves
# every entity its own budget. The entry cap bounds how MANY keys a row carries; the byte cap bounds how
# LARGE it is, which the entry cap alone cannot do because a metadata value carries no length limit — a
# few hundred long values, or one very long one, otherwise build a row past the 400 KB DynamoDB item
# limit, failing the input-metadata write after the state machine has already started. The byte budget
# leaves room for the row's identity attributes and for DynamoDB's per-element map overhead, which a
# JSON byte count does not reflect. Truncation is deterministic (keys are retained in sort order) and is
# both logged and surfaced as an execute warning, so a capped row is never silently short.
MAX_METADATA_ENTRIES_PER_ENTITY = 1000
MAX_METADATA_BYTES_PER_ENTITY = 300 * 1024

# Cap on the content ONE execution's grouped envelope carries across every entity together — every row's
# metadata plus the assetData of every involved asset. The per-entity caps bound each row but not how many
# rows there are: a request may name MAX_INPUT_FILES_PER_EXECUTION input files and
# MAX_METADATA_SOURCE_ASSETS_PER_EXECUTION metadata-source assets, which assemble into thousands of rows
# whose per-entity ceilings sum past a gigabyte — held in Lambda memory, serialized into the run's S3
# metadata object, and written out as one DynamoDB row each per pipeline of the run. Bytes is the unit
# because the ROW COUNT is already bounded structurally by those two request caps, while the total SIZE is
# the dimension nothing bounds: a metadata value has no individual length limit, so a cap on rows or
# entries still admits a gigabyte. One byte budget therefore bounds the S3 metadata object, the Lambda
# working set, and the total write volume together. It is measured the way the per-entity byte cap is
# (_metadata_entry_bytes: key + value + per-entry framing), so it covers the content and not the
# envelope's own JSON scaffolding — a run at the bound serializes a few tens of KB above it. Metadata rows
# are retained most-useful-first and deterministically, while assetData holds its place (see
# _apply_total_metadata_budget); a row left out is logged and surfaced as an execute warning rather than
# silently dropped.
#
# The value clears a heavy REAL workload rather than a small one. A production asset carries thousands of
# files and hundreds of metadata entries each, so a run selecting the full MAX_INPUT_FILES_PER_EXECUTION
# with a few hundred entries per file assembles tens of megabytes of content — a budget sized against a
# lightly-populated asset would truncate ordinary work and report it as a warning, which is worse than not
# bounding at all. 128 MB leaves that case untouched while still capping the run a Lambda cannot hold:
# the write path's real ceiling is memory and in-request time (the function has 5308 MB), and the
# separately-bounded detail view protects the read path, so the budget only has to keep one execution's
# metadata inside a fraction of the working set.
MAX_METADATA_BYTES_PER_EXECUTION = 128 * 1024 * 1024

# Per-entry framing (quotes, separators, DynamoDB map element overhead) added to each key/value pair
# when measuring a row against MAX_METADATA_BYTES_PER_ENTITY.
METADATA_ENTRY_FRAMING_BYTES = 8

# How many entity names one execute warning lists when several rows truncate, or several databases
# cannot be read, so a run over many entities returns a bounded warning rather than one per entity.
MAX_METADATA_NOTICE_ENTITIES_LISTED = 5

# Cap on the output base path extension AFTER its template tags are rendered. Matches the raw-value cap
# the request model and the workflow systemConfig validator apply, so a value that only becomes
# oversized once rendered is rejected at launch rather than failing every object write on S3's
# 1024-byte key limit.
MAX_OUTPUT_PATH_EXTENSION_LENGTH = 1024

# Whether the DeadlineCloud execution type is enabled for this deployment. Execution is blocked when a
# referenced pipeline is DeadlineCloud but the type has been turned off (the workflow's createJob task
# state + the job-callback lambda are not deployed, so the execution would hang on an unresolvable task
# token). This guards the case where a DeadlineCloud pipeline/workflow was created while the type was
# enabled and the deployment later disabled it.
DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED = (
    os.environ.get("DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED", "false").strip().lower() == "true")

dynamodb = boto3.resource("dynamodb")
s3c = boto3.client("s3")
lambda_client = boto3.client("lambda")
sfn_client = boto3.client("stepfunctions")

try:
    asset_table_name = get_table_name(ResourceKeys.ASSET_STORAGE_TABLE)
    workflow_table_name = get_table_name(ResourceKeys.WORKFLOW_STORAGE_TABLE_V2)
    pipeline_table_name = get_table_name(ResourceKeys.PIPELINE_STORAGE_TABLE_V2)
    templates_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATES_STORAGE_TABLE)
    tag_schema_table_name = get_table_name(ResourceKeys.PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE)
    buckets_table_name = get_table_name(ResourceKeys.S3_ASSET_BUCKETS_STORAGE_TABLE)
    bucket_name_assetAuxiliary = get_bucket_name(ResourceKeys.ASSET_AUXILIARY_BUCKET)
    metadata_service_function = os.environ["METADATA_SERVICE_LAMBDA_FUNCTION_NAME"]
    workflow_execution_database_v2 = get_table_name(ResourceKeys.WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2)
    pipeline_executions_table = get_table_name(ResourceKeys.PIPELINE_EXECUTIONS_STORAGE_TABLE)
    pipeline_execution_input_metadata_table = get_table_name(
        ResourceKeys.PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE)
    pipeline_execution_input_configuration_table = get_table_name(
        ResourceKeys.PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE)
    workflow_execution_inputs_table = get_table_name(ResourceKeys.WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE)
    workflow_execution_configuration_table = get_table_name(
        ResourceKeys.WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE)
    workflow_execution_log_group_arn = os.environ.get("WORKFLOW_EXECUTION_LOG_GROUP_ARN", "")
    orchestration_bus_arn = os.environ.get("ORCHESTRATION_BUS_ARN", "")
    orchestration_event_source_prefix = os.environ.get("ORCHESTRATION_EVENT_SOURCE_PREFIX", "")
except Exception as e:
    logger.exception("Failed loading environment variables or resolving resource names")
    raise e


def _clean_validation_message(v):
    """Extract the human-readable message a request model's @root_validator raised."""
    try:
        errors = v.errors()
        if errors and errors[0].get("msg"):
            return errors[0]["msg"]
    except Exception:
        pass
    return str(v)


def _is_item_size_rejection(err):
    """True when a DynamoDB ValidationException is the 400 KB item-size limit rather than one of the
    many other conditions that share the code (a malformed key, an empty attribute name, a bad update
    expression). Only the message distinguishes them."""
    message = (err.response.get("Error", {}) or {}).get("Message", "") or ""
    return "item size" in message.lower()


#######################
# Table accessors
#######################

def _asset_table():
    return dynamodb.Table(asset_table_name)


def _workflow_table():
    return dynamodb.Table(workflow_table_name)


def _pipeline_table():
    return dynamodb.Table(pipeline_table_name)


def _templates_table():
    return dynamodb.Table(templates_table_name)


def _tag_schema_table():
    return dynamodb.Table(tag_schema_table_name)


def _buckets_table():
    return dynamodb.Table(buckets_table_name)


#######################
# Casbin helpers (Tier-2)
#######################

def _enforce(item, object_type, action):
    """Tier-2 enforce on a data entity, fail-closed on empty tokens (Rule 4)."""
    if len(claims_and_roles["tokens"]) == 0:
        return False
    obj = dict(item)
    obj["object__type"] = object_type
    return CasbinEnforcer(claims_and_roles).enforce(obj, action)


#######################
# Bucket resolution
#######################

def _default_run_bucket():
    """The VAMS default asset bucket used for all run I/O (manifests, config files, output/aux
    prefixes). Input files are still read from their own asset buckets."""
    return resolve_default_bucket(_buckets_table())


def _asset_bucket_details(bucket_id):
    """Resolve an asset's own bucket {bucketName, baseAssetsPrefix} from the buckets table.

    Memoized per request (bucket rows are immutable for the launch): a single execute resolves the
    same bucketId once per verify/manifest/persist pass and once per input file, so without the cache
    a many-file single-asset launch re-queries the identical row thousands of times."""
    if bucket_id in _bucket_details_cache:
        return _bucket_details_cache[bucket_id]
    response = _buckets_table().query(
        KeyConditionExpression=Key("bucketId").eq(bucket_id), Limit=1)
    bucket = (response.get("Items") or [{}])[0]
    bucket_name = bucket.get("bucketName")
    base_prefix = bucket.get("baseAssetsPrefix") or ""
    if not bucket_name:
        raise VAMSGeneralErrorResponse("Asset bucket details could not be resolved.")
    if base_prefix and not base_prefix.endswith("/"):
        base_prefix += "/"
    if base_prefix.startswith("/"):
        base_prefix = base_prefix[1:]
    details = {"bucketName": bucket_name, "baseAssetsPrefix": base_prefix}
    _bucket_details_cache[bucket_id] = details
    return details


#######################
# Record fetch
#######################

def _get_workflow(workflow_database_id, workflow_id):
    return _workflow_table().get_item(
        Key={"databaseId": workflow_database_id, "workflowId": workflow_id}).get("Item")


def _get_pipeline(pipeline_database_id, pipeline_id):
    return _pipeline_table().get_item(
        Key={"databaseId": pipeline_database_id, "pipelineId": pipeline_id}).get("Item")


def _get_asset(database_id, asset_id):
    response = _asset_table().query(
        KeyConditionExpression=Key("databaseId").eq(database_id) & Key("assetId").eq(asset_id))
    items = response.get("Items", [])
    return items[0] if items else None


def _get_template_row(pipeline_database_id, pipeline_id, template_id):
    composite = pr.pipeline_composite_key(pipeline_database_id, pipeline_id)
    return _templates_table().get_item(
        Key={"pipelineDatabaseId:pipelineId": composite, "templateId": template_id}).get("Item")


def _get_default_template_id(pipeline_database_id, pipeline_id):
    """Return the templateId of the pipeline's default template (isDefault=True), or "" when none.
    Used as the fallback templateId when a run supplies none (e.g. a require-template pipeline)."""
    composite = pr.pipeline_composite_key(pipeline_database_id, pipeline_id)
    query_kwargs = {"KeyConditionExpression": Key("pipelineDatabaseId:pipelineId").eq(composite)}
    while True:
        response = _templates_table().query(**query_kwargs)
        for row in response.get("Items", []):
            if row.get("isDefault"):
                return row.get("templateId", "")
        if "LastEvaluatedKey" not in response:
            break
        query_kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return ""


def _load_tag_schema_fields(pipeline_database_id, pipeline_id, template_id, default_bucket_name):
    """Parsed tag-schema fields for a template (rehydrating from S3 when offloaded), or [] when no
    schema row exists."""
    owner = pr.template_owner_key(pipeline_database_id, pipeline_id, template_id)
    response = _tag_schema_table().query(
        IndexName="TagSchemaByTemplateGSI",
        KeyConditionExpression=Key("pipelineDatabaseId:pipelineId:templateId").eq(owner))
    rows = response.get("Items", [])
    if not rows:
        return []
    row = rows[0]
    if row.get("bodyStorage") == tbs.BODY_STORAGE_S3 and row.get("fieldsS3Key"):
        text = tbs.read_body_from_s3(s3c, default_bucket_name, row["fieldsS3Key"])
        return json.loads(text) if text else []
    fields = row.get("fields") or ""
    return json.loads(fields) if fields else []


def _rehydrate_template_row(row, default_bucket_name):
    """Return the template row with configBody/webFormJson rehydrated inline (reading S3 when
    offloaded), so template resolution sees the full stored body."""
    bodies = tbs.rehydrate_template_bodies(s3c, default_bucket_name, row)
    resolved = dict(row)
    resolved["configBody"] = bodies["configBody"]
    resolved["webFormJson"] = bodies["webFormJson"]
    return resolved


#######################
# Input existence
#######################

def _input_exists_in_s3(bucket, key, version_id=""):
    """Whether an input file (specific key, optional version) or folder/prefix exists in its bucket,
    plus the S3 VersionId that was resolved. Returns (exists, resolved_version_id).

    For a specific file, head_object returns the concrete VersionId even when no version was
    requested (S3 resolves "current" to a real version id) — this is the exact version the run
    reads, which is what should be recorded (rather than the time-relative notion of "latest").
    Folder/prefix selections have no single version, so ("" is returned).
    A permission/other error re-raises so the launch fails loudly rather than skipping the guard."""
    if not key:
        return False, ""
    if key.endswith("/"):
        resp = s3c.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
        exists = resp.get("KeyCount", 0) > 0 or len(resp.get("Contents", [])) > 0
        return exists, ""
    try:
        head_kwargs = {"Bucket": bucket, "Key": key}
        if version_id:
            head_kwargs["VersionId"] = version_id
        resp = s3c.head_object(**head_kwargs)
        return True, resp.get("VersionId", "") or version_id
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        # HeadObject against a delete-marker version answers 405 MethodNotAllowed; the version is
        # not readable, so it counts as a missing input rather than an unexpected failure.
        if code in ("404", "NoSuchKey", "NotFound", "405", "MethodNotAllowed"):
            return False, ""
        # A versionId that is not a well-formed S3 version answers 400 Bad Request rather than 404
        # (S3 rejects the argument before it looks anything up). That is caller-supplied input, so it
        # belongs with the other missing/unusable inputs and produces a 400 naming the file — not a
        # 500. Only treated this way when a version was actually requested; a 400 on an unversioned
        # head is a genuine fault and still raises.
        if version_id and code in ("400", "BadRequest", "InvalidArgument", "InvalidVersionId"):
            logger.warning(
                f"Rejected input version for key ending '{key[-40:]}': S3 reports the requested "
                f"versionId is not valid ({code}).")
            return False, ""
        raise


#######################
# Metadata service
#######################

def _metadata_service_lambda(payload):
    return lambda_client.invoke(
        FunctionName=metadata_service_function,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"))


def _metadata_service_payload(path, path_parameters, event):
    """A metadata-service GET invoke payload carrying the identity THIS execute request arrived with.

    An API request forwards its authorizer context. A lambda cross-call (trigger dispatch, re-run)
    has no authorizer at all, so its `lambdaCrossCall` identity is forwarded instead — forwarding an
    absent authorizer as `authorizer: None` makes the metadata service's claims extraction fail and
    answer no metadata for a run that is otherwise fully authorized.

    Every metadata read of the envelope goes through here, so the identity a run reads with is decided
    in one place rather than per endpoint."""
    payload = {
        "requestContext": {"http": {"path": path, "method": "GET"}},
        "pathParameters": path_parameters or {},
        "queryStringParameters": {},
    }
    if event.get("lambdaCrossCall"):
        payload["lambdaCrossCall"] = event["lambdaCrossCall"]
    else:
        payload["requestContext"]["authorizer"] = (
            event.get("requestContext") or {}).get("authorizer")
    return payload


def _fetch_metadata(database_id, asset_id, query_params, event):
    """Invoke the metadata service GET endpoint; return the parsed 'metadata' list (best-effort).

    An answer that is neither a 200-with-body nor a payload at all is reported: the metadata service
    answers a failure with an error payload carrying no statusCode, which is otherwise indistinguishable
    from an asset that genuinely carries no metadata."""
    try:
        payload = _metadata_service_payload(
            f"/database/{database_id}/assets/{asset_id}/metadata",
            {"databaseId": database_id, "assetId": asset_id}, event)
        payload["queryStringParameters"] = query_params or {}
        response = _metadata_service_lambda(payload)
        stream = response.get("Payload", "")
        if not stream:
            logger.warning(
                f"Metadata service returned no payload for asset {database_id}:{asset_id}; the "
                f"execution captures no metadata for it.")
            return []
        json_response = json.loads(stream.read().decode("utf-8"))
        if json_response.get("statusCode") == 200 and "body" in json_response:
            return json.loads(json_response["body"]).get("metadata", [])
        logger.warning(
            f"Metadata service answered status {json_response.get('statusCode')} for asset "
            f"{database_id}:{asset_id}; the execution captures no metadata for it.")
    except Exception as e:
        logger.exception(f"Failed fetching metadata for {database_id}:{asset_id}: {e}")
    return []


def _fetch_file_metadata(database_id, asset_id, file_path, meta_type, event):
    """Invoke the metadata-service file endpoint (type 'metadata' or 'attribute'). A failed read is
    reported for the same reason _fetch_metadata reports one."""
    try:
        payload = _metadata_service_payload(
            f"/database/{database_id}/assets/{asset_id}/metadata/file",
            {"databaseId": database_id, "assetId": asset_id}, event)
        payload["queryStringParameters"] = {"filePath": file_path, "type": meta_type}
        response = _metadata_service_lambda(payload)
        stream = response.get("Payload", "")
        if not stream:
            logger.warning(
                f"Metadata service returned no payload for file {database_id}:{asset_id}{file_path} "
                f"({meta_type}); the execution captures none of it.")
            return []
        json_response = json.loads(stream.read().decode("utf-8"))
        if json_response.get("statusCode") == 200 and "body" in json_response:
            return json.loads(json_response["body"]).get("metadata", [])
        logger.warning(
            f"Metadata service answered status {json_response.get('statusCode')} for file "
            f"{database_id}:{asset_id}{file_path} ({meta_type}); the execution captures none of it.")
    except Exception as e:
        logger.exception(f"Failed fetching file metadata for {database_id}:{asset_id}{file_path}: {e}")
    return []


def _fetch_database_metadata(database_id, event):
    """Invoke the metadata service's database endpoint; return the parsed 'metadata' list, or None when
    the read did not succeed.

    Database metadata is a read-only execution input, so a failure yields no metadata rather than
    failing the launch. None distinguishes a read that FAILED — no payload, a non-200 answer, or an
    exception — from a successful read of a database that genuinely carries nothing, which returns an
    empty list. The two are otherwise identical in the envelope and in the persisted rows, so a run over
    many databases could lose one silently; the caller reports a failed read as an execute warning."""
    try:
        payload = _metadata_service_payload(
            f"/database/{database_id}/metadata", {"databaseId": database_id}, event)
        response = _metadata_service_lambda(payload)
        stream = response.get("Payload", "")
        if not stream:
            logger.warning(f"Metadata service returned no payload for database {database_id}")
            return None
        json_response = json.loads(stream.read().decode("utf-8"))
        if json_response.get("statusCode") == 200 and "body" in json_response:
            return json.loads(json_response["body"]).get("metadata", [])
        logger.warning(
            f"Metadata service answered status {json_response.get('statusCode')} for database "
            f"{database_id}; the execution captures no database metadata.")
    except Exception as e:
        logger.exception(f"Failed fetching metadata for database {database_id}: {e}")
    return None


def _metadata_entry_bytes(key, value):
    """The measured size of one metadata entry against the per-entity byte budget: its key and value in
    UTF-8 plus METADATA_ENTRY_FRAMING_BYTES for the serialization/DynamoDB overhead of a single map
    element."""
    return (len(str(key).encode("utf-8")) + len(str(value).encode("utf-8"))
            + METADATA_ENTRY_FRAMING_BYTES)


def _simplify_metadata_array(metadata_array, entity_label="", truncated=None):
    """Convert a verbose metadata array to a simple {key: value} dict, bounded BOTH at
    MAX_METADATA_ENTRIES_PER_ENTITY entries and at MAX_METADATA_BYTES_PER_ENTITY bytes.

    Every metadata read of the envelope — per database, per asset, per file's metadata, per file's
    attributes — passes through here, so the bounds apply per entity row rather than to the run as a
    whole. Both are needed: a metadata value has no length limit, so an entry count alone cannot keep a
    row inside the DynamoDB item limit, and one entity carrying a few very long values would fail its
    input-metadata write after the state machine had already started.

    Keys are considered in sorted order and each is kept if it fits the remaining byte budget, so the
    same entity yields the same subset on every run (and on a re-run) instead of whatever order the
    metadata service happened to answer in. A single entry too large for the whole budget is skipped
    rather than ending the walk, so one oversized value costs only itself.

    entity_label names the row in the warning, and the label is appended to `truncated` (when a list is
    supplied) so the caller can surface the cap to the caller of the execute request rather than leaving
    it in the logs alone."""
    simplified = {}
    for item in metadata_array or []:
        key = item.get("metadataKey", "")
        if key:
            simplified[key] = item.get("metadataValue", "")
    total_bytes = sum(_metadata_entry_bytes(k, v) for k, v in simplified.items())
    if (len(simplified) <= MAX_METADATA_ENTRIES_PER_ENTITY
            and total_bytes <= MAX_METADATA_BYTES_PER_ENTITY):
        return simplified

    retained = {}
    used_bytes = 0
    for key in sorted(simplified):
        if len(retained) >= MAX_METADATA_ENTRIES_PER_ENTITY:
            break
        entry_bytes = _metadata_entry_bytes(key, simplified[key])
        if used_bytes + entry_bytes > MAX_METADATA_BYTES_PER_ENTITY:
            continue
        retained[key] = simplified[key]
        used_bytes += entry_bytes

    label = entity_label or "an execution input entity"
    logger.warning(
        f"Metadata for {label} carries {len(simplified)} entries in {total_bytes} bytes, over the "
        f"{MAX_METADATA_ENTRIES_PER_ENTITY}-entry / {MAX_METADATA_BYTES_PER_ENTITY}-byte bound for one "
        f"entity; capturing {len(retained)} entries in {used_bytes} bytes by key order and dropping "
        f"{len(simplified) - len(retained)}.")
    if truncated is not None:
        truncated.append(label)
    return retained


def _run_bounded(jobs):
    """Run zero-arg callables through a bounded worker pool, returning results in job order. A single
    job runs inline (no pool). Keeps the per-input fan-out inside the request window without an
    unbounded burst of child invocations."""
    if not jobs:
        return []
    if len(jobs) == 1:
        return [jobs[0]()]
    workers = min(MAX_PARALLEL_INPUT_WORKERS, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(lambda job: job(), jobs))


def _derive_metadata_source_databases(selected_inputs, metadata_source_assets,
                                      metadata_source_database_id, metadata_inputs):
    """The databases a run captures database metadata from, de-duplicated in first-seen order.

    Every entity the run names contributes its database: each input file's asset, and each asset named
    purely as a metadata source. Those are the databases the run's data actually lives in. The caller's
    metadataSourceDatabaseId joins them only on the arity-'none' path, where there are no input files
    and it is the one database the caller can name directly; it leads the order there. A run WITH input
    files derives its set from them alone. An off databaseMetadata gate captures nothing.

    The two ways a database enters the set matter to authorization, not to the set itself: a NAMED
    database that the caller may not read fails the launch, while a DERIVED one is skipped
    (_resolve_and_authorize_metadata_sources owns that split, and takes the named ids separately).

    GLOBAL is dropped from the derived set. An asset may live in the GLOBAL database (both input files
    and metadata-source assets accept it), but GLOBAL is the unscoped/all-databases keyword rather than a
    database record, so there is no entity whose metadata could be read — the named field rejects it for
    the same reason."""
    if not er.metadata_input_enabled(metadata_inputs, "databaseMetadata"):
        return []
    ordered = []
    seen = set()
    if not selected_inputs and metadata_source_database_id:
        seen.add(metadata_source_database_id)
        ordered.append(metadata_source_database_id)
    for item in list(selected_inputs) + list(metadata_source_assets or []):
        database_id = item.get("databaseId", "")
        if database_id and database_id != GLOBAL_DATABASE and database_id not in seen:
            seen.add(database_id)
            ordered.append(database_id)
    return ordered


def _build_grouped_metadata(selected_inputs, asset_records, metadata_inputs, event,
                            metadata_source_assets=None, metadata_source_databases=None,
                            notices=None):
    """Build the v2 grouped metadata envelope for the selected inputs plus any named metadata-source
    entities, honoring the workflow's metadataInputs gate (asset/file/attribute/database toggles). One
    assets[] entry per unique involved asset; per-file records for file metadata/attributes.

    metadata_source_assets are [{databaseId, assetId}] named purely as metadata sources: each carries
    the asset-level ('/') record only (a metadata source is an entity, never a file), and an asset that
    is both an input's asset and a named source appears once. metadata_source_databases are the
    databaseIds whose metadata becomes the envelope's top-level 'databases' list, one entry each
    (_derive_metadata_source_databases resolves the set).

    The metadata-service reads (one per asset, one per source database, up to two per selected file) are
    independent, so they run through a bounded worker pool; the envelope is assembled afterwards in
    selection order. Each read is bounded per entity by MAX_METADATA_ENTRIES_PER_ENTITY and
    MAX_METADATA_BYTES_PER_ENTITY, so one heavily tagged entity cannot inflate the whole payload.

    'notices' collects non-fatal messages about the capture itself — entity rows that were truncated and
    source databases whose metadata could not be read — for the caller to return as execute warnings, so
    a partial capture is visible in the response rather than only in the logs.

    This is the INTAKE half of the two-level metadataInputs contract: the WORKFLOW's gate decides what
    the run gathers into the shared envelope. The DELIVERY half — a step's own effective gate deciding
    what that one step receives — is applied separately by er.narrow_metadata_envelope. Both are needed:
    dropping this one gathers metadata no step asked for, and dropping that one hands every step the
    workflow gate. They are not redundant."""
    want_asset = er.metadata_input_enabled(metadata_inputs, "assetMetadata")
    want_file = er.metadata_input_enabled(metadata_inputs, "fileMetadata")
    want_attr = er.metadata_input_enabled(metadata_inputs, "fileAttributes")
    want_database = er.metadata_input_enabled(metadata_inputs, "databaseMetadata")
    source_databases = list(metadata_source_databases or []) if want_database else []

    # Group selected inputs by (databaseId, assetId), preserving order.
    grouped = {}
    order = []
    for item in selected_inputs:
        key = (item["databaseId"], item["assetId"])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)

    # Metadata-source assets join the same ordering with no files of their own, so a source that is
    # also an input's asset keeps that asset's file records instead of producing a second group.
    for source in metadata_source_assets or []:
        key = (source["databaseId"], source["assetId"])
        if key not in grouped:
            grouped[key] = []
            order.append(key)

    # Collect every metadata read as a job keyed by what it populates, then run them together.
    jobs = []
    job_keys = []

    def _add_job(key, fn):
        job_keys.append(key)
        jobs.append(fn)

    for database_id in source_databases:
        _add_job(("database", database_id),
                 lambda d=database_id: _fetch_database_metadata(d, event))

    for (database_id, asset_id) in order:
        if want_asset:
            _add_job(("asset", database_id, asset_id),
                     lambda d=database_id, a=asset_id: _fetch_metadata(d, a, {}, event))
        for item in grouped[(database_id, asset_id)]:
            relative_key = item["relativeFileKey"]
            if relative_key in ("", "/"):
                continue  # whole-asset selection has no per-file metadata beyond the '/' record
            if want_file:
                _add_job(("metadata", database_id, asset_id, relative_key),
                         lambda d=database_id, a=asset_id, r=relative_key: _fetch_file_metadata(
                             d, a, r, "metadata", event))
            if want_attr:
                _add_job(("attribute", database_id, asset_id, relative_key),
                         lambda d=database_id, a=asset_id, r=relative_key: _fetch_file_metadata(
                             d, a, r, "attribute", event))

    fetched = dict(zip(job_keys, _run_bounded(jobs)))

    # Entity rows whose metadata hit a per-entity bound, and source databases whose read failed. Both
    # become execute warnings so a partial capture is visible in the response.
    truncated_entities = []
    unread_databases = []

    asset_groups = []
    for (database_id, asset_id) in order:
        asset = asset_records.get((database_id, asset_id), {})
        asset_data = {
            "assetName": asset.get("assetName", ""),
            "description": asset.get("description", ""),
            "tags": asset.get("tags", []),
        }
        asset_metadata = _simplify_metadata_array(
            fetched.get(("asset", database_id, asset_id), []),
            entity_label=f"asset {database_id}:{asset_id}", truncated=truncated_entities)

        files = []
        # Asset-level record (fileKey '/').
        files.append(er.build_metadata_file_record("/", metadata=asset_metadata))
        for item in grouped[(database_id, asset_id)]:
            relative_key = item["relativeFileKey"]
            if relative_key in ("", "/"):
                continue
            file_label = f"file {database_id}:{asset_id}{relative_key}"
            file_metadata = _simplify_metadata_array(
                fetched.get(("metadata", database_id, asset_id, relative_key), []),
                entity_label=f"{file_label} metadata", truncated=truncated_entities)
            file_attributes = _simplify_metadata_array(
                fetched.get(("attribute", database_id, asset_id, relative_key), []),
                entity_label=f"{file_label} attributes", truncated=truncated_entities)
            files.append(er.build_metadata_file_record(
                relative_key, metadata=file_metadata, attributes=file_attributes if want_attr else None))
        asset_groups.append(er.build_metadata_asset_group(
            database_id, asset_id, asset_data=asset_data, files=files))

    # A database entry is emitted for every captured database even when it carries no metadata, so a
    # reader can tell "the run captured this database and it has nothing" apart from "the run captured
    # no database metadata" (which emits no list at all). A read that FAILED answers None rather than an
    # empty list, and is reported — the entry stays, so the run still records which databases it covered.
    database_groups = []
    for database_id in source_databases:
        database_metadata = fetched.get(("database", database_id))
        if database_metadata is None:
            unread_databases.append(database_id)
            database_metadata = []
        database_groups.append(er.build_metadata_database_group(
            database_id,
            metadata=_simplify_metadata_array(
                database_metadata, entity_label=f"database {database_id}",
                truncated=truncated_entities)))

    # The per-entity caps bound each row; this bounds all of them together, so a run over thousands of
    # entities cannot assemble an envelope no Lambda can hold and no details response can serve.
    dropped_entities = _apply_total_metadata_budget(asset_groups, database_groups)

    if notices is not None:
        notices.extend(_metadata_capture_warnings(
            truncated_entities, unread_databases, dropped_entities=dropped_entities))

    return er.build_grouped_metadata_envelope(asset_groups, databases=database_groups)


def _metadata_budget_rows(asset_groups, database_groups):
    """Every metadata-carrying row of an assembled envelope, ordered most-useful-first for the total
    budget, as (label, container, key) triples naming the dict whose metadata may be cleared.

    The retention order is by how much of the run a row describes:

    1. Database rows — one per captured database, the broadest context and the fewest rows.
    2. Asset-level ('/') rows — one per involved asset; the asset's own metadata, which every pipeline
       reading asset metadata expects and which no other row carries.
    3. Per-file metadata rows, then per-file attribute rows — the most numerous and the narrowest, each
       describing one file, and the rows a pathological run multiplies.

    Within each band rows keep the envelope's own selection order, which is derived from the request, so
    the retained subset is identical across runs and processes. Nothing here iterates a set or a dict's
    insertion order — the bands are explicit and each band walks the ordered lists the envelope was
    assembled from."""
    database_rows = []
    asset_rows = []
    file_metadata_rows = []
    file_attribute_rows = []
    for group in database_groups:
        database_rows.append((f"database {group.get('databaseId', '')}", group, "metadata"))
    for group in asset_groups:
        database_id = group.get("databaseId", "")
        asset_id = group.get("assetId", "")
        for record in group.get("files") or []:
            file_key = record.get("fileKey", "/")
            if file_key == "/":
                asset_rows.append((f"asset {database_id}:{asset_id}", record, "metadata"))
                continue
            label = f"file {database_id}:{asset_id}{file_key}"
            file_metadata_rows.append((f"{label} metadata", record, "metadata"))
            if "attributes" in record:
                file_attribute_rows.append((f"{label} attributes", record, "attributes"))
    return database_rows + asset_rows + file_metadata_rows + file_attribute_rows


def _asset_data_bytes(asset_groups):
    """The bytes an envelope spends on assetData — each group's assetName, description and tags.

    Counted against the total budget but never reclaimable: assetData is the asset's identity, which
    every consumer of a group reads and which no other row carries, so it holds its place and the
    metadata rows are what give way to make room for it. It is bounded structurally instead — one
    assetData per involved asset, and the involved assets are capped by MAX_INPUT_FILES_PER_EXECUTION
    and MAX_METADATA_SOURCE_ASSETS_PER_EXECUTION — so counting it here is what keeps the budget a bound
    on the whole envelope rather than on its metadata alone."""
    total = 0
    for group in asset_groups:
        asset_data = group.get("assetData") or {}
        for key, value in asset_data.items():
            if isinstance(value, (list, tuple)):
                total += len(str(key).encode("utf-8")) + METADATA_ENTRY_FRAMING_BYTES
                for element in value:
                    total += len(str(element).encode("utf-8")) + METADATA_ENTRY_FRAMING_BYTES
                continue
            total += _metadata_entry_bytes(key, value)
    return total


def _apply_total_metadata_budget(asset_groups, database_groups):
    """Bound a whole envelope at MAX_METADATA_BYTES_PER_EXECUTION, clearing the least useful metadata
    rows first. Mutates the groups in place; returns the labels of the rows that were emptied.

    The measured total is every row's metadata plus the assetData the groups carry. assetData is
    non-reclaimable (see _asset_data_bytes), so it is charged to the budget first and the metadata rows
    are fitted into what is left — which is what makes the bound cover the envelope, not just its
    metadata.

    Rows are considered in _metadata_budget_rows order and each is kept whole while the running total
    fits, so the broadest metadata survives and the per-file rows of a pathological run are what give
    way. A row that does not fit is emptied rather than partially kept: a half-populated metadata map
    reads as complete to every consumer, whereas an empty one carries the same meaning as an entity with
    no metadata, which the envelope already emits. The walk continues past a row that does not fit, so a
    single oversized row does not discard the smaller ones after it."""
    rows = _metadata_budget_rows(asset_groups, database_groups)
    asset_data_bytes = _asset_data_bytes(asset_groups)
    total_bytes = asset_data_bytes
    for _label, container, key in rows:
        for entry_key, entry_value in (container.get(key) or {}).items():
            total_bytes += _metadata_entry_bytes(entry_key, entry_value)
    if total_bytes <= MAX_METADATA_BYTES_PER_EXECUTION:
        return []

    dropped = []
    used_bytes = asset_data_bytes
    for label, container, key in rows:
        metadata = container.get(key) or {}
        if not metadata:
            continue
        row_bytes = sum(_metadata_entry_bytes(k, v) for k, v in metadata.items())
        if used_bytes + row_bytes > MAX_METADATA_BYTES_PER_EXECUTION:
            container[key] = {}
            dropped.append(label)
            continue
        used_bytes += row_bytes

    logger.warning(
        f"Execution input metadata totals {total_bytes} bytes across {len(rows)} entity rows plus "
        f"{asset_data_bytes} bytes of assetData, over the {MAX_METADATA_BYTES_PER_EXECUTION}-byte bound "
        f"for one execution; capturing {used_bytes} bytes and emptying {len(dropped)} rows, narrowest "
        f"first.")
    return dropped


def _metadata_capture_warnings(truncated_entities, unread_databases, dropped_entities=None,
                               ignored_source_databases=None, suppressed_source_databases=None):
    """Non-blocking warnings describing an incomplete metadata capture: entity rows bounded by the
    per-entity caps, rows dropped by the total cap, source databases whose metadata could not be read,
    a named source database the run derived its databases past, and one the workflow's own
    databaseMetadata gate turns off entirely.

    Each is one message naming up to MAX_METADATA_NOTICE_ENTITIES_LISTED entities with a count of the
    rest, so a run over hundreds of entities returns a bounded response rather than one warning each."""
    warnings = []
    for entities, singular, plural in (
        (truncated_entities,
         "captured only part of the metadata for {names}, which exceeded the per-entity metadata limits",
         "captured only part of the metadata for {count} execution input entities ({names}), which "
         "exceeded the per-entity metadata limits"),
        (dropped_entities or [],
         "captured no metadata for {names}, because the execution reached its total metadata limit of "
         f"{MAX_METADATA_BYTES_PER_EXECUTION} bytes",
         "captured no metadata for {count} execution input entities ({names}), because the execution "
         f"reached its total metadata limit of {MAX_METADATA_BYTES_PER_EXECUTION} bytes"),
        (unread_databases,
         "could not read the metadata of database {names}, which therefore contributes no database "
         "metadata",
         "could not read the metadata of {count} metadata-source databases ({names}), which therefore "
         "contribute no database metadata"),
        (ignored_source_databases or [],
         "did not use the metadata-source database {names} it was given: an execution with input files "
         "derives its metadata-source databases from those files' assets instead",
         "did not use the {count} metadata-source databases ({names}) it was given: an execution with "
         "input files derives its metadata-source databases from those files' assets instead"),
        (suppressed_source_databases or [],
         "did not use the metadata-source database {names} it was given: this workflow's "
         "databaseMetadata input is turned off, so no database metadata is captured",
         "did not use the {count} metadata-source databases ({names}) it was given: this workflow's "
         "databaseMetadata input is turned off, so no database metadata is captured"),
    ):
        if not entities:
            continue
        listed = entities[:MAX_METADATA_NOTICE_ENTITIES_LISTED]
        names = ", ".join(listed)
        if len(entities) > len(listed):
            names += f", and {len(entities) - len(listed)} more"
        message = singular if len(entities) == 1 else plural
        warnings.append(
            f"This execution {message.format(count=len(entities), names=names)}.")
    return warnings


#######################
# Referenced-entity resolution + authorization
#######################

def _resolve_and_authorize_pipelines(workflow, workflow_database_id):
    """Resolve every referenced pipeline record, enforce Tier-2 GET, and gate enabled + not archived.

    Returns (error_response_or_None, ordered_pipeline_records). Each record is the raw V2 pipeline
    item; the disabled/archived gate is ALSO applied by the cross-entity validator, but is enforced
    here too so a disabled pipeline fails fast with a clear message before template resolution."""
    records = []
    for ref in workflow.get("specifiedPipelines", []) or []:
        pipeline_db = ref.get("pipelineDatabaseId") or workflow_database_id
        pipeline_id = ref.get("pipelineId", "")
        record = _get_pipeline(pipeline_db, pipeline_id)
        if not record:
            logger.error(f"Referenced pipeline {pipeline_db}:{pipeline_id} not found")
            return validation_error(body={"message": "A referenced pipeline could not be found."}), None
        # Tier-2 GET on the pipeline object; empty tokens fail closed. Surface the flat
        # pipelineExecutionType ABAC field (from executionConfig) so execution-type constraints apply.
        if not _enforce(
                pr.apply_pipeline_constraint_fields(dict(record), record),
                OBJECT_TYPE_PIPELINE, "GET"):
            return authorization_error(), None
        # A DeadlineCloud pipeline can only run when the deployment has the type enabled (the createJob
        # task state + job-callback lambda exist). If the type was disabled after the pipeline was
        # created, block the launch rather than starting an execution that hangs on the task token.
        if ((record.get("executionConfig") or {}).get("executionType") == "DeadlineCloud"
                and not DEADLINE_CLOUD_EXECUTION_TYPE_ENABLED):
            logger.error(
                f"Pipeline {pipeline_db}:{pipeline_id} is DeadlineCloud but the execution type is "
                f"disabled for this deployment")
            return validation_error(body={
                "message": "A referenced pipeline uses the DeadlineCloud execution type, which is "
                           "not enabled for this deployment."}), None
        record["_jobName"] = ref.get("jobName", "")
        # A workflow ref may carry a defaultTemplateId (e.g. set by the v2.5->v2.6 migration when a
        # consolidated built-in reference needs the format-specific template). It is the fallback the
        # execution uses when the run supplies no per-pipeline templateId.
        record["_defaultTemplateId"] = ref.get("defaultTemplateId", "") or ""
        records.append(record)
    if not records:
        return validation_error(body={"message": "Workflow has no pipelines to execute."}), None
    return None, records


def _with_name(item, name_field):
    """Casbin object carrying a `name` attribute (category/name-based rules)."""
    obj = dict(item)
    obj.setdefault("name", item.get(name_field, ""))
    return obj


def _resolve_and_authorize_assets(selected_inputs, output_asset_id, output_database_id):
    """Resolve + authorize every distinct input asset (GET) and the output-target asset (POST).

    Returns (error_response_or_None, asset_records, output_asset, output_bucket_details) where
    asset_records maps (databaseId, assetId) -> asset item. Empty tokens fail closed.

    A results-only execution passes output_asset_id/output_database_id as None: there is no output
    asset to resolve or authorize, so (output_asset, output_bucket_details) come back as None."""
    asset_records = {}
    for item in selected_inputs:
        key = (item["databaseId"], item["assetId"])
        if key in asset_records:
            continue
        asset = _get_asset(item["databaseId"], item["assetId"])
        # A genuinely-missing asset is a 404 (matches the rest of VAMS, which checks existence before
        # authorization); an asset that exists but the caller cannot GET is a 403.
        if not asset:
            logger.info(f"Input asset {key[0]}:{key[1]} not found")
            return validation_error(
                status_code=404, body={"message": "An input asset was not found."}), None, None, None
        if not _enforce(_with_name(asset, "assetName"), OBJECT_TYPE_ASSET, "GET"):
            return authorization_error(), None, None, None
        asset_records[key] = asset

    # Results-only: no output asset to resolve/authorize.
    if not output_asset_id or not output_database_id:
        return None, asset_records, None, None

    # Output-target asset (write permission = POST). Resolve its own bucket for output write-back.
    output_asset = _get_asset(output_database_id, output_asset_id)
    if not output_asset:
        logger.info(f"Output asset {output_database_id}:{output_asset_id} not found")
        return validation_error(
            status_code=404, body={"message": "The output asset was not found."}), None, None, None
    if not _enforce(_with_name(output_asset, "assetName"), OBJECT_TYPE_ASSET, "POST"):
        return authorization_error(), None, None, None
    try:
        output_bucket_details = _asset_bucket_details(output_asset.get("bucketId"))
    except VAMSGeneralErrorResponse:
        return validation_error(body={"message": "The output asset bucket is invalid."}), None, None, None

    return None, asset_records, output_asset, output_bucket_details


def _resolve_and_authorize_metadata_sources(metadata_source_assets, candidate_source_databases,
                                            asset_records, named_database_ids=()):
    """Resolve + authorize the run's metadata-source entities and merge the source assets into
    asset_records (mutating it), so the envelope builder finds their assetData.

    Returns (error_response_or_None, resolved_source_assets, resolved_source_databases) where
    resolved_source_assets is the de-duplicated ordered [{databaseId, assetId}] and
    resolved_source_databases the ordered databaseIds the run captures metadata from.

    A metadata source is read, so an asset source needs the same GET an input asset needs, and each
    source database needs a database GET. Both fail closed on empty tokens (via _enforce). A database is
    authorized on its ids alone — the metadata service performing the read enforces GET again against
    the same object, so no database record is resolved here.

    named_database_ids are the candidate databases the caller named directly (the arity-'none'
    metadataSourceDatabaseId); every other candidate was derived from the run's own entities. The two
    are separated because a denial means different things in each:
      - NAMED: a denial is the caller asking for something they may not read, so it fails the launch
        with a 403 rather than quietly dropping the source they asked for.
      - DERIVED from an input file's or a metadata-source asset's database: an asset GET does not imply
        a database GET, so a caller legitimately running a workflow over assets they can read may hold
        no access to those assets' databases. Database metadata is optional, so such a database is
        SKIPPED (logged, and left out of the resolved set, so no read path later gates on metadata this
        run never captured) instead of failing an otherwise-valid execution."""
    resolved_assets = []
    seen = set()
    for source in metadata_source_assets or []:
        key = (source["databaseId"], source["assetId"])
        if key in seen:
            continue
        seen.add(key)
        resolved_assets.append({"databaseId": key[0], "assetId": key[1]})
        if key in asset_records:
            continue  # already resolved + GET-authorized as an input asset
        asset = _get_asset(key[0], key[1])
        # Same 404-then-403 split the input assets use: a missing entity is reported distinctly from
        # one the caller may not read.
        if not asset:
            logger.info(f"Metadata-source asset {key[0]}:{key[1]} not found")
            return validation_error(
                status_code=404,
                body={"message": "A metadata-source asset was not found."}), None, None
        if not _enforce(_with_name(asset, "assetName"), OBJECT_TYPE_ASSET, "GET"):
            return authorization_error(), None, None
        asset_records[key] = asset

    named = set(named_database_ids or ())
    resolved_databases = []
    for database_id in candidate_source_databases or []:
        if _enforce({"databaseId": database_id}, OBJECT_TYPE_DATABASE, "GET"):
            resolved_databases.append(database_id)
            continue
        if database_id in named:
            return authorization_error(), None, None
        logger.info(
            f"Database {database_id} was derived from an input or metadata-source asset but the caller "
            f"cannot read it; the execution captures no metadata for it.")

    return None, resolved_assets, resolved_databases


#######################
# Template resolution phase
#######################

def _resolve_pipeline_configs(pipeline_records, pipeline_exec_params, default_bucket_name):
    """Per-pipeline template resolution + tag validation (templateResolution 5-case contract).

    Returns (errors, resolved_by_pipeline) where resolved_by_pipeline maps pipelineId ->
    {templateId, renderedConfig, templateTags, customTemplateOverrideUsed, configFormat,
    tagSchemaVersion, templateSchemaVersion, templateOverrides}. `templateOverrides` is the chosen
    template's overrides map (for the cross-entity validator's effective-config merge)."""
    errors = []
    resolved = {}
    for record in pipeline_records:
        pipeline_id = record.get("pipelineId", "")
        pipeline_db = record.get("databaseId", "")
        system_config = record.get("systemConfig", {}) or {}
        params = pipeline_exec_params.get(pipeline_id) or {}

        # Template id precedence: the run's per-pipeline templateId wins; then the workflow ref's
        # defaultTemplateId (set for a consolidated built-in whose old id baked in the output
        # format); then, only for a pipeline that REQUIRES a template, the pipeline's own default
        # template (isDefault) — this lets a require-template pipeline run without the caller naming
        # a template. A pipeline that does not require a template is left template-less unless the
        # run supplies one, so its default is a UI pre-selection only (never auto-applied here).
        template_id = params.get("templateId") or record.get("_defaultTemplateId", "") or None
        if not template_id and system_config.get("requireTemplate"):
            template_id = _get_default_template_id(pipeline_db, pipeline_id) or None
        template_row = None
        tag_schema_fields = None
        template_overrides = {}
        if template_id:
            template_row = _get_template_row(pipeline_db, pipeline_id, template_id)
            if template_row is not None:
                template_row = _rehydrate_template_row(template_row, default_bucket_name)
                tag_schema_fields = _load_tag_schema_fields(
                    pipeline_db, pipeline_id, template_id, default_bucket_name)
                template_overrides = template_row.get("overrides", {}) or {}

        # When the templateId came from a fallback (workflow-ref default or the pipeline's default
        # template) rather than the run's params, surface it to the resolver so its case selection
        # treats the run as template-backed (params drives resolve_pipeline_config's case choice).
        resolve_params = params
        if template_id and not params.get("templateId"):
            resolve_params = {**params, "templateId": template_id}

        pipeline_errors, result = tres.resolve_pipeline_config(
            system_config, template_row, tag_schema_fields, resolve_params)
        if pipeline_errors:
            label = f"pipeline '{pipeline_db}:{pipeline_id}'"
            errors.extend(f"{label}: {e}" for e in pipeline_errors)
            continue

        result["templateSchemaVersion"] = str(template_row.get("schemaVersion", "")) if template_row else ""
        result["tagSchemaVersion"] = str(pr.TAG_SCHEMA_VERSION) if tag_schema_fields else ""
        result["templateOverrides"] = template_overrides
        # Carry the RAW override body (pre-render) so the config snapshot can persist it verbatim —
        # a template-less override run has no templateId to re-resolve, so re-run needs the raw text.
        if result.get("customTemplateOverrideUsed"):
            result["customTemplateOverrideRaw"] = params.get("customTemplateOverride", "") or ""
        # Key by the COMPOSITE pipeline key (pipelineDatabaseId:pipelineId), not pipelineId alone: a
        # workflow may reference two same-id pipelines across databases (e.g. GLOBAL + same-db), and
        # keying on pipelineId would let the second silently overwrite the first's resolved config.
        resolved[er.pipeline_composite_key(pipeline_db, pipeline_id)] = result
    return errors, resolved


#######################
# Cross-entity validation
#######################

def _pipeline_filtered_inputs(effective_system_config, selected_inputs):
    """The subset of the run's selected inputs a pipeline receives: none for arity 'none' (a pipeline
    that consumes no files), otherwise the inputs passing its effective inputFileFilters."""
    if (effective_system_config or {}).get("inputFileArity", "one") == "none":
        return []
    return ev.apply_input_file_filters(
        selected_inputs, (effective_system_config or {}).get("inputFileFilters"))


def _run_cross_validation(workflow, pipeline_records, resolved_configs, selected_inputs,
                          output_target):
    """Assemble the effective pipeline configs (pipeline systemConfig + chosen-template overrides)
    and run executionValidation.validate_execution.

    Returns (errors, filtered_inputs_by_composite) where filtered_inputs_by_composite maps
    pipelineDatabaseId:pipelineId -> the inputs that pipeline accepts. The validator's own filtered
    map is keyed by pipelineId alone, which two same-id pipelines from different databases share, so
    the composite-keyed map is built here from the same shared filter helper."""
    pipeline_effective_configs = []
    filtered_by_composite = {}
    for record in pipeline_records:
        pipeline_id = record.get("pipelineId", "")
        composite = er.pipeline_composite_key(record.get("databaseId", ""), pipeline_id)
        overrides = (resolved_configs.get(composite) or {}).get("templateOverrides", {})
        effective_system = ev.resolve_effective_pipeline_config(
            record.get("systemConfig", {}) or {}, overrides)
        pipeline_effective_configs.append({
            "pipelineId": pipeline_id,
            "pipelineDatabaseId": record.get("databaseId", ""),
            "enabled": record.get("enabled", True),
            "archived": record.get("archived", False),
            "systemConfig": effective_system,
        })
        filtered_by_composite[composite] = _pipeline_filtered_inputs(
            effective_system, selected_inputs)
    errors, _filtered_by_pipeline_id = ev.validate_execution(
        workflow.get("systemConfig", {}) or {}, pipeline_effective_configs, selected_inputs,
        output_target)
    return errors, filtered_by_composite


def _metadata_source_span_errors(workflow_asset_scope, metadata_source_assets):
    """Asset-span errors for the metadata-source selection under the workflow's assetScope. Returns []
    when the span is allowed.

    The span rule is ev._scope_errors — the same evaluation the input FILES pass through at the workflow
    level, with the same defaults for an omitted key — so both spans read ONE interpretation of
    assetScope rather than two that can drift apart. Only the span keys (singleAssetOnly,
    crossAssetAllowed) can apply: a metadata source is an entity with no file key, so the whole-asset and
    folder keys have nothing to describe and are left out of the scope handed over, which `declared_only`
    then skips. Both span keys are always jointly evaluated, so a scope declaring singleAssetOnly bounds
    the sources whether or not it also allows cross-asset inputs — the contradictory
    {crossAssetAllowed: true, singleAssetOnly: true} pair (unreachable in the web asset-span control,
    arrivable by API or vamsSchema registration) resolves to the stricter key and admits a single source
    asset."""
    scope = ev.normalize_asset_scope(workflow_asset_scope)
    span_scope = {
        "singleAssetOnly": scope.get("singleAssetOnly", False),
        "crossAssetAllowed": scope.get("crossAssetAllowed", False),
    }
    entries = [{"assetId": s.get("assetId", "")} for s in metadata_source_assets or []]
    messages = ev._scope_errors(span_scope, entries, "This workflow", declared_only=True)
    return [m.replace("inputs span multiple assets",
                      "the named metadata-source assets span several")
            + " Name at most one metadata-source asset."
            for m in messages]


def _missing_metadata_source_warnings(pipeline_records, resolved_configs, workflow_metadata_inputs,
                                      metadata_source_assets, metadata_source_databases):
    """Non-blocking warnings for an arity-'none' pipeline that declares a metadata type the run named
    no source for. Returns [] or one message per unsourced type.

    A metadata source is always optional, so this never blocks: a pipeline that genuinely requires the
    metadata performs its own check and fails itself. The warning turns the silent case — a pipeline
    asking for metadata that the run gave it no entity to read from — into something the caller sees at
    launch. Only arity 'none' is covered: at any other arity the input files supply the assets, so
    asset metadata is collected without a named source."""
    warnings = []
    workflow_gate = workflow_metadata_inputs or {}
    for record in pipeline_records:
        composite = er.pipeline_composite_key(
            record.get("databaseId", ""), record.get("pipelineId", ""))
        overrides = (resolved_configs.get(composite) or {}).get("templateOverrides", {})
        effective = ev.resolve_effective_pipeline_config(
            record.get("systemConfig", {}) or {}, overrides)
        if (effective.get("inputFileArity") or "one") != "none":
            continue
        metadata = effective.get("metadataInputs") or {}
        # Both gates must be on for the type to be collected at all, so a type the workflow suppresses
        # is not reported as missing a source (validate_workflow_save already warns about that case).
        if (er.metadata_input_enabled(metadata, "assetMetadata")
                and er.metadata_input_enabled(workflow_gate, "assetMetadata")
                and not metadata_source_assets):
            warnings.append(
                f"{composite} uses asset metadata but expects no input files and the execution named "
                "no metadata-source asset, so it receives no asset metadata.")
        if (er.metadata_input_enabled(metadata, "databaseMetadata")
                and er.metadata_input_enabled(workflow_gate, "databaseMetadata")
                and not metadata_source_databases):
            warnings.append(
                f"{composite} uses database metadata but the execution captured no metadata-source "
                "database, so it receives no database metadata.")
    return warnings


#######################
# Input key resolution + existence
#######################

def _asset_root_key(asset):
    """The asset's base location key within its bucket (asset-bucket relative, no scheme)."""
    location = asset.get("assetLocation") or {}
    return location.get("Key", "") if isinstance(location, dict) else ""


def _resolve_full_key(asset_root_key, relative_file_key):
    """Full S3 key for an input file: the asset root key joined with the asset-relative key,
    avoiding duplication when the relative key already starts with the root."""
    root = asset_root_key or ""
    if root and not root.endswith("/"):
        root += "/"
    rel = (relative_file_key or "").lstrip("/")
    if rel and rel.startswith(root):
        return rel
    return root + rel


def _verify_inputs_exist(selected_inputs, asset_records):
    """Verify every selected input (specific file/version or whole-asset/folder) exists in its own
    asset bucket. Returns the list of human-readable missing-input labels (empty when all exist).

    Side effect: stamps each single-file item with `resolvedVersionId` — the concrete S3 VersionId
    the run reads (from head_object), so the execution record captures the exact version used rather
    than the time-relative "latest". Folder/whole-asset selections get no resolved version.

    The per-input S3 checks are independent, so they run through a bounded worker pool; results are
    applied afterwards in selection order."""
    missing = []
    checks = []
    jobs = []
    for item in selected_inputs:
        asset = asset_records[(item["databaseId"], item["assetId"])]
        try:
            bucket = _asset_bucket_details(asset.get("bucketId"))["bucketName"]
        except VAMSGeneralErrorResponse:
            missing.append(f"{item['assetId']}{item['relativeFileKey']}")
            continue
        root = _asset_root_key(asset)
        relative = item["relativeFileKey"]
        if relative in ("", "/"):
            key = root.rstrip("/") + "/"
        else:
            key = _resolve_full_key(root, relative)
        checks.append(item)
        jobs.append(lambda b=bucket, k=key, v=item.get("versionId", ""): _input_exists_in_s3(b, k, v))

    for item, (exists, resolved_version_id) in zip(checks, _run_bounded(jobs)):
        if not exists:
            missing.append(f"{item['assetId']}{item['relativeFileKey']}")
            continue
        item["resolvedVersionId"] = resolved_version_id
    return missing


#######################
# Concurrency guard
#######################

def _candidate_execution_ids(inputs_table, partition, file_keys, restriction, seen):
    """Yield distinct, not-yet-seen workflowExecutionIds for a (databaseId:assetId) partition,
    newest-first, filtered to the exact input file keys when restriction is perInputFile. Paginates
    to exhaustion; the per-partition inspection bound is applied by the caller."""
    query_kwargs = {
        "IndexName": "WorkflowExecInputsByAssetGSI",
        "KeyConditionExpression": Key("databaseId:assetId").eq(partition),
        "ScanIndexForward": False,
    }
    while True:
        resp = inputs_table.query(**query_kwargs)
        for input_item in resp.get("Items", []):
            if restriction == "perInputFile" and input_item.get("inputAssetFileKey") not in file_keys:
                continue
            execution_id = input_item.get("workflowExecutionId", "")
            if not execution_id or execution_id in seen:
                continue
            seen.add(execution_id)
            yield execution_id
        if "LastEvaluatedKey" not in resp:
            return
        query_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _running_execution_exists(workflow_database_id, workflow_id, selected_inputs, asset_records,
                              restriction):
    """True when a still-running execution of this workflow conflicts with the concurrency
    restriction:
      - none: never conflicts.
      - perAsset: a running execution touching any of the selected inputs' assets.
      - perInputFile: a running execution on any of the exact selected input file keys.
    The number of distinct executions confirmed via Step Functions is bounded by
    MAX_CONCURRENCY_CANDIDATES_INSPECTED (warns rather than silently truncating)."""
    if restriction not in ("perAsset", "perInputFile"):
        return False

    inputs_table = dynamodb.Table(workflow_execution_inputs_table)
    main_table = dynamodb.Table(workflow_execution_database_v2)
    composite = er.workflow_composite_key(workflow_database_id, workflow_id)

    asset_partitions = {f"{i['databaseId']}:{i['assetId']}" for i in selected_inputs}
    # inputAssetFileKey is stored as the normalized FULL asset key (asset root + relative), so build
    # the comparison set the same way (per its own asset's root) rather than from the relative key.
    file_keys = set()
    for i in selected_inputs:
        asset = asset_records.get((i["databaseId"], i["assetId"]), {})
        root = _asset_root_key(asset)
        relative = i["relativeFileKey"]
        full_key = root.rstrip("/") + "/" if relative in ("", "/") else _resolve_full_key(root, relative)
        file_keys.add(er.normalize_file_key(full_key))

    seen = set()
    inspected = 0
    for partition in asset_partitions:
        for execution_id in _candidate_execution_ids(
                inputs_table, partition, file_keys, restriction, seen):
            if inspected >= MAX_CONCURRENCY_CANDIDATES_INSPECTED:
                logger.warning(
                    f"Concurrency check bounded at {MAX_CONCURRENCY_CANDIDATES_INSPECTED} distinct "
                    "executions; older executions were not inspected.")
                return False
            inspected += 1
            if _execution_running(main_table, execution_id, composite):
                return True
    return False


#######################
# Manifest + input-file writing
#######################

def _build_input_manifest_entries(selected_inputs, asset_records):
    """Build pipeline 1's manifest input-file entries from the selected inputs. Each entry carries
    its own asset bucket + full key + version + asset identity + per-file aux preview prefix, so a
    multi-asset selection resolves each file from its own bucket."""
    entries = []
    for item in selected_inputs:
        database_id = item["databaseId"]
        asset_id = item["assetId"]
        asset = asset_records[(database_id, asset_id)]
        bucket = _asset_bucket_details(asset.get("bucketId"))["bucketName"]
        root = _asset_root_key(asset)
        relative = item["relativeFileKey"]
        full_key = root.rstrip("/") + "/" if relative in ("", "/") else _resolve_full_key(root, relative)
        aux_preview_prefix = er.aux_preview_file_prefix(database_id, full_key)
        # The concrete S3 version the run reads (stamped by _verify_inputs_exist), so the manifest
        # and the persisted input row name the same version.
        version_id = item.get("resolvedVersionId") or item.get("versionId", "")
        entries.append(er.build_manifest_entry(
            relative_path=relative, bucket=bucket, key=full_key,
            version_id=version_id, database_id=database_id, asset_id=asset_id,
            asset_root_s3_key=root, aux_preview_prefix=aux_preview_prefix))
    return entries


def _write_execution_input_files(execution_id, run_bucket, pipelines_count, metadata_envelope,
                                 first_manifest, pipeline_config_bodies,
                                 step_metadata_gates=None):
    """Write the execution's input-definition files to the DEFAULT run bucket (per-execution input
    folder keyed on execution id): the shared metadata file, one config.json per pipeline, and
    pipeline 1's manifest. Returns {metadataFileS3Key, configKeys[], firstManifestS3Key,
    narrowedMetadataKeys{}}.

    'step_metadata_gates' maps a 1-based pipeline index to that step's effective metadataInputs. A
    step whose gate narrows the envelope gets its OWN metadata.json written alongside its config, and
    only those steps — a step wanting everything the workflow gathered keeps reading the shared file,
    so the common case stays one object."""
    locations = {"metadataFileS3Key": "", "configKeys": [], "firstManifestS3Key": "",
                 "narrowedMetadataKeys": {}}
    if pipelines_count == 0:
        return locations

    metadata_key = er.execution_input_metadata_key(execution_id)
    s3c.put_object(Bucket=run_bucket, Key=metadata_key,
                   Body=json.dumps(metadata_envelope).encode("utf-8"), ContentType="application/json")
    locations["metadataFileS3Key"] = metadata_key

    for idx in range(pipelines_count):
        cfg_key = er.pipeline_input_config_key(execution_id, idx + 1)
        cfg_body = pipeline_config_bodies[idx] if idx < len(pipeline_config_bodies) else ""
        s3c.put_object(Bucket=run_bucket, Key=cfg_key,
                       Body=(cfg_body or "").encode("utf-8"), ContentType="application/json")
        locations["configKeys"].append(cfg_key)

        # Per-step DELIVERY narrowing — the second half of the two-level metadataInputs contract.
        # The INTAKE half is the workflow gate applied in _build_grouped_metadata, which decides what
        # the shared envelope carries at all; this decides what THIS step receives out of it. The two
        # apply the same four keys at different scopes and are NOT redundant: drop this one and every
        # step silently widens back to the workflow gate (the bug this exists to fix), drop that one
        # and the run gathers metadata no step asked for.
        #
        # Written only where the step's gate actually subtracts something, so an unnarrowed step keeps
        # pointing at the shared envelope and the common case stays a single object.
        gate = (step_metadata_gates or {}).get(idx + 1)
        if gate is not None:
            narrowed = er.narrow_metadata_envelope(metadata_envelope, gate)
            if narrowed is not metadata_envelope:
                step_key = er.pipeline_input_metadata_key(execution_id, idx + 1)
                s3c.put_object(Bucket=run_bucket, Key=step_key,
                               Body=json.dumps(narrowed).encode("utf-8"),
                               ContentType="application/json")
                locations["narrowedMetadataKeys"][idx + 1] = step_key

    manifest_key = er.pipeline_input_manifest_key(execution_id, 1)
    s3c.put_object(Bucket=run_bucket, Key=manifest_key,
                   Body=json.dumps(first_manifest).encode("utf-8"), ContentType="application/json")
    locations["firstManifestS3Key"] = manifest_key
    return locations


def _pipeline_exec_type(record):
    return (record.get("executionConfig", {}) or {}).get("executionType", "Lambda")


def _pipeline_wait_for_callback(record):
    return (record.get("executionConfig", {}) or {}).get("waitForCallback", "Disabled")


def _pipeline_resource_arn(record):
    """The pipeline's execution resource id (Lambda fn / SQS url / EventBridge bus), for the
    pipeline-execution row's pipelineResourceArn (recording only)."""
    ec = record.get("executionConfig", {}) or {}
    exec_type = ec.get("executionType", "Lambda")
    if exec_type == "SQS":
        return (ec.get("sqs") or {}).get("queueUrl", "")
    if exec_type == "EventBridge":
        return (ec.get("eventBridge") or {}).get("busArn", "")
    if exec_type == "DeadlineCloud":
        return ""
    return (ec.get("lambda") or {}).get("resourceId", "")


def _resolve_requested_output_extension(request_model, workflow):
    """The execution's output base path extension: the request's value, or the workflow's stored
    default when the request supplies none.

    A request that omits the field (or sends null) opts in to the workflow's default; an explicit ""
    or "/" is a deliberate choice of the asset root and is NOT overridden by the default. Both values
    are stored unresolved, so any {{tag}} placeholders survive to _render_output_path_extension."""
    requested = getattr(request_model, "outputFileBaseExecutionPathExtension", None)
    if requested is None:
        requested = (workflow.get("systemConfig", {}) or {}).get(
            "defaultOutputFileBaseExecutionPathExtension") or None
    return ope.normalize_output_path_extension(requested)


def _unescape_rendered_path(rendered):
    """Reverse the JSON string escaping render_config applies to scalar tag values, so a rendered
    path carries the real characters (a non-ASCII file stem, a quote) rather than their escapes. The
    text is a bare path, not a value sitting inside a template's own JSON quotes. Returns the text
    unchanged when it does not decode as a JSON string body."""
    try:
        return json.loads(f'"{rendered}"')
    except (ValueError, TypeError):
        return rendered


def _step_tag_metadata_payload(step_envelope, subject_input):
    """The legacy {"VAMS": {...}} view a STEP's template tags render, projected for its primary input.

    `step_envelope` must be that step's DELIVERY envelope (already narrowed by its effective
    metadataInputs), not the shared per-execution one. A step's gate has to govern its tags as well as
    its metadata file: {{inputMetadataObject}} renders the whole payload, so rendering the shared
    envelope here would let any template recover an excluded type just by choosing that tag over the
    per-scope one, making the gate advisory."""
    subject = subject_input or {}
    return er.to_legacy_vams_view(
        step_envelope or {}, subject.get("databaseId", ""), subject.get("assetId", ""),
        subject.get("relativeFileKey", "/"))


def _resolve_step_delivery(metadata_envelope, gate, execution_id, run_bucket, pipeline_index,
                           shared_location):
    """One step's metadata DELIVERY: (envelope, s3Location, tagPayloadFn).

    All three DELIVERY channels resolve through here so the step's gate cannot be applied to one and
    missed on another — the envelope written for the step, the location its manifest points at, and the
    payload its template tags render all come from this single decision. A gate that subtracts nothing
    yields the shared envelope and location unchanged, so the common case stays one S3 object.

    REPORTING is a fourth consumer and does NOT come through here: _persist_execution_records applies
    the same gate itself when it writes the per-step input-metadata rows, because those rows are also
    narrowed by ENTITY (this step's own input files) which delivery is not. Both call
    er.narrow_metadata_envelope with the step's gate, so they agree — a change to one has to be made
    in the other, and the row writer's own comment says so.

    `tagPayloadFn` takes the step's primary input (the projection subject) and is passed to
    templateRender as its lazy metadata_loader, so no metadata is projected unless a tag needs it."""
    narrowed = er.narrow_metadata_envelope(metadata_envelope, gate)
    location = shared_location
    if narrowed is not metadata_envelope:
        location = f"s3://{run_bucket}/{er.pipeline_input_metadata_key(execution_id, pipeline_index)}"

    def tag_payload(subject_input):
        return _step_tag_metadata_payload(narrowed, subject_input)

    return narrowed, location, tag_payload


def _render_output_path_extension(extension, manifest, execution_context, metadata_loader=None):
    """Substitute the output base path extension's {{dynamicTag}} placeholders against the launch's
    manifest + execution context and re-normalize the result.

    The extension becomes part of every output object key, so the rendered value is held to the same
    shape rules the request model applies to the raw value: no '..' path segment, no backslashes, and
    the same length cap. An undefined tag or a rendered value that breaks those rules is a caller
    error. The length re-check matters because rendering can grow the value without bound — a JSON-kind
    tag such as {{assetFileKeyArray}} on a large selection renders to kilobytes, which would otherwise
    pass here and fail much later, once per object, on S3's 1024-byte key limit."""
    if not tr.uses_template_tags(extension):
        return extension
    try:
        rendered = tr.render_config(
            extension, manifest, execution_context, metadata_loader=metadata_loader)
    except tr.MissingTemplateTagError as e:
        logger.error(f"Output base path extension uses undefined template tag(s): {e.unknown_tags}")
        raise VAMSGeneralErrorResponse(
            "The output base path extension uses one or more undefined template tags.")
    unescaped = _unescape_rendered_path(rendered)
    normalized = ope.normalize_output_path_extension(unescaped)
    segments = [s for s in normalized.split("/") if s]
    # Checked on the RAW rendered text, not the normalized one: normalization collapses duplicate
    # separators, so a URI-valued tag's "s3://bucket/key" silently becomes "s3:/bucket/key" and no
    # longer looks wrong. A scheme in an output path prefix is always an authoring mistake.
    looks_like_uri = "://" in unescaped
    invalid = (".." in segments or "\\" in normalized
               or any(ord(c) < 0x20 for c in normalized)
               # A JSON-kind tag renders braces/brackets/quotes, which would become literal
               # characters in every output key.
               or any(c in normalized for c in '{}[]"')
               or looks_like_uri)
    if invalid:
        logger.error(f"Rendered output base path extension is not a valid path: {normalized[:200]}")
        raise VAMSGeneralErrorResponse(
            "The output base path extension resolved to an invalid path.")
    if len(normalized) > MAX_OUTPUT_PATH_EXTENSION_LENGTH:
        logger.error(
            f"Rendered output base path extension is {len(normalized)} chars, over the "
            f"{MAX_OUTPUT_PATH_EXTENSION_LENGTH} limit: {normalized[:200]}")
        raise VAMSGeneralErrorResponse(
            "The output base path extension resolved to a value that is too long.")
    return normalized


def _stored_job_names_error(workflow, pipeline_records):
    """Whether the workflow's stored jobNames can reconstruct the output prefixes its deployed ASL
    baked in. Returns a log-only reason string when they cannot, else "".

    The generator prefixes each job name with a fresh uuid fragment, so the names exist only on the
    workflow record its deploy wrote. A record with missing or short jobNames (a workflow whose ASL
    was deployed by an earlier schema, or one migrated without regenerating the state machine) cannot
    be mapped to the ASL's folders, and every pipeline output would land where the end-state lambda
    never lists."""
    stored = workflow.get("jobNames") or []
    if not stored:
        return "workflow record has no jobNames"
    if len(stored) < len(pipeline_records):
        return (f"workflow record has {len(stored)} jobNames for "
                f"{len(pipeline_records)} referenced pipelines")
    return ""


#######################
# Launch + persist
#######################

def _launch_workflow(workflow, pipeline_records, resolved_configs, selected_inputs, asset_records,
                     output_asset, output_database_id, output_asset_id, run_bucket, metadata_envelope,
                     trigger_type_stored, execution_group_id, executing_user, executing_request_context,
                     output_location_type=OUTPUT_LOCATION_TYPE_ASSET, output_extension="/",
                     filtered_inputs_by_composite=None, metadata_source_assets=None,
                     metadata_source_database_id="", metadata_source_databases=None):
    """Build the first-pipeline manifest, start the Step Functions execution, and persist all V2
    records. Run I/O (manifest, config files, output/aux prefixes) lives in run_bucket; input files
    are read from their own asset buckets (carried per manifest entry). Returns the executionId.

    filtered_inputs_by_composite maps pipelineDatabaseId:pipelineId -> the inputs that pipeline
    accepts (its effective inputFileFilters applied, empty for arity 'none'). Pipeline 1's manifest
    carries its own filtered set, not the workflow's full selection; pipelines 2+ receive the prior
    step's outputs from the interim tracking lambda. The same map narrows each pipeline's persisted
    input-metadata rows to the entities that pipeline reads.

    metadata_source_assets/metadata_source_databases are the resolved metadata-source entities whose
    metadata the envelope already carries, and metadata_source_database_id the database the caller
    NAMED; they are recorded on the configuration row so a re-run names the same sources and the read
    paths gate on the same databases. They are NOT input files, so they take no part in the manifest or
    the input rows."""
    workflow_id = workflow["workflowId"]
    workflow_database_id = workflow["databaseId"]
    workflow_arn = workflow.get("workflow_arn", "")

    execution_id = er.new_guid()
    pipeline_execution_ids = [er.new_guid() for _ in pipeline_records]
    end_state_pipeline_execution_id = pipeline_execution_ids[-1] if pipeline_execution_ids else ""

    first_pipeline = pipeline_records[0]
    # Output-path pipeline-name segment must match what the ASL baked in. The ASL derives it from
    # the workflow ref's jobName (workflowAsl.to_asl_pipeline_dict: `job_name or pipelineId`), NOT the
    # pipelineId — so a first pipeline whose ref jobName differs from its pipelineId would otherwise
    # orphan its outputs (the end-state lambda lists the jobName-based folder).
    first_pipeline_name = first_pipeline.get("_jobName", "") or first_pipeline.get("pipelineId", "")
    # The uuid-prefixed job-name segment exists only in the ASL the deploy produced, so it is read
    # from the workflow record's jobNames (gated by _stored_job_names_error before launch).
    first_job_name = workflow["jobNames"][0]

    # Pipeline 1's manifest carries the inputs PIPELINE 1 accepts (its own inputFileFilters applied,
    # empty for arity 'none'), not the workflow's full selection — the pipeline is handed exactly the
    # files the cross-entity validator sized it against.
    first_composite = er.pipeline_composite_key(
        first_pipeline.get("databaseId", ""), first_pipeline.get("pipelineId", ""))
    first_pipeline_inputs = (filtered_inputs_by_composite or {}).get(first_composite, selected_inputs)

    # Pipeline 1's manifest: input entries (each from its own asset bucket) + run-bucket output/aux.
    input_entries = _build_input_manifest_entries(first_pipeline_inputs, asset_records)
    out_prefixes = er.pipeline_output_prefixes(first_pipeline_name, first_job_name, execution_id)
    outputs = er.build_manifest_outputs(bucket=run_bucket, **out_prefixes)
    first_aux_temp_prefix = er.aux_pipeline_prefix(first_pipeline_name, execution_id)
    metadata_location = f"s3://{run_bucket}/{er.execution_input_metadata_key(execution_id)}"
    first_event_prefix = er.orchestration_event_prefix(
        orchestration_event_source_prefix, execution_id, pipeline_execution_ids[0]) \
        if (orchestration_event_source_prefix and pipeline_execution_ids) else ""
    first_aux_preview_suffix = (first_pipeline.get("systemConfig", {}) or {}).get(
        "auxPreviewPipelineSuffix", "")

    # Each step's DELIVERY gate: its pipeline systemConfig with the chosen template's overrides
    # applied, keyed 1-based to match the pipeline input folders. A step whose gate narrows the shared
    # envelope reads its own metadata file instead (see _write_execution_input_files); the gates for
    # steps 2+ also travel in the ASL so the interim lambda can point their manifests at theirs.
    # The same effective config supplies each step's input filters and arity, one entry per pipeline in
    # workflow order: steps 2+ have their manifests assembled by the interim lambda, which narrows the
    # run's selection to the step's own share the way step 1's manifest is narrowed here.
    step_metadata_gates = {}
    step_input_filters = []
    step_input_arity = []
    for idx, record in enumerate(pipeline_records):
        composite = er.pipeline_composite_key(
            record.get("databaseId", ""), record.get("pipelineId", ""))
        overrides = (resolved_configs.get(composite) or {}).get("templateOverrides", {})
        effective = ev.resolve_effective_pipeline_config(
            record.get("systemConfig", {}) or {}, overrides)
        step_metadata_gates[idx + 1] = effective.get("metadataInputs") or {}
        step_input_filters.append(effective.get("inputFileFilters") or {})
        step_input_arity.append(ev._arity(effective))

    # Pipeline 1's delivery: its own narrowed metadata file when its gate subtracts anything, else the
    # shared envelope. The manifest is what the pipeline honors (manifestHelper.resolve_inputs takes
    # the metadata location from the manifest), so pointing it here is the whole delivery mechanism —
    # and the same decision supplies the payload its template tags render.
    first_narrowed, metadata_location, _first_tag_payload = _resolve_step_delivery(
        metadata_envelope, step_metadata_gates.get(1), execution_id, run_bucket, 1,
        metadata_location)

    # output_extension is the caller's output base path, normalized to a single leading + trailing
    # slash and defaulting to "/" (asset root). Its {{dynamicTag}} placeholders are substituted below,
    # once the manifest + execution context the tags read from exist.
    first_manifest = er.build_manifest_envelope(
        input_files=input_entries,
        input_metadata_s3_location=metadata_location,
        outputs=outputs,
        aux_bucket=bucket_name_assetAuxiliary,
        aux_temp_prefix=first_aux_temp_prefix,
        aux_preview_pipeline_suffix=first_aux_preview_suffix,
        system_config=er.build_manifest_system_config(
            orchestration_bus_arn=orchestration_bus_arn,
            orchestration_event_prefix=first_event_prefix),
        output_target=er.build_manifest_output_target(
            location_type=output_location_type, asset_id=output_asset_id,
            database_id=output_database_id,
            file_base_execution_path_extension=output_extension),
    )

    # Render pipeline 1's resolved config against its manifest now; pipelines 2+ keep their resolved
    # (but un-rendered-against-task-manifest) config text — the interim lambda re-renders per task.
    execution_start_ts = er.iso_now()

    # The renderer's metadata-content tags read the legacy {"VAMS": {...}} view; project the v2 grouped
    # envelope onto it for this pipeline's primary input file (the first file in its manifest, falling
    # back to the run's first selection when the pipeline takes no files, then to the first
    # metadata-source asset when the run has no files at all), so tags like {{assetMetadataObject}}
    # resolve to real values rather than {}. A source asset's projection subject is its asset-level
    # record, so its relativeFileKey is '/'.
    _projection_inputs = first_pipeline_inputs or selected_inputs
    if _projection_inputs:
        _first_input = _projection_inputs[0]
    elif metadata_source_assets:
        _first_input = {**metadata_source_assets[0], "relativeFileKey": "/"}
    else:
        _first_input = {}

    def _metadata_payload():
        return _first_tag_payload(_first_input)

    first_context = {
        "executionId": execution_id,
        "workflowId": workflow_id,
        "workflowDatabaseId": workflow_database_id,
        "pipelineExecutionId": pipeline_execution_ids[0] if pipeline_execution_ids else "",
        "pipelineId": first_pipeline.get("pipelineId", ""),
        "pipelineDatabaseId": first_pipeline.get("databaseId", ""),
        "jobName": first_job_name,
        "triggerType": trigger_type_stored,
        "executingUserName": executing_user,
        "executionStartTimestamp": execution_start_ts,
        "inputConfigurationS3Location": f"s3://{run_bucket}/{er.pipeline_input_config_key(execution_id, 1)}",
    }

    # The output base path extension may carry {{dynamicTag}} placeholders; substitute them against
    # pipeline 1's manifest + context so the value that reaches the manifest, the SFN input, and the
    # persisted configuration row is the concrete path outputs are written under.
    output_extension = _render_output_path_extension(
        output_extension, first_manifest, first_context, metadata_loader=_metadata_payload)
    first_manifest["outputTarget"]["fileBaseExecutionPathExtension"] = output_extension

    pipeline_config_bodies = []
    for idx, record in enumerate(pipeline_records):
        composite = er.pipeline_composite_key(record.get("databaseId", ""), record.get("pipelineId", ""))
        rendered = (resolved_configs.get(composite) or {}).get("renderedConfig", "")
        if idx == 0:
            pipeline_config_bodies.append(tr.render_config(
                rendered, first_manifest, first_context, metadata_loader=_metadata_payload))
        else:
            pipeline_config_bodies.append(rendered)

    input_locations = _write_execution_input_files(
        execution_id, run_bucket, len(pipeline_records), metadata_envelope, first_manifest,
        pipeline_config_bodies, step_metadata_gates=step_metadata_gates)

    # SFN input: identity, run bucket, output target, per-pipeline execution ids, user context.
    response = sfn_client.start_execution(
        stateMachineArn=workflow_arn,
        name=execution_id,
        input=json.dumps({
            "workflowExecutionId": execution_id,
            "workflowDatabaseId": workflow_database_id,
            "workflowId": workflow_id,
            "endStatePipelineExecutionId": end_state_pipeline_execution_id,
            "pipelineExecutionIds": pipeline_execution_ids,
            "workflowExecutionS3InputOutputBucket": run_bucket,
            "outputLocationType": output_location_type,
            "outputAssetId": output_asset_id,
            "outputDatabaseId": output_database_id,
            "outputFileBaseExecutionPathExtension": output_extension,
            "executingUserName": executing_user,
            "executingRequestContext": executing_request_context,
            # Per-step DELIVERY metadata keys, one entry per pipeline in workflow order ("" where the
            # step reads the shared envelope). Templates are chosen per EXECUTION while the ASL is
            # baked at workflow save time, so the gates cannot live in the ASL itself — the ASL
            # threads a static index into this per-execution list, exactly as it does for
            # pipelineExecutionIds.
            "stepMetadataS3Keys": [
                input_locations["narrowedMetadataKeys"].get(i + 1, "")
                for i in range(len(pipeline_records))
            ],
            # Per-step input narrowing, threaded for the same reason and in the same order: a step's
            # filters and arity come from its effective config (template overrides applied), so they
            # are known only per execution. The interim lambda applies them before writing the next
            # step's manifest, which otherwise carries the run's entire selection.
            "stepInputFilters": step_input_filters,
            "stepInputArity": step_input_arity,
        }))
    logger.info(f"Started workflow execution {execution_id}")

    # The state machine is running before any record exists, so a failed record write would leave an
    # execution nothing can see or abort. Stop the execution before surfacing the failure, so the run
    # does not keep advancing (and writing outputs) with an incomplete record set.
    try:
        _persist_execution_records(
            execution_id=execution_id, workflow_arn=workflow_arn,
            workflow_execution_arn=response["executionArn"],
            workflow=workflow, pipeline_records=pipeline_records, resolved_configs=resolved_configs,
            selected_inputs=selected_inputs, asset_records=asset_records,
            pipeline_execution_ids=pipeline_execution_ids, first_job_name=first_job_name,
            run_bucket=run_bucket, metadata_envelope=metadata_envelope,
            output_database_id=output_database_id, output_asset_id=output_asset_id,
            output_extension=output_extension, trigger_type_stored=trigger_type_stored,
            executing_user=executing_user, input_locations=input_locations,
            execution_group_id=execution_group_id, output_location_type=output_location_type,
            metadata_source_assets=metadata_source_assets,
            metadata_source_database_id=metadata_source_database_id,
            metadata_source_databases=metadata_source_databases,
            filtered_inputs_by_composite=filtered_inputs_by_composite,
            step_metadata_gates=step_metadata_gates)
    except Exception:
        logger.exception(
            f"Failed persisting execution records for {execution_id}; stopping the started execution "
            f"{response['executionArn']}")
        _stop_started_execution(response["executionArn"])
        raise

    return execution_id


def _stop_started_execution(workflow_execution_arn):
    """Best-effort Step Functions StopExecution for an execution whose records could not be written.
    Never raises: the caller is already surfacing the record-write failure, and an execution that
    cannot be stopped is logged with its ARN so it can be stopped out-of-band."""
    if not workflow_execution_arn:
        return
    try:
        sfn_client.stop_execution(
            executionArn=workflow_execution_arn,
            error="VAMSExecutionRecordWriteFailed",
            cause="The execution's VAMS records could not be written; the execution was stopped.")
    except Exception as e:
        logger.exception(f"Could not stop execution {workflow_execution_arn}: {e}")


def _persist_execution_records(execution_id, workflow_arn, workflow_execution_arn, workflow,
                               pipeline_records, resolved_configs, selected_inputs, asset_records,
                               pipeline_execution_ids, first_job_name, run_bucket, metadata_envelope,
                               output_database_id, output_asset_id, output_extension,
                               trigger_type_stored, executing_user, input_locations,
                               execution_group_id, output_location_type=OUTPUT_LOCATION_TYPE_ASSET,
                               metadata_source_assets=None, metadata_source_database_id="",
                               metadata_source_databases=None, filtered_inputs_by_composite=None,
                               step_metadata_gates=None):
    """Write the main execution row, per-input workflow-input rows, the workflow configuration row,
    one PipelineExecutions row + config-snapshot per pipeline, and the output index rows.

    The metadata-source entities land on the configuration row only. They get no workflow-input row:
    those rows are input FILES, and a re-run rebuilds its inputFiles from them — so a source asset
    recorded there would come back as an input file and fail an arity-'none' workflow's own
    no-input-files rule. Their captured metadata VALUES are still persisted, as scope-tagged
    input-metadata rows alongside the asset ones.

    filtered_inputs_by_composite maps pipelineDatabaseId:pipelineId -> the inputs that pipeline
    receives (the same map the manifest and the cross-entity validator are built from), so each
    pipeline's input-metadata rows describe the entities IT reads rather than the run's whole
    selection. A composite the map does not carry falls back to the full selection, mirroring the
    manifest's own fallback."""
    start_date = er.iso_now()
    workflow_id = workflow["workflowId"]
    workflow_database_id = workflow["databaseId"]
    # Output-path pipeline-name segment mirrors the ASL (`ref.jobName or pipelineId`), so the recorded
    # PipelineExecutions output prefixes match the folder the end-state lambda lists (see _launch_workflow).
    first_pipeline_name = (pipeline_records[0].get("_jobName", "")
                           or pipeline_records[0].get("pipelineId", "")) if pipeline_records else ""
    output_prefixes = er.pipeline_output_prefixes(first_pipeline_name, first_job_name, execution_id) \
        if pipeline_records else {"files": "", "previews": "", "metadata": "", "results": ""}

    # 1) Main V2 row (+ optional group id). The SFN execution is already started, so record RUNNING
    # (not NEW) — every read path shows the true status with no read-time SFN poll.
    main_table = dynamodb.Table(workflow_execution_database_v2)
    main_row = er.build_workflow_execution_record(
        execution_id=execution_id, workflow_database_id=workflow_database_id, workflow_id=workflow_id,
        workflow_arn=workflow_arn, workflow_execution_arn=workflow_execution_arn,
        execution_start_date=start_date, execution_status="RUNNING",
        triggered_by_user_id=executing_user, trigger_type=trigger_type_stored,
        execution_log_group_arn=workflow_execution_log_group_arn,
        execution_group_id=execution_group_id or "")
    main_table.put_item(Item=main_row)

    # 2) One workflow-input row per selected input (asset-scoped GET source of truth).
    wf_inputs_table = dynamodb.Table(workflow_execution_inputs_table)
    for item in selected_inputs:
        database_id = item["databaseId"]
        asset_id = item["assetId"]
        asset = asset_records[(database_id, asset_id)]
        root = _asset_root_key(asset)
        relative = item["relativeFileKey"]
        full_key = root.rstrip("/") + "/" if relative in ("", "/") else _resolve_full_key(root, relative)
        try:
            asset_bucket = _asset_bucket_details(asset.get("bucketId"))["bucketName"]
        except VAMSGeneralErrorResponse:
            asset_bucket = ""
        wf_inputs_table.put_item(Item=er.build_workflow_execution_input_record(
            workflow_execution_id=execution_id, database_id=database_id, asset_id=asset_id,
            input_asset_file_key=full_key, execution_start_date=start_date,
            workflow_id=workflow_id, workflow_database_id=workflow_database_id,
            s3_bucket=asset_bucket, asset_root_s3_key=root,
            version_id=item.get("resolvedVersionId", "")))

    # 3) Workflow configuration row: pipeline snapshot + grouped metadata + output target + the
    #    metadata-source selection.
    wf_cfg_table = dynamodb.Table(workflow_execution_configuration_table)
    wf_cfg_table.put_item(Item=er.build_workflow_configuration_record(
        workflow_execution_id=execution_id,
        input_metadata=json.dumps(metadata_envelope),
        specified_pipelines_snapshot=workflow.get("specifiedPipelines", []),
        output_location_type=output_location_type, output_asset_id=output_asset_id,
        output_database_id=output_database_id,
        output_file_base_execution_path_extension=output_extension,
        input_metadata_file_s3_key=input_locations.get("metadataFileS3Key", ""),
        input_metadata_database_id=metadata_source_database_id or "",
        metadata_source_assets=metadata_source_assets or [],
        metadata_source_databases=metadata_source_databases or [],
        # The SAME timestamp the main row and the per-input rows use (start_date, above), so an
        # output-asset listing orders and bounds identically to the by-input-asset one instead of by
        # when this particular row happened to be written.
        execution_start_date=start_date))

    # 4) One PipelineExecutions row + config-snapshot row per pipeline.
    pexec_table = dynamodb.Table(pipeline_executions_table)
    pin_cfg_table = dynamodb.Table(pipeline_execution_input_configuration_table)
    pin_md_table = dynamodb.Table(pipeline_execution_input_metadata_table)
    config_keys = input_locations.get("configKeys", [])
    # One S3 metadata object per execution, referenced by every input-metadata row of every pipeline.
    metadata_file_key = input_locations.get("metadataFileS3Key", "")
    prev_id = ""
    # The input-metadata rows of every pipeline go out through ONE batch writer spanning the whole loop:
    # boto3's writer buffers to 25 items per BatchWriteItem — the request's hard limit — and retries
    # unprocessed items itself, so a run whose pipelines each contribute a handful of rows fills whole
    # requests instead of sending a part-filled one per pipeline. An asset carrying thousands of files
    # with metadata makes this the dominant cost of recording a run: the rows are one per (asset,
    # filePath) PER PIPELINE, so a per-row put_item spends thousands of serial round-trips inside the
    # same invocation that has to start the state machine.
    #
    # Size-aware chunking is unnecessary: DynamoDB caps a single item at 400 KB, so 25 items cannot
    # exceed 10 MB against BatchWriteItem's 16 MB request limit. That holds for any item the table can
    # accept, independent of MAX_METADATA_BYTES_PER_ENTITY, so it stays true if that cap moves.
    #
    # overwrite_by_pkeys names the table's own key, making a repeated row collapse to its last value the
    # way a per-row put_item did — one request carrying the same key twice is rejected outright, and a
    # file selected at two versionIds yields one metadata row per pipeline (the version is not part of
    # the metadata row's identity).
    #
    # Buffered rows flush on context exit, including when the loop raises: any exception here leaves the
    # caller stopping the started execution and re-surfacing the failure, so a run whose rows are
    # incomplete is aborted rather than left advancing — the same handling a failed put_item got.
    with pin_md_table.batch_writer(
            overwrite_by_pkeys=["pipelineExecutionId", "databaseId:assetId:filePath"]) as pin_md_batch:
        for idx, record in enumerate(pipeline_records):
            pexec_id = pipeline_execution_ids[idx]
            is_end_state = (idx == len(pipeline_records) - 1)
            pipeline_id = record.get("pipelineId", "")
            composite = er.pipeline_composite_key(record.get("databaseId", ""), pipeline_id)
            resolved = resolved_configs.get(composite, {}) or {}
            cfg_key = config_keys[idx] if idx < len(config_keys) else ""
            # Aux temp prefix mirrors the ASL's per-pipeline working folder (jobName-based name), so the
            # recorded prefix matches where the interim lambda stages the pipeline's working files.
            aux_pipeline_name = record.get("_jobName", "") or pipeline_id
            aux_temp_prefix = er.aux_pipeline_prefix(aux_pipeline_name, execution_id)
            event_prefix = er.orchestration_event_prefix(
                orchestration_event_source_prefix, execution_id, pexec_id) \
                if orchestration_event_source_prefix else ""

            pexec_record = er.build_pipeline_execution_record(
                pipeline_execution_id=pexec_id, workflow_execution_id=execution_id,
                pipeline_database_id=record.get("databaseId", ""), pipeline_id=pipeline_id,
                end_state_pipeline=is_end_state, s3_asset_bucket=run_bucket,
                s3_aux_bucket=bucket_name_assetAuxiliary, output_prefixes=output_prefixes,
                input_metadata_file_prefix="", input_config_file_prefix=cfg_key,
                aux_temp_prefix=aux_temp_prefix, aux_preview_prefix="",
                pipeline_execution_type=_pipeline_exec_type(record),
                wait_for_callback=_pipeline_wait_for_callback(record),
                pipeline_resource_arn=_pipeline_resource_arn(record),
                from_pipeline_execution_id=prev_id,
                orchestration_bus_event_prefix=event_prefix)
            # First pipeline starts immediately; the rest stay NEW until the interim lambda advances it.
            # Its start date is stamped here for the same reason: the interim lambda stamps a step as it
            # advances INTO it, which never happens for step 1, so it would otherwise report no duration.
            if idx == 0:
                pexec_record["executionStatus"] = "RUNNING"
                pexec_record["executionStartDate"] = start_date
            pexec_table.put_item(Item=pexec_record)

            # Config snapshot: what the run was built from (traceable + re-runnable).
            pin_cfg_table.put_item(Item=er.build_input_configuration_record(
                pipeline_execution_id=pexec_id,
                input_configuration=resolved.get("renderedConfig", ""),
                input_configuration_file_s3_key=cfg_key,
                template_id=resolved.get("templateId", ""),
                template_schema_version=resolved.get("templateSchemaVersion", ""),
                tag_schema_version=resolved.get("tagSchemaVersion", ""),
                template_tags=resolved.get("templateTags", []),
                custom_template_override_used=resolved.get("customTemplateOverrideUsed", False),
                custom_template_override=resolved.get("customTemplateOverrideRaw", ""),
                config_format=resolved.get("configFormat", ""),
                # The merge of the pipeline's systemConfig with this run's chosen template overrides —
                # the settings the step actually ran under, recomputed here from the same inputs the
                # execute validation used so the snapshot cannot drift from what was enforced.
                effective_system_config=ev.resolve_effective_pipeline_config(
                    record.get("systemConfig", {}) or {}, resolved.get("templateOverrides", {})),
                template_overrides=resolved.get("templateOverrides", {})))

            # Input-metadata snapshot, one row per (asset, filePath) this step received plus one per
            # captured database's own metadata. The grouped envelope is already written to S3 for the
            # pipeline to read; these rows are what the execution DETAILS response reads, so without them
            # the details page reports no input metadata even when the pipeline was handed plenty.
            # Asset-level metadata lands on the '/' filePath row; each row's scope says whether it
            # describes an asset or a database.
            #
            # The rows are narrowed TWICE, and both narrowings are required:
            #   - by TYPE, through this step's own effective metadataInputs gate. These rows are what
            #     the details response reports as "what this step received", so they must be built from
            #     the step's DELIVERY envelope — the same narrowing _resolve_step_delivery applies to
            #     the step's metadata file, its manifest location, and its template-tag payload.
            #     Reporting from the shared envelope instead over-states a gated step's input.
            #   - by ENTITY, to this pipeline's own inputs (plus the run's named metadata sources and
            #     database rows, which every pipeline reads) — see er.pipeline_metadata_envelope_rows
            #     for which row kinds belong to a pipeline and why.
            # The envelope itself stays per-execution and shared: one S3 file every task reads, whose
            # shape these rows do not touch.
            pipeline_inputs = (filtered_inputs_by_composite or {}).get(composite, selected_inputs)
            step_gate = (step_metadata_gates or {}).get(idx + 1)
            step_envelope = er.narrow_metadata_envelope(metadata_envelope, step_gate)
            for row in er.pipeline_metadata_envelope_rows(
                    step_envelope, pipeline_inputs, metadata_source_assets=metadata_source_assets):
                pin_md_batch.put_item(Item=er.build_input_metadata_record(
                    pipeline_execution_id=pexec_id,
                    database_id=row["databaseId"], asset_id=row["assetId"],
                    file_path=row["filePath"], metadata=row["metadata"],
                    source_input_metadata_file_s3_key=metadata_file_key,
                    scope=row["scope"], attributes=row.get("attributes")))
            prev_id = pexec_id

    # The execution's output target is recorded on the configuration row above (outputDatabaseId /
    # outputAssetId, plus the composite key that backs WorkflowExecConfigByOutputAssetGSI). That one
    # row answers both output questions — "which executions wrote to this asset" via the GSI, and
    # "what did this execution write to" by reading the row — so no separate index row is written.


#######################
# Execute orchestration
#######################

def execute_workflow(event, workflow_database_id, workflow_id, request_model):
    """Validate, authorize, resolve templates, cross-validate, and launch an asset-less execution.

    Returns the API response (success with the new execution id + any warnings, or an error)."""
    # 1) Resolve + authorize the workflow; gate enabled + not archived.
    # Tier-2 GET on the workflow object: the right to execute comes from Tier-1 on the execute route,
    # while GET confirms the caller may see the workflow being run (the referenced pipelines are
    # checked the same way). POST on a workflow object means create/modify, not execute.
    workflow = _get_workflow(workflow_database_id, workflow_id)
    if not workflow:
        return validation_error(status_code=404, body={"message": "Workflow does not exist"}, event=event)
    if not _enforce(_with_name(workflow, "workflowName"), OBJECT_TYPE_WORKFLOW, "GET"):
        return authorization_error()
    if workflow.get("archived"):
        return validation_error(body={"message": "Workflow is archived and cannot be executed."}, event=event)
    if workflow.get("enabled") is False:
        return validation_error(body={"message": "Workflow is disabled and cannot be executed."}, event=event)
    if not workflow.get("workflow_arn"):
        logger.error(f"Workflow {workflow_database_id}:{workflow_id} has no deployed state machine")
        return validation_error(body={"message": "Workflow has no deployed state machine."}, event=event)

    # 2) Resolve + authorize referenced pipelines; gate enabled + not archived (also in the validator).
    err, pipeline_records = _resolve_and_authorize_pipelines(workflow, workflow_database_id)
    if err:
        return err

    # The ASL's uuid-prefixed job names are only recoverable from the workflow record's jobNames; a
    # record that cannot supply them would send every pipeline's outputs to a folder the end-state
    # lambda never lists, so refuse to launch rather than orphaning the run's outputs.
    job_names_reason = _stored_job_names_error(workflow, pipeline_records)
    if job_names_reason:
        logger.error(f"Workflow {workflow_database_id}:{workflow_id} cannot be executed: "
                     f"{job_names_reason}")
        return validation_error(body={"message":
            "Workflow's deployed state machine is out of sync with its record. Update the workflow "
            "to redeploy its state machine before executing it."}, event=event)

    # 3) Selected inputs (normalized dicts for the validators + downstream builders). A repeated
    #    selection names one file: it is collapsed here so arity, the pipeline manifest, and the
    #    persisted input rows all describe the same set.
    selected_inputs = []
    _seen_inputs = set()
    for f in (request_model.inputFiles or []):
        item = {
            "databaseId": f.databaseId, "assetId": f.assetId,
            "relativeFileKey": f.relativeFileKey, "versionId": f.versionId or "",
        }
        identity = (item["databaseId"], item["assetId"], item["relativeFileKey"], item["versionId"])
        if identity in _seen_inputs:
            continue
        _seen_inputs.add(identity)
        selected_inputs.append(item)

    # 3b) Metadata sources: entities the run reads stored metadata from. They are not input files, so
    #     they stay out of selected_inputs — which drives arity, filters, output-target resolution, the
    #     S3 existence check, the concurrency guard, and the input-FILE rows. A repeated source asset is
    #     collapsed the same way a repeated input file is.
    metadata_source_assets = []
    _seen_sources = set()
    for source in (request_model.metadataSourceAssets or []):
        identity = (source.databaseId, source.assetId)
        if identity in _seen_sources:
            continue
        _seen_sources.add(identity)
        metadata_source_assets.append({"databaseId": identity[0], "assetId": identity[1]})
    metadata_source_database_id = request_model.metadataSourceDatabaseId or ""

    # The WORKFLOW's assetScope governs how many assets a run may span, so it governs the source span
    # too: one envelope is built from the workflow gate and shared by every step.
    workflow_asset_scope = ev.normalize_asset_scope(
        (workflow.get("systemConfig", {}) or {}).get("assetScope"))
    span_errors = _metadata_source_span_errors(workflow_asset_scope, metadata_source_assets)
    if span_errors:
        return validation_error(body={"message": " ".join(span_errors)}, event=event)

    # 4) Resolve the output target. Three shapes:
    #    - One input asset: output is locked to that asset; outputTarget.allowOverride is the SOLE gate
    #      for redirecting it elsewhere (db falls back to the single input asset's db).
    #    - Zero or multiple input assets: there is no single input asset to lock to, so an explicit
    #      output (both ids) is honored regardless of allowOverride.
    #    - Results-only ("none"): a workflow whose outputTarget.locationType is "none" (arity none) writes
    #      no asset outputs — only results text + logs against the execution. No output asset is resolved.
    output_target_cfg = (workflow.get("systemConfig", {}) or {}).get("outputTarget", {}) or {}
    allow_override = bool(output_target_cfg.get("allowOverride", False))
    declared_location_type = output_target_cfg.get("locationType") or OUTPUT_LOCATION_TYPE_ASSET
    input_asset_keys = {(i["databaseId"], i["assetId"]) for i in selected_inputs}

    requested_output_asset = request_model.outputAssetId
    requested_output_db = request_model.outputDatabaseId
    output_location_type = OUTPUT_LOCATION_TYPE_ASSET
    if declared_location_type == OUTPUT_LOCATION_TYPE_NONE:
        # Results-only: no asset output regardless of inputs. The workflow MAY still take input
        # files (e.g. reading files to emit only results text + logs), so inputs are not rejected;
        # outputs are the execution's results + logs. A supplied output asset is meaningless here,
        # so reject it as a contradiction rather than silently ignoring it.
        if requested_output_asset or requested_output_db:
            return validation_error(body={"message":
                "This workflow is results-only (outputTarget.locationType 'none'): it writes no asset "
                "output. Omit outputAssetId/outputDatabaseId."}, event=event)
        output_location_type = OUTPUT_LOCATION_TYPE_NONE
        output_asset_id = ""
        output_database_id = ""
    elif len(input_asset_keys) == 1:
        # Exactly one input asset: locked to it. allowOverride gates redirecting output away from it.
        single_db, single_asset = next(iter(input_asset_keys))
        if allow_override and requested_output_asset:
            output_asset_id = requested_output_asset
            output_database_id = requested_output_db or single_db
        else:
            output_database_id, output_asset_id = single_db, single_asset
            # A requested target this run cannot honor is a contradiction, not a hint: dropping it
            # silently writes the outputs somewhere the caller never asked for, and the caller is told
            # the run launched. The results-only branch above refuses a supplied target for the same
            # reason. A request naming the asset the run is already locked to is a no-op and passes,
            # which is what lets a re-run replay its recorded target verbatim — for a run that did not
            # override, that recorded target IS the input asset.
            if ((requested_output_asset and requested_output_asset != output_asset_id)
                    or (requested_output_db and requested_output_db != output_database_id)):
                if not allow_override:
                    return validation_error(body={"message":
                        "This workflow writes its output to the single input asset and does not allow "
                        "redirecting it (outputTarget.allowOverride is false). Omit outputAssetId and "
                        "outputDatabaseId."}, event=event)
                return validation_error(body={"message":
                    "Redirecting this execution's output requires outputAssetId; outputDatabaseId "
                    "alone does not name a target asset."}, event=event)
    else:
        # 0 or multiple input assets with an asset output: honor the explicit output (both ids required).
        output_asset_id = requested_output_asset or ""
        output_database_id = requested_output_db or ""
        if not output_asset_id or not output_database_id:
            return validation_error(body={"message":
                "This execution does not resolve to a single input asset; supply an explicit output "
                "target (both outputAssetId and outputDatabaseId), or configure the workflow as "
                "results-only (outputTarget.locationType 'none')."}, event=event)

    # 5) Resolve + authorize input assets (GET) + output asset (POST). Results-only skips the output
    #    asset (there is none); input assets are still authorized (there are none for arity-none).
    if output_location_type == OUTPUT_LOCATION_TYPE_NONE:
        err, asset_records, output_asset, _output_bucket = _resolve_and_authorize_assets(
            selected_inputs, None, None)
    else:
        err, asset_records, output_asset, _output_bucket = _resolve_and_authorize_assets(
            selected_inputs, output_asset_id, output_database_id)
    if err:
        return err

    # 5b) Resolve + authorize the metadata-source entities (asset GET, database GET per database). Their
    #     assets join asset_records so the envelope builder finds their assetData, but never
    #     selected_inputs. The candidate databases are every database the run's own entities live in,
    #     plus the caller's single named database on the arity-'none' path. Only the named one is passed
    #     as named: it fails the launch when denied, while a derived one is skipped.
    metadata_inputs = (workflow.get("systemConfig", {}) or {}).get("metadataInputs", {})
    # A workflow whose databaseMetadata gate is off captures no database metadata at all, so a named
    # source database takes no part in the run: it is dropped here, before it can be derived,
    # authorized or recorded. Recording it anyway is what the read paths gate on
    # (inputMetadataDatabaseId), so a database the launch never made an authorization decision about
    # would become the gate on the launcher's own execution.
    suppressed_source_database = ""
    if (metadata_source_database_id
            and not er.metadata_input_enabled(metadata_inputs, "databaseMetadata")):
        suppressed_source_database = metadata_source_database_id
        metadata_source_database_id = ""
    candidate_source_databases = _derive_metadata_source_databases(
        selected_inputs, metadata_source_assets, metadata_source_database_id, metadata_inputs)
    named_source_databases = ([metadata_source_database_id]
                              if metadata_source_database_id and not selected_inputs else [])
    err, metadata_source_assets, metadata_source_databases = _resolve_and_authorize_metadata_sources(
        metadata_source_assets, candidate_source_databases, asset_records,
        named_database_ids=named_source_databases)
    if err:
        return err
    # A run WITH input files derives its databases from them, so the named database took no part in
    # choosing the set; the recorded selection is narrowed to match, rather than naming a database as the
    # caller's choice when the run reached it another way.
    #
    # The warning names the database only when the DERIVED set really left it out. Naming the database the
    # input files already live in is the ordinary request and that database's metadata is captured
    # regardless, so warning there would contradict the envelope. A database that was derived but is
    # skipped for want of database GET is left to the derived-skip path that owns it
    # (_resolve_and_authorize_metadata_sources logs it): the run did drop it, but not for the reason this
    # message states, and a warning naming the wrong cause is worse than none.
    discarded_source_database = ""
    if selected_inputs:
        if (metadata_source_database_id
                and er.metadata_input_enabled(metadata_inputs, "databaseMetadata")
                and metadata_source_database_id not in candidate_source_databases):
            discarded_source_database = metadata_source_database_id
        metadata_source_database_id = ""

    executing_user = claims_and_roles["tokens"][0] if claims_and_roles.get("tokens") else ""

    # 6) Verify every selected input exists in its own asset bucket.
    missing = _verify_inputs_exist(selected_inputs, asset_records)
    if missing:
        logger.error(f"Workflow input(s) not found in S3: {missing}")
        return validation_error(status_code=404,
                                body={"message": "One or more selected input files do not exist."},
                                event=event)

    # 7) Resolve the default run bucket (all run I/O lives here).
    try:
        run_bucket = _default_run_bucket()["bucketName"]
    except DefaultBucketNotFoundError as de:
        logger.exception(f"Default run bucket not resolved: {de}")
        return internal_error(event=event)

    # 8) Per-pipeline template resolution + tag validation.
    pipeline_exec_params = request_model.pipelineExecutionParameters or {}
    resolution_errors, resolved_configs = _resolve_pipeline_configs(
        pipeline_records, pipeline_exec_params, run_bucket)
    if resolution_errors:
        return validation_error(body={"message": {"templateResolutionErrors": resolution_errors}},
                                event=event)

    # 9) Cross-entity validation (arity, scope, filters, disabled/archived gate).
    output_target = {"outputAssetId": output_asset_id, "outputDatabaseId": output_database_id}
    validation_errors, filtered_inputs_by_composite = _run_cross_validation(
        workflow, pipeline_records, resolved_configs, selected_inputs, output_target)
    if validation_errors:
        return validation_error(body={"message": {"executionValidationErrors": validation_errors}},
                                event=event)

    # 10) Concurrency guard per the workflow's concurrencyRestriction.
    restriction = (workflow.get("systemConfig", {}) or {}).get("concurrencyRestriction", "none")
    if _running_execution_exists(
            workflow_database_id, workflow_id, selected_inputs, asset_records, restriction):
        return validation_error(body={
            "message": "A conflicting execution of this workflow is already running."}, event=event)

    # 11) Grouped input metadata (honoring the workflow's metadataInputs gate) from the selected inputs
    #     plus the resolved metadata sources. capture_notices collects what the capture could not take
    #     whole — truncated entity rows, rows past the total bound, unreadable source databases, a named
    #     source database the run derived past, and one the workflow's gate turns off — for the response
    #     warnings.
    capture_notices = _metadata_capture_warnings(
        [], [], ignored_source_databases=[discarded_source_database] if discarded_source_database else [],
        suppressed_source_databases=[suppressed_source_database] if suppressed_source_database else [])
    metadata_envelope = _build_grouped_metadata(
        selected_inputs, asset_records, metadata_inputs, event,
        metadata_source_assets=metadata_source_assets,
        metadata_source_databases=metadata_source_databases, notices=capture_notices)

    # 12) Launch + persist.
    trigger_type_stored = TRIGGER_TYPE_TO_STORED.get(request_model.triggerType, "Manual")
    output_extension = _resolve_requested_output_extension(request_model, workflow)
    warnings = _missing_metadata_source_warnings(
        pipeline_records, resolved_configs, metadata_inputs, metadata_source_assets,
        metadata_source_databases) + capture_notices
    execution_id = _launch_workflow(
        workflow=workflow, pipeline_records=pipeline_records, resolved_configs=resolved_configs,
        selected_inputs=selected_inputs, asset_records=asset_records, output_asset=output_asset,
        output_database_id=output_database_id, output_asset_id=output_asset_id, run_bucket=run_bucket,
        metadata_envelope=metadata_envelope, trigger_type_stored=trigger_type_stored,
        execution_group_id=request_model.executionGroupId, executing_user=executing_user,
        executing_request_context=event.get("requestContext"),
        output_location_type=output_location_type, output_extension=output_extension,
        filtered_inputs_by_composite=filtered_inputs_by_composite,
        metadata_source_assets=metadata_source_assets,
        metadata_source_database_id=metadata_source_database_id,
        metadata_source_databases=metadata_source_databases)

    # AUDIT LOG: execution launched. Logged after the state machine has started, so a launch that
    # failed is never audited as a run. Tag VALUES are omitted deliberately — they can carry prompts and
    # credential-shaped strings; the counts and the output target are what an audit needs. A
    # trigger-launched run is attributed to SYSTEM_USER by design (a user may upload a file without
    # holding permission to run the workflow), and triggerType records that cause.
    log_actions(event, "workflowExecute", {
        "workflowDatabaseId": workflow_database_id,
        "workflowId": workflow_id,
        "executionId": execution_id,
        "executionGroupId": request_model.executionGroupId or "",
        "triggerType": trigger_type_stored,
        "inputFileCount": len(selected_inputs or []),
        "outputLocationType": output_location_type or "",
        "outputDatabaseId": output_database_id or "",
        "outputAssetId": output_asset_id or "",
        "operation": "execute",
    })
    return success(body={"message": ExecuteWorkflowResponseModel(
        executionId=execution_id, executionGroupId=request_model.executionGroupId,
        warnings=warnings or None).dict()})


#######################
# Route handler
#######################

def handle_post_request(event):
    """Validate path params + request body, then execute the workflow."""
    path_params = event.get("pathParameters", {}) or {}

    required = ["workflowDatabaseId", "workflowId"]
    missing = [p for p in required if not path_params.get(p)]
    if missing:
        return validation_error(
            body={"message": f"Missing path parameter(s): {', '.join(missing)}"}, event=event)

    (valid, message) = validate({
        "workflowDatabaseId": {"value": path_params.get("workflowDatabaseId"), "validator": "ID",
                               "allowGlobalKeyword": True},
        "workflowId": {"value": path_params.get("workflowId"), "validator": "ID"},
    })
    if not valid:
        return validation_error(body={"message": message}, event=event)

    body = {}
    if event.get("body"):
        raw = event["body"]
        if isinstance(raw, str):
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={"message": "Invalid JSON in request body"}, event=event)
        else:
            body = raw

    request_model = parse(body, model=ExecuteWorkflowRequestV2Model)
    return execute_workflow(
        event, path_params["workflowDatabaseId"], path_params["workflowId"], request_model)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for the asset-less execute-workflow API (POST)."""
    global claims_and_roles
    normalize_event(event)
    claims_and_roles = request_to_claims(event)
    # Fresh per-request bucket cache (a warm Lambda container reuses module globals across invokes).
    _bucket_details_cache.clear()

    try:
        method = event["requestContext"]["http"]["method"]

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            if CasbinEnforcer(claims_and_roles).enforceAPI(event):
                method_allowed_on_api = True
        if not method_allowed_on_api:
            return authorization_error()

        if method == "POST":
            return handle_post_request(event)
        return validation_error(body={"message": "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={"message": _clean_validation_message(v)}, event=event)
    except tr.MissingTemplateTagError as e:
        logger.error(f"Input configuration uses undefined template tag(s): {e.unknown_tags}")
        return validation_error(body={"message":
            "An input configuration uses one or more undefined template tags."}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={"message": str(v)}, event=event)
    except DefaultBucketNotFoundError as de:
        logger.exception(f"Default bucket not resolved: {de}")
        return internal_error(event=event)
    except botocore.exceptions.ClientError as err:
        code = err.response.get("Error", {}).get("Code")
        if code in ("LimitExceededException", "ThrottlingException"):
            logger.exception("Throttling Error")
            return general_error(
                status_code=err.response["ResponseMetadata"]["HTTPStatusCode"],
                body={"message": "ThrottlingException: Too many requests within a given period."},
                event=event)
        if code == "ExecutionLimitExceeded":
            logger.exception("ExecutionLimitExceeded")
            return general_error(
                status_code=err.response["ResponseMetadata"]["HTTPStatusCode"],
                body={"message": "ExecutionLimitExceeded: Reached the maximum state machine execution limit."},
                event=event)
        # A record the request made too large for one DynamoDB item is the caller's to shrink (fewer
        # or shorter template tag values, fewer metadata-source entities), so it answers with what to
        # change rather than an internal error. The started execution has already been stopped by the
        # record-write path that re-raised, so nothing keeps running.
        if code == "ValidationException" and _is_item_size_rejection(err):
            logger.exception("Execution record exceeded the DynamoDB item size limit")
            return general_error(body={"message":
                "This execution's recorded inputs are too large to store. Reduce the number or size of "
                "the template tag values, metadata-source assets, and metadata-source databases, then "
                "run it again."}, event=event)
        logger.exception(err)
        return internal_error(event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)


def _execution_running(main_table, execution_id, workflow_composite):
    """True when the execution belongs to this workflow, has no stop date, and Step Functions
    confirms it is still running."""
    main_resp = main_table.query(
        KeyConditionExpression=Key("workflowExecutionId").eq(execution_id), ScanIndexForward=False)
    main_rows = main_resp.get("Items", [])
    if not main_rows:
        return False
    main_item = main_rows[0]
    if main_item.get("workflowDatabaseId:workflowId", "") != workflow_composite:
        return False
    if main_item.get("executionStopDate"):
        return False
    try:
        execution = sfn_client.describe_execution(
            executionArn=main_item.get("workflow_execution_arn", ""))
        return not execution.get("stopDate")
    except Exception as e:
        logger.exception(f"Error confirming running execution {execution_id}: {e}")
        return False
