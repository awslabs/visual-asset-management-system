# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded, cached reads of single AWS Systems Manager Parameter Store parameters.

Which lookup to use:

* A resource name VAMS creates at deploy time (a table, bucket, log group or function name under the
  deployment's resource-name prefix) is resolved with ``common.resourceNames.get_resource_name()``,
  which loads every registered name for the container from the resource-name chunk parameters.
* A parameter read by its full name goes through this module: a value VAMS does not create (an
  AWS-published or add-on-owned parameter), a value that can change after the deployment, or a
  deploy-time value the chunks do not carry. The parameter name comes from the environment the
  deployment sets; this module knows nothing about what a value means.

Plain String parameters only. Nothing here asks Parameter Store to decrypt: a secret is read from
AWS Secrets Manager, never through this module, so nothing it caches is a secret.

Under throttling a read costs what a resource-name lookup costs, never Rule 6's five adaptive
attempts (about 30 s):

* One read retries throttling, 5xx responses, and the transport failures the resolver retries, with
  jittered sleeps inside ``PARAMETER_READ_BUDGET_SECONDS``; no call starts and no sleep runs past
  the budget. The client has short timeouts and no botocore retries, so one call adds at most its
  own timeout. The error classification is the resolver's (``common.resourceNames``), so both treat
  an error alike.
* A read that fails on a retryable error opens a back-off window for the whole container (a
  throttle is account- and Region-wide): no parameter is read until it passes, a cached value is
  served, stale or not, and a parameter not cached is refused with ``ParameterUnavailableError``
  without a call. A read that succeeds closes the window.
* Any other error (access denied, validation, missing credentials) raises ``ParameterReadError`` at
  once and opens no window, since a retry meets the same refusal. A cached value is still served.
* ``ParameterNotFound`` raises ``ParameterNotFoundError`` (a ``KeyError``) and is remembered for
  ``MISSING_PARAMETER_TTL_SECONDS``.
* A value is cached per container for its TTL. A cache hit takes no lock and makes no call; one read
  runs at a time per container.

Each failed read logs one line (error code, calls, elapsed time, parameter name), and the refused
lookups of a back-off window log one warning. A parameter value is never logged.
"""

import random
import threading
import time

import boto3
from botocore.config import Config
from common.resourceNames import _error_code, _is_retryable
from customLogging.logger import safeLogger

logger = safeLogger()

# The read client. A read can run during Lambda init (10 s limit) or inside an API request, so the
# client is bounded rather than given Rule 6's five adaptive attempts: one-second connect and
# two-second read timeouts and no botocore retries. The read loop in _read owns the retries, under
# PARAMETER_READ_BUDGET_SECONDS, so no SDK back-off sleep can carry a read past its budget.
parameter_client_config = Config(connect_timeout=1, read_timeout=2, retries={'max_attempts': 0, 'mode': 'standard'})

# Wall-clock budget of one read, its retries and their sleeps included.
PARAMETER_READ_BUDGET_SECONDS = 4.0

# How long a value read with get_parameter_value is served before it is read again (the default; a
# caller whose value can change after the deployment passes a shorter ttl_seconds).
CACHE_TTL_SECONDS = 3600

# How long a parameter Parameter Store reported missing is answered as missing without a read.
MISSING_PARAMETER_TTL_SECONDS = 300

# Sleep before retrying a call: uniform(0, min(READ_RETRY_MAX_SLEEP_SECONDS,
# READ_RETRY_BASE_SECONDS * 2 ** k)) for the k-th retry in a row.
READ_RETRY_BASE_SECONDS = 0.1
READ_RETRY_MAX_SLEEP_SECONDS = 1.0

# After n failed reads in a row, the container reads no parameter for uniform(0.5, 1.0) *
# min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * 2 ** (n - 1)) seconds: about 2, 4, 8 and 16
# seconds, then 30. The jitter spreads containers that failed together.
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 30.0

# Clock, sleep and jitter the read loop, the cache and the back-off window use.
_monotonic = time.monotonic
_sleep = time.sleep
_uniform = random.uniform


class ParameterNotFoundError(KeyError):
    """Parameter Store reports that the parameter does not exist."""


class ParameterUnavailableError(RuntimeError):
    """The parameter could not be read because Parameter Store could not be reached in time: the
    read was still throttled (or failing transiently) when its budget ran out, or the lookup came
    inside the back-off window that follows such a failure. Not a KeyError: the parameter may well
    exist."""


class ParameterReadError(RuntimeError):
    """Parameter Store refused the read with an error a retry cannot fix, most often
    AccessDeniedException when the function's role may not call ssm:GetParameter on the parameter.
    Opens no back-off window."""


_ssm_client = None
# Parameter name -> (value, _monotonic() time it was read).
_cache = {}
# Parameter name -> _monotonic() time Parameter Store reported it missing.
_missing = {}

# Failed reads in a row, and the _monotonic() time before which no parameter is read.
_failed_reads = 0
_retry_after = 0.0
# The window (its _retry_after) whose refused lookups have been logged.
_window_logged_for = None

# One read at a time per container. Taken only when a lookup needs a read, so a cache hit never
# waits on it.
_read_lock = threading.Lock()

# What _answer_without_read returns when only a read can answer.
_NEEDS_READ = object()


def _get_ssm_client():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client('ssm', config=parameter_client_config)
    return _ssm_client


def _is_transient(error: Exception) -> bool:
    """A read that ran out of its budget on retryable errors (or on none, TimeoutError)."""
    return isinstance(error, TimeoutError) or _is_retryable(error)


def _read(name: str) -> str:
    """One GetParameter for ``name`` inside PARAMETER_READ_BUDGET_SECONDS. A retryable error is
    retried after a jittered sleep; any other error is raised at once. When the budget runs out the
    last error is raised, or TimeoutError when none was. Logs one line when the read fails."""
    client = _get_ssm_client()
    started = _monotonic()
    deadline = started + PARAMETER_READ_BUDGET_SECONDS
    calls = retries = 0
    last_error = None

    def failed(error: Exception) -> Exception:
        logger.error(
            f"Parameter Store read failed ({_error_code(error)}) after {calls} calls, "
            f"{_monotonic() - started:.2f}s: {name}")
        return error

    while True:
        if _monotonic() >= deadline:
            raise failed(last_error or TimeoutError(
                f"Parameter Store read did not finish within {PARAMETER_READ_BUDGET_SECONDS}s"))
        calls += 1
        try:
            return client.get_parameter(Name=name)["Parameter"]["Value"]
        except Exception as e:
            if _error_code(e) == "ParameterNotFound":
                raise
            if not _is_retryable(e):
                raise failed(e)
            last_error = e
            pause = _uniform(0, min(READ_RETRY_MAX_SLEEP_SECONDS, READ_RETRY_BASE_SECONDS * 2 ** retries))
            retries += 1
            if _monotonic() + pause >= deadline:
                raise failed(e)
            _sleep(pause)


def _record_failed_read():
    """Open the back-off window: no parameter is read in this container until it has passed."""
    global _failed_reads, _retry_after
    _failed_reads += 1
    window = BACKOFF_BASE_SECONDS * 2 ** min(_failed_reads - 1, 16)
    _retry_after = _monotonic() + _uniform(0.5, 1.0) * min(BACKOFF_MAX_SECONDS, window)


def _record_completed_read():
    global _failed_reads, _retry_after
    _failed_reads = 0
    _retry_after = 0.0


def _in_backoff_window() -> bool:
    return _failed_reads > 0 and _monotonic() < _retry_after


def _log_backoff_window_once():
    """One WARNING per back-off window for the lookups it answers without a read."""
    global _window_logged_for
    if _window_logged_for == _retry_after:
        return
    _window_logged_for = _retry_after
    logger.warning(
        f"Parameter Store lookups are answered from the cache only for {_retry_after - _monotonic():.1f}s "
        f"after {_failed_reads} failed read(s), and parameters not cached are refused")


def _refusal(name: str, error: Exception) -> Exception:
    """The error a failed read of ``name`` raises to the caller, with the SSM error as its cause.
    Records a missing parameter, and opens the back-off window for a transient failure."""
    if _error_code(error) == "ParameterNotFound":
        _missing[name] = _monotonic()
        _cache.pop(name, None)
        logger.warning(f"Parameter Store parameter not found: {name}")
        refusal = ParameterNotFoundError(f"Parameter Store parameter not found: {name}")
    elif _is_transient(error):
        _record_failed_read()
        refusal = ParameterUnavailableError(f"Parameter Store parameter could not be read in time: {name}")
    else:
        refusal = ParameterReadError(f"Parameter Store refused the read ({_error_code(error)}): {name}")
    refusal.__cause__ = error
    return refusal


def _answer_without_read(name: str, ttl_seconds: float):
    """The answer the module state gives without reading: a fresh cached value,
    ParameterNotFoundError for a recorded miss and, inside the back-off window, a stale cached value
    or ParameterUnavailableError. _NEEDS_READ when only a read can answer."""
    entry = _cache.get(name)
    now = _monotonic()
    if entry is not None and now - entry[1] < ttl_seconds:
        return entry[0]

    recorded_at = _missing.get(name)
    if recorded_at is not None and now - recorded_at < MISSING_PARAMETER_TTL_SECONDS:
        raise ParameterNotFoundError(f"Parameter Store parameter not found: {name}")

    if _in_backoff_window():
        _log_backoff_window_once()
        if entry is not None:
            return entry[0]
        raise ParameterUnavailableError(
            f"Parameter Store parameter not read while reads back off after a failure: {name}")

    return _NEEDS_READ


def get_parameter_value(name: str, *, ttl_seconds: float = CACHE_TTL_SECONDS) -> str:
    """The value of the String parameter ``name``, read at most once per ``ttl_seconds`` per
    container.

    A cache hit takes no lock and reads nothing. Otherwise one bounded read runs (one at a time per
    container); when it fails, a cached value is served, stale or not, unless Parameter Store
    reported the parameter missing. Raises ParameterNotFoundError (a KeyError) for a missing
    parameter, ParameterUnavailableError when Parameter Store could not be read in time or the
    container is backing off after such a failure, and ParameterReadError for a refusal a retry
    cannot fix."""
    if not name:
        raise ValueError("A Parameter Store parameter name is required")

    answer = _answer_without_read(name, ttl_seconds)
    if answer is not _NEEDS_READ:
        return answer

    with _read_lock:
        # Another thread may have read the parameter, or failed to, while this one waited.
        answer = _answer_without_read(name, ttl_seconds)
        if answer is not _NEEDS_READ:
            return answer

        try:
            value = _read(name)
        except Exception as e:
            refusal = _refusal(name, e)
            entry = _cache.get(name)
            if entry is not None:
                logger.warning(f"Parameter Store read failed, serving the cached value of {name}")
                return entry[0]
            raise refusal
        _record_completed_read()
        _missing.pop(name, None)
        _cache[name] = (value, _monotonic())
        return value


def read_parameter_value(name: str) -> str:
    """The current value of the String parameter ``name``, read now: for a caller that must see a
    value written moments ago. Bypasses the cache (it neither serves nor stores a value) and the
    record of missing parameters, but is bounded, classified and logged as get_parameter_value is,
    and shares its back-off window: inside the window it raises ParameterUnavailableError without a
    call, and a transient failure opens the window. It takes the same lock, so reads of either kind
    run one at a time per container and the window is updated by one thread at a time.

    A missing parameter is an expected answer for such a value (a marker not written yet, a name a
    deployment is still publishing), so it is logged at INFO and the caller decides how much it
    matters."""
    if not name:
        raise ValueError("A Parameter Store parameter name is required")

    def refused_inside_window():
        _log_backoff_window_once()
        return ParameterUnavailableError(
            f"Parameter Store parameter not read while reads back off after a failure: {name}")

    if _in_backoff_window():
        raise refused_inside_window()

    with _read_lock:
        # A read by another thread may have opened the window while this one waited.
        if _in_backoff_window():
            raise refused_inside_window()
        try:
            value = _read(name)
        except Exception as e:
            if _error_code(e) == "ParameterNotFound":
                logger.info(f"Parameter Store parameter not found: {name}")
                raise ParameterNotFoundError(f"Parameter Store parameter not found: {name}") from e
            raise _refusal(name, e)
        _record_completed_read()
        return value
