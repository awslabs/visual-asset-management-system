/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * While the service is still building its name index after an upgrade, a name-sorted overview
 * page comes back in asset ID order with `nameSortUnavailable`; the page says so instead of
 * presenting the rows as sorted by name.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import DatabaseCompliancePage, { NAME_SORT_UNAVAILABLE_NOTICE } from "./DatabaseCompliance";

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

const overview = (nameSortUnavailable?: boolean) => ({
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
    ...(nameSortUnavailable === undefined ? {} : { nameSortUnavailable }),
});

beforeEach(() => {
    jest.clearAllMocks();
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
    ]);
});

describe("DatabaseCompliance name sort during indexing", () => {
    it("shows the notice when the name-sorted page came back in ID order", async () => {
        window.history.replaceState(null, "", "/#/databases/db1/compliance?sort=name");
        service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overview(true)]);
        render(<DatabaseCompliancePage />);
        expect(await screen.findByText(NAME_SORT_UNAVAILABLE_NOTICE)).toBeInTheDocument();
        expect(NAME_SORT_UNAVAILABLE_NOTICE).toMatch(
            /^Sort by name is available once indexing finishes/
        );
        // The rows are still shown.
        expect(screen.getByText("Turbine Blade")).toBeInTheDocument();
    });

    it("shows no notice once the index answers", async () => {
        window.history.replaceState(null, "", "/#/databases/db1/compliance?sort=name");
        service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overview()]);
        render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        expect(screen.queryByText(NAME_SORT_UNAVAILABLE_NOTICE)).not.toBeInTheDocument();
    });

    it("shows no notice when the ID sort is chosen", async () => {
        window.history.replaceState(null, "", "/#/databases/db1/compliance");
        service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overview(true)]);
        render(<DatabaseCompliancePage />);
        await screen.findByText("Turbine Blade");
        await waitFor(() => expect(service().fetchDatabaseComplianceOverview).toHaveBeenCalled());
        expect(screen.queryByText(NAME_SORT_UNAVAILABLE_NOTICE)).not.toBeInTheDocument();
    });
});
