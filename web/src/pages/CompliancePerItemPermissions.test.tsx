/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The compliance pages follow the per-item permissions the service returns, beside the route
 * check: a database's compliance administrator holds every compliance route, so route gating alone
 * would show it Edit on GLOBAL schemas and Approve / Release on other databases' items.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ComplianceSchemas from "./ComplianceSchemas";
import ComplianceQuarantine from "./ComplianceQuarantine";
import ComplianceCascades from "./ComplianceCascades";
import { bindableSchemas, itemAllows } from "../services/ComplianceService";

jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
    loader: { config: jest.fn() },
}));

jest.mock("../services/ComplianceService", () => ({
    ...jest.requireActual("../services/ComplianceService"),
    fetchComplianceSchemaListing: jest.fn(),
    fetchQuarantinedAssets: jest.fn(),
    fetchCascades: jest.fn(),
}));

jest.mock("../services/APIService", () => ({
    ...jest.requireActual("../services/APIService"),
    fetchAllDatabases: jest.fn().mockResolvedValue([]),
}));

// Every compliance route is allowed: what is hidden below is hidden by the item's permissions.
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/ComplianceService");

const RULES_BODY = { schemaFormat: "vams-rules-v1", rules: {} };

const schema = (name: string, databaseId: string, permissions: Record<string, boolean>) => ({
    schemaName: name,
    databaseId,
    version: 1,
    schemaFormat: "vams-rules-v1",
    schemaBody: RULES_BODY,
    permissions,
});

beforeEach(() => {
    jest.clearAllMocks();
});

describe("itemAllows and bindableSchemas", () => {
    it("defer to the route check when the item carries no permissions", () => {
        expect(itemAllows({}, "edit")).toBe(true);
        expect(itemAllows(null, "edit")).toBe(true);
    });

    it("follow the item's permissions when it carries them", () => {
        expect(itemAllows({ permissions: { edit: true } }, "edit")).toBe(true);
        expect(itemAllows({ permissions: { edit: false } }, "edit")).toBe(false);
        expect(itemAllows({ permissions: {} }, "edit")).toBe(false);
    });

    it("offer a binding picker only the schemas marked bindable", () => {
        const schemas = [
            schema("global-1", "GLOBAL", { bind: true }),
            schema("blocked", "db1", { bind: false }),
            { schemaName: "older-response", schemaBody: RULES_BODY },
        ] as any[];
        expect(bindableSchemas(schemas).map((s) => s.schemaName)).toEqual([
            "global-1",
            "older-response",
        ]);
    });
});

describe("Compliance Schemas per schema", () => {
    const renderSchemas = async (createScopes?: string[]) => {
        service().fetchComplianceSchemaListing.mockResolvedValue([
            true,
            {
                schemas: [
                    schema("global-1", "GLOBAL", { edit: false, delete: false, bind: true }),
                    schema("own-1", "db1", { edit: true, delete: true, bind: true }),
                ],
                createScopes,
            },
        ]);
        render(<ComplianceSchemas />);
        await waitFor(() => expect(screen.getByText("own-1")).toBeInTheDocument());
    };

    const rowOf = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;

    it("shows Edit on the database's own schema and not on a GLOBAL one", async () => {
        await renderSchemas(["db1"]);
        expect(within(rowOf("own-1")).getByRole("button", { name: "Edit" })).toBeInTheDocument();
        expect(within(rowOf("global-1")).queryByRole("button", { name: "Edit" })).toBeNull();
    });

    it("opens a GLOBAL schema read-only for a caller who may not edit it", async () => {
        await renderSchemas(["db1"]);
        await userEvent.click(screen.getByRole("button", { name: "global-1" }));
        expect(await screen.findByText("View Schema")).toBeInTheDocument();
    });

    it("offers creation only in the scopes the caller may create in", async () => {
        await renderSchemas(["db1"]);
        await userEvent.click(screen.getByRole("button", { name: "Create Schema" }));
        const global = await screen.findByRole("radio", { name: "All databases (GLOBAL)" });
        expect(global).toBeDisabled();
        expect(screen.getByRole("radio", { name: /One database/i })).toBeChecked();
    });

    it("offers GLOBAL to a caller who may create there", async () => {
        await renderSchemas(["GLOBAL"]);
        await userEvent.click(screen.getByRole("button", { name: "Create Schema" }));
        const global = await screen.findByRole("radio", { name: "All databases (GLOBAL)" });
        expect(global).not.toBeDisabled();
        expect(global).toBeChecked();
        expect(screen.getByRole("radio", { name: /One database/i })).toBeDisabled();
    });

    it("hides Create Schema from a caller who may create in no scope", async () => {
        await renderSchemas([]);
        expect(screen.queryByRole("button", { name: "Create Schema" })).toBeNull();
    });
});

describe("Cascade Approvals per cascade", () => {
    const cascade = (id: string, approve: boolean) => ({
        cascadeId: id,
        state: "pending_approval",
        triggeredByDatabaseId: "db1",
        triggeredByAssetId: `asset-${id.substring(0, 4)}`,
        createdAt: "2026-01-01T00:00:00Z",
        permissions: { approve },
    });

    it("shows Approve and Reject only on the cascades the caller may approve", async () => {
        service().fetchCascades.mockResolvedValue([
            true,
            [
                cascade("1111aaaa-1111-4222-8333-444455556666", true),
                cascade("2222bbbb-1111-4222-8333-444455556666", false),
            ],
        ]);
        render(<ComplianceCascades />);
        const allowed = (await screen.findByText("asset-1111")).closest("tr") as HTMLElement;
        const denied = screen.getByText("asset-2222").closest("tr") as HTMLElement;
        expect(within(allowed).getByRole("button", { name: "Approve" })).toBeInTheDocument();
        expect(within(allowed).getByRole("button", { name: "Reject" })).toBeInTheDocument();
        expect(within(denied).queryByRole("button", { name: "Approve" })).toBeNull();
        expect(within(denied).queryByRole("button", { name: "Reject" })).toBeNull();
    });
});

describe("Quarantine per asset", () => {
    it("shows Release and Exception only on the rows the caller may act on", async () => {
        service().fetchQuarantinedAssets.mockResolvedValue([
            true,
            {
                quarantinedAssets: [
                    {
                        databaseId: "db1",
                        assetId: "a1",
                        assetName: "own-asset",
                        complianceState: "quarantined",
                        permissions: { release: true, exception: true },
                    },
                    {
                        databaseId: "db2",
                        assetId: "a2",
                        assetName: "other-asset",
                        complianceState: "quarantined",
                        permissions: { release: false, exception: false },
                    },
                ],
            },
        ]);
        render(<ComplianceQuarantine />);
        const own = (await screen.findByText("own-asset")).closest("tr") as HTMLElement;
        const other = screen.getByText("other-asset").closest("tr") as HTMLElement;
        expect(within(own).getByRole("button", { name: "Release" })).toBeInTheDocument();
        expect(within(own).getByRole("button", { name: "Exception" })).toBeInTheDocument();
        expect(within(other).queryByRole("button", { name: "Release" })).toBeNull();
        expect(within(other).queryByRole("button", { name: "Exception" })).toBeNull();
    });
});
