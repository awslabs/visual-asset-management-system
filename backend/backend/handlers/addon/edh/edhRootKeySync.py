# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Copies the EDH root key from the EDH controller into the root-key secret.

EDH regenerates its web interface root key each time the web interface starts, including after every
controller reboot, writes it to ``/opt/edh/<cluster id>/cluster_manager/web_interface/keys/
admin_api_key.txt`` on the controller and publishes it nowhere. An add-on Lambda whose EDH client met a
second 401 after re-reading the secret invokes this function asynchronously (``edhRootKeySyncTrigger``),
and an operator may invoke it directly (``{"source": "manual"}``); both run the same sync:

1. admit at most one sync per ``ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS`` across the deployment, and no automatic
   one while a backoff is in force, by a conditional write on the reserved ``SETTINGS#rootKeySync`` row of the
   session table;
2. read the controller's instance id from EDH's ``/edh/<cluster id>/configuration/ControllerInstanceId``
   parameter, and confirm that instance is running and carries EDH's controller tags for the cluster
   (``edh:NodeType`` = ``controller``, ``aws:cloudformation:stack-name`` = the cluster id);
3. generate an RSA key pair for this run (``RSA_KEY_SIZE`` bits, in this invocation's memory only) and run the
   deployment's own Run Command document, at its deployed version, on that instance with the public half
   (DER SubjectPublicKeyInfo, base64) as its only parameter. The document encrypts the key file to it with
   openssl (RSAES-OAEP, SHA-256 for the digest and MGF1) and prints the base64 ciphertext, so the command
   history holds no key;
4. decrypt the ciphertext with the run's private key (OAEP, SHA-256 for the digest and MGF1), then drop the
   key. Nothing but this invocation ever holds it, so the ciphertext left in the Run Command history cannot
   be decrypted afterwards by anyone. Accept the plaintext only when it is one 32-character alphanumeric key,
   the form EDH generates;
5. write ``{"rootKey": <key>}`` to the existing secret only when the secret holds another value. The secret
   keeps its previous version as AWSPREVIOUS. EDH writes the key file before its web interface starts
   (edhwebui.sh), so a rejected key equal to the file is a misconfiguration, such as an endpoint or cluster id
   of another cluster, not a web interface still starting: nothing is written, an automatic run counts it as a
   failure and backs the trigger off (twice the interval, doubling up to ``MAX_BACKOFF_SECONDS``; a written key
   clears it), and an administrator's manual run reports it without counting a failure. A written key stamps
   ``updatedAt`` on the row: an automatic trigger that finds the key unchanged within ``MAX_EVENT_AGE_SECONDS``
   of it may have been queued before that write (Lambda keeps a throttled event up to the function's maximum
   event age and may deliver one twice), so it counts no failure and sets no backoff.

An automatic trigger the backoff holds answers ``backedOff`` and counts a failure: EDH still rejects the key,
so a lasting misconfiguration keeps the failure metric's hourly sum up while the document runs rarely.

Every other answer writes nothing. The root key and the run's private key live only in local variables of
one invocation: neither is ever logged, returned or written anywhere, the root key but to the secret. Each
run logs one outcome code and writes one embedded-metric line counting a failure (1) or not (0), before it
writes the backoff or the ``updatedAt`` stamp, so a slow or failing bookkeeping write cannot cost the run its
outcome. The deployment creates no alarm on the metric; an operator may add one. The handler never raises,
since its invocations are asynchronous and not retried.
"""

import base64
import binascii
import hashlib
import json
import os
import re
import time
from decimal import Decimal

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext
# cryptography comes from the add-on's own layer (backendAddons/edh/rootKeySync/lambdaLayer), on this function only.
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import get_table_name
from customLogging.logger import safeLogger
from .common.edhRootKeySyncTrigger import ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS, ROOT_KEY_SYNC_TRIGGER

# Bounded clients: one call ends within (connect + read) x attempts = 26 s, so a call started with
# RUN_RESERVE_SECONDS left still leaves the run time to log its outcome before the function's timeout.
CLIENT_CONNECT_TIMEOUT_SECONDS = 3
CLIENT_READ_TIMEOUT_SECONDS = 10
CLIENT_TOTAL_ATTEMPTS = 2
sync_client_config = Config(connect_timeout=CLIENT_CONNECT_TIMEOUT_SECONDS, read_timeout=CLIENT_READ_TIMEOUT_SECONDS,
                            retries={"total_max_attempts": CLIENT_TOTAL_ATTEMPTS, "mode": "standard"})
logger = safeLogger(service_name="EdhRootKeySync")

# Module-level config, read once at cold start; a missing value aborts the import.
try:
    EDH_ADMIN_KEY_SECRET_ARN = os.environ["EDH_ADMIN_KEY_SECRET_ARN"]
    EDH_CLUSTER_ID = os.environ["EDH_CLUSTER_ID"]
    EDH_ROOT_KEY_DOCUMENT_NAME = os.environ["EDH_ROOT_KEY_DOCUMENT_NAME"]
    # The document version the deployment created; a version added to the document later is never run.
    EDH_ROOT_KEY_DOCUMENT_VERSION = os.environ["EDH_ROOT_KEY_DOCUMENT_VERSION"]
    # The SHA-256 of the document step's command lines, joined with newlines, as the stack built them.
    EDH_ROOT_KEY_DOCUMENT_STEP_SHA256 = os.environ["EDH_ROOT_KEY_DOCUMENT_STEP_SHA256"]
    SESSION_TABLE_NAME = get_table_name(AddonResourceKeys.EDH_SESSION_STORAGE_TABLE)
except Exception:
    logger.exception("Failed loading EDH root key sync configuration")
    raise

# EDH publishes its controller's instance id in its configuration parameters, as a plain String.
CONTROLLER_INSTANCE_ID_PARAMETER = f"/edh/{EDH_CLUSTER_ID}/configuration/ControllerInstanceId"
INSTANCE_ID_PATTERN = re.compile(r"^i-[0-9a-f]{8,17}$")
# The tags the controller carries: EDH's node type, and the stack name CloudFormation sets (an aws: tag,
# which no caller can write). The Run Command grant is conditioned on the same two.
CONTROLLER_TAGS = {"edh:NodeType": "controller", "aws:cloudformation:stack-name": EDH_CLUSTER_ID}
# The key EDH generates: 32 characters of [A-Za-z0-9] (edhwebui.sh, `tr -dc 'a-zA-Z0-9' | head -c 32`).
ROOT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9]{32}$")
ROOT_KEY_SECRET_FIELD = "rootKey"
# The run's own RSA key: generated for each run, held only in this invocation's memory. Its DER public half is 422
# bytes, 564 base64 characters, inside the publicKey parameter's 1000-character limit.
RSA_KEY_SIZE = 3072
RSA_PUBLIC_EXPONENT = 65537
# The padding the document's openssl call encrypts with: RSAES-OAEP, SHA-256 for the digest and for MGF1.
OAEP_SHA_256 = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)
# Base64, as the document's publicKey parameter admits it and as its output is printed.
BASE64_PATTERN = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
# The document's publicKey parameter, exactly as the stack declares it (EDH_ROOT_KEY_PUBLIC_KEY_PATTERN).
PUBLIC_KEY_ALLOWED_PATTERN = "^[A-Za-z0-9+/]{1,1000}={0,2}$"
PUBLIC_KEY_PATTERN = re.compile(PUBLIC_KEY_ALLOWED_PATTERN)
PUBLIC_KEY_PARAMETER_FIELDS = frozenset({"type", "description", "allowedPattern"})
DOCUMENT_HASH_TYPE = "Sha256"
# The session-table row the rate limit is held on. Like the lease settings row it cannot collide with a
# session row's UUID key, and it carries no state, userId or sessionId, so no session read or index yields it.
ROOT_KEY_SYNC_SETTINGS_KEY = "SETTINGS#rootKeySync"
# Admission: no sync started within the interval, a stamp further ahead than one interval being ignored as
# forged or skewed; for the automatic trigger also no backoff in force, one beyond its ceiling being ignored.
RATE_LIMIT_CONDITION = "(attribute_not_exists(syncStartedAt) OR syncStartedAt < :cutoff OR syncStartedAt > :ahead)"
BACKOFF_CONDITION = "(attribute_not_exists(retryAfter) OR retryAfter <= :now OR retryAfter > :ceiling)"
# After an automatic run that finds the key EDH rejected in the controller's key file, the next one waits twice
# the interval, then doubles, up to this many seconds. A written key clears it, whichever run wrote it.
MAX_BACKOFF_SECONDS = 6 * 3600
# The function's maximum event age (EDH_ROOT_KEY_SYNC_MAX_EVENT_AGE in edhWorkstationsFunctions.ts): a trigger
# queued before a written key can still arrive this long after it, delayed by throttling or delivered twice.
MAX_EVENT_AGE_SECONDS = 15 * 60
# How far ahead of this function's clock a recorded updatedAt may be and still be honoured; one further ahead can
# only be forged or skewed and is ignored, so it cannot keep failures from counting.
CLOCK_SKEW_SECONDS = 60
# Run Command: how long the command may wait for delivery, and how long this function waits for its result.
COMMAND_DELIVERY_TIMEOUT_SECONDS = 30
COMMAND_WAIT_SECONDS = 60
COMMAND_POLL_SECONDS = 2
COMMAND_TERMINAL_STATUSES = frozenset({"Success", "Failed", "Cancelled", "TimedOut"})
COMMAND_COMMENT = "VAMS EDH root key sync"
# No step starts with less than this much of the invocation left, so every run logs its outcome and writes its
# metric inside the function's timeout; a run with no context reads DEFAULT_RUN_SECONDS, the function timeout.
RUN_RESERVE_SECONDS = 30
DEFAULT_RUN_SECONDS = 120
# The labels a run may log for what invoked it; any other event is logged as "other" and never echoed.
TRIGGER_LABELS = frozenset({ROOT_KEY_SYNC_TRIGGER, "manual"})
# The invocation's request id, logged on the outcome line only in the form Lambda gives it, so the line can be
# matched to the AWS CloudTrail Invoke event that names the caller of a manual run.
REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

OUTCOME_UPDATED = "updated"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_INVALID = "invalid"
OUTCOME_COMMAND_FAILED = "commandFailed"
OUTCOME_COMMAND_TIMED_OUT = "commandTimedOut"
OUTCOME_CONTROLLER_NOT_FOUND = "controllerNotFound"
OUTCOME_CONTROLLER_NOT_VERIFIED = "controllerNotVerified"
OUTCOME_DOCUMENT_NOT_VERIFIED = "documentNotVerified"
# The run could not generate its RSA key pair, or its public half does not fit the document's parameter.
OUTCOME_KEY_UNAVAILABLE = "keyUnavailable"
OUTCOME_DECRYPT_FAILED = "decryptFailed"
OUTCOME_SECRET_READ_FAILED = "secretReadFailed"
OUTCOME_SECRET_WRITE_FAILED = "secretWriteFailed"
OUTCOME_RATE_LIMITED = "rateLimited"
OUTCOME_BACKED_OFF = "backedOff"
OUTCOME_OUT_OF_TIME = "outOfTime"
OUTCOME_ERROR = "error"
# Outcomes that are not a failure of the sync: a write, nothing to write, or a sync another run just made.
# Nothing to write is a failure after all when the automatic trigger asked (_is_failure), and so is an
# automatic run the backoff held (backedOff): EDH still rejects the key, so the metric keeps counting.
SUCCESS_OUTCOMES = frozenset({OUTCOME_UPDATED, OUTCOME_UNCHANGED, OUTCOME_RATE_LIMITED})
# The embedded-metric line each run writes, counting failures; the deployment creates no alarm on it.
METRIC_NAMESPACE = "VAMS/EdhWorkstations"
METRIC_FAILURES = "RootKeySyncFailures"
METRIC_DIMENSION = "FunctionName"

_ssm = None
_secretsmanager = None
_ec2 = None
_table = None


class _Deadline:
    """The time left in the invocation: the Lambda context's, or DEFAULT_RUN_SECONDS from the start without one."""

    def __init__(self, context):
        remaining = getattr(context, "get_remaining_time_in_millis", None)
        self._remaining_ms = remaining if callable(remaining) else None
        self._end = time.monotonic() + DEFAULT_RUN_SECONDS

    def remaining(self) -> float:
        if self._remaining_ms is not None:
            millis = self._remaining_ms()
            if isinstance(millis, (int, float)):
                return millis / 1000.0
        return self._end - time.monotonic()

    def check(self) -> None:
        """Ends the run outOfTime unless a step can still start and the outcome still be logged after it."""
        if self.remaining() < RUN_RESERVE_SECONDS:
            raise _SyncStop(OUTCOME_OUT_OF_TIME)


class _SyncStop(Exception):
    """Ends a sync with an outcome; carries the outcome code and no data."""

    def __init__(self, outcome: str):
        super().__init__(outcome)
        self.outcome = outcome


def _ssm_client():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm", config=sync_client_config)
    return _ssm


def _secretsmanager_client():
    global _secretsmanager
    if _secretsmanager is None:
        _secretsmanager = boto3.client("secretsmanager", config=sync_client_config)
    return _secretsmanager


def _ec2_client():
    global _ec2
    if _ec2 is None:
        _ec2 = boto3.client("ec2", config=sync_client_config)
    return _ec2


def _lock_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", config=sync_client_config).Table(SESSION_TABLE_NAME)
    return _table


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "ClientError"))
    return exc.__class__.__name__


