# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import random
import threading
import time
import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from customLogging.logger import safeLogger

logger = safeLogger()

# The sweep client. A sweep runs during Lambda init, which Lambda stops after 10 seconds, so the
# client is bounded rather than given Rule 6's five adaptive attempts: one-second connect and
# two-second read timeouts and no botocore retries. The page loop in _sweep_parameters owns the
# retries, under SWEEP_BUDGET_SECONDS, so no SDK back-off sleep can carry a sweep past its budget.
sweep_client_config = Config(connect_timeout=1, read_timeout=2, retries={'max_attempts': 0, 'mode': 'standard'})

CACHE_TTL_SECONDS = 3600

# Held far shorter than CACHE_TTL_SECONDS: a parameter published after the miss was
# recorded is picked up within this window instead of at the next full refresh.
MISSING_KEY_TTL_SECONDS = 300

# Wall-clock budget of one sweep, its retries and their sleeps included. A sweep that cannot
# finish inside it fails, and the back-off window below answers the lookups that follow.
SWEEP_BUDGET_SECONDS = 4.0

# GetParametersByPath returns at most 10 parameters per call.
SWEEP_PAGE_SIZE = 10

# Sleep before retrying a page: uniform(0, min(SWEEP_RETRY_MAX_SLEEP_SECONDS,
# SWEEP_RETRY_BASE_SECONDS * 2 ** k)) for the k-th retry in a row of the same page.
SWEEP_RETRY_BASE_SECONDS = 0.1
SWEEP_RETRY_MAX_SLEEP_SECONDS = 1.0

# After n failed sweeps in a row, the container starts no sweep for uniform(0.5, 1.0) *
# min(SWEEP_BACKOFF_MAX_SECONDS, SWEEP_BACKOFF_BASE_SECONDS * 2 ** (n - 1)) seconds: about 2,
# 4, 8 and 16 seconds, then 30. The jitter spreads containers that failed together.
SWEEP_BACKOFF_BASE_SECONDS = 2.0
SWEEP_BACKOFF_MAX_SECONDS = 30.0

# Error codes a page is retried on inside the budget: throttling and transient service faults.
# Any HTTP 5xx response is retried as well.
_RETRYABLE_ERROR_CODES = frozenset({
    "ThrottlingException",
    "Throttling",
    "ThrottledException",
    "RequestThrottledException",
    "TooManyRequestsException",
    "RequestLimitExceeded",
    "RequestTimeout",
    "RequestTimeoutException",
    "InternalServerError",
    "ServiceUnavailable",
})

# Connection failures and timeouts a page is retried on inside the budget.
_RETRYABLE_CONNECTION_ERRORS = (ConnectTimeoutError, ReadTimeoutError, EndpointConnectionError, ConnectionClosedError)

# Clock, sleep and jitter the sweep and the back-off window use.
_monotonic = time.monotonic
_sleep = time.sleep
_uniform = random.uniform


class ResourceNameUnavailableError(RuntimeError):
    """A resource name could not be resolved because Parameter Store could not be read: the sweep
    failed with throttling, a service fault or a timeout, or the lookup came inside the back-off
    window that follows such a failure. Not a KeyError, because the parameter may well be published.
    The workflow scaffolding states retry it (workflowAslBuilder.RESOURCE_NAME_UNAVAILABLE_RETRIER)."""


class ResourceNameAccessError(RuntimeError):
    """A resource name could not be resolved because Parameter Store refused the sweep with an error
    a retry cannot fix, most often AccessDeniedException: the function's role may not call
    ssm:GetParametersByPath on the deployment's parameters (its policy, a permissions boundary or a
    service control policy). Opens no back-off window, and no workflow state retries it as it does
    ResourceNameUnavailableError, so the lookup fails at once with the SSM error as its cause."""


class ResourceParamKey:
    """A fixed-name deployment resource: SSM parameter key suffix (relative to the
    deployment prefix) plus the legacy environment variable names that override it."""

    def __init__(self, param_key: str, env_var_names: tuple):
        self.param_key = param_key
        self.env_var_names = env_var_names


