/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Unarchiving a version from the list tells the page, as the create, revert, edit and archive dialogs
 * do, so the asset header's version selector gets the version back without a page reload. A failed
 * unarchive tells nobody.
 */

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import * as AssetVersionService from "../../../../services/AssetVersionService";
import { AssetVersionContext } from "../AssetVersionManager";
import { AssetVersionList } from "./AssetVersionList";

jest.mock("../../../../services/AssetVersionService", () => ({
    fetchAssetVersion: jest.fn(),
    unarchiveAssetVersion: jest.fn(),
}));
jest.mock("../../../../services/APIService", () => ({ fetchAssetS3Files: jest.fn() }));
// The version context lives in AssetVersionManager, whose file list and comparison view load the
// viewer registry; neither is rendered here.
jest.mock("./FileVersionsList", () => ({ FileVersionsList: () => null }));
jest.mock("../AssetVersionComparison", () => ({
    __esModule: true,
    default: () => null,
    AssetVersionComparison: () => null,
    EnhancedAssetVersionComparison: () => null,
}));

const mockUnarchive = AssetVersionService.unarchiveAssetVersion as jest.Mock;

const archivedVersion = {
    Version: "2",
    DateModified: "2026-01-02T00:00:00Z",
    Comment: "second",
    description: "",
    specifiedPipelines: [],
    createdBy: "user",
    isCurrent: false,
    isArchived: true,
    fileCount: 1,
};

function renderList(context: Record<string, unknown>) {
    const noop = () => undefined;
    const value: any = {
        loading: false,
        error: null,
        versions: [archivedVersion],
        selectedVersion: null,
        selectedVersionDetails: null,
        totalVersions: 1,
        currentPage: 1,
        pageSize: 5,
        setCurrentPage: noop,
        setPageSize: noop,
        setSelectedVersion: noop,
        compareMode: false,
        setCompareMode: noop,
        versionToCompare: null,
        setVersionToCompare: noop,
        versionsForComparison: [],
        handleVersionSelectionForComparison: noop,
        filterText: "",
        setFilterText: noop,
        sortingColumn: { sortingField: "dateModified", isDescending: true },
        setSortingColumn: noop,
        showComparisonOptions: false,
        comparisonType: "two-versions",
        showArchivedVersions: true,
        setShowArchivedVersions: noop,
        ...context,
    };
    render(
        <MemoryRouter initialEntries={["/databases/db1/assets/a1"]}>
            <Routes>
                <Route
                    path="/databases/:databaseId/assets/:assetId"
                    element={
                        <AssetVersionContext.Provider value={value}>
                            <AssetVersionList
                                onRevertVersion={noop}
                                onVersionSelect={noop}
                                onEditVersion={noop}
                                onArchiveVersion={noop}
                            />
                        </AssetVersionContext.Provider>
                    }
                />
            </Routes>
        </MemoryRouter>
    );
}

describe("unarchiving a version from the list", () => {
    beforeEach(() => jest.clearAllMocks());

    it("refreshes the list and tells the page", async () => {
        mockUnarchive.mockResolvedValue([true, {}]);
        const refreshVersions = jest.fn();
        const onVersionsChanged = jest.fn();
        renderList({ refreshVersions, onVersionsChanged });

        fireEvent.click(screen.getByRole("button", { name: "Unarchive version 2" }));

        await waitFor(() => expect(onVersionsChanged).toHaveBeenCalledTimes(1));
        expect(refreshVersions).toHaveBeenCalledTimes(1);
        expect(mockUnarchive).toHaveBeenCalledWith({
            databaseId: "db1",
            assetId: "a1",
            assetVersionId: "2",
        });
    });

    it("tells nobody when the unarchive fails", async () => {
        mockUnarchive.mockResolvedValue([false, "Not Authorized"]);
        const refreshVersions = jest.fn();
        const onVersionsChanged = jest.fn();
        renderList({ refreshVersions, onVersionsChanged });

        fireEvent.click(screen.getByRole("button", { name: "Unarchive version 2" }));

        await waitFor(() => expect(mockUnarchive).toHaveBeenCalledTimes(1));
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "Unarchive version 2" })).toBeEnabled()
        );
        expect(refreshVersions).not.toHaveBeenCalled();
        expect(onVersionsChanged).not.toHaveBeenCalled();
    });

    it("works without a page callback", async () => {
        mockUnarchive.mockResolvedValue([true, {}]);
        const refreshVersions = jest.fn();
        renderList({ refreshVersions });

        fireEvent.click(screen.getByRole("button", { name: "Unarchive version 2" }));

        await waitFor(() => expect(refreshVersions).toHaveBeenCalledTimes(1));
    });
});
