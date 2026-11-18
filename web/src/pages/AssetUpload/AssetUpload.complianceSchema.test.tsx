/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Create Asset form's compliance schema field: it offers "Inherit from database" beside the
 * schemas, says when the schemas could not be read, drops the choice when the database changes,
 * and the bind sent after the upload reports a refusal instead of dropping it.
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
