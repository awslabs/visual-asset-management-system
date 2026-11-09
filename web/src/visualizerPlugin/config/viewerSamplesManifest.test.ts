/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

/**
 * The viewer sample manifest (web/e2e/fixtures/viewer-samples.json) is the shared input of every
 * viewer render case and of tools/VamsCLI/examples/seed_viewer_samples.py. An entry the seeder
 * cannot produce, or one naming a viewer that no longer exists, would only fail on a sandbox run;
 * this checks the schema in CI. Durable guard (root CLAUDE.md Rule 13).
 */

import fs from "fs";
import path from "path";
import viewerConfig from "./viewerConfig.json";
import addonViewerConfig from "./addonViewerConfig.json";
import { extensionToken, WILDCARD_EXTENSION } from "../core/extensionMatching";

const WEB_ROOT = path.resolve(__dirname, "..", "..", "..");
const manifest = JSON.parse(
    fs.readFileSync(path.join(WEB_ROOT, "e2e", "fixtures", "viewer-samples.json"), "utf8")
);
const seeder = fs.readFileSync(
    path.join(WEB_ROOT, "..", "tools", "VamsCLI", "examples", "seed_viewer_samples.py"),
    "utf8"
);
const samples: any[] = manifest.samples;
const allViewers: any[] = [...(viewerConfig as any).viewers, ...(addonViewerConfig as any).viewers];
const viewerIds = new Set(allViewers.map((v: any) => v.id));
/** `viewer` of a sample no viewer may open: the subject of a `not-offered` case. */
const NO_VIEWER = "none";
/** The text viewers, whose samples are matched by file name alone (extensionless files, #1198). */
const TEXT_VIEWERS = new Set(["text-viewer", "text-diff-viewer"]);

/** The keys of a `NAME = {` / `NAME: ... = {` dict literal in the seeder. */
function seederKeys(name: string): string[] {
    const start = seeder.search(new RegExp(`^${name}\\b[^=]*= \\{`, "m"));
    expect(start).toBeGreaterThanOrEqual(0);
    const body = seeder.slice(start, seeder.indexOf("\n}", start));
    return [...body.matchAll(/^\s+"([a-z0-9-]+)":/gm)].map((m) => m[1]);
}
const TRANSFORMS = seederKeys("TRANSFORMS");
const GENERATORS = seederKeys("GENERATORS");
const sourcesOf = (s: any): string[] =>
    s.derivedFrom === undefined ? [] : [].concat(s.derivedFrom);

describe("viewer sample manifest", () => {
    it("is schema version 1 with samples", () => {
        expect(manifest.schemaVersion).toBe(1);
        expect(samples.length).toBeGreaterThan(0);
    });

    it("[control] reads the seeder's transforms and generators", () => {
        expect(TRANSFORMS).toEqual(
            expect.arrayContaining(["prepend-utf8-bom", "zip", "splat-transform", "las-to-ply"])
        );
        expect(GENERATORS).toEqual(expect.arrayContaining(["off-cube", "text"]));
    });

    it("has unique ids and unique asset paths", () => {
        const ids = samples.map((s) => s.id);
        const paths = samples.map((s) => s.path);
        expect(new Set(ids).size).toBe(ids.length);
        expect(new Set(paths).size).toBe(paths.length);
    });

    it.each(samples.map((s) => [s.id, s]))("%s can be produced as written", (_id, s) => {
        for (const key of ["id", "viewer", "path", "licence", "source"]) {
            expect(s[key]).toBeTruthy();
        }
        expect(s.path.startsWith("/")).toBe(true);
        expect(s.viewer === NO_VIEWER || viewerIds.has(s.viewer)).toBe(true);
        const kinds = ["url", "generated", "derivedFrom"].filter((k) => k in s);
        expect(kinds).toHaveLength(1);
        if (s.url) {
            expect(s.url.startsWith("https://")).toBe(true);
            expect(Number.isInteger(s.size)).toBe(true);
            expect(s.sha256).toMatch(/^[0-9a-f]{64}$/);
        }
        if (s.generated) expect(GENERATORS).toContain(s.generated);
        if (s.derivedFrom !== undefined) {
            expect(TRANSFORMS).toContain(s.transform);
            const sources = sourcesOf(s);
            expect(sources.length).toBeGreaterThan(0);
            sources.forEach((src) => expect(samples.map((x) => x.id)).toContain(src));
            if (s.transform !== "zip") expect(sources).toHaveLength(1);
            if (s.transform === "las-to-ply") {
                // The seeder reads uncompressed LAS only (LAZ needs a native decompressor).
                const source = samples.find((x) => x.id === sources[0]);
                expect(source.path.toLowerCase().endsWith(".las")).toBe(true);
                expect(s.path.toLowerCase().endsWith(".ply")).toBe(true);
            }
            if (s.transform === "splat-transform") expect(s.version).toMatch(/^\d+\.\d+\.\d+$/);
        }
        if (s.expect?.minForegroundFraction !== undefined) {
            // A per-sample render threshold lowers the default 1%; it never turns the check off.
            expect(s.expect.minForegroundFraction).toBeGreaterThan(0);
            expect(s.expect.minForegroundFraction).toBeLessThanOrEqual(1);
        }
        if (s.expect?.minDistinctColours !== undefined) {
            expect(Number.isInteger(s.expect.minDistinctColours)).toBe(true);
            expect(s.expect.minDistinctColours).toBeGreaterThanOrEqual(2);
        }
        if ("requiresFeature" in s) {
            const required = [].concat(s.requiresFeature);
            required.forEach((f) => expect(typeof f).toBe("string"));
        }
    });
});

