/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import {
    AwsCustomResource,
    AwsSdkCall,
    PhysicalResourceId,
} from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";
import { Config } from "../../../config/config";
import { NagSuppressions } from "cdk-nag";

export interface DynamoDbFmmSchemaDefaultsConstructProps extends cdk.StackProps {
    customResourceRole: iam.Role;
    storageResources: storageResources;
    config: Config;
}

const DEFAULT_COMPLIANCE_SCHEMA = {
    type: "object",
    required: ["name", "owner", "classification"],
    properties: {
        name: {
            type: "string",
            description: "Asset name or identifier",
        },
        version: {
            type: "string",
            description: "Schema or asset version",
        },
        classification: {
            type: "string",
            enum: ["public", "internal", "confidential", "restricted"],
            description: "Data classification level",
        },
        owner: {
            type: "string",
            description: "Owner email address",
        },
        retention_days: {
            type: "integer",
            minimum: 1,
            maximum: 3650,
            description: "Data retention period in days",
        },
        department: {
            type: "string",
            description: "Owning department or team",
        },
    },
    additionalProperties: true,
};

export class DynamoDbFmmSchemaDefaultsConstruct extends Construct {
    constructor(
        parent: Construct,
        name: string,
        props: DynamoDbFmmSchemaDefaultsConstructProps
    ) {
        super(parent, name);

        if (!props.config.app.federatedModelManagement.autoLoadDefaultSchema) {
            return;
        }

        const now = new Date().toISOString();

        const awsSdkCall: AwsSdkCall = {
            service: "DynamoDB",
            action: "putItem",
            parameters: {
                TableName: props.storageResources.dynamo.fmmSchemaStorageTable.tableName,
                Item: {
                    schemaName: { S: "default-compliance-schema" },
                    internalVersion: { N: "1" },
                    databaseId: { S: "GLOBAL" },
                    description: {
                        S: "Default compliance schema requiring name, owner, and classification fields. Deployed automatically with VAMS.",
                    },
                    schemaBody: { S: JSON.stringify(DEFAULT_COMPLIANCE_SCHEMA) },
                    registeredAt: { S: now },
                    registeredBy: { S: "SYSTEM" },
                },
                ConditionExpression:
                    "attribute_not_exists(schemaName) AND attribute_not_exists(internalVersion)",
            },
            physicalResourceId: PhysicalResourceId.of(
                props.storageResources.dynamo.fmmSchemaStorageTable.tableName +
                    "_default_schema_initialization"
            ),
        };

        new AwsCustomResource(this, "FmmDefaultSchemaCustomResource", {
            onCreate: awsSdkCall,
            role: props.customResourceRole,
        });

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Custom resource role requires DynamoDB PutItem permissions for FMM default schema initialization. Scoped to the specific FMM schema table.",
                },
            ],
            true
        );
    }
}
