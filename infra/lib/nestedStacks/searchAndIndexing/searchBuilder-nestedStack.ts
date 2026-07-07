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
import { RouteRegistry, attachFunctionToApi } from "../apiLambda/apiRouteRegistry";
import { NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as cdk from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { Service } from "../../helper/service-helper";
import * as cr from "aws-cdk-lib/custom-resources";
import { RESOURCE_PARAM_KEYS } from "../../../common/resourceParamKeys";

export class SearchBuilderNestedStack extends NestedStack {
    public reindexerFunctionName = "";

    constructor(
        parent: Construct,
        name: string,
        config: Config.Config,
        registry: RouteRegistry,
        storageResources: storageResources,
        lambdaCommonBaseLayer: LayerVersion,
        vpc: ec2.IVpc,
        subnets: ec2.ISubnet[]
    ) {
        super(parent, name);

        this.reindexerFunctionName = searchBuilder(
            this,
            config,
            registry,
            storageResources,
            lambdaCommonBaseLayer,
            vpc,
            subnets
        );
    }
}

export function searchBuilder(
    scope: Construct,
    config: Config.Config,
    registry: RouteRegistry,
    storageResources: storageResources,
    lambdaCommonBaseLayer: LayerVersion,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
): string {
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
        registry: registry,
    });
    attachFunctionToApi(scope, searchFun, {
        routePath: "/search",
        method: apigwv2.HttpMethod.GET,
        registry: registry,
    });

    // Add simple search endpoint
    attachFunctionToApi(scope, searchFun, {
        routePath: "/search/simple",
        method: apigwv2.HttpMethod.POST,
        registry: registry,
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
            availabilityZoneCount: config.app.openSearch.useProvisioned.availabilityZoneCount,
            numberOfShards: config.app.openSearch.useProvisioned.numberOfShards,
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

    // Publish the reindexer function name for data-migration tooling. Created here
    // rather than through the resource-name registry because this stack builds after
    // the ResourceNames stack materializes the registry.
    if (reindexerFunction) {
        new ssm.StringParameter(scope, "ResourceNameParamCrOsReindexer", {
            parameterName: `${config.resourceNamesSSMParamPrefix}/${RESOURCE_PARAM_KEYS.lambdaFunctions.crOsReindexer}`,
            stringValue: reindexerFunction.functionName,
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

    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-L1",
                reason: "The non-latest runtime here belongs to the CDK custom-resource provider framework Lambda (cr.Provider / AwsCustomResource) that drives OpenSearch schema deployment and reindexing. VAMS does not author or control this function's runtime version; it is managed by the aws-cdk custom-resources framework.",
            },
        ],
        true
    );

    // Scope IAM5 suppressions to the specific wildcards VAMS actually creates for the
    // OpenSearch search/indexing roles (schema-deploy, search, fileIndexer, assetIndexer,
    // crOsReindexer), rather than a blanket match-all. Each entry names the exact wildcard
    // action/resource and why it is required, so a new, unrelated wildcard policy in this
    // stack is still surfaced by CDK Nag.
    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "OpenSearch Serverless data-plane access uses the aoss:* action set scoped to the specific VAMS collection ARN. Index-level permissions in AOSS are enforced by the collection data-access policy (not the IAM action), and the schema-deploy resource creates indexes at runtime, so the action is left as aoss:* against the single collection resource.",
                appliesTo: [{ regex: "/^Action::aoss:\\*$/g" }],
            },
            {
                id: "AwsSolutions-IAM5",
                reason: "The OpenSearch endpoint/index names are published to and read from SSM Parameter Store under this deployment's parameter prefix; the search/indexing roles read those parameters via a prefix wildcard scoped to the deployment name (parameter/*<config.name>*).",
                appliesTo: [
                    { regex: "/^Action::ssm:\\*$/g" },
                    { regex: "/^Resource::arn:.*:ssm:.*:parameter\\/.*$/g" },
                ],
            },
            {
                id: "AwsSolutions-IAM5",
                reason: "grantReadData/grantReadWriteData on the DynamoDB tables the search/indexing Lambdas read (asset, constraints, etc.) includes the table's secondary indexes via the standard '<table>/index/*' resource wildcard, which is required to query any GSI on the table.",
                appliesTo: [{ regex: "/^Resource::<.*Table.*\\.Arn>\\/index\\/\\*$/g" }],
            },
            {
                id: "AwsSolutions-IAM5",
                reason: "The CDK custom-resources provider framework (cr.Provider backing the OpenSearch schema-deploy resource) grants lambda:InvokeFunction on the versioned handler function ARN, which CDK renders with a trailing ':*'. This policy is generated by the aws-cdk framework, not authored by VAMS.",
                appliesTo: [{ regex: "/^Resource::<.*DeploySchema.*\\.Arn>:\\*$/g" }],
            },
        ],
        true
    );

    return reindexerFunction ? reindexerFunction.functionName : "";
}
