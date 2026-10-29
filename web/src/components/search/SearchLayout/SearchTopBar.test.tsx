/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchTopBar, { searchModeDescription } from "./SearchTopBar";

const baseProps = {
    query: "",
    onQueryChange: jest.fn(),
    onSearch: jest.fn(),
    onClearAll: jest.fn(),
    title: "Assets and Files - Search",
};

describe("SearchTopBar search-mode control", () => {
    beforeEach(() => jest.clearAllMocks());

    it("renders no mode control when the prop is absent (only OpenSearch is on)", () => {
        render(<SearchTopBar {...baseProps} />);
        expect(screen.queryByTestId("keyword")).toBeNull();
        expect(screen.queryByTestId("nlp")).toBeNull();
        expect(screen.getByPlaceholderText("Search by keywords...")).toBeInTheDocument();
    });

    it("offers both modes and reports a change when both engines are on", async () => {
        const onChange = jest.fn();
        render(
            <SearchTopBar
                {...baseProps}
                searchMode="keyword"
                searchModeControl={{ mode: "keyword", onChange }}
            />
        );
        expect(screen.getByTestId("keyword")).toHaveAttribute("aria-pressed", "true");
        expect(screen.getByTestId("nlp")).not.toBeDisabled();
        await userEvent.click(screen.getByTestId("nlp"));
        expect(onChange).toHaveBeenCalledWith("nlp");
    });

    it("renders no mode control but the natural-language placeholder when only vector search is on", () => {
        render(<SearchTopBar {...baseProps} searchMode="nlp" />);
        expect(screen.queryByTestId("nlp")).toBeNull();
        expect(screen.queryByTestId("keyword")).toBeNull();
        expect(screen.queryByRole("toolbar", { name: "Search mode" })).toBeNull();
        expect(
            screen.getByPlaceholderText("Describe what you are looking for...")
        ).toBeInTheDocument();
    });
});

describe("SearchTopBar natural-language query length", () => {
    beforeEach(() => jest.clearAllMocks());

    it("past 1,000 characters says so, disables Search and does not send on Enter or click", async () => {
        const onSearch = jest.fn();
        render(
            <SearchTopBar
                {...baseProps}
                onSearch={onSearch}
                searchMode="nlp"
                query={"a".repeat(1001)}
            />
        );
        const box = screen.getByRole("searchbox");
        expect(
            screen.getByText(/Natural-language queries can be up to 1,000 characters\./)
        ).toBeInTheDocument();
        expect(box).toHaveAccessibleDescription(/up to 1,000 characters/);
        expect(box).toHaveAttribute("aria-invalid", "true");
        const search = screen.getByRole("button", { name: "Search" });
        expect(search).toBeDisabled();
        await userEvent.type(box, "{Enter}");
        await userEvent.click(search);
        expect(onSearch).not.toHaveBeenCalled();
    });

    it("says how much to remove", () => {
        const { rerender } = render(
            <SearchTopBar {...baseProps} searchMode="nlp" query={"a".repeat(1001)} />
        );
        expect(screen.getByText(/Shorten it by 1 character to search\./)).toBeInTheDocument();
        rerender(<SearchTopBar {...baseProps} searchMode="nlp" query={"a".repeat(1250)} />);
        expect(screen.getByText(/Shorten it by 250 characters to search\./)).toBeInTheDocument();
    });

    it("accepts exactly 1,000 characters as sent: trimmed, counted per character not per UTF-16 unit", async () => {
        const onSearch = jest.fn();
        const { rerender } = render(
            <SearchTopBar
                {...baseProps}
                onSearch={onSearch}
                searchMode="nlp"
                query={"a".repeat(1000)}
            />
        );
        // The box is sent trimmed, and the route counts characters, so neither surrounding spaces
        // nor characters outside the Basic Multilingual Plane (two UTF-16 units each) count extra.
        for (const query of ["a".repeat(1000), `  ${"a".repeat(1000)}  `, "🌊".repeat(1000)]) {
            rerender(
                <SearchTopBar {...baseProps} onSearch={onSearch} searchMode="nlp" query={query} />
            );
            expect(screen.queryByText(/up to 1,000 characters/)).toBeNull();
            expect(screen.getByRole("button", { name: "Search" })).toBeEnabled();
        }
        await userEvent.type(screen.getByRole("searchbox"), "{Enter}");
        expect(onSearch).toHaveBeenCalledTimes(1);
    });

    it("leaves keyword mode unbounded", async () => {
        const onSearch = jest.fn();
        render(
            <SearchTopBar
                {...baseProps}
                onSearch={onSearch}
                searchMode="keyword"
                query={"a".repeat(1001)}
            />
        );
        expect(screen.queryByText(/up to 1,000 characters/)).toBeNull();
        expect(screen.getByRole("button", { name: "Search" })).toBeEnabled();
        await userEvent.type(screen.getByRole("searchbox"), "{Enter}");
        expect(onSearch).toHaveBeenCalledTimes(1);
    });
});

