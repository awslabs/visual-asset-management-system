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

    it("shows the truncation banner and a View full results link when truncated", async () => {
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
        expect(await screen.findByTestId("result-surface-truncation")).toBeInTheDocument();
        expect(screen.getByTestId("result-surface-view-full")).toBeInTheDocument();
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
});
