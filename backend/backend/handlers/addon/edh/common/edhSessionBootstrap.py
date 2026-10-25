# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-session VAMS credential for an EDH workstation.

A VAMS API key is minted for the launching user through the apiKeyService Lambda (a lambdaCrossCall
invoke, so hashing, name uniqueness, the roles precondition and the audit row stay in one place) and
delivered through a Secrets Manager secret named ``<prefix><bootstrapId>``. The name derives from the
bootstrapId, which reaches the desktop as EDH's own session name (the instance's edh:JobName tag), so
no per-launch tag is passed to EDH and the name stays valid across stop/start and key re-mints. The
secret is created with no read grant at all; once EDH lists the desktop with the EC2 instance it is
placed on, ``bind_session_secret`` writes the resource policy that lets the workstation instance roles
read the value from that one instance only, so no other desktop of the cluster can read another
session's key. The key is never logged and never stored by VAMS outside that secret.

The mint sequence runs inside the caller's request, so it is bounded twice: a wall-clock deadline the
caller derives from the time its Lambda has left (a fixed budget stands in when no deadline is given),
checked before every remote step, and per-client connect/read timeouts that cap the one step allowed
to be in flight when the deadline runs out. The apiKeyService invoke is started only while the deadline
still holds its whole connect and read timeout, so a cold callee is waited for rather than abandoned
with a key minted that no row names; a key of the session's name that such a lost reply did leave
behind is taken back before the create is sent again. An invoke whose connection failed is sent once
more on the same terms.

A renew of a running desktop re-issues its key in two halves, so the caller can name the new key on the
row before any secret holds it: ``mint_reissue_key`` mints a key under the session's name and the UTC
second of the re-issue, writing nothing, and ``write_reissued_secret`` then puts it in the session secret.
Every key a running desktop was given stays valid until its own expiry, since the desktop signs in with
whichever one its secret held when it read it; a revoke deletes the key the row names and then every other
key whose name encodes the row's bootstrap, and a start deletes the key the row names ahead of its mint and
every other key but the fresh one once EDH has taken the start, within the deadline the caller gives.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import (BotoCoreError, ClientError, ConnectionClosedError, ConnectTimeoutError,
                                 EndpointConnectionError)

from common.apiRoutes import API_AUTH_API_KEYS, API_AUTH_API_KEY_BY_ID
from common.constants import TEMPORARY_SESSION_KEY_NAME_PREFIX
from customLogging.auditLogging import log_auth_changes
from customLogging.logger import safeLogger
from .edhIdentity import SYSTEM_USER_ID

# The mint sequence's wall-clock budget when the caller supplies no deadline, and the per-step timeouts
# that bound the step in flight when the deadline runs out. The invoke read timeout covers a cold
# apiKeyService (its module init, the Casbin build and a deployment-wide key-name check); the invoke is
# started only while at least its connect and read timeouts remain, so it can never outrun the deadline.
BOOTSTRAP_BUDGET_SECONDS = 15.0
CROSS_CALL_CONNECT_TIMEOUT_SECONDS = 1
CROSS_CALL_READ_TIMEOUT_SECONDS = 10
CROSS_CALL_TIMEOUT_SECONDS = CROSS_CALL_CONNECT_TIMEOUT_SECONDS + CROSS_CALL_READ_TIMEOUT_SECONDS
SECRETS_CONNECT_TIMEOUT_SECONDS = 1
SECRETS_READ_TIMEOUT_SECONDS = 2

