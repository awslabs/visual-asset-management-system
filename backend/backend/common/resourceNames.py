# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import time
import boto3
from botocore.config import Config
from customLogging.logger import safeLogger

logger = safeLogger(service_name="ResourceNames")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

CACHE_TTL_SECONDS = 3600


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


def _get_ssm_client():
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client('ssm', config=retry_config)
    return _ssm_client


def _refresh_cache():
    """Fetch every resource-name parameter under the deployment prefix in one
    paginated GetParametersByPath sweep and replace the module cache."""
    global _cache, _cache_fetched_at
    prefix = os.environ["VAMS_RESOURCE_PARAM_PREFIX"].rstrip("/")
    new_cache = {}
    paginator = _get_ssm_client().get_paginator('get_parameters_by_path')
    for page in paginator.paginate(Path=prefix, Recursive=True):
        for param in page.get('Parameters', []):
            key = param['Name'][len(prefix):].lstrip('/')
            new_cache[key] = param['Value']
    _cache = new_cache
    _cache_fetched_at = time.time()
    logger.info(f"Refreshed {len(_cache)} resource name parameters from SSM")


def get_resource_name(key: ResourceParamKey) -> str:
    """Resolve a resource name: env var override first, then the cached SSM map
    (refreshed at most once per CACHE_TTL_SECONDS per container)."""
    for env_name in key.env_var_names:
        value = os.environ.get(env_name)
        if value:
            return value

    if key.param_key in _cache and (time.time() - _cache_fetched_at) < CACHE_TTL_SECONDS:
        return _cache[key.param_key]

    try:
        _refresh_cache()
    except Exception as e:
        if key.param_key in _cache:
            logger.warning(f"SSM refresh failed; serving cached value for {key.param_key}: {e}")
            return _cache[key.param_key]
        logger.exception(f"Failed loading resource name parameters from SSM: {e}")
        raise

    if key.param_key not in _cache:
        raise KeyError(f"Resource name parameter not found in SSM: {key.param_key}")
    return _cache[key.param_key]


# Readable aliases for call sites
get_table_name = get_resource_name
get_bucket_name = get_resource_name
get_log_group_name = get_resource_name
