/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3not from "aws-cdk-lib/aws-s3-notifications";
import * as sns from "aws-cdk-lib/aws-sns";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../config/config";
import {
    kmsKeyPolicyStatementPrincipalGenerator,
    suppressCdkNagLambdaFrameworkResources,
} from "./helper/security";
import { Service } from "./helper/service-helper";
import {
    CrossRegionBucketTopics,
    crossRegionTopicKey,
    normalizeAssetBucketPrefix,
} from "./helper/s3AssetBuckets";

export interface CrossRegionBucketNotificationsStackProps {
    env: cdk.Environment;
    stackName: string;
    description: string;
    synthesizer?: cdk.IStackSynthesizer;
    config: Config.Config;
    /** The Region every bucket entry in this stack lives in (the stack's own Region). */
    bucketRegion: string;
    /** The external bucket entries configured in bucketRegion. */
    bucketEntries: Config.ConfigPublicAssetS3Buckets[];
}

/**
 * Amazon S3 event notification resources for the external asset buckets that live in one Region
 * other than the deployment Region. Amazon S3 delivers bucket notifications only to a destination
 * in the bucket's Region, so this stack is deployed into that Region and owns, per registered
 * (bucket, prefix) entry, the object-created and object-removed Amazon SNS topics and the bucket
 * notification configuration that publishes to them. The deployment-Region SQS queues subscribe to
 * these topics cross-Region from the core stack, which receives the topic ARNs through this
 * stack's outputs (crossRegionReferences).
 *
 * With customer managed KMS encryption the topics are encrypted with a key in this Region: AWS KMS
 * keys are regional, so the core stack's key cannot encrypt a topic here. A VAMS-generated key
 * yields one generated key per bucket Region with the same key policy; an imported multi-Region
 * key (key id `mrk-...`) is imported as its replica in this Region, which the operator replicates
 * beforehand; an imported single-Region key cannot be used here, so a regional key is generated
 * for the topics and a synth warning says so.
 */
export class CrossRegionBucketNotificationsStack extends cdk.Stack {
    /** Topic ARNs keyed by crossRegionTopicKey(bucketArn, prefix). */
    public readonly topics: CrossRegionBucketTopics = {};
    /** The key encrypting this Region's topics; undefined when CMK encryption is off. */
    public readonly topicsKmsKey: kms.IKey | undefined;

