/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * After an archive, delete or unarchive, the search table dispatches "refresh" for its container to
 * re-run the current search (ModernSearchContainer.refresh.test.tsx covers the container side).
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
// The delete modal reports a completed archive through onSuccess.
jest.mock("../modals/AssetDeleteModal", () => ({
    __esModule: true,
    default: ({ onSuccess }: any) => (
        <button onClick={() => onSuccess({ assetId: "a1" })}>Complete archive</button>
    ),
}));
jest.mock("../modals/AssetUnarchiveModal", () => ({
    __esModule: true,
    default: ({ onSuccess }: any) => (
        <button onClick={() => onSuccess({ assetId: "a1" })}>Complete unarchive</button>
    ),
}));

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("search table refresh", () => {
    it("dispatches refresh after an archive and an unarchive complete", () => {
        const dispatch = jest.fn();
        render(
            <MemoryRouter>
                <SearchPageListView
                    state={{
                        ...INITIAL_STATE,
                        initialResult: true,
                        recordType: "asset",
                        result: { hits: { hits: [], total: { value: 0 } } },
                        tablePreferences: { pageSize: 10, visibleContent: [] },
                    }}
                    dispatch={dispatch}
                />
            </MemoryRouter>
        );
        fireEvent.click(screen.getByText("Complete archive"));
        fireEvent.click(screen.getByText("Complete unarchive"));
        const refreshes = dispatch.mock.calls.filter(([action]) => action?.type === "refresh");
        expect(refreshes).toHaveLength(2);
    });
});
