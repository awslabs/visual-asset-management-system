#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Global Pipeline + Workflow Import Lambda (CloudFormation custom resource).

Registers a built-in (or externally self-registered) pipeline + workflow into the pipeline/workflow
tables from a ``vamsSchema`` bundle at CDK deploy time. It upserts via SYSTEM_USER cross-calls to the
service handlers (pipelineServiceV2, pipelineTemplateService, workflowServiceV2, workflowTriggerService).

Schema delivery: the static schema files (pipeline.json / workflow.json / templates/*.json) are
uploaded to the artefacts bucket by CDK; the CR receives their S3 keys (avoids CFN property / lambda
payload size limits) plus the small deploy-time values — resolved resource ids (Lambda name / SQS url /
bus arn), optional id overrides, and the deploy-time trigger-enable flag. The CR fetches + merges +
parses the bundle, then upserts. The CR is also invocable outside CloudFormation (a direct invoke with
an ``inlineBundle`` or ``bundleS3Keys`` payload) so external solutions can self-register.

Idempotent re-register: on redeploy an existing built-in pipeline/workflow is UPDATED (and re-enabled —
a soft-archived built-in unarchives), so redeploying overwrites from the schema while preserving
execution history. DELETE archives (soft) rather than hard-deletes.
"""

import json
import os

import boto3
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext

from customLogging.logger import safeLogger
from common.workflows import vamsSchemaImport as vsi

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})

lambda_client = boto3.client("lambda", config=retry_config)
s3_client = boto3.client("s3", config=retry_config)
logger = safeLogger(service="ImportGlobalPipelineWorkflow")

try:
    pipeline_service_function = os.environ["PIPELINE_SERVICE_V2_FUNCTION_NAME"]
    template_service_function = os.environ["PIPELINE_TEMPLATE_SERVICE_FUNCTION_NAME"]
    workflow_service_function = os.environ["WORKFLOW_SERVICE_V2_FUNCTION_NAME"]
    trigger_service_function = os.environ["WORKFLOW_TRIGGER_SERVICE_FUNCTION_NAME"]
    # Artefacts/deployment bucket the vamsSchema files are uploaded to (optional: inline bundles
    # don't need it).
    schema_bucket = os.environ.get("VAMS_SCHEMA_BUCKET", "")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

_TARGET_FUNCTIONS = {
    vsi.TARGET_PIPELINE_SERVICE: lambda: pipeline_service_function,
    vsi.TARGET_TEMPLATE_SERVICE: lambda: template_service_function,
    vsi.TARGET_WORKFLOW_SERVICE: lambda: workflow_service_function,
    vsi.TARGET_TRIGGER_SERVICE: lambda: trigger_service_function,
}


class ImportError_(Exception):
    """Import failure surfaced to CloudFormation as a FAILED response."""


# ---------------------------------------------------------------------------
# Cross-call plumbing
# ---------------------------------------------------------------------------

def _invoke(target, method, path, path_parameters, body=None):
    """Invoke a V2 service handler as a SYSTEM_USER lambda cross-call. Returns (status_code, parsed
    body dict). Raises ImportError_ on a transport/parse failure."""
    function_name = _TARGET_FUNCTIONS[target]()
    event = {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_parameters or {},
        "queryStringParameters": {},
        "lambdaCrossCall": {"userName": "SYSTEM_USER"},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    try:
        response = lambda_client.invoke(
            FunctionName=function_name, InvocationType="RequestResponse",
            Payload=json.dumps(event).encode("utf-8"))
    except Exception as e:
        logger.exception(f"Cross-call transport error invoking {function_name} {method} {path}: {e}")
        raise ImportError_(f"Failed invoking {target} service")
    payload = response.get("Payload")
    if not payload:
        raise ImportError_(f"No payload from {target} service")
    parsed = json.loads(payload.read().decode("utf-8"))
    if "errorMessage" in parsed:
        raise ImportError_(f"{target} service errored: {parsed['errorMessage']}")
    status_code = parsed.get("statusCode", 500)
    inner = {}
    if parsed.get("body"):
        try:
            inner = json.loads(parsed["body"])
        except (ValueError, TypeError):
            inner = {}
    return status_code, inner


def _exists(request):
    """GET the exists-path to decide create-vs-update. 200 -> exists, 404 -> absent, else raise."""
    status_code, _ = _invoke(
        request["target"], "GET", request["existsPath"], request.get("existsPathParameters"))
    if status_code == 200:
        return True
    if status_code == 404:
        return False
    raise ImportError_(
        f"Unexpected status {status_code} probing {request['kind']} '{request['id']}'")


def _apply_request(request):
    """Upsert one request descriptor. Templates/triggers use PUT-idempotent set; pipeline/workflow
    probe then create-or-update. Raises ImportError_ on a non-2xx service response."""
    kind = request["kind"]

    if "setPath" in request:  # trigger: idempotent PUT
        status_code, inner = _invoke(
            request["target"], "PUT", request["setPath"], request.get("setPathParameters"),
            body=request["setBody"])
        if status_code != 200:
            raise ImportError_(f"Failed setting {kind} '{request['id']}': {inner.get('message', status_code)}")
        return f"{kind} '{request['id']}' set"

    if kind == "template":
        # Template service is POST-create / PUT-update keyed on templateId.
        if _exists(request):
            status_code, inner = _invoke(
                request["target"], "PUT", request["updatePath"],
                request.get("updatePathParameters"), body=request["updateBody"])
            action = "updated"
        else:
            status_code, inner = _invoke(
                request["target"], "POST", request["createPath"],
                request.get("createPathParameters"), body=request["createBody"])
            action = "created"
        if status_code != 200:
            raise ImportError_(f"Failed {action} {kind} '{request['id']}': {inner.get('message', status_code)}")
        return f"{kind} '{request['id']}' {action}"

    # pipeline / workflow: probe then create or update (+ re-enable via updateBody).
    if _exists(request):
        status_code, inner = _invoke(
            request["target"], "PUT", request["updatePath"],
            request.get("updatePathParameters"), body=request["updateBody"])
        action = "updated"
    else:
        status_code, inner = _invoke(
            request["target"], "POST", request["createPath"],
            request.get("createPathParameters"), body=request["createBody"])
        action = "created"
    if status_code != 200:
        raise ImportError_(f"Failed {action} {kind} '{request['id']}': {inner.get('message', status_code)}")
    return f"{kind} '{request['id']}' {action}"


# ---------------------------------------------------------------------------
# Bundle assembly (inline or S3)
# ---------------------------------------------------------------------------

def _read_s3_json(key):
    if not schema_bucket:
        raise ImportError_("VAMS_SCHEMA_BUCKET not configured but an S3 schema key was supplied")
    try:
        obj = s3_client.get_object(Bucket=schema_bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        logger.exception(f"Failed reading schema object {key}: {e}")
        raise ImportError_(f"Failed reading schema file: {key}")


def assemble_bundle(resource_properties):
    """Assemble the vamsSchema bundle from the CR resource properties. Two delivery modes:
      - inlineBundle: the full bundle object inline (small external self-registrations).
      - bundleS3Keys: {pipeline, workflow?, templates?: [key,...]} of S3 keys; each is fetched from
        the schema bucket. Missing OPTIONAL keys are skipped (minimal-required ingestion).
    Returns the assembled bundle dict. Raises ImportError_ when neither mode nor 'pipeline' present."""
    inline = resource_properties.get("inlineBundle")
    if inline:
        if isinstance(inline, str):
            inline = json.loads(inline)
        return inline

    keys = resource_properties.get("bundleS3Keys") or {}
    if isinstance(keys, str):
        keys = json.loads(keys)
    if not keys.get("pipeline"):
        raise ImportError_("No inlineBundle and no bundleS3Keys.pipeline supplied")

    bundle = {"pipeline": _read_s3_json(keys["pipeline"])}
    if keys.get("workflow"):
        bundle["workflow"] = _read_s3_json(keys["workflow"])
    template_keys = keys.get("templates") or []
    if template_keys:
        bundle["templates"] = [_read_s3_json(k) for k in template_keys]
    return bundle


def _parse_json_prop(value):
    """A CloudFormation resource property may arrive as a JSON string or an object; normalize to a
    dict (empty dict when absent)."""
    if not value:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return dict(value)


def _parse_bool_prop(value):
    """Normalize an optional CloudFormation boolean-ish property to True/False/None. CloudFormation
    passes properties as strings, so "true"/"false" (case-insensitive) and actual bools are honored;
    an absent/empty property returns None (no override)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def register_bundle(resource_properties):
    """Assemble + upsert a vamsSchema bundle. Returns a result dict with the resolved ids + applied
    actions. Raises ImportError_/VamsSchemaError on failure."""
    bundle = assemble_bundle(resource_properties)
    resource_overrides = _parse_json_prop(resource_properties.get("resourceOverrides"))
    id_overrides = _parse_json_prop(resource_properties.get("idOverrides"))
    # Deploy-time trigger enable (the pipeline's autoRegisterAutoTriggerOnFileUpload). A CloudFormation
    # property arrives as a string; treat only "true" (case-insensitive) as True, absent as no override.
    trigger_enabled_override = _parse_bool_prop(resource_properties.get("triggerEnabled"))

    requests = vsi.build_import_requests(
        bundle, resource_overrides, id_overrides, trigger_enabled_override=trigger_enabled_override)
    ids = vsi.collect_ids(bundle, id_overrides)

    applied = []
    for request in requests:
        applied.append(_apply_request(request))

    logger.info(f"Registered vamsSchema bundle: {ids} | {applied}")
    return {"ids": ids, "applied": applied}


def archive_bundle(resource_properties):
    """Archive (soft-delete) the built-in pipeline + workflow a bundle registered. Best-effort: a
    stack teardown is never blocked by a missing/already-archived resource."""
    # For archive we only need the ids; assemble the bundle when present, else fall back to id
    # overrides alone (a Delete may arrive with only the physical-id ids).
    id_overrides = _parse_json_prop(resource_properties.get("idOverrides"))
    try:
        bundle = assemble_bundle(resource_properties)
        ids = vsi.collect_ids(bundle, id_overrides)
    except Exception:
        # Fall back to id overrides only (Delete of a resource created by an older revision).
        ids = {
            "pipelineDatabaseId": id_overrides.get("pipelineDatabaseId", vsi.GLOBAL_DATABASE),
            "pipelineId": id_overrides.get("pipelineId", ""),
            "workflowDatabaseId": id_overrides.get("workflowDatabaseId", vsi.GLOBAL_DATABASE),
            "workflowId": id_overrides.get("workflowId", ""),
        }

    warnings = []
    if ids.get("workflowId"):
        try:
            _invoke(vsi.TARGET_WORKFLOW_SERVICE, "DELETE",
                    f"/database/{ids['workflowDatabaseId']}/workflows/{ids['workflowId']}",
                    {"databaseId": ids["workflowDatabaseId"], "workflowId": ids["workflowId"]})
        except Exception as e:
            warnings.append(f"workflow archive: {e}")
    if ids.get("pipelineId"):
        try:
            _invoke(vsi.TARGET_PIPELINE_SERVICE, "DELETE",
                    f"/database/{ids['pipelineDatabaseId']}/pipelines/{ids['pipelineId']}",
                    {"databaseId": ids["pipelineDatabaseId"], "pipelineId": ids["pipelineId"]})
        except Exception as e:
            warnings.append(f"pipeline archive: {e}")
    return {"ids": ids, "warnings": warnings}


# ---------------------------------------------------------------------------
# CloudFormation custom-resource plumbing
# ---------------------------------------------------------------------------

def send_cfn_response(event, context, status, data=None, physical_resource_id=None, reason=None):
    import urllib3
    data = data or {}
    physical_resource_id = physical_resource_id or context.log_stream_name
    reason = reason or f"See CloudWatch Log Stream: {context.log_stream_name}"
    body = json.dumps({
        "Status": status, "Reason": reason, "PhysicalResourceId": physical_resource_id,
        "StackId": event["StackId"], "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"], "Data": data,
    })
    try:
        http = urllib3.PoolManager()
        resp = http.request("PUT", event["ResponseURL"], body=body,
                            headers={"content-type": "", "content-length": str(len(body))})
        logger.info(f"CloudFormation response status: {resp.status}")
    except Exception as e:
        logger.exception(f"Failed to send CloudFormation response: {e}")


def _physical_id(resource_properties, ids, fallback):
    """Stable physical id from the resolved pipeline id so an id change triggers replace (and thus a
    Delete of the old built-in)."""
    return ids.get("pipelineId") or resource_properties.get("logicalName") or fallback


def lambda_handler(event, context: LambdaContext):
    """CloudFormation custom resource + direct-invoke handler. A CloudFormation event carries
    RequestType; a direct invoke (external self-registration) omits it and is treated as a register."""
    logger.info(f"Received event: {json.dumps(event, default=str)}")

    # Direct (non-CloudFormation) invoke: register + return the result inline.
    if "RequestType" not in event:
        result = register_bundle(event.get("ResourceProperties") or event)
        return {"statusCode": 200, "body": json.dumps(result)}

    request_type = event["RequestType"]
    resource_properties = event.get("ResourceProperties", {}) or {}
    try:
        if request_type in ("Create", "Update"):
            result = register_bundle(resource_properties)
        elif request_type == "Delete":
            result = archive_bundle(resource_properties)
        else:
            raise ImportError_(f"Unsupported RequestType: {request_type}")

        physical_id = event.get("PhysicalResourceId") or _physical_id(
            resource_properties, result.get("ids", {}), context.log_stream_name)
        send_cfn_response(event, context, "SUCCESS", result, physical_resource_id=physical_id,
                          reason=f"{request_type} completed")
        return {"statusCode": 200, "body": json.dumps(result)}
    except Exception as e:
        logger.exception(f"{request_type} failed: {e}")
        # A Delete must never block teardown: report success-with-warning rather than FAILED.
        if request_type == "Delete":
            send_cfn_response(event, context, "SUCCESS", {"warning": str(e)},
                              physical_resource_id=event.get("PhysicalResourceId", context.log_stream_name),
                              reason=f"Delete completed with warnings: {e}")
            return {"statusCode": 200, "body": json.dumps({"warning": str(e)})}
        send_cfn_response(event, context, "FAILED", {},
                          physical_resource_id=event.get("PhysicalResourceId", context.log_stream_name),
                          reason=str(e))
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
