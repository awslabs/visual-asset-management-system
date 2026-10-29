/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Update Asset outcome. `updateAsset` resolves `[false, message]` for every rejected request (a
 * validation failure, a permission denial, a request the AWS WAF web ACL blocked) instead of
 * throwing, so the modal reads the result: a failure shows the error status with the message and does
 * not complete, and only an accepted update completes.
 *
 * The asset save and the compliance schema binding are separate requests with separate permissions.
 * The modal sends the save only when an asset field changed and the binding only when the schema
 * selection changed, sends no binding after a refused save, reports a refused binding and stays open,
 * and completes only when every request it sent succeeded.
 */
import React from "react";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { MemoryRouter } from "react-router-dom";
import { UpdateAsset } from "./UpdateAsset";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({
    fetchTagsForAsset: jest.fn(() => Promise.resolve([])),
    fetchTagTypesForAsset: jest.fn(() => Promise.resolve([])),
    updateAsset: jest.fn(),
}));
jest.mock("../../services/ComplianceService", () => ({
    bindableSchemas: jest.requireActual("../../services/ComplianceService").bindableSchemas,
    liveDatabaseId: jest.requireActual("../../services/ComplianceService").liveDatabaseId,
    fetchComplianceSchemas: jest.fn(),
    fetchComplianceState: jest.fn(),
    bindSchemaToAsset: jest.fn(),
    unbindSchemaFromAsset: jest.fn(),
}));
// The compliance schema field gates on this hook. The outcome tests deny it to stay on the save; the
// binding tests allow the schema list and the asset binding route.
let mockCanBind = false;
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => mockCanBind }),
}));

const { updateAsset } = jest.requireMock("../../services/APIService");
const compliance = jest.requireMock("../../services/ComplianceService");

const BLOCKED = "Request blocked by the deployment's AWS WAF web ACL (request ID r-1)";

const asset = {
    assetId: "asset1",
    assetName: "model",
    databaseId: "db1",
    description: "a model",
    isDistributable: true,
    tags: [],
};

const mount = () => {
    const onComplete = jest.fn();
    const onClose = jest.fn();
    render(
        <MemoryRouter>
            <UpdateAsset asset={asset} isOpen={true} onClose={onClose} onComplete={onComplete} />
        </MemoryRouter>
    );
    return { onComplete, onClose };
};

const submit = () =>
    fireEvent.click(screen.getByRole("button", { name: `Update ${Synonyms.Asset}` }));

const formField = (label: string) => {
    const field = createWrapper()
        .findAllFormFields()
        .find((f) => f.findLabel()?.getElement().textContent === label);
    if (!field) throw new Error(`no ${label} field`);
    return field;
};

function present<T>(value: T | null | undefined, what: string): T {
    if (value === null || value === undefined) throw new Error(`no ${what}`);
    return value;
}

const editDescription = (value: string) => {
    const field = formField(`${Synonyms.Asset} Description`);
    const input = present(field.findControl()?.findInput(), "description input");
    act(() => {
        input.setInputValue(value);
    });
};

// Options of the schema select: 1 is "Inherit", then the schemas in list order.
const chooseSchema = (index: number) => {
    const select = present(
        formField("Compliance Schema").findControl()?.findSelect(),
        "schema select"
    );
    act(() => {
        select.openDropdown();
    });
    act(() => {
        select.selectOption(index);
    });
};

const schemaOptionsLoaded = () =>
    waitFor(() => expect(compliance.fetchComplianceSchemas).toHaveBeenCalled());

beforeEach(() => {
    jest.clearAllMocks();
    mockCanBind = false;
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    compliance.fetchComplianceSchemas.mockResolvedValue([
        true,
        [
            { schemaName: "std", schemaBody: {} },
            { schemaName: "strict", schemaBody: {} },
        ],
    ]);
    // No asset-level override: the asset inherits the database schema.
    compliance.fetchComplianceState.mockResolvedValue([
        true,
        { schemaSource: "database", schemaName: "db-schema" },
    ]);
});

afterEach(() => jest.restoreAllMocks());

