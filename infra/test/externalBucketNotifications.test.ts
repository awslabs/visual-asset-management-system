/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Template, Match } from "aws-cdk-lib/assertions";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3not from "aws-cdk-lib/aws-s3-notifications";
import * as sns from "aws-cdk-lib/aws-sns";

/**
 * These tests lock in the CDK behavior that the multi-prefix external-bucket feature
 * depends on: a single imported bucket instance with multiple prefix-filtered
 * addEventNotification calls must produce exactly ONE bucket notification
 * configuration (S3 permits only one per bucket), with one topic configuration per
 * (prefix, event) pair. This mirrors what storageBuilder does for a bucket ARN that
 * is registered under multiple non-overlapping prefixes.
 */
describe("external bucket notification merging", () => {
    test("one imported bucket + two prefixes => single notification config with all entries", () => {
        const app = new cdk.App();
        const stack = new cdk.Stack(app, "TestStack", {
            env: { account: "111111111111", region: "us-east-1" },
        });

        // Import the external (cross-account) bucket exactly once, as storageBuilder does.
        const bucket = s3.Bucket.fromBucketAttributes(stack, "ImportedAssetBucket", {
            bucketArn: "arn:aws:s3:::external-shared-bucket",
            account: "222222222222",
            region: "us-east-1",
        });

        // Two non-overlapping prefixes, each with its own created/removed topics.
        const prefixes = ["teamA/", "teamB/"];
        for (const prefix of prefixes) {
            const createdTopic = new sns.Topic(stack, `Created-${prefix}`);
            const removedTopic = new sns.Topic(stack, `Removed-${prefix}`);
            bucket.addEventNotification(
                s3.EventType.OBJECT_CREATED,
                new s3not.SnsDestination(createdTopic),
                { prefix }
            );
            bucket.addEventNotification(
                s3.EventType.OBJECT_REMOVED,
                new s3not.SnsDestination(removedTopic),
                { prefix }
            );
        }

        const template = Template.fromStack(stack);

        // Exactly one notification custom resource is emitted for the bucket, even
        // though four addEventNotification calls were made.
        template.resourceCountIs("Custom::S3BucketNotifications", 1);

        // The single notification config carries all four topic configurations
        // (two prefixes x created/removed), each with its prefix filter.
        const resources = template.findResources("Custom::S3BucketNotifications");
        const notification = Object.values(resources)[0] as {
            Properties: {
                NotificationConfiguration: {
                    TopicConfigurations: Array<{
                        Events: string[];
                        Filter: { Key: { FilterRules: Array<{ Name: string; Value: string }> } };
                    }>;
                };
            };
        };
        const topicConfigs = notification.Properties.NotificationConfiguration.TopicConfigurations;
        expect(topicConfigs).toHaveLength(4);

        const prefixValues = topicConfigs
            .map((tc) => tc.Filter.Key.FilterRules.find((r) => r.Name === "prefix")?.Value)
            .sort();
        expect(prefixValues).toEqual(["teamA/", "teamA/", "teamB/", "teamB/"]);

        const createdCount = topicConfigs.filter((tc) =>
            tc.Events.some((e) => e.includes("ObjectCreated"))
        ).length;
        const removedCount = topicConfigs.filter((tc) =>
            tc.Events.some((e) => e.includes("ObjectRemoved"))
        ).length;
        expect(createdCount).toBe(2);
        expect(removedCount).toBe(2);
    });

    test("cross-account SNS topic policy can be scoped to the external source", () => {
        // Mirrors the topic resource policy storageBuilder adds for cross-account
        // buckets so S3 in the bucket's account may publish to the VAMS topic.
        const app = new cdk.App();
        const stack = new cdk.Stack(app, "TopicPolicyStack", {
            env: { account: "111111111111", region: "us-east-1" },
        });
        const topic = new sns.Topic(stack, "CreatedTopic");
        topic.addToResourcePolicy(
            new cdk.aws_iam.PolicyStatement({
                effect: cdk.aws_iam.Effect.ALLOW,
                principals: [new cdk.aws_iam.ServicePrincipal("s3.amazonaws.com")],
                actions: ["SNS:Publish"],
                resources: [topic.topicArn],
                conditions: {
                    ArnLike: { "aws:SourceArn": "arn:aws:s3:::external-shared-bucket" },
                    StringEquals: { "aws:SourceAccount": "222222222222" },
                },
            })
        );

        const template = Template.fromStack(stack);
        template.hasResourceProperties(
            "AWS::SNS::TopicPolicy",
            Match.objectLike({
                PolicyDocument: Match.objectLike({
                    Statement: Match.arrayWith([
                        Match.objectLike({
                            Action: "SNS:Publish",
                            Condition: {
                                ArnLike: { "aws:SourceArn": "arn:aws:s3:::external-shared-bucket" },
                                StringEquals: { "aws:SourceAccount": "222222222222" },
                            },
                        }),
                    ]),
                }),
            })
        );
    });
});
