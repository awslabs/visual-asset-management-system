/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every SQS queue that feeds a Lambda must dead-letter, and the two bucket-sync queues in particular.
 *
 * Why this is a synth assertion rather than a code review note. A queue with no redrive policy has no
 * failure signal at all: a record the event source cannot land is redelivered every visibility timeout
 * until the retention period expires, and nothing about that is visible in the metrics an operator
 * watches. This was MEASURED on a live deployment of `sqsBucketSync`: 15 messages cycled every 960
 * seconds for roughly 14 hours with `Errors` and `Throttles` both flat at zero and not one log line
 * written, because AWS Lambda's recursive-loop detection was dropping the invocations before the
 * handler ran. A dropped invocation never deletes its message, so the queue reissued it forever. The
 * only two signals were `RecursiveInvocationsDropped` and the in-flight message depth — neither of
 * which is where anyone looks first, and neither of which names the record.
 *
 * `sqsBucketSync` is the queue that matters most here. It is the single path by which S3 object changes
 * become VAMS file records, S3 object metadata, indexer notifications, and workflow triggers, for every
 * registered bucket. A record it cannot process is a file whose state VAMS never learns, and without a
 * DLQ that record is indistinguishable from one that was handled.
 *
 * The rule is written as a sweep over the whole assembly rather than as an assertion about the two
 * queues that were fixed, because the missing redrive policy was not a typo in one construct: three
 * other builders (`searchBuilder`, `workflowFunctions`, the Garnet and Physna add-ons) each pair every
 * queue with a DLQ, and `storageBuilder` was simply the one that did not. The next queue added is the
 * one this needs to catch.
 */

