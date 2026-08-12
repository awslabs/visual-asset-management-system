/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import WorkflowsPage from "./WorkflowsPage";

// Mock the queries module
jest.mock("../api/queries", () => ({
    useWorkflows: jest.fn(),
    useWorkflowMutations: jest.fn(),
    // DatabasePickerDialog (create-in-database picker) calls useDatabases; default to an empty,
    // idle result so the page renders without opening the picker.
    useDatabases: jest.fn(() => ({ data: [], isLoading: false, error: null })),
}));

// Mock useAllowedRoutes
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

// Mock useNavigate
const mockNavigate = jest.fn();
jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useNavigate: () => mockNavigate,
}));

// Mock ExecuteWizard
jest.mock("../wizard/ExecuteWizard", () => ({
    __esModule: true,
    default: () => null,
}));

// useWorkflows is now a useInfiniteQuery — the component reads data.pages[].Items plus the
// fetchNextPage/hasNextPage fields. Build that shape from a flat array of workflows.
const infinite = (items: any[]) => ({
    data: { pages: [{ Items: items }], pageParams: [undefined] },
    isLoading: false,
    error: null,
    fetchNextPage: jest.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
});

describe("WorkflowsPage", () => {
    const mockWorkflows = [
        {
            workflowId: "wf-1",
            workflowName: "Workflow Alpha",
            databaseId: "db1",
            category: "Processing",
            enabled: true,
            archived: false,
            subDashboardUrl: "https://example.com/dashboard",
            specifiedPipelines: [{ pipelineId: "p1" }, { pipelineId: "p2" }],
            executionCount: 42,
        },
        {
            workflowId: "wf-2",
            workflowName: "Workflow Beta",
            databaseId: "db1",
            category: "Analysis",
            enabled: false,
            archived: false,
            specifiedPipelines: [{ pipelineId: "p3" }],
        },
        {
            workflowId: "wf-3",
            workflowName: "Workflow Gamma",
            databaseId: "db1",
            category: null,
            enabled: true,
            archived: false,
            specifiedPipelines: [],
        },
    ];

    let queryClient: QueryClient;

    beforeEach(() => {
        queryClient = new QueryClient({
            defaultOptions: { queries: { retry: false } },
        });
        jest.clearAllMocks();
    });

    it("renders workflows grouped by category", async () => {
        const { useWorkflows, useWorkflowMutations } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        useWorkflows.mockReturnValue(infinite(mockWorkflows));

        useWorkflowMutations.mockReturnValue({
            archiveWorkflow: { mutateAsync: jest.fn() },
        });

        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: () => true,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowsPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Check category headers
        expect(screen.getByText("Processing")).toBeInTheDocument();
        expect(screen.getByText("Analysis")).toBeInTheDocument();
        expect(screen.getByText("Uncategorized")).toBeInTheDocument();

        // Check workflow names
        expect(screen.getByText("Workflow Alpha")).toBeInTheDocument();
        expect(screen.getByText("Workflow Beta")).toBeInTheDocument();
        expect(screen.getByText("Workflow Gamma")).toBeInTheDocument();

        // Execution count from the list response is shown when present (Alpha has 42).
        expect(screen.getByText("Executions: 42")).toBeInTheDocument();
    });

    it("shows Dashboard link with target=_blank when subDashboardUrl is set", async () => {
        const { useWorkflows, useWorkflowMutations } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        useWorkflows.mockReturnValue(infinite(mockWorkflows));

        useWorkflowMutations.mockReturnValue({
            archiveWorkflow: { mutateAsync: jest.fn() },
        });

        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: () => true,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowsPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Workflow Alpha has subDashboardUrl, should show Dashboard link
        const dashboardLinks = screen.getAllByRole("link", { name: /dashboard/i });
        expect(dashboardLinks.length).toBeGreaterThan(0);

        const firstDashboardLink = dashboardLinks[0];
        expect(firstDashboardLink).toHaveAttribute("target", "_blank");
        expect(firstDashboardLink).toHaveAttribute("rel", "noopener noreferrer");
        expect(firstDashboardLink).toHaveAttribute("href", "https://example.com/dashboard");
    });

    it("hides Create button when POST permission is denied", async () => {
        const { useWorkflows, useWorkflowMutations } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        useWorkflows.mockReturnValue(infinite(mockWorkflows));

        useWorkflowMutations.mockReturnValue({
            archiveWorkflow: { mutateAsync: jest.fn() },
        });

        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: () => false,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowsPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Create button should not be present
        expect(screen.queryByRole("button", { name: /create/i })).not.toBeInTheDocument();
    });

    it("navigates to executions page with aligned params on 'View Executions'", async () => {
        const { useWorkflows, useWorkflowMutations } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        useWorkflows.mockReturnValue(infinite(mockWorkflows));

        useWorkflowMutations.mockReturnValue({
            archiveWorkflow: { mutateAsync: jest.fn() },
        });

        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: () => true,
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowsPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Open the first workflow card's actions menu via its kebab (⋮) button.
        const actionsButton = screen.getByRole("button", { name: "Actions for Workflow Alpha" });
        await userEvent.click(actionsButton);

        // Click "View Executions" in the actions menu
        await waitFor(() => {
            const viewExecutionsButton = screen.getByText("View Executions");
            expect(viewExecutionsButton).toBeInTheDocument();
        });

        const viewExecutionsButton = screen.getByText("View Executions");
        await userEvent.click(viewExecutionsButton);

        // Check navigate was called with the correct params
        await waitFor(() => {
            expect(mockNavigate).toHaveBeenCalledWith(
                "/executions?workflowId=wf-1&workflowDatabaseId=db1"
            );
        });
    });

    it("hides 'View Executions' when the execution-list route is not allowed", async () => {
        const { useWorkflows, useWorkflowMutations } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        useWorkflows.mockReturnValue(infinite(mockWorkflows));
        useWorkflowMutations.mockReturnValue({ archiveWorkflow: { mutateAsync: jest.fn() } });
        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: (_method: string, path: string) => path !== "/workflows/executions",
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowsPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await userEvent.click(screen.getByRole("button", { name: "Actions for Workflow Alpha" }));

        await waitFor(() => {
            expect(screen.getByText("Edit")).toBeInTheDocument();
        });
        expect(screen.queryByText("View Executions")).not.toBeInTheDocument();
    });
});

