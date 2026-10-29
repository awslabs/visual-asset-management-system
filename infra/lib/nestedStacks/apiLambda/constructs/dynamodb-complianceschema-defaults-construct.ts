/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import { Duration } from "aws-cdk-lib";
import { Construct } from "constructs";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import * as Config from "../../../../config/config";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    suppressCdkNagLambda,
} from "../../../helper/security";

export interface DynamoDbComplianceSchemaDefaultsConstructProps extends cdk.StackProps {
    storageResources: storageResources;
    config: Config.Config;
    lambdaCommonBaseLayer: lambda.ILayerVersion;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
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

/**
 * The schema row the seed writes, as a DynamoDB attribute-value map. It carries no timestamp:
 * `registeredAt` is stamped by the handler when it writes the row, so the synthesized template is the
 * same on every synth of the same source and the seed runs only when this row changes.
 */
const DEFAULT_COMPLIANCE_SCHEMA_ITEM = {
    schemaName: { S: "default-compliance-schema" },
    internalVersion: { N: "1" },
    databaseId: { S: "GLOBAL" },
    // Written once and never refreshed, so the text must stay true after autoLoadDefaultSchema is
    // turned off: say where the schema came from, not that the deployment maintains it.
    description: {
        S: "Default compliance schema: validates bound assets against the default asset metadata schema. Written once at deployment by app.compliance.autoLoadDefaultSchema; later deployments do not change or remove it.",
    },
    schemaBody: { S: JSON.stringify(DEFAULT_COMPLIANCE_SCHEMA) },
    registeredBy: { S: "SYSTEM_USER" },
    isSystem: { BOOL: true },
};

/**
 * Seeds the GLOBAL `default-compliance-schema` through a Lambda-backed custom resource
 * (`backend/backend/customResources/complianceDefaultSchemaSeed.py`).
 *
 * Create and Update put the row with `registeredAt` set to the time of the write, conditional on the
 * row not existing, and treat an existing row as success: a redeploy, a replaced seed resource, or a
 * retained table adopted by a new stack never overwrites it. Delete leaves the row in place. The
 * function can issue `dynamodb:PutItem` on the schema table and nothing else.
 */
export class DynamoDbComplianceSchemaDefaultsConstruct extends Construct {
    constructor(
        parent: Construct,
        name: string,
        props: DynamoDbComplianceSchemaDefaultsConstructProps
    ) {
        super(parent, name);

        const schemaTable = props.storageResources.dynamo.complianceSchemaStorageTable;
        const config = props.config;
        const inVpc = config.app.useGlobalVpc.enabled && config.app.useGlobalVpc.useForAllLambdas;

        const seedFunction = new lambda.Function(this, "ComplianceDefaultSchemaSeedFunction", {
            code: lambda.Code.fromAsset(path.join(__dirname, "../../../../../backend/backend")),
            handler: "customResources.complianceDefaultSchemaSeed.lambda_handler",
            runtime: Config.LAMBDA_PYTHON_RUNTIME,
            // The layer provides aws-lambda-powertools for safeLogger.
            layers: [props.lambdaCommonBaseLayer],
            timeout: Duration.minutes(5),
            // One PutItem per deployment that changes the seed; the API memory size buys nothing.
            memorySize: 256,
            vpc: inVpc ? props.vpc : undefined,
            vpcSubnets: inVpc ? { subnets: props.subnets } : undefined,
        });

        // The one call the handler makes. Not grantWriteData: the seed never updates or deletes.
        seedFunction.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["dynamodb:PutItem"],
                resources: [schemaTable.tableArn],
            })
        );
        kmsKeyLambdaPermissionAddToResourcePolicy(
            seedFunction,
            props.storageResources.encryption.kmsKey
        );
        suppressCdkNagLambda(seedFunction);

        const provider = new cr.Provider(this, "ComplianceDefaultSchemaSeedProvider", {
            onEventHandler: seedFunction,
        });

        // The properties are the row's content and its table, so the template changes only when the
        // default schema does. A new construct id (the previous seed was an AwsCustomResource) makes
        // an upgrade create this resource, whose conditional put leaves an existing row alone.
        new cdk.CustomResource(this, "ComplianceDefaultSchemaSeed", {
            serviceToken: provider.serviceToken,
            properties: {
                TableName: schemaTable.tableName,
                Item: JSON.stringify(DEFAULT_COMPLIANCE_SCHEMA_ITEM),
            },
        });
    }
}
