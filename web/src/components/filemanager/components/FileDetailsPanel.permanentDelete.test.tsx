/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { FileDetailsPanel } from "./FileDetailsPanel";
import { FileManagerContext } from "./FileTreeView";
import { AssetDetailContext } from "../../../context/AssetDetailContext";
import type { FileManagerState, FileTree } from "../types/FileManagerTypes";

jest.mock("../../../services/APIService", () => ({
    createFolder: jest.fn(),
    downloadAsset: jest.fn(),
    fetchAsset: jest.fn().mockResolvedValue({ assetId: "a1", isDistributable: true }),
    fetchFileInfo: jest.fn().mockResolvedValue([false, null]),
    fetchAllowedApiRoutes: jest.fn(),
}));
jest.mock("../../../services/FileOperationsService", () => ({
    archiveFile: jest.fn(),
    deleteAssetPreview: jest.fn(),
}));

const PERMANENT_DELETE_ROUTE = "/database/{databaseId}/assets/{assetId}/deleteFile";
const REFUSED_REASON = "Your role does not allow permanently deleting files of this asset.";

// The Tier-1 gate: every route is allowed except the ones a test refuses.
let refusedRoutes: string[] = [];
const mockCan = jest.fn((_method: string, route: string) => !refusedRoutes.includes(route));
jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, failed: false, can: mockCan }),
}));

// The delete dialog is checked on its own (AssetDeleteModal.permanentDelete.test.tsx); here it
// records the props the panel gives it.
const deleteModalProps: any[] = [];
jest.mock("../../modals/AssetDeleteModal", () => ({
    __esModule: true,
    default: (props: any) => {
        deleteModalProps.push(props);
        return null;
    },
}));

