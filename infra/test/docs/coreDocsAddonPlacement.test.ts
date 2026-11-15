/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Core docs carry no add-on internals. An add-on whose pages have a folder of their own describes its
 * internals and the core places that may name them in `infra/test/addon/<addon>/docs/placement.ts`;
 * every Markdown and MDX page outside that folder names an identifier only inside an allowed page or
 * section: a registry that lists every add-on, or a page a documentation rule requires it on. The
 * other core pages point to the add-ons generically, through the Add-ons Overview.
 *
 * Durable (root CLAUDE.md Rule 13): any core page can name an add-on's route, key, switch, label or
 * page again, and an allowance can outlive the text it allowed.
 */

import * as fs from "fs";
import * as path from "path";
import {
    AddonDocsPlacement,
    addonPages,
    allDocsPages,
    inPageDir,
    linesWithSections,
    loadPlacements,
    matches,
    placementFiles,
    placementHits,
    staleAllowances,
} from "./support/addonDocsPlacement";
import { DOCS } from "./support/docsText";

/** A descriptor for the controls, over synthetic pages only. */
const SYNTHETIC: AddonDocsPlacement = {
    addon: "Widgets",
    pageDir: "developer/widgets",
    identifiers: [/\buseWidgets\b/, /#\/widgets\//],
    allowed: [
        {
            page: "deployment/configuration-reference.md",
            sections: ["### Widgets (`app.addons.useWidgets`)"],
            reason: "the add-on's configuration section",
        },
        { page: "additional/revisions.md", reason: "release notes" },
    ],
};

const SYNTHETIC_PAGES: Record<string, string> = {
    "developer/widgets/overview.md":
        "# Widgets\n\nSet `app.addons.useWidgets` and open `#/widgets/`.\n",
    "deployment/configuration-reference.md": [
        "# Configuration",
        "",
        "## Add-ons",
        "",
        "### Widgets (`app.addons.useWidgets`)",
        "",
        "#### Fields",
        "",
        "| `app.addons.useWidgets.enabled` | boolean |",
        "",
        "```bash",
        "# a comment, not a heading",
        "```",
        "",
        "Open `#/widgets/` after the deploy.",
        "",
        "### Other add-on",
        "",
        "It is unrelated to `useWidgets`.",
    ].join("\n"),
    "additional/revisions.md": "# Revisions\n\n## 1.0\n\n-   Adds `useWidgets`.\n",
    "user-guide/web-interface.md": "# Web Interface\n\nThe add-on opens at `#/widgets/`.\n",
};

const keyed = (hits: ReturnType<typeof placementHits>): string[] =>
    hits.map((h) => `${h.page}:${h.line} ${h.identifier}`);

describe("core docs carry no add-on internals", () => {
    describe("the scanner (controls)", () => {
        test("a hit on a core page outside every allowed place is reported", () => {
            expect(keyed(placementHits(SYNTHETIC, SYNTHETIC_PAGES))).toEqual([
                "deployment/configuration-reference.md:19 /\\buseWidgets\\b/",
                "user-guide/web-interface.md:3 /#\\/widgets\\//",
            ]);
        });

        test("a hit inside an allowed section, its subsections, an allowed page or the add-on's folder is not reported", () => {
            const hits = keyed(placementHits(SYNTHETIC, SYNTHETIC_PAGES));
            expect(hits.filter((h) => h.startsWith("developer/widgets/"))).toEqual([]);
            expect(hits.filter((h) => h.startsWith("additional/revisions.md"))).toEqual([]);
            expect(hits).not.toContain(
                "deployment/configuration-reference.md:9 /\\buseWidgets\\b/"
            );
            expect(hits).not.toContain("deployment/configuration-reference.md:15 /#\\/widgets\\//");
        });

        test("a heading inside a code fence does not open a section", () => {
            const lines = linesWithSections(
                SYNTHETIC_PAGES["deployment/configuration-reference.md"]
            );
            expect(lines[14].headings).toEqual([
                "# Configuration",
                "## Add-ons",
                "### Widgets (`app.addons.useWidgets`)",
                "#### Fields",
            ]);
        });

        test("an allowance that covers no identifier, or names a missing page or section, is stale", () => {
            const placement: AddonDocsPlacement = {
                ...SYNTHETIC,
                allowed: [
                    ...SYNTHETIC.allowed,
                    { page: "user-guide/web-interface.md", sections: ["## Gone"], reason: "x" },
                    {
                        page: "deployment/configuration-reference.md",
                        sections: ["## Add-ons"],
                        reason: "x",
                    },
                    {
                        page: "deployment/configuration-reference.md",
                        sections: ["# Configuration"],
                        reason: "x",
                    },
                    { page: "overview/missing.md", reason: "x" },
                ],
            };
            const pages = {
                ...SYNTHETIC_PAGES,
                "deployment/configuration-reference.md": SYNTHETIC_PAGES[
                    "deployment/configuration-reference.md"
                ].replace("It is unrelated to `useWidgets`.", "It is unrelated."),
            };
            expect(staleAllowances(SYNTHETIC, SYNTHETIC_PAGES)).toEqual([]);
            expect(staleAllowances(placement, pages)).toEqual([
                "user-guide/web-interface.md ## Gone: no such section",
                "overview/missing.md: no such page",
            ]);
            const unused: AddonDocsPlacement = {
                ...SYNTHETIC,
                allowed: [
                    {
                        page: "user-guide/web-interface.md",
                        sections: ["# Web Interface"],
                        reason: "x",
                    },
                ],
            };
            expect(
                staleAllowances(unused, {
                    ...pages,
                    "user-guide/web-interface.md": "# Web Interface\n",
                })
            ).toEqual(["user-guide/web-interface.md # Web Interface: names no identifier"]);
        });

        test("the folder test takes the folder's pages and nothing beside it", () => {
            expect(inPageDir("developer/widgets/overview.md", "developer/widgets")).toBe(true);
            expect(inPageDir("developer/widgets.md", "developer/widgets")).toBe(true);
            expect(inPageDir("developer/widgets-legacy.md", "developer/widgets")).toBe(false);
        });
    });

    describe("the descriptors", () => {
        const placements = loadPlacements();

        test("every add-on folder's placement.ts is found and loaded (count control)", () => {
            expect(placementFiles().length).toBeGreaterThan(0);
            expect(placements.length).toBe(placementFiles().length);
        });

        test("each descriptor names an add-on folder of pages, identifiers and only core pages as allowed", () => {
            const problems: string[] = [];
            for (const p of placements) {
                if (addonPages(p.pageDir).length === 0)
                    problems.push(`${p.addon}: no pages in ${p.pageDir}`);
                if (p.identifiers.length === 0) problems.push(`${p.addon}: no identifiers`);
                for (const a of p.allowed) {
                    if (inPageDir(a.page, p.pageDir))
                        problems.push(`${p.addon}: ${a.page} is its own page`);
                    if (!fs.existsSync(path.join(DOCS, a.page))) {
                        problems.push(`${p.addon}: ${a.page} does not exist`);
                    }
                    if (a.reason.trim() === "")
                        problems.push(`${p.addon}: ${a.page} gives no reason`);
                }
            }
            expect(problems).toEqual([]);
        });

        test("every identifier matches a line of the docs tree, so none is dead", () => {
            const pages = allDocsPages();
            const lines = Object.values(pages).flatMap((t) => t.split("\n"));
            const dead = placements.flatMap((p) =>
                p.identifiers
                    .filter((id) => !lines.some((l) => matches(id, l)))
                    .map((id) => `${p.addon}: ${String(id)}`)
            );
            expect(dead).toEqual([]);
        });

        test("every allowance covers a line that names an identifier", () => {
            const pages = allDocsPages();
            expect(
                placements.flatMap((p) => staleAllowances(p, pages).map((s) => `${p.addon}: ${s}`))
            ).toEqual([]);
        });
    });

    test("no core page names an add-on identifier outside its allowed places", () => {
        const pages = allDocsPages();
        const hits = loadPlacements().flatMap((p) =>
            placementHits(p, pages).map(
                (h) =>
                    `${p.addon}: ${h.page}:${h.line} [${h.section.slice(-1)[0] ?? ""}] ${
                        h.identifier
                    } :: ${h.text}`
            )
        );
        expect(hits).toEqual([]);
    });
});
