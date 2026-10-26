/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The compliance pages keep the side margins every VAMS list page has (the Compliance Schemas,
 * Metadata Schema, Tags and auth pages' `Box padding={{ top: "m", horizontal: "l" }}`). Quarantine,
 * Cascade Approvals and Audit Log, and the database compliance overview, rendered their tables
 * against the window's edges.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import ComplianceQuarantine from "./ComplianceQuarantine";
import ComplianceCascades from "./ComplianceCascades";
import ComplianceAuditLog from "./ComplianceAuditLog";
import ComplianceSchemas from "./ComplianceSchemas";
import DatabaseCompliancePage from "./DatabaseCompliance";
import { VAMS_PAGE_PADDING } from "../components/compliance/rowActions";

jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
}));

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
    loader: { config: jest.fn() },
}));

jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

jest.mock("../services/APIService", () => ({
    ...jest.requireActual("../services/APIService"),
    fetchAllDatabases: jest.fn().mockResolvedValue([]),
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchQuarantinedAssets: jest.fn().mockResolvedValue([true, { quarantinedAssets: [] }]),
    fetchCascades: jest.fn().mockResolvedValue([true, []]),
    fetchAuditLog: jest.fn().mockResolvedValue([true, { entries: [] }]),
    fetchComplianceSchemas: jest.fn().mockResolvedValue([true, []]),
    fetchDatabaseComplianceOverview: jest
        .fn()
        .mockResolvedValue([true, { databaseId: "db1", totalAssets: 0, summary: {}, assets: [] }]),
    getDatabaseBindings: jest
        .fn()
        .mockResolvedValue([true, { databaseId: "db1", databaseSchema: null, assetOverrides: [] }]),
}));

const pages: [string, () => JSX.Element][] = [
    ["Quarantine", () => <ComplianceQuarantine />],
    ["Cascade Approvals", () => <ComplianceCascades />],
    ["Audit Log", () => <ComplianceAuditLog />],
    ["database compliance overview", () => <DatabaseCompliancePage />],
    ["Compliance Schemas", () => <ComplianceSchemas />],
];

it("pins the padding the other VAMS list pages use", () => {
    expect(VAMS_PAGE_PADDING).toEqual({ top: "m", horizontal: "l" });
});

it.each(pages)("%s has the page's side and top margins", async (_name, page) => {
    const { container } = render(page());
    await waitFor(() => expect(container.firstElementChild).not.toBeNull());
    const root = container.firstElementChild as HTMLElement;
    // Cloudscape Box's padding classes for `top: "m"` and `horizontal: "l"`.
    expect(root.className).toMatch(/p-top-m/);
    expect(root.className).toMatch(/p-horizontal-l/);
});
