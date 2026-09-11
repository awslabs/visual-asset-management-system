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

import { NAG_REASON_LAMBDA_BASIC_EXECUTION } from "../../helper/security";

export class SearchBuilderNestedStack extends NestedStack {
    public reindexerFunctionName = "";
    public searchFunction: lambda.Function;

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

        this.searchFunction = this.searchBuilder(
            this,
            config,
            registry,
            storageResources,
            lambdaCommonBaseLayer,
            vpc,
            subnets
        );
    }

    searchBuilder(
        scope: Construct,
        config: Config.Config,
        registry: RouteRegistry,
        storageResources: storageResources,
        lambdaCommonBaseLayer: LayerVersion,
        vpc: ec2.IVpc,
        subnets: ec2.ISubnet[]
    ): lambda.Function {
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
        // The schema-deploy custom resource of whichever OpenSearch flavour is built. Held in the
        // outer scope so the reindex trigger created after both branches can declare a dependency on
        // it; the constructs themselves are block-scoped to their branch.
        let schemaDeployResource: cdk.CustomResource | undefined = undefined;

        // The dead-letter queues of the two indexer source queues. Held in the outer scope so the
        // CDK Nag suppression at the end of this method can be scoped to them rather than to the
        // whole stack, which would also cover a source queue added later without a redrive policy.
        let fileIndexerSqsDlq: sqs.Queue | undefined = undefined;
        let assetIndexerSqsDlq: sqs.Queue | undefined = undefined;

        // Deliveries a message gets before its source queue moves it to the dead-letter queue.
        // A record the indexer rejects alone is redelivered alone, but a failure it cannot pin
        // on one record reports the WHOLE batch, and a timeout reports none of it, so the count
        // is not per-message in the failure mode that matters: three deliveries spaced by the
        // 960 s visibility timeout below is ~45 minutes before a persistent fault dead-letters
        // a batch's healthy records too, which then need a DLQ redrive inside its 14-day
        // retention. Raising the count costs only later dead-lettering, as indexing is an
        // idempotent upsert keyed by document id.
        const indexerQueueMaxReceiveCount = 3;

        if (config.app.openSearch.useServerless.enabled) {
            //Serverless Deployment
            const aoss = new OpensearchServerlessConstruct(scope, "AOSS", {
                config: config,
                principalArn: [],
                storageResources: storageResources,
                vpc: vpc,
                subnets: subnets,
            });
            schemaDeployResource = aoss.schemaDeployResource;

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
            this.reindexerFunctionName = reindexerFunction.functionName;

            // Create SQS queues for indexers and subscribe to SNS topics
            // File indexer dead-letter queue. Encryption mirrors the source queue below: a message
            // moved into a DLQ the indexer's role cannot decrypt is unreadable dead letter.
            fileIndexerSqsDlq = new sqs.Queue(scope, "FileIndexerSqsDLQ", {
                retentionPeriod: cdk.Duration.days(14),
                encryption: storageResources.encryption.kmsKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: storageResources.encryption.kmsKey,
                enforceSSL: true,
            });

            // File indexer SQS queue
            const fileIndexerSqsQueue = new sqs.Queue(scope, "FileIndexerSqsQueue", {
                queueName: `${config.name}-${config.app.baseStackName}-fileIndexer`,
                visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
                encryption: storageResources.encryption.kmsKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: storageResources.encryption.kmsKey,
                enforceSSL: true,
                deadLetterQueue: {
                    queue: fileIndexerSqsDlq,
                    maxReceiveCount: indexerQueueMaxReceiveCount,
                },
            });

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
                        reportBatchItemFailures: true,
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
                        reportBatchItemFailures: true,
                    })
                );
            }

            // Asset indexer dead-letter queue. One per source queue, so the file indexer's poison
            // records stay distinguishable from the asset indexer's.
            assetIndexerSqsDlq = new sqs.Queue(scope, "AssetIndexerSqsDLQ", {
                retentionPeriod: cdk.Duration.days(14),
                encryption: storageResources.encryption.kmsKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: storageResources.encryption.kmsKey,
                enforceSSL: true,
            });

            // Asset indexer SQS queue
            const assetIndexerSqsQueue = new sqs.Queue(scope, "AssetIndexerSqsQueue", {
                queueName: `${config.name}-${config.app.baseStackName}-assetIndexer`,
                visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
                encryption: storageResources.encryption.kmsKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: storageResources.encryption.kmsKey,
                enforceSSL: true,
                deadLetterQueue: {
                    queue: assetIndexerSqsDlq,
                    maxReceiveCount: indexerQueueMaxReceiveCount,
                },
            });

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
                        reportBatchItemFailures: true,
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
                        reportBatchItemFailures: true,
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
            schemaDeployResource = aos.schemaDeployResource;

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
            this.reindexerFunctionName = reindexerFunction.functionName;

            // Create SQS queues for indexers and subscribe to SNS topics
            // File indexer dead-letter queue. Encryption mirrors the source queue below: a message
            // moved into a DLQ the indexer's role cannot decrypt is unreadable dead letter.
            fileIndexerSqsDlq = new sqs.Queue(scope, "FileIndexerSqsDLQ", {
                retentionPeriod: cdk.Duration.days(14),
                encryption: storageResources.encryption.kmsKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: storageResources.encryption.kmsKey,
                enforceSSL: true,
            });

            // File indexer SQS queue
            const fileIndexerSqsQueue = new sqs.Queue(scope, "FileIndexerSqsQueue", {
                queueName: `${config.name}-${config.app.baseStackName}-fileIndexer`,
                visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
                encryption: storageResources.encryption.kmsKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: storageResources.encryption.kmsKey,
                enforceSSL: true,
                deadLetterQueue: {
                    queue: fileIndexerSqsDlq,
                    maxReceiveCount: indexerQueueMaxReceiveCount,
                },
            });

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
                        reportBatchItemFailures: true,
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
                        reportBatchItemFailures: true,
                    })
                );
            }

            // Asset indexer dead-letter queue. One per source queue, so the file indexer's poison
            // records stay distinguishable from the asset indexer's.
            assetIndexerSqsDlq = new sqs.Queue(scope, "AssetIndexerSqsDLQ", {
                retentionPeriod: cdk.Duration.days(14),
                encryption: storageResources.encryption.kmsKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: storageResources.encryption.kmsKey,
                enforceSSL: true,
            });

            // Asset indexer SQS queue
            const assetIndexerSqsQueue = new sqs.Queue(scope, "AssetIndexerSqsQueue", {
                queueName: `${config.name}-${config.app.baseStackName}-assetIndexer`,
                visibilityTimeout: cdk.Duration.seconds(960), // Corresponding function's timeout is 900
                encryption: storageResources.encryption.kmsKey
                    ? sqs.QueueEncryption.KMS
                    : sqs.QueueEncryption.SQS_MANAGED,
                encryptionMasterKey: storageResources.encryption.kmsKey,
                enforceSSL: true,
                deadLetterQueue: {
                    queue: assetIndexerSqsDlq,
                    maxReceiveCount: indexerQueueMaxReceiveCount,
                },
            });

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
                        reportBatchItemFailures: true,
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
                        reportBatchItemFailures: true,
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

            const reindexTrigger = new cdk.CustomResource(scope, "ReindexTrigger", {
                serviceToken: reindexProvider.serviceToken,
                properties: {
                    Operation: "both",
                    ClearIndexes: "true",
                    Timestamp: Date.now().toString(),
                },
            });

            // The reindexer reads the index names and the endpoint from SSM, and the schema-deploy
            // custom resource is what writes them and creates the indexes. Two custom resources with no
            // declared dependency are ordered arbitrarily by CloudFormation (Rule 9), so without this
            // the reindexer can run first: on an upgrade it reads the previous index name and fills an
            // index nothing searches — a successful deploy with an empty search — and on a fresh install
            // it fails against an index that does not exist yet and rolls the stack back.
            if (schemaDeployResource) {
                reindexTrigger.node.addDependency(schemaDeployResource);
            }
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
        const openSearchIndexAssetSOutput = new cdk.CfnOutput(
            scope,
            "OpenSearchIndexAssetsOutput",
            {
                value: config.openSearchAssetIndexName,
                description: "The OpenSearch index name for assets",
            }
        );

        const openSearchIndexFilesOutput = new cdk.CfnOutput(scope, "OpenSearchIndexFilesOutput", {
            value: config.openSearchFileIndexName,
            description: "The OpenSearch index name for files",
        });

        //Nag supressions
        // Scoped to the two DLQ resources rather than the stack: both indexer source queues carry a
        // redrive policy, so a source queue added here later without one is still reported.
        const indexerDlqs = [fileIndexerSqsDlq, assetIndexerSqsDlq].filter(
            (queue): queue is sqs.Queue => queue !== undefined
        );
        if (indexerDlqs.length > 0) {
            NagSuppressions.addResourceSuppressions(
                indexerDlqs,
                [
                    {
                        id: "AwsSolutions-SQS3",
                        reason: "This queue IS the dead-letter queue for an indexer source queue. A DLQ is the terminal destination for records the indexer could not process, so giving it a redrive policy of its own would only defer the same failure to a further queue.",
                    },
                ],
                true
            );
        }

        NagSuppressions.addResourceSuppressions(
            scope,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_LAMBDA_BASIC_EXECUTION,
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
                    reason: "Provisioned OpenSearch data-plane access uses the es:* action set scoped to the single VAMS domain ARN and its sub-resources (<domain>/*). Index- and document-level actions are not separable from es:* for the OpenSearch HTTP API, and access is further restricted by the domain access policy to this role, so the schema-deploy/search/indexer roles use es:* against only the deployment's own domain.",
                    appliesTo: [
                        { regex: "/^Action::es:\\*$/g" },
                        { regex: "/^Resource::<.*OpenSearchDomain.*\\.Arn>\\/\\*$/g" },
                    ],
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

        return searchFun;
    }
}