describe("viewer sample file names (#1198)", () => {
    // A text sample is matched by its name through the one filename rule (`extensionToken`), not
    // `path.extname`: `.gitignore` has no extension (token "(none)"), `.env.example` has ".example".
    const tokenOf = (s: any): string => extensionToken(s.path) as string;

    it("[control] reads dotfiles and extensionless names as the web app does", () => {
        expect(tokenOf({ path: "/noext/.gitignore" })).toBe("(none)");
        expect(tokenOf({ path: "/noext/README" })).toBe("(none)");
        expect(tokenOf({ path: "/noext/.prettierrc.json" })).toBe(".json");
        expect(tokenOf({ path: "/noext/.env.example" })).toBe(".example");
    });

    it.each(samples.filter((s) => TEXT_VIEWERS.has(s.viewer)).map((s) => [s.id, s]))(
        "%s is offered by the text viewer it names",
        (_id, s) => {
            const viewer = allViewers.find((v) => v.id === s.viewer);
            expect(viewer.supportedExtensions).toContain(tokenOf(s));
        }
    );

    it.each(samples.filter((s) => s.viewer === NO_VIEWER).map((s) => [s.id, s]))(
        "%s is offered by no viewer except the wildcard preview",
        (_id, s) => {
            const offering = allViewers
                .filter((v) => !v.supportedExtensions.includes(WILDCARD_EXTENSION))
                .filter((v) => v.supportedExtensions.includes(tokenOf(s)))
                .map((v) => v.id);
            expect(offering).toEqual([]);
        }
    );
});

describe("viewer render cases (web/e2e/viewers.spec.ts)", () => {
    // Each render case asserts that the viewer it names is the one the file page selected, by the
    // viewer's config `name`; a name that drifted from the config would fail every sandbox run.
    const spec = fs.readFileSync(path.join(WEB_ROOT, "e2e", "viewers.spec.ts"), "utf8");
    const names = new Set(
        [...(viewerConfig as any).viewers, ...(addonViewerConfig as any).viewers].map(
            (v: any) => v.name
        )
    );
    const cases = [...spec.matchAll(/viewer: "([^"]+)",\s*kind: "([a-z-]+)"/g)].map((m) => ({
        viewer: m[1],
        kind: m[2],
    }));

    it("[control] finds the render cases", () => {
        expect(cases.length).toBeGreaterThanOrEqual(10);
    });

    it("names each rendering viewer exactly as the viewer config does", () => {
        const unknown = cases
            .filter((c) => c.kind !== "not-offered" && !names.has(c.viewer))
            .map((c) => c.viewer);
        expect(unknown).toEqual([]);
    });
});
