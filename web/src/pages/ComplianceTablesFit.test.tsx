/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The compliance tables fit the content area at 1280 px, so nothing sits under the pinned Actions
 * column. At 1280 and 1440 px the Quarantine table was wider than the page and the pinned column
 * covered "Quarantined At" (and the State badge at 1280); the overview, Cascade Approvals and
 * Audit Log tables scrolled sideways too. The tables now carry fewer, denser columns: an asset is
 * one column (name, ID beneath), the overview's binding source sits beneath the bound schema, a
 * cascade's short ID beneath its status and its trigger database beneath the trigger asset, the
 * Quarantine page drops its State column (every row is quarantined), the Audit Log wraps its
 * columns, and long tokens in free text wrap anywhere.
 *
 * jsdom does no layout, so this pins the column sets and styles the browser lays out; the widths
 * at 1280 / 1440 / 1920 px were measured in a browser (the report's Round 8 section).
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import ComplianceQuarantine from "./ComplianceQuarantine";
import ComplianceCascades from "./ComplianceCascades";
import ComplianceAuditLog from "./ComplianceAuditLog";
import DatabaseCompliancePage from "./DatabaseCompliance";
import { WRAP_ANYWHERE_STYLE } from "../components/compliance/rowActions";

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
    fetchAuditLog: jest.fn(),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const lastTable = () => mockTableProps[mockTableProps.length - 1];
const columnIds = () => (lastTable().columnDefinitions as any[]).map((column) => column.id);
const column = (id: string) =>
    (lastTable().columnDefinitions as any[]).find((definition) => definition.id === id);

/** Render one cell to a container. */
const renderCell = (id: string, item: any) => render(<>{column(id).cell(item)}</>).container;

const ASSET = { databaseId: "db1", assetId: "x1a2-b3c4", assetName: "Turbine Blade" };

beforeEach(() => {
    jest.clearAllMocks();
    mockTableProps.length = 0;
    service().fetchQuarantinedAssets.mockResolvedValue([
        true,
        { quarantinedAssets: [], nextToken: undefined },
    ]);
    service().fetchCascades.mockResolvedValue([true, { cascades: [] }]);
    service().fetchAuditLog.mockResolvedValue([true, { entries: [] }]);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([
        true,
        { databaseId: "db1", totalAssets: 0, summary: {}, assets: [] },
    ]);
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: "std", assetOverrides: [] },
    ]);
    service().fetchComplianceSchemas.mockResolvedValue([true, []]);
});

describe("the compliance tables fit the page at 1280 px", () => {
    it("Quarantine: one asset column, no State column, a reason that wraps anywhere", async () => {
        render(<ComplianceQuarantine />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        expect(columnIds()).toEqual([
            "asset",
            "databaseId",
            "schemaName",
            "quarantineReason",
            "updatedAt",
            "actions",
        ]);
        const asset = renderCell("asset", ASSET);
        expect(asset.textContent).toContain("Turbine Blade");
        expect(asset.textContent).toContain("x1a2-b3c4");
        const reason = renderCell("quarantineReason", {
            quarantineReason: "no current file matches ^models/[a-z0-9_-]+\\.glb$",
        });
        expect((reason.firstChild as HTMLElement).style.overflowWrap).toBe(
            WRAP_ANYWHERE_STYLE.overflowWrap
        );
    });

    it("the database overview: one asset column, the binding source under the schema", async () => {
        render(<DatabaseCompliancePage />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        expect(columnIds()).toEqual([
            "asset",
            "complianceState",
            "schemaName",
            "updatedAt",
            "actions",
        ]);
        const schema = renderCell("schemaName", { schemaName: "std", schemaSource: "asset" });
        expect(schema.textContent).toContain("std");
        expect(schema.textContent).toContain("Set on the");
    });

    it("Cascade Approvals: status with the short ID, trigger asset with its database", async () => {
        render(<ComplianceCascades />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        expect(columnIds()).toEqual([
            "state",
            "assetId",
            "triggerReason",
            "createdAt",
            "approvalTimeoutAt",
            "actions",
        ]);
        const cascade = {
            cascadeId: "0f6f9c1e-1111-4222-8333-444455556666",
            state: "pending_approval",
            triggeredByDatabaseId: "db1",
            triggeredByAssetId: "x1a2-b3c4",
            assetName: "Turbine Blade",
            createdAt: "2026-10-27T04:46:43+00:00",
            actor: "reviewer",
        };
        expect(renderCell("state", cascade).textContent).toContain("0f6f9c1e");
        expect(renderCell("assetId", cascade).textContent).toContain("db1 / x1a2-b3c4");
        expect(renderCell("createdAt", cascade).textContent).toContain("by reviewer");
    });

    it("Audit Log: the columns wrap, identifiers break only at _ and camelCase", async () => {
        render(<ComplianceAuditLog />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        expect(lastTable().wrapLines).toBe(true);
        // The schema is shown beneath the event type, not in a column of its own.
        expect(columnIds()).toEqual([
            "timestamp",
            "eventType",
            "databaseId",
            "assetId",
            "actor",
            "details",
        ]);
        // The two identifier columns are never squeezed below a readable width.
        expect(column("eventType").minWidth).toBeGreaterThanOrEqual(150);
        expect(column("details").minWidth).toBeGreaterThanOrEqual(190);
        const eventType = renderCell("eventType", {
            eventType: "schema_bound_to_database",
            schemaName: "std",
        });
        expect(eventType.textContent).toContain("schema_bound_to_database");
        expect(eventType.textContent).toContain("std");
        // A break opportunity after each underscore, and nothing that wraps anywhere.
        expect(eventType.querySelectorAll("wbr")).toHaveLength(3);
        expect(eventType.querySelector("[style*='overflow-wrap']")).toBeNull();
        const details = renderCell("details", {
            details: JSON.stringify({ exceptionSchemaVersion: 3, reason: "non_compliant" }),
        });
        expect(details.textContent).toContain("exceptionSchemaVersion: 3");
        // exception|Schema|Version and non_|compliant.
        expect(details.querySelectorAll("wbr")).toHaveLength(3);
        expect(details.querySelector("[style*='overflow-wrap']")).toBeNull();
    });
});
