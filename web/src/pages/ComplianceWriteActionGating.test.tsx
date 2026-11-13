/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Write actions are offered only to a caller who may call their routes, and none while the routes
 * load: a compliance read-only role saw Create Schema / Edit / Sweep, Release / Exception,
 * Approve / Reject and Evaluate All, and every click ended in "Not Authorized".
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComplianceSchemas from "./ComplianceSchemas";
import ComplianceQuarantine from "./ComplianceQuarantine";
import ComplianceCascades from "./ComplianceCascades";
import DatabaseCompliancePage from "./DatabaseCompliance";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
    loader: { config: jest.fn() },
}));

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchComplianceSchemas: jest.fn(),
    // The Schemas page reads the unfiltered listing through fetchComplianceSchemaListing; it
    // answers with what the test's fetchComplianceSchemas mock returns.
    fetchComplianceSchemaListing: jest.fn(async () => {
        const [ok, result] = await jest
            .requireMock("../services/ComplianceService")
            .fetchComplianceSchemas();
        return [ok, ok ? { schemas: result } : result];
    }),
    fetchQuarantinedAssets: jest.fn(),
    fetchCascades: jest.fn(),
    fetchDatabaseComplianceOverview: jest.fn(),
    getDatabaseBindings: jest.fn(),
}));

// The routes the caller may call, as "METHOD path-template" strings; `mockLoading` is the hook's
// loading flag (it fails closed while loading).
let mockAllowed: string[] = [];
let mockLoading = false;
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: mockLoading,
        can: (method: string, path: string) =>
            !mockLoading && mockAllowed.includes(`${method} ${path}`),
    }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const READ_ROUTES = [
    "GET /compliance/schemas",
    "GET /compliance/quarantine",
    "GET /compliance/cascades",
    "GET /compliance/state/{databaseId}",
    "GET /compliance/bind/{databaseId}",
];

beforeEach(() => {
    jest.clearAllMocks();
    mockAllowed = [...READ_ROUTES];
    mockLoading = false;
    service().fetchComplianceSchemas.mockResolvedValue([
        true,
        [{ schemaName: "std", description: "d", version: 1, schemaBody: { rules: {} } }],
    ]);
    service().fetchQuarantinedAssets.mockResolvedValue([
        true,
        {
            quarantinedAssets: [
                {
                    databaseId: "db1",
                    assetId: "a1",
                    assetName: "quarantined-asset",
                    complianceState: "quarantined",
                },
            ],
        },
    ]);
    service().fetchCascades.mockResolvedValue([
        true,
        [
            {
                cascadeId: "0f6f9c1e-1111-4222-8333-444455556666",
                state: "pending_approval",
                triggeredByDatabaseId: "db1",
                triggeredByAssetId: "a1",
                createdAt: "2026-01-01T00:00:00Z",
            },
        ],
    ]);
    service().getDatabaseBindings.mockResolvedValue([
        true,
        { databaseId: "db1", databaseSchema: "std", assetOverrides: [], assetOverrideCount: 0 },
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
                    assetName: "tracked-asset",
                    complianceState: "compliant",
                    schemaName: "std",
                },
            ],
        },
    ]);
});

describe("Compliance Schemas write actions", () => {
    it("offers no Create, Edit or Sweep to a caller with read routes only", async () => {
        render(<ComplianceSchemas />);
        await screen.findByText("std");
        expect(screen.queryByRole("button", { name: "Create Schema" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Sweep" })).toBeNull();
    });

    it("opens a schema read-only from its name for that caller", async () => {
        render(<ComplianceSchemas />);
        await userEvent.click(await screen.findByRole("button", { name: "std" }));
        expect(await screen.findByText("View Schema")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Update" })).toBeNull();
        expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    });

    it("offers each action whose route the caller may call", async () => {
        mockAllowed = [
            ...READ_ROUTES,
            "POST /compliance/schemas",
            "PUT /compliance/schemas/{schemaName}",
            "POST /compliance/sweep/{schemaName}",
        ];
        render(<ComplianceSchemas />);
        await screen.findByText("std");
        expect(screen.getByRole("button", { name: "Create Schema" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Sweep" })).toBeInTheDocument();
    });

    it("offers nothing while the routes load", async () => {
        mockAllowed = [...READ_ROUTES, "POST /compliance/schemas"];
        mockLoading = true;
        render(<ComplianceSchemas />);
        await screen.findByText("std");
        expect(screen.queryByRole("button", { name: "Create Schema" })).toBeNull();
    });
});

describe("Quarantine write actions", () => {
    it("offers no Release or Exception to a caller with read routes only", async () => {
        render(<ComplianceQuarantine />);
        await screen.findByText("quarantined-asset");
        expect(screen.queryByRole("button", { name: "Release" })).toBeNull();
        expect(screen.queryByRole("button", { name: "Exception" })).toBeNull();
        expect(screen.queryByText("Actions")).toBeNull();
    });

    it("offers Release alone to a caller who may release only", async () => {
        mockAllowed = [
            ...READ_ROUTES,
            "POST /compliance/quarantine/{databaseId}/{assetId}/release",
        ];
        render(<ComplianceQuarantine />);
        await screen.findByText("quarantined-asset");
        expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Exception" })).toBeNull();
    });
});

describe("Cascade Approvals write actions", () => {
    it("offers no Approve or Reject to a caller with read routes only", async () => {
        render(<ComplianceCascades />);
        await screen.findByText("db1");
        // Within the table: the reject dialog (not open) carries its own Reject button.
        const table = within(screen.getByRole("table"));
        expect(table.queryByRole("button", { name: "Approve" })).toBeNull();
        expect(table.queryByRole("button", { name: "Reject" })).toBeNull();
    });

    it("offers both to a caller who may approve and reject", async () => {
        mockAllowed = [
            ...READ_ROUTES,
            "POST /compliance/cascades/{cascadeId}/approve",
            "POST /compliance/cascades/{cascadeId}/reject",
        ];
        render(<ComplianceCascades />);
        await screen.findByText("db1");
        const table = within(screen.getByRole("table"));
        expect(table.getByRole("button", { name: "Approve" })).toBeInTheDocument();
        expect(table.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    });
});

describe("Database compliance overview write actions", () => {
    it("offers no Evaluate All to a caller with read routes only", async () => {
        render(<DatabaseCompliancePage />);
        await screen.findByText("tracked-asset");
        expect(screen.queryByRole("button", { name: /Evaluate All/ })).toBeNull();
    });

    it("offers Evaluate All to a caller who may evaluate the database", async () => {
        mockAllowed = [...READ_ROUTES, "POST /compliance/evaluate/{databaseId}"];
        render(<DatabaseCompliancePage />);
        await screen.findByText("tracked-asset");
        await waitFor(() =>
            expect(screen.getByRole("button", { name: /Evaluate All/ })).toBeInTheDocument()
        );
    });
});
