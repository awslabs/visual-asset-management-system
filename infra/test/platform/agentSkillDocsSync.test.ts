/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as fs from "fs";
import * as path from "path";

/**
 * Keeps the Claude Code slash commands and the documentation that describes them in step.
 *
 * Root `CLAUDE.md` Rule 12 requires a skill and the steering it restates to move together, and the
 * documentation site carries a user-facing table of the same commands. Nothing checked either
 * direction, so three drifts were silently possible: a new skill nobody documents, a documented
 * command that no longer exists, and — the one that prompted this file — an authoring guide pointing a
 * reader at a command by name with no guarantee the name is still right.
 *
 * The assertions are deliberately shallow. They compare NAMES and the presence of a reference, not
 * prose, because the descriptions are meant to be edited freely and a test that fails on a reworded
 * table row is a test that gets deleted.
 *
 * Note the ambiguity this file has to avoid: the documentation writes API routes in the same
 * backtick-slash form as slash commands (`/buckets`, `/assets`, `/search`). A regex for `` `/word` ``
 * therefore matches both, so a naive "every slash reference resolves to a command" assertion would
 * fail on every documented API route. Everything below is anchored on the command FILES or on an
 * explicit expected reference instead of on a free scan of the prose.
 */

const REPO = path.join(__dirname, "..", "..", "..");
const COMMANDS_DIR = path.join(REPO, ".claude", "commands");
const DOCS = path.join(REPO, "documentation", "docusaurus-site", "docs");
const AGENTIC_DOC = path.join(DOCS, "developer", "agentic-development.md");

/** Every shipped slash command, by name, derived from the directory rather than listed here. */
function shippedCommands(): string[] {
    return fs
        .readdirSync(COMMANDS_DIR)
        .filter((f) => f.endsWith(".md"))
        .map((f) => f.replace(/\.md$/, ""))
        .sort();
}

/** The command names the agentic-development table documents. */
function documentedCommands(): string[] {
    const md = fs.readFileSync(AGENTIC_DOC, "utf8");
    // Table rows reference the file path, which is unambiguous — unlike the `/name` form, which the
    // API documentation also uses for routes.
    const found = new Set<string>();
    for (const m of md.matchAll(/\.claude\/commands\/([a-z0-9-]+)\.md/g)) {
        found.add(m[1]);
    }
    return [...found].sort();
}

/** Pages that name a slash command as an aid, and the command each must still reference. */
const REFERENCING_PAGES: { page: string; mustReference: string[] }[] = [
    {
        page: path.join(DOCS, "pipelines", "custom-pipelines.md"),
        mustReference: ["add-pipeline", "add-api-endpoint"],
    },
    {
        page: path.join(DOCS, "pipelines", "migrating-pipelines-v25-to-v26.md"),
        mustReference: ["add-pipeline"],
    },
];

describe("Claude Code slash commands and their documentation stay in step", () => {
    test("commands are discoverable on disk", () => {
        // Control. Every assertion below compares against this list; if the directory moved, an empty
        // list would satisfy the set comparisons trivially and the referencing-page checks would be
        // asserting against nothing.
        const commands = shippedCommands();
        expect(commands.length).toBeGreaterThan(5);
        expect(commands).toContain("add-pipeline");
    });

    test("every shipped command is documented, and every documented command exists", () => {
        // Both directions in one assertion, because the two failures need different fixes and the
        // diff names which side is short: a new skill nobody documented, or a documented command that
        // was renamed or removed and has left a dangling reference.
        expect(documentedCommands()).toEqual(shippedCommands());
    });

    test.each(REFERENCING_PAGES)(
        "$page still references the commands it points readers at",
        ({ page, mustReference }) => {
            const md = fs.readFileSync(page, "utf8");
            const missing = mustReference.filter((cmd) => !md.includes("`/" + cmd + "`"));
            expect(missing).toEqual([]);
        }
    );

    test.each(REFERENCING_PAGES)("$page links to a heading that exists", ({ page }) => {
        // A relative link to a renamed heading is the quiet failure here: Docusaurus reports a broken
        // anchor only when the site is built, which these suites do not do.
        const md = fs.readFileSync(page, "utf8");
        const anchors = [
            ...md.matchAll(/\]\(\.\.\/developer\/agentic-development\.md#([a-z0-9-]+)\)/g),
        ].map((m) => m[1]);
        if (anchors.length === 0) {
            return; // the page links the doc without an anchor, which cannot break this way
        }
        const target = fs.readFileSync(AGENTIC_DOC, "utf8");
        const slugs = [...target.matchAll(/^#{2,4}\s+(.+?)\s*$/gm)].map((m) =>
            m[1]
                .toLowerCase()
                .replace(/[^\w\s-]/g, "")
                .trim()
                .replace(/\s+/g, "-")
        );
        expect(anchors.filter((a) => !slugs.includes(a))).toEqual([]);
    });
});
