/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * No documentation page may carry a backslash-escaped brace (`\{` or `\}`) INSIDE a code span or a
 * fenced code block.
 *
 * `documentation/CLAUDE.md` asks authors to write `\{variable\}` in prose because MDX would
 * otherwise parse `{variable}` as a JSX expression and fail the build. That rule is scoped to
 * prose. Inside a code span or fence MDX does not evaluate braces, and CommonMark does not process
 * backslash escapes there either, so the backslash reaches the reader verbatim: a documented
 * command such as `vamscli --profile \{profile-name\} ...` is wrong in the reader's clipboard, and
 * a placeholder such as `\{databaseId\}` reads as required literal syntax.
 *
 * The scan is structural rather than a whole-file grep. Fence state is tracked line by line, and
 * on a non-fence line only the backtick-delimited regions are inspected, so the escapes MDX
 * genuinely needs in prose are neither counted nor disturbed. The scanner's discrimination is
 * asserted with its own positive and negative controls before it is applied to the tree.
 *
 * This is a durable guard, not a temporary one (root CLAUDE.md Rule 13): the escape is re-writable
 * on any page at any time, and the docs build passes with it in place, so nothing else catches it.
 */

import * as fs from "fs";
import * as path from "path";

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const DOCS_ROOT = path.join(REPO_ROOT, "documentation", "docusaurus-site", "docs");

/** Every Markdown/MDX page under a tree. */
function pagesUnder(root: string): string[] {
    if (!fs.existsSync(root)) return [];
    const out: string[] = [];
    const walk = (dir: string) => {
        for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isDirectory()) walk(full);
            else if (/\.mdx?$/.test(entry.name)) out.push(full);
        }
    };
    walk(root);
    return out;
}

const FENCE = /^\s*(`{3,}|~{3,})/;
/** Inline code spans: a run of backticks, the shortest body, then the same run. */
const SPAN = /(`+)(.+?)\1/g;
const ESCAPED_BRACE = /\\[{}]/g;

interface Hit {
    line: number;
    count: number;
    where: "fence" | "span";
}

const countEscapes = (text: string): number => (text.match(ESCAPED_BRACE) ?? []).length;

/**
 * Escaped braces that sit inside code, per line. Prose is never inspected: a fenced body is
 * inspected whole, and an unfenced line only within its backtick-delimited spans.
 */
function escapedBracesInCode(source: string): Hit[] {
    const hits: Hit[] = [];
    let inFence = false;
    let fenceMarker = "";
    source.split("\n").forEach((line, index) => {
        const lineNo = index + 1;
        const fence = FENCE.exec(line);
        if (fence) {
            const marker = fence[1];
            if (!inFence) {
                inFence = true;
                fenceMarker = marker;
            } else if (marker[0] === fenceMarker[0] && marker.length >= fenceMarker.length) {
                // Closes only on a run of the same character at least as long as the opener.
                inFence = false;
                fenceMarker = "";
            }
            return;
        }
        if (inFence) {
            const count = countEscapes(line);
            if (count) hits.push({ line: lineNo, count, where: "fence" });
            return;
        }
        for (const span of line.matchAll(SPAN)) {
            const count = countEscapes(span[2]);
            if (count) hits.push({ line: lineNo, count, where: "span" });
        }
    });
    return hits;
}

/**
 * The shipped documentation set is 135 pages. The floor is deliberately far below that so ordinary
 * authoring does not touch this test, while a wrong root (which takes the count to 0) fails
 * immediately instead of passing vacuously.
 */
const MIN_DOCS_PAGES = 100;

describe("backslash-escaped braces inside documentation code spans", () => {
    it("the documentation tree contains pages to scan (count control)", () => {
        expect(pagesUnder(DOCS_ROOT).length).toBeGreaterThanOrEqual(MIN_DOCS_PAGES);
    });

    it("the scanner detects an escape inside an inline code span (positive control)", () => {
        const doc = "Run `vamscli --profile \\{profile-name\\} user list` to list users.";
        expect(escapedBracesInCode(doc)).toEqual([{ line: 1, count: 2, where: "span" }]);
    });

    it("the scanner detects an escape inside a fenced block (positive control)", () => {
        const doc = [
            "Example:",
            "```bash",
            "vamscli auth login -u \\{your-username\\}",
            "```",
        ].join("\n");
        expect(escapedBracesInCode(doc)).toEqual([{ line: 3, count: 2, where: "fence" }]);
    });

    it("the scanner ignores the escapes MDX needs in prose (negative control)", () => {
        // The same placeholder written in prose, where the escape is REQUIRED, followed by a code
        // span that carries none. Flagging this line would make the guard fight the MDX rule.
        const doc = "The asset URN is \\{databaseId\\}:\\{assetId\\}; see `urn:vams:asset`.";
        expect(escapedBracesInCode(doc)).toEqual([]);
    });

    it("a fence closes only on a matching marker, so a shorter run inside it is content", () => {
        // A four-backtick fence wrapping a three-backtick line: the inner run must not end the
        // fence, or everything after it would be scanned as prose and an escape there would go
        // unreported.
        const doc = ["````md", "```", "\\{inner\\}", "````", "prose \\{kept\\}"].join("\n");
        expect(escapedBracesInCode(doc)).toEqual([{ line: 3, count: 2, where: "fence" }]);
    });

    it("EVERY documentation page is free of escaped braces inside code", () => {
        const offenders: string[] = [];
        for (const page of pagesUnder(DOCS_ROOT)) {
            const source = fs.readFileSync(page, "utf-8");
            for (const hit of escapedBracesInCode(source)) {
                offenders.push(
                    `${path.relative(REPO_ROOT, page).split(path.sep).join("/")}:${hit.line} ` +
                        `(${hit.where}, ${hit.count} escaped brace${hit.count === 1 ? "" : "s"})`
                );
            }
        }
        expect(offenders).toEqual([]);
    });
});