import { Resource, SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

/**
 * Queues that deliberately carry no redrive policy, each with the reason it is exempt.
 *
 * **Currently empty, and that is the state to preserve** — every SQS queue VAMS creates dead-letters.
 * The map and `exemptionFor` are kept because an exemption may one day be justified, and an escape
 * hatch that has to be built under pressure gets built without a recorded reason.
 *
 * Keyed by logical-id PREFIX: CDK appends a hash to the construct id, so an exact match would go stale
 * the moment anything about the construct's path changes.
 *
 * `LargeFileProcessingQueue` was the one entry (`deadLetterQueue: undefined, // No DLQ initially as
 * per requirements`). It now has a DLQ, so the entry is **removed rather than left in place**: a stale
 * exemption is worse than none, because it would silently re-excuse that queue the day someone dropped
 * its redrive policy — which is the exact regression this suite exists to catch.
 */
const NO_REDRIVE_EXPECTED: Record<string, string> = {};

function exemptionFor(logicalId: string): string | undefined {
    for (const [prefix, reason] of Object.entries(NO_REDRIVE_EXPECTED)) {
        if (logicalId.startsWith(prefix)) return reason;
    }
    return undefined;
}

/**
 * Property keys that put a queue in the role of somebody's failure destination.
 *
 * A queue is not excused from needing a redrive policy because of what it is CALLED — matching `/DLQ$/`
 * on the logical id would excuse a queue named `...DLQ` that nothing actually redrives to. It is
 * excused because some other resource names it as where failures go, and **four different resource
 * types express that, only one of which is a queue.** Scanning only `RedrivePolicy` reported
 * `PipelineExecutionRegisterDLQ` and `DeadlineCloudJobCallbackDLQ` as offenders: both are the
 * dead-letter target of an `AWS::Events::Rule` target (`DeadLetterConfig.Arn`), which no queue's own
 * properties mention.
 */
const FAILURE_DESTINATION_KEYS = /^(RedrivePolicy|DeadLetterConfig|OnFailure)$/;

/** Logical ids of every queue some resource names as its failure destination. */
function deadLetterTargets(synth: SynthResult): Set<string> {
    const queueIds = synth.ofType("AWS::SQS::Queue").map((q) => q.logicalId);
    const targets = new Set<string>();

    // Walk raw resource JSON rather than reading known property paths: the four expressions sit at
    // different depths (a queue's own Properties, a rule target's array element, an event source
    // mapping's DestinationConfig), and a fifth added later would otherwise be silently unseen.
    const visit = (node: any): void => {
        if (node === null || typeof node !== "object") return;
        if (Array.isArray(node)) {
            node.forEach(visit);
            return;
        }
        for (const [key, value] of Object.entries(node)) {
            if (FAILURE_DESTINATION_KEYS.test(key)) {
                // Fn::GetAtt [LogicalId, "Arn"] flattens to ${LogicalId.Arn}; a cross-stack reference
                // flattens to a parameter name carrying the same id.
                const flattened = SynthResult.flatten(value);
                for (const id of queueIds) {
                    if (flattened.includes(id)) targets.add(id);
                }
            }
            visit(value);
        }
    };
    for (const resource of synth.resources) visit(resource.raw);
    return targets;
}

/** Queues with no redrive policy that are neither a failure destination nor exempt, as `stack/id`. */
function queuesWithoutRedrive(synth: SynthResult): string[] {
    const dlqs = deadLetterTargets(synth);
    const offenders: string[] = [];
    for (const queue of synth.ofType("AWS::SQS::Queue")) {
        if (dlqs.has(queue.logicalId)) continue;
        if (queue.properties.RedrivePolicy !== undefined) continue;
        if (exemptionFor(queue.logicalId)) continue;
        offenders.push(`${queue.stack}/${queue.logicalId}`);
    }
    return offenders;
}

/**
 * Assert one named DLQ is recognized as a failure destination *despite* having no `RedrivePolicy`.
 *
 * Guards the exemption mechanism itself: `deadLetterTargets()` is what excuses a queue from needing a
 * redrive policy, so widening it is how this suite would be quietly defeated. The queues checked here
 * are named ONLY by an `AWS::Events::Rule` target's `DeadLetterConfig`, so a walk that regressed to
 * reading `RedrivePolicy` alone would surface them as offenders — while the opposite mistake, a walk so
 * broad it matches any reference to a queue, would fail nothing at all. Pinning a rule-owned DLQ by name
 * and asserting it carries no `RedrivePolicy` is what separates those two cases.
 */
function expectRuleOwnedDlqRecognized(synth: SynthResult, prefix: string): void {
    const dlqs = deadLetterTargets(synth);
    const matched = synth.ofType("AWS::SQS::Queue").filter((q) => q.logicalId.startsWith(prefix));
    expect(matched.length).toBeGreaterThan(0);
    for (const queue of matched) {
        expect(queue.properties.RedrivePolicy).toBeUndefined();
        expect(dlqs.has(queue.logicalId)).toBe(true);
    }
}

describe.each(TEMPLATES)("%s: SQS redrive policies", (templateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(templateName);
    });

    test("the assembly emits SQS queues at all", () => {
        // The control. Every assertion below is satisfied by a synth that emitted no queue.
        expect(synth.countOfType("AWS::SQS::Queue")).toBeGreaterThan(0);
    });

    test("a rule-owned DLQ is recognized without carrying a RedrivePolicy of its own", () => {
        expectRuleOwnedDlqRecognized(synth, "PipelineExecutionRegisterDLQ");
    });

    test("every queue that is not itself a dead-letter target has a redrive policy", () => {
        expect(queuesWithoutRedrive(synth)).toEqual([]);
    });

    test("every redrive policy names a finite maxReceiveCount", () => {
        // A redrive policy with no receive count is accepted by CloudFormation and dead-letters nothing.
        const policies = synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => q.properties.RedrivePolicy !== undefined);
        expect(policies.length).toBeGreaterThan(0);
        for (const queue of policies) {
            const count = queue.properties.RedrivePolicy.maxReceiveCount;
            expect(typeof count).toBe("number");
            expect(count).toBeGreaterThan(0);
        }
    });

    test("no queue is exempt", () => {
        // Asserted as a fact rather than left implicit. The sweep above is only as strong as this map
        // is small, and an empty map makes the loop in the next test iterate over nothing — so without
        // this assertion an exemption could be added and nothing would register the loss of coverage.
        expect(Object.keys(NO_REDRIVE_EXPECTED)).toEqual([]);
    });

    test("each exempt queue is still present, so the exemption is not covering a deleted queue", () => {
        // Vacuous while NO_REDRIVE_EXPECTED is empty, and kept for when it is not: an exemption entry
        // for a queue the assembly no longer emits is dead configuration that would silently excuse a
        // future queue whose logical id happens to share the prefix.
        const ids = synth.ofType("AWS::SQS::Queue").map((q) => q.logicalId);
        for (const prefix of Object.keys(NO_REDRIVE_EXPECTED)) {
            expect(ids.some((id) => id.startsWith(prefix))).toBe(true);
        }
    });

    test("the large-file upload queue specifically redrives to its own DLQ", () => {
        // Named explicitly because it was the last exemption, and because the sweep would still pass if
        // this queue were removed from the deployment altogether.
        const source = synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => q.logicalId.startsWith("LargeFileProcessingQueue"));
        expect(source.length).toBe(1);
        const redrive = source[0].properties.RedrivePolicy;
        expect(redrive).toBeDefined();
        expect(redrive.maxReceiveCount).toBe(3);
        // Its target must be a DLQ of its own, not one shared with an unrelated queue: a shared target
        // makes a failed upload indistinguishable from a failed index or bucket-sync record.
        const target = SynthResult.flatten(redrive.deadLetterTargetArn);
        const dlq = synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => q.logicalId.startsWith("LargeFileProcessingDLQ"));
        expect(dlq.length).toBe(1);
        expect(target).toContain(dlq[0].logicalId);
        // Outlives the 5-day source retention, so a failed upload cannot expire in the same window it
        // would have expired in unnoticed before.
        expect(dlq[0].properties.MessageRetentionPeriod).toBe(14 * 24 * 60 * 60);
        expect(dlq[0].properties.MessageRetentionPeriod).toBeGreaterThan(
            source[0].properties.MessageRetentionPeriod
        );
    });
});

