/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { storageResources } from "../storage/storageBuilder-nestedStack";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import { NagSuppressions } from "cdk-nag";
import { OpensearchServerlessConstruct } from "./constructs/opensearch-serverless";
import { OpensearchProvisionedConstruct } from "./constructs/opensearch-provisioned";
import {
    buildSearchFunction,
    buildFileIndexingFunction,
    buildAssetIndexingFunction,
    buildReindexerFunction,
} from "../../lambdaBuilder/searchIndexBucketSyncFunctions";
import { attachFunctionToApi } from "../apiLambda/apiBuilder-nestedStack";
import { NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as cdk from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Service } from "../../helper/service-helper";
import * as cr from "aws-cdk-lib/custom-resources";

export class SearchBuilderNestedStack extends NestedStack {
    constructor(
        parent: Construct,
        name: string,
        config: Config.Config,
        api: apigwv2.HttpApi,
        storageResources: storageResources,
        lambdaCommonBaseLayer: LayerVersion,
        vpc: ec2.IVpc,
        subnets: ec2.ISubnet[]
    ) {
        super(parent, name);

        searchBuilder(this, config, api, storageResources, lambdaCommonBaseLayer, vpc, subnets);
    }
}

export function searchBuilder(
    scope: Construct,
    config: Config.Config,
    api: apigwv2.HttpApi,
    storageResources: storageResources,
    lambdaCommonBaseLayer: LayerVersion,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
) {
    const searchFun = buildSearchFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );

    attachFunctionToApi(scope, searchFun, {
        routePath: "/search",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    attachFunctionToApi(scope, searchFun, {
        routePath: "/search",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    // Add simple search endpoint
    attachFunctionToApi(scope, searchFun, {
        routePath: "/search/simple",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });

    let fileIndexingFunction: lambda.Function | undefined = undefined;
    let assetIndexingFunction: lambda.Function | undefined = undefined;
    let reindexerFunction: lambda.Function | undefined = undefined;

    if (config.app.openSearch.useServerless.enabled) {
        //Serverless Deployment
        const aoss = new OpensearchServerlessConstruct(scope, "AOSS", {
            config: config,
            principalArn: [],
            storageResources: storageResources,
            vpc: vpc,
            subnets: subnets,
        });

        const osEndpointOutput = new cdk.CfnOutput(
            scope,
            "OpenSearchServerlessDomainEndpointOutput",
            {
                value: aoss.aossEndpointUrl,
                description: "The HTTP endpoint for the serverless open search domain",
            }
        );

        // Build file indexer function
        fileIndexingFunction = buildFileIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        // Build asset indexer function
        assetIndexingFunction = buildAssetIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        // Build reindexer function (always created regardless of reindexOnDeploy config)
        reindexerFunction = buildReindexerFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        // Create SQS queues for indexers and subscribe to SNS topics
        // File indexer SQS queue
        const fileIndexerSqsQueue = new sqs.Queue(scope, "FileIndexerSqsQueue", {
            queueName: `${config.name}-${config.app.baseStackName}-fileIndexer`,
            visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
        });
        fileIndexerSqsQueue.grantSendMessages(Service("SNS").Principal);

        // Subscribe file indexer queue to file indexer SNS topic
        storageResources.sns.fileIndexerSnsTopic.addSubscription(
            new SqsSubscription(fileIndexerSqsQueue)
        );

        fileIndexerSqsQueue.grantConsumeMessages(fileIndexingFunction);

        // Setup event source mapping for file indexer with GovCloud support
        if (config.app.govCloud.enabled) {
            const esmFileIndexer = new lambda.EventSourceMapping(
                scope,
                "FileIndexerSqsEventSource",
                {
                    eventSourceArn: fileIndexerSqsQueue.queueArn,
                    target: fileIndexingFunction,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsmFileIndexer = esmFileIndexer.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmFileIndexer.addPropertyDeletionOverride("Tags");
        } else {
            fileIndexingFunction.addEventSource(
                new eventsources.SqsEventSource(fileIndexerSqsQueue, {
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                })
            );
        }

        // Asset indexer SQS queue
        const assetIndexerSqsQueue = new sqs.Queue(scope, "AssetIndexerSqsQueue", {
            queueName: `${config.name}-${config.app.baseStackName}-assetIndexer`,
            visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
        });
        assetIndexerSqsQueue.grantSendMessages(Service("SNS").Principal);

        // Subscribe asset indexer queue to asset indexer SNS topic
        storageResources.sns.assetIndexerSnsTopic.addSubscription(
            new SqsSubscription(assetIndexerSqsQueue)
        );

        assetIndexerSqsQueue.grantConsumeMessages(assetIndexingFunction);

        // Setup event source mapping for asset indexer with GovCloud support
        if (config.app.govCloud.enabled) {
            const esmAssetIndexer = new lambda.EventSourceMapping(
                scope,
                "AssetIndexerSqsEventSource",
                {
                    eventSourceArn: assetIndexerSqsQueue.queueArn,
                    target: assetIndexingFunction,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsmAssetIndexer = esmAssetIndexer.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmAssetIndexer.addPropertyDeletionOverride("Tags");
        } else {
            assetIndexingFunction.addEventSource(
                new eventsources.SqsEventSource(assetIndexerSqsQueue, {
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                })
            );
        }

        // Grant OpenSearch access to both indexers
        aoss.grantCollectionAccess(fileIndexingFunction);
        aoss.grantCollectionAccess(assetIndexingFunction);
        aoss.grantVPCeAccess(fileIndexingFunction);
        aoss.grantVPCeAccess(assetIndexingFunction);

        //grant search function access to collection and VPCe
        aoss.grantCollectionAccess(searchFun);
        aoss.grantVPCeAccess(searchFun);

        // Grant OpenSearch access to reindexer
        aoss.grantCollectionAccess(reindexerFunction);
        aoss.grantVPCeAccess(reindexerFunction);
    } else if (config.app.openSearch.useProvisioned.enabled) {
        //Provisioned Deployment
        const aos = new OpensearchProvisionedConstruct(scope, "AOS", {
            storageResources: storageResources,
            config: config,
            vpc: vpc,
            subnets: subnets,
            dataNodeInstanceType:
                config.app.openSearch.useProvisioned.dataNodeInstanceType &&
                config.app.openSearch.useProvisioned.dataNodeInstanceType != ""
                    ? config.app.openSearch.useProvisioned.dataNodeInstanceType
                    : undefined,
            masterNodeInstanceType:
                config.app.openSearch.useProvisioned.masterNodeInstanceType &&
                config.app.openSearch.useProvisioned.masterNodeInstanceType != ""
                    ? config.app.openSearch.useProvisioned.masterNodeInstanceType
                    : undefined,
            ebsVolumeSize: config.app.openSearch.useProvisioned.ebsInstanceNodeSizeGb
                ? config.app.openSearch.useProvisioned.ebsInstanceNodeSizeGb
                : undefined,
        });

        const osEndpointOutput = new cdk.CfnOutput(
            scope,
            "OpenSearchProvisionedDomainEndpointOutput",
            {
                value: aos.domainEndpoint,
                description: "The HTTP endpoint for the provisioned open search domain",
            }
        );

        // Build file indexer function
        fileIndexingFunction = buildFileIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        // Build asset indexer function
        assetIndexingFunction = buildAssetIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        // Build reindexer function (always created regardless of reindexOnDeploy config)
        reindexerFunction = buildReindexerFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );

        // Create SQS queues for indexers and subscribe to SNS topics
        // File indexer SQS queue
        const fileIndexerSqsQueue = new sqs.Queue(scope, "FileIndexerSqsQueue", {
            queueName: `${config.name}-${config.app.baseStackName}-fileIndexer`,
            visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
        });
        fileIndexerSqsQueue.grantSendMessages(Service("SNS").Principal);

        // Subscribe file indexer queue to file indexer SNS topic
        storageResources.sns.fileIndexerSnsTopic.addSubscription(
            new SqsSubscription(fileIndexerSqsQueue)
        );

        fileIndexerSqsQueue.grantConsumeMessages(fileIndexingFunction);

        // Setup event source mapping for file indexer with GovCloud support
        if (config.app.govCloud.enabled) {
            const esmFileIndexer = new lambda.EventSourceMapping(
                scope,
                "FileIndexerSqsEventSource",
                {
                    eventSourceArn: fileIndexerSqsQueue.queueArn,
                    target: fileIndexingFunction,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsmFileIndexer = esmFileIndexer.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmFileIndexer.addPropertyDeletionOverride("Tags");
        } else {
            fileIndexingFunction.addEventSource(
                new eventsources.SqsEventSource(fileIndexerSqsQueue, {
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                })
            );
        }

        // Asset indexer SQS queue
        const assetIndexerSqsQueue = new sqs.Queue(scope, "AssetIndexerSqsQueue", {
            queueName: `${config.name}-${config.app.baseStackName}-assetIndexer`,
            visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
        });
        assetIndexerSqsQueue.grantSendMessages(Service("SNS").Principal);

        // Subscribe asset indexer queue to asset indexer SNS topic
        storageResources.sns.assetIndexerSnsTopic.addSubscription(
            new SqsSubscription(assetIndexerSqsQueue)
        );

        assetIndexerSqsQueue.grantConsumeMessages(assetIndexingFunction);

        // Setup event source mapping for asset indexer with GovCloud support
        if (config.app.govCloud.enabled) {
            const esmAssetIndexer = new lambda.EventSourceMapping(
                scope,
                "AssetIndexerSqsEventSource",
                {
                    eventSourceArn: assetIndexerSqsQueue.queueArn,
                    target: assetIndexingFunction,
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                }
            );
            const cfnEsmAssetIndexer = esmAssetIndexer.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmAssetIndexer.addPropertyDeletionOverride("Tags");
        } else {
            assetIndexingFunction.addEventSource(
                new eventsources.SqsEventSource(assetIndexerSqsQueue, {
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                })
            );
        }

        // Grant OpenSearch access to both indexers
        aos.grantOSDomainAccess(fileIndexingFunction);
        aos.grantOSDomainAccess(assetIndexingFunction);

        //grant search function access to AOS
        aos.grantOSDomainAccess(searchFun);

        // Grant OpenSearch access to reindexer
        aos.grantOSDomainAccess(reindexerFunction);
    }

    /////////////////////////////////////////////////////////////////////////////
    // Setup Custom Resource for Reindexing
    /////////////////////////////////////////////////////////////////////////////

    // Create custom resource to trigger reindex on deployment if enabled
    if (reindexerFunction && config.app.openSearch.reindexOnCdkDeploy) {
        const reindexProvider = new cr.Provider(scope, "OsReindexProvider", {
            onEventHandler: reindexerFunction,
        });

        new cdk.CustomResource(scope, "ReindexTrigger", {
            serviceToken: reindexProvider.serviceToken,
            properties: {
                Operation: "both",
                ClearIndexes: "true",
                Timestamp: Date.now().toString(),
            },
        });
    }

    //Setup final index output
    const openSearchIndexAssetSOutput = new cdk.CfnOutput(scope, "OpenSearchIndexAssetsOutput", {
        value: config.openSearchAssetIndexName,
        description: "The OpenSearch index name for assets",
    });

    const openSearchIndexFilesOutput = new cdk.CfnOutput(scope, "OpenSearchIndexFilesOutput", {
        value: config.openSearchFileIndexName,
        description: "The OpenSearch index name for files",
    });

    // Output reindexer function name if it was created
    if (reindexerFunction) {
        const reindexerFunctionOutput = new cdk.CfnOutput(scope, "ReindexerFunctionNameOutput", {
            value: reindexerFunction.functionName,
            description: "The Lambda function name for the OpenSearch reindexer",
        });
    }

    //Nag supressions
    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-SQS3",
                reason: "Intended not to use DLQs for these types of SQS events. Files easily redriven based on the logic of assets.",
            },
        ],
        true
    );

    NagSuppressions.addResourceSuppressions(
        scope,
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

    NagSuppressions.addResourceSuppressions(scope, [
        {
            id: "AwsSolutions-L1",
            reason: "Configured as intended.",
        },
    ]);

    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "Configured as intended.",
                appliesTo: [
                    {
                        regex: "/.*$/g",
                    },
                ],
            },
        ],
        true
    );
}
