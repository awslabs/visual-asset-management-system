/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The open database overview follows its URL: when the URL changes to another filter state of the
 * same overview (an in-app link or a URL pasted into the tab change the hash; Back and Forward
 * between two entries of it fire popstate), the page re-derives its filters from the URL and
 * reloads the table from the first page. A filter changed on the page still replaces the history
 * entry, so Back leaves the page rather than stepping through filters.
 */

import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import DatabaseCompliancePage from "./DatabaseCompliance";
import { readUrlFilters } from "../components/compliance/listingFilters";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
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

const overview = {
    databaseId: "db1",
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
            databaseId: "db1",
            assetId: "a1",
            assetName: "Turbine Blade",
            complianceState: "compliant",
            schemaName: "std",
        },
    ],
    nextToken: "tok-2",
};

const calls = () => service().fetchDatabaseComplianceOverview.mock.calls;
const lastCall = () => calls()[calls().length - 1];

/** Navigate the open tab: a new history entry, and the hash change event the browser fires. */
const navigate = (url: string) => {
    act(() => {
        window.history.pushState(null, "", url);
        window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
};

beforeEach(() => {
    jest.clearAllMocks();
    window.history.replaceState(null, "", "/#/databases/db1/compliance");
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
    ]);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overview]);
});

describe("DatabaseCompliance follows its URL", () => {
    it("a link to another filter state reloads the table with that state", async () => {
        render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        const before = calls().length;
        navigate("/#/databases/db1/compliance?text=fin-pc");
        await waitFor(() => expect(calls().length).toBe(before + 1));
        expect(lastCall()[1].startingToken).toBeUndefined();
        expect(lastCall()[2].text).toBe("fin-pc");
        // The search box shows the URL's text.
        await waitFor(() =>
            expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("fin-pc")
        );
    });

    it("a pasted URL with another sort and state reloads with both", async () => {
        render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        const before = calls().length;
        navigate("/#/databases/db1/compliance?sort=name&state=quarantined");
        await waitFor(() => expect(calls().length).toBe(before + 1));
        expect(lastCall()[2]).toEqual(
            expect.objectContaining({ sort: "name", state: "quarantined", text: "" })
        );
    });

    it("Back to an earlier entry of the overview restores its filters", async () => {
        render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        navigate("/#/databases/db1/compliance?state=compliant");
        await waitFor(() => expect(lastCall()[2].state).toBe("compliant"));
        const before = calls().length;
        act(() => {
            window.history.replaceState(null, "", "/#/databases/db1/compliance");
            window.dispatchEvent(new PopStateEvent("popstate"));
        });
        await waitFor(() => expect(calls().length).toBe(before + 1));
        expect(lastCall()[2].state).toBe("");
    });

    it("a hash change that names the same filters starts no reload", async () => {
        window.history.replaceState(null, "", "/#/databases/db1/compliance?state=compliant");
        render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        const before = calls().length;
        navigate("/#/databases/db1/compliance?state=compliant");
        // Give a reload the chance to start.
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 50));
        });
        expect(calls().length).toBe(before);
    });

    it("a filter changed on the page replaces the history entry and adds none", async () => {
        render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        const entries = window.history.length;
        const quarantinedTile = screen.getByRole("button", { name: /Show only quarantined/ });
        act(() => {
            quarantinedTile.click();
        });
        await waitFor(() => expect(lastCall()[2].state).toBe("quarantined"));
        expect(window.history.length).toBe(entries);
        expect(readUrlFilters(["state"])).toEqual({ state: "quarantined" });
    });
});
