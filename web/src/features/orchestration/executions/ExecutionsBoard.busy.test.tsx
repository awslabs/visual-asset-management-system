/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Executions board's refresh, Load more and its abort and permanent-delete confirmations keep
 * keyboard focus while their request runs: each is `aria-disabled` rather than `disabled`, ignores
 * a second press and says it is working, and a confirmation stays open until it is answered.
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionsBoard from "./ExecutionsBoard";
import { ToastProvider } from "../components/ToastProvider";
import type { Execution } from "../types";

jest.mock("../api/queries", () => ({
    useExecutions: jest.fn(),
    useExecutionActions: jest.fn(),
    useExecutionDetails: jest.fn(),
    useAllWorkflows: jest.fn(() => ({ data: [] })),
    useDatabases: jest.fn(() => ({ data: [] })),
    useAllPipelines: jest.fn(() => ({ data: [] })),
    useWorkflow: jest.fn(() => ({ data: undefined })),
}));
jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));
const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useNavigate: () => mockNavigate,
}));

const { useExecutions, useExecutionActions } = jest.requireMock("../api/queries");
const { useAllowedRoutes } = jest.requireMock("../permissions/useAllowedRoutes");

const execution = (id: string, status: Execution["executionStatus"]): Execution => ({
    workflowExecutionId: id,
    workflowId: "wf-1",
    workflowDatabaseId: "db-1",
    executionStatus: status,
    executionStartDate: "2026-07-18T10:00:00Z",
});

const fetchNextPage = jest.fn();
const refetch = jest.fn();
const listState = (pages: Execution[][], state: Record<string, unknown> = {}) => ({
    data: { pages: pages.map((Items) => ({ Items })), pageParams: [] },
    isLoading: false,
    error: null,
    fetchNextPage,
    refetch,
    hasNextPage: false,
    isFetchingNextPage: false,
    isFetching: false,
    ...state,
});

// Each mutation reads whether it is in flight from here, so a test can flip it and re-render.
const pending = { abort: false, delete: false };
// Requests that stay in flight: their executor never settles them.
const abortExecution = jest.fn(() => new Promise(jest.fn()));
const permanentDeleteExecution = jest.fn(() => new Promise(jest.fn()));
// Stable, as the hook's own is: the board's columns, and so its row controls, are built from it.
const rerunExecution = jest.fn();

// The provider the module mounts, so the board's handlers, and the row controls built from them,
// keep their identity across renders as they do on the page.
const queryClient = new QueryClient();
const board = () => (
    <QueryClientProvider client={queryClient}>
        <ToastProvider>
            <MemoryRouter>
                <ExecutionsBoard scope={{ kind: "global" }} />
            </MemoryRouter>
        </ToastProvider>
    </QueryClientProvider>
);

describe("ExecutionsBoard while a request runs", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        pending.abort = false;
        pending.delete = false;
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });
        useExecutionActions.mockImplementation(() => ({
            abortExecution: { mutateAsync: abortExecution, isPending: pending.abort },
            rerunExecution: { mutateAsync: rerunExecution },
            permanentDeleteExecution: {
                mutateAsync: permanentDeleteExecution,
                isPending: pending.delete,
            },
        }));
    });

    it("keeps focus on the refresh button while the list is read, and after", async () => {
        const user = userEvent.setup({ delay: null });
        useExecutions.mockReturnValue(listState([[execution("exec-1", "SUCCEEDED")]]));
        const { rerender } = render(board());
        const refresh = screen.getByRole("button", { name: "Refresh" });
        refresh.focus();
        await user.keyboard("{Enter}");
        expect(refetch).toHaveBeenCalledTimes(1);

        // A background poll or the press: either way the read is in flight.
        useExecutions.mockReturnValue(
            listState([[execution("exec-1", "SUCCEEDED")]], { isFetching: true })
        );
        rerender(board());
        expect(refresh).not.toBeDisabled();
        expect(refresh).toHaveAttribute("aria-disabled", "true");
        expect(refresh).toHaveAttribute("aria-busy", "true");
        expect(refresh).toHaveFocus();
        await user.keyboard("{Enter}");
        expect(refetch).toHaveBeenCalledTimes(1);
    });

    it("keeps focus on Load more while the page is read, and hands it to the first row loaded after the last", async () => {
        const user = userEvent.setup({ delay: null });
        const first = [execution("exec-1", "SUCCEEDED")];
        const last = [execution("exec-2", "SUCCEEDED")];
        useExecutions.mockReturnValue(listState([first], { hasNextPage: true }));
        const { rerender } = render(board());
        const loadMore = screen.getByRole("button", { name: "Load more" });
        loadMore.focus();
        await user.keyboard("{Enter}");
        expect(fetchNextPage).toHaveBeenCalledTimes(1);

        useExecutions.mockReturnValue(
            listState([first], { hasNextPage: true, isFetchingNextPage: true })
        );
        rerender(board());
        expect(loadMore).toHaveAccessibleName("Loading more...");
        expect(loadMore).toHaveAttribute("aria-disabled", "true");
        expect(loadMore).not.toBeDisabled();
        expect(loadMore).toHaveFocus();
        await user.keyboard("{Enter}");
        expect(fetchNextPage).toHaveBeenCalledTimes(1);

        useExecutions.mockReturnValue(listState([first, last]));
        rerender(board());
        await act(() => Promise.resolve());
        expect(screen.getByText("exec-2").closest("tr")).toHaveFocus();
    });

    it("holds the abort confirmation while the abort runs", async () => {
        const user = userEvent.setup({ delay: null });
        useExecutions.mockReturnValue(listState([[execution("exec-1", "RUNNING")]]));
        const { rerender } = render(board());
        await user.click(screen.getByRole("button", { name: "Execution actions" }));
        await user.click(await screen.findByRole("menuitem", { name: /abort/i }));
        const abort = await screen.findByRole("button", { name: "Abort" });
        abort.focus();
        await user.keyboard("{Enter}");
        expect(abortExecution).toHaveBeenCalledTimes(1);

        pending.abort = true;
        rerender(board());
        expect(abort).toHaveAccessibleName("Aborting...");
        expect(abort).toHaveAttribute("aria-disabled", "true");
        expect(abort).not.toBeDisabled();
        expect(abort).toHaveFocus();
        await user.keyboard("{Enter}");
        await user.keyboard("{Escape}");
        await user.click(screen.getByRole("button", { name: "Cancel" }));
        expect(abortExecution).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Close dialog" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
    });

    it("holds the permanent-delete confirmation while the delete runs, its field read-only", async () => {
        const user = userEvent.setup({ delay: null });
        useExecutions.mockReturnValue(listState([[execution("exec-1", "SUCCEEDED")]]));
        const { rerender } = render(board());
        await user.click(screen.getByRole("button", { name: "Execution actions" }));
        await user.click(await screen.findByRole("menuitem", { name: /permanent delete/i }));
        const field = screen.getByPlaceholderText("CONFIRM");
        await user.type(field, "CONFIRM{Enter}");
        expect(permanentDeleteExecution).toHaveBeenCalledTimes(1);

        pending.delete = true;
        rerender(board());
        const remove = screen.getByRole("button", { name: "Deleting..." });
        expect(remove).toHaveAttribute("aria-disabled", "true");
        expect(remove).not.toBeDisabled();
        expect(field).toHaveFocus();
        expect(field).toHaveAttribute("readonly");
        expect(field).toHaveAttribute("aria-disabled", "true");
        await user.keyboard("{Enter}");
        await user.click(remove);
        await user.keyboard("{Escape}");
        expect(permanentDeleteExecution).toHaveBeenCalledTimes(1);
        expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
});
