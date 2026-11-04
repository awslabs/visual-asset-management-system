/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The bucket-sync created-events function opts out of Lambda recursive-loop detection, and its
 * dead-letter queue is alarmed.
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

/** Source and dead-letter queues of the created direction. */
function createdDlqs(synth: SynthResult): Resource[] {
    return synth
        .ofType("AWS::SQS::Queue")
        .filter((q) => q.logicalId.startsWith("bucketSyncCreatedDLQ"));
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

describe.each(TEMPLATES)("%s: bucket-sync created DLQ alarm", (templateName) => {
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

    test("each created-direction DLQ has exactly one depth alarm above zero", () => {
        const dlqs = createdDlqs(synth);
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
        }
    });

    test("the alarm name carries the deployment and queue index", () => {
        const names = createdDlqs(synth)
            .flatMap((dlq) => alarmsFor(dlq))
            .map((a) => a.properties.AlarmName);
        expect(names.length).toBeGreaterThan(0);
        for (const name of names) {
            expect(name).toMatch(new RegExp(`-t1-${templateName}-bucketSyncCreatedDLQ--\\d+$`));
        }
    });

    test("no other queue gains an alarm", () => {
        // Scope check: the alarm is added for the created direction only.
        const createdIds = new Set(createdDlqs(synth).map((q) => q.logicalId));
        const others = synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => !createdIds.has(q.logicalId))
            .filter((q) => alarmsFor(q).length > 0)
            .map((q) => q.logicalId);
        expect(others).toEqual([]);
    });
});
