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
