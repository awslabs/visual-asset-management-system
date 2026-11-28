/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// #998 D3: a SKIPPED record's detail page shows the skip dates instead of the fields that only make
// sense for a run that ran (duration, pipeline progress, output target).

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionDetailPage from "./ExecutionDetailPage";

jest.mock("@monaco-editor/react", () => ({ __esModule: true, default: () => null }));

jest.mock("../api/queries", () => ({
    useExecutionDetails: jest.fn(),
    useExecutionDetailMetadata: jest.fn(),
    useExecutionDetailFiles: jest.fn(),
    useExecutionResults: jest.fn(),
}));

jest.mock("../api/executions", () => ({ getExecutionLogs: jest.fn() }));

jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));

const IDLE = {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: jest.fn(),
};

const base = {
    workflowExecutionId: "s1",
    workflowId: "wf1",
    workflowDatabaseId: "db1",
    triggerType: "File-Upload",
    triggeredByUserId: "SYSTEM_USER",
    executionStartDate: "2026-11-06T10:00:00Z",
    executionStopDate: "2026-11-06T10:00:00Z",
    outputLocationType: "asset",
    outputDatabaseId: "db1",
    outputAssetId: "a1",
    pipelines: [],
    inputFiles: [],
    outputs: { files: [], metadata: [], results: [] },
};

function renderPage(execution: any) {
    const queries = require("../api/queries");
    queries.useExecutionDetails.mockReturnValue({ data: execution, isLoading: false, error: null });
    queries.useExecutionDetailMetadata.mockReturnValue(IDLE);
    queries.useExecutionDetailFiles.mockReturnValue(IDLE);
    queries.useExecutionResults.mockReturnValue({
        data: undefined,
        pageContents: [],
        isLoading: false,
        isError: false,
        error: null,
    });
    require("../permissions/useAllowedRoutes").useAllowedRoutes.mockReturnValue({
        loading: false,
        can: jest.fn(() => true),
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={queryClient}>
            <MemoryRouter>
                <ExecutionDetailPage executionId={execution.workflowExecutionId} />
            </MemoryRouter>
        </QueryClientProvider>
    );
}

describe("ExecutionDetailPage for a SKIPPED record", () => {
    beforeEach(() => jest.clearAllMocks());

    it("shows the first and last skipped times and no run-only fields", async () => {
        renderPage({
            ...base,
            executionStatus: "SKIPPED",
            skip: {
                reason: "A conflicting execution of this workflow is already running for this asset.",
                scope: "asset",
                count: 4,
                firstSkippedDate: "2026-11-06T10:00:00Z",
                lastSkippedDate: "2026-11-06T10:09:00Z",
                files: [],
                filesTruncated: false,
            },
        });
        expect((await screen.findAllByText("First skipped:")).length).toBeGreaterThan(0);
        expect(screen.getAllByText("Last skipped:").length).toBeGreaterThan(0);
        for (const label of [
            "Duration",
            "Start",
            "Stop",
            "Output Type",
            "Output Database ID",
            "Output Asset ID",
            "Output Path Prefix",
        ]) {
            expect(screen.queryByText(`${label}:`)).not.toBeInTheDocument();
        }
        await userEvent.click(screen.getByRole("tab", { name: /Outputs/i }));
        expect(screen.getByText(/did not run, so they wrote no/)).toBeInTheDocument();
        expect(screen.queryByText("Output Type:")).not.toBeInTheDocument();
    });

    it("keeps the run fields for any other status", async () => {
        renderPage({ ...base, executionStatus: "SUCCEEDED" });
        expect(await screen.findByText("Start:")).toBeInTheDocument();
        expect(screen.getByText("Output Type:")).toBeInTheDocument();
        expect(screen.getByText("Duration:")).toBeInTheDocument();
        expect(screen.queryByText("First skipped:")).not.toBeInTheDocument();
    });
});
