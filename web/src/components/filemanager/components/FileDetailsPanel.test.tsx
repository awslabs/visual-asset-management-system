/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { FileDetailsPanel } from "./FileDetailsPanel";
import { FileManagerContext } from "./FileTreeView";
import { AssetDetailContext } from "../../../context/AssetDetailContext";
import type { FileManagerState, FileTree } from "../types/FileManagerTypes";

jest.mock("../../../services/APIService", () => ({
    createFolder: jest.fn(),
    downloadAsset: jest.fn(),
    fetchAsset: jest.fn().mockResolvedValue({ assetId: "a1", isDistributable: true }),
    fetchFileInfo: jest.fn(),
    fetchAllowedApiRoutes: jest.fn(),
}));
jest.mock("../../../services/FileOperationsService", () => ({
    archiveFile: jest.fn(),
    deleteAssetPreview: jest.fn(),
}));

// The Tier-1 gate is driven per test; the panel must hide the link while it says no.
const mockCan = jest.fn();
jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({ loading: false, can: mockCan }),
}));

// The panel composes many modals, thumbnails and the viewer registry; none of them bear on the
// provenance row, so they are stubbed to keep this suite on that row.
jest.mock("../../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => false,
}));
jest.mock("../../../visualizerPlugin/core/viewableExtensions", () => ({
    isViewableExtension: () => false,
    areFilenamesViewableTogether: () => false,
    extensionOfFilename: () => undefined,
}));
jest.mock("../../../visualizerPlugin/components/EyeIconSvg", () => ({ EYE_ICON_SVG: null }));
jest.mock("../../metadata/FileMetadata", () => ({ __esModule: true, default: () => null }));
jest.mock("../modals/CreateFolderModal", () => ({
    CreateFolderModal: ({ visible, onSubmit, submitError }: any) =>
        visible ? (
            <div data-testid="create-folder-modal">
                <span data-testid="create-folder-error">{submitError}</span>
                <button onClick={() => onSubmit("new-folder")}>Submit folder</button>
            </div>
        ) : null,
}));
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

const root: FileTree = {
    name: "TestAsset",
    displayName: "TestAsset",
    relativePath: "/",
    keyPrefix: "/",
    level: 0,
    expanded: true,
    subTree: [],
};

// A file node as the tree holds it after the detailed listing: a versionId is present, so the
// panel does not issue its on-demand fetchFileInfo, and the provenance comes from the node.
const fileNode = (over: Partial<FileTree> = {}): FileTree => ({
    name: "BoomBox.glb",
    displayName: "BoomBox.glb",
    relativePath: "/optimized/BoomBox.glb",
    keyPrefix: "a1/optimized/BoomBox.glb",
    level: 2,
    expanded: false,
    subTree: [],
    isFolder: false,
    size: 188336,
    dateCreatedCurrentVersion: "2026-09-12T15:30:18+00:00",
    versionId: "g.cFP5H7RuKiXmhtKp26tyfdu3_IF8Bg",
    isArchived: false,
    primaryType: null,
    previewFile: "",
    changeSource: "workflowExecution",
    changeUserId: "scheurik",
    changeWorkflowId: "conversion-3d-basic",
    changeWorkflowExecutionId: "571a47bf03d645f08f9c11606f4f251e",
    ...over,
});

const stateFor = (selected: FileTree): FileManagerState => ({
    fileTree: root,
    unfilteredFileTree: root,
    selectedItem: selected,
    selectedItems: [selected],
    selectedItemPath: selected.relativePath,
    selectedItemPaths: [selected.relativePath],
    multiSelectMode: false,
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
    flattenedItems: [selected],
    totalAssetSize: 188336,
    paginationTokens: { basic: null, detailed: null },
    expandedFolders: new Set<string>(),
    readOnly: false,
    assetVersionId: undefined,
});

function renderPanel(selected: FileTree, onViewExecution?: (id: string) => void) {
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
                                    onViewExecution,
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

const changeSourceRow = () => screen.getByText("Change Source:").parentElement as HTMLElement;

describe("FileDetailsPanel — workflow execution provenance link", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockCan.mockReturnValue(true);
    });

    it("keeps the source and user text and offers View execution for a workflow-written version", async () => {
        const onViewExecution = jest.fn();
        renderPanel(fileNode(), onViewExecution);

        const row = changeSourceRow();
        expect(row).toHaveTextContent("Workflow Execution (scheurik)");
        const link = await screen.findByText("View execution");
        expect(row).toContainElement(link);

        await userEvent.click(link);
        expect(onViewExecution).toHaveBeenCalledTimes(1);
        expect(onViewExecution).toHaveBeenCalledWith("571a47bf03d645f08f9c11606f4f251e");
        // The gate consulted is the execution details read, not some other execution route.
        expect(mockCan).toHaveBeenCalledWith("GET", "/workflows/executions/{executionId}/details");
    });

    it("does not offer the link for a version written by an upload", async () => {
        renderPanel(
            fileNode({
                changeSource: "upload",
                changeWorkflowId: undefined,
                changeWorkflowExecutionId: undefined,
            }),
            jest.fn()
        );
        expect(changeSourceRow()).toHaveTextContent("Upload (scheurik)");
        await waitFor(() => expect(screen.queryByText("View execution")).not.toBeInTheDocument());
    });

    it("does not offer the link when the workflow version carries no execution id", () => {
        // A legacy or partially stamped object: the source alone is not a link target.
        renderPanel(fileNode({ changeWorkflowExecutionId: undefined }), jest.fn());
        expect(changeSourceRow()).toHaveTextContent("Workflow Execution (scheurik)");
        expect(screen.queryByText("View execution")).not.toBeInTheDocument();
    });

    it("hides the link when the caller may not read execution details", () => {
        mockCan.mockReturnValue(false);
        renderPanel(fileNode(), jest.fn());
        expect(changeSourceRow()).toHaveTextContent("Workflow Execution (scheurik)");
        expect(screen.queryByText("View execution")).not.toBeInTheDocument();
    });

    it("hides the link when no host can open the quick view", () => {
        // The context is shared with hosts that mount the panel without a quick view.
        renderPanel(fileNode(), undefined);
        expect(screen.queryByText("View execution")).not.toBeInTheDocument();
    });
});