    constructor(scope: Construct, id: string, props: CrossRegionBucketNotificationsStackProps) {
        super(scope, id, {
            env: props.env,
            stackName: props.stackName,
            description: props.description,
            synthesizer: props.synthesizer,
            crossRegionReferences: true,
        });

        const config = props.config;
        const bucketRegion = props.bucketRegion;

        /**
         * Regional topic encryption key
         */
        this.topicsKmsKey = this.resolveTopicsKmsKey(config, bucketRegion);

        /**
         * Buckets, topics and notifications
         */
        // Each unique bucket ARN is imported once so every prefix registered against it
        // accumulates into one Amazon S3 notification configuration (several prefix-filtered
        // topic entries), matching the storage builder's handling of same-Region buckets.
        const importedBucketsByArn = new Map<string, s3.IBucket>();
        const externalAccountIds = new Set<string>();
        let index = 0;

        for (const bucketConfig of props.bucketEntries) {
            const bucketAccountId = normalizeOptional(bucketConfig.bucketAccountId);
            const prefix = normalizeAssetBucketPrefix(bucketConfig.baseAssetsPrefix);

            let bucket = importedBucketsByArn.get(bucketConfig.bucketArn);
            if (!bucket) {
                bucket = s3.Bucket.fromBucketAttributes(
                    this,
                    `ImportedAssetBucket-${bucketConfig.bucketArn}`,
                    {
                        bucketArn: bucketConfig.bucketArn,
                        account: bucketAccountId,
                        region: bucketRegion,
                    }
                );
                importedBucketsByArn.set(bucketConfig.bucketArn, bucket);
            }
            if (bucketAccountId) {
                externalAccountIds.add(bucketAccountId);
            }

            const createdTopic = new sns.Topic(this, `S3ObjectCreatedTopic-${index}`, {
                masterKey: this.topicsKmsKey,
                enforceSSL: true,
            });
            index = index + 1;
            const removedTopic = new sns.Topic(this, `S3ObjectRemovedTopic-${index}`, {
                masterKey: this.topicsKmsKey,
                enforceSSL: true,
            });
            index = index + 1;

            // Amazon S3 ignores a "/" prefix filter, so the bucket root registers no filter.
            const filters = prefix == "/" ? [] : [{ prefix }];
            bucket.addEventNotification(
                s3.EventType.OBJECT_CREATED,
                new s3not.SnsDestination(createdTopic),
                ...filters
            );
            bucket.addEventNotification(
                s3.EventType.OBJECT_REMOVED,
                new s3not.SnsDestination(removedTopic),
                ...filters
            );

            // For a cross-account bucket, allow the Amazon S3 service to publish on behalf of
            // that bucket: the condition CDK derives uses this stack's account as SourceAccount,
            // which does not match the bucket owner's and would drop notifications silently.
            if (bucketAccountId) {
                for (const topic of [createdTopic, removedTopic]) {
                    topic.addToResourcePolicy(
                        new iam.PolicyStatement({
                            effect: iam.Effect.ALLOW,
                            principals: [Service("S3").Principal],
                            actions: ["SNS:Publish"],
                            resources: [topic.topicArn],
                            conditions: {
                                ArnLike: { "aws:SourceArn": bucket.bucketArn },
                                StringEquals: { "aws:SourceAccount": bucketAccountId },
                            },
                        })
                    );
                }
            }

            this.topics[
                crossRegionTopicKey(bucketConfig.bucketArn, bucketConfig.baseAssetsPrefix)
            ] = {
                createdTopicArn: createdTopic.topicArn,
                removedTopicArn: removedTopic.topicArn,
            };
        }

        // The Amazon S3 service in a cross-account bucket's account must be able to generate data
        // keys with this Region's key to publish to the encrypted topics (no-op on an imported
        // replica, whose policy is managed where the primary key lives).
        if (this.topicsKmsKey instanceof kms.Key && externalAccountIds.size > 0) {
            this.topicsKmsKey.addToResourcePolicy(
                new iam.PolicyStatement({
                    sid: "AllowExternalBucketS3Notifications",
                    effect: iam.Effect.ALLOW,
                    principals: [Service("S3").Principal],
                    actions: ["kms:GenerateDataKey*", "kms:Decrypt"],
                    resources: ["*"],
                    conditions: {
                        StringEquals: { "aws:SourceAccount": Array.from(externalAccountIds) },
                    },
                })
            );
        }

        /**
         * Outputs
         */
        if (this.topicsKmsKey) {
            new cdk.CfnOutput(this, "CrossRegionTopicsKmsKeyArnOutput", {
                value: this.topicsKmsKey.keyArn,
                description: `KMS key encrypting the VAMS asset bucket notification topics in ${bucketRegion}`,
            });
        }

        /**
         * Nag suppressions
         */
        if (!this.topicsKmsKey) {
            NagSuppressions.addResourceSuppressions(
                this,
                [
                    {
                        id: "AwsSolutions-SNS2",
                        reason: "Encryption not provided due to customer configuration of not wanting to use a KMS encryption key in VAMS",
                    },
                ],
                true
            );
        }
        this.node.findAll().forEach((item) => {
            if (item instanceof cdk.aws_lambda.Function) {
                // The bucket-notification handler CDK generates is pinned to its own Python runtime.
                NagSuppressions.addResourceSuppressions(item, [
                    {
                        id: "AwsSolutions-L1",
                        reason: "The lambda function is configured with the appropriate runtime version",
                    },
                ]);
            }
        });
        suppressCdkNagLambdaFrameworkResources(this);

        cdk.Tags.of(this).add("vams:stackname", props.stackName);
    }

