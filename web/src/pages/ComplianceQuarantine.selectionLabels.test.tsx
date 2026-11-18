/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Compliance tables that select rows name their selection controls (#1520).
 *
 * A Cloudscape table that selects rows with no `ariaLabels` leaves each radio button or checkbox
 * unnamed (axe: a critical `label` violation) and the selection column's header empty. The
 * Quarantine table no longer selects rows (no action read the selection, so the column was
 * removed; ComplianceQuarantine.test.tsx checks it renders none). Any compliance table that selects
 * rows must name its controls the way the asset and search tables do (`tableSelectionLabels`), and
 * this source check holds them to it.
 */

import fs from "fs";
import path from "path";

describe("compliance tables that select rows name their selection controls", () => {
    const SRC = path.join(__dirname, "..");
    // Every compliance table's source: the compliance pages, the database overview, the
    // compliance components and the asset's Compliance tab.
    const sources = [
        ...fs
            .readdirSync(path.join(SRC, "pages"))
            .filter((name) => /^(Compliance|DatabaseCompliance)\w*\.tsx$/.test(name))
            .map((name) => path.join(SRC, "pages", name)),
        ...fs
            .readdirSync(path.join(SRC, "components", "compliance"))
            .filter((name) => name.endsWith(".tsx"))
            .map((name) => path.join(SRC, "components", "compliance", name)),
        path.join(SRC, "components", "asset", "tabs", "ComplianceTab.tsx"),
    ].filter((file) => !/\.test\.tsx$/.test(file));

    const count = (text: string, pattern: RegExp) => (text.match(pattern) || []).length;

    it("reads the compliance table sources", () => {
        const names = sources.map((file) => path.basename(file));
        expect(names).toEqual(
            expect.arrayContaining([
                "ComplianceQuarantine.tsx",
                "ComplianceCascades.tsx",
                "ComplianceAuditLog.tsx",
                "ComplianceSchemas.tsx",
                "DatabaseCompliance.tsx",
                "ComplianceTab.tsx",
            ])
        );
    });

    it("gives every selecting table its selection labels", () => {
        const unnamed = sources.filter((file) => {
            const text = fs.readFileSync(file, "utf8");
            const selecting = count(text, /\bselectionType=/g);
            const labelled = count(text, /\btableSelectionLabels\(|\bitemSelectionLabel\b/g);
            return selecting > labelled;
        });
        expect(unnamed.map((file) => path.relative(SRC, file))).toEqual([]);
    });
});
