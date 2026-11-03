/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import {
    AwsCustomResource,
    AwsSdkCall,
    AwsCustomResourcePolicy,
    PhysicalResourceId,
} from "aws-cdk-lib/custom-resources";
import { SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT } from "../../../helper/seedCustomResource";
import { Construct } from "constructs";
import { Config } from "../../../../config/config";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";

export interface DynamoDbMetadataSchemaDefaultsConstructProps extends cdk.StackProps {
    customResourceRole: iam.Role;
    lambdaCommonBaseLayer: LayerVersion;
    storageResources: storageResources;
    config: Config;
}

/**
 * Helper function to create a metadata schema field definition
 * Matches MetadataSchemaFieldModel structure from backend/backend/models/metadataSchema.py
 */
function createField(
    metadataFieldKeyName: string,
    metadataFieldValueType: string,
    required = false,
    dependsOnFieldKeyName: string[] | null = null,
    defaultMetadataFieldValue: string | null = null,
    sequence: number | null = null,
    controlledListKeys: string[] | null = null
): any {
    return {
        metadataFieldKeyName: metadataFieldKeyName,
        metadataFieldValueType: metadataFieldValueType,
        required: required,
        sequence: sequence,
        dependsOnFieldKeyName: dependsOnFieldKeyName,
        controlledListKeys: controlledListKeys,
        defaultMetadataFieldValue: defaultMetadataFieldValue,
    };
}

/** `modifiedBy` of a default that no user has edited since a deployment wrote it. */
const SEED_WRITERS = {
    ":systemUser": { S: "SYSTEM_USER" },
    // Read-only match for rows that the 2.4.0 to 2.5.3 seeds wrote; never written by this construct.
    ":legacySystem": { S: "SYSTEM" },
};

/**
 * The create and update calls that seed one default schema item.
 *
 * Create writes the item unless a row already exists that a user has edited; update refreshes the
 * row only while it still exists and no user has edited it. Every API edit, disabling included,
 * records its editor in `modifiedBy`, so edited, disabled and deleted defaults are left alone.
 */
function seedCalls(
    tableName: string,
    item: Record<string, unknown>,
    physicalResourceId: string
): { onCreate: AwsSdkCall; onUpdate: AwsSdkCall } {
    const call = (conditionExpression: string): AwsSdkCall => ({
        service: "DynamoDB",
        action: "putItem",
        parameters: {
            TableName: tableName,
            Item: item,
            ConditionExpression: conditionExpression,
            ExpressionAttributeValues: SEED_WRITERS,
        },
        ignoreErrorCodesMatching: "ConditionalCheckFailedException",
        physicalResourceId: PhysicalResourceId.of(physicalResourceId),
    });
    return {
        onCreate: call(
            "attribute_not_exists(metadataSchemaId) OR modifiedBy IN (:systemUser, :legacySystem)"
        ),
        onUpdate: call(
            "attribute_exists(metadataSchemaId) AND modifiedBy IN (:systemUser, :legacySystem)"
        ),
    };
}

/**
 * Deploys default metadata schemas to DynamoDB table.
 *
 * A default is written on the deployment that creates its seed resource and refreshed on later
 * deployments only while no user has edited it. A release changes a shipped default only by adding
 * optional fields, never by renaming, retyping or removing one, and names the schema ID and the
 * field in its CHANGELOG entry, so administrators with an edited copy can add the field themselves.
 */
