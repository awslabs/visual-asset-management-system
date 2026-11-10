/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search table's counter and its "About this count" popover. Totals count only records the
 * caller can open. When the backend can only give a minimum (hits.total.relation "gte") the counter
 * ends in "+" and the popover says why; an exact count gets neither. The popover opens from an info
 * icon button named "About this count", which the keyboard reaches and opens with Enter or Space.
 */
import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
// The viewer registry pulls in Vite's import.meta.glob, so the registry modules are stubbed.
jest.mock("../../visualizerPlugin/core/viewableExtensions", () => ({
    areFilenamesViewableTogether: () => false,
    areFilesComparableTogether: () => false,
}));
jest.mock("../../visualizerPlugin/core/useViewerRegistryReady", () => ({
    useViewerRegistryReady: () => true,
}));
jest.mock("../../visualizerPlugin/core/PluginRegistry", () => ({}));
const exactState = {
    ...INITIAL_STATE,
    initialResult: true,
    recordType: "asset",
    result: {
        hits: {
            hits: [
                {
                    _id: "a1",
                    _index: "assets",
                    _source: { str_assetid: "a1", str_databaseid: "db1", str_assetname: "A1" },
                },
            ],
            total: { value: 1, relation: "eq" },
        },
        aggregationTotal: 1,
    },
    tablePreferences: { pageSize: 10, visibleContent: [] },
};

const lowerBoundState = {
    ...exactState,
    result: {
        ...exactState.result,
        hits: { ...exactState.result.hits, total: { value: 3, relation: "gte" } },
        aggregationTotal: 2,
    },
};

function counterText(container: HTMLElement): string | null | undefined {
    return container.querySelector('[class*="counter"]')?.textContent?.trim();
}

function renderView(state: any) {
    return render(
        <MemoryRouter>
            <SearchPageListView state={state} dispatch={jest.fn()} />
        </MemoryRouter>
    );
}

const countInfoButton = () => screen.getByRole("button", { name: "About this count" });

beforeEach(() => {
    jest.spyOn(console, "log").mockImplementation(() => undefined);
});

afterEach(() => jest.restoreAllMocks());

describe("Search list view count", () => {
    it("shows a minimum with + and explains it", () => {
        const { container } = renderView(lowerBoundState);

        expect(counterText(container)).toBe("3+");
        fireEvent.click(countInfoButton());
        expect(screen.getByText("About this count")).toBeInTheDocument();
        expect(screen.getByText(/includes only records you can open/)).toBeInTheDocument();
        expect(screen.getByText(/there may be more/)).toBeInTheDocument();
    });

    it.each([
        ["Enter", "{Enter}"],
        ["Space", " "],
    ])("opens the explanation from the keyboard with %s", async (_, key) => {
        renderView(lowerBoundState);
        const trigger = countInfoButton();
        // An icon button, not a bare glyph: the keyboard reaches it and it is named for screen readers.
        expect(trigger.tagName).toBe("BUTTON");
        expect(screen.queryByText(/includes only records you can open/)).toBeNull();

        trigger.focus();
        expect(trigger).toHaveFocus();
        await userEvent.keyboard(key);
        expect(await screen.findByText(/includes only records you can open/)).toBeInTheDocument();
    });

    it("is reached by Tab", async () => {
        renderView(lowerBoundState);
        const trigger = countInfoButton();
        // Tab from the start of the page until the trigger takes focus (it is not the first stop).
        for (let i = 0; i < 20 && document.activeElement !== trigger; i++) {
            await userEvent.tab();
        }
        expect(trigger).toHaveFocus();
    });

    it("explains a natural-language minimum by approximate matching, not by permission rules", () => {
        const nlpState = {
            ...lowerBoundState,
            result: {
                ...lowerBoundState.result,
                nlp: { query: "gearbox", truncated: false },
                warnings: [],
            },
        };
        renderView(nlpState);

        fireEvent.click(countInfoButton());
        expect(screen.getByText(/includes only records you can open/)).toBeInTheDocument();
        expect(
            screen.getByText(/finds the closest matches approximately, so it can miss a weak match/)
        ).toBeInTheDocument();
        // No window filled and every database was searched: neither is named.
        expect(screen.queryByText(/result limit/)).not.toBeInTheDocument();
        expect(screen.queryByText(/permission rules/)).not.toBeInTheDocument();
    });

    it("also names the filled window when the natural-language answer reports one", () => {
        const nlpState = {
            ...lowerBoundState,
            result: {
                ...lowerBoundState.result,
                nlp: { query: "gearbox", truncated: true },
                warnings: [{ code: "truncated:window", message: "A result window of 100 filled" }],
            },
        };
        renderView(nlpState);

        fireEvent.click(countInfoButton());
        expect(screen.getByText(/approximately/)).toBeInTheDocument();
        expect(screen.getByText(/This search also reached its result limit\./)).toBeInTheDocument();
        expect(screen.queryByText(/A result window of 100 filled/)).not.toBeInTheDocument();
    });

    it("shows an exact count without + or a popover", () => {
        const { container } = renderView(exactState);

        expect(counterText(container)).toBe("1");
        expect(screen.queryByRole("button", { name: "About this count" })).toBeNull();
    });
});
