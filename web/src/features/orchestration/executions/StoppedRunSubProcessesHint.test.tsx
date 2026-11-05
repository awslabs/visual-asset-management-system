/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionDetailPage from "./ExecutionDetailPage";
import { stillRunningSubProcessCount } from "./StoppedRunSubProcessesHint";

jest.mock("@monaco-editor/react", () => ({ __esModule: true, default: () => null }));

const mockAbort = jest.fn();
jest.mock("../api/queries", () => ({
    useExecutionDetails: jest.fn(),
    useExecutionDetailMetadata: jest.fn(() => ({
        data: undefined,
        isLoading: false,
        isError: false,
        error: null,
        hasNextPage: false,
        isFetchingNextPage: false,
        fetchNextPage: jest.fn(),
    })),
    useExecutionDetailFiles: jest.fn(() => ({
        data: undefined,
        isLoading: false,
        isError: false,
        error: null,
        hasNextPage: false,
        isFetchingNextPage: false,
        fetchNextPage: jest.fn(),
    })),
    useExecutionResults: jest.fn(() => ({
        data: undefined,
        pageContents: [],
        isLoading: false,
        isError: false,
        error: null,
    })),
    useExecutionActions: jest.fn(() => ({
        abortExecution: { mutateAsync: mockAbort, isPending: false },
    })),
}));
jest.mock("../api/executions", () => ({ getExecutionLogs: jest.fn() }));
jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));

const sub = (status: string) => ({
    resourceType: "batchJob",
    label: "Container job",
    resourceName: "job-1",
    status,
    stageSource: "none",
    stages: [],
});

const execution = (executionStatus: string, subStatus: string, pipelineStatus = "ABORTED") => ({
    workflowExecutionId: "e-ext",
    workflowId: "wf-1",
    workflowDatabaseId: "db-1",
    executionStatus,
    executionStartDate: "2026-11-03T09:00:00Z",
    executionStopDate: "2026-11-03T09:15:00Z",
    pipelines: [
        {
            pipelineId: "p1",
            name: "Batch step",
            pipelineExecutionId: "pe-1",
            executionStatus: pipelineStatus,
            subExecutions: [sub(subStatus)],
        },
    ],
});

function renderPage(data: any, canAbort = true) {
    const { useExecutionDetails } = require("../api/queries");
    const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
    useExecutionDetails.mockReturnValue({ data, isLoading: false, error: null });
    useAllowedRoutes.mockReturnValue({
        loading: false,
        can: jest.fn((method: string, path: string) =>
            method === "DELETE" && path === "/workflows/executions/{executionId}" ? canAbort : true
        ),
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={queryClient}>
            <MemoryRouter>
                <ExecutionDetailPage executionId="e-ext" />
            </MemoryRouter>
        </QueryClientProvider>
    );
}

describe("stopped run with sub-processes still running (#1020)", () => {
    beforeEach(() => jest.clearAllMocks());

    it("counts RUNNING sub-processes only under a stopped execution", () => {
        expect(stillRunningSubProcessCount(execution("ABORTED", "RUNNING") as any)).toBe(1);
        expect(stillRunningSubProcessCount(execution("TIMED_OUT", "RUNNING") as any)).toBe(1);
        expect(stillRunningSubProcessCount(execution("ABORTED", "ABORTED") as any)).toBe(0);
        expect(stillRunningSubProcessCount(execution("RUNNING", "RUNNING", "RUNNING") as any)).toBe(
            0
        );
        expect(stillRunningSubProcessCount(execution("SUCCEEDED", "RUNNING") as any)).toBe(0);
        expect(stillRunningSubProcessCount(undefined)).toBe(0);
    });

    it("counts only the steps Abort sweeps, so the hint never promises a stop Abort skips", () => {
        // Abort stops the sub-processes of unfinished steps and of steps VAMS marked stopped
        // (ABORTED, or TIMED_OUT by a read of a run that timed out outside VAMS).
        for (const pipelineStatus of ["ABORTED", "TIMED_OUT", "RUNNING", "NEW"]) {
            expect(
                stillRunningSubProcessCount(
                    execution("TIMED_OUT", "RUNNING", pipelineStatus) as any
                )
            ).toBe(1);
        }
        // A step that finished on its own is left alone by Abort.
        for (const pipelineStatus of ["SUCCEEDED", "FAILED"]) {
            expect(
                stillRunningSubProcessCount(execution("FAILED", "RUNNING", pipelineStatus) as any)
            ).toBe(0);
        }
    });

    it("shows no hint when the only running sub-process is under a step that finished", async () => {
        renderPage(execution("FAILED", "RUNNING", "FAILED"));
        await screen.findByText("Execution Detail");
        expect(screen.queryByTestId("stopped-run-subprocesses-hint")).not.toBeInTheDocument();
    });

    it("shows the hint with an Abort that aborts this execution", async () => {
        mockAbort.mockResolvedValue({ message: "ok" });
        renderPage(execution("ABORTED", "RUNNING"));
        const hint = await screen.findByTestId("stopped-run-subprocesses-hint");
        expect(hint).toHaveTextContent(/has stopped, but 1 sub-process .* is still running/);
        expect(hint).toHaveTextContent(/Abort stops them/);
        await userEvent.click(screen.getByRole("button", { name: "Abort" }));
        await waitFor(() => expect(mockAbort).toHaveBeenCalledWith({ executionId: "e-ext" }));
    });

    it("keeps the hint but hides the button for a caller who cannot abort", async () => {
        renderPage(execution("ABORTED", "RUNNING"), false);
        expect(await screen.findByTestId("stopped-run-subprocesses-hint")).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Abort" })).not.toBeInTheDocument();
    });

    it("shows no hint once the sub-processes have stopped", async () => {
        renderPage(execution("ABORTED", "ABORTED"));
        await screen.findByText("Execution Detail");
        expect(screen.queryByTestId("stopped-run-subprocesses-hint")).not.toBeInTheDocument();
    });

    it("shows no hint for a run that is still going", async () => {
        renderPage(execution("RUNNING", "RUNNING", "RUNNING"));
        await screen.findByText("Execution Detail");
        expect(screen.queryByTestId("stopped-run-subprocesses-hint")).not.toBeInTheDocument();
    });
});
