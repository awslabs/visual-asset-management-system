/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The row actions of the Quarantine, Cascade Approvals, Compliance Schemas and database compliance
 * overview tables fit on one line and stay on screen at 1280-1920 px. Bordered buttons in a fixed
 * column first wrapped one letter per line, then (with a 220-240 px column) stacked on two lines
 * with the column off-screen at 1440 px, because two bordered buttons need more than 200 px once the
 * cell padding and the gap are counted and the other columns widened the table past the window.
 *
 * The layout pinned here: inline-link actions side by side in a fixed-width Actions column that is
 * the table's last column and stays pinned (`stickyColumns.last`), with the other columns wrapping
 * (`wrapLines`). The table is replaced by a stand-in that records the props each page hands it, and
 * each actions cell is rendered on its own.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import ComplianceQuarantine, { QUARANTINE_ACTIONS_COLUMN_WIDTH } from "./ComplianceQuarantine";
import ComplianceCascades, { CASCADE_ACTIONS_COLUMN_WIDTH } from "./ComplianceCascades";
import ComplianceSchemas, { SCHEMA_ACTIONS_COLUMN_WIDTH } from "./ComplianceSchemas";
import DatabaseCompliancePage, { ASSET_ROW_ACTIONS_COLUMN_WIDTH } from "./DatabaseCompliance";
import { ROW_ACTIONS_STICKY_COLUMNS } from "../components/compliance/rowActions";

const mockTableProps: any[] = [];
jest.mock("@cloudscape-design/components/table", () => {
    const StandIn = (props: any) => {
        mockTableProps.push(props);
        return null;
    };
    return { __esModule: true, default: StandIn };
});

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn(),
    fetchCascades: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
    // The Schemas page reads the unfiltered listing through fetchComplianceSchemaListing; it
    // answers with what the test's fetchComplianceSchemas mock returns.
    fetchComplianceSchemaListing: jest.fn(async () => {
        const [ok, result] = await jest
            .requireMock("../services/ComplianceService")
            .fetchComplianceSchemas();
        return [ok, ok ? { schemas: result } : result];
    }),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
}));

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
    loader: { config: jest.fn() },
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const lastTableProps = () => mockTableProps[mockTableProps.length - 1];

const actionsColumn = () => {
    const columns = lastTableProps().columnDefinitions as any[];
    const actions = columns.filter((column) => column.id === "actions");
    expect(actions).toHaveLength(1);
    // It is the last column, which is the one `stickyColumns.last` pins.
    expect(columns[columns.length - 1].id).toBe("actions");
    return actions[0];
};

const cases = [
    {
        page: "Quarantine",
        render: () => render(<ComplianceQuarantine />),
        width: QUARANTINE_ACTIONS_COLUMN_WIDTH,
        item: { databaseId: "db1", assetId: "a1", assetName: "Asset one" },
        labels: ["Release", "Exception"],
    },
    {
        page: "Cascade Approvals",
        render: () => render(<ComplianceCascades />),
        width: CASCADE_ACTIONS_COLUMN_WIDTH,
        item: { cascadeId: "0f6f9c1e-1111-4222-8333-444455556666", state: "pending_approval" },
        labels: ["Approve", "Reject"],
    },
    {
        page: "Compliance Schemas",
        render: () => render(<ComplianceSchemas />),
        width: SCHEMA_ACTIONS_COLUMN_WIDTH,
        item: { schemaName: "std", schemaFormat: "vams-rules-v1", schemaBody: {} },
        labels: ["Edit", "Sweep"],
    },
    {
        page: "database compliance overview",
        render: () => render(<DatabaseCompliancePage />),
        width: ASSET_ROW_ACTIONS_COLUMN_WIDTH,
        item: { assetId: "a1", assetName: "Asset one", complianceState: "exception" },
        labels: ["History", "Revoke exception"],
    },
];

describe("compliance table row actions", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockTableProps.length = 0;
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            { quarantinedAssets: [], nextToken: undefined },
        ]);
        service().fetchCascades.mockResolvedValue([true, []]);
        service().fetchComplianceSchemas.mockResolvedValue([true, []]);
        service().fetchDatabaseComplianceOverview.mockResolvedValue([
            true,
            { databaseId: "db1", totalAssets: 0, summary: {}, assets: [] },
        ]);
        service().getDatabaseBindings.mockResolvedValue([
            true,
            { databaseId: "db1", databaseSchema: "std", assetOverrides: [] },
        ]);
    });

    it.each(cases)(
        "$page: the actions are pinned as the last column and the other columns wrap",
        async ({ render: renderPage }) => {
            renderPage();
            await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
            expect(lastTableProps().stickyColumns).toEqual(ROW_ACTIONS_STICKY_COLUMNS);
            expect(ROW_ACTIONS_STICKY_COLUMNS.last).toBe(1);
            expect(lastTableProps().wrapLines).toBe(true);
            actionsColumn();
        }
    );

    it.each(cases)(
        "$page: the Actions column has a fixed width for its two inline links",
        async ({ render: renderPage, width }) => {
            renderPage();
            await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
            const column = actionsColumn();
            expect(column.width).toBe(width);
            expect(column.minWidth).toBe(width);
            // Narrow enough to leave the other columns room at 1280 px, wide enough for two labels.
            expect(width).toBeGreaterThanOrEqual(140);
            expect(width).toBeLessThanOrEqual(240);
        }
    );

    it.each(cases)(
        "$page: each action is an inline link, side by side",
        async ({ render: renderPage, item, labels }) => {
            renderPage();
            await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
            const { container } = render(<>{actionsColumn().cell(item)}</>);
            const buttons = Array.from(container.querySelectorAll("button"));
            expect(buttons.map((b) => b.textContent)).toEqual(labels);
            for (const button of buttons) {
                // Cloudscape's class for `variant="inline-link"`: no border and no outer padding.
                expect(button.className).toMatch(/variant-inline-link/);
            }
        }
    );
});
