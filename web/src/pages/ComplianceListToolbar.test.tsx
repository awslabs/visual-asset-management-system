/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every compliance list page lays its toolbar out the way the rest of VAMS does (TableList, D185):
 * the search on the left with the refresh button right next to it, and the filter drop-downs in a
 * group aligned to the right of the table. The refresh is not repeated in the header actions.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import Synonyms from "../synonyms";
import ComplianceQuarantine from "./ComplianceQuarantine";
import ComplianceCascades from "./ComplianceCascades";
import ComplianceAuditLog from "./ComplianceAuditLog";
import DatabaseCompliancePage from "./DatabaseCompliance";
import ComplianceSchemas from "./ComplianceSchemas";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
    loader: { config: jest.fn() },
}));

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(async () => [true, { quarantinedAssets: [] }]),
    fetchCascades: jest.fn(async () => [true, { cascades: [] }]),
    fetchAuditLog: jest.fn(async () => [true, { entries: [] }]),
    fetchDatabaseComplianceOverview: jest.fn(async () => [
        true,
        { databaseId: "db1", totalAssets: 0, summary: {}, assets: [] },
    ]),
    getDatabaseBindings: jest.fn(async () => [
        true,
        { databaseId: "db1", databaseSchema: null, assetOverrides: [] },
    ]),
    fetchComplianceSchemas: jest.fn(async () => [true, []]),
    fetchComplianceSchemaListing: jest.fn(async () => [true, { schemas: [] }]),
}));

jest.mock("../services/APIService", () => ({
    fetchAllDatabases: jest.fn(() => Promise.resolve([{ databaseId: "db1" }])),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../hooks/useAssetPageAccess", () => ({
    useAssetPageAccess: () => ({ canOpenAsset: false, canOpenDatabaseAssets: false }),
}));

const group = (name: "search" | "filters") =>
    document.querySelector<HTMLElement>(`[data-list-toolbar-group='${name}']`);

/** The search group's input, then its refresh button, in that order; the filters after. */
const expectToolbar = (searchLabel: string, refreshLabel: string, filterLabels: string[]) => {
    const toolbar = document.querySelector<HTMLElement>("[data-list-toolbar]");
    expect(toolbar).not.toBeNull();
    const groups = Array.from(toolbar!.children).map((child) =>
        child.getAttribute("data-list-toolbar-group")
    );
    expect(groups).toEqual(["search", "filters"]);

    const search = group("search")!;
    const controls = Array.from(search.querySelectorAll("input, button"));
    expect(controls[0]).toBe(screen.getByLabelText(searchLabel));
    expect(controls[controls.length - 1]).toBe(screen.getByRole("button", { name: refreshLabel }));
    // One refresh, beside the search: none in the header actions.
    expect(screen.getAllByRole("button", { name: refreshLabel })).toHaveLength(1);

    const filters = group("filters")!;
    filterLabels.forEach((label) => {
        const control = screen
            .getAllByLabelText(label)
            .find((element) => filters.contains(element));
        expect(control).toBeDefined();
        expect(search.contains(control!)).toBe(false);
    });
};

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    window.history.replaceState(null, "", "/#/compliance");
});

afterEach(() => jest.restoreAllMocks());

describe("compliance list toolbars", () => {
    it("Quarantine: search and refresh, then the database filter", async () => {
        render(<ComplianceQuarantine />);
        await screen.findByText(`No quarantined ${Synonyms.assets}`);
        expectToolbar(
            `Search quarantined ${Synonyms.assets}`,
            `Refresh quarantined ${Synonyms.assets}`,
            [`Filter by ${Synonyms.database}`]
        );
    });

    it("Cascades: search and refresh, then the state and database filters", async () => {
        render(<ComplianceCascades />);
        await screen.findByText("No cascades");
        expectToolbar("Search cascades", "Refresh cascades", [
            "Filter by cascade state",
            `Filter by ${Synonyms.database}`,
        ]);
    });

    it("Audit log: search and refresh, then the event type, database, asset and day filters", async () => {
        render(<ComplianceAuditLog />);
        await screen.findByText("No audit entries");
        expectToolbar("Search audit entries", "Refresh audit log", [
            "Filter audit entries by event type",
            `Filter by ${Synonyms.database}`,
            `Filter by ${Synonyms.asset} ID`,
            "Entries from",
            "Entries to",
        ]);
        // The event type filter keeps its visible label in the toolbar.
        expect(group("filters")!.textContent).toContain("Event type");
        // Its five filters do not fit beside the search at 1440 px; on their own line they stay
        // at the right (D190).
        expect(group("filters")!.style.marginLeft).toBe("auto");
        expect(group("filters")!.style.justifyContent).toBe("flex-end");
    });

    it(`${Synonyms.Database} overview: search and refresh, then the filters and Sort by`, async () => {
        render(<DatabaseCompliancePage />);
        await waitFor(() => expect(document.querySelector("[data-list-toolbar]")).not.toBeNull());
        expectToolbar(
            `Search ${Synonyms.assets}`,
            `Refresh the ${Synonyms.database} compliance overview`,
            [
                "Filter by compliance state",
                "Filter by bound schema",
                "Filter by binding source",
                "Filter by last evaluation",
                `Sort ${Synonyms.assets}`,
            ]
        );
    });

    it("Schemas: search and refresh, then the availability filter", async () => {
        render(<ComplianceSchemas />);
        await screen.findByText(/No compliance schemas registered/);
        expectToolbar("Find schemas", "Refresh compliance schemas", ["Filter by availability"]);
    });
});
