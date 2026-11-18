/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The sidebar offers a control only in the mode that uses it. Keyword Search Scope ("Include metadata
 * field data in keyword search") and Search Insights ("Show result explanation") shape the keyword
 * request only; a natural-language search never sends them, so they are not offered in that mode,
 * where changing them would re-run the search for the same answer.
 */
import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import SearchSidebar from "./SearchSidebar";
import { fetchAllDatabases, fetchTags } from "../../../services/APIService";
import { DEFAULT_PREFERENCES } from "../types";
import type { SearchFilters, SearchMode } from "../types";

jest.mock("../../../services/APIService", () => ({
    fetchAllDatabases: jest.fn(),
    fetchTags: jest.fn(),
}));
// Panels without a keyword-only control; the geo panel pulls in the map libraries.
jest.mock("./MetadataSearchPanel", () => ({ __esModule: true, default: () => null }));
jest.mock("./GeoFilterPanel", () => ({ __esModule: true, default: () => null }));

const assetMode: SearchFilters = { _rectype: { label: "Assets", value: "asset" } };

const renderSidebar = (searchMode: SearchMode) =>
    render(
        <SearchSidebar
            recordType="asset"
            onRecordTypeChange={jest.fn()}
            filters={assetMode}
            onFilterChange={jest.fn()}
            metadataFilters={[]}
            onAddMetadataFilter={jest.fn()}
            onRemoveMetadataFilter={jest.fn()}
            onUpdateMetadataFilter={jest.fn()}
            preferences={DEFAULT_PREFERENCES}
            onPreferencesChange={jest.fn()}
            showThumbnails={false}
            onThumbnailToggle={jest.fn()}
            searchMode={searchMode}
        />
    );

describe("SearchSidebar keyword-only controls", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAllDatabases as jest.Mock).mockResolvedValue([]);
        (fetchTags as jest.Mock).mockResolvedValue([]);
    });

    it("offers Keyword Search Scope and Search Insights in keyword mode", async () => {
        renderSidebar("keyword");
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(screen.getByText("Keyword Search Scope")).toBeInTheDocument();
        expect(
            screen.getByText("Include metadata field data in keyword search")
        ).toBeInTheDocument();
        expect(screen.getByText("Search Insights")).toBeInTheDocument();
        expect(screen.getByText("Show result explanation")).toBeInTheDocument();
    });

    it("offers neither in natural-language mode, and keeps the filters both modes use", async () => {
        renderSidebar("nlp");
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        expect(screen.queryByText("Keyword Search Scope")).toBeNull();
        expect(screen.queryByText("Include metadata field data in keyword search")).toBeNull();
        expect(screen.queryByText("Search Insights")).toBeNull();
        expect(screen.queryByText("Show result explanation")).toBeNull();
        // The filters a natural-language request carries stay.
        expect(screen.getByText("Include archived items")).toBeInTheDocument();
        expect(screen.getByText("Search inside files")).toBeInTheDocument();
    });
});