AUDIT_SESSION_KEY_CREATE = "edhSessionApiKeyCreate"
AUDIT_SESSION_KEY_REVOKE = "edhSessionApiKeyRevoke"
# Audit operations of a revoke record: a key taken back, or an expired key record deleted.
AUDIT_OPERATION_REVOKE = "revoke"
AUDIT_OPERATION_EXPIRE = "expire"
# Audit operation of the create record of a key a renew re-issued.
AUDIT_OPERATION_REISSUE = "reissue"
# The name every session key is minted under: the core temporary session key prefix and the add-on's own
# part. apiKeyService refuses a caller holding a key under the core prefix where a key could be created
# or extended, and lets only a Lambda cross-call create a key under it.
SESSION_KEY_NAME_PREFIX = TEMPORARY_SESSION_KEY_NAME_PREFIX + "edh-"
SESSION_KEY_DESCRIPTION_PREFIX = "EDH workstation session "
# The suffix a re-issued key's name carries after the session's own: the UTC second it was minted at.
_REISSUE_SUFFIX_FORMAT = "-r%Y%m%d%H%M%S"
_REISSUE_SUFFIX = re.compile(r"-r[0-9]{14}$")
VAMS_BASE_URL_SSM_SCHEME = "ssm:"
# Value a revoked session's secret is overwritten with before it is deleted or reused.
REVOKED_SECRET_VALUE = json.dumps({"revoked": True})
# Secrets Manager errors that mean the container is already gone or already scheduled for deletion.
_SECRET_GONE_CODES = ("ResourceNotFoundException", "InvalidRequestException")
# Secrets Manager's answer when the key a secret is encrypted under cannot encrypt: disabled, pending
# deletion or gone. The value cannot be replaced then; the container can still be removed.
_SECRET_KEY_UNUSABLE_CODE = "EncryptionFailure"
# Row attributes cleared by a revoke; the secret attributes, the instance binding among them, are
# cleared only when the container goes.
CLEARED_KEY_ATTRIBUTES = {"apiKeyId": "", "keyExpiresAt": ""}
CLEARED_SECRET_ATTRIBUTES = {"secretArn": "", "secretName": "", "secretBoundInstanceId": ""}
_NO_ROLES_MARKER = "has no roles assigned"
# apiKeyService's refusal of a key name already in use. A session key of this name that no row references
# was minted by a request whose reply was lost.
_DUPLICATE_NAME_MARKER = "already exists"
# apiKeyService's answer to a delete of a key id it holds no record of. Every other 400 -- a route or an
# id it could not read, a general refusal -- is a delete that did not happen.
_KEY_NOT_FOUND_STATUS = 400
_KEY_NOT_FOUND_MESSAGE = "API key not found"
# Transport failures after which an apiKeyService invoke is sent once more: the pooled connection was
# closed under the request (a warm container's stale connection), or none could be opened. A read timeout
# is not among them -- the callee may have run. A create that a closed connection did deliver is refused
# on the resend as a duplicate name, which takes that key back.
_RESENT_TRANSPORT_ERRORS = (ConnectionClosedError, EndpointConnectionError, ConnectTimeoutError)
CROSS_CALL_ATTEMPTS = 2
# Secrets Manager's refusal of a resource policy that names a principal it cannot resolve. One allowlisted
# workstation role that no longer exists refuses the whole policy.
_POLICY_REFUSED_CODE = "MalformedPolicyDocumentException"
_UNSUPPORTED_PRINCIPAL_MARKER = "unsupported principal"
# An EC2 instance id as EDH lists it. IAM reads * and ? in an ArnEquals value as wildcards, so the secret
# policy is only ever conditioned on an id of this shape.
_INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")

# Outcomes of one bootstrap attempt, reported on the launch and start responses.
BOOTSTRAP_PROVISIONED = "provisioned"
BOOTSTRAP_SKIPPED = "skipped"
BOOTSTRAP_UNAVAILABLE = "unavailable"
BOOTSTRAP_DISABLED = "disabled"

# One page of the deployment-wide key listing (the apiKeyService clamps both bounds to its own
# ceiling), and the most pages one caller follows before leaving the rest to its next run.
KEY_LISTING_PAGE_QUERY = {"maxItems": "1000", "pageSize": "1000"}
KEY_LISTING_MAX_PAGES = 50

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'},
                      connect_timeout=SECRETS_CONNECT_TIMEOUT_SECONDS,
                      read_timeout=SECRETS_READ_TIMEOUT_SECONDS)
# The apiKeyService Invoke is synchronous and not idempotent: a retry of a slow-but-successful create
# would mint a second key that no row names. This client delivers exactly one Invoke per call.
cross_call_config = Config(retries={'total_max_attempts': 1},
                           connect_timeout=CROSS_CALL_CONNECT_TIMEOUT_SECONDS,
                           read_timeout=CROSS_CALL_READ_TIMEOUT_SECONDS)
lambda_client = boto3.client('lambda', config=cross_call_config)
secretsmanager_client = boto3.client('secretsmanager', config=retry_config)
ssm_client = boto3.client('ssm', config=retry_config)
logger = safeLogger(service_name="EdhSessionBootstrap")

# With the feature on, every variable is required and a missing one aborts the import. With it off --
# or absent altogether, as on a Lambda deployed without the feature -- the module loads on empty values:
# nothing is minted, and a row that still names a key is revoked through whatever apiKeyService name the
# environment does carry.
session_bootstrap_enabled = os.environ.get("EDH_SESSION_BOOTSTRAP_ENABLED", "false") == "true"


def _configured(name: str, default: str) -> str:
    """The variable's value: required while the feature is on, ``default`` when it is off and absent."""
    return os.environ[name] if session_bootstrap_enabled else os.environ.get(name, default)


try:
    session_secret_prefix = _configured("EDH_SESSION_SECRET_PREFIX", "")
    session_key_ttl_minutes = int(_configured("EDH_SESSION_KEY_TTL_MINUTES", "0"))
    workstation_instance_role_arns = json.loads(_configured("EDH_WORKSTATION_INSTANCE_ROLE_ARNS", "[]"))
    session_secret_kms_key_arn = _configured("EDH_SESSION_SECRET_KMS_KEY_ARN", "")
    api_key_service_function_name = _configured("API_KEY_SERVICE_LAMBDA_FUNCTION_NAME", "")
    # Either the deployment's static host or an ``ssm:<parameter>`` pointer to the deployed web URL.
    vams_base_url = _configured("VAMS_BASE_URL", "")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

_resolved_base_url: Optional[str] = None
_monotonic = time.monotonic
# Monotonic instant at which the current mint sequence's budget ends; None outside a sequence.
_budget_deadline: Optional[float] = None


class SessionBootstrapError(Exception):
    """A session credential could not be minted, stored or revoked."""


