/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import { AwsCustomResource, AwsSdkCall, PhysicalResourceId } from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";
import { storageResources } from "../../../storage/storageBuilder-nestedStack";
import { SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT } from "../../../../helper/seedCustomResource";
import mirisStreamSchema from "./dynamodb-metadataschema-miris-stream.json";

export interface DynamoDbMetadataSchemaMirisConstructProps {
    customResourceRole: iam.IRole;
    storageResources: storageResources;
}

/** `modifiedBy` of the schema while no user has edited it since a deployment wrote it. */
const SEED_WRITER = { ":systemUser": { S: "SYSTEM_USER" } };

/**
 * Deploys the GLOBAL "Miris stream" file metadata schema to the metadata schema table.
 * The schema definition (fields, entity type, file extension restriction) is read from
 * dynamodb-metadataschema-miris-stream.json, which the backend model tests also validate.
 *
 * Create writes the item unless a row already exists that a user has edited; update refreshes the
 * row only while it still exists and no user has edited it. Every API edit, disabling included,
 * records its editor in `modifiedBy`, so an edited, disabled or deleted schema is left alone.
 */
export class DynamoDbMetadataSchemaMirisConstruct extends Construct {
    constructor(parent: Construct, name: string, props: DynamoDbMetadataSchemaMirisConstructProps) {
        super(parent, name);

        const tableName = props.storageResources.dynamo.metadataSchemaStorageTableV2.tableName;

        const seedCall = (conditionExpression: string): AwsSdkCall => ({
            service: "DynamoDB",
            action: "putItem",
            parameters: {
                TableName: tableName,
                Item: {
                    metadataSchemaId: {
                        S: mirisStreamSchema.metadataSchemaId,
                    },
                    "databaseId:metadataEntityType": {
                        S: `${mirisStreamSchema.databaseId}:${mirisStreamSchema.metadataSchemaEntityType}`,
                    },
                    databaseId: {
                        S: mirisStreamSchema.databaseId,
                    },
                    metadataSchemaEntityType: {
                        S: mirisStreamSchema.metadataSchemaEntityType,
                    },
                    schemaName: {
                        S: mirisStreamSchema.schemaName,
                    },
                    fileKeyTypeRestriction: {
                        S: mirisStreamSchema.fileKeyTypeRestriction,
                    },
                    fields: {
                        S: JSON.stringify({ fields: mirisStreamSchema.fields }),
                    },
                    enabled: {
                        BOOL: mirisStreamSchema.enabled,
                    },
                    createdBy: {
                        S: "SYSTEM_USER",
                    },
                    modifiedBy: {
                        S: "SYSTEM_USER",
                    },
                },
                ConditionExpression: conditionExpression,
                ExpressionAttributeValues: SEED_WRITER,
            },
            ignoreErrorCodesMatching: "ConditionalCheckFailedException",
            physicalResourceId: PhysicalResourceId.of(tableName + `_mirisstream_initialization`),
        });

        new AwsCustomResource(this, `metadataSchemaStorageTable_MirisStreamCustomResource`, {
            onCreate: seedCall(
                "attribute_not_exists(metadataSchemaId) OR modifiedBy = :systemUser"
            ),
            onUpdate: seedCall("attribute_exists(metadataSchemaId) AND modifiedBy = :systemUser"),
            role: props.customResourceRole,
            serviceTimeout: SEED_CUSTOM_RESOURCE_SERVICE_TIMEOUT,
        });
    }
}
