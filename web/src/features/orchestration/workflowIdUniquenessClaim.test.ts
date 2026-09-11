/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The frontend must not restate that a workflow or pipeline ID is database-scoped.
 *
 * Create rejects an ID any other database already owns (workflowService.find_workflow_id_owner,
 * pipelineService's equivalent), so an ID identifies the entity on its own and a workflowDatabaseId
 * is an extra narrowing filter, not a disambiguator. The consequence of believing otherwise is
 * silent: the execution list matches workflowId and workflowDatabaseId independently, so a database
 * that is not the workflow's own filters the page to zero rows and reports "never ran" with a 200.
 *
 * Several orchestration comments stated the opposite rule while the code around them was correct,
 * which is how the next edit gets steered back to it. This is a durable guard: the wrong rule is a
 * sentence anyone can write again, so its absence here is the guard working rather than the test
 * expiring.
 *
 * The claim is legitimate for ASSET ids, which really are unique only within a database, so the scan
 * flags an occurrence only when the surrounding text is talking about a workflow or pipeline id.
 */

import * as fs from "fs";
import * as path from "path";

const WEB_ROOT = path.resolve(__dirname, "..", "..", "..");
const SCANNED_ROOTS = [path.join(WEB_ROOT, "src"), path.join(WEB_ROOT, "e2e")];
const SCANNED_EXTENSIONS = [".ts", ".tsx"];

const DATABASE_SCOPED_CLAIM = /unique only within/i;
const WORKFLOW_OR_PIPELINE_ID = /(workflow|pipeline)\s*(id|Id|ID|DatabaseId)/;
/** How much text either side of the claim counts as "what it is talking about". */
const CONTEXT_WINDOW = 240;

const CORRECTED_RULE = "unique across every database";

function sourceFiles(root: string): string[] {
    if (!fs.existsSync(root)) return [];
    const found: string[] = [];
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
        const full = path.join(root, entry.name);
        if (entry.isDirectory()) {
            if (entry.name === "node_modules" || entry.name === "dist") continue;
            found.push(...sourceFiles(full));
        } else if (SCANNED_EXTENSIONS.includes(path.extname(entry.name))) {
            // This file quotes the claim to define and to prove the detector, so it is not a site.
            if (full !== __filename) found.push(full);
        }
    }
    return found;
}

/** Every place `text` claims a WORKFLOW or PIPELINE id is database-scoped, as line numbers. */
export function databaseScopedIdClaims(text: string): number[] {
    const offenders: number[] = [];
    const claim = new RegExp(DATABASE_SCOPED_CLAIM.source, "gi");
    let match: RegExpExecArray | null;
    while ((match = claim.exec(text)) !== null) {
        const window = text.slice(
            Math.max(0, match.index - CONTEXT_WINDOW),
            match.index + CONTEXT_WINDOW
        );
        if (WORKFLOW_OR_PIPELINE_ID.test(window)) {
            offenders.push(text.slice(0, match.index).split("\n").length);
        }
    }
    return offenders;
}

describe("the workflow-id scoping rule the frontend states", () => {
    const files = SCANNED_ROOTS.flatMap(sourceFiles);

    it("detects the claim, and does not fire on the asset-id statement that is true", () => {
        // POSITIVE CONTROL for the scan below: an empty-offenders assertion is also satisfied by a
        // detector that never matches anything.
        expect(
            databaseScopedIdClaims(
                "// a workflowId is unique only within its database, so pass both"
            )
        ).toEqual([1]);
        expect(databaseScopedIdClaims("// an asset id is unique only within its database")).toEqual(
            []
        );
    });

    it("reads the real web sources", () => {
        // POSITIVE CONTROL for the corpus: the scan below is satisfied by reading nothing.
        expect(files.length).toBeGreaterThan(100);
        expect(files.some((f) => f.endsWith("ExecutionsBoard.tsx"))).toBe(true);
        expect(files.some((f) => f.includes("orchestration.executions.spec.ts"))).toBe(true);
    });

    it("states the rule the backend enforces somewhere in the orchestration sources", () => {
        // POSITIVE CONTROL for the wording: proves the corrected rule is what is written, not just
        // that the wrong one was deleted.
        const stating = files.filter((f) => fs.readFileSync(f, "utf8").includes(CORRECTED_RULE));
        expect(stating.length).toBeGreaterThan(0);
    });

    it("never claims a workflow or pipeline id is unique only within its database", () => {
        const offenders = files.flatMap((file) =>
            databaseScopedIdClaims(fs.readFileSync(file, "utf8")).map(
                (line) => `${path.relative(WEB_ROOT, file)}:${line}`
            )
        );
        expect(offenders).toEqual([]);
    });
});
