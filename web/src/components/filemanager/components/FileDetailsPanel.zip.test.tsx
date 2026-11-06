/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { FileDetailsPanel } from "./FileDetailsPanel";
import { FileManagerContext } from "./FileTreeView";
import { AssetDetailContext } from "../../../context/AssetDetailContext";
import { appCache } from "../../../services/appCache";
import type { FileManagerState, FileTree } from "../types/FileManagerTypes";

const mockFetchAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    createFolder: jest.fn(),
    downloadAsset: jest.fn(),
    fetchAsset: (...args: any[]) => mockFetchAsset(...args),
    fetchFileInfo: jest.fn().mockResolvedValue([false, null]),
    fetchAllowedApiRoutes: jest.fn(),
}));
jest.mock("../../../services/FileOperationsService", () => ({
    archiveFile: jest.fn(),
    deleteAssetPreview: jest.fn(),
}));

// The Tier-1 gate is driven per test (the API's decision on the zip create route).
const mockCan = jest.fn();
jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, failed: false, can: mockCan }),
}));

// The create modal is checked on its own; here it only shows the selection it was opened with.
jest.mock("../../zipDownloads/CreateZipDownloadModal", () => ({
    __esModule: true,
    default: ({ visible, selection, initialAssetVersionId }: any) =>
        visible ? (
            <div data-testid="zip-modal">
                <span data-testid="zip-entries">{JSON.stringify(selection.entries)}</span>
                <span data-testid="zip-pin">{initialAssetVersionId ?? ""}</span>
                <span data-testid="zip-refused">{JSON.stringify(selection.refused)}</span>
            </div>
        ) : null,
}));

jest.mock("../../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => false,
}));
jest.mock("../../../visualizerPlugin/core/viewableExtensions", () => ({
    isViewableExtension: () => false,
    areFilenamesViewableTogether: () => false,
    areFilesComparableTogether: () => false,
    extensionOfFilename: () => undefined,
}));
jest.mock("../../../visualizerPlugin/components/EyeIconSvg", () => ({ EYE_ICON_SVG: null }));
jest.mock("../../metadata/FileMetadata", () => ({ __esModule: true, default: () => null }));
jest.mock("../modals/CreateFolderModal", () => ({ CreateFolderModal: () => null }));
jest.mock("../../modals/AssetDeleteModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../../modals/UnarchiveFileModal", () => ({ __esModule: true, default: () => null }));
jest.mock("../modals/MoveFilesModal", () => ({ MoveFilesModal: () => null }));
jest.mock("../modals/FileVersionsModal", () => ({ FileVersionsModal: () => null }));
jest.mock("../modals/AssetHistoryModal", () => ({ AssetHistoryModal: () => null }));
jest.mock("../modals/SetPrimaryTypeModal", () => ({ SetPrimaryTypeModal: () => null }));
jest.mock("../modals/ShareUrlsModal", () => ({ ShareUrlsModal: () => null }));
jest.mock("../modals/RenameFileModal", () => ({ RenameFileModal: () => null }));
jest.mock("../modals/FileViewerModal", () => ({ __esModule: true, default: () => null }));
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
    ...over,
});

const root = node({
    name: "Asset",
    displayName: "Asset",
    relativePath: "/",
    keyPrefix: "/",
    level: 0,
    isFolder: true,
    subTree: [node({ relativePath: "/a.glb", isFolder: false })],
});
const file = node({
    name: "a.glb",
    relativePath: "/a.glb",
    keyPrefix: "a1/a.glb",
    isFolder: false,
});
const file2 = node({
    name: "b.glb",
    relativePath: "/b.glb",
    keyPrefix: "a1/b.glb",
    isFolder: false,
});
const folder = node({
    name: "textures",
    relativePath: "/textures",
    keyPrefix: "a1/textures/",
    isFolder: true,
    subTree: [node({ relativePath: "/textures/t.png", isFolder: false })],
});

function stateFor(selected: FileTree[], over: Partial<FileManagerState> = {}): FileManagerState {
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
        showArchived: false,
        showNonIncluded: false,
        flattenedItems: selected,
        totalAssetSize: 1,
        paginationTokens: { basic: null, detailed: null },
        expandedFolders: new Set<string>(),
        readOnly: false,
        assetVersionId: undefined,
        ...over,
    };
}

