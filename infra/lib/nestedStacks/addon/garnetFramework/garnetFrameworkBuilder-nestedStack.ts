/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as gameliftstreams from "aws-cdk-lib/aws-gameliftstreams";
import * as Config from "../../../../config/config";
import * as kms from "aws-cdk-lib/aws-kms";
import {
    buildGarnetDataIndexDatabaseFunction,
    buildGarnetDataIndexAssetFunction,
    buildGarnetDataIndexFileFunction,
} from "./lambdaBuilder/garnetIndexerFunctions";
import { NagSuppressions } from "cdk-nag";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { Service } from "../../../helper/service-helper";
import * as cr from "aws-cdk-lib/custom-resources";

export interface garnetFrameworkBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    isolatedSubnets: ec2.ISubnet[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
}

/**
 * Default input properties
 */
const defaultProps: Partial<garnetFrameworkBuilderNestedStackProps> = {};

export class GarnetFrameworkBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: garnetFrameworkBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        // Build Garnet indexer functions
        const garnetDatabaseIndexerFunction = buildGarnetDataIndexDatabaseFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.isolatedSubnets
        );

        const garnetAssetIndexerFunction = buildGarnetDataIndexAssetFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.isolatedSubnets
        );

        const garnetFileIndexerFunction = buildGarnetDataIndexFileFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.isolatedSubnets
        );

        // Create SQS queues for Garnet indexers and subscribe to SNS topics
        // Garnet Database indexer SQS queue
        const garnetDatabaseIndexerSqsQueue = new sqs.Queue(this, "GarnetDatabaseIndexerSqsQueue", {
            queueName: `${props.config.name}-${props.config.app.baseStackName}-garnetDatabaseIndexer`,
            visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
            encryption: props.storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: props.storageResources.encryption.kmsKey,
            enforceSSL: true,
        });
        garnetDatabaseIndexerSqsQueue.grantSendMessages(Service("SNS").Principal);

        // Subscribe Garnet database indexer queue to database indexer SNS topic
        props.storageResources.sns.databaseIndexerSnsTopic.addSubscription(
            new SqsSubscription(garnetDatabaseIndexerSqsQueue)
        );

        garnetDatabaseIndexerSqsQueue.grantConsumeMessages(garnetDatabaseIndexerFunction);

        // Setup event source mapping for Garnet database indexer with GovCloud support
        if (props.config.app.govCloud.enabled) {
            const esmGarnetDatabase = new lambda.EventSourceMapping(
                this,
                "GarnetDatabaseIndexerSqsEventSource",
                {
                    eventSourceArn: garnetDatabaseIndexerSqsQueue.queueArn,
                    target: garnetDatabaseIndexerFunction,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsmGarnetDatabase = esmGarnetDatabase.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmGarnetDatabase.addPropertyDeletionOverride("Tags");
        } else {
            garnetDatabaseIndexerFunction.addEventSource(
                new eventsources.SqsEventSource(garnetDatabaseIndexerSqsQueue, {
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                })
            );
        }

        // Garnet Asset indexer SQS queue
        const garnetAssetIndexerSqsQueue = new sqs.Queue(this, "GarnetAssetIndexerSqsQueue", {
            queueName: `${props.config.name}-${props.config.app.baseStackName}-garnetAssetIndexer`,
            visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
            encryption: props.storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: props.storageResources.encryption.kmsKey,
            enforceSSL: true,
        });
        garnetAssetIndexerSqsQueue.grantSendMessages(Service("SNS").Principal);

        // Subscribe Garnet asset indexer queue to asset indexer SNS topic
        props.storageResources.sns.assetIndexerSnsTopic.addSubscription(
            new SqsSubscription(garnetAssetIndexerSqsQueue)
        );

        garnetAssetIndexerSqsQueue.grantConsumeMessages(garnetAssetIndexerFunction);

        // Setup event source mapping for Garnet asset indexer with GovCloud support
        if (props.config.app.govCloud.enabled) {
            const esmGarnetAsset = new lambda.EventSourceMapping(
                this,
                "GarnetAssetIndexerSqsEventSource",
                {
                    eventSourceArn: garnetAssetIndexerSqsQueue.queueArn,
                    target: garnetAssetIndexerFunction,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsmGarnetAsset = esmGarnetAsset.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmGarnetAsset.addPropertyDeletionOverride("Tags");
        } else {
            garnetAssetIndexerFunction.addEventSource(
                new eventsources.SqsEventSource(garnetAssetIndexerSqsQueue, {
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                })
            );
        }

        // Garnet File indexer SQS queue
        const garnetFileIndexerSqsQueue = new sqs.Queue(this, "GarnetFileIndexerSqsQueue", {
            queueName: `${props.config.name}-${props.config.app.baseStackName}-garnetFileIndexer`,
            visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
            encryption: props.storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: props.storageResources.encryption.kmsKey,
            enforceSSL: true,
        });
        garnetFileIndexerSqsQueue.grantSendMessages(Service("SNS").Principal);

        // Subscribe Garnet file indexer queue to file indexer SNS topic
        props.storageResources.sns.fileIndexerSnsTopic.addSubscription(
            new SqsSubscription(garnetFileIndexerSqsQueue)
        );

        garnetFileIndexerSqsQueue.grantConsumeMessages(garnetFileIndexerFunction);

        // Setup event source mapping for Garnet file indexer with GovCloud support
        if (props.config.app.govCloud.enabled) {
            const esmGarnetFile = new lambda.EventSourceMapping(
                this,
                "GarnetFileIndexerSqsEventSource",
                {
                    eventSourceArn: garnetFileIndexerSqsQueue.queueArn,
                    target: garnetFileIndexerFunction,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsmGarnetFile = esmGarnetFile.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmGarnetFile.addPropertyDeletionOverride("Tags");
        } else {
            garnetFileIndexerFunction.addEventSource(
                new eventsources.SqsEventSource(garnetFileIndexerSqsQueue, {
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                })
            );
        }

        //Nag supressions
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-SQS3",
                    reason: "Intended not to use DLQs for these types of SQS events. Files easily redriven based on the logic of assets.",
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: "Intend to use AWSLambdaBasicExecutionRole as is at this stage of this project.",
                    appliesTo: [
                        {
                            regex: "/.*AWSLambdaBasicExecutionRole$/g",
                        },
                    ],
                },
            ],
            true
        );
    }
}
