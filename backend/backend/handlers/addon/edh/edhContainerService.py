# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""EDH container routes: the Batch/EKS catalog, and submit, list and kill of the caller's jobs.

Serves ``/addon/edh/containers/*``. Every EDH call runs under the root key with the caller's
``edhUid`` as ``X-EDH-USER``, so EDH scopes the job listing to the caller and attributes each job to
them. This handler holds no ECR, Batch or EKS permissions of its own; every container operation
goes through the EDH API. The catalog offers the job definitions the caller may submit, every queue
and every EKS cluster, each projected to a curated set of fields rather than the Batch record EDH
relays. A Batch job is addressed by the job id EDH reports; an EKS job by its name inside one
cluster and namespace, so the eks backend carries ``cluster`` (required) and ``namespace``
(``default``) as query parameters on the list and kill routes. Memory is MiB on this API and
gigabytes on EDH's; the conversion happens here.
"""

import json
import math
import os
import re

from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.apiRoutes import (
    API_EDH_CONTAINERS_CATALOG,
    API_EDH_CONTAINERS_JOB,
    API_EDH_CONTAINERS_JOBS,
)
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_actions
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse, validation_error_message,
)
from models.addon.edh import CONTAINER_BACKENDS, SubmitContainerJobRequestModel
from .common.edhClient import (
    EDH_CONTAINER_JOB_ROUTES, EDH_CONTAINER_JOBS_ROUTES, EDH_EKS_DEFAULT_NAMESPACE,
    EDH_JOB_HANDLE_FIELD, EDH_JOB_ID_FIELD, EDH_ROUTE_BATCH_JOB_DEFINITIONS,
    EDH_ROUTE_BATCH_JOB_QUEUES, EDH_ROUTE_EKS_CLUSTERS, EdhClient, EdhError,
    map_edh_error_to_response,
)
from .common.edhIdentity import SYSTEM_USER_ID, edh_uid_for

logger = safeLogger(service_name="EdhContainerService")

claims_and_roles = {}

# Casbin object type enforced by this handler.
CONTAINER_OBJECT_TYPE = "edhContainer"

# Backends of the EDH container API. A list or kill that names none addresses Batch; a submit names its backend.
BACKEND_BATCH = "batch"
BACKEND_EKS = "eks"
DEFAULT_BACKEND = BACKEND_BATCH

# `kind` of each catalog item.
CATALOG_KIND_JOB_DEFINITION = "jobDefinition"
CATALOG_KIND_JOB_QUEUE = "jobQueue"
CATALOG_KIND_EKS_CLUSTER = "eksCluster"

# EDH record fields. Batch records are boto3 describe shapes; an EKS job is the Kubernetes Job dict
# EDH returns plus the cluster and image list it adds; an EKS cluster is listed as its bare name.
EDH_FIELD_JOB_DEFINITION_NAME = "jobDefinitionName"
EDH_FIELD_JOB_DEFINITION_ARN = "jobDefinitionArn"
EDH_FIELD_JOB_DEFINITION_REVISION = "revision"
EDH_FIELD_JOB_DEFINITION_STATUS = "status"
EDH_FIELD_JOB_DEFINITION_PLATFORMS = "platformCapabilities"
EDH_FIELD_CONTAINER_PROPERTIES = "containerProperties"
EDH_FIELD_CONTAINER_IMAGE = "image"
EDH_FIELD_RESOURCE_REQUIREMENTS = "resourceRequirements"
EDH_FIELD_JOB_QUEUE_NAME = "jobQueueName"
EDH_FIELD_JOB_QUEUE_ARN = "jobQueueArn"
EDH_FIELD_JOB_QUEUE_STATE = "state"
EDH_FIELD_JOB_QUEUE_STATUS = "status"
EDH_FIELD_BATCH_JOB_ID = "jobId"
EDH_FIELD_BATCH_JOB_NAME = "jobName"
EDH_FIELD_BATCH_JOB_STATUS = "status"
EDH_FIELD_BATCH_JOB_DEFINITION = "jobDefinition"
EDH_FIELD_BATCH_JOB_STATUS_REASON = "statusReason"
EDH_FIELD_BATCH_JOB_QUEUE_NAME = "jobQueueName"
EDH_FIELD_BATCH_JOB_CREATED_AT = "createdAt"
EDH_FIELD_BATCH_JOB_STARTED_AT = "startedAt"
EDH_FIELD_BATCH_JOB_STOPPED_AT = "stoppedAt"
EDH_FIELD_EKS_METADATA = "metadata"
EDH_FIELD_EKS_CREATION_TIMESTAMP = "creation_timestamp"
EDH_FIELD_EKS_JOB_NAME = "name"
EDH_FIELD_EKS_NAMESPACE = "namespace"
EDH_FIELD_EKS_CLUSTER = "eks_cluster"
EDH_FIELD_EKS_IMAGES = "images"
EDH_FIELD_EKS_STATUS = "status"
# The field an EKS cluster's name is offered under in the catalog.
CLUSTER_NAME_FIELD = "name"

# Kubernetes Job status counters -> the status word a listed EKS job carries; first match wins.
EKS_STATUS_BY_COUNTER = (("active", "RUNNING"), ("succeeded", "SUCCEEDED"), ("failed", "FAILED"))
EKS_STATUS_PENDING = "PENDING"

# Request-model field -> EDH submit form field, per backend. _submit_payload renders the values as
# EDH's parsers read them: lists and mappings as JSON text, numbers as strings, memory in gigabytes.
EDH_SUBMIT_FIELDS_BY_BACKEND = {
    BACKEND_BATCH: {
        "jobDefinition": "job_definition",
        "jobQueue": "job_queue",
        "name": "job_name",
        "command": "overrideCommands",
        "environment": "envVars",
        "vcpus": "vcpus",
        "memory": "memory",
        "gpu": "consumableResources",
    },
    BACKEND_EKS: {
        "jobDefinition": "image_uri",
        "jobQueue": "eks_cluster",
        "name": "job_name",
        "command": "overrideCommands",
        "environment": "envVars",
        "vcpus": "cpu",
        "memory": "memory",
        "gpu": "gpu",
    },
}
# EDH reads memory as gigabytes on both backends; this API and the job definitions use MiB.
MIB_PER_GB = 1024
MIN_JOB_MEMORY_MIB = 512
EDH_GPU_RESOURCE_TYPE = "GPU"
# Resource requirement types of a job definition, and the top-level keys older definitions carry the
# same two numbers under.
RESOURCE_TYPE_VCPU = "VCPU"
RESOURCE_TYPE_MEMORY = "MEMORY"
LEGACY_RESOURCE_KEYS = {RESOURCE_TYPE_VCPU: "vcpus", RESOURCE_TYPE_MEMORY: "memory"}

# Job names as each backend accepts them: Batch's own rule, and the lower-case DNS label EDH
# lower-cases an EKS name into (lower-case is required here so the listed name equals the submitted one).
JOB_NAME_PATTERNS = {
    BACKEND_BATCH: re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'),
    BACKEND_EKS: re.compile(r'^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$'),
}
JOB_NAME_RULES = {
    BACKEND_BATCH: ("name must be 1-128 letters, digits, hyphens or underscores, starting with a letter or digit, "
                    "for the batch backend"),
    BACKEND_EKS: "name must be a lower-case DNS label of at most 63 characters for the eks backend",
}

# Job ids as they appear in a route (a Batch job id or an EKS job name), and cluster/namespace values.
PATH_ID_PATTERN = re.compile(r'^[A-Za-z0-9._-]{1,128}$')
EKS_NAME_PATTERN = re.compile(r'^[A-Za-z0-9._-]{1,128}$')

# The EDH route behind each client wrapper, as recorded on the edhProxy audit trail.
EDH_CALLS = {
    "list_batch_job_definitions": ("GET", EDH_ROUTE_BATCH_JOB_DEFINITIONS),
    "list_batch_queues": ("GET", EDH_ROUTE_BATCH_JOB_QUEUES),
    "list_eks_clusters": ("GET", EDH_ROUTE_EKS_CLUSTERS),
}
# Wrappers whose route depends on the backend they are called with (the argument after edh_uid).
EDH_BACKEND_CALLS = {
    "submit_job": ("POST", EDH_CONTAINER_JOB_ROUTES),
    "list_jobs": ("GET", EDH_CONTAINER_JOBS_ROUTES),
    "kill_job": ("DELETE", EDH_CONTAINER_JOB_ROUTES),
}

try:
    eks_enabled = os.environ["EDH_CONTAINERS_EKS_ENABLED"] == "true"
except Exception as e:
    logger.exception("Failed loading configuration")
    raise e


# ---------------------------------------------------------------------------
# Caller identity and authorization
# ---------------------------------------------------------------------------
def _requesting_user_id():
    return claims_and_roles['tokens'][0] if claims_and_roles.get('tokens') else None


def _caller_edh_uid():
    return edh_uid_for(_requesting_user_id())


def _enforcer():
    return CasbinEnforcer(claims_and_roles)


def _container_auth_object(job_definition):
    """The edhContainer enforcement dict: exactly the matrix field, as a string."""
    return {
        "object__type": CONTAINER_OBJECT_TYPE,
        "edhJobDefinition": str(job_definition if job_definition is not None else ""),
    }


# ---------------------------------------------------------------------------
# EDH calls
# ---------------------------------------------------------------------------
def _edh_call_route(wrapper, args):
    """(method, route) of a client wrapper call, resolving backend-dependent routes from the backend."""
    if wrapper in EDH_CALLS:
        return EDH_CALLS[wrapper]
    method, routes = EDH_BACKEND_CALLS[wrapper]
    return method, routes[args[1]]


def _call_edh(event, client, wrapper, operation, edh_uid, *args, **kwargs):
    """One EDH call through a client wrapper, audited as edhProxy after it returns or raises.

    ``args``/``kwargs`` are the wrapper's own arguments; ``edh_uid`` is the principal recorded on the
    audit record. Any exception the wrapper raises is recorded by its type name and re-raised: a typed
    EDH error for the caller to map, anything else for the handler's internal-error response.
    """
    edh_method, edh_route = _edh_call_route(wrapper, args)
    audit = {
        "operation": operation,
        "edhUid": edh_uid,
        "edhMethod": edh_method,
        "edhRoute": edh_route,
    }
    try:
        result = getattr(client, wrapper)(*args, **kwargs)
    except Exception as err:
        log_actions(event, "edhProxy", {**audit, "edhStatus": type(err).__name__})
        raise
    log_actions(event, "edhProxy", {**audit, "edhStatus": "ok"})
    return result


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------
def _parse_body(event):
    """The JSON object of the request body; anything else is a 400."""
    try:
        body = json.loads(event.get('body') or '{}')
    except ValueError:
        raise VAMSGeneralErrorResponse("Request body must be a JSON object")
    if not isinstance(body, dict):
        raise VAMSGeneralErrorResponse("Request body must be a JSON object")
    return body


def _path_id(event, name):
    """The path parameter ``name`` as a string, or None when absent or malformed."""
    value = (event.get('pathParameters') or {}).get(name)
    if value is None or not PATH_ID_PATTERN.fullmatch(str(value)):
        return None
    return str(value)


def _resolve_backend(value):
    """The backend a request names: an absent query value means Batch; an unknown or disabled one is a 400."""
    backend = value or DEFAULT_BACKEND
    if backend not in CONTAINER_BACKENDS:
        raise VAMSGeneralErrorResponse(f"backend must be one of: {', '.join(CONTAINER_BACKENDS)}")
    if backend == BACKEND_EKS and not eks_enabled:
        raise VAMSGeneralErrorResponse("The eks backend is not enabled in this deployment")
    return backend


def _query_scope(event):
    """(backend, scope) of a list or kill: ``{}`` for Batch, ``{cluster, namespace}`` for eks."""
    query = event.get('queryStringParameters') or {}
    backend = _resolve_backend(query.get('backend'))
    if backend == BACKEND_BATCH:
        return backend, {}
    cluster = query.get('cluster')
    namespace = query.get('namespace') or EDH_EKS_DEFAULT_NAMESPACE
    if not cluster or not EKS_NAME_PATTERN.fullmatch(str(cluster)):
        raise VAMSGeneralErrorResponse("cluster is required for the eks backend")
    if not EKS_NAME_PATTERN.fullmatch(str(namespace)):
        raise VAMSGeneralErrorResponse("namespace is invalid")
    return backend, {"cluster": str(cluster), "namespace": str(namespace)}


# ---------------------------------------------------------------------------
# Catalog items
# ---------------------------------------------------------------------------
def _resource_number(properties, resource_type):
    """The number a definition requests for ``resource_type``: from its resource requirements, else from
    the legacy top-level key; None when absent or not a finite number. A whole number reads as an int
    and a fractional Fargate vCPU keeps its fraction."""
    value = None
    for requirement in properties.get(EDH_FIELD_RESOURCE_REQUIREMENTS) or []:
        if isinstance(requirement, dict) and requirement.get("type") == resource_type:
            value = requirement.get("value")
    if value is None and resource_type in LEGACY_RESOURCE_KEYS:
        value = properties.get(LEGACY_RESOURCE_KEYS[resource_type])
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _job_definition_item(record):
    """The curated catalog shape of one Batch job definition; nothing else of the record is copied."""
    properties = record.get(EDH_FIELD_CONTAINER_PROPERTIES)
    properties = properties if isinstance(properties, dict) else {}
    return {
        "kind": CATALOG_KIND_JOB_DEFINITION,
        "jobDefinitionName": record.get(EDH_FIELD_JOB_DEFINITION_NAME),
        "jobDefinitionArn": record.get(EDH_FIELD_JOB_DEFINITION_ARN),
        "revision": record.get(EDH_FIELD_JOB_DEFINITION_REVISION),
        "status": record.get(EDH_FIELD_JOB_DEFINITION_STATUS),
        "image": properties.get(EDH_FIELD_CONTAINER_IMAGE),
        "platformCapabilities": record.get(EDH_FIELD_JOB_DEFINITION_PLATFORMS),
        "vcpus": _resource_number(properties, RESOURCE_TYPE_VCPU),
        "memory": _resource_number(properties, RESOURCE_TYPE_MEMORY),
        "gpu": _resource_number(properties, EDH_GPU_RESOURCE_TYPE),
    }


def _job_queue_item(record):
    """The curated catalog shape of one Batch job queue."""
    return {
        "kind": CATALOG_KIND_JOB_QUEUE,
        "jobQueueName": record.get(EDH_FIELD_JOB_QUEUE_NAME),
        "jobQueueArn": record.get(EDH_FIELD_JOB_QUEUE_ARN),
        "state": record.get(EDH_FIELD_JOB_QUEUE_STATE),
        "status": record.get(EDH_FIELD_JOB_QUEUE_STATUS),
    }


# ---------------------------------------------------------------------------
# Job records
# ---------------------------------------------------------------------------
def _job_definition_name(value):
    """A Batch job definition's name from its ARN, its ``name:revision`` form or its bare name."""
    text = str(value or "")
    if text.startswith("arn:") and "job-definition/" in text:
        text = text.split("job-definition/", 1)[1]
    return text.split(":", 1)[0]


def _eks_status(status):
    """The status word for a Kubernetes Job's ``status`` counters."""
    counters = status if isinstance(status, dict) else {}
    for counter, word in EKS_STATUS_BY_COUNTER:
        try:
            if int(counters.get(counter) or 0) > 0:
                return word
        except (TypeError, ValueError):
            continue
    return EKS_STATUS_PENDING


def _job_item(record, backend, scope):
    """The curated shape of one listed job: the id, name, status, definition and handle VAMS names, plus
    the queue and timing fields of the backend's record. Nothing else of the record is copied -- the
    container section a Batch record carries echoes the environment the job was submitted with."""
    if backend == BACKEND_BATCH:
        return {
            "backend": backend,
            "jobId": str(record.get(EDH_FIELD_BATCH_JOB_ID) or ""),
            "jobName": str(record.get(EDH_FIELD_BATCH_JOB_NAME) or ""),
            "status": str(record.get(EDH_FIELD_BATCH_JOB_STATUS) or ""),
            "statusReason": record.get(EDH_FIELD_BATCH_JOB_STATUS_REASON),
            "edhJobDefinition": _job_definition_name(record.get(EDH_FIELD_BATCH_JOB_DEFINITION)),
            "jobHandle": str(record.get(EDH_JOB_HANDLE_FIELD) or ""),
            "jobQueueName": record.get(EDH_FIELD_BATCH_JOB_QUEUE_NAME),
            "createdAt": record.get(EDH_FIELD_BATCH_JOB_CREATED_AT),
            "startedAt": record.get(EDH_FIELD_BATCH_JOB_STARTED_AT),
            "stoppedAt": record.get(EDH_FIELD_BATCH_JOB_STOPPED_AT),
        }
    metadata = record.get(EDH_FIELD_EKS_METADATA)
    metadata = metadata if isinstance(metadata, dict) else {}
    images = record.get(EDH_FIELD_EKS_IMAGES)
    images = images if isinstance(images, list) else []
    name = str(metadata.get(EDH_FIELD_EKS_JOB_NAME) or "")
    return {
        "backend": backend,
        "jobId": name,
        "jobName": name,
        "status": _eks_status(record.get(EDH_FIELD_EKS_STATUS)),
        "edhJobDefinition": str(images[0]) if images else "",
        "jobHandle": str(record.get(EDH_JOB_HANDLE_FIELD) or ""),
        "cluster": str(record.get(EDH_FIELD_EKS_CLUSTER) or scope.get("cluster") or ""),
        "namespace": str(metadata.get(EDH_FIELD_EKS_NAMESPACE) or scope.get("namespace") or ""),
        "creationTimestamp": metadata.get(EDH_FIELD_EKS_CREATION_TIMESTAMP),
    }


def _job_handle(backend, scope, job_id):
    """The handle the client kills by: ``batch:<jobId>`` or ``eks:<cluster>/<namespace>/<name>``."""
    if backend == BACKEND_BATCH:
        return f"batch:{job_id}"
    return f"eks:{scope['cluster']}/{scope['namespace']}/{job_id}"


def _list_records(event, client, operation, edh_uid, backend, scope):
    """The caller's jobs on one backend as items, EKS items limited to the requested cluster/namespace."""
    kwargs = {"cluster": scope["cluster"], "namespace": scope["namespace"]} if scope else {}
    records = _call_edh(event, client, "list_jobs", operation, edh_uid, edh_uid, backend, **kwargs)
    items = [_job_item(record, backend, scope) for record in records if isinstance(record, dict)]
    if backend == BACKEND_EKS:
        items = [item for item in items
                 if item["cluster"] == scope["cluster"] and item["namespace"] == scope["namespace"]]
    return items


# ---------------------------------------------------------------------------
# Submit body
# ---------------------------------------------------------------------------
def _submit_definition(request, backend):
    """The edhJobDefinition value a submit is authorized on: the Batch definition name, or the image."""
    return _job_definition_name(request.jobDefinition) if backend == BACKEND_BATCH else request.jobDefinition


def _submit_payload(request, backend):
    """The EDH submit form for the backend, every unset optional omitted, values in EDH's units."""
    values = request.dict()
    if not values.get("jobQueue"):
        raise VAMSGeneralErrorResponse(
            "jobQueue is required: a Batch job queue, or the EKS cluster for the eks backend")
    if not JOB_NAME_PATTERNS[backend].fullmatch(values["name"]):
        raise VAMSGeneralErrorResponse(JOB_NAME_RULES[backend])
    memory = values.get("memory")
    if memory is not None and memory < MIN_JOB_MEMORY_MIB:
        raise VAMSGeneralErrorResponse(f"memory must be at least {MIN_JOB_MEMORY_MIB} MiB")

    payload = {}
    for model_field, edh_field in EDH_SUBMIT_FIELDS_BY_BACKEND[backend].items():
        value = values.get(model_field)
        if value is None:
            continue
        if model_field == "command":
            if value:
                payload[edh_field] = json.dumps(list(value))
        elif model_field == "environment":
            if value:
                payload[edh_field] = json.dumps([{"name": key, "value": val} for key, val in value.items()])
        elif model_field == "memory":
            payload[edh_field] = str(value / MIB_PER_GB)
        elif model_field == "gpu":
            if value > 0:
                payload[edh_field] = (json.dumps([{"type": EDH_GPU_RESOURCE_TYPE, "value": str(value)}])
                                      if backend == BACKEND_BATCH else str(value))
        else:
            payload[edh_field] = str(value)
    if backend == BACKEND_EKS:
        payload["namespace"] = EDH_EKS_DEFAULT_NAMESPACE
    return payload


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
def get_catalog(event):
    """The job definitions the caller may submit, every queue and (when enabled) every EKS cluster,
    each in its curated shape and tagged with its kind."""
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    definitions = _call_edh(event, client, "list_batch_job_definitions", "containerCatalog", edh_uid)
    queues = _call_edh(event, client, "list_batch_queues", "containerCatalog", edh_uid)
    enforcer = _enforcer() if len(claims_and_roles["tokens"]) > 0 else None
    items = []
    for record in definitions:
        if not isinstance(record, dict):
            continue
        item = _job_definition_item(record)
        # Appends only on an allow, so an absent enforcer offers no definition, never every one.
        if enforcer and enforcer.enforce(_container_auth_object(item["jobDefinitionName"]), "GET"):
            items.append(item)
    items.extend(_job_queue_item(record) for record in queues if isinstance(record, dict))
    if eks_enabled:
        clusters = _call_edh(event, client, "list_eks_clusters", "containerCatalog", edh_uid)
        items.extend({"kind": CATALOG_KIND_EKS_CLUSTER, CLUSTER_NAME_FIELD: name}
                     for name in clusters if isinstance(name, str) and name)
    return success(body={"Items": items, "eksEnabled": eks_enabled})


def list_jobs(event):
    """The caller's jobs on one backend, each passed through edhContainer enforcement."""
    backend, scope = _query_scope(event)
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    items = _list_records(event, client, "containerJobsList", edh_uid, backend, scope)

    enforcer = _enforcer() if len(claims_and_roles["tokens"]) > 0 else None
    allowed = []
    for item in items:
        # Appends only on an allow, so an absent enforcer yields an empty list, never an open one.
        if enforcer and enforcer.enforce(_container_auth_object(item["edhJobDefinition"]), "GET"):
            allowed.append(item)
    return success(body={'Items': allowed})


def submit_job(event):
    body = _parse_body(event)
    request = parse(event=body, model=SubmitContainerJobRequestModel)
    backend = _resolve_backend(request.backend)
    payload = _submit_payload(request, backend)

    # Tier-2 from the validated body, before any EDH call.
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error(event=event)
    if not _enforcer().enforce(_container_auth_object(_submit_definition(request, backend)), "POST"):
        return authorization_error(event=event)

    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    submitted = _call_edh(event, client, "submit_job", "containerJobSubmit", edh_uid,
                          edh_uid, backend, payload)
    submitted = submitted if isinstance(submitted, dict) else {}
    response = {
        "jobId": str(submitted.get(EDH_JOB_ID_FIELD) or submitted.get("job_name") or ""),
        "jobHandle": str(submitted.get(EDH_JOB_HANDLE_FIELD) or ""),
        "backend": backend,
        "jobDefinition": request.jobDefinition,
        "jobQueue": request.jobQueue,
        "name": request.name,
        "message": str(submitted.get("message") or ""),
    }
    if backend == BACKEND_EKS:
        response["cluster"] = request.jobQueue
        response["namespace"] = EDH_EKS_DEFAULT_NAMESPACE
    return success(status_code=202, body=response)


def kill_job(event):
    job_id = _path_id(event, 'jobId')
    if job_id is None:
        return validation_error(body={'message': "jobId is invalid"}, event=event)
    backend, scope = _query_scope(event)
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")

    # The caller's own listing decides whether the id is theirs; a foreign or unknown id reads the same.
    items = _list_records(event, client, "containerJobKill", edh_uid, backend, scope)
    item = next((candidate for candidate in items if candidate["jobId"] == job_id), None)
    if item is None:
        return general_error(status_code=404, body={'message': "Job not found"}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error(event=event)
    if not _enforcer().enforce(_container_auth_object(item["edhJobDefinition"]), "DELETE"):
        return authorization_error(event=event)

    killed = _call_edh(event, client, "kill_job", "containerJobKill", edh_uid,
                       edh_uid, backend, _job_handle(backend, scope, job_id))
    killed = killed if isinstance(killed, dict) else {}
    response = {"jobId": job_id, "backend": backend, "message": str(killed.get("message") or "")}
    response.update(scope)
    return success(body=response)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def handle_get(event, path):
    if API_EDH_CONTAINERS_CATALOG.matches(path):
        return get_catalog(event)
    if API_EDH_CONTAINERS_JOBS.matches(path):
        return list_jobs(event)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_post(event, path):
    if API_EDH_CONTAINERS_JOBS.matches(path):
        return submit_job(event)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_delete(event, path):
    if API_EDH_CONTAINERS_JOB.matches(path):
        return kill_job(event)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error(event=event)

        # The system identity is never an EDH principal.
        if claims_and_roles["tokens"][0] == SYSTEM_USER_ID:
            return authorization_error(event=event)

        if method == 'GET':
            return handle_get(event, path)
        elif method == 'POST':
            return handle_post(event, path)
        elif method == 'DELETE':
            return handle_delete(event, path)
        else:
            return validation_error(body={'message': "Method not allowed"}, event=event)

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': validation_error_message(v)}, event=event)
    except EdhError as e:
        logger.exception(f"EDH error: {type(e).__name__}")
        return map_edh_error_to_response(e, event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
