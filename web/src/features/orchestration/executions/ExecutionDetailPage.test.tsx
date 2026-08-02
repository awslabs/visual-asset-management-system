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

    it("shows the recorded per-step settings and flags what the template overrode", async () => {
        // The point of the Settings tab: a finished run must explain which settings were ENFORCED, even
        // after the template or pipeline has since been edited or archived. effectiveSystemConfig is the
        // recorded merge; templateOverrides says which keys the template changed.
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionDetails.mockReturnValue({
            data: {
                workflowExecutionId: "e-set",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                workflowSystemConfig: { inputFileArity: "one", concurrencyRestriction: "perAsset" },
                pipelines: [
                    {
                        pipelineId: "p1",
                        pipelineName: "Cosmos 3 Nano",
                        executionStatus: "SUCCEEDED",
                        templateId: "cosmos3-nano-text2video",
                        effectiveSystemConfig: { inputFileArity: "one", requireTemplate: true },
                        templateOverrides: { inputFileArity: "one" },
                    },
                ],
            },
            isLoading: false,
            error: null,
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e-set" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await userEvent.click(await screen.findByRole("tab", { name: /Settings/i }));

        // Workflow level is labelled CURRENT, because it is read live rather than snapshotted.
        expect(screen.getByText(/Workflow settings \(current\)/i)).toBeInTheDocument();
        expect(screen.getByText(/can differ if/i)).toBeInTheDocument();

        // Per-step card names the step and its template.
        expect(screen.getByText(/Step 1: Cosmos 3 Nano/)).toBeInTheDocument();
        expect(screen.getByText("cosmos3-nano-text2video")).toBeInTheDocument();

        // Settings are rendered with readable labels, not raw camelCase keys.
        expect(screen.getAllByText(/Input file count:/).length).toBeGreaterThan(0);
        // And the key the template changed is flagged. (Only the step grid receives overrides, but the
        // badge renders once per overridden key, so assert on the count rather than a single match.)
        expect(screen.getAllByText("overridden").length).toBeGreaterThan(0);
    });

    it("says so when a step recorded no settings, rather than implying empty settings", async () => {
        // Executions from before settings capture existed have no effectiveSystemConfig. Showing an empty
        // grid would read as "no restrictions", which is a materially different claim.
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionDetails.mockReturnValue({
            data: {
                workflowExecutionId: "e-old",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                pipelines: [{ pipelineId: "p1", executionStatus: "SUCCEEDED" }],
            },
            isLoading: false,
            error: null,
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e-old" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await userEvent.click(await screen.findByRole("tab", { name: /Settings/i }));
        expect(screen.getByText(/No settings recorded for this step/i)).toBeInTheDocument();
        expect(screen.queryByText("overridden")).not.toBeInTheDocument();
    });

    it("exposes the tab strip with tablist/tab semantics and marks the active tab", async () => {
        // Styled after Cloudscape's Tabs (a bordered strip with the selected tab lifted onto the
        // container surface). The ARIA roles are what make it a tab strip rather than loose buttons,
        // so they are asserted here alongside the selected state.
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionDetails.mockReturnValue({
            data: {
                workflowExecutionId: "e-tabs",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                pipelines: [{ pipelineId: "p1", executionStatus: "SUCCEEDED" }],
            },
            isLoading: false,
            error: null,
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e-tabs" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
        expect(screen.getByRole("tab", { name: /Inputs/i })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        const pipelines = screen.getByRole("tab", { name: /Pipelines/i });
        expect(pipelines).toHaveAttribute("aria-selected", "false");
        await userEvent.click(pipelines);
        expect(screen.getByRole("tab", { name: /Pipelines/i })).toHaveAttribute(
            "aria-selected",
            "true"
        );
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
                    name: "Test Pipeline",
                    executionStatus: "SUCCEEDED",
                    executionStartDate: "2026-07-18T10:05:00Z",
                    executionStopDate: "2026-07-18T10:25:00Z",
                    renderedConfig: '{"key": "value"}',
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
        // The execution id appears in both the breadcrumb and the header field.
        expect(screen.getAllByText(/e1/).length).toBeGreaterThan(0);

        // Navigate to Pipelines tab
        // The tab strip is role="tablist"/role="tab" (Cloudscape-style), not plain buttons.
        const pipelinesTab = screen.getByRole("tab", { name: /Pipelines/i });
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
                    name: "Test Pipeline",
                    executionStatus: "SUCCEEDED",
                    executionStartDate: "2026-07-18T10:05:00Z",
                    executionStopDate: "2026-07-18T10:25:00Z",
                    renderedConfig: '{"key": "value"}',
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
        // The tab strip is role="tablist"/role="tab" (Cloudscape-style), not plain buttons.
        const pipelinesTab = screen.getByRole("tab", { name: /Pipelines/i });
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
