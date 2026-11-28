/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The database compliance page links the database name in its breadcrumb to the database's
 * assets page, and each asset name to its asset page, only when the caller may open that page.
 * A caller who may not (such as one built from the Compliance Admin template) sees plain text,
 * not a link to a page that answers Not Authorized.
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
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
    sweepSchema: jest.fn(),
    fetchComplianceSchemas: jest.fn(),
    bindSchemaToDatabase: jest.fn(),
    unbindSchemaFromDatabase: jest.fn(),
}));

// The runtime configuration; null is a deployment with OpenSearch.
let mockConfig: any = null;
jest.mock("../services/appCache", () => ({ appCache: { getItem: () => mockConfig } }));

// The routes the caller may call, as "METHOD path-template" strings.
let mockAllowed: string[] = [];
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        can: (method: string, path: string) => mockAllowed.includes(`${method} ${path}`),
    }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const COMPLIANCE_ADMIN = [
    "GET /compliance/state/{databaseId}",
    "GET /compliance/schemas",
    "PUT /compliance/bind/{databaseId}",
    "DELETE /compliance/bind/{databaseId}",
];

// The breadcrumb group is the page's only navigation landmark.
const breadcrumbs = () => screen.getByRole("navigation");

const mount = async () => {
    render(<DatabaseCompliancePage />);
    await screen.findByText("asset-a");
};

beforeEach(() => {
    jest.clearAllMocks();
    mockConfig = null;
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([
        true,
        {
            databaseId: "db1",
            totalAssets: 1,
            summary: { compliant: 1 },
            assets: [
                {
                    databaseId: "db1",
                    assetId: "id-a",
                    assetName: "asset-a",
                    complianceState: "compliant",
                    schemaName: "std",
                },
            ],
        },
    ]);
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: null, assetOverrides: [], assetOverrideCount: 0 },
    ]);
    service().fetchComplianceSchemas.mockResolvedValue([true, []]);
});

afterEach(() => jest.restoreAllMocks());

describe(`${Synonyms.Database} compliance page links for a caller who may not open ${Synonyms.asset} pages`, () => {
    beforeEach(() => {
        mockAllowed = COMPLIANCE_ADMIN;
    });

    it(`does not link the breadcrumb to the ${Synonyms.assets} page`, async () => {
        await mount();
        const crumbs = breadcrumbs();
        expect(within(crumbs).getByRole("link", { name: Synonyms.Databases })).toHaveAttribute(
            "href",
            "#/databases"
        );
        expect(within(crumbs).queryByRole("link", { name: "db1" })).not.toBeInTheDocument();
        expect(within(crumbs).getAllByText("db1 Compliance").length).toBeGreaterThan(0);
        for (const link of within(crumbs).queryAllByRole("link")) {
            expect(link.getAttribute("href") || "").not.toContain("/assets");
        }
    });

    it(`shows ${Synonyms.asset} names as text, not links`, async () => {
        await mount();
        expect(screen.getByText("asset-a").closest("a")).toBeNull();
        expect(screen.queryByRole("link", { name: "asset-a" })).not.toBeInTheDocument();
    });
});

describe(`${Synonyms.Database} compliance page links for a caller who may open ${Synonyms.asset} pages`, () => {
    it(`links the breadcrumb to the ${Synonyms.assets} page when the caller may search`, async () => {
        mockAllowed = [...COMPLIANCE_ADMIN, "POST /search"];
        await mount();
        expect(within(breadcrumbs()).getByRole("link", { name: "db1" })).toHaveAttribute(
            "href",
            "#/databases/db1/assets/"
        );
    });

    it(`links the breadcrumb without OpenSearch when the caller may list the ${Synonyms.database}'s ${Synonyms.assets}`, async () => {
        mockConfig = { featuresEnabled: ["NOOPENSEARCH"] };
        mockAllowed = [...COMPLIANCE_ADMIN, "GET /database/{databaseId}/assets"];
        await mount();
        expect(within(breadcrumbs()).getByRole("link", { name: "db1" })).toHaveAttribute(
            "href",
            "#/databases/db1/assets/"
        );
    });

    it(`does not count search as access to the ${Synonyms.assets} page without OpenSearch`, async () => {
        mockConfig = { featuresEnabled: ["NOOPENSEARCH"] };
        mockAllowed = [...COMPLIANCE_ADMIN, "POST /search"];
        await mount();
        expect(within(breadcrumbs()).queryByRole("link", { name: "db1" })).not.toBeInTheDocument();
    });

    it(`links ${Synonyms.asset} names when the caller may read the ${Synonyms.asset}`, async () => {
        mockAllowed = [...COMPLIANCE_ADMIN, "GET /database/{databaseId}/assets/{assetId}"];
        await mount();
        expect(screen.getByRole("link", { name: "asset-a" })).toHaveAttribute(
            "href",
            "#/databases/db1/assets/id-a"
        );
    });
});
