/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Canonical SSM parameter key suffixes (relative to config.resourceNamesSSMParamPrefix)
 * for every fixed-name deployment resource distributed to backend Lambda functions.
 * The dynamoTables, s3Buckets, and cloudwatchLogGroups categories are mirrored by
 * backend/backend/common/resourceNames.py — keep those lists identical. The
 * dynamoTablesLegacy and lambdaFunctions categories are consumed only by deployment
 * and data-migration tooling (mirrored by
 * infra/deploymentDataMigration/tools/ssm_resource_lookup.py), never by backend
 * Lambda handlers.
 */
export const RESOURCE_PARAM_KEYS = {
    dynamoTables: {
        appFeatureEnabledStorage: "dynamoTables/appFeatureEnabledStorage",
        assetLinksStorageV2: "dynamoTables/assetLinksStorageV2",
        assetLinksMetadataStorage: "dynamoTables/assetLinksMetadataStorage",
        assetStorage: "dynamoTables/assetStorage",
        assetUploadsStorage: "dynamoTables/assetUploadsStorage",
        assetVersionsStorage: "dynamoTables/assetVersionsStorage",
        assetFileVersionsStorage: "dynamoTables/assetFileVersionsStorage",
        assetFileVersionHistoryStorage: "dynamoTables/assetFileVersionHistoryStorage",
        assetHistoryStorage: "dynamoTables/assetHistoryStorage",
        syncTrackingOutboundStorage: "dynamoTables/syncTrackingOutboundStorage",
        assetFileMetadataVersionsStorage: "dynamoTables/assetFileMetadataVersionsStorage",
        assetFileMetadataStorage: "dynamoTables/assetFileMetadataStorage",
        authEntitiesStorage: "dynamoTables/authEntitiesStorage",
        commentStorage: "dynamoTables/commentStorage",
        constraintsStorage: "dynamoTables/constraintsStorage",
        databaseStorage: "dynamoTables/databaseStorage",
        metadataSchemaStorageV2: "dynamoTables/metadataSchemaStorageV2",
        databaseMetadataStorage: "dynamoTables/databaseMetadataStorage",
        fileAttributeStorage: "dynamoTables/fileAttributeStorage",
        pipelineStorage: "dynamoTables/pipelineStorage",
        rolesStorage: "dynamoTables/rolesStorage",
        s3AssetBucketsStorage: "dynamoTables/s3AssetBucketsStorage",
        subscriptionsStorage: "dynamoTables/subscriptionsStorage",
        tagStorage: "dynamoTables/tagStorage",
        tagTypeStorage: "dynamoTables/tagTypeStorage",
        userRolesStorage: "dynamoTables/userRolesStorage",
        userStorage: "dynamoTables/userStorage",
        workflowExecutionsStorage: "dynamoTables/workflowExecutionsStorage",
        apiKeyStorage: "dynamoTables/apiKeyStorage",
        workflowStorage: "dynamoTables/workflowStorage",
    },
    // Deprecated tables retained for data migration only (no handler reads them)
    dynamoTablesLegacy: {
        assetVersionsStorageV1: "dynamoTables/legacy/assetVersionsStorageV1",
        assetFileVersionsStorageV1: "dynamoTables/legacy/assetFileVersionsStorageV1",
        assetLinksStorage: "dynamoTables/legacy/assetLinksStorage",
        metadataStorage: "dynamoTables/legacy/metadataStorage",
        metadataSchemaStorage: "dynamoTables/legacy/metadataSchemaStorage",
    },
    // Lambda function names consumed by data-migration tooling
    lambdaFunctions: {
        crOsReindexer: "lambdaFunctions/crOsReindexer",
    },
    s3Buckets: {
        assetAuxiliary: "s3Buckets/assetAuxiliary",
        artefacts: "s3Buckets/artefacts",
    },
    cloudwatchLogGroups: {
        auditAuthentication: "cloudwatchLogGroups/auditAuthentication",
        auditAuthorization: "cloudwatchLogGroups/auditAuthorization",
        auditFileUpload: "cloudwatchLogGroups/auditFileUpload",
        auditFileDownload: "cloudwatchLogGroups/auditFileDownload",
        auditFileDownloadStreamed: "cloudwatchLogGroups/auditFileDownloadStreamed",
        auditAuthOther: "cloudwatchLogGroups/auditAuthOther",
        auditAuthChanges: "cloudwatchLogGroups/auditAuthChanges",
        auditActions: "cloudwatchLogGroups/auditActions",
        auditErrors: "cloudwatchLogGroups/auditErrors",
    },
} as const;
