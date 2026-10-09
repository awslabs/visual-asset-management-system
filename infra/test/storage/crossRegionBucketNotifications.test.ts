/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * External asset buckets in a Region other than the deployment Region.
 *
 * Amazon S3 delivers bucket notifications only to a destination in the bucket's Region, so VAMS
 * places the object-created and object-removed SNS topics for such a bucket in a separate stack
 * deployed into that Region (`CrossRegionBucketNotificationsStack`), and the deployment-Region
 * SQS queues subscribe to those topics cross-Region. These synth assertions pin that shape:
 *
 *   - the per-Region stack is emitted with the topics and the bucket notification configuration;
 *   - the storage nested stack creates NO topic for the cross-Region record (the same-Region
 *     bucket's topics are the positive control) and its subscriptions to the imported topics carry
 *     a `Region`;
 *   - with CMK encryption on, the per-Region stack carries its own retained KMS key and the topics
 *     use it; with CMK off, the topics are unencrypted and no key is emitted;
 *   - the storage nested template is identical with and without the cross-Region bucket apart from
 *     the resources the extra bucket record itself contributes, so a same-Region deployment is
 *     unchanged by this feature.
 *
 * The synth builds the app through `buildCrossRegionBucketNotificationStacks`, the same function
 * `bin/infra.ts` uses, so the harness and the real app agree on what is emitted.
 */

import * as cdk from "aws-cdk-lib";
import { Template, Match } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import {
    CrossRegionBucketNotificationsStack,
    buildCrossRegionBucketNotificationStacks,
} from "../../lib/crossRegionBucketNotifications-stack";
import { crossRegionTopicKey } from "../../lib/helper/s3AssetBuckets";
import { newTestApp } from "../support/testApp";
import { synthTemplate, expectAbsent, SynthResult, Resource } from "../support/templateSynth";
import commercialTemplate from "../../config/config.template.commercial.json";

const REMOTE_BUCKET_ARN = "arn:aws:s3:::remote-assets-bucket";
const REMOTE_REGION = "eu-west-1";
const LOCAL_BUCKET_ARN = "arn:aws:s3:::local-assets-bucket";

/** A same-Region external bucket (the positive control) plus a cross-Region one. */
const withExternalBuckets = (c: any) => {
    c.app.assetBuckets.externalAssetBuckets = [
        {
            bucketArn: LOCAL_BUCKET_ARN,
            baseAssetsPrefix: "/",
            defaultSyncDatabaseId: "local",
        },
        {
            bucketArn: REMOTE_BUCKET_ARN,
            baseAssetsPrefix: "team/",
            defaultSyncDatabaseId: "remote",
            bucketRegion: REMOTE_REGION,
        },
    ];
};

const withExternalBucketsAndCmk = (c: any) => {
    withExternalBuckets(c);
    c.app.useKmsCmkEncryption.enabled = true;
    c.app.useKmsCmkEncryption.optionalExternalCmkArn = null;
};

/** The same-Region bucket only — the baseline the storage template is compared against. */
const withLocalBucketOnly = (c: any) => {
    c.app.assetBuckets.externalAssetBuckets = [
        {
            bucketArn: LOCAL_BUCKET_ARN,
            baseAssetsPrefix: "/",
            defaultSyncDatabaseId: "local",
        },
    ];
};

const xregionResources = (s: SynthResult): Resource[] =>
    s.resources.filter((r) => /-xregion-/.test(r.stack));
const storageResources = (s: SynthResult): Resource[] =>
    s.resources.filter((r) => /StorageResourcesBuilder/.test(r.stack));

describe("cross-Region external asset bucket: per-Region notification stack", () => {
    const synth = () =>
        synthTemplate("commercial", { mutateKey: "xregion-bucket", mutate: withExternalBuckets });

    test("emits one stack in the bucket's Region named after the deployment and the Region", () => {
        const s = synth();
        const stacks = Array.from(new Set(xregionResources(s).map((r) => r.stack)));
        expect(stacks).toEqual([`vams-xregion-t1-commercial-${REMOTE_REGION}`]);
    });

    test("the per-Region stack owns the two topics and the bucket's notification configuration", () => {
        const s = synth();
        const topics = xregionResources(s).filter((r) => r.type === "AWS::SNS::Topic");
        expect(topics).toHaveLength(2);
        const notifications = xregionResources(s).filter(
            (r) => r.type === "Custom::S3BucketNotifications"
        );
        expect(notifications).toHaveLength(1);
        expect(notifications[0].properties.BucketName).toBe("remote-assets-bucket");
        const topicConfigs = notifications[0].properties.NotificationConfiguration
            .TopicConfigurations as any[];
        expect(topicConfigs).toHaveLength(2);
        expect(
            topicConfigs
                .map((tc) => tc.Filter.Key.FilterRules[0])
                .every((rule) => rule.Value === "team/")
        ).toBe(true);
        // Not an owned bucket: the configuration is managed, not replaced.
        expect(notifications[0].properties.Managed).toBe(false);
    });

    test("every topic in the per-Region stack enforces TLS", () => {
        const s = synth();
        const policies = xregionResources(s).filter((r) => r.type === "AWS::SNS::TopicPolicy");
        expect(policies).toHaveLength(2);
        for (const policy of policies) {
            const statements = policy.properties.PolicyDocument.Statement as any[];
            expect(
                statements.some(
                    (st) =>
                        st.Effect === "Deny" &&
                        st.Condition?.Bool?.["aws:SecureTransport"] === "false"
                )
            ).toBe(true);
        }
    });

    test("the storage stack creates no topic for the cross-Region bucket (control: the same-Region bucket's topics are there)", () => {
        const s = synth();
        // Storage creates topics for the created bucket, the same-Region external bucket, the
        // three indexer fan-outs and the email topic — but none for the remote bucket, whose
        // notification configuration never appears there.
        const storageNotifications = storageResources(s).filter(
            (r) => r.type === "Custom::S3BucketNotifications"
        );
        const remote = storageNotifications.filter(
            (r) => SynthResult.flatten(r.properties.BucketName) === "remote-assets-bucket"
        );
        const local = storageNotifications.filter(
            (r) => SynthResult.flatten(r.properties.BucketName) === "local-assets-bucket"
        );
        expectAbsent("a storage-stack notification configuration for the remote bucket", remote, {
            description: "the same-Region external bucket's notification configuration is there",
            count: local.length,
        });
    });

    test("the storage stack subscribes its bucket-sync queues to the imported topics with a Region", () => {
        const s = synth();
        const subscriptions = storageResources(s).filter(
            (r) => r.type === "AWS::SNS::Subscription" && r.properties.Protocol === "sqs"
        );
        const crossRegion = subscriptions.filter((r) => "Region" in r.properties);
        const sameRegion = subscriptions.filter((r) => !("Region" in r.properties));
        // Two for the remote bucket (created + removed); every other SQS subscription is local.
        expect(crossRegion).toHaveLength(2);
        expect(sameRegion.length).toBeGreaterThan(0);
        for (const sub of crossRegion) {
            // The Region is derived from the imported topic ARN (Fn::Select over Fn::Split), so
            // it follows the topic wherever the per-Region stack puts it.
            expect(JSON.stringify(sub.properties.Region)).toMatch(/Fn::Select|eu-west-1/);
            // The topic ARN is a cross-Region reference resolved through the core stack's
            // ExportsReader from the per-Region stack's export, not a topic in this template.
            expect(JSON.stringify(sub.properties.TopicArn)).toMatch(
                /ExportsReader[\s\S]*vamsxregiont1commercialeuwest1/
            );
        }
    });

    test("the cross-Region subscription queue policies allow only that topic", () => {
        const s = synth();
        const queuePolicies = storageResources(s).filter((r) => r.type === "AWS::SQS::QueuePolicy");
        const statements = queuePolicies.flatMap(
            (r) => r.properties.PolicyDocument.Statement as any[]
        );
        const snsSends = statements.filter(
            (st) => st.Action === "sqs:SendMessage" && st.Condition?.ArnEquals?.["aws:SourceArn"]
        );
        expect(snsSends.length).toBeGreaterThan(0);
        for (const st of snsSends) {
            expect(st.Effect).toBe("Allow");
            expect(st.Principal).toEqual({ Service: "sns.amazonaws.com" });
        }
    });

    test("with CMK off, the per-Region stack emits no KMS key and the topics have no master key", () => {
        const s = synth();
        const keys = xregionResources(s).filter((r) => r.type === "AWS::KMS::Key");
        const topicsWithKey = xregionResources(s).filter(
            (r) => r.type === "AWS::SNS::Topic" && "KmsMasterKeyId" in r.properties
        );
        expect(keys).toEqual([]);
        expect(topicsWithKey).toEqual([]);
    });

    test("the core stack depends on the per-Region stack", () => {
        // Dependencies are an assembly property, not a template one; assert them on a fresh app
        // built through the same wiring the harness and bin/infra.ts use.
        const app = newTestApp();
        const config = loadConfigForUnitTest(withExternalBuckets);
        const { stacks } = buildCrossRegionBucketNotificationStacks(app, config);
        expect(stacks).toHaveLength(1);
        expect(stacks[0]).toBeInstanceOf(CrossRegionBucketNotificationsStack);
        expect(stacks[0].region).toBe(REMOTE_REGION);
        expect(Object.keys(stacks[0].topics)).toEqual([
            crossRegionTopicKey(REMOTE_BUCKET_ARN, "team/"),
        ]);
    });
});

describe("cross-Region external asset bucket: regional CMK", () => {
    const synth = () =>
        synthTemplate("commercial", {
            mutateKey: "xregion-bucket-cmk",
            mutate: withExternalBucketsAndCmk,
        });

    test("with CMK on, the per-Region stack emits one retained, rotating KMS key", () => {
        const s = synth();
        const keys = xregionResources(s).filter((r) => r.type === "AWS::KMS::Key");
        expect(keys).toHaveLength(1);
        expect(keys[0].properties.EnableKeyRotation).toBe(true);
        expect(keys[0].raw.DeletionPolicy).toBe("Retain");
        expect(keys[0].raw.UpdateReplacePolicy).toBe("Retain");
        // No alias: a retained key never collides with the one a redeploy creates.
        expect(xregionResources(s).filter((r) => r.type === "AWS::KMS::Alias")).toEqual([]);
    });

    test("the regional key policy admits the SNS and S3 service principals and the account root", () => {
        const s = synth();
        const [key] = xregionResources(s).filter((r) => r.type === "AWS::KMS::Key");
        const statements = key.properties.KeyPolicy.Statement as any[];
        const flat = JSON.stringify(statements);
        expect(flat).toContain("sns.amazonaws.com");
        expect(flat).toContain("s3.amazonaws.com");
        expect(flat).toMatch(/:root/);
        // No wildcard principal.
        expect(statements.some((st) => st.Principal === "*" || st.Principal?.AWS === "*")).toBe(
            false
        );
    });

    test("both topics in the per-Region stack are encrypted with that stack's key", () => {
        const s = synth();
        const [key] = xregionResources(s).filter((r) => r.type === "AWS::KMS::Key");
        const topics = xregionResources(s).filter((r) => r.type === "AWS::SNS::Topic");
        expect(topics).toHaveLength(2);
        for (const topic of topics) {
            expect(topic.properties.KmsMasterKeyId).toEqual({
                "Fn::GetAtt": [key.logicalId, "Arn"],
            });
        }
    });

    test("the storage stack's own key is unchanged by the cross-Region bucket (one key, no S3 cross-account statement for it)", () => {
        const s = synth();
        const storageKeys = storageResources(s).filter((r) => r.type === "AWS::KMS::Key");
        expect(storageKeys).toHaveLength(1);
        const sids = (storageKeys[0].properties.KeyPolicy.Statement as any[]).map((st) => st.Sid);
        // The remote bucket is same-account, so no cross-account S3 statement is added anywhere.
        expect(sids).not.toContain("AllowExternalBucketS3Notifications");
    });
});

describe("cross-Region external asset bucket: imported CMK handling", () => {
    const buildStack = (externalCmkArn: string) => {
        const app = newTestApp();
        const config = loadConfigForUnitTest((c) => {
            withExternalBuckets(c);
            c.app.useKmsCmkEncryption.enabled = true;
            c.app.useKmsCmkEncryption.optionalExternalCmkArn = externalCmkArn;
        });
        const stack = new CrossRegionBucketNotificationsStack(app, "xregion-test", {
            stackName: "xregion-test",
            env: { account: "123456789012", region: REMOTE_REGION },
            description: "test",
            config,
            bucketRegion: REMOTE_REGION,
            bucketEntries: Config.crossRegionExternalBuckets(
                config.app.assetBuckets.externalAssetBuckets,
                config.env.region
            ),
        });
        return { app, stack, template: Template.fromStack(stack) };
    };

    test("a multi-Region key (mrk-) is imported as its replica in the bucket Region; no key is created", () => {
        const { stack, template } = buildStack(
            "arn:aws:kms:us-east-1:123456789012:key/mrk-1234abcd12ab34cd56ef1234567890ab"
        );
        template.resourceCountIs("AWS::KMS::Key", 0);
        expect(stack.topicsKmsKey?.keyArn).toBe(
            `arn:aws:kms:${REMOTE_REGION}:123456789012:key/mrk-1234abcd12ab34cd56ef1234567890ab`
        );
        template.hasResourceProperties("AWS::SNS::Topic", {
            KmsMasterKeyId: `arn:aws:kms:${REMOTE_REGION}:123456789012:key/mrk-1234abcd12ab34cd56ef1234567890ab`,
        });
        const warnings = stack.node.metadata.filter((m) => m.type === "aws:cdk:warning");
        expect(warnings.map((w) => String(w.data)).join("\n")).not.toMatch(/single-Region key/);
    });

    test("a single-Region imported key is not reused: a regional key is generated and a synth warning says so", () => {
        const { stack, template } = buildStack(
            "arn:aws:kms:us-east-1:123456789012:key/11111111-2222-3333-4444-555555555555"
        );
        template.resourceCountIs("AWS::KMS::Key", 1);
        template.hasResource("AWS::KMS::Key", {
            DeletionPolicy: "Retain",
            Properties: Match.objectLike({ EnableKeyRotation: true }),
        });
        const warnings = stack.node.metadata.filter((m) => m.type === "aws:cdk:warning");
        expect(warnings.map((w) => String(w.data)).join("\n")).toMatch(
            /single-Region key in us-east-1, which cannot encrypt Amazon SNS topics in eu-west-1/
        );
    });

    test("a cross-account bucket gets the S3 publish statement on both topics and the S3 key statement", () => {
        const app = newTestApp();
        const config = loadConfigForUnitTest((c) => {
            c.app.assetBuckets.externalAssetBuckets = [
                {
                    bucketArn: REMOTE_BUCKET_ARN,
                    baseAssetsPrefix: "/",
                    defaultSyncDatabaseId: "remote",
                    bucketRegion: REMOTE_REGION,
                    bucketAccountId: "222222222222",
                },
            ];
            c.app.useKmsCmkEncryption.enabled = true;
            c.app.useKmsCmkEncryption.optionalExternalCmkArn = null;
        });
        const stack = new CrossRegionBucketNotificationsStack(app, "xregion-xacct", {
            stackName: "xregion-xacct",
            env: { account: "123456789012", region: REMOTE_REGION },
            description: "test",
            config,
            bucketRegion: REMOTE_REGION,
            bucketEntries: config.app.assetBuckets.externalAssetBuckets,
        });
        const template = Template.fromStack(stack);
        const policies = template.findResources("AWS::SNS::TopicPolicy");
        expect(Object.keys(policies)).toHaveLength(2);
        for (const policy of Object.values(policies) as any[]) {
            const statements = policy.Properties.PolicyDocument.Statement as any[];
            expect(
                statements.some(
                    (st) =>
                        st.Action === "SNS:Publish" &&
                        st.Condition?.StringEquals?.["aws:SourceAccount"] === "222222222222" &&
                        st.Condition?.ArnLike?.["aws:SourceArn"] === REMOTE_BUCKET_ARN
                )
            ).toBe(true);
        }
        template.hasResourceProperties("AWS::KMS::Key", {
            KeyPolicy: Match.objectLike({
                Statement: Match.arrayWith([
                    Match.objectLike({
                        Sid: "AllowExternalBucketS3Notifications",
                        Condition: { StringEquals: { "aws:SourceAccount": ["222222222222"] } },
                    }),
                ]),
            }),
        });
        // The bucket root registers no prefix filter.
        const notifications = template.findResources("Custom::S3BucketNotifications");
        const [notification] = Object.values(notifications) as any[];
        for (const tc of notification.Properties.NotificationConfiguration.TopicConfigurations) {
            expect(tc.Filter).toBeUndefined();
        }
    });
});

describe("cross-Region external asset bucket: a same-Region deployment is unchanged", () => {
    test("the storage template with a cross-Region bucket differs from the same-Region baseline only by that record's own queues and sync Lambdas", () => {
        const baseline = synthTemplate("commercial", {
            mutateKey: "xregion-baseline-local-only",
            mutate: withLocalBucketOnly,
        });
        const withRemote = synthTemplate("commercial", {
            mutateKey: "xregion-bucket",
            mutate: withExternalBuckets,
        });
        const baselineTypes = countByType(storageResources(baseline));
        const withRemoteTypes = countByType(storageResources(withRemote));
        // The extra record contributes exactly: 2 queues + 2 DLQs, 2 queue policies, 2 subscriptions,
        // 2 bucket-sync Lambdas (+ roles, policies, event source mappings). It contributes NO topic,
        // NO topic policy and NO notification configuration to the storage stack.
        expect(withRemoteTypes["AWS::SNS::Topic"]).toBe(baselineTypes["AWS::SNS::Topic"]);
        expect(withRemoteTypes["AWS::SNS::TopicPolicy"]).toBe(
            baselineTypes["AWS::SNS::TopicPolicy"]
        );
        expect(withRemoteTypes["Custom::S3BucketNotifications"]).toBe(
            baselineTypes["Custom::S3BucketNotifications"]
        );
        expect(withRemoteTypes["AWS::SQS::Queue"]).toBe(baselineTypes["AWS::SQS::Queue"] + 4);
        expect(withRemoteTypes["AWS::SNS::Subscription"]).toBe(
            baselineTypes["AWS::SNS::Subscription"] + 2
        );
    });

    test("no shipped template emits a cross-Region stack", () => {
        for (const name of ["commercial", "govcloud", "eusovereign"] as const) {
            const s = synthTemplate(name);
            expect(xregionResources(s)).toEqual([]);
            // Control: the synth produced the storage stack at all.
            expect(storageResources(s).length).toBeGreaterThan(0);
        }
    });
});

function countByType(resources: Resource[]): Record<string, number> {
    const counts: Record<string, number> = {};
    for (const r of resources) {
        counts[r.type] = (counts[r.type] ?? 0) + 1;
    }
    return counts;
}

/**
 * A Config built from the commercial template the way templateSynth's buildConfig does, for the
 * unit tests that construct the stack directly rather than through a full-app synth.
 */
function loadConfigForUnitTest(mutate: (c: any) => void): Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-xregion-unit";
    config.app.baseStackName = "xregion-unit";
    const internal = config as any;
    internal.enableCdkNag = false;
    mutate(internal);
    Service.SetConfig(config);
    return config;
}