describe("Update Asset outcome", () => {
    it("completes an update the API accepted", async () => {
        updateAsset.mockResolvedValue([true, "Asset updated successfully"]);
        const { onComplete } = mount();
        editDescription("a revised model");
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(updateAsset).toHaveBeenCalledWith(
            expect.objectContaining({ databaseId: "db1", assetId: "asset1" })
        );
    });

    it("shows a rejected update's message and does not complete", async () => {
        updateAsset.mockResolvedValue([false, BLOCKED]);
        const { onComplete } = mount();
        editDescription("a revised model");
        submit();
        expect(await screen.findByText(BLOCKED)).toBeInTheDocument();
        expect(screen.queryByText("Update complete")).not.toBeInTheDocument();
        expect(onComplete).not.toHaveBeenCalled();
    });

    it("clears a rejected update's message when the modal is closed and opened again", async () => {
        updateAsset.mockResolvedValue([false, BLOCKED]);
        const onClose = jest.fn();
        const modal = (isOpen: boolean) => (
            <MemoryRouter>
                <UpdateAsset
                    asset={asset}
                    isOpen={isOpen}
                    onClose={onClose}
                    onComplete={jest.fn()}
                />
            </MemoryRouter>
        );
        const { rerender } = render(modal(true));
        editDescription("a revised model");
        submit();
        expect(await screen.findByText(BLOCKED)).toBeInTheDocument();
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        expect(onClose).toHaveBeenCalled();
        rerender(modal(false));
        rerender(modal(true));
        expect(screen.queryByText(BLOCKED)).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: `Update ${Synonyms.Asset}` })).toBeEnabled();
    });

    it("lets the user retry after a rejected update", async () => {
        updateAsset
            .mockResolvedValueOnce([false, "Asset name is invalid"])
            .mockResolvedValueOnce([true, "Asset updated successfully"]);
        const { onComplete } = mount();
        editDescription("a revised model");
        submit();
        expect(await screen.findByText("Asset name is invalid")).toBeInTheDocument();
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(screen.queryByText("Asset name is invalid")).not.toBeInTheDocument();
    });

    it("closes without sending anything when nothing changed", async () => {
        const { onComplete, onClose } = mount();
        submit();
        await waitFor(() => expect(onClose).toHaveBeenCalled());
        expect(updateAsset).not.toHaveBeenCalled();
        expect(onComplete).not.toHaveBeenCalled();
    });
});

describe("Update Asset save and compliance schema binding", () => {
    beforeEach(() => {
        mockCanBind = true;
    });

    it("sends only the binding for a schema-only change", async () => {
        compliance.bindSchemaToAsset.mockResolvedValue([true, "Schema bound to asset"]);
        const { onComplete } = mount();
        await schemaOptionsLoaded();
        chooseSchema(2);
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(compliance.bindSchemaToAsset).toHaveBeenCalledWith("db1", "asset1", "std");
        expect(updateAsset).not.toHaveBeenCalled();
    });

    it("sends only the save for a field-only change", async () => {
        updateAsset.mockResolvedValue([true, "Asset updated successfully"]);
        const { onComplete } = mount();
        await schemaOptionsLoaded();
        editDescription("a revised model");
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(updateAsset).toHaveBeenCalledTimes(1);
        expect(compliance.bindSchemaToAsset).not.toHaveBeenCalled();
        expect(compliance.unbindSchemaFromAsset).not.toHaveBeenCalled();
    });

    it("sends no binding after a refused save and shows the save's error", async () => {
        updateAsset.mockResolvedValue([false, BLOCKED]);
        const { onComplete } = mount();
        await schemaOptionsLoaded();
        editDescription("a revised model");
        chooseSchema(2);
        submit();
        expect(await screen.findByText(BLOCKED)).toBeInTheDocument();
        expect(compliance.bindSchemaToAsset).not.toHaveBeenCalled();
        expect(onComplete).not.toHaveBeenCalled();
    });

    it("reports a refused binding after a save and stays open", async () => {
        updateAsset.mockResolvedValue([true, "Asset updated successfully"]);
        compliance.bindSchemaToAsset.mockResolvedValue([false, "Not Authorized"]);
        const { onComplete, onClose } = mount();
        await schemaOptionsLoaded();
        editDescription("a revised model");
        chooseSchema(2);
        submit();
        expect(
            await screen.findByText(
                `${Synonyms.Asset} saved, but the compliance schema binding failed: Not Authorized`
            )
        ).toBeInTheDocument();
        expect(onComplete).not.toHaveBeenCalled();
        expect(onClose).not.toHaveBeenCalled();

        // A retry resends only the binding: the save already landed.
        compliance.bindSchemaToAsset.mockResolvedValue([true, "Schema bound to asset"]);
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(updateAsset).toHaveBeenCalledTimes(1);
        expect(compliance.bindSchemaToAsset).toHaveBeenCalledTimes(2);
    });

    it("reports a refused schema-only binding without claiming a save", async () => {
        compliance.bindSchemaToAsset.mockResolvedValue([false, "Not Authorized"]);
        const { onComplete } = mount();
        await schemaOptionsLoaded();
        chooseSchema(2);
        submit();
        expect(
            await screen.findByText("The compliance schema binding failed: Not Authorized")
        ).toBeInTheDocument();
        expect(updateAsset).not.toHaveBeenCalled();
        expect(onComplete).not.toHaveBeenCalled();
    });

    it("refreshes the view when closed after a save whose binding was refused", async () => {
        updateAsset.mockResolvedValue([true, "Asset updated successfully"]);
        compliance.bindSchemaToAsset.mockResolvedValue([false, "Not Authorized"]);
        const { onComplete } = mount();
        await schemaOptionsLoaded();
        editDescription("a revised model");
        chooseSchema(2);
        submit();
        await screen.findByText(/compliance schema binding failed/);
        fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
        expect(onComplete).toHaveBeenCalled();
    });

    it("sends the save then the binding and completes when both succeed", async () => {
        updateAsset.mockResolvedValue([true, "Asset updated successfully"]);
        compliance.bindSchemaToAsset.mockResolvedValue([true, "Schema bound to asset"]);
        const { onComplete } = mount();
        await schemaOptionsLoaded();
        editDescription("a revised model");
        chooseSchema(3);
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(updateAsset).toHaveBeenCalledTimes(1);
        expect(compliance.bindSchemaToAsset).toHaveBeenCalledWith("db1", "asset1", "strict");
        expect(updateAsset.mock.invocationCallOrder[0]).toBeLessThan(
            compliance.bindSchemaToAsset.mock.invocationCallOrder[0]
        );
    });

    it("removes an asset-level override when Inherit is chosen", async () => {
        compliance.fetchComplianceState.mockResolvedValue([
            true,
            { schemaSource: "asset", schemaName: "std" },
        ]);
        compliance.unbindSchemaFromAsset.mockResolvedValue([true, "Schema unbound from asset"]);
        const { onComplete } = mount();
        await waitFor(() => expect(compliance.fetchComplianceState).toHaveBeenCalled());
        await schemaOptionsLoaded();
        chooseSchema(1);
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(compliance.unbindSchemaFromAsset).toHaveBeenCalledWith("db1", "asset1");
        expect(updateAsset).not.toHaveBeenCalled();
    });
});

