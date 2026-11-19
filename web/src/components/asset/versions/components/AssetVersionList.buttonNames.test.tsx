/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The version list's comparison buttons are named by the text they show, then the version (#1592,
 * WCAG 2.5.3 Label in Name). They were named "Add version N to comparison", "Remove version N from
 * comparison" and "Compare version N with current files", so a speech-input user saying "Select for
 * Comparison", "Selected" or "Compare with Current" could not reach them.
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
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

const version = {
    Version: "2",
    DateModified: "2026-01-02T00:00:00Z",
    Comment: "second",
    description: "",
    specifiedPipelines: [],
    createdBy: "user",
    isCurrent: false,
    isArchived: false,
    fileCount: 1,
};

function renderList(versionsForComparison: unknown[]) {
    const noop = () => undefined;
    const value: any = {
        loading: false,
        error: null,
        versions: [version],
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
        versionsForComparison,
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
    const { container } = render(
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
    // The Compare column is off by default: turn it on in the table preferences, as a user does.
    const preferences = found(createWrapper(container).findCollectionPreferences());
    act(() => preferences.findTriggerButton().click());
    const modal = found(preferences.findModal());
    const compare = found(found(modal.findVisibleContentPreference()).findToggleByIndex(2, 1));
    act(() => compare.findNativeInput().click());
    act(() => found(modal.findConfirmButton()).click());
}

/** `value`, checked to be there. */
function found<T>(value: T | null): T {
    expect(value).not.toBeNull();
    return value as T;
}

/** The button whose visible text is `text`. */
const buttonShowing = (text: string) => {
    const button = screen.getByText(text, { selector: "button *" }).closest("button");
    expect(button).not.toBeNull();
    return button as HTMLElement;
};

describe("the version list's comparison buttons are named by the text they show (#1592)", () => {
    it("Select for Comparison, before the version is selected", () => {
        renderList([]);
        expect(buttonShowing("Select for Comparison")).toHaveAccessibleName(
            "Select for Comparison: version 2"
        );
    });

    it("Selected (n), once the version is selected", () => {
        renderList([version]);
        expect(buttonShowing("Selected (1)")).toHaveAccessibleName(
            "Selected (1): remove version 2 from comparison"
        );
    });

    it("Compare with Current", () => {
        renderList([]);
        expect(buttonShowing("Compare with Current")).toHaveAccessibleName(
            "Compare with Current: version 2"
        );
    });
});
