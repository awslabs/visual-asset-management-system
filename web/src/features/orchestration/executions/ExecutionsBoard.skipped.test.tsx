/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// #998: SKIPPED records (file-upload trigger launches a concurrency restriction refused) on the
// executions board: the Skipped badge with its count and reason, the status filter that selects or
// hides them, no Abort in their row menu, and the detail page's skip panel.

import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionsBoard, { EXCLUDE_SKIPPED_FILTER, ExecutionStatusCell } from "./ExecutionsBoard";
import ExecutionRowActions from "./ExecutionRowActions";
import SkippedLaunchPanel from "./SkippedLaunchPanel";
import type { Execution, ExecutionSkip } from "../types";

jest.mock("../api/queries", () => ({
    useExecutions: jest.fn(),
    useExecutionActions: jest.fn(),
    useExecutionDetails: jest.fn(),
    useAllWorkflows: jest.fn(() => ({ data: [] })),
    useDatabases: jest.fn(() => ({ data: [] })),
    useAllPipelines: jest.fn(() => ({ data: [] })),
    useWorkflow: jest.fn(() => ({ data: undefined })),
}));

jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

const REASON = "A conflicting execution of this workflow is already running for this asset.";

const skipped: Execution = {
    workflowExecutionId: "s1",
    workflowId: "wf1",
    workflowDatabaseId: "db1",
    executionStatus: "SKIPPED",
    triggerType: "File-Upload",
    triggeredByUserId: "SYSTEM_USER",
    executionStartDate: "2026-11-06T10:00:00Z",
    executionStopDate: "2026-11-06T10:00:00Z",
    skippedLaunchCount: 49,
    skipScope: "asset",
    skipReason: REASON,
};

const running: Execution = {
    workflowExecutionId: "r1",
    workflowId: "wf1",
    workflowDatabaseId: "db1",
    executionStatus: "RUNNING",
    executionStartDate: "2026-11-06T09:59:00Z",
};

function renderBoard() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={queryClient}>
            <MemoryRouter>
                <ExecutionsBoard scope={{ kind: "global" } as any} />
            </MemoryRouter>
        </QueryClientProvider>
    );
}

describe("SKIPPED executions", () => {
    let useExecutions: jest.Mock;

    beforeEach(() => {
        jest.clearAllMocks();
        const queries = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutions = queries.useExecutions;
        queries.useExecutionActions.mockReturnValue({
            abortExecution: { mutateAsync: jest.fn() },
            rerunExecution: { mutateAsync: jest.fn() },
            permanentDeleteExecution: { mutateAsync: jest.fn() },
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });
        useExecutions.mockReturnValue({
            data: { pages: [{ Items: [skipped, running] }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
            refetch: jest.fn(),
            isFetching: false,
        });
    });

    it("shows the Skipped badge with the launch count and the API's reason", () => {
        render(<ExecutionStatusCell execution={skipped} />);
        const cell = screen.getByTestId("skipped-status");
        expect(within(cell).getByText("Skipped")).toBeInTheDocument();
        expect(cell).toHaveTextContent("49 launches skipped");
        expect(cell).toHaveAttribute("title", REASON);
    });

    it("renders other statuses as before", () => {
        render(<ExecutionStatusCell execution={running} />);
        expect(screen.queryByTestId("skipped-status")).not.toBeInTheDocument();
        expect(screen.getByText("Running")).toBeInTheDocument();
    });

    it("lists skipped records by default and hides them through excludeStatus", async () => {
        renderBoard();
        expect(screen.getByText("s1")).toBeInTheDocument();
        const firstFilters = useExecutions.mock.calls[0][1];
        expect(firstFilters.excludeStatus).toBeUndefined();
        expect(firstFilters.status).toBeUndefined();

        await userEvent.selectOptions(
            screen.getByLabelText("Filter by status"),
            EXCLUDE_SKIPPED_FILTER
        );
        // The server drops the rows; the board sends the exclusion and no status filter.
        const lastFilters = useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
        expect(lastFilters.excludeStatus).toBe("SKIPPED");
        expect(lastFilters.status).toBeUndefined();
    });

    it("sends SKIPPED to the API when the Skipped status is chosen", async () => {
        renderBoard();
        await userEvent.selectOptions(screen.getByLabelText("Filter by status"), "SKIPPED");
        const lastFilters = useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
        expect(lastFilters.status).toBe("SKIPPED");
    });

    it("offers no Abort for a skipped record", async () => {
        render(
            <ExecutionRowActions
                execution={{ ...skipped, executionGroupId: "g1" }}
                can={() => true}
                onView={jest.fn()}
                onAbort={jest.fn()}
                onAbortGroup={jest.fn()}
                onRerun={jest.fn()}
                onLogs={jest.fn()}
                onPermanentDelete={jest.fn()}
                onOpenDetails={jest.fn()}
            />
        );
        await userEvent.click(screen.getByLabelText("Execution actions"));
        expect(screen.queryByText("Abort")).not.toBeInTheDocument();
        expect(screen.queryByText("Abort group")).not.toBeInTheDocument();
        expect(screen.getByText("Rerun")).toBeInTheDocument();
        expect(screen.getByText("Permanent delete")).toBeInTheDocument();
    });
});

describe("SkippedLaunchPanel", () => {
    const skip: ExecutionSkip = {
        reason: REASON,
        scope: "asset",
        count: 49,
        firstSkippedDate: "2026-11-06T10:00:00Z",
        lastSkippedDate: "2026-11-06T10:09:00Z",
        files: [
            { databaseId: "db1", assetId: "a1", inputAssetFileKey: "/a1/f.glb", versionId: "v1" },
        ],
        filesTruncated: true,
        blockingExecutionId: "e-blocking",
    };

    const renderPanel = (value: ExecutionSkip) =>
        render(
            <MemoryRouter>
                <SkippedLaunchPanel skip={value} />
            </MemoryRouter>
        );

    it("shows the reason, the count, the files and the blocking run the API returned", () => {
        renderPanel(skip);
        const panel = screen.getByTestId("skipped-launch-panel");
        expect(panel).toHaveTextContent("49 file-upload launches were skipped and did not run.");
        expect(panel).toHaveTextContent(REASON);
        expect(panel).toHaveTextContent("Per asset");
        expect(panel).toHaveTextContent("/a1/f.glb (version v1)");
        expect(panel).toHaveTextContent("Not every skipped file is listed here.");
        expect(screen.getByRole("link", { name: "e-blocking" })).toHaveAttribute(
            "href",
            "/executions/e-blocking"
        );
    });

    it("shows no blocking run when the API withheld it", () => {
        const withheld: ExecutionSkip = { ...skip, blockingExecutionId: undefined };
        renderPanel({ ...withheld, filesTruncated: false });
        expect(screen.queryByText("Blocked by:")).not.toBeInTheDocument();
        expect(screen.queryByRole("link")).not.toBeInTheDocument();
        expect(screen.queryByText(/Not every skipped file/)).not.toBeInTheDocument();
    });
});
