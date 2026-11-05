/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * "Show system runs" on the Executions board (#1121).
 *
 * The server hides system workflow runs unless the list request carries includeSystem, and shows them
 * by default on a list scoped to one system workflow, reporting the applied value as `includeSystem`.
 * The board keeps the user's choice in the URL, sends it only when chosen, ticks the box from the
 * server's echo otherwise, and badges system rows. It never works the default out itself.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import ExecutionsBoard, { INCLUDE_SYSTEM_PARAM } from "./ExecutionsBoard";
import type { ExecutionScope } from "../api/queries";

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

const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useNavigate: () => mockNavigate,
}));

let lastSearch = "";
const LocationProbe: React.FC = () => {
    lastSearch = useLocation().search;
    return null;
};

const page = (items: any[], extra: Record<string, unknown> = {}) => ({
    data: { pages: [{ Items: items, ...extra }], pageParams: [] },
    isLoading: false,
    error: null,
    fetchNextPage: jest.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
    refetch: jest.fn(),
    isFetching: false,
});

const row = (id: string, isSystem?: boolean) => ({
    workflowExecutionId: id,
    workflowId: `wf-${id}`,
    workflowDatabaseId: "db-1",
    executionStatus: "SUCCEEDED",
    executionStartDate: "2026-07-18T10:00:00Z",
    executionStopDate: "2026-07-18T10:01:00Z",
    ...(isSystem === undefined ? {} : { isSystem }),
});

function renderBoard(scope: ExecutionScope, initialEntry = "/executions") {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
        <QueryClientProvider client={queryClient}>
            <MemoryRouter initialEntries={[initialEntry]}>
                <ExecutionsBoard scope={scope} />
                <LocationProbe />
            </MemoryRouter>
        </QueryClientProvider>
    );
}

/** The filters object the board handed useExecutions on its latest render. */
const lastFilters = (): Record<string, string> => {
    const { useExecutions } = require("../api/queries");
    const calls = useExecutions.mock.calls;
    return calls[calls.length - 1][1];
};

