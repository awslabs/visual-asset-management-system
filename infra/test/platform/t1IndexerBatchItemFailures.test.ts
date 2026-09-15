/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier: the file and asset indexer SQS event source mappings must report partial batch failures,
 * and each indexer source queue must dead-letter the records it keeps failing on.
 *
 * `fileIndexer.lambda_handler` and `assetIndexer.lambda_handler` return `batchItemFailures` for the
 * records they could not index. Lambda reads that field only when the event source mapping declares
 * `FunctionResponseTypes: ["ReportBatchItemFailures"]`; without it a 200 response deletes the WHOLE
 * batch, so the handler's per-record bookkeeping changes nothing and the unindexed files are dropped
 * silently. The property is visible in the synthesized template, which is why this is asserted here
 * rather than in a smoke test — the construct call existing is exactly what was not enough.
 *
 * The two properties are one contract, not two. Reporting per-record failures without a redrive
 * policy means a record the indexer can never process is redelivered for the whole message retention
 * window with no terminal state and nowhere for an operator to inspect it: the queue simply never
 * drains. So each source queue carries `RedrivePolicy` + `maxReceiveCount` pointing at its OWN
 * dead-letter queue, encrypted the same way the source queue is — a DLQ encrypted under a key the
 * indexer's role cannot use holds unreadable dead letter.
 *
 * `searchBuilder-nestedStack.ts` builds these mappings at EIGHT places — {file, asset} indexer x
 * {serverless, provisioned} OpenSearch x {govCloud L1, commercial `addEventSource()`} — and a shipped
 * template exercises only two of the four branch pairs:
 *
 *   commercial   -> serverless   + commercial `addEventSource()`
 *   govcloud     -> provisioned  + govCloud L1 + Tags deletion override
 *   eusovereign  -> provisioned  + govCloud L1 + Tags deletion override
 *
 * The other two pairs (provisioned + commercial, serverless + govCloud) are reachable only through a
 * hybrid config, so each gets one. An assertion limited to the shipped templates would leave four of
 * the eight call sites free to ship inert.
 *
 * Every negative assertion is paired with a positive control on the same template, because a template
 * that emitted no mapping at all satisfies all of them.
 */

