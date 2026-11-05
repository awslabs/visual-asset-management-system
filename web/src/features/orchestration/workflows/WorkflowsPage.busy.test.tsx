/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Workflows toolbar's refresh, Load more and the archive confirmation keep keyboard focus while
 * their request runs: each is `aria-disabled` rather than `disabled`, ignores a second press and
 * says it is working.
 */

import React from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import WorkflowsPage from "./WorkflowsPage";
import * as queries from "../api/queries";
import * as useAllowedRoutesModule from "../permissions/useAllowedRoutes";

jest.mock("../api/queries", () => ({
    useWorkflows: jest.fn(),
    useWorkflowMutations: jest.fn(),
    useDatabases: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));
jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));
jest.mock("../executions/ExecuteWorkflowModal", () => ({ __esModule: true, default: () => null }));

const workflow = (workflowId: string, workflowName: string, category: string) => ({
    workflowId,
    workflowName,
    databaseId: "db1",
    category,
    enabled: true,
    archived: false,
    specifiedPipelines: [{ pipelineId: "p1" }],
});
const PAGE_ONE = [workflow("wf-1", "Workflow Alpha", "Processing")];
const PAGE_TWO = [workflow("wf-2", "Workflow Beta", "Zeta")];

const fetchNextPage = jest.fn();
const refetch = jest.fn();
const listState = (pages: any[][], state: Record<string, unknown> = {}) => ({
    data: { pages: pages.map((Items) => ({ Items })), pageParams: pages.map(() => undefined) },
    isLoading: false,
    error: null,
    fetchNextPage,
    refetch,
    hasNextPage: false,
    isFetchingNextPage: false,
    isFetching: false,
    ...state,
});

const page = () => (
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <WorkflowsPage databaseId="db1" />
        </MemoryRouter>
    </QueryClientProvider>
);

describe("WorkflowsPage while a request runs", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });
        (queries.useWorkflowMutations as jest.Mock).mockReturnValue({
            archiveWorkflow: { mutateAsync: jest.fn(), isPending: false },
        });
    });

    it("keeps focus on the refresh button while the list is read, and after", async () => {
        const user = userEvent.setup({ delay: null });
        const useWorkflows = queries.useWorkflows as jest.Mock;
        useWorkflows.mockReturnValue(listState([PAGE_ONE]));
        const { rerender } = render(page());
        const refresh = screen.getByRole("button", { name: "Refresh" });
        refresh.focus();
        await user.keyboard("{Enter}");
        expect(refetch).toHaveBeenCalledTimes(1);

        useWorkflows.mockReturnValue(listState([PAGE_ONE], { isFetching: true }));
        rerender(page());
        expect(refresh).not.toBeDisabled();
        expect(refresh).toHaveAttribute("aria-disabled", "true");
        expect(refresh).toHaveAttribute("aria-busy", "true");
        expect(refresh).toHaveFocus();
        await user.keyboard("{Enter}");
        expect(refetch).toHaveBeenCalledTimes(1);

        useWorkflows.mockReturnValue(listState([PAGE_ONE]));
        rerender(page());
        expect(refresh).toHaveFocus();
    });

    it("keeps focus on Load more while the page is read, and hands it to the page's rows after the last", async () => {
        const user = userEvent.setup({ delay: null });
        const useWorkflows = queries.useWorkflows as jest.Mock;
        useWorkflows.mockReturnValue(listState([PAGE_ONE], { hasNextPage: true }));
        const { rerender } = render(page());
        const loadMore = screen.getByRole("button", { name: "Load more" });
        loadMore.focus();
        await user.keyboard("{Enter}");
        expect(fetchNextPage).toHaveBeenCalledTimes(1);

        useWorkflows.mockReturnValue(
            listState([PAGE_ONE], { hasNextPage: true, isFetchingNextPage: true })
        );
        rerender(page());
        expect(loadMore).toHaveAccessibleName("Loading more...");
        expect(loadMore).toHaveAttribute("aria-disabled", "true");
        expect(loadMore).not.toBeDisabled();
        expect(loadMore).toHaveFocus();
        await user.keyboard("{Enter}");
        expect(fetchNextPage).toHaveBeenCalledTimes(1);

        useWorkflows.mockReturnValue(listState([PAGE_ONE, PAGE_TWO]));
        rerender(page());
        await act(() => Promise.resolve());
        expect(screen.getByRole("button", { name: /Zeta/ })).toHaveFocus();
    });

    it("holds the archive confirmation while the archive runs", async () => {
        const user = userEvent.setup({ delay: null });
        (queries.useWorkflows as jest.Mock).mockReturnValue(listState([PAGE_ONE]));
        (queries.useWorkflowMutations as jest.Mock).mockReturnValue({
            archiveWorkflow: {
                mutateAsync: jest.fn(() => new Promise(jest.fn())),
                isPending: true,
            },
        });
        render(page());
        screen.getByLabelText("Actions for Workflow Alpha").focus();
        await user.keyboard("{Enter}");
        await user.click(await screen.findByRole("menuitem", { name: "Archive" }));
        const archive = await screen.findByRole("button", { name: "Archiving..." });
        expect(archive).toHaveAttribute("aria-disabled", "true");
        await user.keyboard("{Escape}");
        expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
});