def _backoff_in_force(item, now: int) -> bool:
    """Whether the row a refused admission returned (DynamoDB JSON) holds a backoff the condition honours."""
    try:
        retry_after = int(item["retryAfter"]["N"])
    except (KeyError, TypeError, ValueError):
        return False
    return now < retry_after <= now + MAX_BACKOFF_SECONDS


def _acquire_rate_limit(manual: bool, now: int) -> dict:
    """Admit this sync unless another started within the interval or, for the automatic trigger, a backoff is in
    force; a refused write ends it backedOff when a backoff held an automatic run, else rateLimited. Returns
    the row as written."""
    values = {":now": now, ":cutoff": now - ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS,
              ":ahead": now + ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS}
    condition = RATE_LIMIT_CONDITION
    if not manual:
        condition = f"{RATE_LIMIT_CONDITION} AND {BACKOFF_CONDITION}"
        values[":ceiling"] = now + MAX_BACKOFF_SECONDS
    try:
        response = _lock_table().update_item(
            Key={"bootstrapId": ROOT_KEY_SYNC_SETTINGS_KEY},
            UpdateExpression="SET syncStartedAt = :now",
            ConditionExpression=condition,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
            ReturnValuesOnConditionCheckFailure="ALL_OLD",
        )
        return response.get("Attributes") or {}
    except ClientError as exc:
        if _error_code(exc) == "ConditionalCheckFailedException":
            if not manual and _backoff_in_force(exc.response.get("Item"), now):
                raise _SyncStop(OUTCOME_BACKED_OFF)
            raise _SyncStop(OUTCOME_RATE_LIMITED)
        logger.warning("EDH root key sync rate limit could not be written", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_ERROR)
    except BotoCoreError as exc:
        logger.warning("EDH root key sync rate limit could not be written", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_ERROR)


