/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    AwsCustomResource,
    AwsCustomResourcePolicy,
    AwsSdkCall,
    PhysicalResourceId,
} from "aws-cdk-lib/custom-resources";
import { Effect, PolicyStatement } from "aws-cdk-lib/aws-iam";
import { Duration, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import { kmsKeyPolicyStatementGenerator } from "../../helper/security";

/* eslint-disable @typescript-eslint/no-empty-interface */
export interface CustomFeatureEnabledConfigNestedStackProps {
    appFeatureEnabledTable: dynamodb.Table;
    featuresEnabled: string[];
    kmsKey?: kms.IKey;
}

interface RequestItem {
    [key: string]: any[];
}

interface DynamoInsert {
    RequestItems: RequestItem;
}

interface IAppFeatureEnabled {
    featureName: { S: string };
}

const defaultProps: Partial<CustomFeatureEnabledConfigNestedStackProps> = {};

/**
 * Custom configuration for VAMS App Features Enabled.
 * This nested stack manages the appFeaturesEnabled DynamoDB table by:
 * 1. Deduplicating the input features to prevent duplicate key errors
 * 2. Using PutItem (not BatchWriteItem) to overwrite existing entries
 * This ensures the table always reflects the current deployment state without duplicate key errors.
 */
export class CustomFeatureEnabledConfigNestedStack extends NestedStack {
    constructor(
        parent: Construct,
        name: string,
        props: CustomFeatureEnabledConfigNestedStackProps
    ) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        /**
         * Deduplicate features using Set to ensure no duplicate entries.
         * This is a defensive measure in case duplicates are passed from core-stack.
         */
        const uniqueFeatures = Array.from(new Set(props.featuresEnabled));

        /**
         * Convert unique features to DynamoDB item format
         */
        const appFeatureItems: any[] = [];
        uniqueFeatures.forEach((feature) =>
            appFeatureItems.push({
                featureName: { S: feature },
            })
        );

        this.replaceTableRecords(props.appFeatureEnabledTable, appFeatureItems, props.kmsKey);
    }

    /**
     * Replace all records in the table with the new feature list.
     *
     * Strategy:
     * 1. Use PutItem for each feature (overwrites if exists, creates if not)
     * 2. This avoids duplicate key errors from BatchWriteItem
     * 3. Items are already deduplicated before this method is called
     *
     * Note: For tables with many features (>25), this approach is more reliable
     * than BatchWriteItem which has a 25-item limit and fails on duplicates.
     *
     * @param appFeatureEnabledTable - The DynamoDB table to update
     * @param items - The new feature items to insert (already deduplicated)
     * @param kmsKey - Optional KMS key for encryption
     */
    private replaceTableRecords(
        appFeatureEnabledTable: dynamodb.Table,
        items: any[],
        kmsKey?: kms.IKey
    ) {
        // Create policy statements for DynamoDB operations
        const customResourcePolicy = AwsCustomResourcePolicy.fromStatements([
            new PolicyStatement({
                sid: "DynamoWriteAccess",
                effect: Effect.ALLOW,
                actions: ["dynamodb:PutItem"],
                resources: [appFeatureEnabledTable.tableArn],
            }),
        ]);

        // Add KMS permissions if encryption key is provided
        if (kmsKey) {
            customResourcePolicy.statements.push(kmsKeyPolicyStatementGenerator(kmsKey));
        }

        // Create a custom resource for each unique feature
        // Using PutItem instead of BatchWriteItem to avoid duplicate key errors
        // PutItem will overwrite existing items with the same key
        items.forEach((item, index) => {
            const featureName = item.featureName.S;

            const putItemCall: AwsSdkCall = {
                service: "DynamoDB",
                action: "putItem",
                physicalResourceId: PhysicalResourceId.of(
                    `${appFeatureEnabledTable.tableName}-${featureName}`
                ),
                parameters: {
                    TableName: appFeatureEnabledTable.tableName,
                    Item: item,
                },
            };

            const customResource = new AwsCustomResource(
                this,
                `appFeatureEnabled_${featureName}_custom_resource`,
                {
                    onCreate: putItemCall,
                    onUpdate: putItemCall,
                    installLatestAwsSdk: false,
                    policy: customResourcePolicy,
                    timeout: Duration.minutes(5),
                }
            );

            customResource.node.addDependency(appFeatureEnabledTable);
        });
    }
}
