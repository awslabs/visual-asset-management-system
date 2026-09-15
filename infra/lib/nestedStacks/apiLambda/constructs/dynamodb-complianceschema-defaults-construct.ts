/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import * as cdk from "aws-cdk-lib";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { AwsCustomResource, AwsSdkCall, PhysicalResourceId } from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";
import { Config } from "../../../../config/config";
import { Service } from "../../../helper/service-helper";
import { kmsKeyPolicyStatementGenerator } from "../../../helper/security";
import { NagSuppressions } from "cdk-nag";

export interface DynamoDbComplianceSchemaDefaultsConstructProps extends cdk.StackProps {
    storageResources: storageResources;
    config: Config;
}

/**
 * Default GLOBAL compliance schema in the `vams-rules-v1` format
 * (backend/backend/models/compliance.py VamsRulesV1Schema). One metadata rule validates a bound
 * asset's metadata against the GLOBAL `defaultAsset` metadata schema that the metadata schema
 * defaults construct seeds; `warn` enforcement records the verdict without quarantining.
 */
const DEFAULT_COMPLIANCE_SCHEMA = {
    schemaFormat: "vams-rules-v1",
    rules: {
        defaultAssetMetadata: {
            ruleType: "metadata",
            enforcement: "warn",
            metadataSchemaRef: {
                databaseId: "GLOBAL",
                schemaName: "defaultAsset",
            },
            checks: [
                {
                    name: "requiredFieldsAndTypes",
                    description:
                        "Required fields of the default asset metadata schema are present and typed as declared",
                    validateRequired: true,
                    validateTypes: true,
                },
            ],
        },
    },
};

export class DynamoDbComplianceSchemaDefaultsConstruct extends Construct {
    constructor(
        parent: Construct,
        name: string,
        props: DynamoDbComplianceSchemaDefaultsConstructProps
    ) {
        super(parent, name);

        const schemaTable = props.storageResources.dynamo.complianceSchemaStorageTable;

        const customResourceRole = new iam.Role(this, "ComplianceSchemaDefaultsRole", {
            assumedBy: Service("LAMBDA").Principal,
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        });
        schemaTable.grantWriteData(customResourceRole);
        if (props.storageResources.encryption.kmsKey) {
            customResourceRole.addToPolicy(
                kmsKeyPolicyStatementGenerator(props.storageResources.encryption.kmsKey)
            );
        }

        const now = new Date().toISOString();

        const awsSdkCall: AwsSdkCall = {
            service: "DynamoDB",
            action: "putItem",
            parameters: {
                TableName: schemaTable.tableName,
                Item: {
                    schemaName: { S: "default-compliance-schema" },
                    internalVersion: { N: "1" },
                    databaseId: { S: "GLOBAL" },
                    description: {
                        S: "Default compliance schema: validates bound assets against the default asset metadata schema. Deployed automatically with VAMS.",
                    },
                    schemaBody: { S: JSON.stringify(DEFAULT_COMPLIANCE_SCHEMA) },
                    registeredAt: { S: now },
                    registeredBy: { S: "SYSTEM" },
                },
                ConditionExpression:
                    "attribute_not_exists(schemaName) AND attribute_not_exists(internalVersion)",
            },
            physicalResourceId: PhysicalResourceId.of(
                schemaTable.tableName + "_default_schema_initialization"
            ),
        };

        new AwsCustomResource(this, "ComplianceDefaultSchemaCustomResource", {
            onCreate: awsSdkCall,
            role: customResourceRole,
        });

        NagSuppressions.addResourceSuppressions(
            customResourceRole,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "AWSLambdaBasicExecutionRole grants the custom-resource Lambda its CloudWatch log stream only.",
                    appliesTo: [{ regex: "/.*AWSLambdaBasicExecutionRole$/g" }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Index ARNs of the one compliance schema table the seed writes; a table grant covers its global secondary indexes.",
                    appliesTo: [{ regex: "/^Resource::<.*Table.*\\.Arn>/index/\\*$/g" }],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "KMS action wildcards (GenerateDataKey*, ReEncrypt*) on the one VAMS key ARN.",
                    appliesTo: [{ regex: "/^Action::kms:(.*)\\*$/g" }],
                },
            ],
            true
        );
    }
}