class ResourceKeys:
    # DynamoDB tables
    APP_FEATURE_ENABLED_STORAGE_TABLE = ResourceParamKey("dynamoTables/appFeatureEnabledStorage", ("APPFEATUREENABLED_STORAGE_TABLE_NAME",))
    ASSET_LINKS_STORAGE_TABLE_V2 = ResourceParamKey("dynamoTables/assetLinksStorageV2", ("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "ASSET_LINKS_STORAGE_TABLE_NAME"))
    ASSET_LINKS_METADATA_STORAGE_TABLE = ResourceParamKey("dynamoTables/assetLinksMetadataStorage", ("ASSET_LINKS_METADATA_STORAGE_TABLE_NAME",))
    ASSET_STORAGE_TABLE = ResourceParamKey("dynamoTables/assetStorage", ("ASSET_STORAGE_TABLE_NAME",))
    ASSET_UPLOADS_STORAGE_TABLE = ResourceParamKey("dynamoTables/assetUploadsStorage", ("ASSET_UPLOAD_TABLE_NAME",))
    ASSET_VERSIONS_STORAGE_TABLE = ResourceParamKey("dynamoTables/assetVersionsStorage", ("ASSET_VERSIONS_STORAGE_TABLE_NAME",))
    ASSET_FILE_VERSIONS_STORAGE_TABLE = ResourceParamKey("dynamoTables/assetFileVersionsStorage", ("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME",))
    ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE = ResourceParamKey("dynamoTables/assetFileVersionHistoryStorage", ("ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE_NAME",))
    ASSET_FILE_METADATA_VERSIONS_STORAGE_TABLE = ResourceParamKey("dynamoTables/assetFileMetadataVersionsStorage", ("ASSET_FILE_METADATA_VERSIONS_STORAGE_TABLE_NAME",))
    ASSET_FILE_METADATA_STORAGE_TABLE = ResourceParamKey("dynamoTables/assetFileMetadataStorage", ("ASSET_FILE_METADATA_STORAGE_TABLE_NAME",))
    ASSET_HISTORY_STORAGE_TABLE = ResourceParamKey("dynamoTables/assetHistoryStorage", ("ASSET_HISTORY_STORAGE_TABLE_NAME",))
    SYNC_TRACKING_OUTBOUND_STORAGE_TABLE = ResourceParamKey("dynamoTables/syncTrackingOutboundStorage", ("SYNC_TRACKING_OUTBOUND_STORAGE_TABLE_NAME",))
    AUTH_ENTITIES_STORAGE_TABLE = ResourceParamKey("dynamoTables/authEntitiesStorage", ("AUTH_TABLE_NAME", "AUTH_ENTITIES_TABLE"))
    COMMENT_STORAGE_TABLE = ResourceParamKey("dynamoTables/commentStorage", ("COMMENT_STORAGE_TABLE_NAME",))
    CONSTRAINTS_STORAGE_TABLE = ResourceParamKey("dynamoTables/constraintsStorage", ("CONSTRAINTS_TABLE_NAME",))
    DATABASE_STORAGE_TABLE = ResourceParamKey("dynamoTables/databaseStorage", ("DATABASE_STORAGE_TABLE_NAME",))
    METADATA_SCHEMA_STORAGE_TABLE_V2 = ResourceParamKey("dynamoTables/metadataSchemaStorageV2", ("METADATA_SCHEMA_STORAGE_TABLE_V2_NAME",))
    DATABASE_METADATA_STORAGE_TABLE = ResourceParamKey("dynamoTables/databaseMetadataStorage", ("DATABASE_METADATA_STORAGE_TABLE_NAME",))
    FILE_ATTRIBUTE_STORAGE_TABLE = ResourceParamKey("dynamoTables/fileAttributeStorage", ("FILE_ATTRIBUTE_STORAGE_TABLE_NAME",))
    PIPELINE_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineStorage", ("PIPELINE_STORAGE_TABLE_NAME",))
    ROLES_STORAGE_TABLE = ResourceParamKey("dynamoTables/rolesStorage", ("ROLES_TABLE_NAME",))
    S3_ASSET_BUCKETS_STORAGE_TABLE = ResourceParamKey("dynamoTables/s3AssetBucketsStorage", ("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME",))
    SUBSCRIPTIONS_STORAGE_TABLE = ResourceParamKey("dynamoTables/subscriptionsStorage", ("SUBSCRIPTIONS_STORAGE_TABLE_NAME",))
    TAG_STORAGE_TABLE = ResourceParamKey("dynamoTables/tagStorage", ("TAG_STORAGE_TABLE_NAME", "TAGS_STORAGE_TABLE_NAME"))
    TAG_TYPE_STORAGE_TABLE = ResourceParamKey("dynamoTables/tagTypeStorage", ("TAG_TYPES_STORAGE_TABLE_NAME",))
    # Legacy single-key tag tables retained for per-database namespacing migration
    TAG_STORAGE_TABLE_LEGACY = ResourceParamKey("dynamoTables/legacy/tagStorage", ("TAG_STORAGE_TABLE_LEGACY_NAME",))
    TAG_TYPE_STORAGE_TABLE_LEGACY = ResourceParamKey("dynamoTables/legacy/tagTypeStorage", ("TAG_TYPES_STORAGE_TABLE_LEGACY_NAME",))
    USER_ROLES_STORAGE_TABLE = ResourceParamKey("dynamoTables/userRolesStorage", ("USER_ROLES_TABLE_NAME", "USER_ROLES_STORAGE_TABLE_NAME"))
    USER_STORAGE_TABLE = ResourceParamKey("dynamoTables/userStorage", ("USER_STORAGE_TABLE_NAME",))
    WORKFLOW_EXECUTIONS_STORAGE_TABLE = ResourceParamKey("dynamoTables/workflowExecutionsStorage", ("WORKFLOW_EXECUTION_STORAGE_TABLE_NAME",))
    API_KEY_STORAGE_TABLE = ResourceParamKey("dynamoTables/apiKeyStorage", ("API_KEY_STORAGE_TABLE_NAME",))
    WORKFLOW_STORAGE_TABLE = ResourceParamKey("dynamoTables/workflowStorage", ("WORKFLOW_STORAGE_TABLE_NAME",))
    # Workflow-execution V2 data model tables
    WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2 = ResourceParamKey("dynamoTables/workflowExecutionsStorageV2", ("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME",))
    WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE = ResourceParamKey("dynamoTables/workflowExecutionInputsStorage", ("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME",))
    WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE = ResourceParamKey("dynamoTables/workflowExecutionConfigurationStorage", ("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME",))
    PIPELINE_EXECUTIONS_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineExecutionsStorage", ("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME",))
    PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineExecutionInputFilesStorage", ("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME",))
    PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineExecutionInputMetadataStorage", ("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME",))
    PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineExecutionInputConfigurationStorage", ("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME",))
    PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineExecutionOutputFilesStorage", ("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME",))
    PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineExecutionOutputMetadataStorage", ("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME",))
    PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineExecutionOutputResultsStorage", ("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME",))
    PIPELINE_EXECUTION_LOGS_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineExecutionLogsStorage", ("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME",))
    # Pipeline + workflow V2 data model tables
    PIPELINE_STORAGE_TABLE_V2 = ResourceParamKey("dynamoTables/pipelineStorageV2", ("PIPELINE_STORAGE_TABLE_V2_NAME",))
    PIPELINE_TEMPLATES_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineTemplatesStorage", ("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME",))
    PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE = ResourceParamKey("dynamoTables/pipelineTemplateTagSchemaStorage", ("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME",))
    WORKFLOW_STORAGE_TABLE_V2 = ResourceParamKey("dynamoTables/workflowStorageV2", ("WORKFLOW_STORAGE_TABLE_V2_NAME",))
    WORKFLOW_TRIGGERS_STORAGE_TABLE = ResourceParamKey("dynamoTables/workflowTriggersStorage", ("WORKFLOW_TRIGGERS_STORAGE_TABLE_NAME",))
    WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE = ResourceParamKey("dynamoTables/workflowExecutionLocksStorage", ("WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME",))
    # Vector search tables
    VECTOR_EMBEDDINGS_STORAGE_TABLE = ResourceParamKey("dynamoTables/vectorEmbeddingsStorage", ("VECTOR_EMBEDDINGS_STORAGE_TABLE_NAME",))
    # Large object copy engine: one row per copy job, plus one per workflow ingestion batch
    LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE = ResourceParamKey("dynamoTables/largeObjectCopyJobsStorage", ("LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME",))
    # Download as Zip jobs: one row per prepared download, owned by its requester (TTL expiresAt)
    ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE = ResourceParamKey("dynamoTables/zipDownloadRequestsStorage", ("ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE_NAME",))
    AUDIT_LOG_QUERIES_STORAGE_TABLE = ResourceParamKey("dynamoTables/auditLogQueriesStorage", ("AUDIT_LOG_QUERIES_STORAGE_TABLE_NAME",))
    # Compliance tables
    COMPLIANCE_SCHEMA_STORAGE_TABLE = ResourceParamKey("dynamoTables/complianceSchemaStorage", ("COMPLIANCE_SCHEMA_STORAGE_TABLE_NAME",))
    COMPLIANCE_ASSET_STATE_STORAGE_TABLE = ResourceParamKey("dynamoTables/complianceAssetStateStorage", ("COMPLIANCE_ASSET_STATE_STORAGE_TABLE_NAME",))
    COMPLIANCE_EVALUATION_STORAGE_TABLE = ResourceParamKey("dynamoTables/complianceEvaluationStorage", ("COMPLIANCE_EVALUATION_STORAGE_TABLE_NAME",))
    COMPLIANCE_CASCADE_STORAGE_TABLE = ResourceParamKey("dynamoTables/complianceCascadeStorage", ("COMPLIANCE_CASCADE_STORAGE_TABLE_NAME",))
    COMPLIANCE_AUDIT_STORAGE_TABLE = ResourceParamKey("dynamoTables/complianceAuditStorage", ("COMPLIANCE_AUDIT_STORAGE_TABLE_NAME",))
    # Non-asset S3 buckets
    ASSET_AUXILIARY_BUCKET = ResourceParamKey("s3Buckets/assetAuxiliary", ("S3_ASSET_AUXILIARY_BUCKET", "ASSET_AUXILIARY_BUCKET_NAME", "S3_ASSETAUXILIARY_STORAGE_BUCKET"))
    ARTEFACTS_BUCKET = ResourceParamKey("s3Buckets/artefacts", ("LAMBDA_PIPELINE_SAMPLE_FUNCTION_BUCKET",))
    # Audit CloudWatch log groups
    AUDIT_LOG_AUTHENTICATION = ResourceParamKey("cloudwatchLogGroups/auditAuthentication", ("AUDIT_LOG_AUTHENTICATION",))
    AUDIT_LOG_AUTHORIZATION = ResourceParamKey("cloudwatchLogGroups/auditAuthorization", ("AUDIT_LOG_AUTHORIZATION",))
    AUDIT_LOG_FILEUPLOAD = ResourceParamKey("cloudwatchLogGroups/auditFileUpload", ("AUDIT_LOG_FILEUPLOAD",))
    AUDIT_LOG_FILEDOWNLOAD = ResourceParamKey("cloudwatchLogGroups/auditFileDownload", ("AUDIT_LOG_FILEDOWNLOAD",))
    AUDIT_LOG_FILEDOWNLOAD_STREAMED = ResourceParamKey("cloudwatchLogGroups/auditFileDownloadStreamed", ("AUDIT_LOG_FILEDOWNLOAD_STREAMED",))
    AUDIT_LOG_AUTHOTHER = ResourceParamKey("cloudwatchLogGroups/auditAuthOther", ("AUDIT_LOG_AUTHOTHER",))
    AUDIT_LOG_AUTHCHANGES = ResourceParamKey("cloudwatchLogGroups/auditAuthChanges", ("AUDIT_LOG_AUTHCHANGES",))
    AUDIT_LOG_ACTIONS = ResourceParamKey("cloudwatchLogGroups/auditActions", ("AUDIT_LOG_ACTIONS",))
    AUDIT_LOG_ERRORS = ResourceParamKey("cloudwatchLogGroups/auditErrors", ("AUDIT_LOG_ERRORS",))


_ssm_client = None
_cache = {}
_cache_fetched_at = 0.0
_missing_keys = {}

# Failed sweeps in a row, and the _monotonic() time before which no sweep starts.
_failed_sweeps = 0
_sweep_retry_after = 0.0
# The window (its _sweep_retry_after) whose refused lookups have been logged.
_window_logged_for = None

# One sweep at a time per container. Taken only when a lookup needs a sweep, so a cache hit
# never waits on it.
_sweep_lock = threading.Lock()

# What _answer_without_sweep returns when only a sweep can answer.
_NEEDS_SWEEP = object()


def _get_ssm_client():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client('ssm', config=sweep_client_config)
    return _ssm_client


def _error_code(error: Exception) -> str:
    """The service error code of a ClientError, else the exception's class name."""
    if isinstance(error, ClientError):
        return (getattr(error, "response", None) or {}).get("Error", {}).get("Code") or "ClientError"
    return type(error).__name__


def _is_retryable(error: Exception) -> bool:
    """Throttling, a transient service fault, or a connection failure or timeout."""
    if isinstance(error, _RETRYABLE_CONNECTION_ERRORS):
        return True
    if isinstance(error, ClientError):
        response = getattr(error, "response", None) or {}
        status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0
        return _error_code(error) in _RETRYABLE_ERROR_CODES or status >= 500
    return False


def _is_transient_sweep_failure(error: Exception) -> bool:
    """A sweep that ran out of its budget on retryable errors (or on none, TimeoutError), as
    opposed to one a non-retryable error ended at once."""
    return isinstance(error, TimeoutError) or _is_retryable(error)


def _sweep_parameters(prefix: str) -> dict:
    """Read every parameter under the prefix with GetParametersByPath, page by page, inside
    SWEEP_BUDGET_SECONDS, and return them keyed by their path below the prefix.

    A page that fails with a retryable error is requested again with its own NextToken after a
    jittered sleep, so the pages already read are kept. Any other error ends the sweep at once.
    No call starts and no sleep runs past the budget; when the budget runs out the sweep raises
    the last error, or TimeoutError when none was raised. Logs one line per sweep: counts and
    timings, never a parameter name or value."""
    client = _get_ssm_client()
    started = _monotonic()
    deadline = started + SWEEP_BUDGET_SECONDS
    parameters = {}
    pages = calls = retries_in_a_row = 0
    next_token = None
    last_error = None

    def failed(error: Exception) -> Exception:
        logger.error(
            f"Resource name sweep failed ({_error_code(error)}) after {pages} pages, {calls} calls, "
            f"{_monotonic() - started:.2f}s")
        return error

    while True:
        if _monotonic() >= deadline:
            raise failed(last_error or TimeoutError(
                f"Resource name sweep did not finish within {SWEEP_BUDGET_SECONDS}s"))
        request = {"Path": prefix, "Recursive": True, "MaxResults": SWEEP_PAGE_SIZE}
        if next_token:
            request["NextToken"] = next_token
        calls += 1
        try:
            page = client.get_parameters_by_path(**request)
        except Exception as e:
            if not _is_retryable(e):
                raise failed(e)
            last_error = e
            pause = _uniform(0, min(SWEEP_RETRY_MAX_SLEEP_SECONDS, SWEEP_RETRY_BASE_SECONDS * 2 ** retries_in_a_row))
            retries_in_a_row += 1
            if _monotonic() + pause >= deadline:
                raise failed(e)
            _sleep(pause)
            continue
        pages += 1
        retries_in_a_row = 0
        for param in page.get('Parameters', []):
            key = param['Name'][len(prefix):].lstrip('/')
            parameters[key] = param['Value']
        next_token = page.get('NextToken')
        if not next_token:
            logger.info(
                f"Refreshed {len(parameters)} resource name parameters from SSM in {pages} pages, "
                f"{calls} calls, {_monotonic() - started:.2f}s")
            return parameters


def _refresh_cache():
    """Fetch every resource-name parameter under the deployment prefix in one bounded
    GetParametersByPath sweep (_sweep_parameters) and replace the module cache. A sweep that
    fails raises and leaves the cache as it was."""
    global _cache, _cache_fetched_at, _missing_keys
    prefix = os.environ["VAMS_RESOURCE_PARAM_PREFIX"].rstrip("/")
    new_cache = _sweep_parameters(prefix)
    _cache = new_cache
    _cache_fetched_at = time.time()
    # The sweep covers the whole prefix, so it is authoritative for absence too: keys it
    # now carries are no longer missing, and the rest are re-confirmed at this timestamp.
    _missing_keys = {k: _cache_fetched_at for k in _missing_keys if k not in _cache}


def _record_failed_sweep():
    """Open the back-off window: no sweep starts in this container until it has passed."""
    global _failed_sweeps, _sweep_retry_after
    _failed_sweeps += 1
    window = SWEEP_BACKOFF_BASE_SECONDS * 2 ** min(_failed_sweeps - 1, 16)
    _sweep_retry_after = _monotonic() + _uniform(0.5, 1.0) * min(SWEEP_BACKOFF_MAX_SECONDS, window)


def _record_completed_sweep():
    global _failed_sweeps, _sweep_retry_after
    _failed_sweeps = 0
    _sweep_retry_after = 0.0


def _in_backoff_window() -> bool:
    return _failed_sweeps > 0 and _monotonic() < _sweep_retry_after


def _log_backoff_window_once():
    """One WARNING per back-off window for the lookups it answers without a sweep."""
    global _window_logged_for
    if _window_logged_for == _sweep_retry_after:
        return
    _window_logged_for = _sweep_retry_after
    logger.warning(
        f"Resource name lookups are answered from the cache only for {_sweep_retry_after - _monotonic():.1f}s "
        f"after {_failed_sweeps} failed SSM sweep(s); names not cached are refused")


def _answer_without_sweep(key: ResourceParamKey):
    """The answer the module state gives without reading SSM: a fresh cached name, KeyError for a
    recorded miss and, inside the back-off window, a stale cached name or
    ResourceNameUnavailableError. _NEEDS_SWEEP when only a sweep can answer.

    Reads the cache map once, since a sweep in another thread may replace it meanwhile."""
    cache = _cache
    if key.param_key in cache and (time.time() - _cache_fetched_at) < CACHE_TTL_SECONDS:
        return cache[key.param_key]

    recorded_at = _missing_keys.get(key.param_key)
    if recorded_at is not None and (time.time() - recorded_at) < MISSING_KEY_TTL_SECONDS:
        # Same error as a fresh miss, so a cached negative is indistinguishable to callers.
        raise KeyError(f"Resource name parameter not found in SSM: {key.param_key}")

    if _in_backoff_window():
        _log_backoff_window_once()
        if key.param_key in cache:
            return cache[key.param_key]
        raise ResourceNameUnavailableError(
            f"Resource name not resolved while SSM sweeps back off after a failure: {key.param_key}")

    return _NEEDS_SWEEP


def get_resource_name(key: ResourceParamKey) -> str:
    """Resolve a resource name: env var override first, then the cached SSM map
    (refreshed at most once per CACHE_TTL_SECONDS per container). A key a completed
    sweep did not carry is remembered as missing for MISSING_KEY_TTL_SECONDS, so an
    unpublished parameter costs one sweep per window rather than one per call.

    A sweep is bounded by SWEEP_BUDGET_SECONDS and runs one at a time per container. A sweep that
    fails on throttling, a service fault or a timeout opens a back-off window in which no sweep
    starts: a lookup inside it is answered from the cache, stale or not, or refused with
    ResourceNameUnavailableError, as is the failed sweep for a name the cache does not hold. A sweep
    a non-retryable error ends (access denied, for one) opens no window and, for a name the cache
    does not hold, raises ResourceNameAccessError. A cache hit takes no lock and reads no SSM."""
    for env_name in key.env_var_names:
        value = os.environ.get(env_name)
        if value:
            return value

    answer = _answer_without_sweep(key)
    if answer is not _NEEDS_SWEEP:
        return answer

    with _sweep_lock:
        # Another thread may have finished a sweep, or failed one, while this one waited.
        answer = _answer_without_sweep(key)
        if answer is not _NEEDS_SWEEP:
            return answer

        try:
            _refresh_cache()
        except Exception as e:
            if "VAMS_RESOURCE_PARAM_PREFIX" not in os.environ:
                # A deployment without the prefix is misconfigured, not throttled: raised as the
                # KeyError it is and never recorded as a failed sweep.
                if key.param_key in _cache:
                    logger.warning(f"SSM refresh failed; serving cached value for {key.param_key}: {e}")
                    return _cache[key.param_key]
                logger.exception(f"Failed loading resource name parameters from SSM: {e}")
                raise
            transient = _is_transient_sweep_failure(e)
            if transient:
                _record_failed_sweep()
            if key.param_key in _cache:
                logger.warning(f"SSM refresh failed; serving cached value for {key.param_key}")
                return _cache[key.param_key]
            if not transient:
                # Not throttling: a retry meets the same refusal, so it is reported for what it is.
                raise ResourceNameAccessError(
                    f"Resource name parameters could not be read from SSM ({_error_code(e)}): the "
                    f"function's role needs ssm:GetParametersByPath on the deployment's resource-name "
                    f"parameters; check its policy, permissions boundary and service control policies: "
                    f"{key.param_key}") from e
            raise ResourceNameUnavailableError(
                f"Resource name parameters could not be read from SSM: {key.param_key}") from e
        _record_completed_sweep()

        if key.param_key not in _cache:
            _missing_keys[key.param_key] = _cache_fetched_at
            logger.warning(f"Resource name parameter not published under the deployment prefix: {key.param_key}")
            raise KeyError(f"Resource name parameter not found in SSM: {key.param_key}")
        return _cache[key.param_key]


# Readable aliases for call sites
get_table_name = get_resource_name
get_bucket_name = get_resource_name
get_log_group_name = get_resource_name
