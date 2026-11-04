/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The archive confirmation and the execute dialog open from a row's menu, whose item closes with the
 * menu, so they return focus to the row's actions button when they close, and the archive
 * confirmation returns it to the list's search box once the archive has taken the row off the list.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
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
    useAllWorkflows: jest.fn(() => ({ data: [] })),
    useAllPipelines: jest.fn(() => ({ data: [] })),
    // The preset workflow is still loading, so the execute dialog shows its spinner and Cancel.
    useWorkflow: jest.fn(() => ({ data: undefined, isError: false })),
}));
jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));

const WORKFLOW = {
    workflowId: "wf-1",
    workflowName: "Workflow Alpha",
    databaseId: "db1",
    category: "Processing",
    enabled: true,
    archived: false,
    specifiedPipelines: [{ pipelineId: "p1" }],
};

const archive = jest.fn();

const page = () => (
    <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
            <WorkflowsPage databaseId="db1" />
        </MemoryRouter>
    </QueryClientProvider>
);

const actions = () =>
    screen.getByRole("button", { name: "Actions for Workflow Alpha", hidden: true });

const openFromMenu = async (user: ReturnType<typeof userEvent.setup>, item: string) => {
    actions().focus();
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("menuitem", { name: item }));
    return screen.findByRole("dialog");
};

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

describe("WorkflowsPage dialogs return focus", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({
            loading: false,
            can: () => true,
        });
        (queries.useWorkflowMutations as jest.Mock).mockReturnValue({
            archiveWorkflow: { mutateAsync: archive, isPending: false },
        });
        (queries.useWorkflows as jest.Mock).mockReturnValue({
            data: { pages: [{ Items: [WORKFLOW] }], pageParams: [undefined] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            refetch: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
            isFetching: false,
        });
    });

    it("returns focus to the row's actions button after the archive confirmation's Cancel", async () => {
        const user = userEvent.setup({ delay: null });
        render(page());
        const dialog = await openFromMenu(user, "Archive");
        await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());
    });

    it("returns focus to the search box once the archive took the row off the list", async () => {
        const user = userEvent.setup({ delay: null });
        archive.mockResolvedValue({});
        render(page());
        const dialog = await openFromMenu(user, "Archive");
        await user.click(within(dialog).getByRole("button", { name: "Archive" }));
        await closed();
        await waitFor(() => expect(screen.getByRole("textbox", { name: "Search" })).toHaveFocus());
    });

    it("returns focus to the row's actions button after the execute dialog's Escape and Cancel", async () => {
        const user = userEvent.setup({ delay: null });
        render(page());
        await openFromMenu(user, "Execute");
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());

        const dialog = await openFromMenu(user, "Execute");
        await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());
    });
});
