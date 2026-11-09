/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Moving from one asset page to another inside the app keeps the asset page mounted. The file
 * manager must then act on the asset the route names: its listing and the asset its details panel
 * downloads from both follow the route.
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import * as APIService from "../../../services/APIService";
import { FileManagerTab } from "./FileManagerTab";
import { AssetDetailContext } from "../../../context/AssetDetailContext";

jest.mock("../../../services/APIService", () => ({
    fetchAssetS3FilesStreaming: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
}));

const mockDownloadFile = jest.fn();

// Stands in for the details panel's Download: it reads the same file manager state the panel
// passes to downloadFile.
jest.mock("../../filemanager/components/FileDetailsPanel", () => ({
    FileDetailsPanel: function MockFileDetailsPanel() {
        const { useContext: useCtx } = jest.requireActual("react");
        const { FileManagerContext: Ctx } = jest.requireActual(
            "../../filemanager/components/FileTreeView"
        );
        const { state } = useCtx(Ctx);
        const item = state.selectedItem;
        return item && item.relativePath !== "/" ? (
            <button
                type="button"
                onClick={() =>
                    mockDownloadFile(
                        state.assetId,
                        state.databaseId,
                        item.keyPrefix,
                        item.versionId
                    )
                }
            >
                Download
            </button>
        ) : null;
    },
}));
jest.mock("../../filemanager/modals/AssetPreviewModal", () => ({
    __esModule: true,
    default: () => null,
}));

const fetchAssetS3FilesStreaming = APIService.fetchAssetS3FilesStreaming as jest.Mock;

const fileFor = (assetId: string) => ({
    fileName: "x.txt",
    key: `${assetId}/x.txt`,
    relativePath: "/x.txt",
    isFolder: false,
    size: 10,
    dateCreatedCurrentVersion: "2026-01-01T00:00:00Z",
    versionId: `v-${assetId}`,
    isArchived: false,
    primaryType: null,
});

function Page() {
    const navigate = useNavigate();
    return (
        <AssetDetailContext.Provider value={{ state: {} as any, dispatch: jest.fn() }}>
            <button type="button" onClick={() => navigate("/databases/db1/assets/assetB")}>
                open asset B
            </button>
            <FileManagerTab assetName="Asset" />
        </AssetDetailContext.Provider>
    );
}

describe("file manager after in-app navigation to another asset", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        fetchAssetS3FilesStreaming.mockImplementation(async function* ({ assetId }: any) {
            yield {
                success: true,
                items: [fileFor(assetId)],
                nextToken: null,
                error: null,
                pageNumber: 1,
                isLastPage: true,
            };
        });
    });

    it("downloads the file of the asset the route names", async () => {
        render(
            <MemoryRouter initialEntries={["/databases/db1/assets/assetA"]}>
                <Routes>
                    <Route path="/databases/:databaseId/assets/:assetId" element={<Page />} />
                </Routes>
            </MemoryRouter>
        );

        await waitFor(() => expect(screen.getByText("x.txt")).toBeInTheDocument());
        fireEvent.click(screen.getByText("x.txt"));
        fireEvent.click(await screen.findByRole("button", { name: "Download" }));
        expect(mockDownloadFile).toHaveBeenLastCalledWith(
            "assetA",
            "db1",
            "assetA/x.txt",
            "v-assetA"
        );

        fireEvent.click(screen.getByRole("button", { name: "open asset B" }));
        await waitFor(() =>
            expect(fetchAssetS3FilesStreaming).toHaveBeenCalledWith(
                expect.objectContaining({ assetId: "assetB" })
            )
        );
        await waitFor(() => expect(screen.getByText("x.txt")).toBeInTheDocument());
        fireEvent.click(screen.getByText("x.txt"));
        fireEvent.click(await screen.findByRole("button", { name: "Download" }));

        expect(mockDownloadFile).toHaveBeenLastCalledWith(
            "assetB",
            "db1",
            "assetB/x.txt",
            "v-assetB"
        );
    });
});