describe("FileDetailsPanel — request failures", () => {
    const { createFolder, downloadAsset } = jest.requireMock("../../../services/APIService");

    beforeEach(() => {
        jest.clearAllMocks();
        mockCan.mockReturnValue(true);
        jest.spyOn(console, "error").mockImplementation(() => undefined);
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });

    afterEach(() => jest.restoreAllMocks());

    const folderNode = (): FileTree => ({
        name: "optimized",
        displayName: "optimized",
        relativePath: "/optimized/",
        keyPrefix: "a1/optimized/",
        level: 1,
        expanded: true,
        subTree: [],
        isFolder: true,
    });

    it("keeps the create-folder modal open and shows the request error", async () => {
        createFolder.mockResolvedValue([false, "Forbidden"]);
        renderPanel(folderNode());

        await userEvent.click(screen.getByRole("button", { name: "Folder Operations" }));
        await userEvent.click(await screen.findByText("Create Sub-Folder"));
        await userEvent.click(screen.getByRole("button", { name: "Submit folder" }));

        await waitFor(() =>
            expect(screen.getByTestId("create-folder-error")).toHaveTextContent(
                "Failed to create folder: Forbidden"
            )
        );
        expect(createFolder).toHaveBeenCalledWith({
            databaseId: "db1",
            assetId: "a1",
            relativeKey: "/optimized/new-folder/",
        });
        expect(screen.getByTestId("create-folder-modal")).toBeInTheDocument();
    });

    it("treats an empty create-folder reply as a failure", async () => {
        createFolder.mockResolvedValue(false);
        renderPanel(folderNode());

        await userEvent.click(screen.getByRole("button", { name: "Folder Operations" }));
        await userEvent.click(await screen.findByText("Create Sub-Folder"));
        await userEvent.click(screen.getByRole("button", { name: "Submit folder" }));

        await waitFor(() =>
            expect(screen.getByTestId("create-folder-error")).toHaveTextContent(
                "Failed to create folder: Unknown error"
            )
        );
        expect(screen.getByTestId("create-folder-modal")).toBeInTheDocument();
    });

    it("shows a dismissible alert with the status and message when a file download fails", async () => {
        downloadAsset.mockResolvedValue([false, "Asset not distributable", 403]);
        renderPanel(fileNode());

        await userEvent.click(await screen.findByRole("button", { name: "Export" }));
        await userEvent.click(await screen.findByText("Download File"));

        const alert = await screen.findByText("Download failed (403): Asset not distributable");
        expect(alert).toBeInTheDocument();
        expect(downloadAsset).toHaveBeenCalledWith(
            expect.objectContaining({
                assetId: "a1",
                databaseId: "db1",
                key: "a1/optimized/BoomBox.glb",
                versionId: "g.cFP5H7RuKiXmhtKp26tyfdu3_IF8Bg",
            })
        );

        await userEvent.click(screen.getByRole("button", { name: /dismiss/i }));
        await waitFor(() =>
            expect(screen.queryByText(/Download failed \(403\)/)).not.toBeInTheDocument()
        );
    });
});

describe("FileDetailsPanel — Upload Files action", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("disables Upload Files and states why for a role that may not upload", async () => {
        mockCan.mockImplementation((method: string, path: string) => path !== "/uploads");
        renderPanel(root);
        const upload = await screen.findByRole("button", { name: "Upload Files" });
        expect(upload).toHaveAttribute("aria-disabled", "true");
        expect(upload).toHaveAccessibleDescription(/does not allow uploading files/);
        expect(mockCan).toHaveBeenCalledWith("POST", "/uploads");
    });

    it("enables Upload Files for a role that may upload", async () => {
        mockCan.mockReturnValue(true);
        renderPanel(root);
        const upload = await screen.findByRole("button", { name: "Upload Files" });
        expect(upload).not.toHaveAttribute("aria-disabled");
        expect(upload).not.toBeDisabled();
    });
});
