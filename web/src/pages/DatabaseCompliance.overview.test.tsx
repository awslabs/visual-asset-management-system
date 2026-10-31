/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The database compliance overview as the user guide describes it: a tile per state including
 * Unknown and Pending Evaluation, each asset's schema source said in words, and no "no assets are
 * tracked" text under an error when the overview could not be read at all.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import DatabaseCompliancePage from "./DatabaseCompliance";
import Synonyms from "../synonyms";

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

const tileValue = (label: string) => {
    const tile = screen.getByText(label).parentElement as HTMLElement;
    return tile.textContent?.replace(label, "").trim();
};

beforeEach(() => {
    jest.clearAllMocks();
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
    ]);
});

it("shows an Unknown tile and a Pending Evaluation tile with their counts", async () => {
    service().fetchDatabaseComplianceOverview.mockResolvedValue([
        true,
        {
            databaseId: "db1",
            totalAssets: 5,
            summary: { compliant: 1, pending_evaluation: 1, unknown: 3 },
            assets: [],
        },
    ]);
    render(<DatabaseCompliancePage />);
    await screen.findByText("Unknown");
    expect(tileValue("Unknown")).toBe("3");
    expect(tileValue("Pending Evaluation")).toBe("1");
});

it("says where each asset's schema binding comes from", async () => {
    service().fetchDatabaseComplianceOverview.mockResolvedValue([
        true,
        {
            databaseId: "db1",
            totalAssets: 2,
            summary: { compliant: 2 },
            assets: [
                {
                    databaseId: "db1",
                    assetId: "a1",
                    assetName: "inherits",
                    complianceState: "compliant",
                    schemaName: "std",
                    schemaSource: "database",
                },
                {
                    databaseId: "db1",
                    assetId: "a2",
                    assetName: "overrides",
                    complianceState: "compliant",
                    schemaName: "strict",
                    schemaSource: "asset",
                },
            ],
        },
    ]);
    render(<DatabaseCompliancePage />);
    expect(await screen.findByText(`Inherited from the ${Synonyms.database}`)).toBeInTheDocument();
    expect(screen.getByText(`Set on the ${Synonyms.asset}`)).toBeInTheDocument();
});

it("does not claim no assets are tracked when the overview could not be read", async () => {
    service().fetchDatabaseComplianceOverview.mockResolvedValue([false, "Not Authorized"]);
    render(<DatabaseCompliancePage />);
    expect(
        await screen.findByText(
            `Could not load the ${Synonyms.database} compliance overview: Not Authorized`
        )
    ).toBeInTheDocument();
    expect(
        await screen.findByText(`The ${Synonyms.asset} compliance states could not be loaded`)
    ).toBeInTheDocument();
    expect(screen.queryByText(/are being tracked for compliance/)).not.toBeInTheDocument();
});

it("says no assets the reader can view are tracked when the read succeeded with none", async () => {
    // The response is the same whether the database tracks no asset or the reader's asset rules
    // allow none of those it tracks, so the text is true for both and gives no count.
    service().fetchDatabaseComplianceOverview.mockResolvedValue([
        true,
        { databaseId: "db1", totalAssets: 0, summary: {}, assets: [] },
    ]);
    render(<DatabaseCompliancePage />);
    expect(
        await screen.findByText(
            `No ${Synonyms.assets} you can view are being tracked for compliance in this ${Synonyms.database}.`
        )
    ).toBeInTheDocument();
    expect(
        screen.queryByText(
            `No ${Synonyms.assets} are being tracked for compliance in this ${Synonyms.database}.`
        )
    ).not.toBeInTheDocument();
});
