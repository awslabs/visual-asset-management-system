/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * "Quarantined" stays on one line at 1440 px. The compliance tables wrap their columns
 * (`wrapLines`, for the pinned row actions), and a column narrower than its longest word broke it
 * mid-word: the Quarantine page's State badge read "Qua / rantine / d", its "Quarantined At"
 * header was clipped, and the database overview's tiles read "Quarantin / ed". The State and
 * timestamp columns now have a minimum width and the tiles a wider minimum column width; the row
 * actions stay the pinned last column (ComplianceActionColumns.test.tsx).
 *
 * The table and the column layout are replaced by stand-ins that record their props: jsdom does no
 * layout, so the widths a browser would apply are checked as the props the pages hand Cloudscape.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import ComplianceQuarantine from "./ComplianceQuarantine";
import DatabaseCompliancePage from "./DatabaseCompliance";
import {
    ROW_ACTIONS_STICKY_COLUMNS,
    SUMMARY_TILE_MIN_WIDTH,
    TIMESTAMP_COLUMN_MIN_WIDTH,
} from "../components/compliance/rowActions";

const mockTableProps: any[] = [];
jest.mock("@cloudscape-design/components/table", () => {
    const StandIn = (props: any) => {
        mockTableProps.push(props);
        return null;
    };
    return { __esModule: true, default: StandIn };
});

const mockColumnLayoutProps: any[] = [];
jest.mock("@cloudscape-design/components/column-layout", () => {
    const StandIn = (props: any) => {
        mockColumnLayoutProps.push(props);
        return <div>{props.children}</div>;
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
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const column = (id: string) => {
    const columns = mockTableProps[mockTableProps.length - 1].columnDefinitions as any[];
    const found = columns.find((definition) => definition.id === id);
    expect(found).toBeDefined();
    return found;
};

// What a label needs on one line: about 8 px per character of the 14 px bold label font (an
// upper bound for this text), plus the status icon and its gap, plus the cell's padding.
const oneLineWidth = (label: string, icon = 20, padding = 40) => label.length * 8 + icon + padding;

beforeEach(() => {
    jest.clearAllMocks();
    mockTableProps.length = 0;
    mockColumnLayoutProps.length = 0;
    service().fetchQuarantinedAssets.mockResolvedValue([
        true,
        { quarantinedAssets: [], nextToken: undefined },
    ]);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([
        true,
        { databaseId: "db1", totalAssets: 0, summary: {}, assets: [] },
    ]);
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: "std", assetOverrides: [] },
    ]);
});

describe("compliance labels keep their words whole", () => {
    it("the minimum widths fit the longest words on one line", () => {
        // The header word plus the sort control (about 30 px) and the padding.
        expect(TIMESTAMP_COLUMN_MIN_WIDTH).toBeGreaterThanOrEqual(oneLineWidth("Quarantined", 30));
        // A tile label has no icon; "Non-Compliant" is the longest word.
        expect(SUMMARY_TILE_MIN_WIDTH).toBeGreaterThanOrEqual(oneLineWidth("Non-Compliant", 0));
    });

    it("Quarantine: the Quarantined At column keeps a minimum width", async () => {
        render(<ComplianceQuarantine />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        expect(column("updatedAt").header).toBe("Quarantined At");
        expect(column("updatedAt").minWidth).toBe(TIMESTAMP_COLUMN_MIN_WIDTH);
        expect(column("updatedAt").minWidth).toBeGreaterThanOrEqual(
            oneLineWidth("Quarantined", 30)
        );
        // The row actions are unchanged: the pinned last column of a wrapping table.
        const props = mockTableProps[mockTableProps.length - 1];
        expect(props.stickyColumns).toEqual(ROW_ACTIONS_STICKY_COLUMNS);
        expect(props.wrapLines).toBe(true);
        const columns = props.columnDefinitions as any[];
        expect(columns[columns.length - 1].id).toBe("actions");
    });

    it("database overview: the tiles keep a minimum width", async () => {
        render(<DatabaseCompliancePage />);
        await waitFor(() => expect(mockTableProps.length).toBeGreaterThan(0));
        const tiles = mockColumnLayoutProps.find((props) => props.variant === "text-grid");
        expect(tiles).toBeDefined();
        expect(tiles.minColumnWidth).toBe(SUMMARY_TILE_MIN_WIDTH);
        expect(tiles.minColumnWidth).toBeGreaterThanOrEqual(oneLineWidth("Non-Compliant", 0));
        // The State column has no fixed width: its labels are one line each (StatusLabel), so the
        // table sizes it to the longest one.
        expect(column("complianceState").minWidth).toBeUndefined();
        const columns = mockTableProps[mockTableProps.length - 1].columnDefinitions as any[];
        expect(columns[columns.length - 1].id).toBe("actions");
    });
});
