/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The bucket-sync created-events function opts out of Lambda recursive-loop detection, and every
 * dead-letter queue on the path from an S3 event to the search index is alarmed: both bucket-sync
 * directions and both indexers, and with vector search on the vector indexer, the
 * `vector.embedding.ready` rule target and the system-workflow launcher. A dropped create leaves a file
 * missing from search; a dropped delete leaves a stale search entry.
 *
 * `sqsBucketSync`'s created handler stamps an unstamped object by copying it onto itself, and the copy
 * re-enters the same queue. The handler ends that chain after one echo, but under sustained parallel
 * writes Lambda's detection counted the chain across shared SQS batches and stopped invocations before
 * the handler ran, dead-lettering whole batches of unrelated files with `Errors` at zero. Measured on
 * live deployments: files present in S3 and missing from search, `RecursiveInvocationsDropped` above
 * zero, and the DLQ holding the dropped batches with nothing alerting on it.
 *
 * The opt-out is scoped as narrowly as the cause. Only the created-events function writes back to its
 * own trigger; the deleted-events function and every other function keep the default, and the property
 * is emitted in the commercial partition only, where the detection exists.
 */

import { Resource, SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

const CREATED_HANDLER = "handlers.indexing.sqsBucketSync.lambda_handler_created";
const DELETED_HANDLER = "handlers.indexing.sqsBucketSync.lambda_handler_deleted";

function functionsWithHandler(synth: SynthResult, handler: string): Resource[] {
    return synth.where("AWS::Lambda::Function", (r) => r.properties.Handler === handler);
}

interface AlarmedDlq {
    prefix: string;
    alarmName: (t: TemplateName) => RegExp;
    /** Templates that emit the queue; every template when absent. */
    templates?: TemplateName[];
}

/**
 * Every alarmed dead-letter queue, by logical-id prefix, with the alarm-name suffix it carries. The
 * bucket-sync DLQs are per registered bucket and carry its queue index; the indexer DLQs are one each.
 * The vector search DLQs (the vector indexer's, the `vector.embedding.ready` rule target's and the
 * system-workflow launcher's) are on the same path to an index and carry the same alarm; only the
 * commercial template ships vector search on, and the GovCloud construct branch is checked below.
 */
const ALARMED_DLQS: AlarmedDlq[] = [
    {
        prefix: "bucketSyncCreatedDLQ",
        alarmName: (t) => new RegExp(`-t1-${t}-bucketSyncCreatedDLQ--\\d+$`),
    },
    {
        prefix: "bucketSyncDeletedDLQ",
        alarmName: (t) => new RegExp(`-t1-${t}-bucketSyncDeletedDLQ--\\d+$`),
    },
    { prefix: "FileIndexerSqsDLQ", alarmName: (t) => new RegExp(`-t1-${t}-fileIndexerDLQ$`) },
    { prefix: "AssetIndexerSqsDLQ", alarmName: (t) => new RegExp(`-t1-${t}-assetIndexerDLQ$`) },
    {
        prefix: "VectorIndexingVectorIndexerDLQ",
        alarmName: (t) => new RegExp(`-t1-${t}-vectorIndexerDLQ$`),
        templates: ["commercial"],
    },
    {
        prefix: "VectorIndexingVectorEmbeddingReadyRuleDLQ",
        alarmName: (t) => new RegExp(`-t1-${t}-vectorEmbeddingReadyRuleDLQ$`),
        templates: ["commercial"],
    },
    {
        prefix: "VectorIndexingSystemWorkflowLaunchDLQ",
        alarmName: (t) => new RegExp(`-t1-${t}-systemWorkflowLaunchDLQ$`),
        templates: ["commercial"],
    },
];

const VECTOR_DLQS = ALARMED_DLQS.filter((d) => d.prefix.startsWith("VectorIndexing"));

function alarmedIn(templateName: TemplateName): AlarmedDlq[] {
    return ALARMED_DLQS.filter((d) => !d.templates || d.templates.includes(templateName));
}

function dlqsWithPrefix(synth: SynthResult, prefix: string): Resource[] {
    return synth.ofType("AWS::SQS::Queue").filter((q) => q.logicalId.startsWith(prefix));
}

/** Alarms whose metric dimension names the given queue. */
function alarmsOn(synth: SynthResult, queue: Resource): Resource[] {
    return synth.where("AWS::CloudWatch::Alarm", (a) =>
        SynthResult.flatten(a.properties.Dimensions).includes(`\${${queue.logicalId}.QueueName}`)
    );
}

/** The depth alarm every alarmed DLQ carries: one message visible, no action. */
function expectDepthAlarm(alarm: Resource): void {
    const p = alarm.properties;
    expect(p.Namespace).toBe("AWS/SQS");
    expect(p.MetricName).toBe("ApproximateNumberOfMessagesVisible");
    expect(p.Statistic).toBe("Maximum");
    expect(p.Period).toBe(300);
    expect(p.Threshold).toBe(0);
    expect(p.ComparisonOperator).toBe("GreaterThanThreshold");
    expect(p.EvaluationPeriods).toBe(1);
    expect(p.TreatMissingData).toBe("notBreaching");
    expect(p.AlarmActions).toBeUndefined();
}

describe.each(TEMPLATES)("%s: bucket-sync recursive-loop setting", (templateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(templateName);
    });

    test("both bucket-sync directions emit functions", () => {
        // The control: every absence assertion below passes on a synth with no bucket-sync function.
        expect(functionsWithHandler(synth, CREATED_HANDLER).length).toBeGreaterThan(0);
        expect(functionsWithHandler(synth, DELETED_HANDLER).length).toBeGreaterThan(0);
    });

    test("the created-events function allows recursion in commercial only", () => {
        const expected = templateName === "commercial" ? "Allow" : undefined;
        for (const fn of functionsWithHandler(synth, CREATED_HANDLER)) {
            expect(fn.properties.RecursiveLoop).toBe(expected);
        }
    });

    test("no other function changes its recursive-loop setting", () => {
        const offenders = synth
            .ofType("AWS::Lambda::Function")
            .filter((fn) => fn.properties.Handler !== CREATED_HANDLER)
            .filter((fn) => fn.properties.RecursiveLoop !== undefined)
            .map((fn) => `${fn.stack}/${fn.logicalId}`);
        expect(synth.countOfType("AWS::Lambda::Function")).toBeGreaterThan(1);
        expect(offenders).toEqual([]);
    });
});

