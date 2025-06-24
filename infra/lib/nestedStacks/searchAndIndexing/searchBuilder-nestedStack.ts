/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { storageResources } from "../storage/storageBuilder-nestedStack";
import { buildIndexingFunction } from "../../lambdaBuilder/searchIndexBucketSyncFunctions";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import { SqsEventSource } from "aws-cdk-lib/aws-lambda-event-sources";
import { NagSuppressions } from "cdk-nag";
import { OpensearchServerlessConstruct } from "./constructs/opensearch-serverless";
import { OpensearchProvisionedConstruct } from "./constructs/opensearch-provisioned";
import {
    buildSearchFunction,
    buildSqsBucketSyncFunction,
} from "../../lambdaBuilder/searchIndexBucketSyncFunctions";
import { attachFunctionToApi } from "../apiLambda/apiBuilder-nestedStack";
import { Stack, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as apigwv2 from "@aws-cdk/aws-apigatewayv2-alpha";
import * as cdk from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as s3AssetBuckets from "../../helper/s3AssetBuckets";
import { Service } from "../../helper/service-helper";
import * as iam from "aws-cdk-lib/aws-iam";
import { PropagatedTagSource } from "aws-cdk-lib/aws-ecs";

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

    let indexingS3ObjectMetadataFunction: lambda.Function | undefined = undefined;

    if (config.app.openSearch.useServerless.enabled) {
        //Serverless Deployment
        const aoss = new OpensearchServerlessConstruct(scope, "AOSS", {
            config: config,
            principalArn: [],
            storageResources: storageResources,
            vpc: vpc,
            subnets: subnets,
        });

        indexingS3ObjectMetadataFunction = buildIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            "m",
            config,
            vpc,
            subnets
        );

        const assetIndexingFunction = buildIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            "a",
            config,
            vpc,
            subnets
        );

        // Due to cdk upgrade, not all regions support tags for EventSourceMapping
        // this line should remove the tags for regions that dont support it (govcloud currently not supported)
        if (config.app.govCloud.enabled) {
            const esmIndexing = new lambda.EventSourceMapping(
                scope,
                "idxmDynamoDBEventSourceStorageResourcesBuilderMetadataStorageTable",
                {
                    target: indexingS3ObjectMetadataFunction,
                    eventSourceArn: storageResources.dynamo.metadataStorageTable.tableStreamArn,
                    startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                    batchSize: 100,
                }
            );
            const cfnEsmIndexing = esmIndexing.node.defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmIndexing.addPropertyDeletionOverride("Tags");

            const esmAssetIndexing = new lambda.EventSourceMapping(
                scope,
                "idxaDynamoDBEventSourceStorageResourcesBuilderAssetStorageTable",
                {
                    target: assetIndexingFunction,
                    eventSourceArn: storageResources.dynamo.assetStorageTable.tableStreamArn,
                    startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                    batchSize: 100,
                }
            );
            const cfnEsmAssetIndexing = esmAssetIndexing.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmAssetIndexing.addPropertyDeletionOverride("Tags");
        } else {
            indexingS3ObjectMetadataFunction.addEventSource(
                new eventsources.DynamoEventSource(storageResources.dynamo.metadataStorageTable, {
                    startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                })
            );
            assetIndexingFunction.addEventSource(
                new eventsources.DynamoEventSource(storageResources.dynamo.assetStorageTable, {
                    startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                })
            );
        }

        aoss.grantCollectionAccess(indexingS3ObjectMetadataFunction);
        aoss.grantCollectionAccess(assetIndexingFunction);
        aoss.grantVPCeAccess(indexingS3ObjectMetadataFunction);
        aoss.grantVPCeAccess(assetIndexingFunction);

        //grant search function access to collection and VPCe
        aoss.grantCollectionAccess(searchFun);
        aoss.grantVPCeAccess(searchFun);
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

        indexingS3ObjectMetadataFunction = buildIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            "m",
            config,
            vpc,
            subnets
        );

        const assetIndexingFunction = buildIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            "a",
            config,
            vpc,
            subnets
        );

        aos.grantOSDomainAccess(assetIndexingFunction);
        aos.grantOSDomainAccess(indexingS3ObjectMetadataFunction);

        // Due to cdk upgrade, not all regions support tags for EventSourceMapping
        // this line should remove the tags for regions that dont support it (govcloud currently not supported)
        if (config.app.govCloud.enabled) {
            const esmIndexing = new lambda.EventSourceMapping(
                scope,
                "idxmDynamoDBEventSourceStorageResourcesBuilderMetadataStorageTable",
                {
                    target: indexingS3ObjectMetadataFunction,
                    eventSourceArn: storageResources.dynamo.metadataStorageTable.tableStreamArn,
                    startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                    batchSize: 100,
                }
            );
            const cfnEsmIndexing = esmIndexing.node.defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmIndexing.addPropertyDeletionOverride("Tags");

            const esmAssetIndexing = new lambda.EventSourceMapping(
                scope,
                "idxaDynamoDBEventSourceStorageResourcesBuilderAssetStorageTable",
                {
                    target: assetIndexingFunction,
                    eventSourceArn: storageResources.dynamo.assetStorageTable.tableStreamArn,
                    startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                    batchSize: 100,
                }
            );
            const cfnEsmAssetIndexing = esmAssetIndexing.node
                .defaultChild as lambda.CfnEventSourceMapping;
            cfnEsmAssetIndexing.addPropertyDeletionOverride("Tags");
        } else {
            indexingS3ObjectMetadataFunction.addEventSource(
                new eventsources.DynamoEventSource(storageResources.dynamo.metadataStorageTable, {
                    startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                })
            );
            assetIndexingFunction.addEventSource(
                new eventsources.DynamoEventSource(storageResources.dynamo.assetStorageTable, {
                    startingPosition: lambda.StartingPosition.TRIM_HORIZON,
                })
            );
        }

        //grant search function access to AOS
        aos.grantOSDomainAccess(searchFun);
    }

    /////////////////////////////////////////////////////////////////////////////
    /////////////////////////////////////////////////////////////////////////////

    //Setup assetbucket sync and indexing
    //Loop through each asset bucket and setup the new event notifications sync method
    // and pass in indexing functions created (if they exist due to feature switching)
    let index = 0;
    const assetBucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
    for (const record of assetBucketRecords) {
        //Create created queue
        const onS3ObjectCreatedQueue = new sqs.Queue(scope, "bucketSyncCreated" + record.bucket, {
            queueName: `${config.app.baseStackName}-bucketSyncCreated-${index}`,
            visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's is 900.
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
        });
        onS3ObjectCreatedQueue.grantSendMessages(Service("SNS").Principal);

        //Create new lambda for bucketSync (pass indexingS3ObjectMetadataFunction function name in if defined)
        const sqsBucketSyncFunctionCreated = buildSqsBucketSyncFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            indexingS3ObjectMetadataFunction,
            record.bucket.bucketName,
            record.prefix,
            record.defaultSyncDatabaseId,
            "created",
            index,
            config,
            vpc,
            subnets
        );

        //Add event notifications for syncing
        if (record.snsS3ObjectCreatedTopic) {
            record.snsS3ObjectCreatedTopic.addSubscription(
                new SqsSubscription(onS3ObjectCreatedQueue)
            );
        }

        // The functions poll the respective queues, which is populated by messages sent to the topic.
        sqsBucketSyncFunctionCreated.addEventSource(
            new SqsEventSource(onS3ObjectCreatedQueue, {
                batchSize: 10, // Max configurable records w/o maxBatchingWindow.
                maxBatchingWindow: cdk.Duration.seconds(30), // Max configurable time to wait before function is invoked.
            })
        );

        index = index + 1;

        //Create deleted queue
        const onS3ObjectDeletedQueue = new sqs.Queue(scope, "bucketSyncDeleted" + record.bucket, {
            queueName: `${config.app.baseStackName}-bucketSyncDeleted-${index}`,
            visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's is 900.
            encryption: storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: storageResources.encryption.kmsKey,
            enforceSSL: true,
        });
        onS3ObjectDeletedQueue.grantSendMessages(Service("SNS").Principal);

        //Create new lambda for bucketSync (pass indexingS3ObjectMetadataFunction function name in if defined)
        const sqsBucketSyncFunctionRemoved = buildSqsBucketSyncFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            indexingS3ObjectMetadataFunction,
            record.bucket.bucketName,
            record.prefix,
            record.defaultSyncDatabaseId,
            "deleted",
            index,
            config,
            vpc,
            subnets
        );
        index = index + 1;

        if (record.snsS3ObjectDeletedTopic) {
            record.snsS3ObjectDeletedTopic.addSubscription(
                new SqsSubscription(onS3ObjectDeletedQueue)
            );
        }

        sqsBucketSyncFunctionRemoved.addEventSource(
            new SqsEventSource(onS3ObjectDeletedQueue, {
                batchSize: 10, // Max configurable records w/o maxBatchingWindow.
                maxBatchingWindow: cdk.Duration.seconds(30), // Max configurable time to wait before function is invoked.
            })
        );
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
