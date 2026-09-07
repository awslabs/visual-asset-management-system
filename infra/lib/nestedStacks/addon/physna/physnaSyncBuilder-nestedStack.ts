/*
 * Physna Sync add-on nested stack.
 *
 * Subscribes two SQS queues to the VAMS file + asset SNS topics and wires them
 * to Lambda consumers that one-way-sync VAMS changes to Physna.
 *
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { SqsSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as kms from "aws-cdk-lib/aws-kms";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { RouteRegistry } from "../../apiLambda/apiRouteRegistry";
import * as Config from "../../../../config/config";
import {
    buildPhysnaFileSyncFunction,
    buildPhysnaAssetSyncFunction,
    buildPhysnaViewerFunction,
} from "./lambdaBuilder/physnaSyncFunctions";
import { Service } from "../../../helper/service-helper";
import { attachFunctionToApi } from "../../apiLambda/apiRouteRegistry";
import { NagSuppressions } from "cdk-nag";
import { populatePhysnaSecret } from "./customResources/populatePhysnaSecret";

import { NAG_REASON_LAMBDA_BASIC_EXECUTION } from "../../../helper/security";

export interface PhysnaSyncBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    isolatedSubnets: ec2.ISubnet[];
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    registry: RouteRegistry;
}

const defaultProps: Partial<PhysnaSyncBuilderNestedStackProps> = {};

export class PhysnaSyncBuilderNestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: PhysnaSyncBuilderNestedStackProps) {
        super(parent, name);
        props = { ...defaultProps, ...props };

        const physnaConfig = props.config.app.addons.usePhysnaSync;

        // Secrets Manager — stores Physna client credentials, encrypted with the
        // shared VAMS CMK when present.
        //
        // Importing the CMK here by ARN (instead of using the cross-stack IKey
        // reference directly) is critical: `secret.grantRead()` below internally
        // calls `key.grantDecrypt(role)`, which on a CDK-owned cross-stack key
        // would mutate the key's resource policy from the Storage stack, adding
        // an import from the Addon stack and creating a CloudFormation circular
        // dependency. An imported `IKey` is treated as "unowned" — grantDecrypt
        // becomes a no-op against the key policy and only writes IAM permissions
        // on the Lambda role (which already works because the shared CMK policy
        // trusts the account root and the Lambda role has kms:Decrypt via
        // `kmsKeyLambdaPermissionAddToResourcePolicy`).
        const sharedKmsKey = props.storageResources.encryption.kmsKey;
        const credsSecretEncryptionKey = sharedKmsKey
            ? kms.Key.fromKeyArn(this, "PhysnaCredsKmsKeyRef", sharedKmsKey.keyArn)
            : undefined;

        // Physna OAuth2 client credentials. Two modes:
        //   1. credentialsSecretArn set — import an operator-managed secret by ARN.
        //   2. otherwise (default) — VAMS creates the secret and populates it from the
        //      config clientId/clientSecret via a custom resource whose Lambda carries the
        //      values in its CODE ASSET (content-hashed, uploaded to the assets bucket).
        //      The credential value therefore never appears in the CloudFormation template
        //      or template properties, yet no secret needs to be created ahead of deploy.
        let credsSecret: secretsmanager.ISecret;
        const configuredSecretArn = physnaConfig.credentialsSecretArn;
        if (configuredSecretArn && configuredSecretArn.length > 0) {
            credsSecret = secretsmanager.Secret.fromSecretCompleteArn(
                this,
                "PhysnaCredentialsSecret",
                configuredSecretArn
            );
        } else {
            // Create the secret empty (no value in the template), then populate it at
            // deploy time from the config credentials via the code-asset custom resource.
            const createdSecret = new secretsmanager.Secret(this, "PhysnaCredentialsSecret", {
                description:
                    "Physna OAuth2 client credentials used by the VAMS Physna Sync add-on.",
                encryptionKey: credsSecretEncryptionKey,
            });

            populatePhysnaSecret(
                this,
                "PhysnaCredentialsSecretPopulate",
                createdSecret,
                physnaConfig.clientId || "",
                physnaConfig.clientSecret || "",
                credsSecretEncryptionKey
            );

            credsSecret = createdSecret;
        }

        // Build lambdas (pass the secret in so they can grant-read it)
        const fileSyncFunction = buildPhysnaFileSyncFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.isolatedSubnets,
            credsSecret
        );

        const assetSyncFunction = buildPhysnaAssetSyncFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.isolatedSubnets,
            credsSecret
        );

        // Physna Viewer proxy lambda — backs GET /addon/physna/viewer.
        const viewerFunction = buildPhysnaViewerFunction(
            this,
            props.lambdaCommonBaseLayer,
            props.storageResources,
            props.config,
            props.vpc,
            props.isolatedSubnets,
            credsSecret
        );
        attachFunctionToApi(this, viewerFunction, {
            routePath: "/addon/physna/viewer",
            method: apigwv2.HttpMethod.GET,
            registry: props.registry,
        });

        // Deliveries a message gets before its queue moves it to the dead-letter queue.
        // A record the consumer rejects alone is redelivered alone, but a failure it cannot
        // pin on one record reports the WHOLE batch, and a timeout reports none of it, so the
        // count is not per-message in the failure mode that matters: three deliveries spaced
        // by the 960 s visibility timeout below is ~45 minutes before a persistent fault
        // dead-letters a batch's healthy records too, which then need a DLQ redrive inside
        // its 14-day retention.
        const syncQueueMaxReceiveCount = 3;

        // SQS queues — one per SNS topic subscription, each with its own dead-letter queue.
        // A message the consumer cannot process is retained there instead of deleted, and one
        // DLQ per queue keeps the file sync's failures distinguishable from the asset sync's.
        const fileSyncDlq = new sqs.Queue(this, "PhysnaFileSyncSqsDLQ", {
            retentionPeriod: cdk.Duration.days(14),
            encryption: props.storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: props.storageResources.encryption.kmsKey,
            enforceSSL: true,
        });

        const fileSyncQueue = new sqs.Queue(this, "PhysnaFileSyncSqsQueue", {
            queueName: `${props.config.name}-${props.config.app.baseStackName}-physnaFileSync`,
            visibilityTimeout: cdk.Duration.seconds(960),
            encryption: props.storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: props.storageResources.encryption.kmsKey,
            enforceSSL: true,
            deadLetterQueue: {
                queue: fileSyncDlq,
                maxReceiveCount: syncQueueMaxReceiveCount,
            },
        });

        props.storageResources.sns.fileIndexerSnsTopic.addSubscription(
            new SqsSubscription(fileSyncQueue)
        );
        fileSyncQueue.grantConsumeMessages(fileSyncFunction);

        // Kept small (2) so a single Lambda invocation never has to upload
        // more than two large files to Physna. The overall Lambda timeout is
        // 15 minutes; at batchSize=10 a burst of sizeable CAD uploads could
        // exhaust it and trigger a whole-batch retry. With batchSize=2 each
        // invocation has ~7.5 min per file, and the remaining messages just
        // wait briefly in SQS instead of being lost to a timeout-retry.
        const fileSyncBatchSize = 2;
        if (props.config.app.govCloud.enabled) {
            const esmFile = new lambda.EventSourceMapping(this, "PhysnaFileSyncSqsEventSource", {
                eventSourceArn: fileSyncQueue.queueArn,
                target: fileSyncFunction,
                batchSize: fileSyncBatchSize,
                maxBatchingWindow: cdk.Duration.seconds(3),
                // Emits FunctionResponseTypes: ["ReportBatchItemFailures"]. Without it Lambda
                // ignores the handler's batchItemFailures and deletes the whole batch on a 200.
                reportBatchItemFailures: true,
            });
            (esmFile.node.defaultChild as lambda.CfnEventSourceMapping).addPropertyDeletionOverride(
                "Tags"
            );
        } else {
            fileSyncFunction.addEventSource(
                new eventsources.SqsEventSource(fileSyncQueue, {
                    batchSize: fileSyncBatchSize,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                    reportBatchItemFailures: true,
                })
            );
        }

        const assetSyncDlq = new sqs.Queue(this, "PhysnaAssetSyncSqsDLQ", {
            retentionPeriod: cdk.Duration.days(14),
            encryption: props.storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: props.storageResources.encryption.kmsKey,
            enforceSSL: true,
        });

        const assetSyncQueue = new sqs.Queue(this, "PhysnaAssetSyncSqsQueue", {
            queueName: `${props.config.name}-${props.config.app.baseStackName}-physnaAssetSync`,
            visibilityTimeout: cdk.Duration.seconds(960),
            encryption: props.storageResources.encryption.kmsKey
                ? sqs.QueueEncryption.KMS
                : sqs.QueueEncryption.SQS_MANAGED,
            encryptionMasterKey: props.storageResources.encryption.kmsKey,
            enforceSSL: true,
            deadLetterQueue: {
                queue: assetSyncDlq,
                maxReceiveCount: syncQueueMaxReceiveCount,
            },
        });
        props.storageResources.sns.assetIndexerSnsTopic.addSubscription(
            new SqsSubscription(assetSyncQueue)
        );
        assetSyncQueue.grantConsumeMessages(assetSyncFunction);

        if (props.config.app.govCloud.enabled) {
            const esmAsset = new lambda.EventSourceMapping(this, "PhysnaAssetSyncSqsEventSource", {
                eventSourceArn: assetSyncQueue.queueArn,
                target: assetSyncFunction,
                batchSize: 10,
                maxBatchingWindow: cdk.Duration.seconds(3),
                // Emits FunctionResponseTypes: ["ReportBatchItemFailures"]. Without it Lambda
                // ignores the handler's batchItemFailures and deletes the whole batch on a 200.
                reportBatchItemFailures: true,
            });
            (
                esmAsset.node.defaultChild as lambda.CfnEventSourceMapping
            ).addPropertyDeletionOverride("Tags");
        } else {
            assetSyncFunction.addEventSource(
                new eventsources.SqsEventSource(assetSyncQueue, {
                    batchSize: 10,
                    maxBatchingWindow: cdk.Duration.seconds(3),
                    reportBatchItemFailures: true,
                })
            );
        }

        // Scoped to the two DLQ resources rather than the stack: both source queues now carry a
        // redrive policy, so a source queue added later without one is still reported.
        NagSuppressions.addResourceSuppressions(
            [fileSyncDlq, assetSyncDlq],
            [
                {
                    id: "AwsSolutions-SQS3",
                    reason: "This queue IS the dead-letter queue for a Physna sync source queue. A DLQ is the terminal destination for messages the consumer could not process, so giving it a redrive policy of its own would only defer the same failure to a further queue.",
                },
            ],
            true
        );
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM4",
                    reason: NAG_REASON_LAMBDA_BASIC_EXECUTION,
                    appliesTo: [{ regex: "/.*AWSLambdaBasicExecutionRole$/g" }],
                },
            ],
            true
        );
        // Only the VAMS-created secret carries a rotation config in this stack; an
        // imported (operator-managed) secret is governed by the owning stack, so the
        // SMG4 suppression applies only when VAMS created the secret here.
        if (!(configuredSecretArn && configuredSecretArn.length > 0)) {
            NagSuppressions.addResourceSuppressions(
                credsSecret,
                [
                    {
                        id: "AwsSolutions-SMG4",
                        reason: "The Physna add-on does not rotate the client secret automatically. Operators rotate the credential manually, or supply an operator-managed secret via credentialsSecretArn.",
                    },
                ],
                true
            );
        }
    }
}
