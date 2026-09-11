/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Direction-correct bounds for the SQS event source mappings VAMS creates.
 *
 * ## Why a bound and not a pinned value
 *
 * The two indexer mappings and the workflow-trigger dispatch mapping were each asserted with
 * `BatchSize` pinned to 10 and `MaximumBatchingWindowInSeconds` pinned to 3. A pin fails a strictly
 * SAFER change — a smaller batch or a shorter window narrows what one bad record holds up — so it
 * trains the next author toward the literal rather than the property. Dropping the pins outright,
 * though, leaves NOTHING constraining the batch size in either direction, and the unsafe direction is
 * real. An upper bound is the assertion that passes every safer value and fails only the unsafe one.
 *
 * ## What breaks when the batch size grows
 *
 * Both mappings fail as a WHOLE BATCH in the case that matters:
 *
 *   - The indexer handlers report `batchItemFailures` per record, but any failure they cannot pin on
 *     one record reports EVERY record in the event (`all_batch_item_failures` in
 *     `backend/backend/handlers/indexing/{file,asset}Indexer.py`, used on every exception path), and a
 *     timeout returns no response at all, which is likewise a whole-batch failure. The dispatch mapping
 *     declares no `FunctionResponseTypes`, so every failure there is whole-batch by definition.
 *   - A redelivered batch advances the receive count of every record in it, so a bigger batch drags
 *     proportionally more healthy records toward the dead-letter queue behind one poison record — and,
 *     for the dispatch buffer, re-invokes `executeWorkflowV2` for records whose triggers already fired.
 *   - Per-invocation work scales with the batch, while the 900 s function timeout has to fit inside the
 *     queue's 960 s visibility timeout. A bigger batch is a bigger chance of being cut off mid-batch,
 *     which is the whole-batch failure above.
 *
 * Nothing else constrains it. CDK caps `batchSize` at 10 only while no batching window is set; all of
 * these mappings set `maxBatchingWindow`, which raises the cap to 10 000, so a four-digit batch size
 * synthesizes and deploys cleanly.
 *
 * ## Why the batching window gets no bound
 *
 * A shorter window is the safer direction and CDK already rejects anything over 300 s, so every
 * threshold that could be written here is one the harness itself guarantees — and a bound that no
 * emitted value could fail asserts nothing. It is left unasserted on purpose rather than by omission.
 */

/**
 * The SQS `ReceiveMessage` maximum, and the largest batch whose whole-batch failure path VAMS accepts.
 *
 * Ten records is one poison record plus at most nine healthy ones riding its receive count into the
 * dead-letter queue, and it is the cap the AWS SQS API itself imposes on a single receive call.
 */
export const MAX_SQS_BATCH_SIZE = 10;

/** One emitted `AWS::Lambda::EventSourceMapping`, labelled so a failure locates the call site. */
export interface BoundedMapping {
    /** Template/partition plus which mapping, e.g. `govcloud (provisioned + L1) fileIndexer`. */
    at: string;
    /** The mapping's emitted `Properties` object, NOT the construct props. */
    properties: Record<string, any>;
}

/**
 * The mappings whose emitted `BatchSize` breaks the upper bound, as printable strings.
 *
 * Returns a list rather than asserting so the caller can name every offender in one failure. Assert it
 * with `expect(batchSizeOffenders(...)).toEqual([])` and keep a non-emptiness control on the input —
 * an empty mapping list satisfies this vacuously.
 *
 * An ABSENT `BatchSize` is not an offender: Lambda's own default for an SQS source is 10, which is the
 * bound, so a mapping that stops setting the property has not moved in the unsafe direction. A present
 * but non-numeric value (a `Ref` or an `Fn::` token) IS reported — the bound cannot be checked against
 * a value resolved at deploy time, and silently skipping it would make the bound bypassable.
 */
export function batchSizeOffenders(mappings: BoundedMapping[]): string[] {
    return mappings.flatMap(({ at, properties }) => {
        const batchSize = properties.BatchSize;
        if (batchSize === undefined) return [];
        if (typeof batchSize !== "number")
            return [
                `${at}: BatchSize is ${JSON.stringify(batchSize)}, which is resolved at deploy ` +
                    `time; the <= ${MAX_SQS_BATCH_SIZE} bound cannot be checked against it`,
            ];
        if (batchSize > MAX_SQS_BATCH_SIZE)
            return [`${at}: BatchSize ${batchSize} exceeds ${MAX_SQS_BATCH_SIZE}`];
        return [];
    });
}