def _controller_instance_id() -> str:
    try:
        value = _ssm_client().get_parameter(Name=CONTROLLER_INSTANCE_ID_PARAMETER)["Parameter"]["Value"]
    except (ClientError, BotoCoreError, KeyError, TypeError) as exc:
        logger.warning("EDH controller instance id could not be read", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_CONTROLLER_NOT_FOUND)
    instance_id = str(value).strip()
    if not INSTANCE_ID_PATTERN.match(instance_id):
        logger.warning("EDH controller instance id parameter holds no instance id")
        raise _SyncStop(OUTCOME_CONTROLLER_NOT_FOUND)
    return instance_id


def _verify_controller(instance_id: str) -> None:
    """The instance EDH publishes must be running and carry the controller tags of this cluster, before
    anything is sent to it; a mis-tagged or replaced instance is never targeted."""
    try:
        reservations = _ec2_client().describe_instances(InstanceIds=[instance_id]).get("Reservations") or []
    except (ClientError, BotoCoreError) as exc:
        logger.warning("EDH controller instance could not be read", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_CONTROLLER_NOT_VERIFIED)
    instances = [i for r in reservations for i in (r.get("Instances") or [])]
    if len(instances) != 1 or instances[0].get("InstanceId") != instance_id:
        logger.warning("EDH controller instance is not the one EDH publishes")
        raise _SyncStop(OUTCOME_CONTROLLER_NOT_VERIFIED)
    instance = instances[0]
    tags = {tag.get("Key"): tag.get("Value") for tag in (instance.get("Tags") or [])}
    if (instance.get("State") or {}).get("Name") != "running" or any(
            tags.get(key) != value for key, value in CONTROLLER_TAGS.items()):
        logger.warning("EDH controller instance is not a running controller of this cluster")
        raise _SyncStop(OUTCOME_CONTROLLER_NOT_VERIFIED)


