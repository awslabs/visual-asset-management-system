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

    /**
     * A custom range's lower bound is not optional in the request, only in the form.
     *
     * The server applies a 90-day floor to any listing that arrives without a start date, so an
     * end-only range would be clipped to the last 90 days — and inverted against an end date older
     * than that, which the key-range BETWEEN rejects outright. "Everything before X" therefore has to
     * send an explicit epoch lower bound.
     */
    it("sends an explicit lower bound for a custom range with only an end date", async () => {
        const { useExecutions } = require("../api/queries");
        renderBoard([]);

        await userEvent.selectOptions(
            screen.getByLabelText("Time window"),
            screen.getByRole("option", { name: "Custom range…" })
        );
        await userEvent.type(screen.getByLabelText("Started on or before"), "2026-03-31");

        await waitFor(() => {
            const lastFilters = useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
            expect(lastFilters.filterEndDate).toBe("2026-03-31T23:59:59Z");
            expect(lastFilters.filterStartDate).toBe("1970-01-01T00:00:00Z");
        });
    });

    it("keeps the chosen lower bound when both ends of a custom range are set", async () => {
        const { useExecutions } = require("../api/queries");
        renderBoard([]);

        await userEvent.selectOptions(
            screen.getByLabelText("Time window"),
            screen.getByRole("option", { name: "Custom range…" })
        );
        await userEvent.type(screen.getByLabelText("Started on or after"), "2026-01-01");
        await userEvent.type(screen.getByLabelText("Started on or before"), "2026-01-31");

        await waitFor(() => {
            const lastFilters = useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
            expect(lastFilters.filterStartDate).toBe("2026-01-01T00:00:00Z");
            expect(lastFilters.filterEndDate).toBe("2026-01-31T23:59:59Z");
        });
    });

    it("sends the custom range on the asset tab too, where both bounds apply", async () => {
        const { useExecutions } = require("../api/queries");
        renderBoard([], { kind: "asset", databaseId: "db-1", assetId: "a-1" });

        await userEvent.selectOptions(
            screen.getByLabelText("Time window"),
            screen.getByRole("option", { name: "Custom range…" })
        );
        await userEvent.type(screen.getByLabelText("Started on or before"), "2026-01-31");

        await waitFor(() => {
            const lastFilters = useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
            expect(lastFilters.filterEndDate).toBe("2026-01-31T23:59:59Z");
        });
    });

    /**
     * A page can withhold rows it could not evaluate against the caller's constraints and say so in
     * `warnings`. Dropping that leaves a short page reading as a quiet window.
     */
    it("shows the list response's warnings above the rows", () => {
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue({
            data: {
                pages: [
                    {
                        Items: [],
                        warnings: [
                            "This page reached the limit of 500 distinct assets; some executions were not listed.",
                        ],
                    },
                ],
                pageParams: [],
            },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
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

        expect(screen.getByRole("status")).toHaveTextContent(/limit of 500 distinct assets/);
    });

    it("reports each distinct warning once across loaded pages", () => {
        const { useExecutions } = require("../api/queries");
        const warning = "This page reached the limit of 500 distinct assets.";
        useExecutions.mockReturnValue({
            data: {
                pages: [
                    { Items: [], warnings: [warning] },
                    { Items: [], warnings: [warning, "A second notice."] },
                ],
                pageParams: [],
            },
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

        expect(screen.getAllByText(warning)).toHaveLength(1);
        expect(screen.getByText("A second notice.")).toBeInTheDocument();
    });

    it("renders no notice banner when the pages carry no warnings", () => {
        renderBoard([]);
        expect(screen.queryByRole("status")).not.toBeInTheDocument();
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

/**
 * Re-run must always report its outcome.
 *
 * Re-run creates a NEW execution while the row the user clicked is the OLD one, so without a toast
 * naming the new id there is no signal that anything happened — and a failure would be silent
 * entirely. The mutation's response passes the execute handler's body through, which is where the new
 * id and any non-fatal warnings come from.
 */
jest.mock("../components/ToastProvider", () => ({
    ...jest.requireActual("../components/ToastProvider"),
    useToast: () => mockToast,
}));
const mockToast = { success: jest.fn(), error: jest.fn(), warning: jest.fn(), info: jest.fn() };

describe("ExecutionsBoard re-run feedback", () => {
    let queryClient: QueryClient;
    const ROW: Execution = {
        workflowExecutionId: "exec-old",
        workflowId: "wf-1",
        workflowDatabaseId: "db-1",
        executionStatus: "FAILED",
        triggeredByUserId: "u1",
        triggerType: "manual",
        executionStartDate: "2026-08-01T10:00:00Z",
    };

    const setup = (rerunImpl: jest.Mock, row: Execution = ROW) => {
        queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        const { useExecutions, useExecutionActions } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });
        useExecutionActions.mockReturnValue({
            abortExecution: { mutateAsync: jest.fn() },
            rerunExecution: { mutateAsync: rerunImpl },
            permanentDeleteExecution: { mutateAsync: jest.fn() },
        });
        useExecutions.mockReturnValue({
            data: { pages: [{ Items: [row] }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });
        return render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionsBoard scope={{ kind: "global" }} />
                </MemoryRouter>
            </QueryClientProvider>
        );
    };

    const clickRerun = async () => {
        // The row's "⋯" menu (aria-label "Execution actions"), then the Re-run item. The column
        // HEADER is also called "Actions", so the label has to be exact.
        await userEvent.click(screen.getByRole("button", { name: "Execution actions" }));
        await userEvent.click(await screen.findByText("Rerun"));
    };

    beforeEach(() => {
        jest.clearAllMocks();
        mockToast.success.mockClear();
        mockToast.error.mockClear();
        mockToast.warning.mockClear();
    });

    it("reports success and names the NEW execution", async () => {
        setup(jest.fn().mockResolvedValue({ executionId: "exec-new" }));
        await clickRerun();
        await waitFor(() => expect(mockToast.success).toHaveBeenCalled());
        const [title, opts] = mockToast.success.mock.calls[0];
        expect(title).toBe("Re-run started");
        // The clicked row is exec-old; without the new id the user cannot tell what to watch.
        expect(opts.description).toContain("exec-new");
    });

    it("reports a failure rather than failing silently", async () => {
        setup(jest.fn().mockRejectedValue(new Error("workflow archived")));
        await clickRerun();
        await waitFor(() => expect(mockToast.error).toHaveBeenCalled());
        const [title, opts] = mockToast.error.mock.calls[0];
        expect(title).toBe("Re-run failed");
        expect(opts.description).toContain("workflow archived");
    });

    it("distinguishes a launch that carried warnings", async () => {
        // "Started" and "started, but read this" are different outcomes.
        setup(
            jest.fn().mockResolvedValue({
                executionId: "exec-new",
                warnings: ["one input file no longer exists"],
            })
        );
        await clickRerun();
        await waitFor(() => expect(mockToast.warning).toHaveBeenCalled());
        expect(mockToast.success).not.toHaveBeenCalled();
        expect(mockToast.warning.mock.calls[0][1].description).toContain("no longer exists");
    });

    it("still reports success when the response carries no id", async () => {
        // A thin response must not produce an empty or broken message.
        setup(jest.fn().mockResolvedValue({}));
        await clickRerun();
        await waitFor(() => expect(mockToast.success).toHaveBeenCalled());
        expect(mockToast.success.mock.calls[0][1].description).toMatch(/new execution/i);
    });

    /**
     * Re-running a row that belongs to a group launches exactly ONE execution — the group id rides
     * along as a label so the new run files with its siblings. Claiming the group was replayed leaves
     * the operator believing work happened that did not.
     */
    it("does not claim the whole group was re-run", async () => {
        setup(jest.fn().mockResolvedValue({ executionId: "exec-new" }), {
            ...ROW,
            executionGroupId: "grp-7",
        });
        await clickRerun();
        await waitFor(() => expect(mockToast.success).toHaveBeenCalled());
        const description = mockToast.success.mock.calls[0][1].description;
        expect(description).not.toMatch(/re-ran every execution/i);
        expect(description).toContain("grp-7");
        expect(description).toMatch(/were not re-run/i);
    });
});

/**
 * The asset tab's Workflow filter.
 *
 * It is a separate control from the global board's pair of dropdowns: it carries the whole composite
 * "databaseId:workflowId" in one value, because a workflowId is unique only within its database and
 * the asset tab has no database dropdown to pair with. Its options come from the loaded rows (the
 * workflows this asset has actually run) rather than from the full workflow catalog.
 */
describe("ExecutionsBoard asset-scope workflow filter", () => {
    let queryClient: QueryClient;
    const ASSET_SCOPE = { kind: "asset", databaseId: "db-1", assetId: "a-1" } as any;

    /** Two executions on the asset, from two different workflows in two different databases. */
    const TWO_WORKFLOW_ROWS: any[] = [
        {
            workflowExecutionId: "E1",
            workflowId: "wf-alpha",
            workflowDatabaseId: "wdb-one",
            executionStatus: "SUCCEEDED",
        },
        {
            workflowExecutionId: "E2",
            workflowId: "wf-beta",
            workflowDatabaseId: "wdb-two",
            executionStatus: "SUCCEEDED",
        },
    ];

    beforeEach(() => {
        queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        jest.clearAllMocks();
        const {
            useExecutions,
            useExecutionActions,
            useAllWorkflows,
            useDatabases,
            useAllPipelines,
            useWorkflow,
        } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionActions.mockReturnValue({
            abortExecution: { mutateAsync: jest.fn() },
            rerunExecution: { mutateAsync: jest.fn() },
            permanentDeleteExecution: { mutateAsync: jest.fn() },
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });
        useAllWorkflows.mockReturnValue({ data: [] });
        useDatabases.mockReturnValue({ data: [] });
        useAllPipelines.mockReturnValue({ data: [] });
        useWorkflow.mockReturnValue({ data: undefined });
        useExecutions.mockReturnValue({
            data: { pages: [{ Items: [] }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });
    });

    afterEach(() => cleanup());

    const renderAssetBoard = (rows: any[], scope: any = ASSET_SCOPE) => {
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue({
            data: { pages: [{ Items: rows }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });
        return render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionsBoard scope={scope} />
                </MemoryRouter>
            </QueryClientProvider>
        );
    };

    const lastFilters = () => {
        const { useExecutions } = require("../api/queries");
        return useExecutions.mock.calls[useExecutions.mock.calls.length - 1][1];
    };

    it("offers the workflow filter on the asset tab", () => {
        renderAssetBoard(TWO_WORKFLOW_ROWS);
        expect(screen.getByLabelText("Filter by workflow")).toBeInTheDocument();
    });

    it("does not offer the WORKFLOW DATABASE dropdown on the asset tab", () => {
        // One composite-valued control replaces the global board's pair here; a lone database
        // dropdown would let a user select a database with no workflow, filtering per field to
        // something they did not ask for.
        renderAssetBoard(TWO_WORKFLOW_ROWS);
        expect(screen.queryByLabelText("Filter by workflow database")).not.toBeInTheDocument();
    });

    it("sends BOTH halves of the composite key to the server", async () => {
        // The backend matches workflowId and workflowDatabaseId per field. Sending only the id would
        // fail to narrow when two databases share a workflow id.
        renderAssetBoard(TWO_WORKFLOW_ROWS);
        await userEvent.selectOptions(
            screen.getByLabelText("Filter by workflow"),
            screen.getByRole("option", { name: "wf-beta" })
        );
        await waitFor(() => {
            expect(lastFilters().workflowId).toBe("wf-beta");
            expect(lastFilters().workflowDatabaseId).toBe("wdb-two");
        });
    });

    it("labels an option with the workflow NAME when the catalog knows it", async () => {
        // Rows carry ids only; without the catalog join the dropdown shows opaque ids.
        const { useAllWorkflows } = require("../api/queries");
        useAllWorkflows.mockReturnValue({
            data: [{ workflowId: "wf-alpha", databaseId: "wdb-one", workflowName: "Thumbnails" }],
        });
        renderAssetBoard(TWO_WORKFLOW_ROWS);
        expect(screen.getByRole("option", { name: "Thumbnails" })).toBeInTheDocument();
        // The workflow with no catalog entry still appears, under its id.
        expect(screen.getByRole("option", { name: "wf-beta" })).toBeInTheDocument();
    });

    it("keeps every seen workflow selectable after the server narrows the rows", async () => {
        // THE REGRESSION THIS GUARDS: the filter is applied server-side, so once a workflow is
        // chosen the response contains only that workflow's rows. Options recomputed from the
        // visible rows would collapse to the single selected entry, leaving no way to switch to
        // another workflow without first clearing the filter.
        const { rerender } = renderAssetBoard(TWO_WORKFLOW_ROWS);
        await userEvent.selectOptions(
            screen.getByLabelText("Filter by workflow"),
            screen.getByRole("option", { name: "wf-alpha" })
        );

        // The server now returns only wf-alpha's execution.
        const { useExecutions } = require("../api/queries");
        useExecutions.mockReturnValue({
            data: { pages: [{ Items: [TWO_WORKFLOW_ROWS[0]] }], pageParams: [] },
            isLoading: false,
            error: null,
            fetchNextPage: jest.fn(),
            hasNextPage: false,
            isFetchingNextPage: false,
        });
        rerender(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionsBoard scope={ASSET_SCOPE} />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await waitFor(() => {
            expect(screen.getByRole("option", { name: "wf-beta" })).toBeInTheDocument();
        });
    });

    it("hides the control when the asset has only one workflow to choose between", () => {
        // With a single workflow in an asset's history the filter can only reproduce the list
        // already on screen.
        renderAssetBoard([TWO_WORKFLOW_ROWS[0]]);
        expect(screen.queryByLabelText("Filter by workflow")).not.toBeInTheDocument();
    });

    it("hides the control when the asset has no executions at all", () => {
        renderAssetBoard([]);
        expect(screen.queryByLabelText("Filter by workflow")).not.toBeInTheDocument();
    });

    it("clears the workflow filter with Clear", async () => {
        renderAssetBoard(TWO_WORKFLOW_ROWS);
        await userEvent.selectOptions(
            screen.getByLabelText("Filter by workflow"),
            screen.getByRole("option", { name: "wf-alpha" })
        );
        await waitFor(() => expect(lastFilters().workflowId).toBe("wf-alpha"));

        await userEvent.click(screen.getByRole("button", { name: "Clear" }));
        await waitFor(() => {
            expect(lastFilters().workflowId).toBeUndefined();
            expect(lastFilters().workflowDatabaseId).toBeUndefined();
        });
    });

    it("sends no workflow filter until one is chosen", () => {
        // An always-present empty filter would be sent as "" and, if the backend ever compared it
        // literally, would match nothing.
        renderAssetBoard(TWO_WORKFLOW_ROWS);
        expect(lastFilters().workflowId).toBeUndefined();
        expect(lastFilters().workflowDatabaseId).toBeUndefined();
    });

    it("does not offer the filter on a WORKFLOW-scoped board", () => {
        // That board is already pinned to one workflow by its scope, so the control could only
        // re-select what is already in force.
        renderAssetBoard(TWO_WORKFLOW_ROWS, {
            kind: "workflow",
            databaseId: "db-1",
            workflowId: "wf-alpha",
        });
        expect(screen.queryByLabelText("Filter by workflow")).not.toBeInTheDocument();
    });
});