    private resolveTopicsKmsKey(config: Config.Config, bucketRegion: string): kms.IKey | undefined {
        if (!config.app.useKmsCmkEncryption.enabled) {
            return undefined;
        }

        const externalCmkArn = normalizeOptional(
            config.app.useKmsCmkEncryption.optionalExternalCmkArn
        );
        if (externalCmkArn) {
            const keyArn = cdk.Arn.split(externalCmkArn, cdk.ArnFormat.SLASH_RESOURCE_NAME);
            const keyId = keyArn.resourceName || "";
            if (keyId.startsWith("mrk-")) {
                // Multi-Region key: the replica in this Region shares the key id.
                const replicaArn = cdk.Arn.format({
                    partition: keyArn.partition,
                    service: "kms",
                    region: bucketRegion,
                    account: keyArn.account,
                    resource: "key",
                    resourceName: keyId,
                    arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                });
                return kms.Key.fromKeyArn(this, "CrossRegionTopicsKmsKey", replicaArn);
            }
            cdk.Annotations.of(this).addWarning(
                `app.useKmsCmkEncryption.optionalExternalCmkArn names a single-Region key in ` +
                    `${keyArn.region}, which cannot encrypt Amazon SNS topics in ${bucketRegion}. VAMS ` +
                    `generates a key in ${bucketRegion} for the asset bucket notification topics ` +
                    `there (the topic payload is the Amazon S3 event envelope, not object content). ` +
                    `To use your own key, replicate a multi-Region key (mrk-...) into ${bucketRegion} ` +
                    `and set optionalExternalCmkArn to it.`
            );
        }

        // RETAIN so the key outlives a stack teardown; it carries no alias, so a retained key never
        // collides with the one a redeploy creates.
        const key = new kms.Key(this, "CrossRegionTopicsKmsKey", {
            description: `VAMS Generated KMS Encryption key for asset bucket notification topics in ${bucketRegion}`,
            enableKeyRotation: true,
            removalPolicy: cdk.RemovalPolicy.RETAIN,
        });
        key.addToResourcePolicy(kmsKeyPolicyStatementPrincipalGenerator(config, key));
        return key;
    }
}

/**
 * Wires one CrossRegionBucketNotificationsStack per Region that holds an external asset bucket
 * outside the deployment Region. Returns the stacks (for the core stack's dependencies) and the
 * merged topic map (for the storage builder). Both the CDK app entry point and the synth test
 * harness build the app through this, so they agree on what is emitted.
 */
export function buildCrossRegionBucketNotificationStacks(
    scope: Construct,
    config: Config.Config,
    synthesizer?: cdk.IStackSynthesizer
): { stacks: CrossRegionBucketNotificationsStack[]; topics: CrossRegionBucketTopics } {
    const stacks: CrossRegionBucketNotificationsStack[] = [];
    const topics: CrossRegionBucketTopics = {};
    const regions = Config.crossRegionExternalBucketRegions(
        config.app.assetBuckets.externalAssetBuckets,
        config.env.region
    );
    for (const bucketRegion of regions) {
        const stackName = `${config.name}-xregion-${config.app.baseStackName}-${bucketRegion}`;
        const stack = new CrossRegionBucketNotificationsStack(scope, stackName, {
            stackName,
            env: { account: config.env.account, region: bucketRegion },
            description: Config.STACK_XREGION_DESCRIPTION,
            synthesizer,
            config,
            bucketRegion,
            bucketEntries: Config.crossRegionExternalBuckets(
                config.app.assetBuckets.externalAssetBuckets,
                config.env.region
            ).filter((bucketConfig) => bucketConfig.bucketRegion == bucketRegion),
        });
        stacks.push(stack);
        Object.assign(topics, stack.topics);
    }
    return { stacks, topics };
}

function normalizeOptional(value: string | undefined): string | undefined {
    return value && value != "" && value != "UNDEFINED" ? value : undefined;
}