/**
 * The Deadline Cloud execution type, which the shipped templates do NOT enable.
 *
 * `app.pipelines.deadlineCloudExecutionTypeEnabled` is `false` in all three, so an unmutated synth emits
 * no `DeadlineCloudJobCallbackDLQ` at all — measured, and worth stating because that queue is the second
 * of the two rule-owned DLQs the walk above has to recognize. A sweep phrased as "every queue in the
 * assembly" reads like whole-repo coverage while never loading the branch that builds it.
 *
 * The flag is the only mutation needed: `templateSynth` bypasses `getConfig()`, so the
 * commercial-partition restriction that validation would enforce does not apply here.
 */
function enableDeadlineCloud(c: any) {
    c.app.pipelines.deadlineCloudExecutionTypeEnabled = true;
}

describe("Deadline Cloud callback queues", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: enableDeadlineCloud,
            mutateKey: "deadline-cloud-enabled",
        });
    });

    test("the Deadline Cloud callback DLQ IS in this synth", () => {
        // The control. Every assertion below is satisfied by a synth that emitted no such queue, and the
        // shipped templates emit none.
        const matched = synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => q.logicalId.startsWith("DeadlineCloudJobCallbackDLQ"));
        expect(matched.length).toBeGreaterThan(0);
    });

    test("its rule-owned DLQ is recognized without carrying a RedrivePolicy of its own", () => {
        expectRuleOwnedDlqRecognized(synth, "DeadlineCloudJobCallbackDLQ");
    });

    test("enabling Deadline Cloud adds no queue that lacks a redrive policy", () => {
        expect(queuesWithoutRedrive(synth)).toEqual([]);
    });
});

