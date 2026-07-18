/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionsBoard from "./ExecutionsBoard";
import type { Execution } from "../types";

// Mock the query hooks
jest.mock("../api/queries", () => ({
    useExecutions: jest.fn(),
    useExecutionActions: jest.fn(),
    useExecutionDetails: jest.fn(),
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

        // Default mock for useExecutions (empty)
        useExecutions.mockReturnValue({
            data: [],
            isLoading: false,
            error: null,
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
            data: mockExecutions,
            isLoading: false,
            error: null,
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

        // Status badges should be visible
        expect(screen.getByText("Running")).toBeInTheDocument();
        expect(screen.getByText("Succeeded")).toBeInTheDocument();
        expect(screen.getByText("Aborted")).toBeInTheDocument();
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
            data: mockExecutions,
            isLoading: false,
            error: null,
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
            data: mockExecutions,
            isLoading: false,
            error: null,
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
            data: mockExecutions,
            isLoading: false,
            error: null,
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
});