jest.mock("../../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => false,
}));
jest.mock("../../../visualizerPlugin/core/viewableExtensions", () => ({
    isViewableExtension: () => false,
    isViewableFilename: () => false,
    areFilenamesViewableTogether: () => false,
    areFilesComparableTogether: () => false,
    extensionOfFilename: () => undefined,
}));
jest.mock("../../../visualizerPlugin/components/EyeIconSvg", () => ({ EYE_ICON_SVG: null }));
jest.mock("../../metadata/FileMetadata", () => ({ __esModule: true, default: () => null }));
jest.mock("../modals/CreateFolderModal", () => ({ CreateFolderModal: () => null }));
jest.mock("../../modals/UnarchiveFileModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../modals/MoveFilesModal", () => ({ MoveFilesModal: () => null }));
jest.mock("../modals/FileVersionsModal", () => ({ FileVersionsModal: () => null }));
jest.mock("../modals/AssetHistoryModal", () => ({ AssetHistoryModal: () => null }));
jest.mock("../modals/SetPrimaryTypeModal", () => ({ SetPrimaryTypeModal: () => null }));
jest.mock("../modals/ShareUrlsModal", () => ({ ShareUrlsModal: () => null }));
jest.mock("../modals/RenameFileModal", () => ({ RenameFileModal: () => null }));
jest.mock("../modals/FileViewerModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../../zipDownloads/CreateZipDownloadModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("./AssetPreviewThumbnail", () => ({ __esModule: true, default: () => null }));
jest.mock("./FilePreviewThumbnail", () => ({ __esModule: true, default: () => null }));
jest.mock("./PreviewModal", () => ({ __esModule: true, default: () => null }));
jest.mock("./AutomationActions", () => ({ __esModule: true, default: () => null }));

const node = (over: Partial<FileTree>): FileTree => ({
    name: "n",
    displayName: "n",
    relativePath: "/n",
    keyPrefix: "a1/n",
    level: 1,
    expanded: false,
    subTree: [],
    versionId: "v1",
    isArchived: false,
    isFolder: false,
    ...over,
});

const root = node({
    name: "Asset",
    displayName: "Asset",
    relativePath: "/",
    keyPrefix: "/",
    level: 0,
    isFolder: true,
});
const file = node({ name: "a.glb", relativePath: "/a.glb", keyPrefix: "a1/a.glb" });
const archived = node({
    name: "old.glb",
    relativePath: "/old.glb",
    keyPrefix: "a1/old.glb",
    isArchived: true,
});
const archived2 = node({
    name: "older.glb",
    relativePath: "/older.glb",
    keyPrefix: "a1/older.glb",
    isArchived: true,
});

function stateFor(selected: FileTree[]): FileManagerState {
    return {
        fileTree: root,
        unfilteredFileTree: root,
        selectedItem: selected[0],
        selectedItems: selected,
        selectedItemPath: selected[0].relativePath,
        selectedItemPaths: selected.map((s) => s.relativePath),
        multiSelectMode: selected.length > 1,
        lastSelectedIndex: 0,
        assetId: "a1",
        databaseId: "db1",
        loading: false,
        loadingPhase: "complete",
        loadingProgress: { current: 1, total: 1 },
        error: null,
        searchTerm: "",
        searchResults: [],
        isSearching: false,
        refreshTrigger: 0,
        showArchived: true,
        showNonIncluded: false,
        flattenedItems: selected,
        totalAssetSize: 1,
        paginationTokens: { basic: null, detailed: null },
        expandedFolders: new Set<string>(),
        readOnly: false,
        assetVersionId: undefined,
    };
}

function renderPanel(selected: FileTree[]) {
    return render(
        <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId"
                    element={
                        <AssetDetailContext.Provider
                            value={{ state: {} as any, dispatch: jest.fn() }}
                        >
                            <FileManagerContext.Provider
                                value={{
                                    state: stateFor(selected),
                                    dispatch: jest.fn(),
                                    onViewExecution: undefined,
                                }}
                            >
                                <FileDetailsPanel />
                            </FileManagerContext.Provider>
                        </AssetDetailContext.Provider>
                    }
                />
            </Routes>
        </MemoryRouter>
    );
}

const lastDeleteModalProps = () => deleteModalProps[deleteModalProps.length - 1];

describe("FileDetailsPanel — permanent file delete", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        deleteModalProps.length = 0;
        refusedRoutes = [];
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it("offers the delete dialog only archiving when the role may not permanently delete files", async () => {
        refusedRoutes = [PERMANENT_DELETE_ROUTE];
        renderPanel([file]);
        expect(await screen.findByRole("button", { name: "File Operations" })).toBeTruthy();
        expect(mockCan).toHaveBeenCalledWith("DELETE", PERMANENT_DELETE_ROUTE);
        expect(lastDeleteModalProps().canPermanentlyDelete).toBe(false);
    });

    it("offers the delete dialog permanent delete when the role may", async () => {
        renderPanel([file]);
        expect(await screen.findByRole("button", { name: "File Operations" })).toBeTruthy();
        expect(lastDeleteModalProps().canPermanentlyDelete).toBe(true);
    });

    it("greys an archived file's Permanently Delete File with the reason", async () => {
        refusedRoutes = [PERMANENT_DELETE_ROUTE];
        renderPanel([archived]);
        const button = await screen.findByRole("button", { name: /Permanently Delete File/ });
        expect(button.getAttribute("aria-disabled")).toBe("true");
        expect(button).toHaveAccessibleDescription(REFUSED_REASON);
        expect(lastDeleteModalProps().canPermanentlyDelete).toBe(false);
    });

    it("greys several archived files' Permanently Delete Files with the reason", async () => {
        refusedRoutes = [PERMANENT_DELETE_ROUTE];
        renderPanel([archived, archived2]);
        const button = await screen.findByRole("button", { name: /Permanently Delete Files/ });
        expect(button.getAttribute("aria-disabled")).toBe("true");
        expect(button).toHaveAccessibleDescription(REFUSED_REASON);
        expect(lastDeleteModalProps().canPermanentlyDelete).toBe(false);
    });

    it("leaves an archived file's Permanently Delete File enabled for a role that may", async () => {
        renderPanel([archived]);
        const button = await screen.findByRole("button", { name: /Permanently Delete File/ });
        expect(button.getAttribute("aria-disabled")).not.toBe("true");
    });
});