import {
    Resource,
    SynthResult,
    TemplateName,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";
import { batchSizeOffenders } from "../support/sqsEventSourceBounds";

// A full-app synth is ~20-30 s and this file needs five of them (three shipped + two hybrids).
jest.setTimeout(900_000);

/** The two indexer queues, matched on the name `searchBuilder-nestedStack.ts` gives them. */
const INDEXER_QUEUE_SUFFIXES = ["fileIndexer", "assetIndexer"] as const;

const queueIndexer = (name: string): string | undefined =>
    INDEXER_QUEUE_SUFFIXES.find((suffix) => name.endsWith(`-${suffix}`));

const indexerQueues = (s: SynthResult) =>
    s.where(
        "AWS::SQS::Queue",
        (q) => queueIndexer(SynthResult.flatten(q.properties.QueueName)) !== undefined
    );

/**
 * Event source mappings whose EventSourceArn resolves to one of the two indexer queues.
 *
 * Matching through the queue rather than by logical id covers both construction paths: the govCloud
 * branch names the L1 itself, while `addEventSource()` derives a CDK-generated id from the function.
 */
const indexerMappings = (s: SynthResult) => {
    const queueIds = new Set(indexerQueues(s).map((q) => q.logicalId));
    return s.where("AWS::Lambda::EventSourceMapping", (m) =>
        Array.from(queueIds).some((id) =>
            SynthResult.flatten(m.properties.EventSourceArn).includes(id)
        )
    );
};

/**
 * The queue a source queue's `RedrivePolicy` points at, resolved back to the emitted resource.
 *
 * Resolved by flattening the reference and matching the LONGEST queue logical id it contains, so the
 * lookup works whether CDK renders the target as `Fn::GetAtt` (same template) or any other reference
 * form, and cannot mistake `...SqsQueue<hash>` for `...SqsDLQ<hash>`.
 */
const redriveTargetQueue = (s: SynthResult, queue: Resource): Resource | undefined => {
    const arn = SynthResult.flatten(queue.properties.RedrivePolicy?.deadLetterTargetArn);
    if (arn === "") return undefined;
    return s
        .ofType("AWS::SQS::Queue")
        .filter((q) => arn.includes(q.logicalId))
        .sort((a, b) => b.logicalId.length - a.logicalId.length)[0];
};

/**
 * The encryption-at-rest identity of a queue, as the template expresses it.
 *
 * Compared between a DLQ and its source rather than asserted against a literal: the shipped templates
 * disagree — commercial runs without a CMK (`SqsManagedSseEnabled`) while both restricted partitions
 * set `useKmsCmkEncryption`, so the queues carry `KmsMasterKeyId`. A mismatch between the two is a
 * failed `CreateQueue`/`SendMessage` or an unreadable dead letter, whichever the deployment hits first.
 */
const encryptionShape = (q: Resource) => ({
    KmsMasterKeyId: SynthResult.flatten(q.properties.KmsMasterKeyId),
    SqsManagedSseEnabled: q.properties.SqsManagedSseEnabled ?? null,
});

/** SQS's own default message retention, 4 days. A DLQ must not expire sooner than the source queue. */
const SQS_DEFAULT_RETENTION_SECONDS = 4 * 24 * 60 * 60;

/** Which indexer a mapping belongs to, resolved through its EventSourceArn -> queue name. */
const mappingIndexer = (s: SynthResult, mapping: Resource): string => {
    const arn = SynthResult.flatten(mapping.properties.EventSourceArn);
    const queue = indexerQueues(s).find((q) => arn.includes(q.logicalId));
    return queueIndexer(SynthResult.flatten(queue?.properties.QueueName)) ?? `unresolved:${arn}`;
};

/**
 * The Lambda a mapping invokes, described by BOTH its logical id and its handler.
 *
 * Two identifiers rather than one so the caller's "the fileIndexer queue reaches the FILE indexer"
 * check does not hang on either name alone: the logical id derives from the CDK construct id and the
 * handler from the backend module path. An unresolvable target returns a string naming the reference,
 * which no indexer name is contained in — so it fails the caller's check rather than passing silently.
 */
const mappingTarget = (s: SynthResult, mapping: Resource): string => {
    const fn = SynthResult.flatten(mapping.properties.FunctionName);
    const target = s
        .ofType("AWS::Lambda::Function")
        .filter((f) => fn !== "" && fn.includes(f.logicalId))
        .sort((a, b) => b.logicalId.length - a.logicalId.length)[0];
    return target
        ? `${target.logicalId} (${SynthResult.flatten(target.properties.Handler)})`
        : `unresolved target ${JSON.stringify(fn)}`;
};

interface IndexerCase {
    /** Test name fragment. */
    label: string;
    /** The searchBuilder branch pair this case exercises, named so a failure locates the call site. */
    branch: string;
    synth: () => SynthResult;
    /** Restricted partitions build the L1 and delete Tags; commercial goes through addEventSource(). */
    stripsTags: boolean;
}

/** commercial with OpenSearch switched to provisioned; a provisioned domain needs a VPC to live in. */
const provisionedCommercial = (): SynthResult =>
    synthTemplate("commercial", {
        mutateKey: "provisionedCommercial",
        mutate: (c: any) => {
            c.app.openSearch.useServerless.enabled = false;
            c.app.openSearch.useProvisioned.enabled = true;
            c.app.useGlobalVpc.enabled = true;
        },
    });

/** govcloud with OpenSearch switched to serverless, to reach the serverless + govCloud L1 pair. */
const serverlessGovcloud = (): SynthResult =>
    synthTemplate("govcloud", {
        mutateKey: "serverlessGovcloud",
        mutate: (c: any) => {
            c.app.openSearch.useServerless.enabled = true;
            c.app.openSearch.useProvisioned.enabled = false;
        },
    });

const shipped = (name: TemplateName) => () => synthTemplate(name);

const CASES: IndexerCase[] = [
    {
        label: "commercial",
        branch: "serverless + commercial addEventSource()",
        synth: shipped("commercial"),
        stripsTags: false,
    },
    {
        label: "govcloud",
        branch: "provisioned + govCloud L1",
        synth: shipped("govcloud"),
        stripsTags: true,
    },
    {
        label: "eusovereign",
        branch: "provisioned + govCloud L1",
        synth: shipped("eusovereign"),
        stripsTags: true,
    },
    {
        label: "commercial+provisioned hybrid",
        branch: "provisioned + commercial addEventSource()",
        synth: provisionedCommercial,
        stripsTags: false,
    },
    {
        label: "govcloud+serverless hybrid",
        branch: "serverless + govCloud L1",
        synth: serverlessGovcloud,
        stripsTags: true,
    },
];

const CASE_ARGS = CASES.map((c) => [c.label, c] as const);

describe("indexer SQS event source mappings report partial batch failures", () => {
    test.each(CASE_ARGS)(
        "%s: one mapping per indexer is emitted, each wired to its own queue",
        (label, c) => {
            // Positive control for every negative below: the queries find the resources at all, and
            // find one mapping per indexer rather than two pointing at the same queue.
            const s = c.synth();
            expect(
                indexerQueues(s)
                    .map((q) => queueIndexer(SynthResult.flatten(q.properties.QueueName)))
                    .sort()
            ).toEqual(["assetIndexer", "fileIndexer"]);

            const mappings = indexerMappings(s);
            expect(mappings.map((m) => mappingIndexer(s, m)).sort()).toEqual([
                "assetIndexer",
                "fileIndexer",
            ]);
            // Each mapping reaches ITS OWN indexer: the fileIndexer queue must be consumed by the
            // file indexer handler and not the asset one. Eight call sites build these mappings, so a
            // copy-paste swap is the live risk, and a swap leaves both queues draining with the wrong
            // handler while every count above still matches. The FunctionName used to be asserted as
            // `!== ""`, which no emitted mapping could fail.
            const crossWired = mappings
                .map((m) => ({ indexer: mappingIndexer(s, m), target: mappingTarget(s, m) }))
                .filter(({ indexer, target }) => !target.includes(indexer))
                .map(
                    ({ indexer, target }) => `${label} (${c.branch}) ${indexer} queue -> ${target}`
                );
            expect(crossWired).toEqual([]);

            // Batch size is bounded from ABOVE rather than pinned. A smaller batch is a strictly
            // safer change -- it narrows what one poison record holds up -- so an exact value would
            // fail the safe direction; but with no bound at all nothing anywhere constrains the
            // indexer batch size, and the unsafe direction is real: any failure the handlers cannot
            // attribute to one record reports the WHOLE batch, so a bigger batch dead-letters more
            // healthy records behind one poison one. support/sqsEventSourceBounds.ts carries the
            // reasoning, including why the batching window is deliberately left unasserted (CDK's
            // own 300 s cap already guarantees every threshold that could be written for it).
            expect(
                batchSizeOffenders(
                    mappings.map((m) => ({
                        at: `${label} (${c.branch}) ${mappingIndexer(s, m)}`,
                        properties: m.properties,
                    }))
                )
            ).toEqual([]);
        }
    );

    test.each(CASE_ARGS)(
        "%s: every indexer mapping declares FunctionResponseTypes ReportBatchItemFailures",
        (label, c) => {
            const s = c.synth();
            const mappings = indexerMappings(s);
            // Control: a template with no mappings would satisfy the loop below vacuously.
            expect(mappings.length).toBe(2);

            // Asserted on the EMITTED template rather than on the construct props: the failure mode
            // being guarded is a handler that reports per-record failures while the mapping never
            // asks for them, and only the template shows which of the two won.
            const missing = mappings
                .filter(
                    (m) =>
                        JSON.stringify(m.properties.FunctionResponseTypes) !==
                        JSON.stringify(["ReportBatchItemFailures"])
                )
                .map(
                    (m) =>
                        `${label} (${c.branch}) ${mappingIndexer(s, m)} -> ${JSON.stringify(
                            m.properties.FunctionResponseTypes
                        )}`
                );
            expect(missing).toEqual([]);
        }
    );

    test.each(CASE_ARGS)(
        "%s: adding FunctionResponseTypes leaves the partition Tags handling intact",
        (label, c) => {
            const s = c.synth();
            const mappings = indexerMappings(s);
            expect(mappings.length).toBe(2);

            if (!c.stripsTags) return;

            // GovCloud and EU Sovereign Lambda reject Tags on an event source mapping outright
            // ("Tags not supported in request"), which fails stack creation and rolls back the core
            // stack. Whichever way FunctionResponseTypes is added, the deletion override must survive.
            //
            // The control is the indexer QUEUES carrying Tags, not a commercial mapping carrying
            // them: it proves stack-tag propagation is switched on in this very template (so an
            // absence here is the override at work rather than tagging being off), while leaving a
            // future, stricter implementation free to strip Tags on the commercial branch too. That
            // CDK stamps tags onto an addEventSource() mapping is pinned in
            // eventSourceMappingGovCloudTags.test.ts.
            const taggedQueues = indexerQueues(s).filter((q) => "Tags" in q.properties);
            expectAbsent(
                `indexer EventSourceMapping with Tags in ${label}`,
                mappings.filter((m) => "Tags" in m.properties).map((m) => m.logicalId),
                {
                    description: `${label} propagates stack tags onto the indexer queues`,
                    count: taggedQueues.length,
                }
            );
        }
    );
});

interface RedrivePair {
    /** `fileIndexer` or `assetIndexer`, resolved from the source queue's name. */
    indexer: string;
    source: Resource;
    dlq: Resource;
}

/**
 * Each indexer source queue paired with the dead-letter queue its redrive policy resolves to.
 *
 * A source queue with no redrive policy, or one whose target is not an emitted queue, is simply
 * absent from the result — which is why every test using this asserts the pair count first.
 */
const redrivePairs = (s: SynthResult): RedrivePair[] =>
    indexerQueues(s).flatMap((source) => {
        const indexer = queueIndexer(SynthResult.flatten(source.properties.QueueName));
        const dlq = redriveTargetQueue(s, source);
        return indexer && dlq ? [{ indexer, source, dlq }] : [];
    });

describe("indexer SQS source queues dead-letter the records they cannot process", () => {
    test.each(CASE_ARGS)(
        "%s: each source queue redrives to its own dead-letter queue with a maxReceiveCount",
        (label, c) => {
            const s = c.synth();

            // Positive control for everything below: both source queues are emitted at all. A queue
            // that is not in the template satisfies a RedrivePolicy assertion vacuously.
            const sources = indexerQueues(s);
            expect(
                sources.map((q) => queueIndexer(SynthResult.flatten(q.properties.QueueName))).sort()
            ).toEqual(["assetIndexer", "fileIndexer"]);

            // Asserted on the EMITTED template rather than on the construct props: a `deadLetterQueue`
            // that never reaches `AWS::SQS::Queue.RedrivePolicy` leaves a permanently failing record
            // recycling for the whole retention window with no terminal state — the exact shape a
            // reportBatchItemFailures mapping turns from "deleted on 200" into "never drains".
            const offenders = sources.flatMap((source) => {
                const indexer = queueIndexer(SynthResult.flatten(source.properties.QueueName));
                const at = `${label} (${c.branch}) ${indexer}`;
                const policy = source.properties.RedrivePolicy;
                if (policy === undefined) return [`${at}: no RedrivePolicy`];
                if (redriveTargetQueue(s, source) === undefined)
                    return [
                        `${at}: deadLetterTargetArn "${SynthResult.flatten(
                            policy.deadLetterTargetArn
                        )}" resolves to no emitted queue`,
                    ];
                // Bounded from below only: a stricter deployment may allow more attempts before
                // giving up, and an absent or zero count is what leaves the record undeliverable.
                if (typeof policy.maxReceiveCount !== "number" || policy.maxReceiveCount < 1)
                    return [`${at}: maxReceiveCount=${JSON.stringify(policy.maxReceiveCount)}`];
                return [];
            });
            expect(offenders).toEqual([]);

            const pairs = redrivePairs(s);
            expect(pairs.map((p) => p.indexer).sort()).toEqual(["assetIndexer", "fileIndexer"]);

            // One DLQ per source queue. A shared DLQ mixes the file indexer's poison records in with
            // the asset indexer's, so neither can be redriven without replaying the other's.
            expect(new Set(pairs.map((p) => p.dlq.logicalId)).size).toBe(2);

            // ...and the target is a dead-letter queue rather than the sibling source queue. The
            // control is the two assertions above: both source ids and both targets are known here.
            const sourceIds = new Set(sources.map((q) => q.logicalId));
            expect(
                pairs.filter((p) => sourceIds.has(p.dlq.logicalId)).map((p) => p.indexer)
            ).toEqual([]);
        }
    );

    test.each(CASE_ARGS)(
        "%s: each dead-letter queue is encrypted exactly as its source queue is",
        (label, c) => {
            const s = c.synth();
            const pairs = redrivePairs(s);
            // Control: the pair lookup found both DLQs, so a comparison below is being made at all.
            expect(pairs.length).toBe(2);

            // Both restricted templates set useKmsCmkEncryption while commercial does not, so the
            // expected shape is taken from the SOURCE queue in the same template rather than pinned.
            const mismatched = pairs
                .filter(
                    ({ source, dlq }) =>
                        JSON.stringify(encryptionShape(dlq)) !==
                        JSON.stringify(encryptionShape(source))
                )
                .map(
                    ({ indexer, source, dlq }) =>
                        `${label} (${c.branch}) ${indexer}: source ${JSON.stringify(
                            encryptionShape(source)
                        )} vs dlq ${JSON.stringify(encryptionShape(dlq))}`
                );
            expect(mismatched).toEqual([]);

            // The encryption shape must also be non-empty, or two unencrypted queues would "match".
            for (const { source } of pairs) {
                expect(JSON.stringify(encryptionShape(source))).not.toEqual(
                    JSON.stringify({ KmsMasterKeyId: "", SqsManagedSseEnabled: null })
                );
            }
        }
    );

    test.each(CASE_ARGS)(
        "%s: each dead-letter queue retains a record at least as long as its source queue would",
        (label, c) => {
            const s = c.synth();
            const pairs = redrivePairs(s);
            expect(pairs.length).toBe(2);

            for (const { indexer, dlq } of pairs) {
                const retention =
                    dlq.properties.MessageRetentionPeriod ?? SQS_DEFAULT_RETENTION_SECONDS;
                // Lower bound, so a longer retention never fails this: a DLQ that expires sooner
                // than the source queue would have loses the poison record before anyone reads it.
                expect({
                    at: `${label} (${c.branch}) ${indexer} DLQ retention`,
                    ok: retention >= SQS_DEFAULT_RETENTION_SECONDS,
                }).toEqual({
                    at: `${label} (${c.branch}) ${indexer} DLQ retention`,
                    ok: true,
                });
            }
        }
    );

    test.each(CASE_ARGS)("%s: each dead-letter queue denies non-TLS access", (label, c) => {
        const s = c.synth();
        const pairs = redrivePairs(s);
        expect(pairs.length).toBe(2);

        const unprotected = pairs
            .filter(
                ({ dlq }) =>
                    !s
                        .where("AWS::SQS::QueuePolicy", (p) =>
                            SynthResult.flatten(p.properties.Queues).includes(dlq.logicalId)
                        )
                        .some((p) => {
                            const rendered = JSON.stringify(p.properties.PolicyDocument);
                            return (
                                rendered.includes("aws:SecureTransport") &&
                                rendered.includes("Deny")
                            );
                        })
            )
            .map(({ indexer }) => `${label} (${c.branch}) ${indexer} DLQ`);
        expect(unprotected).toEqual([]);
    });

    test.each(CASE_ARGS)(
        "%s: every queue in the search/indexing stack is covered by a redrive policy or an SQS3 suppression",
        (label, c) => {
            const s = c.synth();
            // `searchBuilder-nestedStack.ts` scopes its AwsSolutions-SQS3 suppression to the two DLQ
            // resources instead of the whole stack, which is what makes a source queue added later
            // without a redrive policy visible to CDK Nag. That only holds while every queue in the
            // stack falls into one of the two categories, so it is checked here rather than trusted.
            const stackQueues = s.where(
                "AWS::SQS::Queue",
                (q) => q.stack.includes("SearchBuilder") || q.stack.includes("searchBuilder")
            );

            const withRedrive = stackQueues.filter((q) => q.properties.RedrivePolicy !== undefined);
            const suppressed = stackQueues.filter((q) =>
                (q.raw.Metadata?.cdk_nag?.rules_to_suppress ?? []).some(
                    (r: any) => r.id === "AwsSolutions-SQS3"
                )
            );

            // Controls: both categories are non-empty, so neither half of the check is vacuous and
            // the stack was actually located. Counted, not pinned — a new queue of either kind passes.
            expect(withRedrive.length).toBeGreaterThan(0);
            expect(suppressed.length).toBeGreaterThan(0);

            const covered = new Set([...withRedrive, ...suppressed].map((q) => q.logicalId));
            expect(
                stackQueues
                    .filter((q) => !covered.has(q.logicalId))
                    .map((q) => `${label} (${c.branch}) ${q.stack}/${q.logicalId}`)
            ).toEqual([]);
        }
    );
});
