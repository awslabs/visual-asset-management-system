#!/usr/bin/env python3
# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
SSM resource-name lookup for VAMS data-migration scripts.

Each VAMS deployment publishes its DynamoDB table names, non-asset S3 bucket
names, audit log group names, deprecated migration-only table names, and
selected Lambda function names as SSM String parameters under a
deployment-unique prefix. The prefix is exposed by the core stack output
``ResourceNamesSSMParamPrefixOutput`` (format:
``/{configName}-{baseStackName}/resourceNames``).

Migration scripts pass the prefix (plus optional profile/region) to
``SsmResourceLookup`` and resolve every table and function name they need,
instead of requiring the operator to copy each physical name into the
migration config by hand. Explicit config values still take precedence via
``resolve_with_override`` so operators can override any individual name.

The key constants below mirror ``infra/common/resourceParamKeys.ts``. When a
new DynamoDB table, audit log group, or migration-consumed Lambda function is
added to the registry, add the matching constant here (see the data-migration
steering docs).
"""

import logging
from typing import Dict, Optional

import boto3

logger = logging.getLogger(__name__)


class ResourceParamKeys:
    """SSM parameter key suffixes relative to the deployment's resourceNames prefix.

    Mirrors infra/common/resourceParamKeys.ts.
    """

    # Active DynamoDB tables
    APP_FEATURE_ENABLED_STORAGE_TABLE = "dynamoTables/appFeatureEnabledStorage"
    ASSET_LINKS_STORAGE_TABLE_V2 = "dynamoTables/assetLinksStorageV2"
    ASSET_LINKS_METADATA_STORAGE_TABLE = "dynamoTables/assetLinksMetadataStorage"
    ASSET_STORAGE_TABLE = "dynamoTables/assetStorage"
    ASSET_UPLOADS_STORAGE_TABLE = "dynamoTables/assetUploadsStorage"
    ASSET_VERSIONS_STORAGE_TABLE = "dynamoTables/assetVersionsStorage"
    ASSET_FILE_VERSIONS_STORAGE_TABLE = "dynamoTables/assetFileVersionsStorage"
    ASSET_FILE_VERSION_HISTORY_STORAGE_TABLE = "dynamoTables/assetFileVersionHistoryStorage"
    ASSET_HISTORY_STORAGE_TABLE = "dynamoTables/assetHistoryStorage"
    SYNC_TRACKING_OUTBOUND_STORAGE_TABLE = "dynamoTables/syncTrackingOutboundStorage"
    ASSET_FILE_METADATA_VERSIONS_STORAGE_TABLE = "dynamoTables/assetFileMetadataVersionsStorage"
    ASSET_FILE_METADATA_STORAGE_TABLE = "dynamoTables/assetFileMetadataStorage"
    AUTH_ENTITIES_STORAGE_TABLE = "dynamoTables/authEntitiesStorage"
    COMMENT_STORAGE_TABLE = "dynamoTables/commentStorage"
    CONSTRAINTS_STORAGE_TABLE = "dynamoTables/constraintsStorage"
    DATABASE_STORAGE_TABLE = "dynamoTables/databaseStorage"
    METADATA_SCHEMA_STORAGE_TABLE_V2 = "dynamoTables/metadataSchemaStorageV2"
    DATABASE_METADATA_STORAGE_TABLE = "dynamoTables/databaseMetadataStorage"
    FILE_ATTRIBUTE_STORAGE_TABLE = "dynamoTables/fileAttributeStorage"
    PIPELINE_STORAGE_TABLE = "dynamoTables/pipelineStorage"
    ROLES_STORAGE_TABLE = "dynamoTables/rolesStorage"
    S3_ASSET_BUCKETS_STORAGE_TABLE = "dynamoTables/s3AssetBucketsStorage"
    SUBSCRIPTIONS_STORAGE_TABLE = "dynamoTables/subscriptionsStorage"
    TAG_STORAGE_TABLE = "dynamoTables/tagStorage"
    TAG_TYPE_STORAGE_TABLE = "dynamoTables/tagTypeStorage"
    USER_ROLES_STORAGE_TABLE = "dynamoTables/userRolesStorage"
    USER_STORAGE_TABLE = "dynamoTables/userStorage"
    WORKFLOW_EXECUTIONS_STORAGE_TABLE = "dynamoTables/workflowExecutionsStorage"
    API_KEY_STORAGE_TABLE = "dynamoTables/apiKeyStorage"
    WORKFLOW_STORAGE_TABLE = "dynamoTables/workflowStorage"
    # Workflow-execution V2 data model tables
    WORKFLOW_EXECUTIONS_STORAGE_TABLE_V2 = "dynamoTables/workflowExecutionsStorageV2"
    WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE = "dynamoTables/workflowExecutionInputsStorage"
    WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE = "dynamoTables/workflowExecutionConfigurationStorage"
    PIPELINE_EXECUTIONS_STORAGE_TABLE = "dynamoTables/pipelineExecutionsStorage"
    PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE = "dynamoTables/pipelineExecutionInputFilesStorage"
    PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE = "dynamoTables/pipelineExecutionInputMetadataStorage"
    PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE = "dynamoTables/pipelineExecutionInputConfigurationStorage"
    PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE = "dynamoTables/pipelineExecutionOutputFilesStorage"
    PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE = "dynamoTables/pipelineExecutionOutputMetadataStorage"
    PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE = "dynamoTables/pipelineExecutionOutputResultsStorage"
    PIPELINE_EXECUTION_LOGS_STORAGE_TABLE = "dynamoTables/pipelineExecutionLogsStorage"
    # Pipeline + workflow V2 data model tables
    PIPELINE_STORAGE_TABLE_V2 = "dynamoTables/pipelineStorageV2"
    PIPELINE_TEMPLATES_STORAGE_TABLE = "dynamoTables/pipelineTemplatesStorage"
    PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE = "dynamoTables/pipelineTemplateTagSchemaStorage"
    WORKFLOW_STORAGE_TABLE_V2 = "dynamoTables/workflowStorageV2"
    WORKFLOW_TRIGGERS_STORAGE_TABLE = "dynamoTables/workflowTriggersStorage"

    # Deprecated tables retained for data migration only
    LEGACY_ASSET_VERSIONS_STORAGE_TABLE_V1 = "dynamoTables/legacy/assetVersionsStorageV1"
    LEGACY_ASSET_FILE_VERSIONS_STORAGE_TABLE_V1 = "dynamoTables/legacy/assetFileVersionsStorageV1"
    LEGACY_ASSET_LINKS_STORAGE_TABLE = "dynamoTables/legacy/assetLinksStorage"
    LEGACY_METADATA_STORAGE_TABLE = "dynamoTables/legacy/metadataStorage"
    LEGACY_METADATA_SCHEMA_STORAGE_TABLE = "dynamoTables/legacy/metadataSchemaStorage"

    # Non-asset S3 buckets
    ASSET_AUXILIARY_BUCKET = "s3Buckets/assetAuxiliary"
    ARTEFACTS_BUCKET = "s3Buckets/artefacts"

    # Audit CloudWatch log groups
    AUDIT_LOG_AUTHENTICATION = "cloudwatchLogGroups/auditAuthentication"
    AUDIT_LOG_AUTHORIZATION = "cloudwatchLogGroups/auditAuthorization"
    AUDIT_LOG_FILEUPLOAD = "cloudwatchLogGroups/auditFileUpload"
    AUDIT_LOG_FILEDOWNLOAD = "cloudwatchLogGroups/auditFileDownload"
    AUDIT_LOG_FILEDOWNLOAD_STREAMED = "cloudwatchLogGroups/auditFileDownloadStreamed"
    AUDIT_LOG_AUTHOTHER = "cloudwatchLogGroups/auditAuthOther"
    AUDIT_LOG_AUTHCHANGES = "cloudwatchLogGroups/auditAuthChanges"
    AUDIT_LOG_ACTIONS = "cloudwatchLogGroups/auditActions"
    AUDIT_LOG_ERRORS = "cloudwatchLogGroups/auditErrors"

    # Lambda function names consumed by migration tooling
    CR_OS_REINDEXER_FUNCTION = "lambdaFunctions/crOsReindexer"


class SsmResourceLookup:
    """Resolves VAMS resource names from the deployment's SSM parameter prefix.

    Fetches the entire prefix once (paginated GetParametersByPath) and serves
    lookups from the in-memory map.
    """

    def __init__(
        self,
        base_param_prefix: str,
        profile: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self.prefix = base_param_prefix.rstrip("/")
        session_kwargs = {}
        if profile:
            session_kwargs["profile_name"] = profile
        if region:
            session_kwargs["region_name"] = region
        session = boto3.Session(**session_kwargs)
        self._ssm = session.client("ssm")
        self._cache: Optional[Dict[str, str]] = None

    def _load(self) -> Dict[str, str]:
        if self._cache is None:
            values: Dict[str, str] = {}
            paginator = self._ssm.get_paginator("get_parameters_by_path")
            for page in paginator.paginate(Path=self.prefix, Recursive=True):
                for param in page.get("Parameters", []):
                    key = param["Name"][len(self.prefix):].lstrip("/")
                    values[key] = param["Value"]
            self._cache = values
            logger.info(
                f"Loaded {len(values)} resource name parameters from SSM prefix {self.prefix}"
            )
        return self._cache

    def resolve(self, param_key: str) -> str:
        """Return the resource name for a ResourceParamKeys constant. Raises KeyError if absent."""
        values = self._load()
        if param_key not in values:
            raise KeyError(
                f"Resource name parameter not found under {self.prefix}: {param_key}. "
                "Verify the deployment is on a VAMS version that publishes resource-name "
                "parameters (v2.6+) and that the prefix matches the core stack output "
                "'ResourceNamesSSMParamPrefixOutput'."
            )
        return values[param_key]

    def resolve_with_override(self, override: Optional[str], param_key: str) -> str:
        """Return the operator-supplied override when set, otherwise resolve from SSM."""
        if override:
            return override
        return self.resolve(param_key)