/**
 * Trigger counts and the trigger facet.
 *
 * The list has to answer "will this workflow fire on its own?" at a glance. A raw count cannot: a
 * workflow with two triggers that are both switched off looks identical to one with two live
 * triggers, and that is exactly the state behind "why did nothing run?".
 */
describe("WorkflowsPage trigger counts", () => {
    let queryClient: QueryClient;

    const wf = (over: any) => ({
        workflowId: "wf-x",
        workflowName: "WF X",
        databaseId: "db1",
        category: "Processing",
        enabled: true,
        archived: false,
        specifiedPipelines: [],
        ...over,
    });

    const renderWith = (items: any[]) => {
        const { useWorkflows, useWorkflowMutations } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useWorkflows.mockReturnValue(infinite(items));
        useWorkflowMutations.mockReturnValue({ archiveWorkflow: { mutateAsync: jest.fn() } });
        useAllowedRoutes.mockReturnValue({ loading: false, can: () => true });
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <WorkflowsPage databaseId="db1" />
                </MemoryRouter>
            </QueryClientProvider>
        );
    };

    beforeEach(() => {
        queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        jest.clearAllMocks();
    });

    it("shows the trigger count", () => {
        renderWith([wf({ triggerCount: 3, triggersEnabledCount: 3 })]);
        expect(screen.getByText(/Triggers: 3/)).toBeInTheDocument();
        // Nothing is off, so no qualifier is added.
        expect(screen.queryByText(/on\)/)).not.toBeInTheDocument();
    });

    it("flags a partly-disabled trigger set", () => {
        renderWith([wf({ triggerCount: 3, triggersEnabledCount: 1 })]);
        expect(screen.getByText(/Triggers: 3/)).toBeInTheDocument();
        expect(screen.getByText(/\(1 on\)/)).toBeInTheDocument();
    });

    it("shows zero rather than omitting the label", () => {
        // Absent would be ambiguous with "the backend did not report counts".
        renderWith([wf({ triggerCount: 0, triggersEnabledCount: 0 })]);
        expect(screen.getByText(/Triggers: 0/)).toBeInTheDocument();
    });

    it("omits the row label entirely when the backend reported no counts", () => {
        // Scoped to the row's "Triggers: <n>" text — the facet select also renders the word
        // "Triggers" (as its aria-label and its "Triggers: All" option), which is always present.
        renderWith([wf({})]);
        expect(screen.queryByText(/Triggers: \d/)).not.toBeInTheDocument();
    });

    it("treats a response with only a total as fully enabled", () => {
        // Defensive: a count without the enabled split must not render as "all off".
        renderWith([wf({ triggerCount: 2 })]);
        expect(screen.getByText(/Triggers: 2/)).toBeInTheDocument();
        expect(screen.queryByText(/on\)/)).not.toBeInTheDocument();
    });

    it("offers a Triggers filter facet", () => {
        renderWith([wf({ triggerCount: 1, triggersEnabledCount: 1 })]);
        const facet = screen.getByLabelText("Triggers");
        const values = Array.from(facet.querySelectorAll("option")).map((o) =>
            o.getAttribute("value")
        );
        expect(values).toEqual(expect.arrayContaining(["", "enabled", "disabled", "none"]));
    });

    it("filters to workflows with an enabled trigger", async () => {
        renderWith([
            wf({
                workflowId: "wf-live",
                workflowName: "Live",
                triggerCount: 1,
                triggersEnabledCount: 1,
            }),
            wf({
                workflowId: "wf-off",
                workflowName: "AllOff",
                triggerCount: 2,
                triggersEnabledCount: 0,
            }),
            wf({
                workflowId: "wf-none",
                workflowName: "NoTriggers",
                triggerCount: 0,
                triggersEnabledCount: 0,
            }),
        ]);
        await userEvent.selectOptions(screen.getByLabelText("Triggers"), "enabled");
        expect(screen.getByText("Live")).toBeInTheDocument();
        expect(screen.queryByText("AllOff")).not.toBeInTheDocument();
        expect(screen.queryByText("NoTriggers")).not.toBeInTheDocument();
    });

    it("filters to workflows whose triggers are all off", async () => {
        renderWith([
            wf({
                workflowId: "wf-live",
                workflowName: "Live",
                triggerCount: 1,
                triggersEnabledCount: 1,
            }),
            wf({
                workflowId: "wf-off",
                workflowName: "AllOff",
                triggerCount: 2,
                triggersEnabledCount: 0,
            }),
            wf({
                workflowId: "wf-none",
                workflowName: "NoTriggers",
                triggerCount: 0,
                triggersEnabledCount: 0,
            }),
        ]);
        await userEvent.selectOptions(screen.getByLabelText("Triggers"), "disabled");
        // "All off" means triggers EXIST but none fire — a workflow with no triggers is a different
        // state and must not be swept in.
        expect(screen.getByText("AllOff")).toBeInTheDocument();
        expect(screen.queryByText("Live")).not.toBeInTheDocument();
        expect(screen.queryByText("NoTriggers")).not.toBeInTheDocument();
    });

    it("filters to workflows with no triggers", async () => {
        renderWith([
            wf({
                workflowId: "wf-live",
                workflowName: "Live",
                triggerCount: 1,
                triggersEnabledCount: 1,
            }),
            wf({
                workflowId: "wf-none",
                workflowName: "NoTriggers",
                triggerCount: 0,
                triggersEnabledCount: 0,
            }),
        ]);
        await userEvent.selectOptions(screen.getByLabelText("Triggers"), "none");
        expect(screen.getByText("NoTriggers")).toBeInTheDocument();
        expect(screen.queryByText("Live")).not.toBeInTheDocument();
    });

    it("keeps a workflow whose counts are unknown rather than hiding it", async () => {
        // The counts are best-effort server-side; a read failure must not make a workflow vanish
        // from a filtered list.
        renderWith([
            wf({ workflowId: "wf-unknown", workflowName: "Unknown" }),
            wf({
                workflowId: "wf-none",
                workflowName: "NoTriggers",
                triggerCount: 0,
                triggersEnabledCount: 0,
            }),
        ]);
        await userEvent.selectOptions(screen.getByLabelText("Triggers"), "enabled");
        expect(screen.getByText("Unknown")).toBeInTheDocument();
        expect(screen.queryByText("NoTriggers")).not.toBeInTheDocument();
    });
});
