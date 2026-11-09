/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The abort and permanent-delete confirmations open from a row's menu, whose item closes with the
 * menu, so they return focus to the row's actions button when they close with the row still listed,
 * and to the board's search box once the action has taken the row off the list. The quick view
 * returns focus to the row that opened it, or to the row's actions button when its menu did.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionsBoard from "./ExecutionsBoard";
import { ToastProvider } from "../components/ToastProvider";
import type { Execution } from "../types";

jest.mock("../api/queries", () => ({
    useExecutions: jest.fn(),
    useExecutionActions: jest.fn(),
    useExecutionDetails: jest.fn(() => ({ data: undefined, isLoading: true, error: null })),
    useAllWorkflows: jest.fn(() => ({ data: [] })),
    useDatabases: jest.fn(() => ({ data: [] })),
    useAllPipelines: jest.fn(() => ({ data: [] })),
    useWorkflow: jest.fn(() => ({ data: undefined })),
}));
jest.mock("../permissions/useAllowedRoutes", () => ({ useAllowedRoutes: jest.fn() }));
jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useNavigate: () => jest.fn(),
}));

const { useExecutions, useExecutionActions } = jest.requireMock("../api/queries");
const { useAllowedRoutes } = jest.requireMock("../permissions/useAllowedRoutes");

const execution = (status: Execution["executionStatus"]): Execution => ({
    workflowExecutionId: "exec-1",
    workflowId: "wf-1",
    workflowDatabaseId: "db-1",
    executionStatus: status,
    executionStartDate: "2026-07-18T10:00:00Z",
});

const abortExecution = jest.fn();
const permanentDeleteExecution = jest.fn();
const rerunExecution = jest.fn();

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

const listing = (status: Execution["executionStatus"]) =>
    useExecutions.mockReturnValue({
        data: { pages: [{ Items: [execution(status)] }], pageParams: [] },
        isLoading: false,
        error: null,
        fetchNextPage: jest.fn(),
        refetch: jest.fn(),
        hasNextPage: false,
        isFetchingNextPage: false,
        isFetching: false,
    });

const actions = () => screen.getByRole("button", { name: "Execution actions", hidden: true });
const search = () => screen.getByRole("textbox", { name: "Search", hidden: true });

const openFromMenu = async (user: ReturnType<typeof userEvent.setup>, item: RegExp) => {
    actions().focus();
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("menuitem", { name: item }));
    return screen.findByRole("dialog");
};

const closed = () => waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

describe("ExecutionsBoard dialogs return focus", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });
        useExecutionActions.mockImplementation(() => ({
            abortExecution: { mutateAsync: abortExecution, isPending: false },
            rerunExecution: { mutateAsync: rerunExecution },
            permanentDeleteExecution: { mutateAsync: permanentDeleteExecution, isPending: false },
        }));
    });

    it("returns focus to the row's actions button after the abort confirmation's Escape and the abort", async () => {
        const user = userEvent.setup({ delay: null });
        abortExecution.mockResolvedValue({});
        listing("RUNNING");
        render(board());
        await openFromMenu(user, /^abort$/i);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());

        const dialog = await openFromMenu(user, /^abort$/i);
        await user.click(within(dialog).getByRole("button", { name: "Abort" }));
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());
    });

    it("returns focus to the search box after an abort on a board filtered by status", async () => {
        const user = userEvent.setup({ delay: null });
        abortExecution.mockResolvedValue({});
        listing("RUNNING");
        render(board());
        await user.selectOptions(
            screen.getByRole("combobox", { name: "Filter by status" }),
            "RUNNING"
        );
        const dialog = await openFromMenu(user, /^abort$/i);
        await user.click(within(dialog).getByRole("button", { name: "Abort" }));
        await closed();
        await waitFor(() => expect(search()).toHaveFocus());
    });

    it("returns focus to the row's actions button after Cancel, and to the search box after the delete", async () => {
        const user = userEvent.setup({ delay: null });
        permanentDeleteExecution.mockResolvedValue({});
        listing("SUCCEEDED");
        render(board());
        let dialog = await openFromMenu(user, /permanent delete/i);
        await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());

        dialog = await openFromMenu(user, /permanent delete/i);
        await user.type(within(dialog).getByPlaceholderText("CONFIRM"), "CONFIRM");
        await user.click(within(dialog).getByRole("button", { name: "Permanent Delete" }));
        await closed();
        await waitFor(() => expect(search()).toHaveFocus());
    });

    it("returns focus to the row that opened the quick view, or to its actions button from the menu", async () => {
        const user = userEvent.setup({ delay: null });
        listing("SUCCEEDED");
        render(board());
        const row = screen.getByText("exec-1").closest("tr") as HTMLElement;
        row.focus();
        await user.keyboard("{Enter}");
        await screen.findByRole("dialog", { name: "Execution Details" });
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(row).toHaveFocus());

        await openFromMenu(user, /view results/i);
        await user.keyboard("{Escape}");
        await closed();
        await waitFor(() => expect(actions()).toHaveFocus());
    });
});
