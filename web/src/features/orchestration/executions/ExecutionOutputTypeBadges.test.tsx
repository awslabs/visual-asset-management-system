/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ExecutionOutputTypeBadges from "./ExecutionOutputTypeBadges";

// The result surface fetches through useExecutionResults; drive it from the mock per test.
jest.mock("../api/queries", () => ({
    useExecutionResults: jest.fn(),
}));

const { useExecutionResults } = require("../api/queries");

function renderBadges(outputTypes?: string[]) {
    return render(
        <MemoryRouter>
            <ExecutionOutputTypeBadges executionId="e1" outputTypes={outputTypes} />
        </MemoryRouter>
    );
}

describe("ExecutionOutputTypeBadges", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        useExecutionResults.mockReturnValue({
            data: undefined,
            isLoading: false,
            isError: false,
        });
    });

    it("renders a badge per output type", () => {
        renderBadges(["File", "Metadata", "Result"]);
        expect(screen.getByTestId("output-type-badge-file")).toHaveTextContent("File");
        expect(screen.getByTestId("output-type-badge-metadata")).toHaveTextContent("Metadata");
        expect(screen.getByTestId("output-type-badge-result")).toHaveTextContent("Result");
    });

    it("shows a dash when the run produced no output types", () => {
        renderBadges([]);
        expect(screen.getByText("—")).toBeInTheDocument();
        expect(screen.queryByTestId("output-type-badge-result")).not.toBeInTheDocument();
    });

    it("treats an undefined outputTypes (legacy row) as empty", () => {
        renderBadges(undefined);
        expect(screen.getByText("—")).toBeInTheDocument();
    });

    it("renders File/Metadata as non-interactive and Result as a button", () => {
        renderBadges(["File", "Metadata", "Result"]);
        expect(screen.getByTestId("output-type-badge-result").tagName).toBe("BUTTON");
        expect(screen.getByTestId("output-type-badge-file").tagName).toBe("SPAN");
    });

    it("opens the result surface when the Result badge is clicked", async () => {
        useExecutionResults.mockReturnValue({
            data: {
                resultContent: "the full result text",
                resultContentTruncated: false,
                resultRowCount: 1,
                resultContentBytes: 20,
            },
            isLoading: false,
            isError: false,
        });
        renderBadges(["Result"]);
        fireEvent.click(screen.getByTestId("output-type-badge-result"));
        expect(await screen.findByTestId("result-surface-content")).toHaveTextContent(
            "the full result text"
        );
        // The query is enabled only once the surface opens.
        expect(useExecutionResults).toHaveBeenLastCalledWith("e1", true);
    });

    it("shows the truncation banner without a link to another view", async () => {
        useExecutionResults.mockReturnValue({
            data: {
                resultContent: "partial",
                resultContentTruncated: true,
                resultRowCount: 3,
                resultContentBytes: 5 * 1024 * 1024,
            },
            isLoading: false,
            isError: false,
        });
        renderBadges(["Result"]);
        fireEvent.click(screen.getByTestId("output-type-badge-result"));
        expect(await screen.findByTestId("result-surface-truncation")).toHaveTextContent(
            "not retrievable"
        );
        expect(screen.queryByText(/View full results/)).not.toBeInTheDocument();
    });

    it("offers Load more while pages remain and fetches the next page", async () => {
        const fetchNextPage = jest.fn();
        useExecutionResults.mockReturnValue({
            data: {
                resultContent: "first page",
                resultContentTruncated: false,
                resultRowCount: 2,
                resultContentBytes: 10,
            },
            isLoading: false,
            isError: false,
            hasNextPage: true,
            isFetchingNextPage: false,
            fetchNextPage,
        });
        renderBadges(["Result"]);
        fireEvent.click(screen.getByTestId("output-type-badge-result"));
        expect(await screen.findByTestId("result-surface-caption")).toHaveTextContent("loaded");
        fireEvent.click(screen.getByTestId("result-surface-load-more"));
        expect(fetchNextPage).toHaveBeenCalledTimes(1);
    });

    it("shows no Load more once every page is loaded", async () => {
        useExecutionResults.mockReturnValue({
            data: {
                resultContent: "all of it",
                resultContentTruncated: false,
                resultRowCount: 1,
                resultContentBytes: 9,
            },
            isLoading: false,
            isError: false,
            hasNextPage: false,
        });
        renderBadges(["Result"]);
        fireEvent.click(screen.getByTestId("output-type-badge-result"));
        await screen.findByTestId("result-surface-content");
        expect(screen.queryByTestId("result-surface-load-more")).not.toBeInTheDocument();
    });

    it("shows a spinner while the result is loading", async () => {
        useExecutionResults.mockReturnValue({
            data: undefined,
            isLoading: true,
            isError: false,
        });
        renderBadges(["Result"]);
        fireEvent.click(screen.getByTestId("output-type-badge-result"));
        expect(await screen.findByTestId("result-surface-loading")).toBeInTheDocument();
    });

    // The surface is a controlled dialog with no Radix Trigger, so focus would otherwise land on
    // the body when it closes and a keyboard user loses their place in the table.
    describe("focus return on close", () => {
        const openFromBadge = async () => {
            useExecutionResults.mockReturnValue({
                data: {
                    resultContent: "text",
                    resultContentTruncated: false,
                    resultRowCount: 1,
                    resultContentBytes: 4,
                },
                isLoading: false,
                isError: false,
            });
            renderBadges(["File", "Result"]);
            const badge = screen.getByTestId("output-type-badge-result");
            badge.focus();
            fireEvent.click(badge);
            await screen.findByTestId("result-surface-content");
            return badge;
        };

        it("returns focus to the Result badge on Escape", async () => {
            const badge = await openFromBadge();
            fireEvent.keyDown(document.activeElement || document.body, {
                key: "Escape",
                code: "Escape",
            });
            await waitFor(() =>
                expect(screen.queryByTestId("result-surface-content")).not.toBeInTheDocument()
            );
            expect(badge).toHaveFocus();
        });

        it("returns focus to the Result badge when the close button is used", async () => {
            const badge = await openFromBadge();
            fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
            await waitFor(() =>
                expect(screen.queryByTestId("result-surface-content")).not.toBeInTheDocument()
            );
            expect(badge).toHaveFocus();
        });
    });
});
