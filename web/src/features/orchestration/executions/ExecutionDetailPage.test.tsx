/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionDetailPage from "./ExecutionDetailPage";
import type { ExecutionDetail } from "../types";

// Mock Monaco editor
jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
}));

// Mock the query hooks
jest.mock("../api/queries", () => ({
    useExecutionDetails: jest.fn(),
}));

// Mock the API service for logs
jest.mock("../api/executions", () => ({
    getExecutionLogs: jest.fn(),
}));

// Mock the permissions hook
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

describe("ExecutionDetailPage", () => {
    let queryClient: QueryClient;

    beforeEach(() => {
        queryClient = new QueryClient({
            defaultOptions: {
                queries: { retry: false },
            },
        });
        jest.clearAllMocks();
    });

    it("renders execution detail with pipeline config body and template snapshot", async () => {
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        const mockDetail: ExecutionDetail = {
            workflowExecutionId: "e1",
            workflowId: "wf-1",
            workflowDatabaseId: "db-1",
            executionStatus: "SUCCEEDED",
            triggeredByUserId: "user-1",
            triggerType: "manual",
            executionStartDate: "2026-07-18T10:00:00Z",
            executionStopDate: "2026-07-18T10:30:00Z",
            pipelines: [
                {
                    pipelineId: "pipe-1",
                    pipelineName: "Test Pipeline",
                    executionStatus: "SUCCEEDED",
                    executionStartDate: "2026-07-18T10:05:00Z",
                    executionStopDate: "2026-07-18T10:25:00Z",
                    renderedConfigBody: '{"key": "value"}',
                    configFormat: "json",
                    templateId: "template-1",
                    templateTags: { tag1: "value1" },
                    customTemplateOverrideUsed: false,
                },
            ],
            inputFiles: [
                {
                    databaseId: "db-1",
                    assetId: "asset-1",
                    inputAssetFileKey: "file.txt",
                    versionId: "v1",
                },
            ],
            outputs: {
                files: [{ relativeFilePath: "output.txt", size: 1024 }],
                metadata: [],
                results: [],
            },
        };

        useExecutionDetails.mockReturnValue({
            data: mockDetail,
            isLoading: false,
            error: null,
        });

        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: jest.fn(() => true),
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Check header elements
        expect(screen.getByText("Succeeded")).toBeInTheDocument();
        expect(screen.getByText(/e1/)).toBeInTheDocument();

        // Navigate to Pipelines tab
        const pipelinesTab = screen.getByRole("button", { name: /Pipelines/i });
        await userEvent.click(pipelinesTab);

        // Check pipeline section renders
        await waitFor(() => {
            expect(screen.getByText("Test Pipeline")).toBeInTheDocument();
        });

        // Check template snapshot is visible
        expect(screen.getByText(/template-1/)).toBeInTheDocument();

        // Config should show as <pre> by default, not Monaco
        const configPre = screen.getByText('{"key": "value"}');
        expect(configPre.tagName).toBe("PRE");

        // Check for "View in editor" button
        expect(screen.getByText("View in editor")).toBeInTheDocument();
    });

    it("expands Monaco editor when 'View in editor' is clicked", async () => {
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        const mockDetail: ExecutionDetail = {
            workflowExecutionId: "e1",
            workflowId: "wf-1",
            workflowDatabaseId: "db-1",
            executionStatus: "SUCCEEDED",
            triggeredByUserId: "user-1",
            triggerType: "manual",
            executionStartDate: "2026-07-18T10:00:00Z",
            executionStopDate: "2026-07-18T10:30:00Z",
            pipelines: [
                {
                    pipelineId: "pipe-1",
                    pipelineName: "Test Pipeline",
                    executionStatus: "SUCCEEDED",
                    executionStartDate: "2026-07-18T10:05:00Z",
                    executionStopDate: "2026-07-18T10:25:00Z",
                    renderedConfigBody: '{"key": "value"}',
                    configFormat: "json",
                    templateId: "template-1",
                    templateTags: { tag1: "value1" },
                    customTemplateOverrideUsed: false,
                },
            ],
            inputFiles: [],
            outputs: {},
        };

        useExecutionDetails.mockReturnValue({
            data: mockDetail,
            isLoading: false,
            error: null,
        });

        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: jest.fn(() => true),
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Navigate to Pipelines tab
        const pipelinesTab = screen.getByRole("button", { name: /Pipelines/i });
        await userEvent.click(pipelinesTab);

        await waitFor(() => {
            expect(screen.getByText("Test Pipeline")).toBeInTheDocument();
        });

        // Click "View in editor"
        const viewButton = screen.getByText("View in editor");
        await userEvent.click(viewButton);

        // Button should disappear after click
        await waitFor(() => {
            expect(screen.queryByText("View in editor")).not.toBeInTheDocument();
        });
    });

    it("hides Logs section when permission is denied", () => {
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        const mockDetail: ExecutionDetail = {
            workflowExecutionId: "e1",
            workflowId: "wf-1",
            workflowDatabaseId: "db-1",
            executionStatus: "SUCCEEDED",
            triggeredByUserId: "user-1",
            triggerType: "manual",
            executionStartDate: "2026-07-18T10:00:00Z",
            executionStopDate: "2026-07-18T10:30:00Z",
            pipelines: [],
            inputFiles: [],
            outputs: {},
        };

        useExecutionDetails.mockReturnValue({
            data: mockDetail,
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
                    <ExecutionDetailPage executionId="e1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Logs section should NOT be present
        expect(screen.queryByText("Logs")).not.toBeInTheDocument();
    });

    it("shows Logs section when permission is granted", () => {
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        const mockDetail: ExecutionDetail = {
            workflowExecutionId: "e1",
            workflowId: "wf-1",
            workflowDatabaseId: "db-1",
            executionStatus: "SUCCEEDED",
            triggeredByUserId: "user-1",
            triggerType: "manual",
            executionStartDate: "2026-07-18T10:00:00Z",
            executionStopDate: "2026-07-18T10:30:00Z",
            pipelines: [],
            inputFiles: [],
            outputs: {},
        };

        useExecutionDetails.mockReturnValue({
            data: mockDetail,
            isLoading: false,
            error: null,
        });

        // Allow logs permission
        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: jest.fn(() => true),
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Logs section should be present
        expect(screen.getByText("Logs")).toBeInTheDocument();
    });
});