describe("bucket-sync queues dead-letter, per bucket and per direction", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial");
    });

    /** Queues whose construct id carries the bucket-sync prefix, split by direction. */
    function bucketSync(direction: "Created" | "Deleted") {
        return synth
            .ofType("AWS::SQS::Queue")
            .filter((q) => q.logicalId.startsWith(`bucketSync${direction}`));
    }

    test("both directions emit a source queue and a DLQ", () => {
        // The control for everything below, and the one that would have caught the original defect: a
        // synth with no bucket-sync queue at all satisfies "every bucket-sync queue has a DLQ".
        for (const direction of ["Created", "Deleted"] as const) {
            const queues = bucketSync(direction);
            expect(queues.length).toBeGreaterThanOrEqual(2);
            expect(queues.filter((q) => q.logicalId.includes("DLQ")).length).toBeGreaterThan(0);
        }
    });

    test("every bucket-sync source queue redrives to a DLQ", () => {
        const dlqs = deadLetterTargets(synth);
        for (const direction of ["Created", "Deleted"] as const) {
            const sources = bucketSync(direction).filter((q) => !dlqs.has(q.logicalId));
            expect(sources.length).toBeGreaterThan(0);
            for (const queue of sources) {
                expect(queue.properties.RedrivePolicy).toBeDefined();
            }
        }
    });

    test("the created and deleted directions do NOT share a DLQ", () => {
        // Attribution is the point of the DLQ. A single shared queue would leave a poison record with no
        // indication of which bucket it arrived on or whether the object was written or removed — and
        // those two cases lead to opposite remediations.
        const target = (q: any) =>
            SynthResult.flatten(q.properties.RedrivePolicy?.deadLetterTargetArn);
        const dlqs = deadLetterTargets(synth);
        const created = bucketSync("Created")
            .filter((q) => !dlqs.has(q.logicalId))
            .map(target);
        const deleted = bucketSync("Deleted")
            .filter((q) => !dlqs.has(q.logicalId))
            .map(target);
        expect(created.length).toBeGreaterThan(0);
        expect(deleted.length).toBeGreaterThan(0);
        for (const c of created) expect(deleted).not.toContain(c);
    });

    test("each bucket-sync DLQ retains for 14 days", () => {
        // The source queues keep the SQS default of 4 days. A DLQ that expired on the same schedule
        // would discard the only copy of a failed record before anyone reviewed it, which is the
        // failure this whole fix exists to make visible.
        const dlqs = bucketSync("Created")
            .concat(bucketSync("Deleted"))
            .filter((q) => q.logicalId.includes("DLQ"));
        expect(dlqs.length).toBeGreaterThan(0);
        for (const queue of dlqs) {
            expect(queue.properties.MessageRetentionPeriod).toBe(14 * 24 * 60 * 60);
        }
    });

    test("each bucket-sync DLQ enforces TLS in transit", () => {
        // A DLQ holds the same S3 object keys and bucket names the source queue carried, so it is not
        // exempt from the transport rule the source queue follows.
        const dlqs = bucketSync("Created")
            .concat(bucketSync("Deleted"))
            .filter((q) => q.logicalId.includes("DLQ"));
        expect(dlqs.length).toBeGreaterThan(0);
        for (const queue of dlqs) {
            const policies = synth
                .ofType("AWS::SQS::QueuePolicy")
                .filter((p) => SynthResult.flatten(p.properties.Queues).includes(queue.logicalId));
            const statements = policies.flatMap((p) => {
                const raw = p.properties.PolicyDocument?.Statement;
                return Array.isArray(raw) ? raw : raw === undefined ? [] : [raw];
            });
            const denies = statements.some(
                (s: any) =>
                    s.Effect === "Deny" &&
                    /aws:SecureTransport/.test(JSON.stringify(s.Condition ?? {}))
            );
            expect(denies).toBe(true);
        }
    });
});

/**
 * The storage stack's AwsSolutions-SQS3 suppression covers its dead-letter queues and nothing else.
 *
 * AwsSolutions-SQS3 reports a queue that has no redrive policy. In the storage stack the queues without
 * one are the dead-letter queues, so the suppression belongs on them alone. A suppression applied to the
 * whole nested stack with `applyToChildren` also covers every DynamoDB table, bucket and topic there and
 * every source queue added later, so CDK Nag would accept a new storage queue with no dead-letter queue
 * without a finding.
 *
 * This tier runs no Nag rule (`templateSynth` sets `enableCdkNag = false`), so what is asserted is the
 * `cdk_nag.rules_to_suppress` metadata that `NagSuppressions` stamps onto each emitted resource, which
 * is the metadata the AwsSolutionsChecks aspect reads during a Nag-enabled synth.
 */