def _generate_private_key():
    """This run's RSA private key. Nothing outside the run that generates it ever holds it."""
    return rsa.generate_private_key(public_exponent=RSA_PUBLIC_EXPONENT, key_size=RSA_KEY_SIZE)


def _new_key_pair():
    """This run's private key and the base64 DER public half the document is sent. A key that cannot be generated
    or encoded, or whose public half does not fit the document's parameter, ends the run keyUnavailable; the error
    is named by type only."""
    try:
        private_key = _generate_private_key()
        public_der = private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        public_key = base64.b64encode(public_der).decode("ascii")
    except Exception as exc:
        # Any failure to generate or encode the key ends the run. Named by type only, like every error here.
        logger.warning("EDH root key sync key pair could not be generated", extra={"error": exc.__class__.__name__})
        raise _SyncStop(OUTCOME_KEY_UNAVAILABLE)
    if not PUBLIC_KEY_PATTERN.match(public_key):
        logger.warning("EDH root key sync public key does not fit the document's parameter")
        raise _SyncStop(OUTCOME_KEY_UNAVAILABLE)
    return private_key, public_key


def _document_is_the_deployed_one(content: str) -> bool:
    """Whether the document content is the one the stack built: one validated publicKey parameter without a
    default, and one shell step whose command lines hash to the deployed value."""
    try:
        document = json.loads(content)
        parameters = document["parameters"]
        steps = document["mainSteps"]
        public_key = parameters["publicKey"]
        run_command = steps[0]["inputs"]["runCommand"]
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return (document.get("schemaVersion") == "2.2"
            and isinstance(parameters, dict) and set(parameters) == {"publicKey"}
            and isinstance(public_key, dict) and set(public_key) <= PUBLIC_KEY_PARAMETER_FIELDS
            and public_key.get("allowedPattern") == PUBLIC_KEY_ALLOWED_PATTERN
            and isinstance(steps, list) and len(steps) == 1 and steps[0].get("action") == "aws:runShellScript"
            and isinstance(run_command, list) and all(isinstance(line, str) for line in run_command)
            and hashlib.sha256("\n".join(run_command).encode("utf-8")).hexdigest()
            == EDH_ROOT_KEY_DOCUMENT_STEP_SHA256)


