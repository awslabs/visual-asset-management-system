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
});
