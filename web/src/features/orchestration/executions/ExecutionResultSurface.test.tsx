/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExecutionResultSurface from "./ExecutionResultSurface";

jest.mock("../api/queries", () => ({ useExecutionResults: jest.fn() }));
const { useExecutionResults } = jest.requireMock("../api/queries");

const results = (state: Record<string, unknown>) => ({
    data: { resultContentTruncated: false, resultRowCount: 1 },
    pageContents: ["page one text"],
    readFullText: jest.fn(),
    isLoading: false,
    isError: false,
    hasNextPage: true,
    isFetchingNextPage: false,
    fetchNextPage: jest.fn(),
    ...state,
});

describe("ExecutionResultSurface Load more", () => {
    it("keeps focus while the next page is read, says so and ignores a second press", async () => {
        const user = userEvent.setup({ delay: null });
        const fetchNextPage = jest.fn();
        useExecutionResults.mockReturnValue(results({ fetchNextPage }));
        const surface = () => (
            <ExecutionResultSurface executionId="exec-1" open onOpenChange={jest.fn()} />
        );
        const { rerender } = render(surface());
        const loadMore = screen.getByTestId("result-surface-load-more");
        loadMore.focus();
        await user.keyboard("{Enter}");
        expect(fetchNextPage).toHaveBeenCalledTimes(1);

        useExecutionResults.mockReturnValue(results({ fetchNextPage, isFetchingNextPage: true }));
        rerender(surface());
        expect(loadMore).toHaveAccessibleName("Loading…");
        expect(loadMore).toHaveAttribute("aria-disabled", "true");
        expect(loadMore).not.toBeDisabled();
        expect(loadMore).toHaveFocus();
        await user.keyboard("{Enter}");
        expect(fetchNextPage).toHaveBeenCalledTimes(1);
    });
});

describe("ExecutionResultSurface failures", () => {
    it("keeps the loaded pages when Load more fails, and says why beside it", async () => {
        const fetchNextPage = jest.fn();
        useExecutionResults.mockReturnValue(
            results({ isError: true, error: new Error("Service unavailable"), fetchNextPage })
        );
        render(<ExecutionResultSurface executionId="e1" open onOpenChange={jest.fn()} />);

        expect(screen.getByTestId("result-surface-content")).toHaveTextContent("page one text");
        expect(screen.getByRole("alert")).toHaveTextContent(
            "Could not load more of the result: Service unavailable. Choose Load more to try again."
        );
        await userEvent.click(screen.getByTestId("result-surface-load-more"));
        expect(fetchNextPage).toHaveBeenCalledTimes(1);
    });

    it("names why the first page failed and retries it", async () => {
        const refetch = jest.fn();
        useExecutionResults.mockReturnValue(
            results({
                data: undefined,
                pageContents: [],
                isError: true,
                error: new Error("Not Authorized"),
                hasNextPage: false,
                refetch,
            })
        );
        render(<ExecutionResultSurface executionId="e1" open onOpenChange={jest.fn()} />);

        expect(screen.getByRole("alert")).toHaveTextContent(
            "The result could not be loaded: Not Authorized"
        );
        expect(screen.queryByTestId("result-surface-content")).toBeNull();
        await userEvent.click(screen.getByRole("button", { name: "Retry" }));
        expect(refetch).toHaveBeenCalledTimes(1);
    });
});
