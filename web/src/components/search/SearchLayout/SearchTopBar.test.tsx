/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchTopBar from "./SearchTopBar";

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
