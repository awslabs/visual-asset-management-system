# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""EDH Workstations admin routes: image catalog administration, cluster-wide instance views and
lifecycle actions, capacity and health.

Serves ``/addon/edh/admin/*``. Every route runs under the EDH root key. A stop names the session
owner as ``X-EDH-USER`` because EDH filters stop on the owner for every caller; a deactivate names
the service actor, whose delete the root key lets through for any owner; the audit trail records the
administrator who asked. Both actions revoke the session's VAMS credential inline once EDH has acted,
as the owner's own stop and delete do; a revoke that fails is logged and left to the reconciler.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from functools import partial

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.apiRoutes import (
    API_EDH_ADMIN_CAPACITY,
    API_EDH_ADMIN_HEALTH,
    API_EDH_ADMIN_IMAGE_SIMULATION,
    API_EDH_ADMIN_IMAGE_VDI,
    API_EDH_ADMIN_IMAGES,
    API_EDH_ADMIN_IMAGES_SIMULATION,
    API_EDH_ADMIN_IMAGES_VDI,
    API_EDH_ADMIN_INSTANCE_DEACTIVATE,
    API_EDH_ADMIN_INSTANCE_STOP,
    API_EDH_ADMIN_INSTANCES,
    API_EDH_ADMIN_INSTANCES_STALE,
    API_EDH_ADMIN_SETTINGS,
)
from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import get_table_name
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_actions
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse, validation_error_message,
)
from models.addon.edh import (
    LeaseSettingsRequestModel, RegisterSimulationImageRequestModel, RegisterVdiImageRequestModel,
)
from .common.edhCatalogFields import (
    IMAGE_KIND_SIMULATION, IMAGE_KIND_VDI, KIND_BY_IMAGE_KIND, KIND_DCV, KIND_TARGET_NODES,
    OS_FAMILY_LINUX, ensure_stack_in_project, os_family_from_base_os, project_assign_error,
    simulation_stack_fields, stack_architecture, vdi_stack_fields,
)
from .common.edhClient import (
    EDH_PROFILE_ROUTES, EDH_ROUTE_DESKTOPS_DELETE, EDH_ROUTE_DESKTOPS_LIST_ALL, EDH_ROUTE_DESKTOPS_STOP,
    EDH_SERVICE_ACTOR_UID, EDH_SESSION_CREATED_ON_FIELD, EDH_SESSION_ID_FIELD, EDH_SESSION_NAME_FIELD,
    EDH_ROUTE_PROJECTS, EDH_SESSION_OWNER_FIELD, EDH_SOFTWARE_STACK_ID_FIELD, EDH_SOFTWARE_STACK_ROUTES,
    EdhApiError, EdhAuthError, EdhClient, EdhError, EdhUnavailableError, edh_error_response_parts,
    edh_session_name, map_edh_error_to_response, outcome_unknown_fields,
)
from .common.edhIdentity import SYSTEM_USER_ID, edh_uid_for
from .common import edhSessionBootstrap as bootstrap
from .common.edhLeasePolicy import LeaseValidationError, read_lease_policy, write_lease_policy
from .common.edhSessionState import (
    EDH_SESSION_STATE_FIELD, EXPIRES_AT_FIELD, STATE_CREATING, STATE_DELETED, STATE_ORPHANED, STATE_STOPPED,
    deleted_row_expiry, start_stamp_cleared, state_for_edh_session_state,
)

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="EdhAdminService")

claims_and_roles = {}

# Casbin object types enforced by this handler.
WORKSTATION_OBJECT_TYPE = "edhWorkstation"
IMAGE_OBJECT_TYPE = "edhImage"

# Session table index.
SESSION_UUID_INDEX = "sessionUuidIndex"

# EDH software-stack records as the list routes return them (the SoftwareStacks and
# TargetNodeSoftwareStacks columns); the register FORM fields and the record's architecture reader
# are edhCatalogFields'.
EDH_FIELD_STACK_ID = EDH_SOFTWARE_STACK_ID_FIELD
EDH_FIELD_STACK_NAME = "stack_name"
EDH_FIELD_STACK_AMI_ID = "ami_id"
EDH_FIELD_STACK_BASE_OS = "ami_base_os"
EDH_FIELD_STACK_ROOT_SIZE = "ami_root_disk_size"
EDH_FIELD_STACK_OS_FAMILY = "os_family"
EDH_FIELD_STACK_VDI_PROFILE_ID = "virtual_desktop_profile_id"
EDH_FIELD_STACK_TARGET_NODE_PROFILE_ID = "target_node_profile_id"
EDH_FIELD_STACK_DESCRIPTION = "description"
# Why a registered stack has no id to assign to the launch project.
CATALOG_NOT_READ_BACK = "the catalog could not be read back"
STACK_ID_NOT_READ_BACK = "its EDH id could not be read back"

# EDH desktop records as virtual_desktops/list_all returns them.
EDH_FIELD_SESSION_UUID = EDH_SESSION_ID_FIELD
EDH_FIELD_SESSION_OWNER = EDH_SESSION_OWNER_FIELD
EDH_FIELD_SESSION_STATE = EDH_SESSION_STATE_FIELD
EDH_FIELD_SESSION_NAME = EDH_SESSION_NAME_FIELD
EDH_FIELD_SESSION_CREATED_ON = EDH_SESSION_CREATED_ON_FIELD
EDH_FIELD_SESSION_OS_FAMILY = "os_family"
EDH_FIELD_SESSION_INSTANCE_TYPE = "instance_type"
EDH_FIELD_SESSION_SOFTWARE_STACK = "software_stack"
# The software_stack label of a list_all record reads "<stack name> (id: <n>)".
_STACK_LABEL_ID = re.compile(r"\(id:\s*(\d+)\)\s*$")

# Stale views: a desktop EDH reports stopped whose row last changed at least idleMinutes ago (or that
# has no row to time against), and a row EDH no longer lists at all.
STALE_IDLE_MINUTES_DEFAULT = 120
STALE_REASON_STOPPED = "stopped"
STALE_REASON_MISSING = "missingInEdh"
STOPPED_STATES = frozenset({STATE_STOPPED, STATE_ORPHANED})

# A session-less launch younger than this may still be on the launch worker; deactivate waits for it.
CREATING_DEACTIVATE_GRACE_MINUTES = 15

# Health probe outcomes: the reason names what an operator has to look at without echoing EDH.
HEALTH_STATUS_OK = "ok"
HEALTH_STATUS_ERROR = "error"
HEALTH_REASON_CREDENTIAL_REJECTED = "credentialRejected"
HEALTH_REASON_UNREACHABLE = "unreachable"
HEALTH_REASON_REFUSED = "refused"
HEALTH_MESSAGES = {
    HEALTH_REASON_CREDENTIAL_REJECTED: "EDH rejected the deployment credential.",
    HEALTH_REASON_UNREACHABLE: "EDH is unavailable. Try again later.",
    HEALTH_REASON_REFUSED: "EDH refused the health probe.",
}

# Path identifiers (EDH software-stack ids, session uuids, bootstrap ids) as they appear in a route.
PATH_ID_PATTERN = re.compile(r'^[A-Za-z0-9._-]{1,128}$')

# The HTTP verb and EDH route behind each client wrapper, recorded on the edhProxy audit trail.
EDH_CALLS = {
    "list_all_desktops": ("GET", EDH_ROUTE_DESKTOPS_LIST_ALL),
    "stop_desktop": ("PUT", EDH_ROUTE_DESKTOPS_STOP),
    "delete_desktop": ("DELETE", EDH_ROUTE_DESKTOPS_DELETE),
    "assign_software_stack_to_project": ("PUT", EDH_ROUTE_PROJECTS),
}
# Wrappers whose route depends on the software-stack kind they are called with.
EDH_KIND_CALLS = {
    "list_software_stacks": ("GET", EDH_SOFTWARE_STACK_ROUTES),
    "register_software_stack": ("POST", EDH_SOFTWARE_STACK_ROUTES),
    "delete_software_stack": ("DELETE", EDH_SOFTWARE_STACK_ROUTES),
    "list_profiles": ("GET", EDH_PROFILE_ROUTES),
}

try:
    edh_session_table_name = get_table_name(AddonResourceKeys.EDH_SESSION_STORAGE_TABLE)
    edh_cluster_id = os.environ["EDH_CLUSTER_ID"]
except Exception as e:
    logger.exception("Failed loading resource names and configuration")
    raise e

edh_session_table = dynamodb.Table(edh_session_table_name)


# ---------------------------------------------------------------------------
# Caller identity and authorization
# ---------------------------------------------------------------------------
def _requesting_user_id():
    """The authenticated administrator the request is acting as."""
    return claims_and_roles['tokens'][0] if claims_and_roles.get('tokens') else None


def _caller_edh_uid():
    """The EDH principal of the administrator, recorded on the audit trail of cluster-scoped calls."""
    return edh_uid_for(_requesting_user_id())


def _enforcer():
    return CasbinEnforcer(claims_and_roles)


def _workstation_auth_object(image_id, os_family, instance_type):
    """The edhWorkstation enforcement dict: exactly the matrix fields, as strings."""
    return {
        "object__type": WORKSTATION_OBJECT_TYPE,
        "edhImageId": str(image_id if image_id is not None else ""),
        "edhOsFamily": str(os_family if os_family is not None else ""),
        "edhInstanceType": str(instance_type if instance_type is not None else ""),
    }


def _image_auth_object(image_id, os_family):
    """The edhImage enforcement dict: exactly the matrix fields, as strings."""
    return {
        "object__type": IMAGE_OBJECT_TYPE,
        "edhImageId": str(image_id if image_id is not None else ""),
        "edhOsFamily": str(os_family if os_family is not None else ""),
    }


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# EDH calls
# ---------------------------------------------------------------------------
def _edh_call_route(wrapper, args):
    """(method, route) of a client wrapper call, resolving kind-dependent routes from the kind."""
    if wrapper in EDH_CALLS:
        return EDH_CALLS[wrapper]
    method, routes = EDH_KIND_CALLS[wrapper]
    return method, routes[args[0]]


def _call_edh(event, client, wrapper, operation, edh_uid, /, *args, **kwargs):
    """One EDH call through a client wrapper, audited as edhProxy after it returns or raises.

    ``args``/``kwargs`` are the wrapper's own; ``edh_uid`` is the principal the audit record names.
    The leading parameters are positional-only so a wrapper keyword of the same name (the catalog
    writes' ``edh_uid=``) reaches the wrapper rather than colliding with the audit principal.
    Any exception the wrapper raises is recorded by its type name and re-raised: a typed EDH error
    for the caller to map, anything else for the handler's internal-error response.
    """
    edh_method, edh_route = _edh_call_route(wrapper, args)
    audit = {"operation": operation, "edhUid": edh_uid, "edhMethod": edh_method, "edhRoute": edh_route}
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


# ---------------------------------------------------------------------------
# Session table access
# ---------------------------------------------------------------------------
def _scan_to_exhaustion():
    """Every session row, paging on key PRESENCE."""
    rows = []
    scan_kwargs = {}
    while True:
        response = edh_session_table.scan(**scan_kwargs)
        rows.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    return rows


def _query_to_exhaustion(**query_kwargs):
    """Every item of a session-table query, paging on key PRESENCE."""
    rows = []
    while True:
        response = edh_session_table.query(**query_kwargs)
        rows.extend(response.get('Items', []))
        if 'LastEvaluatedKey' not in response:
            break
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    return rows


def _row_for_identifier(identifier):
    """The session row a {sessionId} route addresses: by EDH session id, or -- for a launch that never
    received one -- by its bootstrap id. A row that has a session id is reachable by it alone. The row is
    read consistently from the table, since the index lags the writes a concurrent start or launch makes
    and an action takes back the credential the row holds."""
    rows = _query_to_exhaustion(
        IndexName=SESSION_UUID_INDEX,
        KeyConditionExpression=Key('sessionId').eq(identifier),
    )
    if rows:
        return edh_session_table.get_item(Key={'bootstrapId': rows[0]['bootstrapId']},
                                          ConsistentRead=True).get('Item')
    row = edh_session_table.get_item(Key={'bootstrapId': identifier}, ConsistentRead=True).get('Item')
    if row and not row.get('sessionId'):
        return row
    return None


def _row_write(row, attributes):
    """The update_item arguments that SET ``attributes`` (plus updatedAt, added to them) on the row."""
    attributes['updatedAt'] = _utc_now()
    keys = list(attributes)
    return {
        'Key': {'bootstrapId': row['bootstrapId']},
        'UpdateExpression': "SET " + ", ".join(f"#a{i} = :v{i}" for i in range(len(keys))),
        'ExpressionAttributeNames': {f"#a{i}": key for i, key in enumerate(keys)},
        'ExpressionAttributeValues': {f":v{i}": attributes[key] for i, key in enumerate(keys)},
    }


def _update_row(row, **attributes):
    """Persist ``attributes`` (plus updatedAt) onto the row and mirror them into ``row``."""
    edh_session_table.update_item(**_row_write(row, attributes))
    row.update(attributes)
    return row


def _update_row_from_image(row, **attributes):
    """Persist ``attributes`` as _update_row does and bring ``row`` to the item the write left, so what
    follows acts on the credential the row holds now rather than on the one it was read with."""
    response = edh_session_table.update_item(**_row_write(row, attributes), ReturnValues='ALL_NEW')
    row.update(attributes)
    row.update(response.get('Attributes') or {})
    return row


def _revoke_session_credential(event, row, *, delete_secret):
    """Revoke the row's session credential. A failure is logged and left to the reconciler and to
    keyExpiresAt; it never changes the administrative action's own result. A DELETED row whose container
    went with the key holds no credential any more and is given the expiry the table's TTL removes it by."""
    if not (row.get('apiKeyId') or row.get('secretName')):
        return
    try:
        cleared = bootstrap.revoke_session(
            event, row, delete_secret=delete_secret, actor=_requesting_user_id())
    except Exception:
        logger.exception(f"Session credential revoke deferred for bootstrap {row['bootstrapId']}")
        return
    if delete_secret and row.get('state') == STATE_DELETED:
        cleared = {**cleared, EXPIRES_AT_FIELD: deleted_row_expiry()}
    _update_row(row, **cleared)


# ---------------------------------------------------------------------------
# Health and capacity
# ---------------------------------------------------------------------------
def _health_reason(err):
    if isinstance(err, EdhAuthError):
        return HEALTH_REASON_CREDENTIAL_REJECTED
    if isinstance(err, EdhUnavailableError):
        return HEALTH_REASON_UNREACHABLE
    return HEALTH_REASON_REFUSED


def get_health(event):
    """One cheap EDH read under the root key: 200 when it answers, 502 naming the reason otherwise."""
    started = time.monotonic()
    client = EdhClient(profile="api")
    try:
        _call_edh(event, client, "list_profiles", "adminHealth", _caller_edh_uid(), KIND_DCV)
    except EdhError as err:
        reason = _health_reason(err)
        logger.exception(f"EDH health probe failed: {type(err).__name__} ({reason})")
        return general_error(status_code=502, body={
            "status": HEALTH_STATUS_ERROR,
            "reason": reason,
            "edhClusterId": edh_cluster_id,
            "message": HEALTH_MESSAGES[reason],
        }, event=event)
    return success(body={
        "status": HEALTH_STATUS_OK,
        "edhClusterId": edh_cluster_id,
        "latencyMs": int((time.monotonic() - started) * 1000),
    })


def get_capacity(event):
    """The dcv profiles as EDH reports them plus an aggregate of every session on the cluster."""
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    profiles = _call_edh(event, client, "list_profiles", "adminCapacity", edh_uid, KIND_DCV)
    sessions = _call_edh(event, client, "list_all_desktops", "adminCapacity", edh_uid, edh_uid)

    by_state = {}
    owners = set()
    for record in sessions:
        if not isinstance(record, dict):
            by_state["unknown"] = by_state.get("unknown", 0) + 1
            continue
        state = str(record.get(EDH_FIELD_SESSION_STATE) or "unknown")
        by_state[state] = by_state.get(state, 0) + 1
        owner = record.get(EDH_FIELD_SESSION_OWNER)
        if owner:
            owners.add(str(owner))

    return success(body={
        "profiles": profiles,
        "totalSessions": len(sessions),
        "byState": by_state,
        "distinctOwners": len(owners),
    })


# ---------------------------------------------------------------------------
# Lease settings routes
# ---------------------------------------------------------------------------
def get_lease_settings(event):
    """The stored lease policy, or the default when no settings row exists yet."""
    return success(body={"lease": read_lease_policy()})


def update_lease_settings(event):
    """Merge the supplied fields onto the current policy and persist it."""
    body = _parse_body(event)
    request = parse(event=body, model=LeaseSettingsRequestModel)
    patch = {k: getattr(request, k) for k in ("enabled", "leaseHours", "forced", "graceHours")}
    try:
        updated = write_lease_policy(patch, _requesting_user_id())
    except LeaseValidationError as err:
        return validation_error(body={"message": str(err)}, event=event)
    log_actions(event, "edhLeasePolicyUpdate", {"lease": updated})
    return success(body={"lease": updated})


# ---------------------------------------------------------------------------
# Image routes
# ---------------------------------------------------------------------------
def _image_item(kind_label, record):
    """One merged catalog item from a dcv or target_nodes software-stack record."""
    is_vdi = kind_label == IMAGE_KIND_VDI
    stack_id = record.get(EDH_FIELD_STACK_ID)
    base_os = record.get(EDH_FIELD_STACK_BASE_OS) if is_vdi else None
    os_family = record.get(EDH_FIELD_STACK_OS_FAMILY)
    if not os_family:
        os_family = os_family_from_base_os(base_os) if is_vdi else OS_FAMILY_LINUX
    profile_key = EDH_FIELD_STACK_VDI_PROFILE_ID if is_vdi else EDH_FIELD_STACK_TARGET_NODE_PROFILE_ID
    return {
        "kind": kind_label,
        "imageId": str(stack_id) if stack_id is not None else "",
        "stackName": str(record.get(EDH_FIELD_STACK_NAME) or ""),
        "amiId": str(record.get(EDH_FIELD_STACK_AMI_ID) or ""),
        "baseOs": str(base_os or ""),
        "osFamily": str(os_family).lower(),
        "architecture": stack_architecture(record),
        "rootSize": record.get(EDH_FIELD_STACK_ROOT_SIZE),
        "profileId": record.get(profile_key),
        "description": str(record.get(EDH_FIELD_STACK_DESCRIPTION) or ""),
    }


def _stack_record_named(records, stack_name):
    """The software-stack record whose stack_name is ``stack_name``, or None."""
    return next((r for r in records
                 if isinstance(r, dict) and str(r.get(EDH_FIELD_STACK_NAME)) == stack_name), None)


def list_images(event):
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    items = []
    for kind_label, kind in ((IMAGE_KIND_VDI, KIND_DCV), (IMAGE_KIND_SIMULATION, KIND_TARGET_NODES)):
        records = _call_edh(event, client, "list_software_stacks", "adminImagesList", edh_uid, kind)
        items.extend(_image_item(kind_label, record) for record in records if isinstance(record, dict))

    enforcer = _enforcer() if len(claims_and_roles["tokens"]) > 0 else None
    allowed = []
    for item in items:
        # Appends only on an allow, so an absent enforcer yields an empty catalog, never an open one.
        if enforcer and enforcer.enforce(_image_auth_object(item["imageId"], item["osFamily"]), "GET"):
            allowed.append(item)
    return success(body={'Items': allowed})


def register_image(event, kind_label):
    body = _parse_body(event)
    if kind_label == IMAGE_KIND_VDI:
        request = parse(event=body, model=RegisterVdiImageRequestModel)
        fields = vdi_stack_fields(request)
        os_family = os_family_from_base_os(request.baseOs)
    else:
        request = parse(event=body, model=RegisterSimulationImageRequestModel)
        fields = simulation_stack_fields(request)
        os_family = OS_FAMILY_LINUX

    # Tier-2 from the validated body: the EDH id does not exist yet, so the stack name stands in.
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforcer().enforce(_image_auth_object(request.stackName, os_family), "POST"):
        return authorization_error()

    kind = KIND_BY_IMAGE_KIND[kind_label]
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")

    # Idempotent register: before registering, check if the stack already exists. If a stack of
    # the same name with the same AMI is found, adopt it; if the AMI differs, refuse. A stranded first
    # request leaves the stack unassigned; the retry finds it by name, adopts, assigns, and succeeds.
    try:
        existing_records = _call_edh(event, client, "list_software_stacks", "adminImageRegister", edh_uid, kind)
    except EdhError as err:
        logger.exception(f"Catalog could not be read before registering: {type(err).__name__}")
        existing_records = []
    existing = _stack_record_named(existing_records, request.stackName)
    if existing is not None:
        existing_ami = str(existing.get(EDH_FIELD_STACK_AMI_ID, ""))
        if existing_ami == request.amiId:
            logger.info(f"Stack {request.stackName} already exists with AMI {request.amiId}; adopting")
            created = existing
            created_id = created.get(EDH_FIELD_STACK_ID)
        else:
            logger.warning(f"Stack {request.stackName} exists with different AMI {existing_ami}")
            return validation_error(body={'message': "A stack with this name already exists with a different AMI"},
                                    event=event)
    else:
        # No existing stack found; register a new one.
        try:
            _call_edh(event, client, "register_software_stack", "adminImageRegister", edh_uid, kind, fields,
                      edh_uid=edh_uid)
        except EdhApiError as err:
            # Duplicate-name errors pass through here when two requests race: both read the catalog,
            # both see nothing, but only one's register succeeds. The loser re-reads and adopts. EDH's
            # wording is in the refusal's body, not in the error's own message.
            refusal = f"{err} {err.body_text}".lower()
            if "already exists" in refusal or "duplicate" in refusal:
                logger.info(f"Stack {request.stackName} registration refused (duplicate); re-reading catalog")
                try:
                    retry_records = _call_edh(event, client, "list_software_stacks", "adminImageRegister",
                                              edh_uid, kind)
                except EdhError:
                    logger.exception("Catalog could not be re-read after duplicate-name refusal")
                    raise err
                retry_existing = _stack_record_named(retry_records, request.stackName)
                if retry_existing is None:
                    logger.warning("Duplicate-name refusal but the catalog does not list the stack")
                    raise err
                retry_ami = str(retry_existing.get(EDH_FIELD_STACK_AMI_ID, ""))
                if retry_ami != request.amiId:
                    logger.warning(f"Duplicate-name refusal and AMI differs ({retry_ami} != {request.amiId})")
                    raise err
                logger.info(f"Adopted {request.stackName} with matching AMI {request.amiId}")
                created = retry_existing
                created_id = created.get(EDH_FIELD_STACK_ID)
            else:
                # Other EdhApiError types (quota, permission, validation) are not retried.
                raise
        except EdhError:
            # Non-ApiError types (Unavailable, OutcomeUnknown) propagate as-is.
            raise
        else:
            # Register succeeded; read the id back from the catalog.
            try:
                created_records = _call_edh(event, client, "list_software_stacks", "adminImageRegister",
                                            edh_uid, kind)
            except EdhError as err:
                logger.exception(f"Software stack registered but the catalog was not read back: {type(err).__name__}")
                status_code, _ = edh_error_response_parts(err)
                return general_error(status_code=status_code,
                                     body={"message": project_assign_error(CATALOG_NOT_READ_BACK)}, event=event)
            created = _stack_record_named(created_records, request.stackName)
            created_id = created.get(EDH_FIELD_STACK_ID) if created else None
            if created_id is None:
                logger.warning("Software stack registered but the catalog does not list it")
                return general_error(status_code=502, body={"message": project_assign_error(STACK_ID_NOT_READ_BACK)},
                                     event=event)

    # EDH resolves a user's launchable stacks through the projects a stack is attached to.
    try:
        ensure_stack_in_project(client, kind, str(created_id), invoke=partial(
            _call_edh, event, client, "assign_software_stack_to_project", "adminImageRegister",
            edh_uid, edh_uid=edh_uid))
    except EdhError as err:
        # The stack exists in EDH but no project carries it -- or, when EDH did not answer, one may; the
        # answer names that state.
        logger.exception(f"Software stack registered but not attached to the project: {type(err).__name__}")
        status_code, _ = edh_error_response_parts(err)
        return general_error(status_code=status_code,
                             body={"message": project_assign_error(err), **outcome_unknown_fields(err)}, event=event)
    return success(status_code=201, body={
        "kind": kind_label,
        "imageId": str(created_id),
        "stackName": request.stackName,
        "amiId": request.amiId,
        "osFamily": os_family,
    })


def delete_image(event, kind_label):
    image_id = _path_id(event, 'imageId')
    if image_id is None:
        return validation_error(body={'message': "imageId is invalid"}, event=event)

    kind = KIND_BY_IMAGE_KIND[kind_label]
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    records = _call_edh(event, client, "list_software_stacks", "adminImageDelete", edh_uid, kind)
    record = next((r for r in records
                   if isinstance(r, dict) and str(r.get(EDH_FIELD_STACK_ID)) == image_id), None)
    if record is None:
        return general_error(status_code=404, body={'message': "Image not found"}, event=event)

    item = _image_item(kind_label, record)
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    if not _enforcer().enforce(_image_auth_object(item["imageId"], item["osFamily"]), "DELETE"):
        return authorization_error()

    _call_edh(event, client, "delete_software_stack", "adminImageDelete", edh_uid, kind, image_id,
              edh_uid=edh_uid)
    return success(body={"kind": kind_label, "imageId": image_id, "deleted": True})


# ---------------------------------------------------------------------------
# Instance routes
# ---------------------------------------------------------------------------
def _stack_id_from_label(label):
    """The EDH software-stack id inside a list_all record's "<name> (id: <n>)" label, or None."""
    match = _STACK_LABEL_ID.search(str(label or ""))
    return match.group(1) if match else None


def _instance_item(record, row):
    """One admin instance view: EDH's list_all record joined with the VAMS session row."""
    record = record if isinstance(record, dict) else {}
    row = row or {}

    def record_str(key):
        value = record.get(key)
        return str(value) if value is not None else None

    edh_state = record_str(EDH_FIELD_SESSION_STATE)
    image_id = row.get('imageId') or _stack_id_from_label(record.get(EDH_FIELD_SESSION_SOFTWARE_STACK))
    return {
        "sessionId": record_str(EDH_FIELD_SESSION_UUID) or row.get('sessionId'),
        "edhState": edh_state,
        "owner": record_str(EDH_FIELD_SESSION_OWNER) or row.get('edhUid'),
        "name": row.get('name') or record_str(EDH_FIELD_SESSION_NAME),
        "createdAt": record_str(EDH_FIELD_SESSION_CREATED_ON) or row.get('createdAt'),
        "updatedAt": row.get('updatedAt'),
        "userId": row.get('userId'),
        "bootstrapId": row.get('bootstrapId'),
        "state": state_for_edh_session_state(edh_state, row.get('state')),
        "lastError": row.get('lastError'),
        "imageId": str(image_id) if image_id is not None else None,
        "osFamily": row.get('osFamily') or record_str(EDH_FIELD_SESSION_OS_FAMILY),
        "instanceType": row.get('instanceType') or record_str(EDH_FIELD_SESSION_INSTANCE_TYPE),
    }


def _row_index(rows):
    """Live session rows keyed for the join: by session id, and -- for launches that never received
    one -- by the (owner, session name) pair EDH stores for them."""
    by_session, by_launch = {}, {}
    for row in rows:
        if not isinstance(row, dict) or row.get('state') == STATE_DELETED:
            continue
        if row.get('sessionId'):
            by_session[str(row['sessionId'])] = row
        elif row.get('bootstrapId'):
            try:
                by_launch[(row.get('edhUid'), edh_session_name(row['bootstrapId']))] = row
            except ValueError:
                continue
    return by_session, by_launch


def _row_for_record(record, by_session, by_launch):
    session_id = record.get(EDH_FIELD_SESSION_UUID)
    if session_id is not None and str(session_id) in by_session:
        return by_session[str(session_id)]
    return by_launch.get((record.get(EDH_FIELD_SESSION_OWNER), record.get(EDH_FIELD_SESSION_NAME)))


def _joined_instances(event, client, operation):
    """(one item per EDH session, the rows with a session id EDH no longer lists)."""
    admin_uid = _caller_edh_uid()
    records = _call_edh(event, client, "list_all_desktops", operation, admin_uid, admin_uid)
    by_session, by_launch = _row_index(_scan_to_exhaustion())

    items, matched = [], set()
    for record in records:
        if not isinstance(record, dict):
            continue
        row = _row_for_record(record, by_session, by_launch)
        if row is not None:
            matched.add(row['bootstrapId'])
        items.append(_instance_item(record, row))
    missing = [row for row in by_session.values() if row['bootstrapId'] not in matched]
    return items, missing


def _filter_allowed_instances(items):
    enforcer = _enforcer() if len(claims_and_roles["tokens"]) > 0 else None
    allowed = []
    for item in items:
        auth_object = _workstation_auth_object(item["imageId"], item["osFamily"], item["instanceType"])
        # Appends only on an allow, so an absent enforcer yields an empty list, never an open one.
        if enforcer and enforcer.enforce(auth_object, "GET"):
            allowed.append(item)
    return allowed


def list_instances(event):
    client = EdhClient(profile="api")
    items, _missing = _joined_instances(event, client, "adminInstancesList")
    return success(body={'Items': _filter_allowed_instances(items)})


def _parse_timestamp(value):
    """An ISO-8601 text or epoch number as an aware datetime; None for anything else."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _past_grace(row):
    """Whether a session-less launch is old enough that no worker can still be on it."""
    updated_at = _parse_timestamp(row.get('updatedAt'))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=CREATING_DEACTIVATE_GRACE_MINUTES)
    return updated_at is None or updated_at < cutoff


def list_stale_instances(event):
    query_params = event.get('queryStringParameters') or {}
    raw_minutes = query_params.get('idleMinutes', str(STALE_IDLE_MINUTES_DEFAULT))
    try:
        idle_minutes = int(raw_minutes)
    except (TypeError, ValueError):
        return validation_error(body={'message': "idleMinutes must be an integer"}, event=event)
    if idle_minutes < 1:
        return validation_error(body={'message': "idleMinutes must be at least 1"}, event=event)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)
    client = EdhClient(profile="api")
    items, missing_rows = _joined_instances(event, client, "adminInstancesStale")

    stale = []
    for item in items:
        if item["state"] not in STOPPED_STATES:
            continue
        # The row's last change stands in for EDH's state-change time, which list_all does not carry;
        # a stopped desktop with no row has nothing to wait on and is listed at once.
        changed_at = _parse_timestamp(item.get("updatedAt"))
        if changed_at is None or changed_at < cutoff:
            stale.append({**item, "stale": True, "staleReason": STALE_REASON_STOPPED})
    for row in missing_rows:
        stale.append({**_instance_item(None, row), "stale": True, "staleReason": STALE_REASON_MISSING})

    return success(body={'Items': _filter_allowed_instances(stale), 'idleMinutes': idle_minutes})


def act_on_instance(event, action):
    """Stop (EDH stop under the owner, row STOPPED) or deactivate (EDH delete under the service
    actor, row DELETED) one session, addressed by its EDH session id or -- for a launch that never
    received one -- by its bootstrap id, and take its session credential back: the key is revoked and
    the secret neutralised at once, the container removed with a deactivated desktop."""
    identifier = _path_id(event, 'sessionId')
    if identifier is None:
        return validation_error(body={'message': "sessionId is invalid"}, event=event)
    operation = "adminInstanceStop" if action == "stop" else "adminInstanceDeactivate"

    row = _row_for_identifier(identifier)
    admin_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    records = _call_edh(event, client, "list_all_desktops", operation, admin_uid, admin_uid)
    by_session, by_launch = _row_index([row] if row else [])
    record = None
    for candidate in records:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get(EDH_FIELD_SESSION_UUID)) == identifier or (
                row is not None and _row_for_record(candidate, by_session, by_launch) is row):
            record = candidate
            break
    if record is None and row is None:
        return general_error(status_code=404, body={'message': "Session not found"}, event=event)

    item = _instance_item(record, row)
    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error()
    auth_object = _workstation_auth_object(item["imageId"], item["osFamily"], item["instanceType"])
    if not _enforcer().enforce(auth_object, "POST"):
        return authorization_error()

    session_id = item["sessionId"]
    if action == "stop":
        if record is None or not session_id:
            return general_error(status_code=409, body={'message': "Session is not running in EDH"}, event=event)
        # EDH filters stop on the session owner for every caller, the root key included.
        _call_edh(event, client, "stop_desktop", operation, item["owner"], item["owner"], session_id)
        new_state = STATE_STOPPED
    else:
        if record is not None:
            # The root key bypasses the owner filter on delete; the service actor is the deactivator.
            _call_edh(event, client, "delete_desktop", operation, EDH_SERVICE_ACTOR_UID,
                      EDH_SERVICE_ACTOR_UID, session_id)
        elif row is not None and row.get('state') == STATE_CREATING and not _past_grace(row):
            return general_error(status_code=409,
                                 body={'message': "Workstation is still being created"}, event=event)
        new_state = STATE_DELETED

    if row is not None:
        # A desktop that is stopped or gone cannot be connected to; the flag moves with the state. A start
        # the row holds in progress is over: EDH stops a started desktop, and a deleted one cannot start.
        written = {'state': new_state, 'connectionAvailable': False, **start_stamp_cleared(row)}
        if new_state == STATE_DELETED and not (row.get('apiKeyId') or row.get('secretName')):
            # No credential to take back: the row is left to the table's TTL at once.
            written[EXPIRES_AT_FIELD] = deleted_row_expiry()
        # The revoke follows the row as the write left it: a key a renew named since the read goes too.
        _update_row_from_image(row, **written)
        _revoke_session_credential(event, row, delete_secret=(new_state == STATE_DELETED))
    return success(body={**_instance_item(record, row), "state": new_state})


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def handle_get(event, path):
    if API_EDH_ADMIN_HEALTH.matches(path):
        return get_health(event)
    if API_EDH_ADMIN_CAPACITY.matches(path):
        return get_capacity(event)
    if API_EDH_ADMIN_SETTINGS.matches(path):
        return get_lease_settings(event)
    if API_EDH_ADMIN_IMAGES.matches(path):
        return list_images(event)
    if API_EDH_ADMIN_INSTANCES_STALE.matches(path):
        return list_stale_instances(event)
    if API_EDH_ADMIN_INSTANCES.matches(path):
        return list_instances(event)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_post(event, path):
    if API_EDH_ADMIN_IMAGES_VDI.matches(path):
        return register_image(event, IMAGE_KIND_VDI)
    if API_EDH_ADMIN_IMAGES_SIMULATION.matches(path):
        return register_image(event, IMAGE_KIND_SIMULATION)
    if API_EDH_ADMIN_INSTANCE_STOP.matches(path):
        return act_on_instance(event, "stop")
    if API_EDH_ADMIN_INSTANCE_DEACTIVATE.matches(path):
        return act_on_instance(event, "deactivate")
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_delete(event, path):
    if API_EDH_ADMIN_IMAGE_VDI.matches(path):
        return delete_image(event, IMAGE_KIND_VDI)
    if API_EDH_ADMIN_IMAGE_SIMULATION.matches(path):
        return delete_image(event, IMAGE_KIND_SIMULATION)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_put(event, path):
    if API_EDH_ADMIN_SETTINGS.matches(path):
        return update_lease_settings(event)
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
            return authorization_error()

        # The system identity is never an EDH principal.
        if claims_and_roles["tokens"][0] == SYSTEM_USER_ID:
            return authorization_error()

        if method == 'GET':
            return handle_get(event, path)
        elif method == 'POST':
            return handle_post(event, path)
        elif method == 'PUT':
            return handle_put(event, path)
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
