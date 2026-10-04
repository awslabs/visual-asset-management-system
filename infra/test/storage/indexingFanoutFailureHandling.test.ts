/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The indexing fan-out's event source mappings must turn a failed delivery into a redelivery rather
 * than a checkpoint.
 *
 * Two hops feed every indexer topic subscriber (the OpenSearch indexers, Physna, Garnet):
 *
 *   - DynamoDB stream -> `snsQueuing` -> file, asset or database indexer topic. A stream mapping
 *     checkpoints past a batch on any normal return. `snsQueuing` reports the records it did not
 *     publish in `batchItemFailures`, which the mapping reads only with
 *     `FunctionResponseTypes: ["ReportBatchItemFailures"]`. Bisect splits a batch that fails outright,
 *     and the retry bound keeps one failing batch from holding its shard for the stream's 24-hour
 *     retention.
 *   - bucket-sync queue -> `sqsBucketSync` -> file indexer topic. The handler reports the messages
 *     whose records did not reach the topic; without the response type the mapping deletes them.
 *
 * The handler half of both contracts is in
 * `backend/tests/handlers/indexing/test_snsQueuing_batch_failures.py` and
 * `backend/tests/handlers/indexing/test_sqsBucketSync_batch_failures.py`.
 *
 * Asserted on the emitted template for every shipped partition: commercial builds these mappings on
 * the non-govCloud branch of `storageBuilder-nestedStack.ts` and govcloud / eusovereign on the
 * govCloud branch, which are separate call sites.
 */

import { Resource, SynthResult, TemplateName, synthTemplate } from "../support/templateSynth";

// A full-app synth is ~20-30 s and this file needs three of them.
jest.setTimeout(900_000);

const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

const SNS_QUEUING_HANDLER = "handlers.indexing.snsQueuing.lambda_handler";

/** The resource of `type` in the same template whose logical id a reference contains (longest wins). */
const referenced = (
    s: SynthResult,
    type: string,
    stack: string,
    reference: any
): Resource | undefined => {
    const flat = SynthResult.flatten(reference);
    if (flat === "") return undefined;
    return s
        .where(type, (r) => r.stack === stack && flat.includes(r.logicalId))
        .sort((a, b) => b.logicalId.length - a.logicalId.length)[0];
};

const mappingTarget = (s: SynthResult, m: Resource) =>
    referenced(s, "AWS::Lambda::Function", m.stack, m.properties.FunctionName);

const mappingSourceQueue = (s: SynthResult, m: Resource) =>
    referenced(s, "AWS::SQS::Queue", m.stack, m.properties.EventSourceArn);

/** Mappings that invoke the snsQueuing handler, matched on the handler rather than on construct ids. */
const streamMappings = (s: SynthResult) =>
    s.where(
        "AWS::Lambda::EventSourceMapping",
        (m) => SynthResult.flatten(mappingTarget(s, m)?.properties.Handler) === SNS_QUEUING_HANDLER
    );

/** Which bucket-sync direction a queue name belongs to, or undefined for any other queue. */
const bucketSyncDirection = (queue: Resource | undefined): string | undefined =>
    /-bucketSync(Created|Deleted)--\d+$/.exec(
        SynthResult.flatten(queue?.properties.QueueName)
    )?.[1];

const bucketSyncMappings = (s: SynthResult) =>
    s.where(
        "AWS::Lambda::EventSourceMapping",
        (m) => bucketSyncDirection(mappingSourceQueue(s, m)) !== undefined
    );

const reportsBatchItemFailures = (m: Resource) =>
    JSON.stringify(m.properties.FunctionResponseTypes) ===
    JSON.stringify(["ReportBatchItemFailures"]);

describe.each(TEMPLATES)("%s: indexing fan-out event source mappings", (templateName) => {
    let s: SynthResult;

    beforeAll(() => {
        s = synthTemplate(templateName);
    });

    test("the snsQueuing stream mappings and both bucket-sync directions are emitted", () => {
        // The control for every assertion below: each one is satisfied by a template that emitted
        // none of these mappings. Eight streams feed the three queuing Lambdas today (file: 2,
        // asset: 4, database: 2); a lower bound lets a new indexed stream pass.
        const mappings = streamMappings(s);
        expect(mappings.length).toBeGreaterThanOrEqual(8);
        expect(new Set(mappings.map((m) => mappingTarget(s, m)!.logicalId)).size).toBe(3);

        const directions = bucketSyncMappings(s).map((m) =>
            bucketSyncDirection(mappingSourceQueue(s, m))
        );
        expect(new Set(directions)).toEqual(new Set(["Created", "Deleted"]));
    });

    test("every snsQueuing stream mapping reads batchItemFailures and bisects a failing batch", () => {
        const offenders = streamMappings(s)
            .filter(
                (m) =>
                    !reportsBatchItemFailures(m) || m.properties.BisectBatchOnFunctionError !== true
            )
            .map(
                (m) =>
                    `${templateName} ${m.logicalId}: FunctionResponseTypes=${JSON.stringify(
                        m.properties.FunctionResponseTypes
                    )} BisectBatchOnFunctionError=${m.properties.BisectBatchOnFunctionError}`
            );
        expect(offenders).toEqual([]);
    });

    test("every snsQueuing stream mapping bounds its retries", () => {
        // An absent MaximumRetryAttempts (or -1) retries a failing batch until its records expire
        // from the stream, holding every later change on that shard behind it. Bounded from below
        // only, so a smaller bound passes.
        const offenders = streamMappings(s)
            .filter((m) => {
                const retries = m.properties.MaximumRetryAttempts;
                return typeof retries !== "number" || retries < 0;
            })
            .map(
                (m) =>
                    `${templateName} ${m.logicalId}: MaximumRetryAttempts=${JSON.stringify(
                        m.properties.MaximumRetryAttempts
                    )}`
            );
        expect(offenders).toEqual([]);
    });

    test("every bucket-sync mapping reads batchItemFailures", () => {
        const offenders = bucketSyncMappings(s)
            .filter((m) => !reportsBatchItemFailures(m))
            .map(
                (m) =>
                    `${templateName} ${m.logicalId}: FunctionResponseTypes=${JSON.stringify(
                        m.properties.FunctionResponseTypes
                    )}`
            );
        expect(offenders).toEqual([]);
    });
});