describe("ExecutionsBoard — Show system runs", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        lastSearch = "";
        const { useExecutionActions } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionActions.mockReturnValue({
            abortExecution: { mutateAsync: jest.fn() },
            rerunExecution: { mutateAsync: jest.fn() },
            permanentDeleteExecution: { mutateAsync: jest.fn() },
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });
    });

    it("sends nothing by default, leaves the box clear and says system runs are hidden", () => {
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue(page([], { includeSystem: false }));
        renderBoard({ kind: "global" });
        expect(lastFilters()[INCLUDE_SYSTEM_PARAM]).toBeUndefined();
        expect(screen.getByRole("checkbox", { name: "Show system runs" })).not.toBeChecked();
        expect(screen.getByText(/System workflow runs are hidden/)).toBeInTheDocument();
    });

    it("ticking writes includeSystem=true to the URL and sends it", async () => {
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue(page([row("e1")], { includeSystem: false }));
        renderBoard({ kind: "global" }, "/executions?workflowId=x&workflowDatabaseId=y");
        await userEvent.click(screen.getByRole("checkbox", { name: "Show system runs" }));
        expect(new URLSearchParams(lastSearch).get(INCLUDE_SYSTEM_PARAM)).toBe("true");
        // The other query parameters of the page survive the update.
        expect(new URLSearchParams(lastSearch).get("workflowId")).toBe("x");
        expect(lastFilters()[INCLUDE_SYSTEM_PARAM]).toBe("true");
    });

    it("restores the choice from the URL", () => {
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue(page([row("s1", true)], { includeSystem: true }));
        renderBoard({ kind: "asset", databaseId: "db", assetId: "a1" }, "/a?includeSystem=true");
        expect(lastFilters()[INCLUDE_SYSTEM_PARAM]).toBe("true");
        expect(screen.getByRole("checkbox", { name: "Show system runs" })).toBeChecked();
    });

    it("ticks the box from the server's echo on a system-workflow scope, and unticking sends false", async () => {
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue(page([row("s1", true)], { includeSystem: true }));
        renderBoard({ kind: "workflow", databaseId: "GLOBAL", workflowId: "sys-wf" });
        const box = screen.getByRole("checkbox", { name: "Show system runs" });
        expect(box).toBeChecked();
        expect(lastFilters()[INCLUDE_SYSTEM_PARAM]).toBeUndefined();
        await userEvent.click(box);
        expect(new URLSearchParams(lastSearch).get(INCLUDE_SYSTEM_PARAM)).toBe("false");
        expect(lastFilters()[INCLUDE_SYSTEM_PARAM]).toBe("false");
    });

    it("unticking an explicit choice on a default-hidden list drops the parameter", async () => {
        const { useExecutions } = require("../api/queries");
        // The server echoes the explicit true; once the param is gone it would echo false.
        useExecutions.mockReturnValue(page([row("u1", false)], { includeSystem: false }));
        renderBoard({ kind: "global" }, "/executions?includeSystem=true");
        await userEvent.click(screen.getByRole("checkbox", { name: "Show system runs" }));
        expect(new URLSearchParams(lastSearch).has(INCLUDE_SYSTEM_PARAM)).toBe(false);
    });

    it("badges system rows only", () => {
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue(
            page([row("s1", true), row("u1", false), row("l1")], { includeSystem: true })
        );
        renderBoard({ kind: "global" }, "/executions?includeSystem=true");
        expect(screen.getAllByText("System")).toHaveLength(1);
        expect(screen.getByText("System").closest("tr")).toHaveTextContent("wf-s1");
    });

    it("the global workflow filter sends the workflow's own database with All workflow databases selected", async () => {
        // The server shows a system workflow's runs by default only for a list scoped to ONE
        // workflow, which takes both halves of its key. With "All workflow databases" selected the
        // filter used to send the id alone, so a system workflow filtered to an empty list.
        const { useExecutions, useAllWorkflows } = require("../api/queries");
        useAllWorkflows.mockReturnValue({
            data: [
                { workflowId: "sys-wf", workflowName: "System WF", databaseId: "GLOBAL" },
                { workflowId: "user-wf", workflowName: "User WF", databaseId: "db-1" },
            ],
        });
        useExecutions.mockReturnValue(page([], { includeSystem: true }));
        renderBoard({ kind: "global" });
        expect(screen.getByLabelText("Filter by workflow database")).toHaveValue("");
        await userEvent.selectOptions(
            screen.getByLabelText("Filter by workflow"),
            screen.getByRole("option", { name: "System WF" })
        );
        expect(lastFilters().workflowId).toBe("sys-wf");
        expect(lastFilters().workflowDatabaseId).toBe("GLOBAL");
        // The board still leaves the default to the server.
        expect(lastFilters()[INCLUDE_SYSTEM_PARAM]).toBeUndefined();
    });

    it("an empty asset page with more to load says the search continues, not that the history is empty", () => {
        // The asset list drops hidden system runs from its candidate window, so a system-dense asset
        // can return an empty first page that still carries a NextToken.
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue({
            ...page([], { includeSystem: false, NextToken: "more" }),
            hasNextPage: true,
        });
        renderBoard({ kind: "asset", databaseId: "db", assetId: "a1" });
        expect(screen.queryByText("No executions found.")).not.toBeInTheDocument();
        expect(screen.getByText(/load more to keep looking/)).toBeInTheDocument();
        expect(screen.getByText(/System workflow runs are hidden/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Load more/ })).toBeInTheDocument();
    });

    it("Clear returns to the server default", async () => {
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue(page([], { includeSystem: false }));
        renderBoard({ kind: "global" }, "/executions?includeSystem=false");
        await userEvent.click(screen.getByRole("button", { name: "Clear" }));
        expect(new URLSearchParams(lastSearch).has(INCLUDE_SYSTEM_PARAM)).toBe(false);
    });
});
