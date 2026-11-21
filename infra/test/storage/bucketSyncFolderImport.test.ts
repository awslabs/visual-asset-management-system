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