describe("Update Asset of an archived asset", () => {
    // The asset routes return an archived asset's record under `<databaseId>#deleted`; its
    // compliance records keep the live id, and a compliance route refuses the archived form.
    const archivedAsset = { ...asset, databaseId: "db1#deleted" };

    const mountArchived = () =>
        render(
            <MemoryRouter>
                <UpdateAsset
                    asset={archivedAsset}
                    isOpen={false}
                    onClose={jest.fn()}
                    onComplete={jest.fn()}
                />
            </MemoryRouter>
        );

    const requestedDatabaseIds = () => [
        ...compliance.fetchComplianceSchemas.mock.calls.map((call: any[]) => call[0]),
        ...compliance.fetchComplianceState.mock.calls.map((call: any[]) => call[0]),
        ...compliance.bindSchemaToAsset.mock.calls.map((call: any[]) => call[0]),
        ...compliance.unbindSchemaFromAsset.mock.calls.map((call: any[]) => call[0]),
    ];

    beforeEach(() => {
        mockCanBind = true;
    });

    it("asks for the schemas and the binding with the live database id on page load", async () => {
        // The page mounts the editor closed, so these requests go out when the page opens.
        mountArchived();
        await schemaOptionsLoaded();
        await waitFor(() => expect(compliance.fetchComplianceState).toHaveBeenCalled());
        expect(compliance.fetchComplianceSchemas).toHaveBeenCalledWith("db1");
        expect(compliance.fetchComplianceState).toHaveBeenCalledWith("db1", "asset1");
        expect(requestedDatabaseIds()).not.toContain("db1#deleted");
    });

    it("binds a schema with the live database id", async () => {
        compliance.bindSchemaToAsset.mockResolvedValue([true, "Schema bound to asset"]);
        const onComplete = jest.fn();
        render(
            <MemoryRouter>
                <UpdateAsset
                    asset={archivedAsset}
                    isOpen={true}
                    onClose={jest.fn()}
                    onComplete={onComplete}
                />
            </MemoryRouter>
        );
        await schemaOptionsLoaded();
        chooseSchema(2);
        submit();
        await waitFor(() => expect(onComplete).toHaveBeenCalled());
        expect(compliance.bindSchemaToAsset).toHaveBeenCalledWith("db1", "asset1", "std");
        expect(requestedDatabaseIds()).not.toContain("db1#deleted");
    });
});