def _document_hash(run: _Deadline) -> str:
    """The hash of the pinned document version, once its content is confirmed to be the deployed step. The
    hash goes to SendCommand, so Systems Manager refuses a document recreated under the name since."""
    try:
        described = _ssm_client().describe_document(
            Name=EDH_ROOT_KEY_DOCUMENT_NAME, DocumentVersion=EDH_ROOT_KEY_DOCUMENT_VERSION)["Document"]
        run.check()
        fetched = _ssm_client().get_document(
            Name=EDH_ROOT_KEY_DOCUMENT_NAME, DocumentVersion=EDH_ROOT_KEY_DOCUMENT_VERSION, DocumentFormat="JSON")
    except (ClientError, BotoCoreError, KeyError, TypeError) as exc:
        logger.warning("EDH root key document could not be read", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_DOCUMENT_NOT_VERIFIED)
    document_hash = described.get("Hash")
    same_version = (described.get("DocumentVersion") == fetched.get("DocumentVersion") == EDH_ROOT_KEY_DOCUMENT_VERSION
                    and described.get("CreatedDate") == fetched.get("CreatedDate"))
    if (not document_hash or described.get("HashType") != DOCUMENT_HASH_TYPE or not same_version
            or described.get("Status") != "Active" or fetched.get("Status") != "Active"
            or not _document_is_the_deployed_one(fetched.get("Content"))):
        logger.warning("EDH root key document is not the one the deployment created")
        raise _SyncStop(OUTCOME_DOCUMENT_NOT_VERIFIED)
    return str(document_hash)


