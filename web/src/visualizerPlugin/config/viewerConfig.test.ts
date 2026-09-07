/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * @jest-environment node
 */

// Guards the three places viewer capability is declared against each other:
// the registry config, the loader code that actually parses each format, and the
// catalog table + CSP section in visualizerPlugin/CLAUDE.md. All three drifted in
// separate directions before this test existed — an extension a loader handles but
// the config omits makes the viewer unreachable, and an extension the config
// declares but no loader parses is a viewer that never finishes loading.

import fs from "fs";
import path from "path";
import viewerConfig from "./viewerConfig.json";
import { OCCT_FORMATS } from "../viewers/ThreeJSViewerPlugin/utils/fileLoaders";

const viewers: any[] = (viewerConfig as any).viewers;
const byId = (id: string) => {
    const found = viewers.find((viewer) => viewer.id === id);
    if (!found) throw new Error(`No viewer with id ${id} in viewerConfig.json`);
    return found;
};

/** Formats in `formats` (no leading dot) that `declared` does not list. */
const missingFrom = (declared: string[], formats: string[]): string[] =>
    formats.filter((format) => !declared.includes(`.${format}`));

describe("viewerConfig vs the loaders", () => {
    it("offers the Three.js viewer for every CAD format its loader routes to OCCT", () => {
        expect(missingFrom(byId("threejs-viewer").supportedExtensions, OCCT_FORMATS)).toEqual([]);
    });

    it("missingFrom reports a gap when one exists", () => {
        // Positive control for the assertion above: the same check against a list
        // that omits the OCCT formats must not come back empty.
        expect(missingFrom([".stp"], OCCT_FORMATS)).toContain("igs");
    });

    it("declares only the columnar formats the viewer can parse", () => {
        // ColumnarViewerComponent dispatches on .fcs and .csv. A declared format
        // with no parser reaches papaparse and yields zero columns.
        const columnar = byId("columnar-viewer");
        expect(columnar.supportedExtensions.sort()).toEqual([".csv", ".fcs"]);
        expect(columnar.supportedExtensions).not.toContain(".rds");
    });
});

describe("visualizerPlugin/CLAUDE.md catalog", () => {
    const claudeMd = fs.readFileSync(path.join(__dirname, "..", "CLAUDE.md"), "utf8");

    interface Row {
        id: string;
        extensions: string;
        status: string;
    }

    // Catalog rows look like: | `id` | Name | category | .a, .b | status |
    const rows: Row[] = claudeMd
        .split("\n")
        .filter((line) => /^\|\s*`[a-z0-9-]+`\s*\|/.test(line))
        .map((line) => {
            const cells = line.split("|").map((cell) => cell.trim());
            return {
                id: cells[1].replace(/`/g, ""),
                extensions: cells[4],
                status: cells[5],
            };
        })
        // Field-reference tables use the same shape; keep the rows whose first
        // cell is a real viewer id.
        .filter((row) => viewers.some((viewer) => viewer.id === row.id));

    it("finds a row for every configured viewer", () => {
        expect(rows.length).toBeGreaterThan(0);
        const documented = rows.map((row) => row.id).sort();
        expect(documented).toEqual(viewers.map((viewer) => viewer.id).sort());
    });

    it.each(rows.map((row) => [row.id, row] as [string, Row]))(
        "%s's documented extensions exist in the config",
        (id, row) => {
            const configured: string[] = byId(id).supportedExtensions;
            if (/wildcard/.test(row.extensions)) {
                expect(configured).toEqual(["*"]);
                return;
            }
            // Strip parentheticals ("(plaintext only)") before reading the list.
            const documented = row.extensions
                .replace(/\([^)]*\)/g, "")
                .split(",")
                .map((token) => token.trim())
                .filter((token) => token.startsWith("."));
            expect(documented.length).toBeGreaterThan(0);
            // Every documented extension must be configured. Rows that end in
            // "etc." are deliberately partial, so the reverse only holds when the
            // row claims to be complete.
            expect(documented.filter((ext) => !configured.includes(ext))).toEqual([]);
            if (!/etc\./.test(row.extensions)) {
                expect(configured.filter((ext) => !documented.includes(ext))).toEqual([]);
            }
        }
    );

    it.each(rows.map((row) => [row.id, row] as [string, Row]))(
        "%s's documented status matches its enabled flag and feature gates",
        (id, row) => {
            const config = byId(id);
            expect(/\bdisabled\b/.test(row.status)).toBe(config.enabled === false);
            const gates: string[] = config.featuresEnabledRestriction || [];
            gates.forEach((gate) => expect(row.status).toContain(gate));
            ["ALLOWUNSAFEEVAL", "PHYSNA_ADDON"].forEach((gate) => {
                if (row.status.includes(gate)) expect(gates).toContain(gate);
            });
        }
    );

    // "These viewers require ALLOWUNSAFEEVAL" is the claim that decides whether an
    // engineer investigates a viewer failing in a default deployment, so the list
    // has to be the set the config actually gates.
    describe("the ALLOWUNSAFEEVAL section", () => {
        const section = claudeMd.slice(claudeMd.indexOf("## CSP / `unsafe-eval`"));
        const bulletNames = section
            .split("\n")
            .filter((line) => /^-\s{3}\S/.test(line))
            .map((line) => line.replace(/^-\s+/, "").trim().toLowerCase());

        const gatedNames = viewers
            .filter((viewer) =>
                (viewer.featuresEnabledRestriction || []).includes("ALLOWUNSAFEEVAL")
            )
            .map((viewer) => viewer.name.toLowerCase());

        /** Compares names ignoring any parenthetical qualifier on either side. */
        const bare = (name: string) => name.replace(/\([^)]*\)/g, "").trim();
        const matches = (bullet: string, names: string[]) =>
            names.some(
                (name) => bare(name).startsWith(bare(bullet)) || bare(bullet).startsWith(bare(name))
            );

        it("lists exactly the gated viewers", () => {
            expect(bulletNames.length).toBe(gatedNames.length);
            bulletNames.forEach((bullet) => expect(matches(bullet, gatedNames)).toBe(true));
            gatedNames.forEach((name) => expect(matches(name, bulletNames)).toBe(true));
        });

        it("would reject a viewer that is described as gated but is not", () => {
            // Positive control: the entry this section used to carry. The Three.js
            // viewer has no featuresEnabledRestriction — gating it would take its
            // mesh formats down with the CAD ones.
            expect(matches("three.js cad-format loaders", gatedNames)).toBe(false);
        });
    });
});
