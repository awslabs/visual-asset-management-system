/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Search pagination stays inside the 10,000-result from + size window the search API accepts, so
 * the last page offered can always be requested.
 */
import React from "react";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import { getPageCount, SEARCH_RESULT_WINDOW } from "./types";
import SearchPageListView from "./SearchPageListView";
import { INITIAL_STATE } from "./SearchPageTypes";

jest.mock("../../services/APIService", () => ({
    fetchtagTypes: jest.fn(() => Promise.resolve([])),
}));
jest.mock("../filemanager/modals/FileViewerModal", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../../visualizerPlugin/core/viewableExtensions", () => ({
    areFilenamesViewableTogether: () => false,
    areFilesComparableTogether: () => false,
}));
jest.mock("../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({}));

describe("getPageCount", () => {
    it("caps the page count at the result window", () => {
        const pages = getPageCount(25000, 50);
        expect(pages).toBe(200);
        // The last page's request stays inside the window.
        expect((pages - 1) * 50 + 50).toBeLessThanOrEqual(SEARCH_RESULT_WINDOW);
    });

    it("keeps the last page inside the window for a page size that does not divide it", () => {
        const pages = getPageCount(25000, 30);
        expect((pages - 1) * 30 + 30).toBeLessThanOrEqual(SEARCH_RESULT_WINDOW);
        expect(pages * 30 + 30).toBeGreaterThan(SEARCH_RESULT_WINDOW);
    });

    it("leaves totals inside the window unchanged", () => {
        expect(getPageCount(120, 50)).toBe(3);
        expect(getPageCount(0, 50)).toBe(0);
        expect(getPageCount(10000, 100)).toBe(100);
    });
});

describe("Search list view pagination", () => {
    beforeEach(() => {
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });
    afterEach(() => jest.restoreAllMocks());

    it("offers no page past the result window", () => {
        const state = {
            ...INITIAL_STATE,
            initialResult: true,
            recordType: "asset",
            result: {
                hits: { hits: [], total: { value: 50, relation: "eq" } },
                aggregationTotal: 25000,
            },
            tablePreferences: { pageSize: 50, visibleContent: [] },
        };
        render(
            <MemoryRouter>
                <SearchPageListView state={state} dispatch={jest.fn()} />
            </MemoryRouter>
        );
        const pagination = createWrapper(document.body).findPagination();
        expect(pagination).not.toBeNull();
        const pageNumbers = pagination!
            .findPageNumbers()
            .map((p) => Number(p.getElement().textContent))
            .filter((n) => !Number.isNaN(n));
        expect(Math.max(...pageNumbers)).toBe(200);
    });
});