describe("SearchTopBar query band", () => {
    it("explains the active mode under the query box, in every engine configuration", () => {
        const { rerender } = render(<SearchTopBar {...baseProps} searchMode="keyword" />);
        expect(screen.getByText(searchModeDescription("keyword"))).toBeInTheDocument();
        expect(screen.getByText(/lists everything in scope/)).toBeInTheDocument();

        rerender(<SearchTopBar {...baseProps} searchMode="nlp" />);
        expect(screen.getByText(searchModeDescription("nlp"))).toBeInTheDocument();
        // The two engines' defaults differ; the natural-language line says why nothing shows yet
        // and that it reaches file contents, which the keyword line does not claim.
        expect(screen.getByText(/image and video scenes/)).toBeInTheDocument();
        expect(
            screen.getByText(/Describe what you are looking for to see results/)
        ).toBeInTheDocument();
        expect(screen.queryByText(/lists everything in scope/)).toBeNull();
    });

    it("names the query box for assistive technology by the mode it is in", () => {
        const { rerender } = render(<SearchTopBar {...baseProps} searchMode="keyword" />);
        expect(screen.getByRole("searchbox", { name: "Keyword search query" })).toBeInTheDocument();
        rerender(<SearchTopBar {...baseProps} searchMode="nlp" />);
        expect(
            screen.getByRole("searchbox", { name: "Natural-language search query" })
        ).toBeInTheDocument();
    });

    it("shows the result count only once a search has run", () => {
        const { rerender } = render(<SearchTopBar {...baseProps} />);
        expect(screen.queryByText(/results$/)).toBeNull();
        rerender(<SearchTopBar {...baseProps} resultCount={0} />);
        expect(screen.getByText("0 results")).toBeInTheDocument();
        rerender(<SearchTopBar {...baseProps} resultCount={1234} />);
        expect(screen.getByText("1,234 results")).toBeInTheDocument();
    });

    it("keeps the Search button at its label width and lets the query box take the rest", () => {
        render(<SearchTopBar {...baseProps} />);
        // jsdom does no layout, so the contract is checked on the styles that produce it: the
        // button's box never shrinks and its label never breaks (Cloudscape's wrapText={false});
        // the query box grows into the remaining width, and a row too narrow for both moves the
        // button below instead.
        const button = screen.getByRole("button", { name: "Search" });
        expect(button.className).toMatch(/button-no-wrap/);
        const buttonBox = button.parentElement as HTMLElement;
        expect(buttonBox).toHaveStyle({ flexShrink: "0" });
        const queryBox = screen.getByRole("searchbox").closest("[data-query-box]") as HTMLElement;
        expect(queryBox).toHaveStyle({ flexGrow: "1", minWidth: "0" });
        expect(queryBox.parentElement).toBe(buttonBox.parentElement);
        expect(buttonBox.parentElement).toHaveStyle({ display: "flex", flexWrap: "wrap" });
    });

    it("keeps Clear all filters as the header's only secondary action", () => {
        const onClearAll = jest.fn();
        const { rerender } = render(<SearchTopBar {...baseProps} onClearAll={onClearAll} />);
        expect(screen.queryByRole("button", { name: "Clear all filters" })).toBeNull();
        rerender(<SearchTopBar {...baseProps} onClearAll={onClearAll} hasActiveFilters />);
        expect(screen.getByRole("button", { name: "Clear all filters" })).toBeInTheDocument();
    });
});
