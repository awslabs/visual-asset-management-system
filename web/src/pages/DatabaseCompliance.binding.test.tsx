/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The database compliance page binds, changes and removes the database's compliance schema. A role
 * that holds only compliance permissions (the Compliance Admin template) cannot edit the database,
 * so this control is where it binds: it is offered to a caller who may list schemas and bind or
 * unbind one, asks before removing a binding, reports each outcome on the page, and re-reads the
 * binding and the overview after a change.
 */
import React from "react";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
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

const VIEW = ["GET /compliance/state/{databaseId}"];
const BIND = ["GET /compliance/schemas", "PUT /compliance/bind/{databaseId}"];
const UNBIND = ["GET /compliance/schemas", "DELETE /compliance/bind/{databaseId}"];
const COMPLIANCE_ADMIN = [...VIEW, ...BIND, ...UNBIND];

const overview = {
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
};

const bindings = (databaseSchema: string | null) => [
    true,
    { databaseId: "db1", databaseSchema, assetOverrides: [], assetOverrideCount: 0 },
];

const schemaSelect = () => {
    const select = createWrapper()
        .findAllFormFields()
        .find((field) => field.findLabel()?.getElement().textContent === "Bound schema")
        ?.findControl()
        ?.findSelect();
    if (!select) throw new Error("no select in the Bound schema field");
    return select;
};

const choose = (value: string) => {
    const select = schemaSelect();
    act(() => {
        select.openDropdown();
    });
    act(() => {
        select.selectOptionByValue(value);
    });
};

const mount = async () => {
    render(<DatabaseCompliancePage />);
    await screen.findByText("asset-a");
};

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    service().fetchDatabaseComplianceOverview.mockResolvedValue([true, overview]);
    service().getDatabaseBindings.mockResolvedValue(bindings(null));
    service().fetchComplianceSchemas.mockResolvedValue([
        true,
        [
            { schemaName: "std", schemaBody: {} },
            { schemaName: "strict", schemaBody: {} },
        ],
    ]);
});

afterEach(() => jest.restoreAllMocks());

describe(`${Synonyms.Database} compliance schema binding control`, () => {
    it("is not offered to a caller who may not bind or unbind", async () => {
        mockAllowed = VIEW;
        await mount();
        expect(screen.queryByText("Schema Binding")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Bind" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
        expect(service().fetchComplianceSchemas).not.toHaveBeenCalled();
    });

    it("offers only the schemas this database may bind, and says so", async () => {
        mockAllowed = COMPLIANCE_ADMIN;
        await mount();
        // GLOBAL schemas and those available to this database: the listing is asked for db1's.
        await waitFor(() => expect(service().fetchComplianceSchemas).toHaveBeenCalledWith("db1"));
        expect(
            screen.getByText(/Offered: the GLOBAL schemas and those available to this/)
        ).toBeInTheDocument();
    });

    it("binds the selected schema and re-reads the binding and the overview", async () => {
        mockAllowed = COMPLIANCE_ADMIN;
        service().bindSchemaToDatabase.mockResolvedValue([true, "Schema bound"]);
        await mount();
        await waitFor(() => expect(service().fetchComplianceSchemas).toHaveBeenCalled());

        const bind = screen.getByRole("button", { name: "Bind" });
        expect(bind).toBeDisabled();
        service().getDatabaseBindings.mockResolvedValue(bindings("std"));
        choose("std");
        expect(bind).toBeEnabled();
        fireEvent.click(bind);

        expect(
            await screen.findByText(`Compliance schema std bound to this ${Synonyms.database}.`)
        ).toBeInTheDocument();
        expect(service().bindSchemaToDatabase).toHaveBeenCalledWith("db1", "std");
        await waitFor(() => expect(service().getDatabaseBindings).toHaveBeenCalledTimes(2));
        expect(service().fetchDatabaseComplianceOverview).toHaveBeenCalledTimes(2);
        expect(await screen.findByText("Schema: std")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Change" })).toBeInTheDocument();
    });

    it("changes a bound schema to another one", async () => {
        mockAllowed = COMPLIANCE_ADMIN;
        service().getDatabaseBindings.mockResolvedValue(bindings("std"));
        service().bindSchemaToDatabase.mockResolvedValue([true, "Schema bound"]);
        await mount();
        await screen.findByText("Schema: std");

        const change = screen.getByRole("button", { name: "Change" });
        // The bound schema is preselected, so there is nothing to change yet.
        expect(change).toBeDisabled();
        choose("strict");
        fireEvent.click(change);

        await waitFor(() =>
            expect(service().bindSchemaToDatabase).toHaveBeenCalledWith("db1", "strict")
        );
        expect(service().unbindSchemaFromDatabase).not.toHaveBeenCalled();
    });

    it("removes the binding only after confirmation", async () => {
        mockAllowed = COMPLIANCE_ADMIN;
        service().getDatabaseBindings.mockResolvedValue(bindings("std"));
        service().unbindSchemaFromDatabase.mockResolvedValue([true, "Schema unbound"]);
        await mount();
        await screen.findByText("Schema: std");

        fireEvent.click(screen.getByRole("button", { name: "Remove" }));
        expect(service().unbindSchemaFromDatabase).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        expect(service().unbindSchemaFromDatabase).not.toHaveBeenCalled();

        fireEvent.click(screen.getByRole("button", { name: "Remove" }));
        service().getDatabaseBindings.mockResolvedValue(bindings(null));
        fireEvent.click(screen.getByRole("button", { name: "Remove binding" }));

        expect(
            await screen.findByText(`Compliance schema std removed from this ${Synonyms.database}.`)
        ).toBeInTheDocument();
        expect(service().unbindSchemaFromDatabase).toHaveBeenCalledWith("db1");
        expect(await screen.findByText("No compliance schema bound")).toBeInTheDocument();
    });

    it("shows a refused bind on the page and keeps the binding as it was", async () => {
        mockAllowed = COMPLIANCE_ADMIN;
        service().bindSchemaToDatabase.mockResolvedValue([false, "Not Authorized"]);
        await mount();
        await waitFor(() => expect(service().fetchComplianceSchemas).toHaveBeenCalled());

        choose("std");
        fireEvent.click(screen.getByRole("button", { name: "Bind" }));

        expect(
            await screen.findByText("The compliance schema binding failed: Not Authorized")
        ).toBeInTheDocument();
        expect(service().fetchDatabaseComplianceOverview).toHaveBeenCalledTimes(1);
        expect(screen.getByText("No compliance schema bound")).toBeInTheDocument();
    });

    it("shows a refused removal on the page", async () => {
        mockAllowed = COMPLIANCE_ADMIN;
        service().getDatabaseBindings.mockResolvedValue(bindings("std"));
        service().unbindSchemaFromDatabase.mockResolvedValue([false, "Not Authorized"]);
        await mount();
        await screen.findByText("Schema: std");

        fireEvent.click(screen.getByRole("button", { name: "Remove" }));
        fireEvent.click(screen.getByRole("button", { name: "Remove binding" }));

        expect(
            await screen.findByText("Removing the compliance schema binding failed: Not Authorized")
        ).toBeInTheDocument();
        expect(screen.getByText("Schema: std")).toBeInTheDocument();
    });

    it("offers no Remove to a caller who may bind but not unbind", async () => {
        mockAllowed = [...VIEW, ...BIND];
        service().getDatabaseBindings.mockResolvedValue(bindings("std"));
        await mount();
        await screen.findByText("Schema: std");
        expect(screen.getByRole("button", { name: "Change" })).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    });
});
