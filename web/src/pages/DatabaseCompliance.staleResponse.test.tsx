/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Moving from one database's compliance overview to another's shows the new database's rows only.
 *
 * The move changes the URL first: the filters follow the URL at once, the route's `databaseId` a
 * render later. The page sends nothing for the old database in between, and a response that
 * arrives after a later request began (the old database's, here) is dropped, whatever the order
 * the two answers come back in.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import DatabaseCompliancePage from "./DatabaseCompliance";

let mockRouteDatabaseId = "db-a";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: mockRouteDatabaseId }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => false }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const overviewOf = (databaseId: string, assetName: string) => ({
    databaseId,
    totalAssets: 1,
    summary: {
        compliant: 1,
        non_compliant: 0,
        pending_evaluation: 0,
        quarantined: 0,
        exception: 0,
        unknown: 0,
        error: 0,
    },
    assets: [
        {
            databaseId,
            assetId: `${databaseId}-asset`,
            assetName,
            complianceState: "compliant",
            schemaName: "std",
        },
    ],
});

/** Overview requests the test answers by hand, in any order. */
const pending: { databaseId: string; resolve: (value: any) => void }[] = [];

const answer = async (databaseId: string, value: any) => {
    const index = pending.findIndex((request) => request.databaseId === databaseId);
    expect(index).toBeGreaterThanOrEqual(0);
    const [request] = pending.splice(index, 1);
    await act(async () => {
        request.resolve(value);
    });
};

/** The route moves to another database: the URL changes (its listeners run), then the route. */
const moveTo = (url: string, databaseId: string, rerender: () => void) => {
    act(() => {
        window.history.pushState(null, "", url);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    mockRouteDatabaseId = databaseId;
    rerender();
};

beforeEach(() => {
    jest.clearAllMocks();
    pending.length = 0;
    mockRouteDatabaseId = "db-a";
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db-a", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
    ]);
    service().fetchDatabaseComplianceOverview.mockImplementation(
        (databaseId: string) =>
            new Promise((resolve) => {
                pending.push({ databaseId, resolve });
            })
    );
});

describe("DatabaseCompliance stale responses", () => {
    it("shows only the current database's rows when the old answer arrives last", async () => {
        window.history.replaceState(null, "", "/#/databases/db-a/compliance");
        const { rerender } = render(<DatabaseCompliancePage />);
        await waitFor(() => expect(pending.map((request) => request.databaseId)).toEqual(["db-a"]));

        moveTo("/#/databases/db-b/compliance", "db-b", () => rerender(<DatabaseCompliancePage />));
        await waitFor(() =>
            expect(pending.map((request) => request.databaseId)).toEqual(["db-a", "db-b"])
        );

        // Out of order: the new database answers first, the old one after it.
        await answer("db-b", [true, overviewOf("db-b", "Bravo Bracket")]);
        await answer("db-a", [true, overviewOf("db-a", "Alpha Axle")]);

        expect(await screen.findByText("Bravo Bracket")).toBeInTheDocument();
        expect(screen.queryByText("Alpha Axle")).not.toBeInTheDocument();
    });

    it("sends nothing for the old database while the route moves to the new one", async () => {
        window.history.replaceState(null, "", "/#/databases/db-a/compliance?state=non_compliant");
        const { rerender } = render(<DatabaseCompliancePage />);
        await waitFor(() => expect(pending).toHaveLength(1));
        const requests = () => service().fetchDatabaseComplianceOverview.mock.calls;
        expect(requests()[0][0]).toBe("db-a");
        expect(requests()[0][2].state).toBe("non_compliant");

        // The URL drops the filter as it names db-b; the filter reset runs while the route still
        // names db-a.
        moveTo("/#/databases/db-b/compliance", "db-b", () => rerender(<DatabaseCompliancePage />));
        await waitFor(() => expect(requests().map((call: any[]) => call[0])).toContain("db-b"));

        // Exactly one request for db-a (the first page, with its filter) and none for db-a with
        // the reset filters.
        expect(requests().filter((call: any[]) => call[0] === "db-a")).toHaveLength(1);
        await answer("db-b", [true, overviewOf("db-b", "Bravo Bracket")]);
        await answer("db-a", [true, overviewOf("db-a", "Alpha Axle")]);
        expect(await screen.findByText("Bravo Bracket")).toBeInTheDocument();
        expect(screen.queryByText("Alpha Axle")).not.toBeInTheDocument();
    });
});
