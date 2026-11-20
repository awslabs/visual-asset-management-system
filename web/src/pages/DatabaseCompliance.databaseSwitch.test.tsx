/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The compliance overview stays mounted when the route moves to another database (a pasted URL,
 * Back or Forward between two overviews). Evaluate All's continuation token and the last action's
 * message belong to the database they were made in, so the other database starts without them.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatabaseCompliancePage from "./DatabaseCompliance";

let mockDatabaseId = "db1";
jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: mockDatabaseId }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
    evaluateDatabaseCompliance: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

beforeEach(() => {
    jest.clearAllMocks();
    mockDatabaseId = "db1";
    service().fetchComplianceSchemas.mockResolvedValue([true, []]);
    service().getDatabaseBindings.mockImplementation(async (databaseId: string) => [
        true,
        { databaseId, databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
    ]);
    service().fetchDatabaseComplianceOverview.mockImplementation(async (databaseId: string) => [
        true,
        {
            databaseId,
            totalAssets: 1,
            summary: { compliant: 1 },
            assets: [
                {
                    databaseId,
                    assetId: `${databaseId}-a1`,
                    assetName: `${databaseId}-asset`,
                    complianceState: "compliant",
                    schemaName: "std",
                    schemaSource: "database",
                },
            ],
        },
    ]);
});

describe("DatabaseCompliance on another database", () => {
    it("starts Evaluate All afresh and drops the last action's message", async () => {
        service().evaluateDatabaseCompliance.mockResolvedValue([
            true,
            {
                message: "Evaluation triggered for 200 assets",
                assetsTriggered: [],
                assetsRemaining: 50,
                NextToken: "db1-token",
            },
        ]);
        const { rerender } = render(<DatabaseCompliancePage />);
        await screen.findByText("db1-asset");
        await userEvent.click(screen.getByRole("button", { name: /Evaluate All/ }));
        expect(await screen.findByText(/50 not reached yet/)).toBeInTheDocument();

        mockDatabaseId = "db2";
        rerender(<DatabaseCompliancePage />);
        await screen.findByText("db2-asset");
        expect(screen.queryByText(/50 not reached yet/)).not.toBeInTheDocument();

        await waitFor(() =>
            expect(screen.getByRole("button", { name: /Evaluate All/ })).toBeEnabled()
        );
        await userEvent.click(screen.getByRole("button", { name: /Evaluate All/ }));
        await waitFor(() =>
            expect(service().evaluateDatabaseCompliance).toHaveBeenLastCalledWith("db2", undefined)
        );
    });
});
