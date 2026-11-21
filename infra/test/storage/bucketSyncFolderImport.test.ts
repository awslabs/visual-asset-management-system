/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A folder import sends the folder's existing objects to the bucket-sync created-events queue, a page
 * per message (sqsBucketSync.import_folder). The created-events function may send to its own queue
 * and to the deployment's rejected-record queue, and to no other; the deleted-events function sends to
 * none. Its queue URL reaches it as BUCKET_SYNC_CREATED_QUEUE_URL.
 */

import {
    Resource,
    SynthResult,
    actionsOf,
    resourcesOf,
    statementsOf,
    synthTemplate,
} from "../support/templateSynth";

jest.setTimeout(600_000);

const CREATED_HANDLER = "handlers.indexing.sqsBucketSync.lambda_handler_created";
const DELETED_HANDLER = "handlers.indexing.sqsBucketSync.lambda_handler_deleted";

const synth = () =>
    synthTemplate("commercial", {
        mutate: (c: any) => {
            c.app.assetBuckets.externalAssetBuckets = [
                {
                    bucketArn: "arn:aws:s3:::vams-import-external",
                    baseAssetsPrefix: "data/",
                    defaultSyncDatabaseId: "ext",
                },
            ];
        },
        mutateKey: "folder-import-two-registrations",
    });

const functionsOf = (s: SynthResult, handler: string): Resource[] =>
    s.where("AWS::Lambda::Function", (r) => r.properties.Handler === handler);

/** The queue logical ids each SendMessage statement of the function names. */
function sendTargets(s: SynthResult, fn: Resource): string[] {
    return statementsOf(s, fn)
        .filter(
            (st) => st.Effect === "Allow" && actionsOf(st).some((a) => /^sqs:SendMessage/.test(a))
        )
        .flatMap(resourcesOf)
        .map((r) => (r.match(/\$\{([A-Za-z0-9]+)\.Arn\}/) || [])[1] || r);
}

/** The queue logical id the function's event source mapping reads. */
function ownQueue(s: SynthResult, fn: Resource): string {
    const [esm] = s.where("AWS::Lambda::EventSourceMapping", (r) =>
        SynthResult.flatten(r.properties.FunctionName).includes(fn.logicalId)
    );
    return (SynthResult.flatten(esm.properties.EventSourceArn).match(/\$\{([A-Za-z0-9]+)\.Arn\}/) ||
        [])[1];
}

describe("bucket-sync folder import permissions", () => {
    test("each created-events function sends to its own queue and the rejected-record queue only", () => {
        const s = synth();
        const fns = functionsOf(s, CREATED_HANDLER);
        expect(fns.length).toBe(2);
        const queues = new Set(fns.map((fn) => ownQueue(s, fn)));
        expect(queues.size).toBe(2);
        for (const fn of fns) {
            const own = ownQueue(s, fn);
            expect(own).toMatch(/^bucketSyncCreated/);
            const targets = sendTargets(s, fn);
            expect(targets).toContain(own);
            const others = targets.filter((t) => t !== own);
            expect(others.every((t) => /^bucketSyncRejectedQueue/.test(t))).toBe(true);
            expect(targets.filter((t) => queues.has(t) && t !== own)).toEqual([]);
            expect(
                SynthResult.flatten(
                    fn.properties.Environment?.Variables?.BUCKET_SYNC_CREATED_QUEUE_URL
                )
            ).toContain(own);
        }
    });

    test("createAsset sends to exactly the deployment's created-events queues, in one statement", () => {
        const s = synth();
        const [createAsset] = s.where(
            "AWS::Lambda::Function",
            (r) => r.properties.Handler === "handlers.assets.createAsset.lambda_handler"
        );
        expect(createAsset).toBeDefined();
        const sqsStatements = statementsOf(s, createAsset).filter((st) =>
            actionsOf(st).some((a) => a.startsWith("sqs:"))
        );
        expect(sqsStatements).toHaveLength(1);
        expect(actionsOf(sqsStatements[0])).toEqual(["sqs:SendMessage"]);
        const arns = resourcesOf(sqsStatements[0]);
        const createdQueueNames = s
            .ofType("AWS::SQS::Queue")
            .filter((q) => /^bucketSyncCreated(?!DLQ)/.test(q.logicalId))
            .map((q) => SynthResult.flatten(q.properties.QueueName));
        expect(createdQueueNames).toHaveLength(2);
        expect(arns.map((a) => a.split(":").pop()).sort()).toEqual(createdQueueNames.sort());
        for (const arn of arns) {
            expect(arn).not.toContain("*");
            // The partition and Region of the deployment (service-helper), not a literal.
            expect(arn.startsWith(`arn:${s.partition}:sqs:${s.region}:`)).toBe(true);
            expect(arn).not.toMatch(/bucketSyncDeleted|Rejected/);
        }
    });

    test("each registration row carries its created-events queue URL", () => {
        const s = synth();
        const [populator] = s
            .ofType("AWS::CloudFormation::CustomResource")
            .filter((r) => /PopulateS3AssetBucketsTable/.test(r.logicalId));
        const buckets = SynthResult.flatten(populator.properties.buckets);
        const createdQueues = s
            .ofType("AWS::SQS::Queue")
            .filter((q) => /^bucketSyncCreated(?!DLQ)/.test(q.logicalId));
        expect(createdQueues).toHaveLength(2);
        expect((buckets.match(/syncCreatedQueueUrl/g) || []).length).toBe(2);
        for (const queue of createdQueues) {
            expect(buckets).toContain(queue.logicalId);
        }
    });

    test("the deleted-events functions send to no queue", () => {
        const s = synth();
        const fns = functionsOf(s, DELETED_HANDLER);
        expect(fns.length).toBe(2);
        for (const fn of fns) {
            expect(sendTargets(s, fn)).toEqual([]);
            expect(
                fn.properties.Environment?.Variables?.BUCKET_SYNC_CREATED_QUEUE_URL
            ).toBeUndefined();
        }
    });
});

// #1207 D4: the bucket-sync metrics (namespace VAMS/BucketSync) carry a Deployment dimension, the first
// segment of the function's resource-name prefix (vamsObjectTags.metric_deployment), so every
// bucket-sync function must carry the deployment's one prefix.
describe("bucket-sync metric deployment dimension", () => {
    test("every bucket-sync function carries the deployment's resource-name prefix", () => {
        const s = synth();
        const fns = [...functionsOf(s, CREATED_HANDLER), ...functionsOf(s, DELETED_HANDLER)];
        expect(fns.length).toBe(4);
        const prefixes = new Set(
            fns.map((fn) => fn.properties.Environment?.Variables?.VAMS_RESOURCE_PARAM_PREFIX)
        );
        expect(prefixes.size).toBe(1);
        const [prefix] = [...prefixes];
        expect(typeof prefix).toBe("string");
        expect(prefix).toMatch(/^\/[^/]+\/resourceNames$/);
    });
});
