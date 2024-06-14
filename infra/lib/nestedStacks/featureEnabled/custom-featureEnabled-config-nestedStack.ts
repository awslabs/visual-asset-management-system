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
         * Use the AWS SDK to add records to dynamoDB "App Features Enabled", e.g.
         *
         * @type {AwsCustomResource}
         *
         * @see https://docs.aws.amazon.com/cdk/api/latest/docs/@aws-cdk_custom-resources.AwsCustomResource.html
         */

        const appFeatureItems: any[] = [];
        props.featuresEnabled.forEach((feature) =>
            appFeatureItems.push({
                featureName: { S: feature },
            })
        );

        this.insertMultipleRecord(props.appFeatureEnabledTable, appFeatureItems, props.kmsKey);
    }

    //Note: Allows for 25 items to be written as part of BatchWriteItem. If more needed, batch over many different AwsCustomResource
    //Third Party Blog: https://dev.to/elthrasher/exploring-aws-cdk-loading-dynamodb-with-custom-resources-jlf,
    // https://kevin-van-ingen.medium.com/aws-cdk-custom-resources-for-dynamodb-inserts-2d79cb1ae395
    private insertMultipleRecord(
        appFeatureEnabledTable: dynamodb.Table,
        items: any[],
        kmsKey?: kms.IKey
    ) {
        //const records = this.constructBatchInsertObject(items, tableName);

        const awsSdkCall: AwsSdkCall = {
            service: "DynamoDB",
            action: "batchWriteItem",
            physicalResourceId: PhysicalResourceId.of(Date.now().toString()),
            //parameters: records
            parameters: {
                RequestItems: {
                    [appFeatureEnabledTable.tableName]: this.constructBatchInsertObject(items),
                },
            },
        };

        const customResourcePolicy = AwsCustomResourcePolicy.fromStatements([
            new PolicyStatement({
                sid: "DynamoWriteAccess",
                effect: Effect.ALLOW,
                actions: ["dynamodb:BatchWriteItem"],
                resources: [appFeatureEnabledTable.tableArn],
            }),
        ]);

        if (kmsKey) {
            customResourcePolicy.statements.push(kmsKeyPolicyStatementGenerator(kmsKey));
        }

        const customResource: AwsCustomResource = new AwsCustomResource(
            this,
            "appFeatureEnabled_tablePopulate_custom_resource",
            {
                onCreate: awsSdkCall,
                onUpdate: awsSdkCall,
                installLatestAwsSdk: false,
                policy: customResourcePolicy,
                timeout: Duration.minutes(5),
            }
        );

        customResource.node.addDependency(appFeatureEnabledTable);
    }

    // private constructBatchInsertObject(items: any[], tableName: string) {
    //     const itemsAsDynamoPutRequest: any[] = [];
    //     items.forEach(item => itemsAsDynamoPutRequest.push({
    //     PutRequest: {
    //         Item: item
    //     }
    //     }));
    //     const records: DynamoInsert =
    //         {
    //         RequestItems: {}
    //         };
    //     records.RequestItems[tableName] = itemsAsDynamoPutRequest;
    //     return records;
    // }

    private constructBatchInsertObject(items: any[]) {
        const itemsAsDynamoPutRequest: any[] = [];
        items.forEach((item) =>
            itemsAsDynamoPutRequest.push({
                PutRequest: {
                    Item: item,
                },
            })
        );
        return itemsAsDynamoPutRequest;
    }
}
