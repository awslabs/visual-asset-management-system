/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The documentation pages must be REACHABLE by `npm run prettier-check`, and the repo-root review
 * register must not be.
 *
 * `.prettierignore` carried a bare `docs` entry. A pattern with no path separator is not anchored, so
 * it matched a directory of that name at every depth — and therefore excluded all of
 * `documentation/docusaurus-site/docs`, not just the repo-root `docs/` register it was written for.
 * Every documentation page went unchecked for the life of that entry.
 *
 * What made it survive is that the failure is INVISIBLE in CI output: `prettier -c` prints
 * "All matched files use Prettier code style!" and exits 0 after matching **zero** files, which is
 * byte-identical to the message it prints after checking all 135 pages. There is no count in the
 * output to notice, so a green check said nothing about coverage. That is why this guard asserts
 * reachability directly, against prettier's own ignore resolution, rather than trusting the script's
 * exit code.
 *
 * This is a durable guard, not a temporary one (root CLAUDE.md Rule 13): a bare directory name is
 * re-writable at any time, and re-adding one would silently drop coverage again. The absence of a
 * bare `docs` entry is the guard working, not a spent assertion.
 */

import * as fs from "fs";
import * as path from "path";

/**
 * `prettier` ships no type declarations and the repo does not depend on `@types/prettier`, so the one
 * API this test uses is typed here rather than adding a dependency for a single call.
 */
interface PrettierFileInfo {
    getFileInfo(
        filePath: string,
        options: { ignorePath: string }
    ): Promise<{ ignored: boolean; inferredParser: string | null }>;
}
// eslint-disable-next-line @typescript-eslint/no-var-requires
const prettier: PrettierFileInfo = require("prettier");

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");
const IGNORE_PATH = path.join(REPO_ROOT, ".prettierignore");
const DOCS_ROOT = path.join(REPO_ROOT, "documentation", "docusaurus-site", "docs");
const REVIEW_ROOT = path.join(REPO_ROOT, "docs");

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

const isIgnored = async (file: string): Promise<boolean> =>
    (await prettier.getFileInfo(file, { ignorePath: IGNORE_PATH })).ignored;

/**
 * The shipped documentation set is 135 pages. The floor is deliberately far below that so ordinary
 * authoring does not touch this test, while a pattern that re-excludes the tree (which takes the
 * count to 0) fails immediately.
 */
const MIN_DOCS_PAGES = 100;

describe("prettier coverage of the documentation tree", () => {
    it("the documentation tree contains pages to check (count control)", () => {
        // Control for every assertion below: they all iterate this list, so an empty or shrunken one
        // would report success having checked nothing — the same failure mode as the defect itself.
        const pages = pagesUnder(DOCS_ROOT);
        expect(pages.length).toBeGreaterThanOrEqual(MIN_DOCS_PAGES);
    });

    it("EVERY documentation page is reachable by prettier", async () => {
        const pages = pagesUnder(DOCS_ROOT);
        const ignored: string[] = [];
        for (const page of pages) {
            if (await isIgnored(page)) ignored.push(path.relative(REPO_ROOT, page));
        }
        expect(ignored).toEqual([]);
    });

    it("the repo-root review register is still ignored", async () => {
        // The other direction. The bare `docs` entry existed for a reason — the root `docs/` tree is
        // gitignored local-only material (review registers, plans) that must not be format-checked.
        // Narrowing the pattern must not have voided that, or `prettier-check` starts failing on
        // files that never ship.
        //
        // Asserted on a directory that exists rather than a fixed filename, because the register's
        // contents are local and a named file would make this test pass or fail by accident.
        const reviewPages = pagesUnder(REVIEW_ROOT);
        if (reviewPages.length === 0) {
            // Nothing to assert on this machine — the register is gitignored, so a fresh clone has
            // none. Skipping is correct; failing would make the test environment-dependent.
            return;
        }
        const reachable: string[] = [];
        for (const page of reviewPages) {
            if (!(await isIgnored(page))) reachable.push(path.relative(REPO_ROOT, page));
        }
        expect(reachable).toEqual([]);
    });

    it("no .prettierignore entry is a bare directory name that would match at every depth", () => {
        // The defect's shape, forbidden directly. A pattern carrying no `/` is unanchored, so it
        // applies at every depth — which is correct for a tool directory that really does appear
        // anywhere, and wrong for one intended as a specific path.
        //
        // The allow-list is the set for which every-depth matching is the intent: build outputs and
        // dependency/cache directories genuinely occur at any depth in this repo. Anything else must
        // be anchored with a leading slash or carry a path separator.
        const EVERY_DEPTH_BY_DESIGN = new Set([
            "build",
            "node_modules",
            "cdk.out",
            "cdk.out.*",
            ".mypy_cache",
            ".pytest_cache",
            "ash_cf2cdk_output",
            "ash",
            ".venv",
            ".plans",
            "*.yaml",
            "*.patch",
        ]);

        const entries = fs
            .readFileSync(IGNORE_PATH, "utf-8")
            .split("\n")
            .map((l) => l.trim())
            .filter((l) => l && !l.startsWith("#"));

        // Control: the file must actually have been parsed.
        expect(entries.length).toBeGreaterThan(10);

        const unanchored = entries.filter((e) => !e.includes("/") && !EVERY_DEPTH_BY_DESIGN.has(e));
        expect(unanchored).toEqual([]);
    });
});