describe.each(TEMPLATES)("%s: bucket-sync and indexer DLQ alarms", (templateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(templateName);
    });

    const alarmsFor = (queue: Resource): Resource[] => alarmsOn(synth, queue);

    test.each(alarmedIn(templateName))(
        "each $prefix has exactly one depth alarm above zero",
        ({ prefix }) => {
            const dlqs = dlqsWithPrefix(synth, prefix);
            expect(dlqs.length).toBeGreaterThan(0);
            for (const dlq of dlqs) {
                const alarms = alarmsFor(dlq);
                expect(alarms.length).toBe(1);
                expectDepthAlarm(alarms[0]);
            }
        }
    );

    test.each(alarmedIn(templateName))(
        "the $prefix alarm name carries the deployment",
        ({ prefix, alarmName }) => {
            const names = dlqsWithPrefix(synth, prefix)
                .flatMap((dlq) => alarmsFor(dlq))
                .map((a) => a.properties.AlarmName);
            expect(names.length).toBeGreaterThan(0);
            for (const name of names) {
                expect(name).toMatch(alarmName(templateName));
            }
        }
    );

    test("no other queue gains an alarm", () => {
        // Scope check: source queues and the DLQs outside the indexing path stay unalarmed.
        const alarmedIds = new Set(
            alarmedIn(templateName)
                .flatMap(({ prefix }) => dlqsWithPrefix(synth, prefix))
                .map((q) => q.logicalId)
        );
        const others = synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => !alarmedIds.has(q.logicalId))
            .filter((q) => alarmsFor(q).length > 0)
            .map((q) => q.logicalId);
        expect(others).toEqual([]);
    });
});

describe("govcloud with vector search on: the vector DLQ alarms", () => {
    // No restricted template ships vector search on, so the construct's GovCloud branch is reached
    // through this mutation (the one t1IndexerBatchItemFailures.test.ts synthesizes).
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("govcloud", {
            mutateKey: "vectorGovcloud",
            mutate: (c: any) => {
                c.app.vectorSearch.enabled = true;
                c.vectorIndexName = "vec-amazon-titan-embed-text-v2-0-1024";
            },
        });
    });

    test.each(VECTOR_DLQS)("$prefix has one depth alarm named for the deployment", (dlqCase) => {
        const dlqs = dlqsWithPrefix(synth, dlqCase.prefix);
        expect(dlqs).toHaveLength(1);
        const alarms = alarmsOn(synth, dlqs[0]);
        expect(alarms).toHaveLength(1);
        expectDepthAlarm(alarms[0]);
        expect(alarms[0].properties.AlarmName).toMatch(dlqCase.alarmName("govcloud"));
    });

    test("the reindexer's asynchronous-invocation DLQ and the source queues stay unalarmed", () => {
        const vectorQueues = synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => q.logicalId.startsWith("VectorIndexing"));
        const alarmedIds = new Set(
            VECTOR_DLQS.flatMap(({ prefix }) => dlqsWithPrefix(synth, prefix)).map(
                (q) => q.logicalId
            )
        );
        // Control: the three alarmed DLQs, the two source queues and the reindexer's async DLQ.
        expect(vectorQueues).toHaveLength(6);
        expect(
            vectorQueues
                .filter((q) => !alarmedIds.has(q.logicalId))
                .filter((q) => alarmsOn(synth, q).length > 0)
                .map((q) => q.logicalId)
        ).toEqual([]);
    });
});
