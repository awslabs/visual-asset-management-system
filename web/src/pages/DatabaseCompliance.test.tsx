/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatabaseCompliancePage from "./DatabaseCompliance";
import { COMPLIANCE_LISTING_PAGE_SIZE } from "../services/ComplianceService";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    sweepSchema: jest.fn(),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const overviewPage = (assetName: string, nextToken?: string) => ({
    databaseId: "db1",
    totalAssets: 120,
    summary: {
        compliant: 100,
        non_compliant: 11,
        pending_evaluation: 3,
        quarantined: 2,
        exception: 4,
        unknown: 0,
    },
    assets: [
        {
            databaseId: "db1",
            assetId: `id-${assetName}`,
            assetName,
            complianceState: "compliant",
            schemaName: "std",
        },
        {
            databaseId: "db1",
            assetId: `id-${assetName}-excepted`,
            assetName: `${assetName}-excepted`,
            complianceState: "exception",
            schemaName: "std",
        },
    ],
    nextToken,
});

describe("DatabaseCompliance asset-state paging", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        service().getDatabaseBindings.mockResolvedValue([
            true,
            { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
        ]);
    });

    it("shows the page against the full total and fetches page two with its token", async () => {
        service().fetchDatabaseComplianceOverview.mockImplementation(
            async (_db: string, paging: any) =>
                paging.startingToken === "tok-2"
                    ? [true, overviewPage("second-page-asset")]
                    : [true, overviewPage("first-page-asset", "tok-2")]
        );

        render(<DatabaseCompliancePage />);
        await screen.findByText("first-page-asset");

        // The summary is the whole database; the table counter is the page against that total.
        expect(screen.getByText("120")).toBeInTheDocument();
        expect(screen.getByText("(2 of 120)")).toBeInTheDocument();
        expect(screen.getByText(/Showing 2 of 120 tracked/)).toBeInTheDocument();
        expect(service().fetchDatabaseComplianceOverview.mock.calls[0]).toEqual([
            "db1",
            { maxItems: COMPLIANCE_LISTING_PAGE_SIZE, startingToken: undefined },
        ]);

        await userEvent.click(screen.getByRole("button", { name: /Next page of/ }));

        await screen.findByText("second-page-asset");
        expect(screen.queryByText("first-page-asset")).not.toBeInTheDocument();
        expect(service().fetchDatabaseComplianceOverview.mock.calls[1][1]).toEqual({
            maxItems: COMPLIANCE_LISTING_PAGE_SIZE,
            startingToken: "tok-2",
        });
        expect(screen.getByText("Schema: std")).toBeInTheDocument();
    });

    it("shows the exception bucket in the summary and the exception badge in the table", async () => {
        service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overviewPage("asset")]);

        render(<DatabaseCompliancePage />);
        await screen.findByText("asset-excepted");

        // The tile label and the row badge both read "Exception"; the count sits beside the tile.
        expect(screen.getAllByText("Exception")).toHaveLength(2);
        expect(screen.getByText("4")).toBeInTheDocument();
        expect(screen.getAllByText("Compliant")).toHaveLength(2);
    });
});
