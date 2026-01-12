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

/**
 * Deploys default metadata schemas to DynamoDB table
 */
export class DynamoDbMetadataSchemaDefaultsConstruct extends Construct {
    constructor(
        parent: Construct,
        name: string,
        props: DynamoDbMetadataSchemaDefaultsConstructProps
    ) {
        super(parent, name);

        const now = new Date().toISOString();
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

            const awsSdkCallAssetLinks: AwsSdkCall = {
                service: "DynamoDB",
                action: "putItem",
                parameters: {
                    TableName: props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
                    Item: {
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
                        dateCreated: {
                            S: now,
                        },
                        dateModified: {
                            S: now,
                        },
                        createdBy: {
                            S: "SYSTEM",
                        },
                        modifiedBy: {
                            S: "SYSTEM",
                        },
                    },
                },
                physicalResourceId: PhysicalResourceId.of(
                    props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName +
                        `_assetlinks_initialization`
                ),
            };

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_AssetLinksCustomResource_${schemaIndex}`,
                {
                    onCreate: awsSdkCallAssetLinks,
                    onUpdate: awsSdkCallAssetLinks,
                    role: props.customResourceRole,
                }
            );
            schemaIndex++;
        }

        // Database Schema - defaultDatabase
        if (props.config.app.metadataSchema.autoLoadDefaultDatabaseSchema) {
            const databaseFields = [createField("Location", "LLA")];

            const databaseSchemaId = "default-database-schema";
            const databaseCompositeKey = "GLOBAL:databaseMetadata";

            const awsSdkCallDatabase: AwsSdkCall = {
                service: "DynamoDB",
                action: "putItem",
                parameters: {
                    TableName: props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
                    Item: {
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
                        dateCreated: {
                            S: now,
                        },
                        dateModified: {
                            S: now,
                        },
                        createdBy: {
                            S: "SYSTEM",
                        },
                        modifiedBy: {
                            S: "SYSTEM",
                        },
                    },
                },
                physicalResourceId: PhysicalResourceId.of(
                    props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName +
                        `_database_initialization`
                ),
            };

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_DatabaseCustomResource_${schemaIndex}`,
                {
                    onCreate: awsSdkCallDatabase,
                    onUpdate: awsSdkCallDatabase,
                    role: props.customResourceRole,
                }
            );
            schemaIndex++;
        }

        // Asset Schema - defaultAsset
        if (props.config.app.metadataSchema.autoLoadDefaultAssetSchema) {
            const assetFields = [createField("Location", "LLA")];

            const assetSchemaId = "default-asset-schema";
            const assetCompositeKey = "GLOBAL:assetMetadata";

            const awsSdkCallAsset: AwsSdkCall = {
                service: "DynamoDB",
                action: "putItem",
                parameters: {
                    TableName: props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
                    Item: {
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
                        dateCreated: {
                            S: now,
                        },
                        dateModified: {
                            S: now,
                        },
                        createdBy: {
                            S: "SYSTEM",
                        },
                        modifiedBy: {
                            S: "SYSTEM",
                        },
                    },
                },
                physicalResourceId: PhysicalResourceId.of(
                    props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName +
                        `_asset_initialization`
                ),
            };

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_AssetCustomResource_${schemaIndex}`,
                {
                    onCreate: awsSdkCallAsset,
                    onUpdate: awsSdkCallAsset,
                    role: props.customResourceRole,
                }
            );
            schemaIndex++;
        }

        // File Schema - defaultAssetFile3dModel with file type restrictions
        if (props.config.app.metadataSchema.autoLoadDefaultAssetFileSchema) {
            const fileFields = [createField("Polygon_Count", "STRING")];

            const fileSchemaId = "default-file-schema-3dmodel";
            const fileCompositeKey = "GLOBAL:fileAttribute";

            const awsSdkCallFile: AwsSdkCall = {
                service: "DynamoDB",
                action: "putItem",
                parameters: {
                    TableName: props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName,
                    Item: {
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
                        dateCreated: {
                            S: now,
                        },
                        dateModified: {
                            S: now,
                        },
                        createdBy: {
                            S: "SYSTEM",
                        },
                        modifiedBy: {
                            S: "SYSTEM",
                        },
                    },
                },
                physicalResourceId: PhysicalResourceId.of(
                    props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName +
                        `_file_initialization`
                ),
            };

            new AwsCustomResource(
                this,
                `metadataSchemaStorageTable_FileCustomResource_${schemaIndex}`,
                {
                    onCreate: awsSdkCallFile,
                    onUpdate: awsSdkCallFile,
                    role: props.customResourceRole,
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
