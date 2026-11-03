/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search list view's Create Asset and Delete Selected actions are disabled, with the reason, when
 * the page says the signed-in role may not use them; with no reason they behave as before.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SearchPageListView from "./SearchPageListView";
import { INITIAL_STATE } from "./SearchPageTypes";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({
    fetchtagTypes: jest.fn(() => Promise.resolve([])),
}));
jest.mock("../filemanager/modals/FileViewerModal", () => ({
    __esModule: true,
    default: () => null,
}));
// The viewer registry pulls in Vite's import.meta.glob, so the registry modules are stubbed.
jest.mock("../../visualizerPlugin/core/viewableExtensions", () => ({
    areFilenamesViewableTogether: () => false,
    areFilesComparableTogether: () => false,
}));
jest.mock("../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({}));

const hit = { _id: "a1", _source: { str_assetname: "Bracket", str_databaseid: "db1" } };
const state = {
    ...INITIAL_STATE,
    initialResult: true,
    recordType: "asset",
    result: { hits: { hits: [hit], total: { value: 1 } } },
    selectedItems: [{ ...hit._source, _id: "a1" }],
    tablePreferences: { pageSize: 10, visibleContent: [] },
};

const CREATE_REASON = `Your role does not allow creating ${Synonyms.assets}.`;
const DELETE_REASON = `Your role does not allow archiving or deleting ${Synonyms.assets}.`;

const mount = (assetActionDisabledReasons?: { create?: string; delete?: string }) =>
    render(
        <MemoryRouter>
            <SearchPageListView
                state={state}
                dispatch={jest.fn()}
                onCreateAsset={jest.fn()}
                assetActionDisabledReasons={assetActionDisabledReasons}
            />
        </MemoryRouter>
    );

const createButton = () => screen.getByRole("button", { name: `Create ${Synonyms.Asset}` });
const deleteButton = () => screen.getByRole("button", { name: "Delete Selected" });

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("Search list view asset actions by permission", () => {
    it("disables both actions and states why when the role may use neither", () => {
        mount({ create: CREATE_REASON, delete: DELETE_REASON });
        expect(createButton()).toHaveAttribute("aria-disabled", "true");
        expect(createButton()).toHaveAccessibleDescription(CREATE_REASON);
        expect(deleteButton()).toHaveAttribute("aria-disabled", "true");
        expect(deleteButton()).toHaveAccessibleDescription(DELETE_REASON);
    });

    it("keeps both actions enabled when no reason is given", () => {
        const onCreateAsset = jest.fn();
        render(
            <MemoryRouter>
                <SearchPageListView
                    state={state}
                    dispatch={jest.fn()}
                    onCreateAsset={onCreateAsset}
                />
            </MemoryRouter>
        );
        expect(deleteButton()).not.toBeDisabled();
        expect(deleteButton()).not.toHaveAttribute("aria-disabled");
        fireEvent.click(createButton());
        expect(onCreateAsset).toHaveBeenCalledTimes(1);
    });
});
