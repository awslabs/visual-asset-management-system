/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Create Asset form's compliance schema field: it offers "Inherit from database" beside the
 * schemas, says when the schemas could not be read, offers nothing before a database is chosen, drops
 * the choice when the database changes, and the bind sent after the upload reports a refusal instead
 * of dropping it.
 */
import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import {
    AssetPrimaryInfo,
    AssetDetailContext,
    assetDetailReducer,
    bindComplianceSchemaAfterCreate,
} from "./AssetUpload";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({
    fetchTagTypesForAsset: jest.fn(() => Promise.resolve([])),
    fetchTagsForAsset: jest.fn(() => Promise.resolve([])),
    fetchAllDatabases: jest.fn().mockResolvedValue([]),
    fetchDatabase: jest.fn(),
}));
jest.mock("../../services/ComplianceService", () => ({
    fetchComplianceSchemas: jest.fn(),
    bindableSchemas: (schemas: any[]) => schemas,
    bindSchemaToAsset: jest.fn(),
}));
jest.mock("../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: () => true }),
}));
jest.mock("../../components/metadataV2", () => ({ MetadataContainer: () => null }));
jest.mock("../../components/selectors/DatabaseSelector", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../../components/asset/tabs/AssetLinksTab", () => ({ AssetLinksTab: () => null }));
jest.mock("./AssetUploadWorkflow", () => ({ __esModule: true, default: () => null }));
jest.mock("./onSubmit", () => ({
    __esModule: true,
    default: () => () => undefined,
    onUploadRetry: jest.fn(),
}));
jest.mock("localforage", () => ({
    setItem: jest.fn().mockResolvedValue(undefined),
    getItem: jest.fn().mockResolvedValue(null),
}));

const { fetchComplianceSchemas, bindSchemaToAsset } = jest.requireMock(
    "../../services/ComplianceService"
);
const { fetchDatabase } = jest.requireMock("../../services/APIService");

const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

const mount = (state: Record<string, unknown>) =>
    render(
        <MemoryRouter initialEntries={["/upload"]}>
            <AssetDetailContext.Provider
                value={{
                    assetDetailState: {
                        isMultiFile: false,
                        isDistributable: true,
                        ...state,
                    } as any,
                    assetDetailDispatch: jest.fn(),
                }}
            >
                <AssetPrimaryInfo setValid={() => undefined} showErrors={false} />
            </AssetDetailContext.Provider>
        </MemoryRouter>
    );

const schemaSelect = () =>
    createWrapper(document.body).findSelect('[data-testid="asset-compliance-schema"]')!;

beforeEach(() => {
    jest.clearAllMocks();
});

describe("Create Asset compliance schema field", () => {
    it("offers Inherit from the database beside the schemas", async () => {
        fetchComplianceSchemas.mockResolvedValue([true, [{ schemaName: "s1" }]]);
        mount({ databaseId: "db1" });
        await waitFor(() => expect(fetchComplianceSchemas).toHaveBeenCalledWith("db1"));
        schemaSelect().openDropdown();
        const labels = schemaSelect()
            .findDropdown()
            .findOptions()
            .map((option) => option.getElement().textContent);
        expect(labels[0]).toContain(`Inherit from ${Synonyms.database}`);
        expect(labels.some((label) => label?.includes("s1"))).toBe(true);
    });

    it("says the schemas could not be loaded when their read fails", async () => {
        fetchComplianceSchemas.mockResolvedValue([false, "Not Authorized"]);
        mount({ databaseId: "db1" });
        expect(
            await screen.findByText("Could not load the compliance schemas: Not Authorized")
        ).toBeInTheDocument();
    });

    it("shows the chosen schema from the form", async () => {
        fetchComplianceSchemas.mockResolvedValue([true, [{ schemaName: "s1" }]]);
        mount({ databaseId: "db1", complianceSchemaName: "s1" });
        await waitFor(() =>
            expect(schemaSelect().findTrigger().getElement()).toHaveTextContent("s1")
        );
    });

    it("offers no schema before a database is chosen", async () => {
        fetchComplianceSchemas.mockResolvedValue([true, [{ schemaName: "s1" }]]);
        mount({});
        await settle();
        expect(schemaSelect().findTrigger().getElement()).toBeDisabled();
        expect(schemaSelect().findTrigger().getElement()).toHaveTextContent(
            `Choose a ${Synonyms.database} first`
        );
        expect(fetchComplianceSchemas).not.toHaveBeenCalled();
    });
});

describe("Create Asset compliance schema field at /upload/:databaseId", () => {
    /** The form's own reducer behind the field, so the database and schema choices are real state. */
    const Form = ({ onState }: { onState: (state: any) => void }) => {
        const [state, dispatch] = React.useReducer(assetDetailReducer, {
            isMultiFile: false,
            isDistributable: true,
        } as any);
        onState(state);
        return (
            <AssetDetailContext.Provider
                value={{ assetDetailState: state, assetDetailDispatch: dispatch }}
            >
                <AssetPrimaryInfo setValid={() => undefined} showErrors={false} />
            </AssetDetailContext.Provider>
        );
    };

    it("is offered once the route's database has loaded, and the schema picked then is kept", async () => {
        let release: (value: unknown) => void = () => undefined;
        fetchDatabase.mockReturnValue(new Promise((resolve) => (release = resolve)));
        fetchComplianceSchemas.mockResolvedValue([true, [{ schemaName: "s1" }]]);
        let latest: any = null;
        render(
            <MemoryRouter initialEntries={["/upload/db-r"]}>
                <Routes>
                    <Route
                        path="/upload/:databaseId"
                        element={<Form onState={(state) => (latest = state)} />}
                    />
                </Routes>
            </MemoryRouter>
        );

        // While the route's database record loads there is no schema to choose.
        await settle();
        expect(schemaSelect().findTrigger().getElement()).toBeDisabled();
        expect(fetchComplianceSchemas).not.toHaveBeenCalled();

        await act(async () => {
            release({ databaseId: "db-r" });
            await settle();
        });
        await waitFor(() => expect(fetchComplianceSchemas).toHaveBeenCalledWith("db-r"));
        await waitFor(() => expect(schemaSelect().findTrigger().getElement()).toBeEnabled());

        schemaSelect().openDropdown();
        await waitFor(() =>
            expect(schemaSelect().findDropdown().findOptions().length).toBeGreaterThan(1)
        );
        act(() => {
            schemaSelect().selectOptionByValue("s1");
        });

        expect(latest.databaseId).toBe("db-r");
        expect(latest.complianceSchemaName).toBe("s1");
    });
});

describe("Create Asset compliance schema choice", () => {
    it("drops the chosen schema when the database changes", () => {
        const state = { databaseId: "db1", complianceSchemaName: "s1" } as any;
        const next = assetDetailReducer(state, {
            type: "UPDATE_ASSET_DATABASE",
            payload: { databaseId: "db2" },
        } as any);
        expect(next.databaseId).toBe("db2");
        expect(next.complianceSchemaName).toBeUndefined();
    });

    it("keeps the chosen schema when the same database is set again", () => {
        const state = { databaseId: "db1", complianceSchemaName: "s1" } as any;
        const next = assetDetailReducer(state, {
            type: "UPDATE_ASSET_DATABASE",
            payload: { databaseId: "db1" },
        } as any);
        expect(next.complianceSchemaName).toBe("s1");
    });
});

describe("compliance schema bind after Create Asset", () => {
    it("reports a refused bind", async () => {
        bindSchemaToAsset.mockResolvedValue([false, "Database is still being moved"]);
        await expect(
            bindComplianceSchemaAfterCreate(
                { databaseId: "db1", complianceSchemaName: "s1" } as any,
                "a1"
            )
        ).resolves.toBe(
            `${Synonyms.Asset} created; the compliance schema was not bound: Database is still being moved`
        );
        expect(bindSchemaToAsset).toHaveBeenCalledWith("db1", "a1", "s1");
    });

    it("reports nothing when the bind succeeds", async () => {
        bindSchemaToAsset.mockResolvedValue([true, "Schema bound to asset"]);
        await expect(
            bindComplianceSchemaAfterCreate(
                { databaseId: "db1", complianceSchemaName: "s1" } as any,
                "a1"
            )
        ).resolves.toBeNull();
    });

    it("binds nothing when the asset inherits", async () => {
        await expect(
            bindComplianceSchemaAfterCreate({ databaseId: "db1" } as any, "a1")
        ).resolves.toBeNull();
        expect(bindSchemaToAsset).not.toHaveBeenCalled();
    });
});
