/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AssetExecutionsTab from "./AssetExecutionsTab";
import * as queries from "../../../features/orchestration/api/queries";
import * as permissions from "../../../features/orchestration/permissions/useAllowedRoutes";

// Mock orchestration query hooks
jest.mock("../../../features/orchestration/api/queries", () => ({
    useExecutions: jest.fn(),
    useAllWorkflows: jest.fn(),
    useExecutionActions: jest.fn(),
    // The board calls this for its Workflow Database filter. The filter itself is hidden in asset
    // scope (the scope pins the database), but the hook still runs.
    useDatabases: jest.fn(() => ({ data: [] })),
    useWorkflow: jest.fn(() => ({ data: undefined })),
    // The execute modal reads the referenced pipelines' systemConfig to summarize what the selected
    // workflow accepts.
    useAllPipelines: jest.fn(() => ({ data: [] })),
    useExecuteWorkflow: jest.fn(() => ({ mutateAsync: jest.fn() })),
    useAssetSearch: jest.fn(() => ({ data: { items: [], total: 0 }, isFetching: false })),
    useAssetFileSearch: jest.fn(() => ({ data: { items: [], total: 0 }, isFetching: false })),
    useFileVersions: jest.fn(() => ({ data: [] })),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

// Mock Monaco editor
jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => <div>Monaco Editor Mock</div>,
}));

describe("AssetExecutionsTab", () => {
    let queryClient: QueryClient;

    beforeEach(() => {
        queryClient = new QueryClient({
            defaultOptions: {
                queries: { retry: false },
            },
        });
        jest.clearAllMocks();

        // Default mocks
        (queries.useAllWorkflows as jest.Mock).mockReturnValue({
            data: [
                {
                    workflowId: "wf1",
                    workflowName: "Test Workflow",
                    databaseId: "db1",
                    specifiedPipelines: [],
                },
            ],
            isLoading: false,
        });

        (queries.useExecutions as jest.Mock).mockReturnValue({
            data: [
                {
                    workflowExecutionId: "exec1",
                    workflowId: "wf1",
                    executionStatus: "RUNNING",
                    executionStartDate: "2026-07-18T12:00:00Z",
                    triggerType: "manual",
                },
                {
                    workflowExecutionId: "exec2",
                    workflowId: "wf1",
                    executionStatus: "SUCCEEDED",
                    executionStartDate: "2026-07-18T11:00:00Z",
                    executionStopDate: "2026-07-18T11:05:00Z",
                    triggerType: "manual",
                },
            ],
            isLoading: false,
        });

        (queries.useExecutionActions as jest.Mock).mockReturnValue({
            abortExecution: { mutateAsync: jest.fn() },
            rerunExecution: { mutateAsync: jest.fn() },
            permanentDeleteExecution: { mutateAsync: jest.fn() },
        });

        (permissions.useAllowedRoutes as jest.Mock).mockReturnValue({
            can: () => true,
        });
    });

    it("renders the executions board with asset-scoped executions", async () => {
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <AssetExecutionsTab databaseId="db1" assetId="a1" isActive={true} />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await waitFor(() => {
            expect(queries.useExecutions).toHaveBeenCalledWith(
                { kind: "asset", databaseId: "db1", assetId: "a1" },
                {},
                {}
            );
        });

        // Check that the executions render
        await waitFor(() => {
            expect(screen.getByText("Executions")).toBeInTheDocument();
        });

        // Check that the Execute button is present
        expect(screen.getByText("Execute workflow")).toBeInTheDocument();
    });

    it("opens the workflow picker dialog from the execute button", async () => {
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <AssetExecutionsTab databaseId="db1" assetId="a1" isActive={true} />
                </MemoryRouter>
            </QueryClientProvider>
        );

        const executeButton = await screen.findByText("Execute workflow");

        // The workflow picker lives inside a dialog opened by the execute button.
        fireEvent.click(executeButton);

        await waitFor(() => {
            expect(screen.getByText("Execute a workflow")).toBeInTheDocument();
        });
        // The workflow picker (a SearchableSelect labeled "Workflow") is present,
        // collapsed to its placeholder label.
        expect(screen.getByLabelText("Workflow")).toBeInTheDocument();
        expect(screen.getByText("Search workflows…")).toBeInTheDocument();
    });

    it("does not render ExecutionsBoard when tab is not active", () => {
        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <AssetExecutionsTab databaseId="db1" assetId="a1" isActive={false} />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // ExecutionsBoard should not render when inactive
        expect(screen.queryByText("Executions")).not.toBeInTheDocument();
    });
});
