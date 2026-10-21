/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The add-on documentation placement descriptors and the scanner that applies them to the core pages.
 *
 * An add-on whose pages live in a folder of their own under `docs/` describes, in
 * `infra/test/addon/<addon>/docs/placement.ts`, the identifiers that are its internals (route
 * prefixes, configuration keys, feature switches, object types, page labels, file names) and the
 * places outside that folder where a core page may name them: a registry that lists every add-on, or a
 * page a rule requires it on. Every other core page names no identifier of the add-on.
 */

import * as fs from "fs";
import * as path from "path";
import { DOCS, REPO_ROOT, docsPages, page, withoutFences } from "./docsText";

/** Where the descriptors live: one `docs/placement.ts` per add-on folder. */
export const ADDON_TEST_ROOT = "infra/test/addon";
export const DESCRIPTOR_FILE = "placement.ts";

/** A core page, or some of its sections, that may name the add-on's identifiers, and why. */
export interface AddonDocsAllowance {
    /** Page path under `docs/`. */
    page: string;
    /** The registry or the rule that puts the add-on on this page. */
    reason: string;
    /**
     * Heading lines (`## Current add-ons`) of the sections that may carry the identifiers, each to the
     * next heading of its level or above; the whole page when omitted.
     */
    sections?: string[];
}

export interface AddonDocsPlacement {
    /** The add-on's name, as its pages give it. */
    addon: string;
    /** The add-on's page folder under `docs/`. */
    pageDir: string;
    /** The add-on's internals, matched against each line. */
    identifiers: RegExp[];
    allowed: AddonDocsAllowance[];
}

/** One line of a core page that names an identifier, outside every allowed place. */
export interface PlacementHit {
    page: string;
    line: number;
    section: string[];
    identifier: string;
    text: string;
}

/** The descriptor files, as repository-relative paths. */
export function placementFiles(): string[] {
    const root = path.join(REPO_ROOT, ADDON_TEST_ROOT);
    return fs
        .readdirSync(root, { withFileTypes: true })
        .filter((e) => e.isDirectory())
        .map((e) => `${ADDON_TEST_ROOT}/${e.name}/docs/${DESCRIPTOR_FILE}`)
        .filter((rel) => fs.existsSync(path.join(REPO_ROOT, rel)));
}

/** Every descriptor, loaded from its named exports. */
export function loadPlacements(): AddonDocsPlacement[] {
    return placementFiles().map((rel) => {
        // eslint-disable-next-line @typescript-eslint/no-var-requires
        const m = require(path.join(REPO_ROOT, rel)) as Partial<AddonDocsPlacement>;
        if (
            typeof m.addon !== "string" ||
            typeof m.pageDir !== "string" ||
            !Array.isArray(m.identifiers) ||
            !Array.isArray(m.allowed)
        ) {
            throw new Error(`${rel} does not export addon, pageDir, identifiers and allowed`);
        }
        return {
            addon: m.addon,
            pageDir: m.pageDir,
            identifiers: m.identifiers,
            allowed: m.allowed,
        };
    });
}

/** Each line of a page with the heading it sits under, headings in fences not counted. */
export function linesWithSections(
    text: string
): Array<{ line: number; headings: string[]; text: string }> {
    const raw = text.split("\n");
    const markdown = withoutFences(text).split("\n");
    const stack: string[] = [];
    return raw.map((t, i) => {
        const m = /^(#{1,6})\s/.exec(markdown[i]);
        if (m) {
            stack.length = m[1].length - 1;
            stack[m[1].length - 1] = markdown[i];
        }
        return { line: i + 1, headings: stack.filter(Boolean), text: t };
    });
}

/** Whether a line under these headings falls inside one of the allowed sections. */
function inSections(headings: string[], sections: string[] | undefined): boolean {
    return !sections || sections.some((s) => headings.includes(s));
}

/** Whether an identifier matches a line; a global flag is dropped so the test keeps no state. */
export function matches(id: RegExp, text: string): boolean {
    return new RegExp(id.source, id.flags.replace("g", "")).test(text);
}

/** Whether a page path lies in the add-on's page folder. */
export function inPageDir(rel: string, pageDir: string): boolean {
    return rel === `${pageDir}.md` || rel === `${pageDir}.mdx` || rel.startsWith(`${pageDir}/`);
}

/**
 * The hits of a descriptor in a set of pages: every line of a page outside the add-on's folder that
 * matches an identifier and lies in no allowed place.
 */
export function placementHits(
    placement: AddonDocsPlacement,
    pages: Record<string, string>
): PlacementHit[] {
    const out: PlacementHit[] = [];
    for (const [rel, text] of Object.entries(pages)) {
        if (inPageDir(rel, placement.pageDir)) continue;
        const allowances = placement.allowed.filter((a) => a.page === rel);
        for (const l of linesWithSections(text)) {
            for (const id of placement.identifiers) {
                if (!matches(id, l.text)) continue;
                if (allowances.some((a) => inSections(l.headings, a.sections))) continue;
                out.push({
                    page: rel,
                    line: l.line,
                    section: l.headings,
                    identifier: String(id),
                    text: l.text.trim().slice(0, 160),
                });
            }
        }
    }
    return out;
}

/**
 * The allowances that cover no line: a page that is missing, a section the page lacks, or a page or
 * section where no identifier appears.
 */
export function staleAllowances(
    placement: AddonDocsPlacement,
    pages: Record<string, string>
): string[] {
    const out: string[] = [];
    for (const a of placement.allowed) {
        const text = pages[a.page];
        if (text === undefined) {
            out.push(`${a.page}: no such page`);
            continue;
        }
        const lines = linesWithSections(text);
        const heads = new Set(lines.flatMap((l) => l.headings));
        for (const s of a.sections ?? [""]) {
            if (s !== "" && !heads.has(s)) {
                out.push(`${a.page} ${s}: no such section`);
                continue;
            }
            const covered = lines.some(
                (l) =>
                    (s === "" || l.headings.includes(s)) &&
                    placement.identifiers.some((id) => matches(id, l.text))
            );
            if (!covered) out.push(`${a.page}${s ? ` ${s}` : ""}: names no identifier`);
        }
    }
    return out;
}

/** Every docs page by its path under `docs/`. */
export function allDocsPages(): Record<string, string> {
    return Object.fromEntries(docsPages().map((rel) => [rel, page(rel)]));
}

/** The pages of an add-on's folder. */
export function addonPages(pageDir: string): string[] {
    return fs.existsSync(path.join(DOCS, pageDir)) ? docsPages(pageDir) : [];
}

/**
 * The permission templates an add-on claims: each file in `documentation/permissionsTemplates/` whose
 * name one of its identifiers matches.
 */
export function addonClaimedTemplates(placements = loadPlacements()): Set<string> {
    const dir = path.join(REPO_ROOT, "documentation", "permissionsTemplates");
    const files = fs.readdirSync(dir).filter((f) => f.endsWith(".json"));
    return new Set(
        files.filter((f) => placements.some((p) => p.identifiers.some((id) => matches(id, f))))
    );
}
