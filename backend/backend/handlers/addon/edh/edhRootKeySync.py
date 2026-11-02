# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Copies the EDH root key from the EDH controller into the root-key secret.

EDH regenerates its web interface root key each time the web interface starts, including after every
controller reboot, writes it to ``/opt/edh/<cluster id>/cluster_manager/web_interface/keys/
admin_api_key.txt`` on the controller and publishes it nowhere. An add-on Lambda whose EDH client met a
second 401 after re-reading the secret invokes this function asynchronously (``edhRootKeySyncTrigger``),
and an operator may invoke it directly (``{"source": "manual"}``); both run the same sync:

1. admit at most one sync per ``ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS`` across the deployment, by a
   conditional write on the reserved ``SETTINGS#rootKeySync`` row of the session table;
2. read the controller's instance id from EDH's ``/edh/<cluster id>/configuration/ControllerInstanceId``
   parameter, and confirm that instance is running and carries EDH's controller tags for the cluster
   (``edh:NodeType`` = ``controller``, ``aws:cloudformation:stack-name`` = the cluster id);
3. read the public half of the deployment's AWS KMS RSA key, an ENCRYPT_DECRYPT key offering
   ``RSAES_OAEP_SHA_256``;
4. run the deployment's own Run Command document, at its deployed version, on that instance with the
   public key as its only parameter. The document encrypts the key file to it with openssl (RSAES-OAEP,
   SHA-256 for the digest and MGF1) and prints the base64 ciphertext, so the command history holds no key;
5. decrypt the ciphertext with ``kms:Decrypt`` (``RSAES_OAEP_SHA_256``), which only this function's role
   may call, and accept the plaintext only when it is one 32-character alphanumeric key, the form EDH
   generates;
6. write ``{"rootKey": <key>}`` to the existing secret only when the secret holds another value. The same
   key EDH has just rejected means the web interface may still be starting, so nothing is written and a
   later rejection tries again. The secret keeps its previous version as AWSPREVIOUS.

