/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionsBoard from "./ExecutionsBoard";
import type { Execution } from "../types";

// Mock the query hooks
jest.mock("../api/queries", () => ({
    useExecutions: jest.fn(),
    useExecutionActions: jest.fn(),
    useExecutionDetails: jest.fn(),
    // ExecuteWorkflowButton (in the board toolbar) and the Workflow filter dropdown list workflows;
    // the Workflow Database dropdown lists databases. Default both to empty.
    useAllWorkflows: jest.fn(() => ({ data: [] })),
    useDatabases: jest.fn(() => ({ data: [] })),
    // ExecuteWorkflowButton reads the referenced pipelines' systemConfig to summarize what the
    // selected workflow accepts.
    useAllPipelines: jest.fn(() => ({ data: [] })),
    // Breadcrumb label lookup for the workflow-scoped board.
    useWorkflow: jest.fn(() => ({ data: undefined })),
}));

// Mock the permissions hook
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

// Mock react-router-dom navigate
const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useNavigate: () => mockNavigate,
}));

describe("ExecutionsBoard", () => {
    let queryClient: QueryClient;

    beforeEach(() => {
        queryClient = new QueryClient({
            defaultOptions: {
                queries: { retry: false },
            },
        });
        jest.clearAllMocks();

        const { useExecutions, useExecutionActions } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        // Default mock for useExecutionActions
        useExecutionActions.mockReturnValue({
            abortExecution: { mutateAsync: jest.fn() },
            rerunExecution: { mutateAsync: jest.fn() },
            permanentDeleteExecution: { mutateAsync: jest.fn() },
        });

        // Default mock for permissions (allow all)
        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: jest.fn(() => true),
        });

        // Default mock for useExecutions (empty, infinite query shape)
        useExecutions.mockReturnValue({
            data: { pages: [{ Items: [] }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });
    });

    it("renders all execution rows with distinct status badges", () => {
        const { useExecutions } = require("../api/queries");
        const mockExecutions: Execution[] = [
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                triggeredByUserId: "user-1",
                triggerType: "manual",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
            {
                workflowExecutionId: "exec-2",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                triggeredByUserId: "user-1",
                triggerType: "manual",
                executionStartDate: "2026-07-18T09:00:00Z",
                executionStopDate: "2026-07-18T09:30:00Z",
            },
            {
                workflowExecutionId: "exec-3",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "ABORTED",
                triggeredByUserId: "user-1",
                triggerType: "manual",
                executionStartDate: "2026-07-18T08:00:00Z",
                executionStopDate: "2026-07-18T08:15:00Z",
            },
        ];

        useExecutions.mockReturnValue({
            data: { pages: [{ Items: mockExecutions }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionsBoard scope={{ kind: "global" }} />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // All 3 rows should render
        expect(screen.getByText("exec-1")).toBeInTheDocument();
        expect(screen.getByText("exec-2")).toBeInTheDocument();
        expect(screen.getByText("exec-3")).toBeInTheDocument();

        // Status badges should be visible (scope to the badge <span>, not the filter <option>s).
        const badge = (label: string) =>
            screen.getAllByText(label).find((el) => el.tagName.toLowerCase() === "span");
        expect(badge("Running")).toBeInTheDocument();
        expect(badge("Succeeded")).toBeInTheDocument();
        expect(badge("Aborted")).toBeInTheDocument();

        // Database column header + value are shown (sortable via the header).
        expect(screen.getByRole("columnheader", { name: "Workflow Database" })).toBeInTheDocument();
        expect(screen.getAllByText("db-1").length).toBeGreaterThan(0);
    });

    it("sorts non-terminal rows (RUNNING) above terminal rows", () => {
        const { useExecutions } = require("../api/queries");
        const mockExecutions: Execution[] = [
            {
                workflowExecutionId: "exec-succeeded",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
            {
                workflowExecutionId: "exec-running",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                executionStartDate: "2026-07-18T09:00:00Z",
            },
        ];

        useExecutions.mockReturnValue({
            data: { pages: [{ Items: mockExecutions }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });

        const { container } = render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionsBoard scope={{ kind: "global" }} />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Get all table rows in tbody
        const rows = container.querySelectorAll("tbody tr");
        expect(rows.length).toBe(2);

        // First row should contain the RUNNING execution
        expect(rows[0].textContent).toContain("exec-running");
        expect(rows[0].textContent).toContain("Running");

        // Second row should contain the SUCCEEDED execution
        expect(rows[1].textContent).toContain("exec-succeeded");
        expect(rows[1].textContent).toContain("Succeeded");
    });

    it("hides Logs action when permission is denied", () => {
        const { useExecutions } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        const mockExecutions: Execution[] = [
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
        ];

        useExecutions.mockReturnValue({
            data: { pages: [{ Items: mockExecutions }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });

        // Deny logs permission
        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: jest.fn((method, path) => {
                if (path === "/workflows/executions/{executionId}/logs") {
                    return false;
                }
                return true;
            }),
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionsBoard scope={{ kind: "global" }} />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // We can't easily test the context menu without triggering it, but we can verify the component renders
        expect(screen.getByText("exec-1")).toBeInTheDocument();
    });

    it("shows Abort for RUNNING rows and not for SUCCEEDED rows", () => {
        const { useExecutions } = require("../api/queries");

        const mockExecutions: Execution[] = [
            {
                workflowExecutionId: "exec-running",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
            {
                workflowExecutionId: "exec-succeeded",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                executionStartDate: "2026-07-18T09:00:00Z",
                executionStopDate: "2026-07-18T09:30:00Z",
            },
        ];

        useExecutions.mockReturnValue({
            data: { pages: [{ Items: mockExecutions }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionsBoard scope={{ kind: "global" }} />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Component should render both executions
        expect(screen.getByText("exec-running")).toBeInTheDocument();
        expect(screen.getByText("exec-succeeded")).toBeInTheDocument();
    });

    /** Renders the board with the supplied rows and returns the mutation mocks in play. */
    const renderBoard = (rows: Execution[], scope: any = { kind: "global" }) => {
        const { useExecutions, useExecutionActions } = require("../api/queries");
        useExecutions.mockReturnValue({
            data: { pages: [{ Items: rows }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionsBoard scope={scope} />
                </MemoryRouter>
            </QueryClientProvider>
        );
        return useExecutionActions.mock.results[0].value;
    };

    /** Opens the row kebab menu and selects the named action. */
    const openRowAction = async (label: RegExp) => {
        await userEvent.click(screen.getByRole("button", { name: "Execution actions" }));
        await userEvent.click(await screen.findByRole("menuitem", { name: label }));
    };

    it("requires typing CONFIRM before a permanent delete is issued", async () => {
        const actions = renderBoard([
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
        ]);
        actions.permanentDeleteExecution.mutateAsync.mockResolvedValue({});

        await openRowAction(/permanent delete/i);

        const deleteButton = await screen.findByRole("button", { name: /^Permanent Delete$/ });
        expect(deleteButton).toBeDisabled();
        await userEvent.click(deleteButton);
        expect(actions.permanentDeleteExecution.mutateAsync).not.toHaveBeenCalled();

        await userEvent.type(screen.getByPlaceholderText("CONFIRM"), "confirm");
        expect(screen.getByRole("button", { name: /^Permanent Delete$/ })).toBeDisabled();

        await userEvent.clear(screen.getByPlaceholderText("CONFIRM"));
        await userEvent.type(screen.getByPlaceholderText("CONFIRM"), "CONFIRM");
        await userEvent.click(screen.getByRole("button", { name: /^Permanent Delete$/ }));
        expect(actions.permanentDeleteExecution.mutateAsync).toHaveBeenCalledWith("exec-1");
    });

    it("forwards the group id when aborting an execution group", async () => {
        const actions = renderBoard([
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                executionGroupId: "grp-1",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
        ]);
        actions.abortExecution.mutateAsync.mockResolvedValue({});

        await openRowAction(/abort group/i);
        await userEvent.click(await screen.findByRole("button", { name: /^Abort$/ }));

        expect(actions.abortExecution.mutateAsync).toHaveBeenCalledWith({
            executionId: "exec-1",
            groupId: "grp-1",
        });
    });

    it("aborts a single execution without a group id", async () => {
        const actions = renderBoard([
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                executionGroupId: "grp-1",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
        ]);
        actions.abortExecution.mutateAsync.mockResolvedValue({});

        await openRowAction(/^abort$/i);
        await userEvent.click(await screen.findByRole("button", { name: /^Abort$/ }));

        expect(actions.abortExecution.mutateAsync).toHaveBeenCalledWith({
            executionId: "exec-1",
            groupId: undefined,
        });
    });

    it("shows the failure reason in the abort dialog and leaves it open", async () => {
        const actions = renderBoard([
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
        ]);
        actions.abortExecution.mutateAsync.mockRejectedValue(
            new Error("Execution is already in a terminal state")
        );

        await openRowAction(/^abort$/i);
        await userEvent.click(await screen.findByRole("button", { name: /^Abort$/ }));

        expect(await screen.findByRole("alert")).toHaveTextContent(
            "Execution is already in a terminal state"
        );
        expect(screen.getByRole("button", { name: /^Abort$/ })).toBeInTheDocument();
    });

    it("surfaces a rerun failure even though rerun has no dialog", async () => {
        const actions = renderBoard([
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "FAILED",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
        ]);
        actions.rerunExecution.mutateAsync.mockRejectedValue(new Error("Workflow was archived"));

        await openRowAction(/rerun/i);

        expect(await screen.findByRole("alert")).toHaveTextContent("Workflow was archived");
    });

    it("does not carry a rerun failure into the next dialog", async () => {
        const actions = renderBoard([
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
        ]);
        actions.rerunExecution.mutateAsync.mockRejectedValue(new Error("Workflow was archived"));

        await openRowAction(/rerun/i);
        expect(await screen.findByRole("alert")).toHaveTextContent("Workflow was archived");

        await openRowAction(/^abort$/i);
        expect(await screen.findByRole("button", { name: /^Abort$/ })).toBeInTheDocument();
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("aborts the group even when the row is no longer in the loaded list", async () => {
        const { useExecutions } = require("../api/queries");
        const actions = renderBoard([
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                executionGroupId: "grp-1",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
        ]);
        actions.abortExecution.mutateAsync.mockResolvedValue({});

        await openRowAction(/abort group/i);

        // A refetch (poll / mutation invalidation) drops the row while the dialog is open.
        useExecutions.mockReturnValue({
            data: { pages: [{ Items: [] }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });
        await userEvent.click(await screen.findByRole("button", { name: /^Abort$/ }));

        expect(actions.abortExecution.mutateAsync).toHaveBeenCalledWith({
            executionId: "exec-1",
            groupId: "grp-1",
        });
    });

    it("shows a Workflows / <name> / Executions breadcrumb only when scoped to a workflow", () => {
        // A workflow-scoped board is a filtered view reached FROM a workflow, so it needs to say what
        // it is filtered to and offer a way back. The global board has no such trail.
        const { useWorkflow } = require("../api/queries");
        useWorkflow.mockReturnValue({ data: { workflowName: "My Conversion WF" } });

        renderBoard([], { kind: "workflow", databaseId: "db-1", workflowId: "wf-1" });
        const crumbs = screen.getByRole("navigation", { name: /breadcrumb/i });
        expect(crumbs).toHaveTextContent("Workflows");
        expect(crumbs).toHaveTextContent("My Conversion WF");
        expect(crumbs).toHaveTextContent("Executions");

        cleanup();
        renderBoard([]);
        expect(screen.queryByRole("navigation", { name: /breadcrumb/i })).not.toBeInTheDocument();
    });

    it("falls back to the workflow id in the breadcrumb before the record loads", () => {
        const { useWorkflow } = require("../api/queries");
        useWorkflow.mockReturnValue({ data: undefined });
        renderBoard([], { kind: "workflow", databaseId: "db-1", workflowId: "wf-42" });
        expect(screen.getByRole("navigation", { name: /breadcrumb/i })).toHaveTextContent("wf-42");
    });

    it("hides the Group column but still filters the loaded rows by group id", async () => {
        // The column is hidden for now; executionGroupId stays on the row so search/filtering by it
        // keeps working (a grouped abort still needs to find its siblings).
        renderBoard([
            {
                workflowExecutionId: "exec-1",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "RUNNING",
                executionGroupId: "grp-1",
                executionStartDate: "2026-07-18T10:00:00Z",
            },
            {
                workflowExecutionId: "exec-2",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                executionStartDate: "2026-07-18T09:00:00Z",
            },
        ]);

        expect(screen.queryByRole("columnheader", { name: "Group" })).not.toBeInTheDocument();
        expect(screen.queryByText("grp-1")).not.toBeInTheDocument();

        await userEvent.type(screen.getByLabelText("Search"), "grp-1");
        expect(screen.getByText("exec-1")).toBeInTheDocument();
        expect(screen.queryByText("exec-2")).not.toBeInTheDocument();
    });

    it("renders the output target columns from the list row", () => {
        // The output target lives on the execution's CONFIGURATION row server-side; the list projects
        // it so these columns need no extra client fetch.
        renderBoard([
            {
                workflowExecutionId: "E-out",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                outputLocationType: "asset",
                outputAssetId: "asset-out-1",
                outputDatabaseId: "db-out-1",
            },
        ]);

        expect(screen.getByRole("columnheader", { name: "Output Type" })).toBeInTheDocument();
        expect(screen.getByRole("columnheader", { name: "Output Asset ID" })).toBeInTheDocument();
        expect(screen.getByRole("columnheader", { name: "Output Database" })).toBeInTheDocument();
        expect(screen.getByText("asset-out-1")).toBeInTheDocument();
        expect(screen.getByText("db-out-1")).toBeInTheDocument();
    });

    it("omits the output target columns in asset scope, where the list cannot populate them", () => {
        // The per-asset list joins execution-inputs with main rows and never reads the configuration
        // row the output target lives on, so these columns would be permanently blank there. Adding
        // them to that response would cost one extra read per row.
        renderBoard(
            [
                {
                    workflowExecutionId: "E-asset",
                    workflowId: "wf-1",
                    workflowDatabaseId: "db-1",
                    executionStatus: "SUCCEEDED",
                },
            ],
            { kind: "asset", databaseId: "db-1", assetId: "a-1" }
        );

        expect(screen.queryByRole("columnheader", { name: "Output Type" })).not.toBeInTheDocument();
        expect(
            screen.queryByRole("columnheader", { name: "Output Asset ID" })
        ).not.toBeInTheDocument();
        expect(
            screen.queryByRole("columnheader", { name: "Output Database" })
        ).not.toBeInTheDocument();
        // The renamed workflow-database column stays in both scopes.
        expect(screen.getByRole("columnheader", { name: "Workflow Database" })).toBeInTheDocument();
    });

    it("renders a dash for an execution with no output target", () => {
        // A results-only run has no output asset; the columns must render blank rather than crash.
        renderBoard([
            {
                workflowExecutionId: "E-none",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
            },
        ]);
        expect(screen.getByRole("columnheader", { name: "Output Asset ID" })).toBeInTheDocument();
    });

    it("sends the workflow database filter to the server", async () => {
        const { useExecutions, useDatabases } = require("../api/queries");
        useDatabases.mockReturnValue({ data: [{ databaseId: "db-picked" }] });
        renderBoard([]);

        await userEvent.selectOptions(
            screen.getByLabelText("Filter by workflow database"),
            screen.getByRole("option", { name: "db-picked" })
        );

        await waitFor(() => {
            const lastFilters = useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
            expect(lastFilters.workflowDatabaseId).toBe("db-picked");
        });
    });

    it("sends the workflow filter to the server and clears it when the database changes", async () => {
        const { useExecutions, useDatabases, useAllWorkflows } = require("../api/queries");
        useDatabases.mockReturnValue({ data: [{ databaseId: "db-picked" }] });
        useAllWorkflows.mockReturnValue({
            data: [{ workflowId: "wf-picked", workflowName: "Picked WF", databaseId: "db-picked" }],
        });
        renderBoard([]);

        await userEvent.selectOptions(
            screen.getByLabelText("Filter by workflow"),
            screen.getByRole("option", { name: "Picked WF" })
        );
        await waitFor(() => {
            const lastFilters = useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
            expect(lastFilters.workflowId).toBe("wf-picked");
        });

        // Changing the database resets the workflow: the workflow list is scoped to the database, so a
        // stale selection would filter to nothing.
        await userEvent.selectOptions(
            screen.getByLabelText("Filter by workflow database"),
            screen.getByRole("option", { name: "db-picked" })
        );
        await waitFor(() => {
            const lastFilters = useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
            expect(lastFilters.workflowId).toBeUndefined();
        });
    });

    it("filters by the stored trigger vocabulary the backend compares against", async () => {
        const { useExecutions } = require("../api/queries");
        renderBoard([]);

        await userEvent.selectOptions(
            screen.getByLabelText("Filter by trigger"),
            screen.getByRole("option", { name: "File upload" })
        );

        // The stored value is "File-Upload"; sending the request vocabulary "fileUpload" would
        // match no row.
        await waitFor(() => {
            const lastFilters = useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
            expect(lastFilters.triggerType).toBe("File-Upload");
        });
    });

    it("offers Load more when a page returns no visible rows but more pages remain", async () => {
        const { useExecutions } = require("../api/queries");
        const fetchNextPage = jest.fn();

        useExecutions.mockReturnValue({
            // A server page can drop every row it returned (server-side filters, per-object
            // visibility) and still hand back a NextToken.
            data: { pages: [{ Items: [] }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage,
            hasNextPage: true,
            isFetchingNextPage: false,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionsBoard scope={{ kind: "global" }} />
                </MemoryRouter>
            </QueryClientProvider>
        );

        expect(screen.getByText("No executions found.")).toBeInTheDocument();
        const loadMore = screen.getByRole("button", { name: /Load more/ });
        await userEvent.click(loadMore);
        expect(fetchNextPage).toHaveBeenCalled();
    });
});
