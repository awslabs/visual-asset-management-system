/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Versions tab's table selects one version at a time with a radio per row (#1613). It passed no
 * aria labels, so each radio had no accessible name (axe "label", critical) and the selection
 * column's header cell was empty (axe "empty-table-header"). The table now names the selection
 * group, which is also the selection column's header text, and each radio names its version.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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

const version = (Version: string, extra: Record<string, unknown> = {}) => ({
    Version,
    DateModified: "2026-01-02T00:00:00Z",
    Comment: `version ${Version}`,
    description: "",
    specifiedPipelines: [],
    createdBy: "user",
    isCurrent: false,
    isArchived: false,
    fileCount: 1,
    ...extra,
});

const VERSIONS = [version("3", { isCurrent: true }), version("2", { versionAlias: "beta" })];

function renderList(selectedVersion: unknown = null) {
    const noop = () => undefined;
    const value: any = {
        loading: false,
        error: null,
        versions: VERSIONS,
        selectedVersion,
        selectedVersionDetails: null,
        totalVersions: VERSIONS.length,
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
        showArchivedVersions: false,
        setShowArchivedVersions: noop,
        refreshVersions: noop,
    };
    return render(
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

describe("the Versions tab's selection radios and header are named (#1613)", () => {
    it("names each row's radio by its version, alias included", () => {
        renderList();
        const radios = screen.getAllByRole("radio");
        expect(radios).toHaveLength(2);
        expect(radios[0]).toHaveAccessibleName("Select version 3");
        expect(radios[1]).toHaveAccessibleName("Select version 2 (beta)");
    });

    it("keeps the names on a selected version", () => {
        renderList(VERSIONS[1]);
        expect(screen.getByRole("radio", { name: "Select version 2 (beta)" })).toBeChecked();
        expect(screen.getByRole("radio", { name: "Select version 3" })).not.toBeChecked();
    });

    it("gives every column header, the selection column's included, text", () => {
        const { container } = renderList();
        const headers = Array.from(container.querySelectorAll("thead th"));
        expect(headers.length).toBeGreaterThan(1);
        for (const header of headers) {
            expect((header.textContent || "").trim()).not.toBe("");
        }
        // The selection column comes first, and its header names the selection.
        expect(headers[0]).toHaveTextContent("Version selection");
    });
});