function renderPanel(selected: FileTree[], over: Partial<FileManagerState> = {}) {
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
                                    state: stateFor(selected, over),
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

const setFeatures = (features: string[]) =>
    appCache.setItem("config", { featuresEnabled: features });

async function openExport() {
    await userEvent.click(await screen.findByRole("button", { name: "Export" }));
}

const zipItem = () => screen.queryByRole("menuitem", { name: /Download as Zip/ });
const requiredZipItem = () => screen.getByRole("menuitem", { name: /Download as Zip/ });

async function chooseZip(): Promise<string> {
    await openExport();
    await userEvent.click(requiredZipItem());
    return (await screen.findByTestId("zip-entries")).textContent || "";
}

describe("FileDetailsPanel — Download as Zip", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
        mockCan.mockReturnValue(true);
        mockFetchAsset.mockResolvedValue({ assetId: "a1", assetName: "A", isDistributable: true });
        setFeatures(["ZIPDOWNLOADS"]);
    });

    afterEach(() => {
        localStorage.clear();
        jest.restoreAllMocks();
    });

    it("offers it for one file and opens the dialog with that file", async () => {
        renderPanel([file]);
        expect(await chooseZip()).toBe(JSON.stringify([{ type: "file", relativePath: "/a.glb" }]));
        expect(mockCan).toHaveBeenCalledWith(
            "POST",
            "/database/{databaseId}/assets/{assetId}/download/zip"
        );
    });

    it("offers it for a folder", async () => {
        renderPanel([folder]);
        expect(await chooseZip()).toBe(
            JSON.stringify([{ type: "folder", relativePath: "/textures/" }])
        );
    });

    it("offers it for the asset root as the whole asset", async () => {
        renderPanel([root]);
        expect(await chooseZip()).toBe(JSON.stringify([{ type: "asset", relativePath: "/" }]));
    });

    it("offers it for several files", async () => {
        renderPanel([file, file2]);
        expect(await chooseZip()).toBe(
            JSON.stringify([
                { type: "file", relativePath: "/a.glb" },
                { type: "file", relativePath: "/b.glb" },
            ])
        );
    });

    it("offers it for a mixed file and folder selection, which has no other Export", async () => {
        renderPanel([file, folder]);
        await openExport();
        const items = screen.getAllByRole("menuitem");
        expect(items.map((i) => i.textContent)).toEqual(["Download as Zip"]);
        await userEvent.click(requiredZipItem());
        expect((await screen.findByTestId("zip-entries")).textContent).toBe(
            JSON.stringify([
                { type: "file", relativePath: "/a.glb" },
                { type: "folder", relativePath: "/textures/" },
            ])
        );
    });

    it("hands an archived file in the selection to the dialog as refused, not dropped (#1125 M2)", async () => {
        const archived = node({
            name: "old.glb",
            relativePath: "/old.glb",
            keyPrefix: "a1/old.glb",
            isFolder: false,
            isArchived: true,
        });
        // A selection with a folder offers Export > Download as Zip whatever the files are.
        renderPanel([folder, archived], { showArchived: true });
        expect(await chooseZip()).toBe(
            JSON.stringify([{ type: "folder", relativePath: "/textures/" }])
        );
        expect(screen.getByTestId("zip-refused")).toHaveTextContent(JSON.stringify(["/old.glb"]));
    });

    it("passes the asset version being browsed to the dialog", async () => {
        renderPanel([file], { assetVersionId: "3" });
        await chooseZip();
        expect(screen.getByTestId("zip-pin")).toHaveTextContent("3");
    });

    it("greys it with a reason when the role may not create zip downloads", async () => {
        mockCan.mockImplementation((method: string, path: string) => !path.includes("/zip"));
        renderPanel([file]);
        await openExport();
        const item = requiredZipItem();
        expect(item).toHaveAttribute("aria-disabled", "true");
        expect(
            within(item.closest("li") as HTMLElement).getByText(
                /does not allow creating zip downloads/
            )
        ).toBeInTheDocument();
        await userEvent.click(item);
        expect(screen.queryByTestId("zip-modal")).toBeNull();
    });

    it("is absent when the deployment has the feature off", async () => {
        setFeatures([]);
        renderPanel([file]);
        await openExport();
        expect(screen.getByRole("menuitem", { name: /Download File/ })).toBeInTheDocument();
        expect(zipItem()).toBeNull();
    });

    it("is absent, with the whole Export menu, on a non-distributable asset", async () => {
        mockFetchAsset.mockResolvedValue({ assetId: "a1", isDistributable: false });
        renderPanel([file, folder]);
        await waitFor(() => expect(mockFetchAsset).toHaveBeenCalled());
        await waitFor(() => expect(screen.queryByRole("button", { name: "Export" })).toBeNull());
    });
});
