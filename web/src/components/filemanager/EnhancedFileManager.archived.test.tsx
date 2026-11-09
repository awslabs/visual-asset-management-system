/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * #870: on an archived asset's page the file manager lists with showArchived, shows the (all
 * archived) files, and is read-only like a version view. A live asset's file manager is unchanged.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { EnhancedFileManager } from "./EnhancedFileManager";
import { AssetDetailContext } from "../../context/AssetDetailContext";
import { ArchivedAssetContext } from "../../context/ArchivedAssetContext";

jest.mock("../../services/APIService", () => ({
    fetchAssetS3FilesStreaming: jest.fn(),
    fetchFileInfo: jest.fn(),
    downloadAsset: jest.fn(),
}));

// Stands in for the details panel and reports the file manager state it would act on
jest.mock("./components/FileDetailsPanel", () => ({
    FileDetailsPanel: () => {
        const { useContext: useMockContext } = require("react");
        const { FileManagerContext: MockFileManagerContext } = require("./components/FileTreeView");
        const context = useMockContext(MockFileManagerContext);
        return (
            <div data-testid="panel-probe">
                {`readOnly=${context?.state.readOnly} showArchived=${context?.state.showArchived}`}
            </div>
        );
    },
}));
jest.mock("./modals/AssetPreviewModal", () => ({ __esModule: true, default: () => null }));

const { fetchAssetS3FilesStreaming } = require("../../services/APIService");

const archivedFile = {
    fileName: "a.txt",
    key: "asset1/a.txt",
    relativePath: "/a.txt",
    isFolder: false,
    size: 10,
    dateCreatedCurrentVersion: "2026-01-01T00:00:00Z",
    versionId: "v1",
    isArchived: true,
    primaryType: null,
};

function renderFileManager(archived: boolean) {
    return render(
        <MemoryRouter initialEntries={["/databases/db1/assets/asset1"]}>
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId"
                    element={
                        <AssetDetailContext.Provider
                            value={{ state: {} as any, dispatch: jest.fn() }}
                        >
                            <ArchivedAssetContext.Provider value={archived}>
                                <EnhancedFileManager assetName="TestAsset" />
                            </ArchivedAssetContext.Provider>
                        </AssetDetailContext.Provider>
                    }
                />
            </Routes>
        </MemoryRouter>
    );
}

describe("EnhancedFileManager for an archived asset", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAssetS3FilesStreaming as jest.Mock).mockImplementation(async function* () {
            yield {
                success: true,
                items: [archivedFile],
                nextToken: null,
                error: null,
                pageNumber: 1,
                isLastPage: true,
            };
        });
    });

    it("lists with showArchived, shows the archived files and is read-only", async () => {
        renderFileManager(true);
        await waitFor(() => expect(fetchAssetS3FilesStreaming).toHaveBeenCalled());
        for (const [args] of (fetchAssetS3FilesStreaming as jest.Mock).mock.calls) {
            expect(args).toEqual(
                expect.objectContaining({
                    databaseId: "db1",
                    assetId: "asset1",
                    showArchived: true,
                })
            );
        }
        expect(await screen.findByText("a.txt")).toBeInTheDocument();
        expect(screen.getByTestId("panel-probe")).toHaveTextContent(
            "readOnly=true showArchived=true"
        );
    });

    it("a live asset lists without showArchived and stays writable", async () => {
        renderFileManager(false);
        await waitFor(() => expect(fetchAssetS3FilesStreaming).toHaveBeenCalled());
        for (const [args] of (fetchAssetS3FilesStreaming as jest.Mock).mock.calls) {
            expect(args.showArchived).toBe(false);
        }
        await waitFor(() =>
            expect(screen.getByTestId("panel-probe")).toHaveTextContent(
                "readOnly=false showArchived=false"
            )
        );
    });
});