/** Every resource emitted into the storage nested stack's template. */
function storageStackResources(synth: SynthResult): Resource[] {
    return synth.resources.filter((r) => /StorageResourcesBuilder/.test(r.stack));
}

/** One entry of the `cdk_nag.rules_to_suppress` metadata `NagSuppressions` stamps on a resource. */
interface NagRuleSuppression {
    id: string;
    reason: string;
}

/** The AwsSolutions-SQS3 entries in one resource's cdk-nag suppression metadata. */
function sqs3Suppressions(resource: Resource): NagRuleSuppression[] {
    const rules: NagRuleSuppression[] = resource.raw.Metadata?.cdk_nag?.rules_to_suppress ?? [];
    return rules.filter((rule) => rule.id === "AwsSolutions-SQS3");
}

describe.each(TEMPLATES)("%s: storage stack SQS3 suppression scope", (templateName) => {
    let storage: Resource[];
    let storageDlqIds: Set<string>;

    beforeAll(() => {
        const synth = synthTemplate(templateName);
        storage = storageStackResources(synth);
        const failureDestinations = deadLetterTargets(synth);
        storageDlqIds = new Set(
            storage
                .filter((r) => r.type === "AWS::SQS::Queue")
                .filter((r) => failureDestinations.has(r.logicalId))
                .map((r) => r.logicalId)
        );
    });

    /**
     * True for a queue policy attached to one of the storage dead-letter queues. `applyToChildren` stamps
     * the suppression onto a queue's children too, and `enforceSSL` gives each queue a policy child.
     */
    const isDlqQueuePolicy = (r: Resource): boolean => {
        if (r.type !== "AWS::SQS::QueuePolicy") return false;
        const queues = SynthResult.flatten(r.properties.Queues);
        return Array.from(storageDlqIds).some((id) => queues.includes(`\${${id}}`));
    };

    test("the storage stack is located and emits dead-letter queues", () => {
        // The control. Every assertion below is satisfied by a synth whose storage stack was not found
        // or emitted no dead-letter queue. Both bucket-sync directions contribute one per bucket.
        expect(storage.length).toBeGreaterThan(0);
        expect(storageDlqIds.size).toBeGreaterThanOrEqual(2);
    });

    test("every storage dead-letter queue carries the AwsSolutions-SQS3 suppression", () => {
        // Keeps the dead-letter-queue-scoped convention of the search and Physna stacks. cdk-nag
        // already passes a queue that another queue redrives to, so this pins the documented
        // justification on the dead-letter queues for a cdk-nag release that detects them
        // differently. A change that dropped the suppression instead of narrowing it would also
        // satisfy the scope check below.
        const missing = storage
            .filter((r) => storageDlqIds.has(r.logicalId) && sqs3Suppressions(r).length === 0)
            .map((r) => r.logicalId);
        expect(missing).toEqual([]);
    });

    test("only dead-letter queues and their queue policies carry the suppression", () => {
        // A suppression over the whole stack stamps every table, bucket, topic and source queue here,
        // and so covers a storage queue added later with no redrive policy.
        const offenders = storage
            .filter((r) => sqs3Suppressions(r).length > 0)
            .filter((r) => !storageDlqIds.has(r.logicalId) && !isDlqQueuePolicy(r))
            .map((r) => `${r.type}/${r.logicalId}`);
        expect(offenders).toEqual([]);
    });

    test("every AwsSolutions-SQS3 reason in the storage stack states the dead-letter role", () => {
        const reasons = Array.from(
            new Set(storage.flatMap((r) => sqs3Suppressions(r).map((s) => s.reason)))
        );
        // Control: the filter below is vacuous if the stack carries no SQS3 reason at all.
        expect(reasons.length).toBeGreaterThan(0);
        // The suppressed queues are dead-letter queues, so a reason stating that the stack uses no
        // dead-letter queues contradicts the redrive policies beside it.
        const offenders = reasons.filter(
            (reason) => /not to use DLQs/i.test(reason) || !/dead-letter queue/i.test(reason)
        );
        expect(offenders).toEqual([]);
    });
});
