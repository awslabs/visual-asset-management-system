/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The bucket-sync created-events function opts out of Lambda recursive-loop detection, and every
 * dead-letter queue on the path from an S3 event to the search index is alarmed: both bucket-sync
 * directions and both indexers. A dropped create leaves a file missing from search; a dropped delete
 * leaves a stale search entry.
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
 * is emitted only in the partitions where the detection exists: commercial and EU Sovereign. GovCloud
 * documents it as not available and gets no property. The opt-out is a stopgap until the self-write is
 * removed (#1207).
 *
 * The compliance job Lambda opts out too, in the same partitions: a deleted database's compliance
 * move and a database binding change applied in the background continue themselves on its queue, one
 * message per run, for as many runs as the database's size needs (D161), each job stopped by its own
 * run limit instead. The compliance trigger, which evaluates the jobs' evaluations, keeps the default
 * in every partition, as does every other function.
 */

import { Resource, SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

const CREATED_HANDLER = "handlers.indexing.sqsBucketSync.lambda_handler_created";
const DELETED_HANDLER = "handlers.indexing.sqsBucketSync.lambda_handler_deleted";
const COMPLIANCE_TRIGGER_HANDLER = "handlers.compliance.complianceTrigger.lambda_handler";
const COMPLIANCE_JOBS_HANDLER = "handlers.compliance.complianceJobs.lambda_handler";
/** The functions that opt out of recursive-loop detection where the partition has it. */
const OPTED_OUT = new Set([CREATED_HANDLER, COMPLIANCE_JOBS_HANDLER]);

/** The created-events function's `RecursiveLoop` per template: `Allow` where the partition has detection. */
const EXPECTED_RECURSIVE_LOOP: Record<TemplateName, "Allow" | undefined> = {
    commercial: "Allow",
    eusovereign: "Allow",
    govcloud: undefined,
};

function functionsWithHandler(synth: SynthResult, handler: string): Resource[] {
    return synth.where("AWS::Lambda::Function", (r) => r.properties.Handler === handler);
}

/**
 * Every alarmed dead-letter queue, by logical-id prefix, with the alarm-name suffix it carries. The
 * bucket-sync DLQs are per registered bucket and carry its queue index; the indexer DLQs are one each.
 */
const ALARMED_DLQS: { prefix: string; alarmName: (t: TemplateName) => RegExp }[] = [
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
];

function dlqsWithPrefix(synth: SynthResult, prefix: string): Resource[] {
    return synth.ofType("AWS::SQS::Queue").filter((q) => q.logicalId.startsWith(prefix));
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

    test("the created-events function allows recursion in commercial and EU Sovereign only", () => {
        const expected = EXPECTED_RECURSIVE_LOOP[templateName];
        for (const fn of functionsWithHandler(synth, CREATED_HANDLER)) {
            expect(fn.properties.RecursiveLoop).toBe(expected);
        }
    });

    test("the compliance job Lambda allows recursion in commercial and EU Sovereign only", () => {
        const jobs = functionsWithHandler(synth, COMPLIANCE_JOBS_HANDLER);
        expect(jobs).toHaveLength(1);
        expect(jobs[0].properties.RecursiveLoop).toBe(EXPECTED_RECURSIVE_LOOP[templateName]);
    });

    test("the compliance trigger keeps the detection on in every partition", () => {
        const triggers = functionsWithHandler(synth, COMPLIANCE_TRIGGER_HANDLER);
        expect(triggers).toHaveLength(1);
        expect(triggers[0].properties.RecursiveLoop).toBeUndefined();
    });

    test("no other function changes its recursive-loop setting", () => {
        const offenders = synth
            .ofType("AWS::Lambda::Function")
            .filter((fn) => !OPTED_OUT.has(fn.properties.Handler))
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

    /** Alarms whose metric dimension names the given queue. */
    function alarmsFor(queue: Resource): Resource[] {
        return synth.where("AWS::CloudWatch::Alarm", (a) =>
            SynthResult.flatten(a.properties.Dimensions).includes(
                `\${${queue.logicalId}.QueueName}`
            )
        );
    }

    test.each(ALARMED_DLQS)("each $prefix has exactly one depth alarm above zero", ({ prefix }) => {
        const dlqs = dlqsWithPrefix(synth, prefix);
        expect(dlqs.length).toBeGreaterThan(0);
        for (const dlq of dlqs) {
            const alarms = alarmsFor(dlq);
            expect(alarms.length).toBe(1);
            const p = alarms[0].properties;
            expect(p.Namespace).toBe("AWS/SQS");
            expect(p.MetricName).toBe("ApproximateNumberOfMessagesVisible");
            expect(p.Statistic).toBe("Maximum");
            expect(p.Threshold).toBe(0);
            expect(p.ComparisonOperator).toBe("GreaterThanThreshold");
            expect(p.EvaluationPeriods).toBe(1);
            expect(p.TreatMissingData).toBe("notBreaching");
            expect(p.AlarmActions).toBeUndefined();
        }
    });

    test.each(ALARMED_DLQS)(
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
            ALARMED_DLQS.flatMap(({ prefix }) => dlqsWithPrefix(synth, prefix)).map(
                (q) => q.logicalId
            )
        );
        const others = synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => !alarmedIds.has(q.logicalId))
            .filter((q) => alarmsFor(q).length > 0)
            .map((q) => q.logicalId);
        expect(others).toEqual([]);
    });
});
