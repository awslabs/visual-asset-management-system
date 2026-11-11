# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The request side of the automatic EDH root key sync.

EDH regenerates its web interface root key each time the web interface starts, including after every
controller reboot, and publishes the new key nowhere. When EDH rejects a request again after the client
re-read the root-key secret, the secret is out of date, and the EDH client calls ``request_root_key_sync``:
an asynchronous invoke of the ``edhRootKeySync`` function, which copies the current key from the controller
into the secret. The caller keeps its credential error; the next EDH call re-reads the secret.

The request is fire and forget, a single short attempt, and is made at most once per
``ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS`` per container; the sync function holds the deployment-wide rate
limit. Nothing is requested while ``EDH_ROOT_KEY_SYNC_FUNCTION_NAME`` is unset, which is how a deployment
with ``edhRootKeyAutoSync`` off is deployed. The payload carries no key and no request data.
"""

import json
import os
import time
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from customLogging.logger import safeLogger

logger = safeLogger(service_name="EdhRootKeySyncTrigger")

# The environment variable naming the sync function; absent or empty while the sync is off.
ROOT_KEY_SYNC_FUNCTION_ENV = "EDH_ROOT_KEY_SYNC_FUNCTION_NAME"
EDH_ROOT_KEY_SYNC_FUNCTION_NAME = os.environ.get(ROOT_KEY_SYNC_FUNCTION_ENV, "").strip()
# A sync runs at most once per this interval: requested at most once per container, and admitted at most
# once across the deployment by the sync function's conditional write on its settings row.
ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS = 300
# The only payload the sync function is sent; it names why the sync was requested and nothing else.
ROOT_KEY_SYNC_TRIGGER = "credentialRejected"
# One short attempt: the request runs inside the caller's EDH budget, and a lost request is made again on
# the next rejection.
_LAMBDA_CLIENT_CONFIG = BotoConfig(connect_timeout=2, read_timeout=3,
                                   retries={"total_max_attempts": 1, "mode": "standard"})

_lambda_client = None
# time.monotonic() of the last request this container made, or None.
_last_requested_at: Optional[float] = None


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda", config=_LAMBDA_CLIENT_CONFIG)
    return _lambda_client


def root_key_sync_enabled() -> bool:
    """Whether this deployment names a sync function."""
    return bool(EDH_ROOT_KEY_SYNC_FUNCTION_NAME)


def request_root_key_sync() -> bool:
    """Ask the sync function, asynchronously, to copy the current root key from the EDH controller.

    True when a sync stands requested: by this call, or by an earlier one of this container within the
    interval. False when the sync is off or the invoke failed; a failed invoke does not hold the interval,
    so the next rejection asks again. Never raises.
    """
    global _last_requested_at
    if not EDH_ROOT_KEY_SYNC_FUNCTION_NAME:
        return False
    now = time.monotonic()
    if _last_requested_at is not None and now - _last_requested_at < ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS:
        return True
    try:
        _get_lambda_client().invoke(
            FunctionName=EDH_ROOT_KEY_SYNC_FUNCTION_NAME,
            InvocationType="Event",
            Payload=json.dumps({"trigger": ROOT_KEY_SYNC_TRIGGER}).encode("utf-8"),
        )
    except (ClientError, BotoCoreError) as exc:
        logger.warning("EDH root key sync could not be requested", extra={"error": exc.__class__.__name__})
        return False
    _last_requested_at = now
    logger.info("EDH root key sync requested")
    return True


def _reset_for_tests() -> None:
    """Test hook: forget the last request and the Lambda client."""
    global _lambda_client, _last_requested_at
    _lambda_client = None
    _last_requested_at = None
