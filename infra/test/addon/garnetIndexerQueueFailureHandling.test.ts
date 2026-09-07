/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The CDK half of the Garnet partial-batch-failure contract.
 *
 * A failed Garnet indexing operation used to be swallowed and reported as success, so the event-source
 * mapping deleted the SQS message and the entity was silently never indexed — the same defect
 * `S2-BACKEND-031` fixed in the core indexers.
 *
 * Two halves, each inert without the other:
 *   * `reportBatchItemFailures: true` on the event source tells the mapping to READ a
 *     `batchItemFailures` key. Without it, a handler returning that key is ignored.
 *   * the handler returning the key. Without it, the flag has nothing to read and every batch is a
 *     whole-batch success.
 *
 * `backend/tests/handlers/addon/garnetFramework/test_garnet_batch_item_failures.py` pins the handler
 * half and names THIS file for the CDK half. That reference was dangling until this file existed —
 * caught by review — which is worth stating because a cross-reference to a non-existent guard reads
 * exactly like coverage that is present.
 *
 * Asserted on the SOURCE rather than a synthesized template because the two branches are selected by
 * `config.app.govCloud.enabled`: one synth exercises one branch, so a template assertion would leave
 * the other unguarded. Three queues x two branches = six sites that must all carry the flag.
 */

import * as fs from "fs";
import * as path from "path";

const STACK = path.join(
    __dirname,
    "..",
    "..",
    "lib",
    "nestedStacks",
    "addon",
    "garnetFramework",
    "garnetFrameworkBuilder-nestedStack.ts"
);

const INDEXERS = ["Database", "Asset", "File"];

const source = fs.readFileSync(STACK, "utf-8");

describe("Garnet indexer queues report partial batch failures", () => {
    it("reads the stack it reasons about", () => {
        // Control: a moved file would make every assertion below pass over an empty string.
        expect(fs.existsSync(STACK)).toBe(true);
        expect(source.length).toBeGreaterThan(1000);
        // And that the queues are actually declared here, so the counts below mean something.
        for (const name of INDEXERS) {
            expect(source).toContain(`Garnet${name}IndexerSqsQueue`);
        }
    });

    it.each(INDEXERS)("the %s indexer queue has its own dead-letter queue", (name) => {
        expect(source).toContain(`Garnet${name}IndexerSqsDLQ`);
        expect(source).toContain(`const garnet${name}IndexerSqsDlq = new sqs.Queue(`);
    });

    it.each(INDEXERS)("the %s indexer queue redrives to ITS OWN dlq, not a sibling's", (name) => {
        // The failure this catches is a copy/paste crossing: three near-identical blocks where the
        // redrive names the wrong dlq. That still synthesizes and still has "a DLQ", so nothing else
        // would notice — one indexer's poison records would land in another's queue.
        const queueBlock = new RegExp(
            `const garnet${name}IndexerSqsQueue = new sqs\\.Queue\\([\\s\\S]*?\\n        \\}\\);`
        ).exec(source);
        expect(queueBlock).not.toBeNull();
        expect(queueBlock![0]).toContain(`queue: garnet${name}IndexerSqsDlq,`);
        expect(queueBlock![0]).toContain("maxReceiveCount: garnetIndexerQueueMaxReceiveCount,");
        for (const other of INDEXERS.filter((n) => n !== name)) {
            expect(queueBlock![0]).not.toContain(`garnet${other}IndexerSqsDlq`);
        }
    });

    it("every event source reports batch item failures, in BOTH partition branches", () => {
        // Six sites: three queues, each with a GovCloud EventSourceMapping branch and a standard
        // SqsEventSource branch. A count is used rather than per-site anchors because the two branch
        // shapes differ, and the property is "no site is missing it".
        const flags = source.match(/reportBatchItemFailures:\s*true/g) || [];
        expect(flags.length).toBe(INDEXERS.length * 2);
    });

    it("the GovCloud branches still delete the Tags property", () => {
        // Regression guard on the surrounding edit: an AWS::Lambda::EventSourceMapping carrying Tags
        // fails to deploy in a restricted partition, and the flag was inserted into those same blocks.
        const overrides = source.match(/addPropertyDeletionOverride\("Tags"\)/g) || [];
        expect(overrides.length).toBeGreaterThanOrEqual(INDEXERS.length);
    });

    it("the redrive count matches the core indexers'", () => {
        // Stated so the two families cannot silently diverge: a Garnet record that keeps failing should
        // reach a DLQ after the same number of attempts as a core one.
        expect(source).toMatch(/const garnetIndexerQueueMaxReceiveCount = 3;/);
        const core = fs.readFileSync(
            path.join(
                __dirname,
                "..",
                "..",
                "lib",
                "nestedStacks",
                "searchAndIndexing",
                "searchBuilder-nestedStack.ts"
            ),
            "utf-8"
        );
        expect(core).toMatch(/const indexerQueueMaxReceiveCount = 3;/);
    });

    it("each dlq encrypts the way its source queue does", () => {
        // A DLQ holds the same payloads as the queue it drains, so weaker encryption on it would be a
        // silent downgrade of exactly the records someone will later inspect.
        for (const name of INDEXERS) {
            const dlqBlock = new RegExp(
                `const garnet${name}IndexerSqsDlq = new sqs\\.Queue\\([\\s\\S]*?\\n        \\}\\);`
            ).exec(source);
            expect(dlqBlock).not.toBeNull();
            expect(dlqBlock![0]).toContain("QueueEncryption.KMS");
            expect(dlqBlock![0]).toContain(
                "encryptionMasterKey: props.storageResources.encryption.kmsKey"
            );
            expect(dlqBlock![0]).toContain("enforceSSL: true");
        }
    });
});
