/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The create-asset form opened at `/upload/:databaseId` (the search page's create action inside a
 * database) preselects that database, with the upload restrictions the database selector carries,
 * once the database record loads. A database the caller cannot read, a choice the form already
 * holds, a database the user picks while the record loads, or a form closed before it loads, leaves
 * the selection alone.
 */
import React from "react";
import { render, waitFor, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AssetPrimaryInfo, AssetDetailContext } from "./AssetUpload";

jest.mock("../../services/APIService", () => ({
    fetchTagTypesForAsset: jest.fn(() => Promise.resolve([])),
    fetchTagsForAsset: jest.fn(() => Promise.resolve([])),
    fetchAllDatabases: jest.fn().mockResolvedValue([]),
    fetchDatabase: jest.fn(),
}));
jest.mock("../../components/metadataV2", () => ({ MetadataContainer: () => null }));
jest.mock("../../components/selectors/DatabaseSelector", () => ({
    __esModule: true,
    default: ({ onChange }: any) =>
        jest.requireActual<typeof import("react")>("react").createElement(
            "button",
            {
                onClick: () =>
                    onChange({
                        detail: {
                            selectedOption: { value: "db-user" },
                            selectedDatabase: { restrictFileUploadsToExtensions: ".obj" },
                        },
                    }),
            },
            "choose database"
        ),
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

const { fetchDatabase } = jest.requireMock("../../services/APIService");

const RECORD = {
    databaseId: "db-r",
    restrictMetadataOutsideSchemas: true,
    restrictFileUploadsToExtensions: ".glb,.gltf",
};

const mount = (path: string, databaseId?: string) => {
    const dispatch = jest.fn();
    const step = (
        <AssetDetailContext.Provider
            value={{
                assetDetailState: { isMultiFile: false, isDistributable: true, databaseId } as any,
                assetDetailDispatch: dispatch,
            }}
        >
            <AssetPrimaryInfo setValid={() => undefined} showErrors={false} />
        </AssetDetailContext.Provider>
    );
    const { unmount } = render(
        <MemoryRouter initialEntries={[path]}>
            <Routes>
                <Route path="/upload/:databaseId" element={step} />
                <Route path="/upload" element={step} />
            </Routes>
        </MemoryRouter>
    );
    return { dispatch, unmount };
};

/** A fetchDatabase reply the test releases itself. */
const pendingRecord = () => {
    let resolve: (value: unknown) => void = () => undefined;
    fetchDatabase.mockReturnValue(new Promise((r) => (resolve = r)));
    return (value: unknown) => resolve(value);
};

const settle = () => new Promise((resolve) => setTimeout(resolve, 20));

const databaseActions = (dispatch: jest.Mock) =>
    dispatch.mock.calls.map(([action]) => action).filter((a) => a.type === "UPDATE_ASSET_DATABASE");

beforeEach(() => {
    jest.clearAllMocks();
});

describe("create-asset form route database", () => {
    it("preselects the route's database with its upload restrictions", async () => {
        fetchDatabase.mockResolvedValue(RECORD);
        const { dispatch } = mount("/upload/db-r");
        await waitFor(() => expect(databaseActions(dispatch)).toHaveLength(1));
        expect(fetchDatabase).toHaveBeenCalledWith({ databaseId: "db-r" });
        expect(databaseActions(dispatch)[0]).toEqual({
            type: "UPDATE_ASSET_DATABASE",
            payload: {
                databaseId: "db-r",
                restrictMetadataOutsideSchemas: true,
                restrictFileUploadsToExtensions: ".glb,.gltf",
            },
        });
    });

    it("requests no database on the plain upload route", async () => {
        const { dispatch } = mount("/upload");
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(fetchDatabase).not.toHaveBeenCalled();
        expect(databaseActions(dispatch)).toHaveLength(0);
    });

    it("keeps a database the form already holds", async () => {
        fetchDatabase.mockResolvedValue(RECORD);
        const { dispatch } = mount("/upload/db-r", "db-chosen");
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(fetchDatabase).not.toHaveBeenCalled();
        expect(databaseActions(dispatch)).toHaveLength(0);
    });

    it("leaves the choice to the user when the database cannot be read", async () => {
        // fetchDatabase resolves an error message string for a denied or missing database.
        fetchDatabase.mockResolvedValue("Not Authorized");
        const { dispatch } = mount("/upload/db-r");
        await waitFor(() => expect(fetchDatabase).toHaveBeenCalled());
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(databaseActions(dispatch)).toHaveLength(0);
    });

    it("ignores a record for another database", async () => {
        fetchDatabase.mockResolvedValue({ ...RECORD, databaseId: "db-other" });
        const { dispatch } = mount("/upload/db-r");
        await waitFor(() => expect(fetchDatabase).toHaveBeenCalled());
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(databaseActions(dispatch)).toHaveLength(0);
    });

    it("keeps the database the user picks while the route's record loads", async () => {
        const release = pendingRecord();
        const { dispatch } = mount("/upload/db-r");
        await waitFor(() => expect(fetchDatabase).toHaveBeenCalled());
        fireEvent.click(screen.getByText("choose database"));
        release(RECORD);
        await settle();
        expect(databaseActions(dispatch).map((a) => a.payload.databaseId)).toEqual(["db-user"]);
    });

    it("dispatches nothing when the form is closed before the record loads", async () => {
        const release = pendingRecord();
        const { dispatch, unmount } = mount("/upload/db-r");
        await waitFor(() => expect(fetchDatabase).toHaveBeenCalled());
        unmount();
        release(RECORD);
        await settle();
        expect(databaseActions(dispatch)).toHaveLength(0);
    });
});