class SessionBootstrapBudgetError(SessionBootstrapError):
    """A step was not started because the sequence's budget had run out."""


def is_enabled() -> bool:
    return session_bootstrap_enabled


def bootstrap_applies(row: Dict[str, Any]) -> bool:
    """Whether the row's desktop takes a session credential: the feature is on and the desktop is not Windows,
    whose images carry no consumer for one."""
    return session_bootstrap_enabled and row.get("osFamily") != "windows"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _check_budget(step: str, needs: float = 0.0) -> None:
    """Refuse to start ``step`` once the mint sequence's budget has run out, or while less than ``needs``
    seconds of it remain."""
    if _budget_deadline is not None and _monotonic() + needs > _budget_deadline:
        raise SessionBootstrapBudgetError(f"session bootstrap budget exhausted before {step}")


def session_secret_name(bootstrap_id: str) -> str:
    return f"{session_secret_prefix}{bootstrap_id}"


def session_key_name(bootstrap_id: str, rotated_at: Optional[datetime] = None) -> str:
    """The name a session key is minted under: the session's own for a launch or start, and that name plus
    the UTC second of ``rotated_at`` for a key a renew re-issues, so the key it replaces keeps its name. An
    instant without an offset is read as UTC."""
    name = f"{SESSION_KEY_NAME_PREFIX}{bootstrap_id}"
    if rotated_at is None:
        return name
    if rotated_at.tzinfo is None:
        rotated_at = rotated_at.replace(tzinfo=timezone.utc)
    return name + rotated_at.astimezone(timezone.utc).strftime(_REISSUE_SUFFIX_FORMAT)


def bootstrap_id_from_key_name(name) -> str:
    """The bootstrapId a session key name encodes, a re-issue's suffix removed; a name without the session
    key prefix is returned as it is, and no name reads as no bootstrapId."""
    name = str(name or "")
    if not name.startswith(SESSION_KEY_NAME_PREFIX):
        return name
    return _REISSUE_SUFFIX.sub("", name[len(SESSION_KEY_NAME_PREFIX):])


