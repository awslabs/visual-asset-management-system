/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * "Evaluate All Assets" on the database compliance overview evaluates this database's tracked
 * assets through POST /compliance/evaluate/{databaseId}; it no longer runs the schema-wide sweep,
 * which also evaluated other databases bound to the same schema and skipped this database's asset
 * overrides.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatabaseCompliancePage, { evaluateAllMessage } from "./DatabaseCompliance";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
    sweepSchema: jest.fn(),
    evaluateDatabaseCompliance: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const overview = (databaseSchema: string | null) => {
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema, assetOverrides: [], assetOverrideCount: 1 },
    ]);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([
        true,
        {
            databaseId: "db1",
            totalAssets: 1,
            summary: { compliant: 1 },
            assets: [
                {
                    databaseId: "db1",
                    assetId: "a1",
                    assetName: "override-asset",
                    complianceState: "compliant",
                    schemaName: "strict",
                    schemaSource: "asset",
                },
            ],
        },
    ]);
};

describe("DatabaseCompliance Evaluate All", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        service().fetchComplianceSchemas.mockResolvedValue([true, []]);
    });

    it("evaluates this database's assets, not the schema-wide sweep", async () => {
        overview("std");
        service().evaluateDatabaseCompliance.mockResolvedValue([
            true,
            {
                message: "Evaluation triggered for 1 assets",
                databaseId: "db1",
                assetsTriggered: [{ databaseId: "db1", assetId: "a1", schemaName: "strict" }],
                assetsRemaining: 0,
            },
        ]);
        render(<DatabaseCompliancePage />);
        await screen.findByText("override-asset");
        await userEvent.click(screen.getByRole("button", { name: /Evaluate All/ }));
        await waitFor(() =>
            expect(service().evaluateDatabaseCompliance).toHaveBeenCalledWith("db1")
        );
        expect(service().sweepSchema).not.toHaveBeenCalled();
        expect(await screen.findByText(/Evaluation triggered for 1 assets/)).toBeInTheDocument();
    });

    it("is offered for a database whose only tracked assets carry their own schema", async () => {
        overview(null);
        render(<DatabaseCompliancePage />);
        await screen.findByText("override-asset");
        expect(screen.getByRole("button", { name: /Evaluate All/ })).toBeEnabled();
    });

    it("says how many assets were not reached and how many were registered", () => {
        expect(
            evaluateAllMessage({
                message: "Evaluation triggered for 200 assets",
                databaseId: "db1",
                assetsTriggered: [],
                assetsRemaining: 35,
                registered: 4,
            })
        ).toMatch(/200 assets; 4 newly registered.*; 35 not reached yet/);
    });
});