def _send_command(instance_id: str, public_key: str, document_hash: str) -> str:
    """Run the document on the controller, with the public key as its one parameter. Its output stays with
    the invocation: no S3 bucket or CloudWatch Logs group is named."""
    try:
        response = _ssm_client().send_command(
            DocumentName=EDH_ROOT_KEY_DOCUMENT_NAME,
            DocumentVersion=EDH_ROOT_KEY_DOCUMENT_VERSION,
            DocumentHash=document_hash,
            DocumentHashType=DOCUMENT_HASH_TYPE,
            InstanceIds=[instance_id],
            Parameters={"publicKey": [public_key]},
            TimeoutSeconds=COMMAND_DELIVERY_TIMEOUT_SECONDS,
            Comment=COMMAND_COMMENT,
        )
        return response["Command"]["CommandId"]
    except (ClientError, BotoCoreError, KeyError, TypeError) as exc:
        logger.warning("EDH root key command could not be sent", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_COMMAND_FAILED)


def _command_output(command_id: str, instance_id: str, run: _Deadline) -> str:
    """The standard output of a command that succeeded, read within ``COMMAND_WAIT_SECONDS`` and never past
    the run's reserve."""
    deadline = time.monotonic() + min(COMMAND_WAIT_SECONDS, run.remaining() - RUN_RESERVE_SECONDS)
    while True:
        try:
            invocation = _ssm_client().get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ClientError as exc:
            # Run Command registers the invocation shortly after SendCommand answers.
            if _error_code(exc) != "InvocationDoesNotExist":
                logger.warning("EDH root key command could not be read", extra={"error": _error_code(exc)})
                raise _SyncStop(OUTCOME_COMMAND_FAILED)
            invocation = {}
        except BotoCoreError as exc:
            logger.warning("EDH root key command could not be read", extra={"error": _error_code(exc)})
            raise _SyncStop(OUTCOME_COMMAND_FAILED)
        status = invocation.get("Status")
        if status in COMMAND_TERMINAL_STATUSES:
            if status != "Success":
                logger.warning("EDH root key command did not succeed", extra={"commandStatus": status})
                raise _SyncStop(OUTCOME_COMMAND_FAILED)
            return str(invocation.get("StandardOutputContent") or "")
        if time.monotonic() + COMMAND_POLL_SECONDS > deadline:
            logger.warning("EDH root key command did not finish in time", extra={"commandStatus": status})
            raise _SyncStop(OUTCOME_COMMAND_TIMED_OUT)
        # Pause between reads of the invocation, bounded by the wait above. Must be the last comment line
        # before the call -- semgrep attaches `nosemgrep` to the next line only.
        # nosemgrep: arbitrary-sleep
        time.sleep(COMMAND_POLL_SECONDS)


