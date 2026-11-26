/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A version's file list opens and downloads each file by its path in the asset (#1943).
 *
 * A snapshot names a file by its path without the leading "/". The API reads a value without one as the
 * file's full key when the asset holds it, so the snapshot path "A1/cloud.las" of an asset stored at "A1/"
 * named the root file "A1/cloud.las" instead of the file in the subfolder "A1/". "View File" and "Download
 * File" now send the path with its leading "/".
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { FileVersionsList } from "./FileVersionsList";
import { AssetVersionContext } from "../AssetVersionManager";
import { downloadAsset } from "../../../../services/APIService";

jest.mock("../AssetVersionManager", () => {
    const { createContext } = jest.requireActual("react");
    return { AssetVersionContext: createContext(null) };
});
jest.mock("../../../../services/APIService", () => ({ downloadAsset: jest.fn() }));
jest.mock("../../../filemanager/modals/FileViewerModal", () => () => null);
jest.mock("../../../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => false,
}));
jest.mock("../../../../visualizerPlugin/core/viewableExtensions", () => ({
    isFilenameComparableAsVersions: () => false,
}));

const file = {
    relativeKey: "A1/cloud.las",
    versionId: "v-sub",
    isArchived: false,
    exists: true,
    isPermanentlyDeleted: false,
    size: 10,
    lastModified: "2026-01-02T00:00:00Z",
};

const context = {
    loading: false,
    selectedVersion: { Version: "2" },
    selectedVersionDetails: { files: [file], versionedMetadata: [] },
    fileFilterText: "",
    setFileFilterText: jest.fn(),
    fileCurrentPage: 1,
    setFileCurrentPage: jest.fn(),
    filePageSize: 10,
    setFilePageSize: jest.fn(),
    filteredFiles: [file],
    totalFiles: 1,
} as any;

const FilePageProbe: React.FC = () => {
    const location = useLocation();
    return (
        <div data-testid="file-page">
            {JSON.stringify({ pathname: location.pathname, key: (location.state as any)?.key })}
        </div>
    );
};

function renderList() {
    return render(
        <MemoryRouter initialEntries={["/databases/db1/assets/A1/versions"]}>
            <AssetVersionContext.Provider value={context}>
                <Routes>
                    <Route
                        path="/databases/:databaseId/assets/:assetId/versions"
                        element={<FileVersionsList />}
                    />
                    <Route
                        path="/databases/:databaseId/assets/:assetId/file/*"
                        element={<FilePageProbe />}
                    />
                </Routes>
            </AssetVersionContext.Provider>
        </MemoryRouter>
    );
}

describe("FileVersionsList: a snapshot file is named by its path in the asset", () => {
    beforeEach(() => jest.clearAllMocks());

    it("View File opens the subfolder file", async () => {
        renderList();
        await userEvent.click(await screen.findByRole("button", { name: "View File" }));
        const page = JSON.parse((await screen.findByTestId("file-page")).textContent || "{}");
        expect(page.pathname).toBe("/databases/db1/assets/A1/file/%2FA1%2Fcloud.las");
        expect(page.key).toBe("/A1/cloud.las");
    });

    it("Download File asks for the subfolder file", async () => {
        (downloadAsset as jest.Mock).mockResolvedValue([false, "stop here"]);
        renderList();
        await userEvent.click(await screen.findByRole("button", { name: "Download File" }));
        await waitFor(() => expect(downloadAsset).toHaveBeenCalled());
        expect((downloadAsset as jest.Mock).mock.calls[0][0].key).toBe("/A1/cloud.las");
    });
});
