/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An action the API refuses on the database compliance overview (an exception revoke, Evaluate
 * All, a bind, change or removal of the database's schema) is followed by a fresh read of the
 * binding and the overview, as a successful one is (D162). An exception revoked in another tab, or
 * a binding changed by another user, kept the old row or binding beside the refusal until the page
 * was reloaded. Now the refusal stays on the page, and the counts, the rows and the binding follow
 * the records as the API holds them after the refusal. (The bind and removal refusals are covered in
 * DatabaseCompliance.binding.test.tsx.)
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DatabaseCompliancePage from "./DatabaseCompliance";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
    evaluateDatabaseCompliance: jest.fn(),
    revokeException: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const row = (complianceState: string) => ({
    databaseId: "db1",
    assetId: "a-exc",
    assetName: "excepted-asset",
    complianceState,
    schemaName: "std",
    schemaSource: "database",
});

const overviewOf = (complianceState: string) => [
    true,
    {
        databaseId: "db1",
        totalAssets: 1,
        summary: { [complianceState]: 1 },
        assets: [row(complianceState)],
    },
];

// The overview each read answers, in order; the last one repeats.
const overviewsRead = (...states: string[]) => {
    let reads = 0;
    service().fetchDatabaseComplianceOverview.mockImplementation(async () => {
        const state = states[Math.min(reads, states.length - 1)];
        reads += 1;
        return overviewOf(state);
    });
};

const rowOf = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;

beforeEach(() => {
    jest.clearAllMocks();
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
    ]);
    service().fetchComplianceSchemas.mockResolvedValue([true, [{ schemaName: "std" }]]);
});

describe("DatabaseCompliance after a refused action (D162)", () => {
    it("reads the overview again after a refused revoke and shows the row's state now", async () => {
        // Another user revoked the exception; the asset went back to its verdict.
        overviewsRead("exception", "quarantined");
        service().revokeException.mockResolvedValue([false, "No exception is active"]);

        render(<DatabaseCompliancePage />);
        await screen.findByText("excepted-asset");
        await userEvent.click(
            screen.getByRole("button", { name: "Revoke the exception of excepted-asset" })
        );
        const dialog = (await screen.findByText("Revoke compliance exception")).closest(
            '[role="dialog"]'
        ) as HTMLElement;
        await userEvent.click(within(dialog).getByRole("button", { name: "Revoke" }));

        await waitFor(() =>
            expect(service().fetchDatabaseComplianceOverview).toHaveBeenCalledTimes(2)
        );
        await waitFor(() => expect(service().getDatabaseBindings).toHaveBeenCalledTimes(2));
        // The refusal stays and names the asset; its row shows the state the API now holds.
        expect(
            await screen.findByText(
                "The exception for excepted-asset was not revoked: No exception is active"
            )
        ).toBeInTheDocument();
        await waitFor(() =>
            expect(within(rowOf("excepted-asset")).getByText("Quarantined")).toBeInTheDocument()
        );
        expect(
            screen.queryByRole("button", { name: "Revoke the exception of excepted-asset" })
        ).toBeNull();
    });

    it("reads the overview again after a refused Evaluate All", async () => {
        overviewsRead("compliant", "pending_evaluation");
        service().evaluateDatabaseCompliance.mockResolvedValue([
            false,
            "An evaluation of this database is already running",
        ]);

        render(<DatabaseCompliancePage />);
        await screen.findByText("excepted-asset");
        await userEvent.click(screen.getByRole("button", { name: /Evaluate All/ }));

        await waitFor(() =>
            expect(service().fetchDatabaseComplianceOverview).toHaveBeenCalledTimes(2)
        );
        expect(
            await screen.findByText("An evaluation of this database is already running")
        ).toBeInTheDocument();
        await waitFor(() =>
            expect(
                within(rowOf("excepted-asset")).getByText("Pending Evaluation")
            ).toBeInTheDocument()
        );
    });
});