def _decrypt_key(output: str, private_key) -> str:
    """The key the command's ciphertext carries, decrypted with this run's private key; output that is not one
    RSA block of ciphertext, or a plaintext that is not one key, is invalid. Neither the output, the plaintext nor
    the private key is logged, and a failed decrypt is named by type only."""
    text = output.strip()
    if not BASE64_PATTERN.match(text):
        raise _SyncStop(OUTCOME_INVALID)
    try:
        ciphertext = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise _SyncStop(OUTCOME_INVALID)
    # RSAES-OAEP ciphertext is exactly one modulus long: 384 bytes for the run's 3072-bit key.
    if len(ciphertext) != private_key.key_size // 8:
        raise _SyncStop(OUTCOME_INVALID)
    try:
        plaintext = private_key.decrypt(ciphertext, OAEP_SHA_256)
    except (ValueError, TypeError) as exc:
        logger.warning("EDH root key ciphertext could not be decrypted", extra={"error": exc.__class__.__name__})
        raise _SyncStop(OUTCOME_DECRYPT_FAILED)
    try:
        key = plaintext.decode("ascii").strip()
    except (AttributeError, UnicodeDecodeError):
        raise _SyncStop(OUTCOME_INVALID)
    if not ROOT_KEY_PATTERN.match(key):
        raise _SyncStop(OUTCOME_INVALID)
    return key


def _stored_key():
    """The rootKey the secret holds, or None when its value is not the documented JSON object."""
    try:
        secret_string = _secretsmanager_client().get_secret_value(SecretId=EDH_ADMIN_KEY_SECRET_ARN)["SecretString"]
    except (ClientError, BotoCoreError, KeyError, TypeError) as exc:
        logger.warning("EDH root key secret could not be read", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_SECRET_READ_FAILED)
    try:
        payload = json.loads(secret_string)
    except (TypeError, ValueError):
        return None
    stored = payload.get(ROOT_KEY_SECRET_FIELD) if isinstance(payload, dict) else None
    return stored if isinstance(stored, str) else None


def _write_key(key: str) -> None:
    try:
        _secretsmanager_client().put_secret_value(
            SecretId=EDH_ADMIN_KEY_SECRET_ARN,
            SecretString=json.dumps({ROOT_KEY_SECRET_FIELD: key}),
        )
    except (ClientError, BotoCoreError) as exc:
        logger.warning("EDH root key secret could not be written", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_SECRET_WRITE_FAILED)


def _row_time(row: dict, field: str):
    """A whole-second number the row holds under ``field``, or None for anything else."""
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    try:
        return int(value)
    except (ArithmeticError, ValueError):
        return None


def _queued_before_the_last_write(row: dict, admitted_at: int) -> bool:
    """Whether a key was written recently enough that this run may be a trigger queued before that write: the
    row's updatedAt no more than MAX_EVENT_AGE_SECONDS before the admission and no more than CLOCK_SKEW_SECONDS
    after it."""
    updated_at = _row_time(row, "updatedAt")
    if updated_at is None:
        return False
    return admitted_at - MAX_EVENT_AGE_SECONDS <= updated_at <= admitted_at + CLOCK_SKEW_SECONDS


def _record_run(row: dict, outcome: str, manual: bool, queued_before_write: bool) -> None:
    """After a run's outcome is logged: a written key stamps updatedAt and clears the backoff; an automatic run
    that left the rejected key in place, unless it may have been queued before the last write, backs the trigger
    off. A bookkeeping write that fails is logged and changes no outcome."""
    try:
        if outcome == OUTCOME_UPDATED:
            _lock_table().update_item(
                Key={"bootstrapId": ROOT_KEY_SYNC_SETTINGS_KEY},
                UpdateExpression="SET updatedAt = :now REMOVE unchangedStreak, retryAfter",
                ExpressionAttributeValues={":now": int(time.time())},
            )
        elif outcome == OUTCOME_UNCHANGED and not manual and not queued_before_write:
            streak = max(_row_time(row, "unchangedStreak") or 0, 0) + 1
            wait = min(ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS * 2 ** min(streak, 32), MAX_BACKOFF_SECONDS)
            _lock_table().update_item(
                Key={"bootstrapId": ROOT_KEY_SYNC_SETTINGS_KEY},
                UpdateExpression="SET unchangedStreak = :streak, retryAfter = :retry",
                ExpressionAttributeValues={":streak": streak, ":retry": int(time.time()) + wait},
            )
    except (ClientError, BotoCoreError) as exc:
        logger.warning("EDH root key sync backoff could not be written", extra={"error": _error_code(exc)})