Every other answer writes nothing. The key lives only in local variables of one invocation: it is never
logged, returned, or written anywhere but the secret. Each run logs one outcome code and writes one
embedded-metric line counting a failure (1) or not (0), which the add-on's alarm reads. The handler never
raises, since its invocations are asynchronous and not retried.
"""

import base64
import binascii
import json
import os
import re
import time

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from aws_lambda_powertools.utilities.typing import LambdaContext

from common.addonResourceNames import AddonResourceKeys
from common.resourceNames import get_table_name
from customLogging.logger import safeLogger
from .common.edhRootKeySyncTrigger import ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS, ROOT_KEY_SYNC_TRIGGER

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})
logger = safeLogger(service_name="EdhRootKeySync")

# Module-level config, read once at cold start; a missing value aborts the import.
try:
    EDH_ADMIN_KEY_SECRET_ARN = os.environ["EDH_ADMIN_KEY_SECRET_ARN"]
    EDH_CLUSTER_ID = os.environ["EDH_CLUSTER_ID"]
    EDH_ROOT_KEY_DOCUMENT_NAME = os.environ["EDH_ROOT_KEY_DOCUMENT_NAME"]
    # The document version the deployment created; a version added to the document later is never run.
    EDH_ROOT_KEY_DOCUMENT_VERSION = os.environ["EDH_ROOT_KEY_DOCUMENT_VERSION"]
    EDH_ROOT_KEY_ENCRYPTION_KEY_ARN = os.environ["EDH_ROOT_KEY_ENCRYPTION_KEY_ARN"]
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
# The KMS algorithm the document's openssl call matches: RSAES-OAEP, SHA-256 for the digest and for MGF1.
ENCRYPTION_ALGORITHM = "RSAES_OAEP_SHA_256"
# Base64, as the document's publicKey parameter admits it and as its output is printed.
BASE64_PATTERN = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
# The session-table row the rate limit is held on. Like the lease settings row it cannot collide with a
# session row's UUID key, and it carries no state, userId or sessionId, so no session read or index yields it.
ROOT_KEY_SYNC_SETTINGS_KEY = "SETTINGS#rootKeySync"
# Run Command: how long the command may wait for delivery, and how long this function waits for its result.
COMMAND_DELIVERY_TIMEOUT_SECONDS = 30
COMMAND_WAIT_SECONDS = 60
COMMAND_POLL_SECONDS = 2
COMMAND_TERMINAL_STATUSES = frozenset({"Success", "Failed", "Cancelled", "TimedOut"})
COMMAND_COMMENT = "VAMS EDH root key sync"
# The labels a run may log for what invoked it; any other event is logged as "other" and never echoed.
TRIGGER_LABELS = frozenset({ROOT_KEY_SYNC_TRIGGER, "manual"})

OUTCOME_UPDATED = "updated"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_INVALID = "invalid"
OUTCOME_COMMAND_FAILED = "commandFailed"
OUTCOME_COMMAND_TIMED_OUT = "commandTimedOut"
OUTCOME_CONTROLLER_NOT_FOUND = "controllerNotFound"
OUTCOME_CONTROLLER_NOT_VERIFIED = "controllerNotVerified"
OUTCOME_KEY_UNAVAILABLE = "keyUnavailable"
OUTCOME_DECRYPT_FAILED = "decryptFailed"
OUTCOME_SECRET_READ_FAILED = "secretReadFailed"
OUTCOME_SECRET_WRITE_FAILED = "secretWriteFailed"
OUTCOME_RATE_LIMITED = "rateLimited"
OUTCOME_ERROR = "error"
# Outcomes that are not a failure of the sync: a write, nothing to write, or a sync another run just made.
SUCCESS_OUTCOMES = frozenset({OUTCOME_UPDATED, OUTCOME_UNCHANGED, OUTCOME_RATE_LIMITED})
# The embedded-metric line each run writes; the add-on's alarm reads this namespace, metric and dimension.
METRIC_NAMESPACE = "VAMS/EdhWorkstations"
METRIC_FAILURES = "RootKeySyncFailures"
METRIC_DIMENSION = "FunctionName"

_ssm = None
_secretsmanager = None
_kms = None
_ec2 = None
_table = None


class _SyncStop(Exception):
    """Ends a sync with an outcome; carries the outcome code and no data."""

    def __init__(self, outcome: str):
        super().__init__(outcome)
        self.outcome = outcome


def _ssm_client():
    global _ssm
    if _ssm is None:
        _ssm = boto3.client("ssm", config=retry_config)
    return _ssm


def _secretsmanager_client():
    global _secretsmanager
    if _secretsmanager is None:
        _secretsmanager = boto3.client("secretsmanager", config=retry_config)
    return _secretsmanager


def _kms_client():
    global _kms
    if _kms is None:
        _kms = boto3.client("kms", config=retry_config)
    return _kms


def _ec2_client():
    global _ec2
    if _ec2 is None:
        _ec2 = boto3.client("ec2", config=retry_config)
    return _ec2


def _lock_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", config=retry_config).Table(SESSION_TABLE_NAME)
    return _table


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        return str(exc.response.get("Error", {}).get("Code", "ClientError"))
    return exc.__class__.__name__


def _acquire_rate_limit() -> None:
    """Admit this sync unless another started within the interval; a refused write ends it rateLimited."""
    now = int(time.time())
    try:
        _lock_table().update_item(
            Key={"bootstrapId": ROOT_KEY_SYNC_SETTINGS_KEY},
            UpdateExpression="SET syncStartedAt = :now",
            ConditionExpression="attribute_not_exists(syncStartedAt) OR syncStartedAt < :cutoff",
            ExpressionAttributeValues={":now": now, ":cutoff": now - ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS},
        )
    except ClientError as exc:
        if _error_code(exc) == "ConditionalCheckFailedException":
            raise _SyncStop(OUTCOME_RATE_LIMITED)
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


def _public_key() -> str:
    """The base64 DER public key of the encryption key, which must decrypt with RSAES_OAEP_SHA_256."""
    try:
        response = _kms_client().get_public_key(KeyId=EDH_ROOT_KEY_ENCRYPTION_KEY_ARN)
        usable = (response.get("KeyUsage") == "ENCRYPT_DECRYPT"
                  and ENCRYPTION_ALGORITHM in (response.get("EncryptionAlgorithms") or []))
        public_key = base64.b64encode(response["PublicKey"]).decode("ascii") if usable else ""
    except (ClientError, BotoCoreError, KeyError, TypeError) as exc:
        logger.warning("EDH root key encryption key could not be read", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_KEY_UNAVAILABLE)
    if not BASE64_PATTERN.match(public_key):
        logger.warning("EDH root key encryption key does not offer RSAES_OAEP_SHA_256 encryption")
        raise _SyncStop(OUTCOME_KEY_UNAVAILABLE)
    return public_key


def _send_command(instance_id: str, public_key: str) -> str:
    """Run the document on the controller, with the public key as its one parameter. Its output stays with
    the invocation: no S3 bucket or CloudWatch Logs group is named."""
    try:
        response = _ssm_client().send_command(
            DocumentName=EDH_ROOT_KEY_DOCUMENT_NAME,
            DocumentVersion=EDH_ROOT_KEY_DOCUMENT_VERSION,
            InstanceIds=[instance_id],
            Parameters={"publicKey": [public_key]},
            TimeoutSeconds=COMMAND_DELIVERY_TIMEOUT_SECONDS,
            Comment=COMMAND_COMMENT,
        )
        return response["Command"]["CommandId"]
    except (ClientError, BotoCoreError, KeyError, TypeError) as exc:
        logger.warning("EDH root key command could not be sent", extra={"error": _error_code(exc)})
        raise _SyncStop(OUTCOME_COMMAND_FAILED)


def _command_output(command_id: str, instance_id: str) -> str:
    """The standard output of a command that succeeded, read within ``COMMAND_WAIT_SECONDS``."""
    deadline = time.monotonic() + COMMAND_WAIT_SECONDS
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


def _decrypt_key(output: str) -> str:
    """The key the command's ciphertext carries; output that is not ciphertext, or a plaintext that is not
    one key, is invalid. Neither the output nor the plaintext is logged."""
    text = output.strip()
    if not BASE64_PATTERN.match(text):
        raise _SyncStop(OUTCOME_INVALID)
    try:
        ciphertext = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise _SyncStop(OUTCOME_INVALID)
    try:
        plaintext = _kms_client().decrypt(
            KeyId=EDH_ROOT_KEY_ENCRYPTION_KEY_ARN,
            CiphertextBlob=ciphertext,
            EncryptionAlgorithm=ENCRYPTION_ALGORITHM,
        )["Plaintext"]
    except (ClientError, BotoCoreError, KeyError, TypeError) as exc:
        logger.warning("EDH root key ciphertext could not be decrypted", extra={"error": _error_code(exc)})
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


def _sync() -> str:
    _acquire_rate_limit()
    instance_id = _controller_instance_id()
    _verify_controller(instance_id)
    public_key = _public_key()
    command_id = _send_command(instance_id, public_key)
    key = _decrypt_key(_command_output(command_id, instance_id))
    if _stored_key() == key:
        return OUTCOME_UNCHANGED
    _write_key(key)
    return OUTCOME_UPDATED


def _write_metric_line(line: str) -> None:
    """One CloudWatch embedded-metric-format line on standard output, which Lambda delivers to its log group."""
    print(line, flush=True)


def _emit_outcome_metric(outcome: str) -> None:
    failures = 0 if outcome in SUCCESS_OUTCOMES else 1
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


def lambda_handler(event, context: LambdaContext):
    """One sync, whatever invoked it; the event is read for a label from a fixed set and nothing else. The
    answer is the outcome code alone."""
    trigger = _trigger_label(event)
    try:
        outcome = _sync()
    except _SyncStop as stop:
        outcome = stop.outcome
    except Exception as exc:
        # Named by type only: the message of an unexpected failure could carry the key.
        logger.warning("EDH root key sync failed unexpectedly", extra={"error": exc.__class__.__name__})
        outcome = OUTCOME_ERROR
    logger.info("EDH root key sync finished", extra={"outcome": outcome, "trigger": trigger})
    _emit_outcome_metric(outcome)
    return {"outcome": outcome}
