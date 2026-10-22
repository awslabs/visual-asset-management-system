# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""EDH workstation and catalog routes for VAMS users.

Serves ``/addon/edh/workstations*`` and ``/addon/edh/catalog/*``. A workstation is an EDH DCV
desktop launched on the caller's behalf under the EDH root key, with the caller relayed as an
opaque ``edhUid``. A launch names an image of EDH's dcv catalog, whose OS family is the catalog's
(the body's value is checked against it, never trusted). Every launch is recorded in the session
table before the launch worker is handed the EDH calls: the row is the idempotency key for a repeated
create, the owner record every ``{sessionId}`` route is checked against, and the state the list route
overlays EDH's live view onto.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.apiRoutes import (
    API_EDH_CATALOG_IMAGES,
    API_EDH_CATALOG_INSTANCE_TYPES,
    API_EDH_CATALOG_LEASE,
    API_EDH_WORKSTATION,
    API_EDH_WORKSTATION_CONNECT,
    API_EDH_WORKSTATION_RENEW,
    API_EDH_WORKSTATION_START,
    API_EDH_WORKSTATION_STOP,
    API_EDH_WORKSTATION_WINDOWS_SIGN_IN,
    API_EDH_WORKSTATIONS,
)
from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import get_table_name
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import AUTH_METHOD_API_KEY, request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_actions, log_authorization
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse, validation_error_message,
)
from models.addon.edh import (
    ARCHITECTURE_FAMILIES,
    CreateWorkstationRequestModel, WorkstationRecordModel, ConnectResponseModel, WindowsSignInResponseModel,
)
from .common.edhClient import (
    EDH_PROFILE_ROUTES,
    EDH_ROUTE_DESKTOPS_DELETE,
    EDH_ROUTE_DESKTOPS_LIST,
    EDH_ROUTE_DESKTOPS_SESSION_STATE,
    EDH_ROUTE_DESKTOPS_START,
    EDH_ROUTE_DESKTOPS_STOP,
    EDH_SESSION_ID_FIELD,
    EDH_SESSION_OWNER_FIELD,
    EDH_SOFTWARE_STACK_ID_FIELD,
    EDH_SOFTWARE_STACK_ROUTES,
    START_IN_PROGRESS_FIELD,
    EdhClient, EdhError, EdhNotFoundError, EdhOutcomeUnknownError,
    map_edh_error_to_response,
)
from .common.edhCatalogFields import (
    EDH_STACK_RECORD_FIELD_ARCHITECTURE, IMAGE_KIND_VDI, OS_FAMILY_WINDOWS, os_family_from_base_os,
    stack_architecture,
)
from .common.edhIdentity import SYSTEM_USER_ID, edh_uid_for
from .common import edhAmiRecipeStore as recipe_store
from .common import edhSessionBootstrap as bootstrap
from .common.edhLeasePolicy import (
    LeaseValidationError, compute_expiry, parse_iso, read_lease_policy, resolve_launch_lease,
)
from .common.edhSessionState import (
    CREDENTIAL_CLAIM_SECONDS,
    EDH_CONNECTION_STRING_FIELD,
    EDH_SESSION_STATE_FIELD,
    EDH_SESSION_STATE_PENDING,
    EDH_SESSION_STATE_STOPPED,
    EXPIRES_AT_FIELD,
    LAST_ERROR_LAUNCH_ERROR,
    LAST_ERROR_LEASE_EXPIRED,
    START_IN_PROGRESS_SECONDS, STATE_ACTIVE, STATE_CREATING, STATE_DELETED, STATE_ORPHANED, STATE_STOPPED,
    WORKER_PAYLOAD_KEYS,
    credential_claim_stamp, deleted_row_expiry, observed_row_attributes, session_row_live, start_in_progress,
    start_stamp, start_stamp_cleared, state_for_edh_session_state,
)

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
dynamodb = boto3.resource('dynamodb', config=retry_config)
lambda_client = boto3.client('lambda', config=retry_config)
logger = safeLogger(service_name="EdhWorkstationService")

claims_and_roles = {}
# The monotonic instant the current request's Lambda times out at; None when the runtime reports none.
request_deadline = None

# Casbin object types enforced by this handler.
WORKSTATION_OBJECT_TYPE = "edhWorkstation"
IMAGE_OBJECT_TYPE = "edhImage"

# Session table indexes.
SESSION_UUID_INDEX = "sessionUuidIndex"
USER_ID_INDEX = "userIdIndex"

# A CREATING row without a session whose last write is older than this is out of the launch
# worker's hands (its asynchronous retries have expired) and may be deleted without EDH.
CREATING_DELETE_GRACE_MINUTES = 15

# EDH response fields read from desktop, software-stack and profile records. The desktop fields are
# the client's and the shared session-state module's constants, and the stack architecture is the
# field edhCatalogFields' record reader reads, which the admin image listing shares; the other stack
# and the profile fields are read by this handler only.
EDH_FIELD_SESSION_UUID = EDH_SESSION_ID_FIELD
EDH_FIELD_SESSION_STATE = EDH_SESSION_STATE_FIELD
EDH_FIELD_SESSION_OWNER = EDH_SESSION_OWNER_FIELD
EDH_FIELD_CONNECTION_STRING = EDH_CONNECTION_STRING_FIELD
EDH_FIELD_STACK_ID = EDH_SOFTWARE_STACK_ID_FIELD
EDH_FIELD_STACK_NAME = "stack_name"
EDH_FIELD_STACK_BASE_OS = "ami_base_os"
EDH_FIELD_STACK_OS_FAMILY = "os_family"
EDH_FIELD_STACK_ROOT_SIZE = "ami_root_disk_size"
EDH_FIELD_STACK_DESCRIPTION = "description"
EDH_FIELD_STACK_ARCHITECTURE = EDH_STACK_RECORD_FIELD_ARCHITECTURE
EDH_FIELD_STACK_PROFILE = "profile"
EDH_FIELD_PROFILE_ID = "id"
EDH_FIELD_PROFILE_INSTANCE_TYPES = "allowed_instance_types"

# A catalog image an AVAILABLE AMI recipe build registered, and every other image, in list order.
CATEGORY_AMI_RECIPE = "amiRecipe"
CATEGORY_BASELINE = "baseline"
CATEGORY_ORDER = (CATEGORY_AMI_RECIPE, CATEGORY_BASELINE)

# The Windows sign-in of a desktop as EDH lists it: the account's password, a credential this handler alone
# reads, and the query field of the connection string that names the account.
EDH_FIELD_LOCAL_ADMIN_PASSWORD = "session_local_admin_password"
WINDOWS_USERNAME_QUERY_FIELD = "username"

# The HTTP verb and EDH route behind each client wrapper this Lambda calls, recorded on the edhProxy
# audit trail. The routes are the client's constants; the verbs mirror the wrapper bodies. The
# launch's EDH calls are made and labelled by the launch worker.
EDH_AUDIT_ROUTES = {
    "get_desktop_states": ("GET", EDH_ROUTE_DESKTOPS_SESSION_STATE),
    "get_desktop_connection": ("GET", EDH_ROUTE_DESKTOPS_LIST),
    "stop_desktop": ("PUT", EDH_ROUTE_DESKTOPS_STOP),
    "start_desktop": ("PUT", EDH_ROUTE_DESKTOPS_START),
    "delete_desktop": ("DELETE", EDH_ROUTE_DESKTOPS_DELETE),
    "list_software_stacks": ("GET", EDH_SOFTWARE_STACK_ROUTES["dcv"]),
    "list_profiles": ("GET", EDH_PROFILE_ROUTES["dcv"]),
}
# Launch, start and renew response field naming the session-bootstrap outcome beside the record.
RESPONSE_FIELD_SESSION_BOOTSTRAP = "sessionBootstrap"

# What a mint-carrying route still does once the session mint returns, and so what the mint may not spend
# of the Lambda's remaining time: the launch hand-over, the row write and the response (create); the EDH
# start -- at least the api profile's connect and read pair, and at most what the EDH request budget
# leaves, which the read of the desktop's state after a lost answer shares -- the secret binding, the row
# write and the response (start; the key cleanup ahead of the response takes only what is left); the row
# settle, the secret write, the claim release and the response (renew).
MINT_RESERVE_SECONDS_CREATE = 3.0
MINT_RESERVE_SECONDS_START = 14.0
MINT_RESERVE_SECONDS_RENEW = 3.0
# What a start still does once its key cleanup returns, the response, and a cold start's init, which API
# Gateway's timeout counts and the Lambda's deadline does not.
KEY_CLEANUP_RESERVE_SECONDS_START = 5.0


def _parse_allowed_instance_types(raw):
    """The deployment allow-list as a tuple of instance types; an empty array means unfiltered."""
    parsed = json.loads(raw)
    if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
        raise ValueError("EDH_ALLOWED_INSTANCE_TYPES must be a JSON array of instance type strings")
    return tuple(parsed)


try:
    edh_session_table_name = get_table_name(AddonResourceKeys.EDH_SESSION_STORAGE_TABLE)
    allowed_instance_types = _parse_allowed_instance_types(os.environ["EDH_ALLOWED_INSTANCE_TYPES"])
    vdi_profile_id = int(os.environ["EDH_VDI_PROFILE_ID"])
    launch_worker_function_name = os.environ["EDH_LAUNCH_WORKER_FUNCTION_NAME"]
    ami_builds_enabled = os.environ["EDH_AMI_BUILDS_ENABLED"] == "true"
except Exception as e:
    logger.exception("Failed loading resource names and configuration")
    raise e

edh_session_table = dynamodb.Table(edh_session_table_name)


# ---------------------------------------------------------------------------
# Caller identity and authorization
# ---------------------------------------------------------------------------
def _requesting_user_id():
    """The authenticated user the request is acting as."""
    return claims_and_roles['tokens'][0] if claims_and_roles.get('tokens') else None


def _caller_edh_uid():
    """The EDH principal for the caller, derived from the token and never from the request."""
    return edh_uid_for(_requesting_user_id())


def _enforcer():
    return CasbinEnforcer(claims_and_roles)


def _workstation_auth_object(image_id, os_family, instance_type):
    """The edhWorkstation enforcement dict: exactly the matrix fields, as strings."""
    return {
        "object__type": WORKSTATION_OBJECT_TYPE,
        "edhImageId": str(image_id),
        "edhOsFamily": str(os_family),
        "edhInstanceType": str(instance_type),
    }


def _image_auth_object(image_id, os_family):
    """The edhImage enforcement dict: exactly the matrix fields, as strings."""
    return {
        "object__type": IMAGE_OBJECT_TYPE,
        "edhImageId": str(image_id),
        "edhOsFamily": str(os_family),
    }


def _stack_os_family(stack):
    """The OS family of a dcv software stack: EDH's own classification when the record carries one,
    else the family its base OS implies, else None."""
    os_family = stack.get(EDH_FIELD_STACK_OS_FAMILY)
    if os_family:
        return str(os_family).lower()
    base_os = stack.get(EDH_FIELD_STACK_BASE_OS)
    return os_family_from_base_os(base_os) if base_os else None


def _stack_root_size(stack):
    """The root disk size, in GB, EDH registered for a dcv software stack when it is a positive whole number,
    else None: a launch is never held to a size the record does not state plainly."""
    size = stack.get(EDH_FIELD_STACK_ROOT_SIZE)
    if isinstance(size, bool):
        return None
    if isinstance(size, str):
        size = size.strip()
        size = int(size) if size.isdecimal() else None
    elif isinstance(size, float):
        size = int(size) if size.is_integer() else None
    elif not isinstance(size, int):
        return None
    return size if size is not None and size > 0 else None


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _request_deadline(context):
    """The monotonic instant the Lambda times out at, from the runtime context; None when it reports none."""
    remaining = getattr(context, "get_remaining_time_in_millis", None)
    if not callable(remaining):
        return None
    try:
        millis = remaining()
    except Exception:
        return None
    if isinstance(millis, bool) or not isinstance(millis, (int, float)):
        return None
    return time.monotonic() + millis / 1000.0


def _bootstrap_deadline(reserve_seconds):
    """The instant a session-bootstrap step -- the mint, or the start's key cleanup -- must be done by on
    this request: the Lambda's deadline less what the route still does after the step. None hands the
    bootstrap module its own budget."""
    return None if request_deadline is None else request_deadline - reserve_seconds


# ---------------------------------------------------------------------------
# EDH calls
# ---------------------------------------------------------------------------
def _call_edh(event, client, wrapper, operation, edh_uid, *args):
    """One EDH call through a client wrapper, audited as edhProxy after it returns or raises.

    ``args`` are the wrapper's own arguments; ``edh_uid`` is the principal recorded on the audit
    record. Any exception the wrapper raises is recorded by its type name and re-raised: a typed EDH
    error for the caller to map, anything else for the handler's internal-error response.
    """
    edh_method, edh_route = EDH_AUDIT_ROUTES[wrapper]
    audit = {
        "operation": operation,
        "edhUid": edh_uid,
        "edhMethod": edh_method,
        "edhRoute": edh_route,
    }
    call = getattr(client, wrapper)
    try:
        result = call(*args)
    except Exception as err:
        log_actions(event, "edhProxy", {**audit, "edhStatus": type(err).__name__})
        raise
    log_actions(event, "edhProxy", {**audit, "edhStatus": "ok"})
    return result


# ---------------------------------------------------------------------------
# Session table access
# ---------------------------------------------------------------------------
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


def _rows_for_user(user_id):
    return _query_to_exhaustion(
        IndexName=USER_ID_INDEX,
        KeyConditionExpression=Key('userId').eq(user_id),
    )


def _row_for_identifier(identifier):
    """The session row a {sessionId} route addresses: by EDH session id, or -- for a launch that never
    received one -- by the bootstrap id itself. A row that has a session id is reachable by it alone. The
    row is read consistently from the table, since the index lags the writes a concurrent start or launch
    makes and a stop or delete takes back the credential the row holds."""
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


def _stored_row(row):
    """The row as the table holds it now, read consistently; None once it is gone."""
    return edh_session_table.get_item(Key={'bootstrapId': row['bootstrapId']}, ConsistentRead=True).get('Item')


def _update_live_row(row, **attributes):
    """Persist ``attributes`` as _update_row does, provided the row still exists and is not DELETED, and
    answer whether they landed. A row deleted since it was read is terminal and is not written over."""
    write = _row_write(row, attributes)
    names = write['ExpressionAttributeNames']
    # The state the write sets, if it sets one, is named once: the condition reads it under the same name.
    state = next((placeholder for placeholder, key in names.items() if key == 'state'), '#state')
    names[state] = 'state'
    write['ConditionExpression'] = f"attribute_exists(bootstrapId) AND {state} <> :deleted"
    write['ExpressionAttributeValues'][':deleted'] = STATE_DELETED
    try:
        edh_session_table.update_item(**write)
    except ClientError as err:
        if err.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
        return False
    row.update(attributes)
    return True


def _record(row):
    """The API shape of a session row."""
    return WorkstationRecordModel(**row).dict()


def _natural_key(text):
    """A sort key that ignores case and compares each run of digits as a number, so g5.2xlarge sorts
    before g5.12xlarge."""
    parts = re.split(r"(\d+)", str(text or ""))
    return [int(part) if index % 2 else part.lower() for index, part in enumerate(parts)]


# ---------------------------------------------------------------------------
# Catalog routes
# ---------------------------------------------------------------------------
def _profile_instance_types(profiles):
    """The instance types of the deployment's virtual desktop profile mapped to their architecture groups, or None."""
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        if str(profile.get(EDH_FIELD_PROFILE_ID)) != str(vdi_profile_id):
            continue
        return _instance_type_architectures(profile.get(EDH_FIELD_PROFILE_INSTANCE_TYPES))
    return None


def _instance_type_architectures(raw):
    """Each instance type in a profile's allowed-types value, in its order, mapped to the architecture
    groups that list it; a value not keyed by architecture maps each type to no group.

    EDH serialises a profile's allowed types as a JSON string keyed by architecture
    (``{"x86_64": [...], "arm64": [...], "i386": [...]}``); a list, a decoded mapping and a
    comma-separated string are read as well.
    """
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except ValueError:
            decoded = raw
        raw = decoded.split(',') if isinstance(decoded, str) else decoded
    types = {}
    if isinstance(raw, dict):
        for architecture, arch_types in raw.items():
            if not isinstance(arch_types, list):
                continue
            for item in arch_types:
                if isinstance(item, str) and item.strip():
                    groups = types.setdefault(item.strip(), [])
                    if str(architecture) not in groups:
                        groups.append(str(architecture))
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                types.setdefault(item.strip(), [])
    return types


def list_instance_types(event):
    client = EdhClient(profile="api")
    profiles = _call_edh(event, client, "list_profiles", "catalogInstanceTypes", _caller_edh_uid(), "dcv")
    types = _profile_instance_types(profiles)
    if types is None:
        return general_error(status_code=502,
                             body={'message': 'EDH virtual desktop profile is unavailable'}, event=event)
    if allowed_instance_types:
        types = {item: groups for item, groups in types.items() if item in allowed_instance_types}
    return success(body={'Items': [{'instanceType': item, 'architectures': types[item]}
                                   for item in sorted(types, key=_natural_key)]})


def _image_item(stack):
    """The catalog shape of one dcv software stack."""
    return {
        "imageId": str(stack.get(EDH_FIELD_STACK_ID)),
        "name": stack.get(EDH_FIELD_STACK_NAME),
        "baseOs": stack.get(EDH_FIELD_STACK_BASE_OS),
        "osFamily": _stack_os_family(stack),
        "architecture": stack_architecture(stack),
        "rootSize": stack.get(EDH_FIELD_STACK_ROOT_SIZE),
        "description": stack.get(EDH_FIELD_STACK_DESCRIPTION),
    }


def _recipe_builds_by_stack():
    """The AVAILABLE desktop AMI recipe builds keyed by the EDH dcv stack id they registered; none when
    the recipe table is absent or cannot be read, so the catalog is still served, every image as a
    baseline. While AMI builds are off the Lambda holds no grant on the table, which is not read."""
    if not ami_builds_enabled:
        return {}
    try:
        return recipe_store.available_builds_by_stack_id(IMAGE_KIND_VDI)
    except Exception as err:
        code = err.response.get("Error", {}).get("Code") if isinstance(err, ClientError) else None
        logger.warning(f"AMI recipe builds not read; catalog images listed as baseline ({code or type(err).__name__})")
        return {}


def _categorized(item, recipe_build):
    """The catalog item with its category; a recipe image also names its recipe and build version."""
    if recipe_build is None:
        return {**item, "category": CATEGORY_BASELINE}
    recipe, build = recipe_build
    return {**item, "category": CATEGORY_AMI_RECIPE, "recipeId": recipe["recipeId"],
            "recipeDisplayName": recipe.get("displayName"), "recipeVersion": int(build["version"])}


def list_catalog_images(event):
    client = EdhClient(profile="api")
    stacks = _call_edh(event, client, "list_software_stacks", "catalogImages", _caller_edh_uid(), "dcv")
    enforcer = _enforcer() if len(claims_and_roles["tokens"]) > 0 else None
    items = []
    for stack in stacks:
        if not isinstance(stack, dict):
            continue
        item = _image_item(stack)
        if item["osFamily"] is None:
            # A stack whose family cannot be determined is not offered; it is never Linux by default.
            continue
        # Appends only on an allow, so an absent enforcer yields an empty catalog, never an open one.
        if enforcer and enforcer.enforce(_image_auth_object(item["imageId"], item["osFamily"]), "GET"):
            items.append(item)
    recipe_builds = _recipe_builds_by_stack() if items else {}
    items = [_categorized(item, recipe_builds.get(item["imageId"])) for item in items]
    # Recipe images first, then the baseline images, each group by name.
    items.sort(key=lambda item: (CATEGORY_ORDER.index(item["category"]), _natural_key(item["name"])))
    return success(body={'Items': items})


def get_catalog_lease(event):
    """The lease policy as a launch sees it: whether leases apply, the longest (or forced) lease in
    hours, whether it is forced, and the grace window. The policy's audit fields are admin-only."""
    policy = read_lease_policy()
    return success(body={'lease': {key: policy[key] for key in ('enabled', 'leaseHours', 'forced', 'graceHours')}})


# ---------------------------------------------------------------------------
# Workstation routes
# ---------------------------------------------------------------------------
def _desktop_for_row(row, desktops):
    """EDH's record for a session row, matched by session id; None for a row without one, which the
    launch worker or the session sweep completes."""
    session_id = row.get('sessionId')
    if not session_id:
        return None
    for desktop in desktops:
        if isinstance(desktop, dict) and str(desktop.get(EDH_FIELD_SESSION_UUID)) == str(session_id):
            return desktop
    return None


def _observe_desktop(row, desktop):
    """Persist what EDH's desktop record changes on the row, then bind the session secret to the
    instance the row now holds when it is not yet bound to it. A failed binding is logged and left
    to the reconciler. A row deleted since it was read keeps what its deletion wrote, and nothing is
    bound for it."""
    changed = observed_row_attributes(row, desktop)
    if changed and not _update_live_row(row, **changed):
        logger.info(f"Bootstrap {row['bootstrapId']} was deleted since it was read; EDH's view is not merged")
        return
    if row.get('secretName') and row.get('instanceId') and row.get('secretBoundInstanceId') != row['instanceId']:
        _bind_session_credential(row)


def _merge_edh_state(rows, desktops):
    """Overlay EDH's live view onto session rows and persist what changed, binding the session
    secret to the instance a row is first observed on; a row EDH does not report keeps its stored
    attributes."""
    for row in rows:
        desktop = _desktop_for_row(row, desktops)
        if desktop is None:
            continue
        _observe_desktop(row, desktop)
    return rows


def _desktop_states(event, client, rows, edh_uid, operation, unanswered=None):
    """EDH's state records for the rows that hold a session id, from the token-free session-state read;
    [] without a call when none does. ``unanswered`` collects the session ids EDH's answer left out."""
    session_ids = [row['sessionId'] for row in rows if row.get('sessionId')]
    if not session_ids:
        return []
    return _call_edh(event, client, "get_desktop_states", operation, edh_uid, edh_uid, session_ids, unanswered)


def _record_failed_launches(event, rows, unanswered):
    """Record ORPHANED, with its launch-error code, each CREATING row whose session id EDH's session-state
    read left out, and take its credential back. EDH answers only the desktops it holds active and
    deactivates a desktop whose placement failed within minutes; a launch that never ran cannot be held
    out by Save and Resume (a desktop EDH continued under another id is answered, so is not in
    ``unanswered``). A row deleted since it was read keeps what its deletion wrote; a revoke that fails is
    left to the session sweep."""
    for row in rows:
        if row.get('state') != STATE_CREATING or str(row.get('sessionId') or '') not in unanswered:
            continue
        if not _update_live_row(row, state=STATE_ORPHANED, connectionAvailable=False,
                                lastError=row.get('lastError') or LAST_ERROR_LAUNCH_ERROR):
            logger.info(f"Bootstrap {row['bootstrapId']} was deleted since it was read; it is not recorded failed")
            continue
        logger.warning(f"EDH no longer has the desktop of launch {row['bootstrapId']}; recorded as a failed launch")
        _revoke_session_credential(event, row, delete_secret=True)


def _desktop_after_lost_answer(event, client, row, edh_uid, operation, lost):
    """EDH's record of the row's desktop, from the read of the desktop's state after ``lost`` -- a desktop
    call whose answer never came -- and merged onto the row; None when EDH no longer reports it. A read of
    the desktop's state that fails leaves the call's outcome unknown, and ``lost`` is raised in its place."""
    identity = {"operation": operation, "bootstrapId": row['bootstrapId'], "sessionId": row.get('sessionId')}
    try:
        desktops = _desktop_states(event, client, [row], edh_uid, operation)
    except EdhError as err:
        logger.warning("EDH did not answer a desktop call and the desktop's state could not be read after it",
                       extra={**identity, "error": type(err).__name__})
        raise lost
    _merge_edh_state([row], desktops)
    desktop = _desktop_for_row(row, desktops)
    logger.warning("EDH did not answer a desktop call; the desktop as EDH reports it after the call",
                   extra={**identity, "edhState": desktop.get(EDH_FIELD_SESSION_STATE) if desktop else None})
    return desktop


def _listed_state(desktop):
    """The VAMS state EDH's record of a desktop maps to; None without a record or for a state outside the
    shared mapping."""
    if desktop is None:
        return None
    return state_for_edh_session_state(desktop.get(EDH_FIELD_SESSION_STATE), None)


def list_workstations(event):
    user_id = _requesting_user_id()
    rows = [row for row in _rows_for_user(user_id)
            if row.get('userId') == user_id and row.get('state') != STATE_DELETED]
    if any(row.get('sessionId') for row in rows):
        edh_uid = _caller_edh_uid()
        client = EdhClient(profile="api")
        unanswered = set()
        desktops = _desktop_states(event, client, rows, edh_uid, "workstationList", unanswered)
        _merge_edh_state(rows, desktops)
        _record_failed_launches(event, rows, unanswered)
    enforcer = _enforcer() if len(claims_and_roles["tokens"]) > 0 else None
    items = []
    for row in rows:
        auth_object = _workstation_auth_object(row.get('imageId'), row.get('osFamily'), row.get('instanceType'))
        # Appends only on an allow, so an absent enforcer yields an empty list, never an open one.
        if enforcer and enforcer.enforce(auth_object, "GET"):
            items.append(_record(row))
    return success(body={'Items': items})


def _worker_payload(row):
    """The launch worker's invocation payload: the row's identity."""
    return {key: row[key] for key in WORKER_PAYLOAD_KEYS}


def _dispatch_launch(row):
    """Hand the launch to the worker asynchronously; the row is the contract between the two."""
    lambda_client.invoke(
        FunctionName=launch_worker_function_name,
        InvocationType='Event',
        Payload=json.dumps(_worker_payload(row)).encode('utf-8'),
    )


def _revoke_session_credential(event, row, *, delete_secret, every_key=True):
    """Revoke the row's session credential; without ``every_key`` only the key the row names is deleted, not
    the other keys of its bootstrap. A failure is logged and left to the reconciler and to keyExpiresAt; it
    never changes the desktop operation's own result. A DELETED row whose container went with the key holds
    no credential any more and is given the expiry the table's TTL removes it by."""
    if not (row.get('apiKeyId') or row.get('secretName')):
        return
    try:
        cleared = bootstrap.revoke_session(
            event, row, delete_secret=delete_secret, actor=_requesting_user_id(), every_key=every_key)
    except Exception:
        logger.exception(f"Session credential revoke deferred for bootstrap {row['bootstrapId']}")
        return
    if delete_secret and row.get('state') == STATE_DELETED:
        cleared = {**cleared, EXPIRES_AT_FIELD: deleted_row_expiry()}
    _update_row(row, **cleared)


def _delete_other_session_keys(event, row, keep):
    """Delete every key of the row's bootstrap but ``keep``, within the Lambda's deadline less the start's
    cleanup floor: an apiKeyService invoke that no longer fits is not started. Skipped with session
    bootstrap off, where this Lambda holds no path to the API key service; a listing or delete that fails
    or is not started is logged and those keys are left to the next revoke and to their own expiry, and
    it never changes the start's own result."""
    if not bootstrap.is_enabled():
        return
    try:
        bootstrap.delete_other_session_keys(event, row, keep=keep, actor=_requesting_user_id(),
                                            deadline=_bootstrap_deadline(KEY_CLEANUP_RESERVE_SECONDS_START))
    except Exception:
        logger.exception(f"Session key cleanup deferred for bootstrap {row['bootstrapId']}")


def _record_deleted(event, row, **attributes):
    """Write DELETED (plus ``attributes``) onto the row and take back the credential the write left it
    holding. A row that holds no credential to begin with is given the expiry together with the state."""
    expiry = {} if (row.get('apiKeyId') or row.get('secretName')) else {EXPIRES_AT_FIELD: deleted_row_expiry()}
    _update_row_from_image(row, state=STATE_DELETED, **attributes, **expiry)
    _revoke_session_credential(event, row, delete_secret=True)


def _bind_session_credential(row):
    """Bind the row's session secret to the desktop's instance as last observed; False only when a binding
    was due and failed. A failure is logged and left to the reconciler; it never changes the desktop
    operation's own result."""
    if not (row.get('secretName') and row.get('instanceId')):
        return True
    try:
        bound = bootstrap.bind_session_secret(row, row['instanceId'])
        _update_row(row, **bound)
    except Exception:
        logger.exception(f"Session secret binding deferred for bootstrap {row['bootstrapId']}")
        return False
    return True


def _release_row(bootstrap_id):
    """Drop a CREATING row whose launch could not be handed over, so the same bootstrapId may be retried."""
    edh_session_table.delete_item(Key={'bootstrapId': bootstrap_id})


def _parse_body(event):
    """(the JSON object of the request body, None), or (None, the 400) for a body that is not one."""
    try:
        body = json.loads(event.get('body') or '{}')
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return None, validation_error(body={'message': 'Request body is not valid JSON'}, event=event)
    return body, None


def _catalog_stack(event, edh_uid, image_id):
    """EDH's dcv software stack with id ``image_id``, or None when the catalog has none."""
    stacks = _call_edh(event, EdhClient(profile="api"), "list_software_stacks", "catalogImages", edh_uid, "dcv")
    return next((stack for stack in stacks
                 if isinstance(stack, dict) and str(stack.get(EDH_FIELD_STACK_ID)) == image_id), None)


def _instance_type_runs_image(stack, instance_type):
    """False only when the image's own EDH profile lists ``instance_type`` solely under architectures
    of another family than the image's; True whenever either side is unknown, leaving EDH's own check
    at create (which also admits a type a VDI pool grants) to decide."""
    image_architecture = stack_architecture(stack)
    profile = stack.get(EDH_FIELD_STACK_PROFILE)
    if image_architecture is None or not isinstance(profile, dict):
        return True
    groups = _instance_type_architectures(profile.get(EDH_FIELD_PROFILE_INSTANCE_TYPES)).get(instance_type)
    if not groups:
        return True
    family = ARCHITECTURE_FAMILIES.get(image_architecture, image_architecture)
    return any(ARCHITECTURE_FAMILIES.get(group, group) == family for group in groups)


def create_workstation(event):
    if claims_and_roles.get('authMethod') == AUTH_METHOD_API_KEY:
        return authorization_error(
            body={'message': 'Workstation launch is not available to API key sessions'}, event=event)

    body, error = _parse_body(event)
    if error is not None:
        return error
    request = parse(event=body, model=CreateWorkstationRequestModel)

    if allowed_instance_types and request.instanceType not in allowed_instance_types:
        return validation_error(body={'message': 'instanceType is not permitted in this deployment'}, event=event)

    if len(claims_and_roles["tokens"]) == 0:
        return authorization_error(event=event)
    # The image's OS family is read from EDH's catalog before anything is written; the body's value is
    # checked against it, so the Tier-2 object and the row carry a family the caller did not choose.
    edh_uid = _caller_edh_uid()
    stack = _catalog_stack(event, edh_uid, request.imageId)
    if stack is None:
        return validation_error(body={'message': 'imageId is not a registered VDI image'}, event=event)
    os_family = _stack_os_family(stack)
    if os_family is None:
        return validation_error(body={'message': "The image's OS family cannot be determined"}, event=event)
    if request.osFamily != os_family:
        return validation_error(body={'message': 'osFamily does not match the image'}, event=event)
    auth_object = _workstation_auth_object(request.imageId, os_family, request.instanceType)
    if not _enforcer().enforce(auth_object, "POST"):
        return authorization_error(event=event)
    if not _instance_type_runs_image(stack, request.instanceType):
        return validation_error(
            body={'message': "instanceType does not match the image's architecture"}, event=event)
    # EDH raises an image's registered root size to its AMI's root volume, and a smaller disk cannot launch it.
    root_floor = _stack_root_size(stack)
    if root_floor is not None and request.rootSizeGb < root_floor:
        return validation_error(
            body={'message': f'Root disk size must be at least {root_floor} GB for this image'}, event=event)

    user_id = _requesting_user_id()
    now = _utc_now()
    try:
        lease_expires_at, lease_hours_at_launch = resolve_launch_lease(
            read_lease_policy(), request.leaseHours, datetime.fromisoformat(now))
    except LeaseValidationError as err:
        return validation_error(body={'message': str(err)}, event=event)
    row = {
        'bootstrapId': request.bootstrapId,
        'userId': user_id,
        'edhUid': edh_uid,
        'state': STATE_CREATING,
        'name': request.name,
        'imageId': request.imageId,
        'osFamily': os_family,
        'instanceType': request.instanceType,
        'rootSizeGb': request.rootSizeGb,
        'createdAt': now,
        'updatedAt': now,
        'connectionAvailable': False,
    }
    if lease_expires_at is not None:
        row['leaseExpiresAt'] = lease_expires_at
        row['leaseHoursAtLaunch'] = lease_hours_at_launch
    try:
        # A copy is written: the Item DynamoDB received is the CREATING snapshot, independent of ``row``.
        edh_session_table.put_item(Item=dict(row), ConditionExpression='attribute_not_exists(bootstrapId)')
    except ClientError as err:
        if err.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
        # The conditional put proved the row exists; the read must see it, so the owner's own retry is
        # answered with the record rather than refused.
        existing = edh_session_table.get_item(Key={'bootstrapId': request.bootstrapId},
                                              ConsistentRead=True).get('Item')
        if not existing or existing.get('userId') != user_id:
            log_authorization(claims_and_roles, False, {
                "action": "POST",
                "obj": {"object__type": WORKSTATION_OBJECT_TYPE, "bootstrapId": request.bootstrapId},
            })
            return general_error(status_code=409, body={'message': 'bootstrapId is already in use'}, event=event)
        return success(body=_record(existing))

    # The credential is placed before the launch is handed over, so the secret exists before the
    # desktop's first boot. The worker payload is the row's identity and never carries it.
    outcome, credential = bootstrap.bootstrap_session(
        event, row, claims_and_roles.get('mfaEnabled', False),
        deadline=_bootstrap_deadline(MINT_RESERVE_SECONDS_CREATE))
    if credential:
        _update_row(row, **credential)
    try:
        _dispatch_launch(row)
    except Exception:
        # Nothing reached EDH: the credential minted for this launch goes with the row.
        logger.exception("Launch worker invocation failed; releasing the row")
        _revoke_session_credential(event, row, delete_secret=True)
        _release_row(request.bootstrapId)
        raise
    return success(status_code=202, body={**_record(row), RESPONSE_FIELD_SESSION_BOOTSTRAP: outcome})


def _session_id_from(event, path):
    """The {sessionId} path parameter, or the path segment after 'workstations' when absent."""
    session_id = (event.get('pathParameters') or {}).get('sessionId')
    if not session_id:
        segments = path.split('/')
        session_id = segments[4] if len(segments) > 4 else None
    return session_id


def _session_id_or_error(event, path):
    session_id = _session_id_from(event, path)
    (valid, message) = validate({'sessionId': {'value': session_id, 'validator': 'ID'}})
    if not valid:
        return None, validation_error(body={'message': message}, event=event)
    return session_id, None


def _owned_row_or_error(event, session_id, method):
    """The caller's row for ``session_id``, or the non-disclosing not-found response.

    A row owned by someone else is answered exactly like a missing one and is written to the
    authorization audit stream; a missing row is not a refusal and is not audited.
    """
    row = _row_for_identifier(session_id)
    if row is None:
        return None, general_error(status_code=404, body={'message': 'Workstation not found'}, event=event)
    if row.get('userId') != _requesting_user_id():
        log_authorization(claims_and_roles, False, {
            "action": method,
            "obj": {"object__type": WORKSTATION_OBJECT_TYPE, "sessionId": session_id},
        })
        return None, general_error(status_code=404, body={'message': 'Workstation not found'}, event=event)
    return row, None


def _row_authorized(row, method):
    if len(claims_and_roles["tokens"]) == 0:
        return False
    auth_object = _workstation_auth_object(row.get('imageId'), row.get('osFamily'), row.get('instanceType'))
    return _enforcer().enforce(auth_object, method)


def _owned_and_authorized(event, path, method):
    """(session_id, row, None) for the caller's own, authorized row; otherwise (None, None, response)."""
    session_id, error = _session_id_or_error(event, path)
    if error is not None:
        return None, None, error
    row, error = _owned_row_or_error(event, session_id, method)
    if error is not None:
        return None, None, error
    if not _row_authorized(row, method):
        return None, None, authorization_error(event=event)
    return session_id, row, None


def _no_session_response(event):
    return general_error(status_code=409, body={'message': 'Workstation has no EDH session'}, event=event)


def _not_stopped_response(event, state):
    """The 409 for a start of a desktop that is not stopped; a running one is named as such."""
    message = 'Workstation is already running' if state == STATE_ACTIVE else 'Workstation is not stopped'
    return general_error(status_code=409, body={'message': message}, event=event)


def _not_stopped_in_edh_response(event, edh_state):
    """The 409 for a start of a stopped desktop EDH reports in a state other than stopped -- stopping or
    interrupted, say -- which EDH refuses to start; EDH's state is named."""
    message = f"The workstation is still {edh_state} in EDH; start it again once it has stopped."
    return general_error(status_code=409, body={'message': message}, event=event)


def _deleted_start_response(event):
    """The 409 for a start of a DELETED row, which is terminal whatever EDH still reports for it."""
    return general_error(status_code=409, body={'message': 'A deleted workstation cannot be started'}, event=event)


def _start_in_progress_response(event):
    """The 409 for a start of a desktop another start may yet start."""
    return general_error(status_code=409, event=event,
                         body={'message': 'A start of this workstation is already in progress',
                               START_IN_PROGRESS_FIELD: True})


def _creating_grace_elapsed(row):
    """True when a CREATING row's last write is older than the launch grace window; an unreadable
    timestamp keeps the row in the worker's hands."""
    try:
        updated_at = datetime.fromisoformat(str(row.get('updatedAt')))
    except ValueError:
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated_at > timedelta(minutes=CREATING_DELETE_GRACE_MINUTES)


def _claim_start(row):
    """Stamp the row with this start unless another start's stamp younger than the window, or a credential
    claim inside its own window, is on it, or the row is gone or DELETED; False when any holds. The write is
    conditional, so of two starts that read the row alike only one claims it, and a start and a re-issue of
    the key never run together; a stamp that holds nothing off -- one older than the window, or the one
    this request read and found holding nothing off -- is taken over. The row is brought to the item the
    claim left, so the start takes back the credential the row holds now."""
    now = datetime.now(timezone.utc)
    stamp = start_stamp(now)
    claim_window = timedelta(seconds=CREDENTIAL_CLAIM_SECONDS)
    values = {':v0': stamp, ':v1': _utc_now(), ':nullType': 'NULL', ':deleted': STATE_DELETED,
              ':cutoff': start_stamp(now - timedelta(seconds=START_IN_PROGRESS_SECONDS)),
              ':claimCutoff': credential_claim_stamp(now - claim_window),
              ':claimCeiling': credential_claim_stamp(now + claim_window)}
    # A cleared stamp is written as None, which DynamoDB stores as NULL.
    condition = "attribute_not_exists(#a0) OR attribute_type(#a0, :nullType) OR #a0 < :cutoff"
    if row.get('startRequestedAt') is not None:
        condition += " OR #a0 = :read"
        values[':read'] = row['startRequestedAt']
    # A released credential claim is removed; one dated further ahead than its window holds nothing.
    unclaimed = ("attribute_not_exists(#claim) OR attribute_type(#claim, :nullType) OR #claim < :claimCutoff"
                 " OR #claim > :claimCeiling")
    try:
        response = edh_session_table.update_item(
            Key={'bootstrapId': row['bootstrapId']},
            UpdateExpression="SET #a0 = :v0, #a1 = :v1",
            ConditionExpression=(f"attribute_exists(bootstrapId) AND #state <> :deleted AND ({condition})"
                                 f" AND ({unclaimed})"),
            ExpressionAttributeNames={'#a0': 'startRequestedAt', '#a1': 'updatedAt', '#state': 'state',
                                      '#claim': 'credentialClaimedAt'},
            ExpressionAttributeValues=values,
            ReturnValues='ALL_NEW',
        )
    except ClientError as err:
        if err.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
        return False
    row.update(startRequestedAt=stamp, updatedAt=values[':v1'])
    row.update(response.get('Attributes') or {})
    return True


def _deleted_since_read(row):
    """Whether the row, read consistently from the table, is gone or DELETED: which of the causes a claim
    refused on a row read live met."""
    stored = _stored_row(row)
    return not stored or stored.get('state') == STATE_DELETED


def _release_start_claim(row):
    """Clear the row's start stamp after a start that did not happen. A failure is logged and the stamp
    left to age out of the window; it never changes the start's own result."""
    try:
        _update_row(row, startRequestedAt=None)
    except Exception:
        logger.exception(f"Start claim release deferred for bootstrap {row['bootstrapId']}")


def get_workstation(event, path):
    session_id, row, error = _owned_and_authorized(event, path, "GET")
    if error is not None:
        return error
    if not row.get('sessionId'):
        return success(body=_record(row))
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    unanswered = set()
    desktops = _desktop_states(event, client, [row], edh_uid, "workstationGet", unanswered)
    _merge_edh_state([row], desktops)
    _record_failed_launches(event, [row], unanswered)
    return success(body=_record(row))


def stop_workstation(event, path):
    session_id, row, error = _owned_and_authorized(event, path, "POST")
    if error is not None:
        return error
    if not row.get('sessionId'):
        return _no_session_response(event)
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    try:
        _call_edh(event, client, "stop_desktop", "workstationStop", edh_uid, edh_uid, session_id)
    except EdhOutcomeUnknownError as lost:
        desktop = _desktop_after_lost_answer(event, client, row, edh_uid, "workstationStop", lost)
        # A stop EDH carried out reports the desktop stopping or stopped.
        if _listed_state(desktop) != STATE_STOPPED:
            raise
    # The revoke follows the row as the write left it: a key a renew named since the read goes too.
    _update_row_from_image(row, state=STATE_STOPPED, connectionAvailable=False, **start_stamp_cleared(row))
    _revoke_session_credential(event, row, delete_secret=False)
    return success(body=_record(row))


def _lease_expired(row):
    """Whether the row's lease has run out under the current policy. A lease the policy does not enforce,
    or an expiry that cannot be read, counts as no lease: the same non-enforcement the sweep applies."""
    if not read_lease_policy()['enabled'] or not row.get('leaseExpiresAt'):
        return False
    try:
        expiry = parse_iso(row['leaseExpiresAt'])
    except (ValueError, TypeError, AttributeError):
        return False
    return expiry <= datetime.now(timezone.utc)


def _lease_cleared(row, **attributes):
    """The attributes of a write that leaves the row no longer lease-stopped: the stamp that ages a stopped
    desktop toward deletion goes, and so does the expiry code, which only holds while the desktop stays
    stopped for want of a lease. Any other code is the owner's to read and is left in place."""
    cleared = dict(attributes, leaseStoppedAt=None)
    if row.get('lastError') == LAST_ERROR_LEASE_EXPIRED:
        cleared['lastError'] = None
    return cleared


def start_workstation(event, path):
    if claims_and_roles.get('authMethod') == AUTH_METHOD_API_KEY:
        return authorization_error(
            body={'message': 'Workstation start is not available to API key sessions'}, event=event)
    session_id, row, error = _owned_and_authorized(event, path, "POST")
    if error is not None:
        return error
    if not row.get('sessionId'):
        return _no_session_response(event)
    # A DELETED row is terminal, whatever EDH still lists for it, and an expired lease is renewed before
    # its desktop is started again; neither refusal mints a credential or reaches EDH.
    if row.get('state') == STATE_DELETED:
        return _deleted_start_response(event)
    if _lease_expired(row):
        return general_error(
            status_code=409, event=event,
            body={'message': 'The lease on this workstation has expired; renew it before starting'})
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    # EDH starts a stopped desktop only. The row follows EDH's live view first, so a stale row of a
    # stopped desktop proceeds and a running desktop keeps the credential it is using.
    desktops = _desktop_states(event, client, [row], edh_uid, "workstationStart")
    _merge_edh_state([row], desktops)
    if row.get('state') != STATE_STOPPED:
        return _not_stopped_response(event, row.get('state'))
    # VAMS holds a desktop EDH reports stopping or interrupted STOPPED, and EDH does not start it yet. The
    # state is EDH's session-state read; a desktop reported without one is left to EDH, whatever the row
    # last stored.
    desktop = _desktop_for_row(row, desktops)
    edh_state = str(desktop.get(EDH_FIELD_SESSION_STATE) or '') if desktop else ''
    if edh_state and edh_state.lower() != EDH_SESSION_STATE_STOPPED:
        return _not_stopped_in_edh_response(event, edh_state)
    if start_in_progress(row):
        # Another start of the desktop -- one still running, or one whose outcome EDH did not confirm -- may
        # yet start it; nothing is minted or started.
        return _start_in_progress_response(event)
    if not _claim_start(row):
        # Another start claimed the row first, or a delete reached it after the read: a row now DELETED or
        # gone is refused as one read DELETED is. Nothing is minted or started either way.
        if _deleted_since_read(row):
            return _deleted_start_response(event)
        return _start_in_progress_response(event)
    credential = {}
    try:
        if row.get('apiKeyId'):
            # A key a stop could not revoke is replaced, never reused. Only that key goes ahead of the mint,
            # so no listing of the session keys spends the mint's time.
            _revoke_session_credential(event, row, delete_secret=False, every_key=False)
        outcome, credential = bootstrap.bootstrap_session(
            event, row, claims_and_roles.get('mfaEnabled', False),
            deadline=_bootstrap_deadline(MINT_RESERVE_SECONDS_START))
        if credential:
            _update_row(row, **credential)
        _call_edh(event, client, "start_desktop", "workstationStart", edh_uid, edh_uid, session_id)
    except EdhOutcomeUnknownError as lost:
        # EDH records a start only once any capacity reservation it makes has settled and the instance is
        # starting, so a desktop still reported stopped, or one the read of the desktop's state did not
        # report, may yet start: the credential stays, for the reconciler to revoke if the desktop stays
        # stopped, and the start keeps its claim on the row so that another start does not follow it into EDH.
        desktop = _desktop_after_lost_answer(event, client, row, edh_uid, "workstationStart", lost)
        if _listed_state(desktop) in (None, STATE_STOPPED):
            raise
        # The read of the desktop's state bound the fresh secret to the instance it shows, as every
        # observation does.
    except Exception as err:
        if credential and isinstance(err, EdhError):
            # The desktop did not start; the key minted for it is taken back, and only that key: the row
            # stays stopped, so the bootstrap's other keys are the reconciler's orphan sweep's.
            _revoke_session_credential(event, row, delete_secret=False, every_key=False)
        # A start that did not happen gives its claim up, so a retry is not held off.
        _release_start_claim(row)
        raise
    else:
        if credential:
            # The desktop keeps its instance across a stop and start; the fresh key's secret is bound to
            # the instance the row last observed.
            _bind_session_credential(row)
    # EDH moves an accepted start to `pending`; the row follows the shared mapping until a read observes
    # `running`, so a restarted desktop is never reported ACTIVE ahead of EDH. A started desktop is no
    # longer lease-stopped, so the stamp that ages a stopped one toward deletion and the expiry code go
    # with it, as does the start's claim.
    _update_row(row, **_lease_cleared(row, state=STATE_CREATING, edhState=EDH_SESSION_STATE_PENDING,
                                      connectionAvailable=False, **start_stamp_cleared(row)))
    # The desktop was stopped until EDH took the start, so none of its keys but the one just minted is in
    # use. Their cleanup runs last, on what the request has left, so it never spends the mint's time or
    # EDH's window.
    _delete_other_session_keys(event, row, credential.get('apiKeyId'))
    return success(body={**_record(row), RESPONSE_FIELD_SESSION_BOOTSTRAP: outcome})


def _claim_credential(row, claim):
    """Stamp the row with ``claim``, a credential claim for a re-issue of its key, provided the row exists,
    is stored ACTIVE, carries no claim inside its window and no start under way; whether the row now holds
    it. The write is conditional, so of two renews, or a renew and the sweep's revoke, only one holds the
    row at a time; a claim older than the window, or dated further ahead than one, is taken over. A refused
    write the table shows carrying ``claim`` is a resend of one that landed. The row is brought to the item
    the claim left."""
    now = datetime.fromisoformat(claim)
    window = timedelta(seconds=CREDENTIAL_CLAIM_SECONDS)
    values = {':v0': claim, ':v1': _utc_now(), ':nullType': 'NULL', ':active': STATE_ACTIVE,
              ':claimCutoff': credential_claim_stamp(now - window),
              ':claimCeiling': credential_claim_stamp(now + window),
              ':startCutoff': start_stamp(now - timedelta(seconds=START_IN_PROGRESS_SECONDS))}
    # A cleared claim or start stamp is removed or written as None, which DynamoDB stores as NULL.
    condition = ("attribute_exists(bootstrapId) AND #state = :active"
                 " AND (attribute_not_exists(#a0) OR attribute_type(#a0, :nullType) OR #a0 < :claimCutoff"
                 " OR #a0 > :claimCeiling)"
                 " AND (attribute_not_exists(#start) OR attribute_type(#start, :nullType) OR #start < :startCutoff)")
    try:
        response = edh_session_table.update_item(
            Key={'bootstrapId': row['bootstrapId']},
            UpdateExpression="SET #a0 = :v0, #a1 = :v1",
            ConditionExpression=condition,
            ExpressionAttributeNames={'#a0': 'credentialClaimedAt', '#a1': 'updatedAt', '#state': 'state',
                                      '#start': 'startRequestedAt'},
            ExpressionAttributeValues=values,
            ReturnValues='ALL_NEW',
        )
    except ClientError as err:
        if err.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
        current = _stored_row(row)
        if not current or current.get('credentialClaimedAt') != claim:
            return False
        row.update(current)
        return True
    row.update(credentialClaimedAt=claim, updatedAt=values[':v1'])
    row.update(response.get('Attributes') or {})
    return True


def _settle_reissued_key(row, minted, claim):
    """Name the re-issued key on the row, provided the row still carries ``claim``, is ACTIVE and names the
    key it was read with; whether the row now names it. A refused write the table shows naming the key is a
    resend of one that landed. The key it replaces is left valid, since the desktop may still sign in with
    it, until its own expiry."""
    replaced = row.get('apiKeyId')
    attributes = {'apiKeyId': minted['apiKeyId'], 'keyExpiresAt': minted['expiresAt']}
    write = _row_write(row, attributes)
    write['ExpressionAttributeNames'].update({'#claim': 'credentialClaimedAt', '#state': 'state'})
    values = write['ExpressionAttributeValues']
    values.update({':claim': claim, ':active': STATE_ACTIVE})
    if replaced:
        read_key = "#a0 = :read"
        values[':read'] = replaced
    else:
        read_key = "(attribute_not_exists(#a0) OR attribute_type(#a0, :nullType) OR #a0 = :blank)"
        values.update({':nullType': 'NULL', ':blank': ''})
    write['ConditionExpression'] = f"#claim = :claim AND #state = :active AND {read_key}"
    try:
        edh_session_table.update_item(**write)
    except ClientError as err:
        if err.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
        current = _stored_row(row)
        if not current or current.get('apiKeyId') != minted['apiKeyId']:
            return False
        row.update(current)
        return True
    row.update(attributes)
    return True


def _release_credential_claim(row, claim):
    """Remove the row's credential claim, provided the row still carries ``claim``. A failure is logged and
    the claim left to age out of the window; it never changes the renew's own result."""
    attributes = {}
    write = _row_write(row, attributes)
    write['UpdateExpression'] += " REMOVE #claim"
    write['ExpressionAttributeNames']['#claim'] = 'credentialClaimedAt'
    write['ExpressionAttributeValues'][':claim'] = claim
    write['ConditionExpression'] = "#claim = :claim"
    try:
        edh_session_table.update_item(**write)
    except Exception as err:
        if not (isinstance(err, ClientError)
                and err.response.get('Error', {}).get('Code') == 'ConditionalCheckFailedException'):
            logger.exception(f"Credential claim release deferred for bootstrap {row['bootstrapId']}")
        else:
            logger.warning(f"Credential claim on bootstrap {row['bootstrapId']} was overtaken before its release")
        return
    row.update(attributes)
    row.pop('credentialClaimedAt', None)


def _release_reissued_claim(row, claim, minted, attributes):
    """Persist the secret's ``attributes`` (plus updatedAt) and remove the row's credential claim, provided
    the row still carries ``claim``, is ACTIVE and names the re-issued key; (whether the write landed, the
    row as the table held it when a refused write did not). A refused write the table shows released with
    the key named is a resend of one that landed."""
    write = _row_write(row, attributes)
    write['UpdateExpression'] += " REMOVE #claim"
    write['ExpressionAttributeNames'].update({'#claim': 'credentialClaimedAt', '#state': 'state', '#key': 'apiKeyId'})
    write['ExpressionAttributeValues'].update({':claim': claim, ':active': STATE_ACTIVE, ':minted': minted['apiKeyId']})
    write['ConditionExpression'] = "#claim = :claim AND #state = :active AND #key = :minted"
    try:
        edh_session_table.update_item(**write)
    except ClientError as err:
        if err.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise
        current = _stored_row(row)
        if not current or current.get('credentialClaimedAt') or current.get('apiKeyId') != minted['apiKeyId']:
            return False, current
        row.update(current)
    else:
        row.update(attributes)
    row.pop('credentialClaimedAt', None)
    return True, None


def _delete_reissued_key(event, row, minted):
    """Delete the re-issued key's record, and nothing else. A failure is logged and the key left to the
    reconciler's orphan sweep."""
    try:
        bootstrap.delete_session_key(event, minted['apiKeyId'], bootstrap_id=row['bootstrapId'],
                                     user_id=row['userId'], actor=_requesting_user_id())
    except Exception:
        logger.exception(f"Re-issued session key {minted['apiKeyId']} could not be deleted; the reconciler's orphan "
                         f"sweep deletes it once it has expired, or once its desktop is no longer live and it has "
                         f"aged past the orphan grace")


def _take_back_unreleased_key(event, row, minted, attributes, current, claim):
    """Settle what a re-issue whose release was overtaken leaves, ``current`` being the row the refused
    release found. While that row is live the re-issued key stays with the desktop, which may be signing in
    with it, until its own expiry. Otherwise the key is deleted and the secret's value revoked, the
    container going with it when this re-issue created it and the row does not name it. A failure is
    logged; it never changes the renew's own result."""
    if current is not None and session_row_live(current, own_claim=claim):
        return
    _delete_reissued_key(event, row, minted)
    created_unnamed = (attributes.get('secretBoundInstanceId') == ''
                       and (current or {}).get('secretName') != attributes.get('secretName'))
    try:
        bootstrap.neutralise_session_secret(row['bootstrapId'], delete_secret=created_unnamed)
    except Exception:
        logger.exception(f"Session secret of bootstrap {row['bootstrapId']} could not be revoked after its re-issue "
                         f"was overtaken")


def _reissue_session_key(event, row):
    """Re-issue the session key of a desktop stored running, once its lease is renewed; the outcome, or
    None when no re-issue is attempted: an API-key caller, a row not stored ACTIVE, or a start under way.
    The row is claimed first and taken as the claim left it. The fresh key is named on the row before any
    secret holds it, so the secret only ever holds a key the row names: a key the row could not be settled
    with is deleted, and a secret write that fails deletes nothing. The key it replaces stays valid until
    its own expiry, since the desktop may still sign in with it. The claim is released with the secret's
    attributes while the row is still ACTIVE and names the fresh key; a release a stop or a delete
    overtook takes the fresh key back and revokes the secret's value, and one overtaken on a row that is
    still live leaves both with the desktop. A container the write created is bound to the desktop's
    instance. A table or bootstrap failure answers ``unavailable``; none changes the renew's own result."""
    if claims_and_roles.get('authMethod') == AUTH_METHOD_API_KEY:
        # A desktop's own session key keeps its lease alive but never mints or extends a key.
        return None
    if row.get('state') != STATE_ACTIVE or start_in_progress(row):
        return None
    if not bootstrap.bootstrap_applies(row):
        return bootstrap.BOOTSTRAP_DISABLED
    bootstrap_id = row['bootstrapId']
    claim = credential_claim_stamp(datetime.now(timezone.utc))
    try:
        claimed = _claim_credential(row, claim)
    except Exception:
        logger.exception(f"Session key re-issue unavailable for bootstrap {bootstrap_id}: the row could not be claimed")
        _release_credential_claim(row, claim)
        return bootstrap.BOOTSTRAP_UNAVAILABLE
    if not claimed:
        logger.info(f"Session key re-issue skipped for bootstrap {bootstrap_id}: the row is held")
        return bootstrap.BOOTSTRAP_UNAVAILABLE
    try:
        outcome, minted = bootstrap.mint_reissue_key(
            event, row, claims_and_roles.get('mfaEnabled', False),
            deadline=_bootstrap_deadline(MINT_RESERVE_SECONDS_RENEW))
    except Exception:
        logger.exception(f"Session key re-issue unavailable for bootstrap {bootstrap_id}")
        _release_credential_claim(row, claim)
        return bootstrap.BOOTSTRAP_UNAVAILABLE
    if minted is None:
        _release_credential_claim(row, claim)
        return outcome
    try:
        settled = _settle_reissued_key(row, minted, claim)
    except Exception:
        logger.exception(f"Session key re-issue for bootstrap {bootstrap_id} could not settle the row")
        settled = False
    if not settled:
        # The row changed under the claim -- stopped, deleted, or given another key -- or could not be
        # written, so the fresh key is taken back before any secret sees it.
        logger.warning(f"Session key re-issue for bootstrap {bootstrap_id} could not name the re-issued key on "
                       f"the row; the key is taken back")
        _delete_reissued_key(event, row, minted)
        _release_credential_claim(row, claim)
        return bootstrap.BOOTSTRAP_UNAVAILABLE
    try:
        attributes = bootstrap.write_reissued_secret(row, minted)
    except Exception:
        # The row names the fresh key and the secret still holds the one replaced, which stays valid until
        # its own expiry.
        logger.exception(f"Session secret write deferred for bootstrap {bootstrap_id}; the replaced key stays valid")
        _release_credential_claim(row, claim)
        return bootstrap.BOOTSTRAP_UNAVAILABLE
    try:
        released, current = _release_reissued_claim(row, claim, minted, attributes)
    except Exception:
        # The row names the key the secret now holds; the claim alone is released, or ages out of its window.
        logger.exception(f"Credential claim release deferred for bootstrap {bootstrap_id}")
        _release_credential_claim(row, claim)
        return bootstrap.BOOTSTRAP_UNAVAILABLE
    if not released:
        logger.warning(f"Session key re-issue for bootstrap {bootstrap_id} was overtaken before its release")
        _take_back_unreleased_key(event, row, minted, attributes, current, claim)
        _release_credential_claim(row, claim)
        return bootstrap.BOOTSTRAP_UNAVAILABLE
    if attributes.get('secretBoundInstanceId') == '' and not _bind_session_credential(row):
        return bootstrap.BOOTSTRAP_UNAVAILABLE
    return bootstrap.BOOTSTRAP_PROVISIONED


def renew_workstation(event, path):
    """Reset the caller's workstation lease to a new future expiry, clearing any stopped timestamp and the
    expiry code the lease left, and then re-issue the session key of a desktop stored running. The
    response and the audit record name the re-issue's outcome whenever one was attempted; a re-issue that
    fails answers ``unavailable``, and the lease and the audit record stand all the same."""
    _session_id, row, error = _owned_and_authorized(event, path, "POST")
    if error is not None:
        return error
    policy = read_lease_policy()
    if not policy['enabled']:
        return general_error(status_code=409,
                             body={'message': 'Leases are disabled for this deployment'}, event=event)
    if not row.get('leaseExpiresAt'):
        return general_error(status_code=409,
                             body={'message': 'This workstation has no lease to renew'}, event=event)
    expires_at = compute_expiry(datetime.now(timezone.utc), int(policy['leaseHours']))
    # A row deleted after it was read -- by the sweep's lease delete, say -- is refused as one read deleted is,
    # and the lease is not written over the deletion.
    if row.get('state') == STATE_DELETED or not _update_live_row(
            row, **_lease_cleared(row, leaseExpiresAt=expires_at)):
        return general_error(status_code=409,
                             body={'message': 'A deleted workstation cannot be renewed'}, event=event)
    try:
        outcome = _reissue_session_key(event, row)
    except Exception:
        logger.exception(f"Session key re-issue unavailable for bootstrap {row['bootstrapId']}")
        outcome = bootstrap.BOOTSTRAP_UNAVAILABLE
    audit = {"sessionId": row.get("sessionId"), "bootstrapId": row["bootstrapId"],
             "leaseExpiresAt": expires_at, "leaseHours": int(policy["leaseHours"])}
    body = _record(row)
    if outcome is not None:
        audit[RESPONSE_FIELD_SESSION_BOOTSTRAP] = outcome
        body[RESPONSE_FIELD_SESSION_BOOTSTRAP] = outcome
    log_actions(event, "edhWorkstationRenew", audit)
    return success(body=body)


def delete_workstation(event, path):
    session_id, row, error = _owned_and_authorized(event, path, "DELETE")
    if error is not None:
        return error
    if not row.get('sessionId'):
        # Nothing in EDH to delete. The launch record is removed once the worker is done with it,
        # which a CREATING row is only after the grace window.
        if row.get('state') == STATE_CREATING and not _creating_grace_elapsed(row):
            return general_error(status_code=409,
                                 body={'message': 'Workstation is still being created'}, event=event)
        _record_deleted(event, row)
        return success(body=_record(row))
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    try:
        _call_edh(event, client, "delete_desktop", "workstationDelete", edh_uid, edh_uid, session_id)
    except EdhNotFoundError:
        logger.info("EDH desktop already absent on delete; recording the deletion")
    except EdhOutcomeUnknownError as lost:
        desktop = _desktop_after_lost_answer(event, client, row, edh_uid, "workstationDelete", lost)
        # EDH reports every desktop it has not deleted.
        if desktop is not None:
            raise
    _record_deleted(event, row, connectionAvailable=False)
    return success(body=_record(row))


def _not_ready_response(event):
    return general_error(status_code=409,
                         body={'message': 'Workstation is not ready for connection'}, event=event)


def connect_workstation(event, path):
    """The DCV connection URL of the caller's ACTIVE session: the ``connection_string`` EDH's per-user
    listing renders for that one desktop while it is running; EDH issues its connection token on this
    call. A row in any other stored state is not ready and EDH is not asked. The URL is a credential and
    appears in the response body only. A caller that authenticated with an API key, a desktop's own
    temporary session key included, is refused: the URL is a credential for the desktop."""
    if claims_and_roles.get('authMethod') == AUTH_METHOD_API_KEY:
        return authorization_error(
            body={'message': 'Workstation connect is not available to API key sessions'}, event=event)
    session_id, row, error = _owned_and_authorized(event, path, "GET")
    if error is not None:
        return error
    if not row.get('sessionId'):
        return _no_session_response(event)
    if row.get('state') != STATE_ACTIVE:
        return _not_ready_response(event)
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    desktop = _call_edh(event, client, "get_desktop_connection", "workstationConnect", edh_uid, edh_uid,
                        row['sessionId'])
    desktop = _desktop_for_row(row, [desktop] if isinstance(desktop, dict) else [])
    if desktop is None:
        if row.get('connectionAvailable'):
            _update_row(row, connectionAvailable=False)
        return _not_ready_response(event)
    _observe_desktop(row, desktop)
    # _update_row mirrors its writes onto the row, so the row's connectionAvailable is now
    # observed_row_attributes' verdict: EDH reports the desktop running.
    connection_string = desktop.get(EDH_FIELD_CONNECTION_STRING)
    if not row.get('connectionAvailable') or not isinstance(connection_string, str) or not connection_string:
        return _not_ready_response(event)
    return success(body=ConnectResponseModel(
        sessionId=session_id, connectionUrl=connection_string, osFamily=row['osFamily']).dict())


def _windows_username(desktop, edh_uid):
    """The Windows account a desktop signs in with: the ``username`` query value of its connection string,
    else its EDH session owner, else the caller's EDH uid."""
    try:
        query = parse_qs(urlsplit(str(desktop.get(EDH_FIELD_CONNECTION_STRING) or '')).query)
    except ValueError:
        query = {}
    for value in query.get(WINDOWS_USERNAME_QUERY_FIELD, []):
        if value.strip():
            return value.strip()
    owner = str(desktop.get(EDH_FIELD_SESSION_OWNER) or '').strip()
    return owner or edh_uid


def windows_sign_in(event, path):
    """The Windows sign-in of the caller's ACTIVE Windows desktop: the account its connection string names
    and the password EDH generated for it, or no password for a directory (``DOMAIN\\user``) account. The
    route answers only what connect would connect to. The password is a credential and appears in the
    response body only. A caller that authenticated with an API key, a desktop's own temporary session
    key included, is refused, as connect refuses it."""
    if claims_and_roles.get('authMethod') == AUTH_METHOD_API_KEY:
        return authorization_error(
            body={'message': 'Workstation Windows sign-in is not available to API key sessions'}, event=event)
    session_id, row, error = _owned_and_authorized(event, path, "GET")
    if error is not None:
        return error
    if row.get('osFamily') != OS_FAMILY_WINDOWS:
        return validation_error(body={'message': 'Workstation is not a Windows workstation'}, event=event)
    if not row.get('sessionId'):
        return _no_session_response(event)
    if row.get('state') != STATE_ACTIVE:
        return _not_ready_response(event)
    edh_uid = _caller_edh_uid()
    client = EdhClient(profile="api")
    desktop = _call_edh(event, client, "get_desktop_connection", "workstationWindowsSignIn", edh_uid, edh_uid,
                        row['sessionId'])
    desktop = _desktop_for_row(row, [desktop] if isinstance(desktop, dict) else [])
    if desktop is None:
        if row.get('connectionAvailable'):
            _update_row(row, connectionAvailable=False)
        return _not_ready_response(event)
    _observe_desktop(row, desktop)
    connection_string = desktop.get(EDH_FIELD_CONNECTION_STRING)
    if not row.get('connectionAvailable') or not isinstance(connection_string, str) or not connection_string:
        return _not_ready_response(event)
    username = _windows_username(desktop, edh_uid)
    directory_account = '\\' in username
    password = None
    if not directory_account:
        password = desktop.get(EDH_FIELD_LOCAL_ADMIN_PASSWORD)
        if not isinstance(password, str) or not password.strip():
            return general_error(status_code=409, body={'message': 'Windows password is not available yet'},
                                 event=event)
    logger.info(f"Windows sign-in details returned for session {session_id} to {_requesting_user_id()}")
    return success(body=WindowsSignInResponseModel(
        sessionId=session_id, username=username, password=password,
        directoryAccount=directory_account).dict())


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def handle_get(event, path):
    if API_EDH_CATALOG_INSTANCE_TYPES.matches(path):
        return list_instance_types(event)
    if API_EDH_CATALOG_IMAGES.matches(path):
        return list_catalog_images(event)
    if API_EDH_CATALOG_LEASE.matches(path):
        return get_catalog_lease(event)
    if API_EDH_WORKSTATIONS.matches(path):
        return list_workstations(event)
    if API_EDH_WORKSTATION.matches(path):
        return get_workstation(event, path)
    if API_EDH_WORKSTATION_CONNECT.matches(path):
        return connect_workstation(event, path)
    if API_EDH_WORKSTATION_WINDOWS_SIGN_IN.matches(path):
        return windows_sign_in(event, path)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_post(event, path):
    if API_EDH_WORKSTATIONS.matches(path):
        return create_workstation(event)
    if API_EDH_WORKSTATION_STOP.matches(path):
        return stop_workstation(event, path)
    if API_EDH_WORKSTATION_START.matches(path):
        return start_workstation(event, path)
    if API_EDH_WORKSTATION_RENEW.matches(path):
        return renew_workstation(event, path)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def handle_delete(event, path):
    if API_EDH_WORKSTATION.matches(path):
        return delete_workstation(event, path)
    return validation_error(body={'message': 'Unsupported route'}, event=event)


def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    global claims_and_roles, request_deadline
    claims_and_roles = request_to_claims(event)
    request_deadline = _request_deadline(context)

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