def _copy_key(run: _Deadline) -> str:
    """The steps after admission: find and check the controller, check the document, generate the run's key pair,
    run the document, decrypt, compare, write."""
    run.check()
    instance_id = _controller_instance_id()
    run.check()
    _verify_controller(instance_id)
    run.check()
    document_hash = _document_hash(run)
    run.check()
    private_key, public_key = _new_key_pair()
    try:
        run.check()
        command_id = _send_command(instance_id, public_key, document_hash)
        output = _command_output(command_id, instance_id, run)
        run.check()
        key = _decrypt_key(output, private_key)
    finally:
        # The private key is dropped once the ciphertext is decrypted or the run ends without it, so nothing
        # can decrypt the ciphertext the Run Command history keeps.
        del private_key
    run.check()
    stored = _stored_key()
    if stored == key:
        return OUTCOME_UNCHANGED
    run.check()
    _write_key(key)
    return OUTCOME_UPDATED


def _write_metric_line(line: str) -> None:
    """One CloudWatch embedded-metric-format line on standard output, which Lambda delivers to its log group."""
    print(line, flush=True)


def _is_failure(outcome: str, manual: bool, queued_before_write: bool = False) -> bool:
    """A failure, as the metric counts it: every outcome but a write, a sync another run just made, or nothing to
    write; and nothing to write after a rejected key too, unless an administrator ran the sync or the trigger may
    have been queued before the last write."""
    return outcome not in SUCCESS_OUTCOMES or (
        outcome == OUTCOME_UNCHANGED and not manual and not queued_before_write)


def _emit_outcome_metric(outcome: str, failure: bool) -> None:
    failures = 1 if failure else 0
    _write_metric_line(json.dumps({
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE,
                "Dimensions": [[METRIC_DIMENSION]],
                "Metrics": [{"Name": METRIC_FAILURES, "Unit": "Count"}],
            }],
        },
        METRIC_DIMENSION: os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "edhRootKeySync"),
        METRIC_FAILURES: failures,
        "outcome": outcome,
    }))


def _trigger_label(event) -> str:
    label = None
    if isinstance(event, dict):
        label = event.get("trigger") or event.get("source")
    return label if label in TRIGGER_LABELS else "other"


def _request_id(context):
    value = getattr(context, "aws_request_id", None)
    return value if isinstance(value, str) and REQUEST_ID_PATTERN.match(value) else None


def lambda_handler(event, context: LambdaContext):
    """One sync, whatever invoked it; the event is read for a label from a fixed set and nothing else. The
    answer is the outcome code alone."""
    trigger = _trigger_label(event)
    manual = trigger == "manual"
    run = _Deadline(context)
    row = None
    admitted_at = 0
    try:
        run.check()
        admitted_at = int(time.time())
        row = _acquire_rate_limit(manual, admitted_at)
        outcome = _copy_key(run)
    except _SyncStop as stop:
        outcome = stop.outcome
    except Exception as exc:
        # Named by type only: the message of an unexpected failure could carry the key.
        logger.warning("EDH root key sync failed unexpectedly", extra={"error": exc.__class__.__name__})
        outcome = OUTCOME_ERROR
    queued_before_write = (outcome == OUTCOME_UNCHANGED and row is not None
                           and _queued_before_the_last_write(row, admitted_at))
    failure = _is_failure(outcome, manual, queued_before_write)
    finished = {"outcome": outcome, "trigger": trigger, "failure": failure}
    if queued_before_write:
        finished["afterRecentUpdate"] = True
    request_id = _request_id(context)
    if request_id is not None:
        finished["requestId"] = request_id
    logger.info("EDH root key sync finished", extra=finished)
    _emit_outcome_metric(outcome, failure)
    if row is not None:
        try:
            _record_run(row, outcome, manual, queued_before_write)
        except Exception as exc:
            logger.warning("EDH root key sync bookkeeping failed unexpectedly", extra={"error": exc.__class__.__name__})
    return {"outcome": outcome}