def session_key_expires_at(now: Optional[datetime] = None) -> str:
    """ISO 8601 UTC expiry (``Z`` suffix) for a key minted now. Never blank: a blank expiresAt is a key
    that never expires."""
    if session_key_ttl_minutes < 1:
        raise SessionBootstrapError("session key TTL must be at least one minute")
    now = now or datetime.now(timezone.utc)
    return (now + timedelta(minutes=session_key_ttl_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_vams_base_url() -> str:
    """The URL the workstation's CLI is set up against: the configured static host, else the deployed
    web URL the web stack publishes to the named SSM parameter (cached for the life of the container)."""
    global _resolved_base_url
    if not vams_base_url:
        raise SessionBootstrapError("VAMS base URL is not configured")
    if not vams_base_url.startswith(VAMS_BASE_URL_SSM_SCHEME):
        return vams_base_url
    if _resolved_base_url:
        return _resolved_base_url
    _check_budget("deployed web URL lookup")
    parameter_name = vams_base_url[len(VAMS_BASE_URL_SSM_SCHEME):]
    try:
        response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=False)
    except (ClientError, BotoCoreError) as e:
        logger.exception(f"Deployed web URL parameter could not be read: {e}")
        raise SessionBootstrapError("VAMS base URL is not available")
    value = (response.get("Parameter", {}).get("Value") or "").strip()
    if not value:
        raise SessionBootstrapError("VAMS base URL is not available")
    _resolved_base_url = value
    return value


def _cross_call(method: str, path: str, actor: str, mfa_enabled: bool, *,
                path_parameters: Optional[Dict[str, str]] = None,
                query_parameters: Optional[Dict[str, str]] = None,
                body: Optional[Dict[str, Any]] = None):
    """Invoke the apiKeyService Lambda as ``actor`` carrying that caller's real MFA state. Returns
    (status_code, parsed body dict). The response body is never logged: on the create route it carries
    the plaintext key. The invoke shape is the one importGlobalPipelineWorkflow._invoke uses; this copy
    exists for the single-attempt client and the budget check in front of it, which starts the invoke only
    while its whole connect and read timeout still fits the deadline, and for the one resend after a
    connection failure, which is held to the same check."""
    event: Dict[str, Any] = {
        "requestContext": {"http": {"method": method, "path": path}},
        "pathParameters": path_parameters or {},
        "queryStringParameters": query_parameters or {},
        "lambdaCrossCall": {"userName": actor, "mfaEnabled": bool(mfa_enabled)},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    for attempt in range(1, CROSS_CALL_ATTEMPTS + 1):
        _check_budget(f"apiKeyService {method} {path}", CROSS_CALL_TIMEOUT_SECONDS)
        try:
            response = lambda_client.invoke(
                FunctionName=api_key_service_function_name, InvocationType="RequestResponse",
                Payload=json.dumps(event).encode("utf-8"))
            break
        except _RESENT_TRANSPORT_ERRORS as e:
            if attempt == CROSS_CALL_ATTEMPTS:
                logger.exception(f"Cross-call transport error invoking apiKeyService {method} {path}: {e}")
                raise SessionBootstrapError("apiKeyService invocation failed")
            logger.warning(f"Cross-call connection failed invoking apiKeyService {method} {path}; "
                           f"sending it once more: {e}")
        except Exception as e:
            logger.exception(f"Cross-call transport error invoking apiKeyService {method} {path}: {e}")
            raise SessionBootstrapError("apiKeyService invocation failed")
    payload = response.get("Payload")
    if not payload:
        raise SessionBootstrapError("apiKeyService returned no payload")
    parsed = json.loads(payload.read().decode("utf-8"))
    if "errorMessage" in parsed:
        logger.error(f"apiKeyService errored on {method} {path}: {parsed['errorMessage']}")
        raise SessionBootstrapError("apiKeyService invocation errored")
    status_code = parsed.get("statusCode", 500)
    inner: Dict[str, Any] = {}
    if parsed.get("body"):
        try:
            inner = json.loads(parsed["body"])
        except (ValueError, TypeError):
            inner = {}
    return status_code, inner


def _key_already_gone(status_code, body) -> bool:
    """Whether apiKeyService answered a key delete with its not-found answer."""
    return (status_code == _KEY_NOT_FOUND_STATUS and isinstance(body, dict)
            and body.get("message") == _KEY_NOT_FOUND_MESSAGE)


def _delete_stale_session_key(event, bootstrap_id: str, name: str, retain_key_ids=()) -> None:
    """Delete the session key records named ``name``: the name is unique deployment-wide, so a record
    apiKeyService refuses to create again was minted by a request whose reply was lost and is named by no
    row. A record in ``retain_key_ids`` -- a key the row names -- is never deleted. Read and deleted as the
    system user; the deletion is audited as a revoke."""
    retained = {str(key_id) for key_id in retain_key_ids if key_id}
    stale = [key for key in list_session_keys(SYSTEM_USER_ID)
             if key.get("apiKeyName") == name and str(key.get("apiKeyId")) not in retained]
    if not stale:
        logger.warning(f"No stale session key found for bootstrap {bootstrap_id}; the create is sent once more")
    for key in stale:
        logger.warning(f"Stale session key {key.get('apiKeyId')} of bootstrap {bootstrap_id} taken back")
        delete_orphan_key(event, key, SYSTEM_USER_ID, operation=AUDIT_OPERATION_REVOKE)


def mint_session_key(event, user_id: str, bootstrap_id: str, mfa_enabled: bool, *, key_name: Optional[str] = None,
                     operation: str = "create", retain_key_ids=()) -> Optional[Dict[str, str]]:
    """Mint the session key for ``user_id`` under ``key_name`` (the session's own name when not given),
    audited as ``operation``. Returns ``{"apiKeyId", "apiKey", "expiresAt"}``, or None when the user holds
    no roles -- the launch proceeds without a bootstrap credential. A key of that exact name that a lost
    mint reply left behind is taken back, unless it is in ``retain_key_ids``, and the create sent once
    more."""
    expires_at = session_key_expires_at()
    name = key_name or session_key_name(bootstrap_id)
    body = {
        "apiKeyName": name,
        "userId": user_id,
        "description": f"{SESSION_KEY_DESCRIPTION_PREFIX}{bootstrap_id}",
        "expiresAt": expires_at,
    }
    status_code, inner = _cross_call("POST", API_AUTH_API_KEYS.path, user_id, mfa_enabled, body=body)
    if status_code == 400 and _DUPLICATE_NAME_MARKER in str(inner.get("message", "")):
        _delete_stale_session_key(event, bootstrap_id, name, retain_key_ids)
        status_code, inner = _cross_call("POST", API_AUTH_API_KEYS.path, user_id, mfa_enabled, body=body)
    if status_code == 400 and _NO_ROLES_MARKER in str(inner.get("message", "")):
        logger.warning(f"Session key skipped for bootstrap {bootstrap_id}: the user holds no roles")
        return None
    if status_code != 200 or not inner.get("apiKey") or not inner.get("apiKeyId"):
        logger.error(f"Session key mint failed for bootstrap {bootstrap_id}: status {status_code}")
        raise SessionBootstrapError("session key could not be minted")
    log_auth_changes(event, AUDIT_SESSION_KEY_CREATE, {
        "bootstrapId": bootstrap_id,
        "apiKeyId": inner["apiKeyId"],
        "userId": user_id,
        "expiresAt": expires_at,
        "operation": operation,
    })
    return {"apiKeyId": inner["apiKeyId"], "apiKey": inner["apiKey"], "expiresAt": expires_at}


def create_session_secret(bootstrap_id: str, payload: Dict[str, str]) -> str:
    """Create the session secret, or overwrite its value when the container already exists (a start
    after a stop). No resource policy is written here: the secret stays unreadable until it is bound to
    the desktop's instance. Returns the secret ARN."""
    return _write_session_secret(bootstrap_id, payload)[0]


def _write_session_secret(bootstrap_id: str, payload: Dict[str, str]) -> Tuple[str, bool]:
    """(secret ARN, whether the container was created) of a write of ``payload`` into the session secret:
    a create, or an overwrite of the value when the container already exists, its resource policy kept."""
    name = session_secret_name(bootstrap_id)
    created = True
    secret_string = json.dumps(payload)
    try:
        _check_budget("session secret create")
        try:
            response = secretsmanager_client.create_secret(
                Name=name,
                Description=f"VAMS session credential for EDH workstation {bootstrap_id}",
                KmsKeyId=session_secret_kms_key_arn,
                SecretString=secret_string,
                Tags=[{"Key": "bootstrapId", "Value": bootstrap_id},
                      {"Key": "userId", "Value": payload["userId"]}],
            )
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "ResourceExistsException":
                raise
            created = False
            response = secretsmanager_client.put_secret_value(SecretId=name, SecretString=secret_string)
    except (ClientError, BotoCoreError):
        logger.exception(f"Session secret could not be written for bootstrap {bootstrap_id}")
        raise SessionBootstrapError("session secret could not be created")
    return response["ARN"], created


def _secret_arn_parts(secret_arn) -> Tuple[str, str, str]:
    """(partition, region, account) of a Secrets Manager secret ARN."""
    parts = str(secret_arn or "").split(":")
    if len(parts) < 7 or parts[0] != "arn" or parts[2] != "secretsmanager" or not all(parts[1:5]):
        raise SessionBootstrapError("session secret ARN is not readable")
    return parts[1], parts[3], parts[4]


def _instance_session_principals(role_arns, instance_id: str, *, account: str, partition: str) -> List[str]:
    """The assumed-role session ARN each workstation instance role carries on the EC2 instance
    ``instance_id``: EC2 names an instance-profile session after the instance, and the session ARN
    drops the role's path. A value that is not an IAM role ARN of this account is refused."""
    principals = []
    for role_arn in role_arns:
        parts = str(role_arn or "").split(":", 5)
        if (len(parts) != 6 or parts[0] != "arn" or parts[1] != partition or parts[2] != "iam"
                or parts[4] != account or not parts[5].startswith("role/")):
            raise SessionBootstrapError("workstation instance role ARN is not a role of this account")
        role_name = parts[5].rsplit("/", 1)[-1]
        if not role_name:
            raise SessionBootstrapError("workstation instance role ARN has no role name")
        principals.append(f"arn:{partition}:sts::{account}:assumed-role/{role_name}/{instance_id}")
    if not principals:
        raise SessionBootstrapError("no workstation instance role is configured")
    return principals


def _instance_read_policy(principals) -> Dict[str, Any]:
    """Resource policy of a bound session secret: only the workstation instance roles' sessions on the
    one EC2 instance the desktop runs on may read the value. A Secrets Manager resource policy accepts
    only ``secretsmanager:`` and global condition keys, so the instance is named in the principal."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": list(principals)},
            "Action": "secretsmanager:GetSecretValue",
            "Resource": "*",
        }],
    }


def _put_read_policy(secret_id: str, principals) -> None:
    secretsmanager_client.put_resource_policy(
        SecretId=secret_id,
        ResourcePolicy=json.dumps(_instance_read_policy(principals)),
        BlockPublicPolicy=True,
    )


def _principal_refused(error: ClientError) -> bool:
    """Whether Secrets Manager refused a policy for naming a principal it cannot resolve."""
    details = error.response.get("Error", {})
    return (details.get("Code") == _POLICY_REFUSED_CODE
            and _UNSUPPORTED_PRINCIPAL_MARKER in str(details.get("Message", "")).lower())


def bind_session_secret_to_instance(secret_id: str, instance_id: str, *, role_arns, region: str,
                                    account: str, partition: str) -> None:
    """Put the resource policy that lets ``role_arns`` read ``secret_id`` from the EC2 instance
    ``instance_id`` alone. Putting it again for the same instance rewrites the same policy. A policy
    Secrets Manager refuses for a principal it cannot resolve -- an allowlisted role that no longer
    exists -- is put again one role's session at a time, and then names the sessions it accepted. A
    refused put leaves the policy in place, and each single-session policy is narrower than the full
    one."""
    principals = _instance_session_principals(role_arns, instance_id, account=account, partition=partition)
    try:
        try:
            _put_read_policy(secret_id, principals)
            return
        except ClientError as e:
            if not _principal_refused(e):
                raise
        accepted = []
        for principal in principals:
            try:
                _put_read_policy(secret_id, [principal])
            except ClientError as e:
                if not _principal_refused(e):
                    raise
                logger.warning(f"Workstation instance role {principal.split('/')[1]} is not a principal Secrets "
                               f"Manager accepts; it is left out of the binding of {secret_id}")
                continue
            accepted.append(principal)
        if not accepted:
            raise SessionBootstrapError("no workstation instance role could be named in the binding")
        # The last put that succeeded named one accepted session; the policy names them all.
        if len(accepted) > 1:
            _put_read_policy(secret_id, accepted)
    except (ClientError, BotoCoreError):
        logger.exception(f"Session secret policy could not be written for {secret_id}")
        raise SessionBootstrapError("session secret could not be bound to its instance")


def bind_session_secret(row: Dict[str, Any], instance_id) -> Dict[str, str]:
    """Bind the row's session secret to the EC2 instance EDH placed the desktop on; the partition,
    region and account are the secret's own. Returns the row attribute that records the binding. A
    value that is not an instance id is refused before any policy is written."""
    instance_id = str(instance_id)
    if not _INSTANCE_ID_PATTERN.fullmatch(instance_id):
        logger.error(f"Session secret binding refused for bootstrap {row.get('bootstrapId')}: "
                     f"the instance id is not an EC2 instance id")
        raise SessionBootstrapError("instance id is not an EC2 instance id")
    partition, region, account = _secret_arn_parts(row.get("secretArn"))
    bind_session_secret_to_instance(
        row.get("secretName") or session_secret_name(row["bootstrapId"]), instance_id,
        role_arns=workstation_instance_role_arns, region=region, account=account, partition=partition)
    return {"secretBoundInstanceId": instance_id}


def delete_session_key(event, api_key_id: str, *, bootstrap_id: str, user_id: str, actor: str,
                       operation: str = AUDIT_OPERATION_REVOKE) -> None:
    """Delete one session key record as ``actor``, and nothing else: no secret is read or written. A record
    already gone is a deletion that already happened and is not audited; any other answer but success
    raises. The audit row names ``bootstrap_id``, the key's user and ``operation``. Runs inside whatever
    mint budget the caller holds."""
    status_code, body = _cross_call(
        "DELETE", API_AUTH_API_KEY_BY_ID.path.replace("{apiKeyId}", api_key_id),
        actor, False, path_parameters={"apiKeyId": api_key_id})
    if status_code != 200 and not _key_already_gone(status_code, body):
        logger.error(f"Session key {api_key_id} delete failed for bootstrap {bootstrap_id}: status {status_code}")
        raise SessionBootstrapError("session key could not be deleted")
    if status_code == 200:
        log_auth_changes(event, AUDIT_SESSION_KEY_REVOKE, {
            "bootstrapId": bootstrap_id,
            "apiKeyId": api_key_id,
            "userId": user_id,
            "operation": operation,
        })


def revoke_session(event, row: Dict[str, Any], *, delete_secret: bool, actor: str,
                   every_key: bool = True) -> Dict[str, str]:
    """Revoke the row's session key and every other key of its bootstrap, and neutralise its secret. The
    key the row names is deleted first and authoritatively; the session keys are then listed and every
    other key whose name encodes the bootstrap is deleted too, a listing that fails being logged and those
    keys left to the reconciler's orphan sweep. Without ``every_key`` only the key the row names is deleted
    and nothing is listed. The secret value is overwritten before the container is
    deleted so a reader inside the authorizer's cache window (about 45 s) finds no credential. With
    ``delete_secret`` the container is removed without a recovery window; without it the container stays
    for a later start to overwrite. The overwrite needs the session-secrets key: with none configured (the
    feature off, the key gone with the stack), or when the key the secret was written under can no longer
    encrypt, the overwrite is skipped and the deletion, where asked for, still runs -- the value such a
    container holds is unreadable already. Runs outside any mint budget -- a rollback must always be
    allowed to finish -- and is bounded by the clients' own timeouts. Returns the row attributes that
    clear the credential."""
    global _budget_deadline
    _budget_deadline = None
    bootstrap_id = row["bootstrapId"]
    named = row.get("apiKeyId")
    # A key already gone is a revoke that already happened, and is not this caller's action to audit.
    if named:
        delete_session_key(event, str(named), bootstrap_id=bootstrap_id, user_id=row["userId"], actor=actor)
    if every_key:
        _delete_bootstrap_keys(event, bootstrap_id, named, row["userId"], actor)
    _neutralise_session_secret(bootstrap_id, row.get("secretName") or session_secret_name(bootstrap_id),
                               delete_secret=delete_secret)
    cleared = dict(CLEARED_KEY_ATTRIBUTES)
    if delete_secret:
        cleared.update(CLEARED_SECRET_ATTRIBUTES)
    return cleared


def _delete_bootstrap_keys(event, bootstrap_id: str, named, user_id: str, actor: str) -> None:
    """Delete every session key whose name encodes ``bootstrap_id`` save ``named``, the key the row names:
    listed as the system user, deleted as ``actor`` and audited under the key's own user. A listing that
    fails, or that the budget leaves no time for (logged as a warning), is logged and those keys are left
    to the reconciler's orphan sweep; a delete that fails raises."""
    try:
        keys = list_session_keys(SYSTEM_USER_ID)
    except SessionBootstrapBudgetError as e:
        logger.warning(f"Session key listing skipped for bootstrap {bootstrap_id}: {e}; its other keys are left "
                       f"to the reconciler's orphan sweep")
        return
    except Exception:
        logger.exception(f"Session key listing failed for bootstrap {bootstrap_id}; its other keys are left to "
                         f"the reconciler's orphan sweep")
        return
    for key in keys:
        api_key_id = key.get("apiKeyId")
        if (not api_key_id or str(api_key_id) == str(named or "")
                or bootstrap_id_from_key_name(key.get("apiKeyName")) != bootstrap_id):
            continue
        delete_session_key(event, str(api_key_id), bootstrap_id=bootstrap_id, user_id=key.get("userId") or user_id,
                           actor=actor)


def delete_other_session_keys(event, row: Dict[str, Any], *, keep: Optional[str], actor: str,
                              deadline: Optional[float] = None) -> None:
    """The listing half of a revoke on its own: every session key whose name encodes the row's bootstrap is
    deleted save ``keep``, and no secret is read or written. ``deadline`` is the ``time.monotonic()``
    instant the cleanup must be done by, as for ``bootstrap_session``, and replaces any mint budget still
    set; each apiKeyService invoke is started only while its whole connect and read timeout still fits it.
    A listing that fails or is not started is logged and those keys are left to the reconciler's orphan
    sweep; a delete that fails or is not started raises SessionBootstrapError."""
    global _budget_deadline
    _budget_deadline = deadline if deadline is not None else _monotonic() + BOOTSTRAP_BUDGET_SECONDS
    try:
        _delete_bootstrap_keys(event, row["bootstrapId"], keep, row["userId"], actor)
    finally:
        _budget_deadline = None


def neutralise_session_secret(bootstrap_id: str, *, delete_secret: bool) -> None:
    """The secret half of a revoke on its own: the session secret's value is overwritten with the revoked
    marker and, with ``delete_secret``, the container removed after it; no key record is read or deleted.
    A container already gone needs nothing more; any other failure raises SessionBootstrapError."""
    _neutralise_session_secret(bootstrap_id, session_secret_name(bootstrap_id), delete_secret=delete_secret)


def _neutralise_session_secret(bootstrap_id: str, name: str, *, delete_secret: bool) -> None:
    try:
        if session_secret_kms_key_arn:
            try:
                secretsmanager_client.put_secret_value(SecretId=name, SecretString=REVOKED_SECRET_VALUE)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code") != _SECRET_KEY_UNUSABLE_CODE:
                    raise
                logger.warning(f"Session secret overwrite skipped for bootstrap {bootstrap_id}: its key cannot encrypt")
        if delete_secret:
            secretsmanager_client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
    except ClientError as e:
        # A secret already gone, or already scheduled for deletion, needs nothing more.
        if e.response.get("Error", {}).get("Code") not in _SECRET_GONE_CODES:
            logger.exception(f"Session secret cleanup failed for bootstrap {bootstrap_id}")
            raise SessionBootstrapError("session secret could not be revoked")
    except BotoCoreError:
        logger.exception(f"Session secret cleanup failed for bootstrap {bootstrap_id}")
        raise SessionBootstrapError("session secret could not be revoked")


def bootstrap_session(event, row: Dict[str, Any], mfa_enabled: bool, *, deadline: Optional[float] = None):
    """Mint the session key for the row's user and place it in the session secret, inside the mint
    budget. ``deadline`` is the ``time.monotonic()`` instant the sequence must be done by, derived by the
    caller from the time its Lambda has left; without one the module's own budget applies. Returns
    ``(outcome, attributes)``: the attributes to persist for ``provisioned`` (a fresh credential reads as
    unbound until ``bind_session_secret`` binds it), an empty dict for ``disabled`` (feature off, or a Windows row),
    ``skipped`` (the user holds no roles) or ``unavailable`` (a failure, logged) -- save the cleared
    secret attributes once a rollback has removed the session's container. The desktop lifecycle never
    depends on the outcome."""
    global _budget_deadline
    if not bootstrap_applies(row):
        return BOOTSTRAP_DISABLED, {}
    bootstrap_id = row["bootstrapId"]
    minted: Optional[Dict[str, str]] = None
    _budget_deadline = deadline if deadline is not None else _monotonic() + BOOTSTRAP_BUDGET_SECONDS
    try:
        base_url = resolve_vams_base_url()
        minted = mint_session_key(event, row["userId"], bootstrap_id, mfa_enabled)
        if minted is None:
            return BOOTSTRAP_SKIPPED, {}
        secret_arn = create_session_secret(bootstrap_id, {
            "apiKey": minted["apiKey"],
            "userId": row["userId"],
            "baseUrl": base_url,
            "expiresAt": minted["expiresAt"],
        })
    except Exception:
        # A SessionBootstrapError is the expected failure; anything else is logged the same way, because
        # the desktop lifecycle never depends on the credential.
        logger.exception(f"Session bootstrap unavailable for bootstrap {bootstrap_id}")
        if minted is not None:
            # The key exists but nothing can deliver it; take it back, and the container with it: no
            # start reuses the container of a launch that never received a credential. Only that key goes,
            # so no listing runs ahead of a start's EDH call. The revoke drops the budget first, so the
            # invoke gate never refuses the rollback's key delete.
            try:
                revoke_session(event, {**row, "apiKeyId": minted["apiKeyId"]},
                               delete_secret=True, actor=row["userId"], every_key=False)
            except SessionBootstrapError:
                logger.exception(f"Orphaned session key {minted['apiKeyId']} could not be revoked; the reconciler's "
                                 f"orphan sweep deletes it once it has expired, or once its desktop is no longer "
                                 f"live and it has aged past the orphan grace")
                # The container may still exist, so the row keeps naming it.
                return BOOTSTRAP_UNAVAILABLE, {}
            # The container the rollback removed is a name the row must stop carrying.
            return BOOTSTRAP_UNAVAILABLE, dict(CLEARED_SECRET_ATTRIBUTES)
        return BOOTSTRAP_UNAVAILABLE, {}
    finally:
        _budget_deadline = None
    return BOOTSTRAP_PROVISIONED, {
        "apiKeyId": minted["apiKeyId"],
        "secretArn": secret_arn,
        "secretName": session_secret_name(bootstrap_id),
        "keyExpiresAt": minted["expiresAt"],
        "secretBoundInstanceId": "",
    }


def mint_reissue_key(event, row: Dict[str, Any], mfa_enabled: bool, *,
                     deadline: Optional[float] = None) -> Tuple[str, Optional[Dict[str, str]]]:
    """Mint a fresh session key for the row's user under the re-issue name, inside the mint budget, and
    write it nowhere: the caller names it on the row before ``write_reissued_secret`` delivers it.
    ``deadline`` is as for ``bootstrap_session``. The key the row names is never taken back as stale.
    Returns ``(outcome, minted)``: ``{"apiKeyId", "apiKey", "expiresAt"}`` for ``provisioned``, and None
    for ``disabled`` (feature off, or a Windows row), ``skipped`` (the user holds no roles) or
    ``unavailable`` (a failure, logged; a base URL that cannot be resolved mints nothing)."""
    global _budget_deadline
    if not bootstrap_applies(row):
        return BOOTSTRAP_DISABLED, None
    bootstrap_id = row["bootstrapId"]
    retained = (str(row["apiKeyId"]),) if row.get("apiKeyId") else ()
    _budget_deadline = deadline if deadline is not None else _monotonic() + BOOTSTRAP_BUDGET_SECONDS
    try:
        resolve_vams_base_url()
        minted = mint_session_key(event, row["userId"], bootstrap_id, mfa_enabled,
                                  key_name=session_key_name(bootstrap_id, rotated_at=_now()),
                                  operation=AUDIT_OPERATION_REISSUE, retain_key_ids=retained)
    except Exception:
        logger.exception(f"Session key re-issue unavailable for bootstrap {bootstrap_id}")
        return BOOTSTRAP_UNAVAILABLE, None
    finally:
        _budget_deadline = None
    if minted is None:
        return BOOTSTRAP_SKIPPED, None
    return BOOTSTRAP_PROVISIONED, minted


def write_reissued_secret(row: Dict[str, Any], minted: Dict[str, str]) -> Dict[str, str]:
    """Write the re-issued key the row now names into the session secret. An existing container has its
    value overwritten and keeps the resource policy that binds it; one this write creates reads as unbound
    until ``bind_session_secret`` binds it. Returns the row attributes to persist; raises
    SessionBootstrapError when the value could not be written, having deleted nothing."""
    bootstrap_id = row["bootstrapId"]
    secret_arn, created = _write_session_secret(bootstrap_id, {
        "apiKey": minted["apiKey"],
        "userId": row["userId"],
        "baseUrl": resolve_vams_base_url(),
        "expiresAt": minted["expiresAt"],
    })
    attributes = {"secretArn": secret_arn, "secretName": session_secret_name(bootstrap_id)}
    if created:
        attributes["secretBoundInstanceId"] = ""
    return attributes


def list_session_keys(actor: str) -> List[Dict[str, Any]]:
    """Every API key record in the deployment whose name carries ``SESSION_KEY_NAME_PREFIX``, read
    through the apiKeyService listing as ``actor`` and followed through ``NextToken`` until the listing
    ends or the page cap is reached. The records carry neither the hash nor the plaintext: the listing
    route strips the hash and never held the key."""
    keys: List[Dict[str, Any]] = []
    query = dict(KEY_LISTING_PAGE_QUERY)
    for _ in range(KEY_LISTING_MAX_PAGES):
        status_code, body = _cross_call("GET", API_AUTH_API_KEYS.path, actor, False, query_parameters=query)
        if status_code != 200:
            logger.error(f"Session key listing failed: status {status_code}")
            raise SessionBootstrapError("session keys could not be listed")
        for item in body.get("Items", []):
            if isinstance(item, dict) and str(item.get("apiKeyName", "")).startswith(SESSION_KEY_NAME_PREFIX):
                keys.append(item)
        # The listing carries NextToken only while records remain.
        if "NextToken" not in body:
            return keys
        query = {**KEY_LISTING_PAGE_QUERY, "startingToken": str(body["NextToken"])}
    logger.warning(f"Session key listing stopped after {KEY_LISTING_MAX_PAGES} pages; the rest waits for the next run")
    return keys


def delete_orphan_key(event, key: Dict[str, Any], actor: str, operation: str = AUDIT_OPERATION_EXPIRE) -> None:
    """Delete a session key record that no session row names -- expired, aged past the reconciler's grace,
    or left by a mint whose reply was lost. The audit row carries the bootstrapId the key name encodes,
    so the deletion can be tied back to its launch, and ``operation`` names why the record went."""
    delete_session_key(event, str(key["apiKeyId"]), bootstrap_id=bootstrap_id_from_key_name(key.get("apiKeyName")),
                       user_id=key.get("userId", ""), actor=actor, operation=operation)
