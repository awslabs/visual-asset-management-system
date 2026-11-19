/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Quarantine table leaves no selection control unnamed (#1520).
 *
 * No action reads a row selection, so the table renders no radio button or checkbox, and every
 * control it renders has an accessible name: the condition axe's `label` rule checks for form
 * controls (and its `button-name` and `link-name` rules for the row actions).
 *
 * The other compliance tables are checked at the source: any of them that selects rows must name
 * its selection controls.
 */

import fs from "fs";
import path from "path";
import React from "react";
import { render, screen } from "@testing-library/react";
import ComplianceQuarantine from "./ComplianceQuarantine";

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const quarantined = (assetId: string, assetName?: string) => ({
    databaseId: "db1",
    assetId,
    assetName,
    complianceState: "quarantined",
    schemaName: "std",
    quarantineReason: "metadata missing",
    exceptionGranted: false,
    updatedAt: "2026-01-01T00:00:00Z",
});

describe("Quarantine table selection controls (#1520)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            {
                quarantinedAssets: [
                    quarantined("asset-1", "Turbine housing"),
                    // An asset with no name is listed by its id.
                    quarantined("asset-2"),
                ],
                nextToken: undefined,
            },
        ]);
    });

    it("renders no row selection control", async () => {
        render(<ComplianceQuarantine />);
        await screen.findByText("Turbine housing");

        expect(screen.queryAllByRole("radio")).toHaveLength(0);
        expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
    });

    it("gives every control in the table an accessible name", async () => {
        const { container } = render(<ComplianceQuarantine />);
        await screen.findByText("Turbine housing");

        const tables = container.querySelectorAll("table");
        expect(tables.length).toBeGreaterThan(0);
        const controls = Array.from(tables).flatMap((table) =>
            Array.from(
                table.querySelectorAll<HTMLElement>(
                    'input:not([type="hidden"]), select, textarea, button, a[href], [role="radio"], [role="checkbox"]'
                )
            )
        );
        // The row actions (Release, Exception) are controls too, so the check has something to read.
        expect(controls.length).toBeGreaterThan(0);
        controls.forEach((control) => expect(control).toHaveAccessibleName());
    });
});

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
