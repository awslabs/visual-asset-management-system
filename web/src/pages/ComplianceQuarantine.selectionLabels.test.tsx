/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Quarantine table's row selection has accessible names (#1520).
 *
 * The table selects one row with a radio button. It set no `ariaLabels`, so Cloudscape left each
 * radio button unnamed (axe: a critical `label` violation) and the selection column's header
 * empty. Each radio button is now named "Select <asset name, else its id>" and the selection
 * column after the group, the way the asset and search tables name theirs
 * (`tableSelectionLabels`; Cloudscape puts the group label on the column header, not on each
 * row's control).
 *
 * The other compliance tables are checked at the source: any of them that selects rows must name
 * its selection controls too.
 */

import fs from "fs";
import path from "path";
import React from "react";
import { render, screen } from "@testing-library/react";
import Synonyms from "../synonyms";
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

const GROUP_LABEL = `Quarantined ${Synonyms.assets} selection`;

describe("Quarantine table selection names (#1520)", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            {
                quarantinedAssets: [
                    quarantined("asset-1", "Turbine housing"),
                    // An asset with no name is named by its id.
                    quarantined("asset-2"),
                ],
                nextToken: undefined,
            },
        ]);
    });

    it("names each row's radio button after the asset it selects", async () => {
        render(<ComplianceQuarantine />);
        await screen.findByText("Turbine housing");

        const radios = screen.getAllByRole("radio");
        expect(radios).toHaveLength(2);
        expect(radios[0]).toHaveAccessibleName("Select Turbine housing");
        expect(radios[1]).toHaveAccessibleName("Select asset-2");
    });

    it("names the selection column after the group", async () => {
        render(<ComplianceQuarantine />);
        await screen.findByText("Turbine housing");

        expect(screen.getAllByRole("columnheader", { name: GROUP_LABEL }).length).toBeGreaterThan(
            0
        );
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