export class DynamoDbMetadataSchemaDefaultsConstruct extends Construct {
    constructor(
        parent: Construct,
        name: string,
        props: DynamoDbMetadataSchemaDefaultsConstructProps
    ) {
        super(parent, name);

        const tableName = props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName;
        let schemaIndex = 0;

        // Asset Links Schema - defaultAssetLinks
        if (props.config.app.metadataSchema.autoLoadDefaultAssetLinksSchema) {
            const assetLinksFields = [
                createField("Translation", "XYZ", false, null, null, 1),
                createField("Rotation", "WXYZ", false, null, null, 2),
                createField("Scale", "XYZ", false, null, null, 3),
                createField("Matrix", "MATRIX4X4", false, null, null, 4),
            ];

            const assetLinksSchemaId = "default-assetlinks-schema";
            const assetLinksCompositeKey = "GLOBAL:assetLinkMetadata";

            const assetLinksSeed = seedCalls(
                tableName,
                {
                    metadataSchemaId: {
                        S: assetLinksSchemaId,
                    },
                    "databaseId:metadataEntityType": {
                        S: assetLinksCompositeKey,
                    },
                    databaseId: {
                        S: "GLOBAL",
                    },
                    metadataSchemaEntityType: {
                        S: "assetLinkMetadata",
                    },
                    schemaName: {
                        S: "defaultAssetLinks",
                    },
                    fields: {
                        S: JSON.stringify({ fields: assetLinksFields }),
                    },
                    enabled: {
                        BOOL: true,
                    },
                    createdBy: {
                        S: "SYSTEM_USER",
                    },
                    modifiedBy: {
                        S: "SYSTEM_USER",
                    },
                },
                tableName + `_assetlinks_initialization`
            );

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_AssetLinksCustomResource_${schemaIndex}`,
                {
                    ...assetLinksSeed,
                    role: props.customResourceRole,
                    serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
                }
            );
            schemaIndex++;
        }

        // Database Schema - defaultDatabase
        if (props.config.app.metadataSchema.autoLoadDefaultDatabaseSchema) {
            const databaseFields = [createField("Location", "LLA")];

            const databaseSchemaId = "default-database-schema";
            const databaseCompositeKey = "GLOBAL:databaseMetadata";

            const databaseSeed = seedCalls(
                tableName,
                {
                    metadataSchemaId: {
                        S: databaseSchemaId,
                    },
                    "databaseId:metadataEntityType": {
                        S: databaseCompositeKey,
                    },
                    databaseId: {
                        S: "GLOBAL",
                    },
                    metadataSchemaEntityType: {
                        S: "databaseMetadata",
                    },
                    schemaName: {
                        S: "defaultDatabase",
                    },
                    fields: {
                        S: JSON.stringify({ fields: databaseFields }),
                    },
                    enabled: {
                        BOOL: true,
                    },
                    createdBy: {
                        S: "SYSTEM_USER",
                    },
                    modifiedBy: {
                        S: "SYSTEM_USER",
                    },
                },
                tableName + `_database_initialization`
            );

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_DatabaseCustomResource_${schemaIndex}`,
                {
                    ...databaseSeed,
                    role: props.customResourceRole,
                    serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
                }
            );
            schemaIndex++;
        }

        // Asset Schema - defaultAsset
        if (props.config.app.metadataSchema.autoLoadDefaultAssetSchema) {
            const assetFields = [createField("Location", "LLA")];

            const assetSchemaId = "default-asset-schema";
            const assetCompositeKey = "GLOBAL:assetMetadata";

            const assetSeed = seedCalls(
                tableName,
                {
                    metadataSchemaId: {
                        S: assetSchemaId,
                    },
                    "databaseId:metadataEntityType": {
                        S: assetCompositeKey,
                    },
                    databaseId: {
                        S: "GLOBAL",
                    },
                    metadataSchemaEntityType: {
                        S: "assetMetadata",
                    },
                    schemaName: {
                        S: "defaultAsset",
                    },
                    fields: {
                        S: JSON.stringify({ fields: assetFields }),
                    },
                    enabled: {
                        BOOL: true,
                    },
                    createdBy: {
                        S: "SYSTEM_USER",
                    },
                    modifiedBy: {
                        S: "SYSTEM_USER",
                    },
                },
                tableName + `_asset_initialization`
            );

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_AssetCustomResource_${schemaIndex}`,
                {
                    ...assetSeed,
                    role: props.customResourceRole,
                    serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
                }
            );
            schemaIndex++;
        }

        // File Schema - defaultAssetFile3dModel with file type restrictions
        if (props.config.app.metadataSchema.autoLoadDefaultAssetFileSchema) {
            const fileFields = [createField("Polygon_Count", "STRING")];

            const fileSchemaId = "default-file-schema-3dmodel";
            const fileCompositeKey = "GLOBAL:fileAttribute";

            const fileSeed = seedCalls(
                tableName,
                {
                    metadataSchemaId: {
                        S: fileSchemaId,
                    },
                    "databaseId:metadataEntityType": {
                        S: fileCompositeKey,
                    },
                    databaseId: {
                        S: "GLOBAL",
                    },
                    metadataSchemaEntityType: {
                        S: "fileAttribute",
                    },
                    schemaName: {
                        S: "defaultAssetFile3dModel",
                    },
                    fileKeyTypeRestriction: {
                        S: ".glb,.usd,.obj,.fbx,.gltf,.stl,.usdz",
                    },
                    fields: {
                        S: JSON.stringify({ fields: fileFields }),
                    },
                    enabled: {
                        BOOL: true,
                    },
                    createdBy: {
                        S: "SYSTEM_USER",
                    },
                    modifiedBy: {
                        S: "SYSTEM_USER",
                    },
                },
                tableName + `_file_initialization`
            );

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_FileCustomResource_${schemaIndex}`,
                {
                    ...fileSeed,
                    role: props.customResourceRole,
                    serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
                }
            );
            schemaIndex++;
        }

        // Add CDK Nag suppressions
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Custom resource role requires DynamoDB PutItem permissions for metadata schema initialization. The permissions are scoped to the specific metadata schema table created by this deployment.",
                },
            ],
            true
        );
    }
}
