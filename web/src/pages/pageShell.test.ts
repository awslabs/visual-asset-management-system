/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every top-level page sets its own outer padding.
 *
 * The app shell (`AppLayout` in src/routes.tsx) runs with `disableContentPaddings` while the side
 * navigation is open, so a page whose root is a bare table or container sits flush against the
 * navigation and the header. Each page therefore opens with one of the two page shells
 * (web/CLAUDE.md §7.3):
 *
 * - Cloudscape pages: `<Box padding={{ top: "m", horizontal: "l" }}>` around the page's content
 *   (reference: src/pages/ListPageNoDatabase.tsx, which Roles, Constraints and Tags render). Pages
 *   that open with a breadcrumb use a smaller top (`"s"` or `"xs"`); the sides are always `"l"`.
 * - Tailwind (orchestration and add-on) pages: the `orchestration-page` class on the root element
 *   (src/styles/tailwind.css; reference: features/orchestration/pipelines/PipelinesPage.tsx).
 *
 * The check reads each routed page module and the local modules it imports directly (a page
 * file that only forwards to a feature component, such as PipelinesPage2, gets its shell there).
 */

import * as fs from "fs";
import * as path from "path";

const SRC = path.resolve(__dirname, "..");

/** Modules routed in `routeTable`: the core table and the add-on lane. */
const ROUTE_FILES = ["routes.tsx", "addons/addonRoutes.tsx", "custom/customRoutes.tsx"];

/** The page shells: a Cloudscape page `Box` with the standard sides, or the orchestration class. */
const SHELL_PATTERNS = [
    /<Box\s+padding=\{\{\s*top:[^}]*horizontal:\s*"l"/,
    /\borchestration-page\b/,
];

/**
 * Pages with a layout of their own, each with the reason. A new entry needs a reason a reviewer
 * accepts; a list page never belongs here.
 */
const EXEMPT: Record<string, string> = {
    "pages/LandingPage": "full-width landing layout with its own hero and section spacing",
    "pages/WorkflowTriggersPage":
        "Tailwind root with its own px-6 pt-4 padding, written before the orchestration-page class",
};

const resolveModule = (fromFile: string, spec: string): string | null => {
    const base = path.resolve(path.dirname(fromFile), spec);
    for (const candidate of [
        `${base}.tsx`,
        `${base}.ts`,
        path.join(base, "index.tsx"),
        path.join(base, "index.ts"),
    ]) {
        if (fs.existsSync(candidate)) return candidate;
    }
    return null;
};

/** Relative imports of a source file, resolved to files under src/. */
const localImports = (file: string, source: string): string[] =>
    Array.from(source.matchAll(/(?:import|from)\s*\(?\s*["'](\.{1,2}\/[^"']+)["']/g))
        .map((m) => resolveModule(file, m[1]))
        .filter((f): f is string => !!f);

/** Page modules a route file names: its lazy imports and its default page imports. */
const routedPages = (): string[] => {
    const pages = new Set<string>();
    for (const rel of ROUTE_FILES) {
        const file = path.join(SRC, rel);
        if (!fs.existsSync(file)) continue;
        const source = fs.readFileSync(file, "utf8");
        for (const m of Array.from(
            source.matchAll(/lazyWithRetry\(\s*\(\)\s*=>\s*import\(\s*["']([^"']+)["']/g)
        )) {
            const resolved = resolveModule(file, m[1]);
            if (resolved) pages.add(resolved);
        }
        for (const m of Array.from(
            source.matchAll(/import\s+\w+\s+from\s+["'](\.\/pages\/[^"']+)["']/g)
        )) {
            const resolved = resolveModule(file, m[1]);
            if (resolved) pages.add(resolved);
        }
    }
    return Array.from(pages).sort();
};

const hasShell = (file: string): boolean => {
    const source = fs.readFileSync(file, "utf8");
    const sources = [source, ...localImports(file, source).map((f) => fs.readFileSync(f, "utf8"))];
    return sources.some((s) => SHELL_PATTERNS.some((p) => p.test(s)));
};

const key = (file: string) => path.relative(SRC, file).replace(/\.(tsx|ts)$/, "");

describe("top-level page shell", () => {
    const pages = routedPages();

    it("finds the routed pages", () => {
        // Control: the parser must see the route table, or every check below passes vacuously.
        expect(pages.length).toBeGreaterThan(20);
        expect(pages.map(key)).toEqual(
            expect.arrayContaining(["pages/Databases", "pages/ZipDownloadsPage"])
        );
    });

    it("recognises both page shells", () => {
        expect(hasShell(path.join(SRC, "pages/ListPageNoDatabase.tsx"))).toBe(true);
        expect(hasShell(path.join(SRC, "pages/PipelinesPage2.tsx"))).toBe(true);
    });

    it.each(pages.map((f) => [key(f), f]))("%s opens with a page shell", (name, file) => {
        if (EXEMPT[name]) return;
        expect({ page: name, hasShell: hasShell(file) }).toEqual({ page: name, hasShell: true });
    });
});
